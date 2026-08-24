from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Transcript
from .operation import OperationBudget, OwnedPartialCleanupError

IMMUTABLE_TRANSCRIPT_FIELDS = (
    "created_at",
    "source_sha256",
    "language",
    "engine",
    "model",
    "raw_text",
)
SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.sources = self.root / "sources"
        self.exports = self.root / "exports"
        self.models = self.root / "models"
        self.db_path = self.root / "history.sqlite3"
        for path in (self.root, self.sources, self.exports, self.models):
            path.mkdir(parents=True, exist_ok=True)
        self._source_ownership: dict[Path, object] = {}
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
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
            # Keep the UUID fragment compact so managed paths remain usable on
            # Windows installations where legacy MAX_PATH is still enforced.
            target = self.sources / f"{source_hash}-{uuid.uuid4().hex[:8]}{suffix}"
            if budget is not None:
                budget.checkpoint("import")
            partial.replace(target)
            promoted = True
            self._source_ownership[target.resolve()] = object()
            return target, source_hash
        except BaseException as primary:
            if partial_created and not promoted:
                try:
                    partial.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    raise OwnedPartialCleanupError(partial, cleanup_error) from primary
            raise

    def source_ownership_token(self, target: Path) -> object | None:
        """Return the process-local capability for this managed import."""

        try:
            resolved_target = target.expanduser().resolve()
        except OSError:
            return None
        return self._source_ownership.get(resolved_target)

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
        if ownership is not None and self._source_ownership.get(resolved_target) is not ownership:
            return
        # A second immediately adjacent read closes the check/unlink window
        # exercised by concurrent transcript commits. Unique final paths also
        # ensure this capability can never target a peer import.
        if exclude_transcript_id is None:
            referenced = self._source_is_referenced(resolved_target, source_hash)
        else:
            referenced = self._source_is_referenced(
                resolved_target,
                source_hash,
                exclude_transcript_id=exclude_transcript_id,
            )
        if referenced:
            return
        if exclude_transcript_id is None:
            referenced = self._source_is_referenced(resolved_target, source_hash)
        else:
            referenced = self._source_is_referenced(
                resolved_target,
                source_hash,
                exclude_transcript_id=exclude_transcript_id,
            )
        if referenced:
            return
        resolved_target.unlink(missing_ok=True)
        self._source_ownership.pop(resolved_target, None)

    def _source_is_referenced(
        self,
        target: Path,
        source_hash: str,
        *,
        exclude_transcript_id: str | None = None,
    ) -> bool:
        with self._connect() as db:
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
            self._payload_references_target(row["payload_json"], target) for row in rows
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
        mismatched: list[str] = []
        unsafe: list[str] = []
        source_root = self.sources.resolve()
        for row in rows:
            transcript = Transcript.from_dict(json.loads(row["payload_json"]))
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
            elif sha256_file(resolved) != transcript.source_sha256:
                mismatched.append(str(resolved))
        orphans = [
            str(path.resolve())
            for path in self.sources.iterdir()
            if path.is_file() and path.resolve() not in referenced
        ]
        passed = integrity == ["ok"] and not missing and not mismatched and not unsafe
        return {
            "status": "PASS" if passed else "FAIL",
            "schema_version": SCHEMA_VERSION,
            "integrity": integrity,
            "records": len(rows),
            "orphans": sorted(orphans),
            "missing": sorted(missing),
            "hash_mismatches": sorted(mismatched),
            "unsafe_paths": sorted(unsafe),
        }

    def cleanup_orphans(self, *, confirmed: bool = False) -> dict[str, object]:
        if not confirmed:
            raise ValueError("orphan cleanup requires --yes")
        audit = self.audit()
        removed: list[str] = []
        source_root = self.sources.resolve()
        for value in audit["orphans"]:
            target = Path(value)
            try:
                target.resolve().relative_to(source_root)
            except (OSError, ValueError):
                continue
            target.unlink(missing_ok=True)
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
            # Keep the managed file while any other transcript record references
            # this immutable, uniquely owned import.
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT payload_json
                    FROM transcripts
                    WHERE source_sha256 = ? AND id <> ?
                    """,
                    (transcript.source_sha256, transcript.id),
                ).fetchall()
            resolved_target = target.resolve()
            other_references = any(
                self._payload_references_target(row["payload_json"], resolved_target)
                for row in rows
            )
            if not other_references:
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
