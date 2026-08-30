from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import string
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Transcript
from .operation import (
    ManagedTargetAllocationError,
    OperationBudget,
    OwnedPartialCleanupError,
)

IMMUTABLE_TRANSCRIPT_FIELDS = (
    "created_at",
    "source_sha256",
    "language",
    "engine",
    "model",
    "raw_text",
)
SCHEMA_VERSION = 1
MAX_MANAGED_TARGET_ATTEMPTS = 16
_UUID_ALPHABET = string.ascii_letters + string.digits + "!#$%&'()+,-.;=@[]^_{}~`"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_SUPPORTED_EXPORT_SUFFIXES = {".txt", ".md", ".json", ".srt", ".vtt"}


def _compact_uuid(value: uuid.UUID) -> str:
    """Encode all UUID bits compactly enough for legacy Windows paths."""

    number = int(value.hex, 16)
    encoded: list[str] = []
    for _ in range(20):
        number, remainder = divmod(number, len(_UUID_ALPHABET))
        encoded.append(_UUID_ALPHABET[remainder])
    return "".join(reversed(encoded))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.sources = self.root / "sources"
        self.exports = self.root / "exports"
        self.models = self.root / "models"
        self.db_path = self.root / "history.sqlite3"
        self._read_only = False
        for path in (self.root, self.sources, self.exports, self.models):
            path.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def open_read_only(cls, root: Path) -> LocalStore:
        """Open an existing store without bootstrap, migration, or filesystem writes."""

        expanded = root.expanduser()
        try:
            root_info = expanded.lstat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"storage root does not exist: {expanded}") from exc
        if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise ValueError(f"storage root is not a real directory: {expanded}")
        db_path = expanded / "history.sqlite3"
        try:
            db_info = db_path.lstat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"storage database does not exist: {db_path}") from exc
        if _is_reparse_point(db_info) or not stat.S_ISREG(db_info.st_mode):
            raise ValueError(f"storage database is not a real file: {db_path}")

        sidecar_paths = [
            db_path.with_name(f"{db_path.name}-wal"),
            db_path.with_name(f"{db_path.name}-shm"),
        ]
        sidecar_info: list[os.stat_result | None] = []
        for path in sidecar_paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                sidecar_info.append(None)
                continue
            if _is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"storage database sidecar is not a real file: {path}")
            sidecar_info.append(info)
        if (sidecar_info[0] is None) != (sidecar_info[1] is None):
            raise RuntimeError(
                "storage WAL sidecars are incomplete; read-only audit was refused"
            )
        store = cls.__new__(cls)
        store.root = expanded
        store.sources = expanded / "sources"
        store.exports = expanded / "exports"
        store.models = expanded / "models"
        store.db_path = db_path
        store._read_only = True
        store._read_only_immutable = sidecar_info[0] is None
        return store

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._read_only:
            uri = self.db_path.resolve(strict=True).as_uri() + "?mode=ro"
            if self._read_only_immutable:
                uri += "&immutable=1"
            connection = sqlite3.connect(uri, timeout=30, uri=True)
        else:
            connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            if not self._read_only:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"history schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    language TEXT NOT NULL,
                    engine TEXT NOT NULL DEFAULT 'faster-whisper',
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(transcripts)").fetchall()
            }
            if "engine" not in columns:
                db.execute(
                    "ALTER TABLE transcripts ADD COLUMN engine TEXT NOT NULL "
                    "DEFAULT 'faster-whisper'"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_transcript_created ON transcripts(created_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_transcript_hash ON transcripts(source_sha256)"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def import_source(
        self,
        path: Path,
        budget: OperationBudget | None = None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[Path, str]:
        path = path.expanduser()
        if not path.is_file():
            raise FileNotFoundError(path)
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")

        partial = self.sources / f".{uuid.uuid4().hex}.partial"
        digest = hashlib.sha256()
        copied = 0
        partial_created = False
        promoted = False
        cleanup_attempted = False
        try:
            if budget is not None:
                budget.checkpoint("import")
            with path.open("rb") as source, partial.open("xb") as destination:
                partial_created = True
                while True:
                    if budget is not None:
                        budget.checkpoint("import")
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if max_bytes is not None and copied > max_bytes:
                        raise ValueError(
                            f"source exceeds max_bytes limit of {max_bytes} bytes"
                        )
                    if budget is not None:
                        budget.checkpoint("import")
                    written = destination.write(block)
                    if written != len(block):
                        raise OSError(
                            f"managed import wrote {written} of {len(block)} bytes"
                        )
                    digest.update(block)
                    if budget is not None:
                        budget.checkpoint("import")
            source_hash = digest.hexdigest()
            suffix = path.suffix.lower() or ".bin"
            for _attempt in range(1, MAX_MANAGED_TARGET_ATTEMPTS + 1):
                target = self.sources / f"{source_hash}{_compact_uuid(uuid.uuid4())}{suffix}"
                if budget is not None:
                    budget.checkpoint("import")
                try:
                    # A hard link creates the final name exclusively on both
                    # Windows and POSIX; unlike replace/rename it cannot touch
                    # a peer or an open existing target.
                    os.link(partial, target)
                except FileExistsError:
                    continue
                except BaseException as primary:
                    cleanup_attempted = True
                    self._cleanup_partial_or_raise(partial, primary)
                promoted = True
                try:
                    partial.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    raise OwnedPartialCleanupError(
                        partial, cleanup_error, target_path=target
                    ) from cleanup_error
                return target, source_hash
            allocation_error = ManagedTargetAllocationError(MAX_MANAGED_TARGET_ATTEMPTS)
            cleanup_attempted = True
            self._cleanup_partial_or_raise(partial, allocation_error)
        except BaseException as primary:
            if partial_created and not promoted and not cleanup_attempted:
                self._cleanup_partial_or_raise(partial, primary)
            raise

    @staticmethod
    def _cleanup_partial_or_raise(partial: Path, primary: BaseException) -> None:
        try:
            partial.unlink(missing_ok=True)
        except BaseException as cleanup_error:
            raise OwnedPartialCleanupError(partial, cleanup_error) from primary
        raise primary

    def source_ownership_token(self, target: Path) -> object | None:
        """Return the path-bound capability for this managed import."""

        try:
            resolved_target = target.expanduser().resolve()
        except OSError:
            return None
        return resolved_target.name

    def remove_unreferenced_source(
        self,
        target: Path,
        source_hash: str,
        *,
        ownership: object | None = None,
        exclude_transcript_id: str | None = None,
    ) -> None:
        """Remove an imported source only when no transcript still references it."""

        try:
            resolved_target = target.expanduser().resolve()
            resolved_target.relative_to(self.sources.resolve())
        except (OSError, ValueError):
            return
        if ownership is not None and ownership != resolved_target.name:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._source_is_referenced_in_db(
                db, resolved_target, source_hash, exclude_transcript_id
            ):
                return
            if self._source_is_referenced_in_db(
                db, resolved_target, source_hash, exclude_transcript_id
            ):
                return
            resolved_target.unlink(missing_ok=True)

    def _source_is_referenced(
        self,
        target: Path,
        source_hash: str,
        *,
        exclude_transcript_id: str | None = None,
    ) -> bool:
        with self._connect() as db:
            return self._source_is_referenced_in_db(
                db, target, source_hash, exclude_transcript_id
            )

    @staticmethod
    def _source_is_referenced_in_db(
        db: sqlite3.Connection,
        target: Path,
        source_hash: str,
        exclude_transcript_id: str | None = None,
    ) -> bool:
        if exclude_transcript_id is None:
            rows = db.execute(
                "SELECT payload_json FROM transcripts WHERE source_sha256 = ?",
                (source_hash,),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT payload_json
                FROM transcripts
                WHERE source_sha256 = ? AND id <> ?
                """,
                (source_hash, exclude_transcript_id),
            ).fetchall()
        return any(
            LocalStore._payload_references_target(row["payload_json"], target)
            for row in rows
        )

    @staticmethod
    def _payload_references_target(payload_json: str, target: Path) -> bool:
        source_path = Transcript.from_dict(json.loads(payload_json)).source_path
        if not source_path:
            return False
        try:
            return Path(source_path).expanduser().resolve() == target
        except OSError:
            return False

    def save(self, transcript: Transcript) -> None:
        payload = json.dumps(transcript.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_managed_source_exists(transcript)
            existing = db.execute(
                "SELECT payload_json FROM transcripts WHERE id = ?",
                (transcript.id,),
            ).fetchone()
            if existing:
                previous = Transcript.from_dict(json.loads(existing["payload_json"]))
                changed = [
                    field
                    for field in IMMUTABLE_TRANSCRIPT_FIELDS
                    if getattr(previous, field) != getattr(transcript, field)
                ]
                if changed:
                    raise ValueError(
                        "immutable transcript fields cannot be changed: " + ", ".join(changed)
                    )
            db.execute(
                """
                INSERT OR REPLACE INTO transcripts
                (id, created_at, source_sha256, language, engine, model, status, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.id,
                    transcript.created_at,
                    transcript.source_sha256,
                    transcript.language,
                    transcript.engine,
                    transcript.model,
                    transcript.status,
                    payload,
                ),
            )

    def _verify_managed_source_exists(self, transcript: Transcript) -> None:
        if not transcript.source_path:
            return
        target = Path(transcript.source_path).expanduser()
        try:
            resolved_target = target.resolve()
            resolved_target.relative_to(self.sources.resolve())
        except (OSError, ValueError):
            return
        if not resolved_target.is_file():
            raise FileNotFoundError(f"managed source does not exist: {resolved_target}")

    def update_corrected_text(self, transcript_id: str, corrected_text: str) -> Transcript:
        transcript = self.get(transcript_id)
        if transcript is None:
            raise KeyError(f"transcript not found: {transcript_id}")
        transcript.corrected_text = corrected_text
        self.save(transcript)
        return transcript

    def apply_ai_cleanup(
        self,
        transcript_id: str,
        proposal: dict[str, object],
        *,
        provider: str,
        model: str,
    ) -> Transcript:
        """Apply a reviewed proposal while preserving raw/original segment text."""

        transcript = self.get(transcript_id)
        if transcript is None:
            raise KeyError(f"transcript not found: {transcript_id}")
        segments = proposal.get("segments")
        corrected_text = proposal.get("corrected_text")
        if not isinstance(segments, list) or not isinstance(corrected_text, str):
            raise ValueError("invalid AI cleanup proposal")
        if len(segments) != len(transcript.segments):
            raise ValueError("AI cleanup cannot add or remove transcript segments")
        previous = {
            "corrected_text": transcript.corrected_text,
            "segment_corrected_text": [segment.corrected_text for segment in transcript.segments],
        }
        for index, item in enumerate(segments):
            if not isinstance(item, dict) or item.get("segment_index") != index:
                raise ValueError("AI cleanup segment indexes must be consecutive")
            value = item.get("corrected_text")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("AI cleanup segment correction cannot be empty")
            transcript.segments[index].corrected_text = value.strip()
        history = list(transcript.metadata.get("ai_cleanup_history", []))
        history.append(previous)
        transcript.metadata = {
            **transcript.metadata,
            "ai_cleanup_history": history[-10:],
            "last_ai_cleanup": {"provider": provider, "model": model},
        }
        transcript.corrected_text = corrected_text.strip()
        self.save(transcript)
        return transcript

    def undo_last_ai_cleanup(self, transcript_id: str) -> Transcript:
        transcript = self.get(transcript_id)
        if transcript is None:
            raise KeyError(f"transcript not found: {transcript_id}")
        history = list(transcript.metadata.get("ai_cleanup_history", []))
        if not history:
            raise ValueError("there is no AI cleanup revision to undo")
        previous = history.pop()
        values = previous.get("segment_corrected_text") if isinstance(previous, dict) else None
        if not isinstance(values, list) or len(values) != len(transcript.segments):
            raise ValueError("stored AI cleanup revision is invalid")
        for segment, value in zip(transcript.segments, values, strict=True):
            segment.corrected_text = value if isinstance(value, str) else None
        corrected = previous.get("corrected_text") if isinstance(previous, dict) else None
        if not isinstance(corrected, str):
            raise ValueError("stored AI cleanup revision is invalid")
        metadata = {**transcript.metadata, "ai_cleanup_history": history}
        if not history:
            metadata.pop("last_ai_cleanup", None)
        transcript.metadata = metadata
        transcript.corrected_text = corrected
        self.save(transcript)
        return transcript

    def rename_source_name(self, transcript_id: str, source_name: str) -> Transcript:
        """Update the display name only; never move or alter the audio file."""

        value = source_name.strip()
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ValueError("source name must be a non-empty file name without a path")
        transcript = self.get(transcript_id)
        if transcript is None:
            raise KeyError(f"transcript not found: {transcript_id}")
        transcript.source_name = value
        self.save(transcript)
        return transcript

    def update_editor_formatting(
        self, transcript_id: str, formatting: dict[str, list[tuple[str, str]]]
    ) -> Transcript:
        """Persist presentation-only editor tags without changing transcript text."""

        allowed_tags = {"bold", "italic"}
        normalized: dict[str, list[list[str]]] = {}
        for tag, ranges in formatting.items():
            if tag not in allowed_tags:
                raise ValueError(f"unsupported editor formatting tag: {tag}")
            normalized[tag] = [[str(start), str(end)] for start, end in ranges]
        transcript = self.get(transcript_id)
        if transcript is None:
            raise KeyError(f"transcript not found: {transcript_id}")
        transcript.metadata = {**transcript.metadata, "editor_formatting": normalized}
        self.save(transcript)
        return transcript

    def update_editor_state(
        self,
        transcript_id: str,
        corrected_text: str,
        formatting: dict[str, list[tuple[str, str]]],
    ) -> Transcript:
        """Persist corrected text and presentation tags in one transaction."""

        allowed_tags = {"bold", "italic"}
        normalized: dict[str, list[list[str]]] = {}
        for tag, ranges in formatting.items():
            if tag not in allowed_tags:
                raise ValueError(f"unsupported editor formatting tag: {tag}")
            normalized[tag] = [[str(start), str(end)] for start, end in ranges]
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"transcript not found: {transcript_id}")
            transcript = Transcript.from_dict(json.loads(row["payload_json"]))
            transcript.corrected_text = corrected_text
            transcript.metadata = {**transcript.metadata, "editor_formatting": normalized}
            payload = json.dumps(transcript.to_dict(), ensure_ascii=False, separators=(",", ":"))
            db.execute(
                """
                UPDATE transcripts
                SET created_at = ?, source_sha256 = ?, language = ?, engine = ?, model = ?,
                    status = ?, payload_json = ?
                WHERE id = ?
                """,
                (
                    transcript.created_at,
                    transcript.source_sha256,
                    transcript.language,
                    transcript.engine,
                    transcript.model,
                    transcript.status,
                    payload,
                    transcript.id,
                ),
            )
            return transcript

    def get(self, transcript_id: str) -> Transcript | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
        return Transcript.from_dict(json.loads(row["payload_json"])) if row else None

    def list(self, query: str = "", limit: int = 100) -> list[Transcript]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql = "SELECT payload_json FROM transcripts"
        args: tuple[object, ...] = ()
        if query:
            sql += " WHERE payload_json LIKE ?"
            args = (f"%{query}%",)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args += (limit,)
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [Transcript.from_dict(json.loads(row["payload_json"])) for row in rows]

    def audit(self) -> dict[str, object]:
        with self._connect() as db:
            integrity_rows = db.execute("PRAGMA integrity_check").fetchall()
            rows = db.execute("SELECT payload_json FROM transcripts").fetchall()
        integrity = [str(row[0]) for row in integrity_rows]
        referenced: set[Path] = set()
        missing: list[str] = []
        missing_records: list[dict[str, str]] = []
        mismatched: list[str] = []
        unsafe: list[str] = []
        transcript_ids: set[str] = set()
        source_root = self.sources.resolve()
        for row in rows:
            transcript = Transcript.from_dict(json.loads(row["payload_json"]))
            try:
                transcript_id = str(uuid.UUID(transcript.id))
            except (ValueError, AttributeError):
                transcript_id = transcript.id
            transcript_ids.add(transcript_id)
            if not transcript.source_path:
                continue
            target = Path(transcript.source_path).expanduser()
            try:
                resolved = target.resolve()
                resolved.relative_to(source_root)
            except (OSError, ValueError):
                unsafe.append(str(target))
                continue
            referenced.add(resolved)
            if not resolved.is_file():
                missing.append(str(resolved))
                missing_records.append({"id": transcript.id, "path": str(resolved)})
            elif sha256_file(resolved) != transcript.source_sha256:
                mismatched.append(str(resolved))
        orphans = [
            str(path.resolve())
            for path in self.sources.iterdir()
            if path.is_file() and path.resolve() not in referenced
        ]
        from .model_catalog import ModelCatalog

        model_catalog = ModelCatalog.inspect(self.models)
        exports = {
            "files": [],
            "canonical_stale": [],
            "unmanaged": [],
            "blocked": [],
        }
        try:
            exports_stat = self.exports.lstat()
        except FileNotFoundError:
            exports["blocked"].append({"name": ".", "reason": "export directory is missing"})
            export_entries = []
        except OSError as exc:
            exports["blocked"].append(
                {"name": ".", "reason": f"export directory could not be inspected safely: {exc}"}
            )
            export_entries = []
        else:
            if _is_reparse_point(exports_stat):
                reason = (
                    "export directory is a symlink"
                    if stat.S_ISLNK(exports_stat.st_mode)
                    else "export directory is a reparse point"
                )
                exports["blocked"].append(
                    {"name": ".", "reason": reason}
                )
                export_entries = []
            elif not stat.S_ISDIR(exports_stat.st_mode):
                exports["blocked"].append(
                    {"name": ".", "reason": "export directory is not a real directory"}
                )
                export_entries = []
            else:
                try:
                    with os.scandir(self.exports) as iterator:
                        export_entries = sorted(iterator, key=lambda entry: entry.name)
                except OSError as exc:
                    exports["blocked"].append(
                        {
                            "name": ".",
                            "reason": f"export directory could not be inspected safely: {exc}",
                        }
                    )
                    export_entries = []
        for entry in export_entries:
            name = entry.name
            path = self.exports / name
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                exports["blocked"].append(
                    {"name": name, "reason": "export entry could not be inspected safely"}
                )
                continue
            if _is_reparse_point(entry_stat):
                reason = "export entry is a symlink" if stat.S_ISLNK(entry_stat.st_mode) else (
                    "export entry is a reparse point"
                )
                exports["blocked"].append({"name": name, "reason": reason})
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                exports["blocked"].append(
                    {"name": name, "reason": "export entry is not a regular file"}
                )
                continue
            exports["files"].append(name)
            if path.suffix.lower() in _SUPPORTED_EXPORT_SUFFIXES:
                try:
                    candidate_id = str(uuid.UUID(path.stem))
                except (ValueError, AttributeError):
                    pass
                else:
                    if candidate_id not in transcript_ids:
                        exports["canonical_stale"].append(name)
                        continue
            exports["unmanaged"].append(name)
        passed = integrity == ["ok"] and not missing and not mismatched and not unsafe
        return {
            "status": "PASS" if passed else "FAIL",
            "schema_version": SCHEMA_VERSION,
            "integrity": integrity,
            "records": len(rows),
            "orphans": sorted(orphans),
            "missing": sorted(missing),
            "missing_records": sorted(missing_records, key=lambda item: (item["path"], item["id"])),
            "hash_mismatches": sorted(mismatched),
            "unsafe_paths": sorted(unsafe),
            "model_catalog": model_catalog,
            "exports": exports,
        }

    @staticmethod
    def _absolute_lexical_path(value: str | os.PathLike[str]) -> Path:
        """Normalize a path without resolving links in any component."""

        return Path(os.path.abspath(os.fspath(Path(value).expanduser())))

    def _managed_missing_path(self, value: str) -> Path:
        """Validate a managed path without following links and return its lexical path."""

        try:
            source_entry = self.sources.lstat()
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"managed sources could not be inspected safely: {exc}") from exc
        if _is_reparse_point(source_entry) or not stat.S_ISDIR(source_entry.st_mode):
            raise ValueError("managed sources directory is not a real directory")
        try:
            source_root = self.sources.resolve(strict=True)
            root_info = source_root.lstat()
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"managed sources could not be inspected safely: {exc}") from exc
        if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise ValueError("managed sources directory is not a real directory")

        try:
            target = self._absolute_lexical_path(value)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"managed source path could not be inspected safely: {exc}") from exc
        root_text = os.path.normcase(os.path.normpath(os.fspath(source_root)))
        target_text = os.path.normcase(os.path.normpath(os.fspath(target)))
        try:
            contained = os.path.commonpath((root_text, target_text)) == root_text
        except ValueError:
            contained = False
        if not contained or target_text == root_text:
            raise ValueError("managed source path is outside managed sources")
        relative = os.path.relpath(target_text, root_text)
        parts = Path(relative).parts
        if not parts or parts[0] == os.pardir or os.pardir in parts:
            raise ValueError("managed source path is outside managed sources")
        target = source_root.joinpath(*parts)

        parent = source_root
        for part in parts[:-1]:
            parent /= part
            try:
                info = parent.lstat()
            except FileNotFoundError as exc:
                raise ValueError("managed source path has a missing parent") from exc
            except (OSError, ValueError, UnicodeError) as exc:
                raise ValueError(
                    f"managed source path could not be inspected safely: {exc}"
                ) from exc
            if _is_reparse_point(info):
                raise ValueError("managed source path has an unsafe link or reparse ancestor")
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("managed source path has a non-directory ancestor")
        return target

    def repair_missing_source(
        self,
        transcript_id: str,
        *,
        confirmed: bool = False,
        expected_path: str | None = None,
    ) -> dict[str, object]:
        """Detach one transcript from a confirmed missing managed source."""

        if not confirmed:
            raise ValueError("missing-source repair requires --yes")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload_json FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"transcript not found: {transcript_id}")
            transcript = Transcript.from_dict(json.loads(row["payload_json"]))
            old_path = transcript.source_path
            if not old_path:
                raise ValueError("transcript has no source to repair")
            if expected_path is not None:
                try:
                    expected = self._absolute_lexical_path(expected_path)
                    actual = self._absolute_lexical_path(old_path)
                except (OSError, TypeError, ValueError) as exc:
                    raise ValueError("expected path does not match stored source path") from exc
                if os.path.normcase(os.path.normpath(os.fspath(expected))) != os.path.normcase(
                    os.path.normpath(os.fspath(actual))
                ):
                    raise ValueError("expected path does not match stored source path")

            target = self._managed_missing_path(old_path)
            try:
                info = target.lstat()
            except FileNotFoundError:
                pass
            except (OSError, ValueError, UnicodeError) as exc:
                raise ValueError(
                    f"managed source could not be inspected safely: {exc}"
                ) from exc
            else:
                if _is_reparse_point(info):
                    raise ValueError("managed source path is an unsafe link or reparse point")
                raise ValueError("managed source has reappeared; repair was refused")

            transcript.source_path = None
            transcript.audio_retained = False
            payload = json.dumps(transcript.to_dict(), ensure_ascii=False, separators=(",", ":"))
            db.execute(
                "UPDATE transcripts SET payload_json = ? WHERE id = ?",
                (payload, transcript_id),
            )
        return {
            "repaired": True,
            "id": transcript_id,
            "path": old_path,
            "action": "detached_missing_source",
        }

    def cleanup_orphans(self, *, confirmed: bool = False) -> dict[str, object]:
        if not confirmed:
            raise ValueError("orphan cleanup requires --yes")
        audit = self.audit()
        removed: list[str] = []
        try:
            source_entry = self.sources.lstat()
            if _is_reparse_point(source_entry) or not stat.S_ISDIR(source_entry.st_mode):
                return {"removed": removed, "count": len(removed)}
            source_root = self.sources.resolve(strict=True)
            root_info = source_root.lstat()
        except OSError:
            return {"removed": removed, "count": len(removed)}
        if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
            return {"removed": removed, "count": len(removed)}
        candidates = audit.get("orphans", [])
        if not isinstance(candidates, list):
            candidates = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT payload_json FROM transcripts").fetchall()
            referenced: set[Path] = set()
            uninspectable_reference = False
            for row in rows:
                try:
                    source_path = Transcript.from_dict(json.loads(row["payload_json"])).source_path
                except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                    uninspectable_reference = True
                    continue
                if not source_path:
                    continue
                if not isinstance(source_path, str) or "\x00" in source_path:
                    uninspectable_reference = True
                    continue
                try:
                    referenced.add(Path(source_path).expanduser().resolve())
                except (OSError, ValueError, UnicodeError):
                    uninspectable_reference = True
                    continue

            if uninspectable_reference:
                return {"removed": removed, "count": len(removed)}

            for value in candidates:
                if not isinstance(value, str):
                    continue
                try:
                    lexical = self._absolute_lexical_path(value)
                    root_text = os.path.normcase(os.path.normpath(os.fspath(source_root)))
                    candidate_text = os.path.normcase(os.path.normpath(os.fspath(lexical)))
                    relative = os.path.relpath(candidate_text, root_text)
                    parts = Path(relative).parts
                    if (
                        not parts
                        or len(parts) != 1
                        or parts[0] in {".", os.pardir}
                        or os.pardir in parts
                    ):
                        continue
                    target = source_root / parts[0]
                    info = target.lstat()
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    continue
                if _is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
                    continue
                try:
                    resolved = target.resolve(strict=True)
                except OSError:
                    continue
                if resolved in referenced:
                    continue
                try:
                    target.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
                removed.append(str(target))
        return {"removed": removed, "count": len(removed)}

    def delete(self, transcript_id: str, *, delete_audio: bool = False) -> bool:
        transcript = self.get(transcript_id)
        if transcript is None:
            return False
        if delete_audio:
            self.delete_audio(transcript)
        with self._connect() as db:
            cursor = db.execute("DELETE FROM transcripts WHERE id = ?", (transcript_id,))
        return cursor.rowcount > 0

    def delete_audio(self, transcript: Transcript) -> None:
        if transcript.source_path:
            target = Path(transcript.source_path).expanduser()
            try:
                target.resolve().relative_to(self.sources.resolve())
            except (OSError, ValueError):
                return
            self.remove_unreferenced_source(
                target,
                transcript.source_sha256,
                exclude_transcript_id=transcript.id,
            )
        transcript.source_path = None
        transcript.audio_retained = False
        self.save(transcript)

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())
