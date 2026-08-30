from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import stat
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .archive import ZipBudget, inspect_zip, require_free_space
from .models import Settings, Transcript
from .storage import LocalStore

BACKUP_VERSION = 1
RESTORE_JOURNAL_VERSION = 1
RESTORE_SIDECAR_VERSION = 1
RESTORE_SIDECAR_NAME = ".restore-settings.json"
RESTORE_JOURNAL_FIELDS = frozenset(
    {
        "journal_version",
        "backup_version",
        "created_at",
        "data_root",
        "staging_path",
        "recovery_path",
        "expected_records",
        "settings_target",
        "settings_payload_written",
        "stage",
    }
)
_RESTORE_STAGES = ("swap_started", "swap_completed")
BACKUP_FREE_SPACE_MARGIN_BYTES = 256 * 1024**2
BACKUP_ZIP_BUDGET = ZipBudget(
    max_container_bytes=4 * 1024**3,
    max_members=20_000,
    max_member_bytes=2 * 1024**3,
    max_total_bytes=8 * 1024**3,
    max_member_compression_ratio=250.0,
    max_total_compression_ratio=100.0,
    max_central_directory_bytes=64 * 1024**2,
)
_FIXED_MEMBER_LIMITS = {
    "manifest.json": 4 * 1024**2,
    "transcripts.jsonl": 512 * 1024**2,
    "config/settings.json": 1024**2,
    "config/dictionary.json": 16 * 1024**2,
}
_LOCAL_RESTORE_NAMES = ("exports", "models")
_LocalRestoreFingerprint = tuple[int, int, int, int, int, int]
_LocalRestoreEntry = tuple[str, _LocalRestoreFingerprint]


def _stream_hash(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _member_info(payload: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def create_backup(
    store: LocalStore,
    destination: Path,
    *,
    settings_file: Path | None = None,
    include_audio: bool = True,
) -> dict[str, Any]:
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    transcripts = store.list(limit=1_000_000)
    transcript_payload = (
        "\n".join(
            json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True)
            for item in transcripts
        )
        + ("\n" if transcripts else "")
    ).encode("utf-8")
    payloads: dict[str, bytes] = {"transcripts.jsonl": transcript_payload}
    if settings_file and settings_file.is_file():
        settings_payload = settings_file.read_bytes()
        payloads["config/settings.json"] = settings_payload
        try:
            settings = Settings.from_dict(json.loads(settings_payload))
            dictionary = Path(settings.dictionary_path).expanduser()
            if settings.dictionary_path and dictionary.is_file():
                payloads["config/dictionary.json"] = dictionary.read_bytes()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    audio_files: dict[str, Path] = {}
    if include_audio:
        source_root = store.sources.resolve()
        for transcript in transcripts:
            if not transcript.source_path:
                continue
            source = Path(transcript.source_path).expanduser()
            try:
                source.resolve().relative_to(source_root)
            except (OSError, ValueError):
                continue
            if source.is_file():
                audio_files[f"sources/{source.name}"] = source
    inventory = {name: _member_info(payload) for name, payload in payloads.items()}
    for name, path in audio_files.items():
        with path.open("rb") as stream:
            sha256, size = _stream_hash(stream)
        inventory[name] = {"sha256": sha256, "size": size}
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "records": len(transcripts),
        "include_audio": include_audio,
        "members": inventory,
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            for name, payload in payloads.items():
                archive.writestr(name, payload)
            for name, path in audio_files.items():
                archive.write(path, name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination.resolve()),
        "records": len(transcripts),
        "audio_files": len(audio_files),
        "version": BACKUP_VERSION,
    }


def verify_backup(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    inspection = inspect_zip(path, BACKUP_ZIP_BUDGET)
    member_sizes = {member.name: member.expanded_bytes for member in inspection.members}
    for name, size in member_sizes.items():
        if name in _FIXED_MEMBER_LIMITS and size > _FIXED_MEMBER_LIMITS[name]:
            raise ValueError(
                f"backup member exceeds its format limit: {name} ({size} bytes)"
            )
        if name not in _FIXED_MEMBER_LIMITS and not name.startswith("sources/"):
            raise ValueError(f"unsupported backup member: {name}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("backup contains duplicate members")
        if "manifest.json" not in names:
            raise ValueError("backup manifest is missing")
        for name in names:
            member = Path(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise ValueError(f"unsafe backup member: {name}")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"invalid backup manifest: {exc}") from exc
        if manifest.get("version") != BACKUP_VERSION:
            raise ValueError(f"unsupported backup version: {manifest.get('version')}")
        members = manifest.get("members")
        if not isinstance(members, dict):
            raise ValueError("backup manifest members must be an object")
        if set(names) != {"manifest.json", *members}:
            raise ValueError("backup member set does not match manifest")
        for name, expected in members.items():
            if not isinstance(expected, dict):
                raise ValueError(f"backup manifest entry must be an object: {name}")
            expected_size = expected.get("size")
            expected_hash = expected.get("sha256")
            if (
                type(expected_size) is not int
                or expected_size < 0
                or expected_size != member_sizes.get(name)
            ):
                raise ValueError(f"backup member size metadata is invalid: {name}")
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
            ):
                raise ValueError(f"backup member hash metadata is invalid: {name}")
            with archive.open(name) as stream:
                sha256, size = _stream_hash(stream)
            if sha256 != expected_hash or size != expected_size:
                raise ValueError(f"backup member integrity check failed: {name}")
    return {
        "status": "PASS",
        "path": str(path.resolve()),
        "version": BACKUP_VERSION,
        "records": manifest.get("records", 0),
        "members": len(manifest["members"]),
        "expanded_bytes": inspection.total_expanded_bytes,
        "manifest": manifest,
    }


def restore_journal_path(data_root: Path) -> Path:
    """Return the sidecar journal that describes an in-flight restore swap."""

    data_root = data_root.expanduser()
    return data_root.parent / f".{data_root.name}.restore-journal.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_restore_sidecar(
    staging: Path,
    *,
    settings_target: Path,
    settings_payload: dict[str, Any],
    dictionary_payload: bytes | None,
    timestamp: str,
) -> None:
    """Park the settings payload inside staging so the swap carries it along.

    The journal records only paths and counters. The payload travels inside the
    staging directory instead, so a process death between the swap and the
    settings write leaves the payload inside the new ``data_root``.
    """

    _write_json_atomic(
        staging / RESTORE_SIDECAR_NAME,
        {
            "sidecar_version": RESTORE_SIDECAR_VERSION,
            "timestamp": timestamp,
            "settings_target": str(settings_target),
            "settings": settings_payload,
            "dictionary_base64": (
                base64.b64encode(dictionary_payload).decode("ascii")
                if dictionary_payload is not None
                else None
            ),
        },
    )


def _apply_restored_settings(
    settings_target: Path,
    settings_payload: dict[str, Any],
    dictionary_payload: bytes | None,
    timestamp: str,
) -> None:
    if settings_target.exists():
        preserved = settings_target.with_name(
            f"{settings_target.name}.pre-restore-{timestamp}"
        )
        # Never overwrite an existing pre-restore snapshot: a second pass must
        # not replace the user's original settings with post-restore content.
        if not preserved.exists():
            shutil.copy2(settings_target, preserved)
    settings_target.parent.mkdir(parents=True, exist_ok=True)
    if dictionary_payload is not None:
        dictionary_target = settings_target.parent / "dictionary.restored.json"
        dictionary_target.write_bytes(dictionary_payload)
    settings_target.write_text(
        json.dumps(settings_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_restore_sidecar(data_root: Path) -> dict[str, Any] | None:
    sidecar = data_root / RESTORE_SIDECAR_NAME
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"restore settings sidecar is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("restore settings sidecar must contain a JSON object")
    if payload.get("sidecar_version") != RESTORE_SIDECAR_VERSION:
        raise ValueError(
            f"unsupported restore sidecar version: {payload.get('sidecar_version')}"
        )
    if not isinstance(payload.get("settings"), dict):
        raise ValueError("restore settings sidecar has no settings object")
    # The sidecar is an on-disk file. Refuse to write settings that would not
    # load, exactly as the pre-swap path validates them before parking them.
    try:
        Settings.from_dict(payload["settings"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"restore settings sidecar is not valid settings: {exc}") from exc
    return payload


def _finish_restored_settings(data_root: Path, settings_target: Path | None) -> bool:
    """Apply a parked settings payload, then drop the sidecar. Idempotent."""

    payload = _read_restore_sidecar(data_root)
    if payload is None:
        return False
    target = settings_target or (
        Path(payload["settings_target"]) if payload.get("settings_target") else None
    )
    if target is None:
        (data_root / RESTORE_SIDECAR_NAME).unlink(missing_ok=True)
        return False
    encoded = payload.get("dictionary_base64")
    dictionary_payload: bytes | None = None
    if isinstance(encoded, str):
        try:
            dictionary_payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"restore sidecar dictionary is corrupt: {exc}") from exc
    _apply_restored_settings(
        target.expanduser(),
        payload["settings"],
        dictionary_payload,
        str(payload.get("timestamp") or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")),
    )
    (data_root / RESTORE_SIDECAR_NAME).unlink(missing_ok=True)
    return True


def _fail(error: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "action": "none",
        "records": None,
        "recovery": None,
        "error": error,
        **extra,
    }


def _load_restore_journal(journal_path: Path, data_root: Path) -> dict[str, Any]:
    """Parse and validate a journal. Raises ``ValueError`` when unusable."""

    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"restore journal is unreadable: {exc}") from exc
    if not isinstance(journal, dict):
        raise ValueError("restore journal must contain a JSON object")
    if journal.get("journal_version") != RESTORE_JOURNAL_VERSION:
        raise ValueError(
            f"unsupported restore journal version: {journal.get('journal_version')}"
        )
    if journal.get("backup_version") != BACKUP_VERSION:
        raise ValueError(
            f"unsupported restore journal backup version: {journal.get('backup_version')}"
        )
    if journal.get("stage") not in _RESTORE_STAGES:
        raise ValueError(f"unsupported restore journal stage: {journal.get('stage')}")
    if type(journal.get("expected_records")) is not int or journal["expected_records"] < 0:
        raise ValueError("restore journal record count is invalid")
    recorded_root = journal.get("data_root")
    if not isinstance(recorded_root, str):
        raise ValueError("restore journal data root is invalid")
    if Path(recorded_root).resolve() != data_root.resolve():
        raise ValueError("restore journal belongs to a different data directory")
    staging = journal.get("staging_path")
    if not isinstance(staging, str):
        raise ValueError("restore journal staging path is invalid")
    # A journal is an on-disk file in a user-writable directory. Refuse to move
    # anything that does not sit beside the data root, so a tampered journal
    # cannot promote an arbitrary directory into the user's storage.
    if Path(staging).parent.resolve() != data_root.parent.resolve():
        raise ValueError("restore journal staging path is outside the data directory")
    recovery = journal.get("recovery_path")
    if recovery is not None:
        if not isinstance(recovery, str):
            raise ValueError("restore journal recovery path is invalid")
        if Path(recovery).parent.resolve() != data_root.parent.resolve():
            raise ValueError(
                "restore journal recovery path is outside the data directory"
            )
    return journal


def _restored_store_is_sound(data_root: Path, expected_records: int) -> bool:
    """Report whether ``data_root`` holds the complete store the manifest promised."""

    try:
        store = LocalStore(data_root)
        if len(store.list(limit=1_000_000)) != expected_records:
            return False
        return store.audit()["status"] == "PASS"
    except Exception:
        return False


def _promote_staging(staging: Path, data_root: Path, expected_records: int) -> bool:
    """Move staging into place, keeping it only if it audits as the real store.

    ``restore_backup`` rewrites every managed source path to its post-swap
    ``data_root`` location before the swap, so staging can only be audited from
    the position it was built for. The move is therefore made first and undone
    when the audit rejects the result.
    """

    if not staging.is_dir() or not (staging / "history.sqlite3").is_file():
        return False
    staging.replace(data_root)
    if _restored_store_is_sound(data_root, expected_records):
        return True
    data_root.replace(staging)
    return False


def _is_reparse_point(info: os.stat_result) -> bool:
    """Return whether an entry is a link or Windows reparse point."""

    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _unsafe_local_restore_path(path: Path) -> ValueError:
    return ValueError(f"local restore state contains an unsafe path: {path}")


def _local_restore_fingerprint(info: os.stat_result) -> _LocalRestoreFingerprint:
    """Capture identity and mutable attributes used for cooperative change checks."""

    return (
        stat.S_IFMT(info.st_mode),
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
        getattr(info, "st_dev", 0),
        getattr(info, "st_ino", 0),
    )


def _validate_local_restore_entry(path: Path, info: os.stat_result) -> None:
    if _is_reparse_point(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise _unsafe_local_restore_path(path)


def _local_restore_directory_state(
    source: Path,
) -> tuple[_LocalRestoreFingerprint, tuple[_LocalRestoreEntry, ...]]:
    """Validate a directory and snapshot its direct entries without following links."""

    try:
        source_info = os.lstat(source)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _unsafe_local_restore_path(source) from exc
    _validate_local_restore_entry(source, source_info)
    if not stat.S_ISDIR(source_info.st_mode):
        raise _unsafe_local_restore_path(source)
    entries: list[_LocalRestoreEntry] = []
    try:
        with os.scandir(source) as directory:
            for entry in directory:
                entry_path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                _validate_local_restore_entry(entry_path, info)
                entries.append((entry.name, _local_restore_fingerprint(info)))
    except OSError as exc:
        raise _unsafe_local_restore_path(source) from exc
    return _local_restore_fingerprint(source_info), tuple(sorted(entries))


def _local_restore_tree_bytes(source: Path) -> int:
    """Inspect one local-state tree without following links."""

    try:
        before = _local_restore_directory_state(source)
    except FileNotFoundError:
        return 0

    total = 0
    with os.scandir(source) as entries:
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _unsafe_local_restore_path(entry_path) from exc
            _validate_local_restore_entry(entry_path, info)
            if stat.S_ISDIR(info.st_mode):
                total += _local_restore_tree_bytes(entry_path)
            else:
                total += info.st_size
    after = _local_restore_directory_state(source)
    if before != after:
        raise ValueError(f"local restore state changed during scan: {source}")
    return total


def _local_restore_bytes(data_root: Path) -> int:
    """Return bytes occupied by the current machine-local restore state."""

    data_root = data_root.expanduser()
    try:
        root_info = os.lstat(data_root)
    except FileNotFoundError:
        return 0
    _validate_local_restore_entry(data_root, root_info)
    if not stat.S_ISDIR(root_info.st_mode):
        raise _unsafe_local_restore_path(data_root)

    return sum(_local_restore_tree_bytes(data_root / name) for name in _LOCAL_RESTORE_NAMES)


def _copy_local_restore_tree(source: Path, destination: Path) -> None:
    """Copy one validated local-state tree without following links.

    Validation is best-effort: a cooperative local change between syscalls is
    rejected, while a malicious same-account TOCTTOU actor remains outside the
    threat model because that actor can already alter private local files.
    """

    try:
        before = _local_restore_directory_state(source)
    except OSError as exc:
        raise _unsafe_local_restore_path(source) from exc

    destination.mkdir(exist_ok=True)
    with os.scandir(source) as entries:
        for entry in entries:
            source_entry = Path(entry.path)
            destination_entry = destination / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _unsafe_local_restore_path(source_entry) from exc
            _validate_local_restore_entry(source_entry, info)
            if stat.S_ISDIR(info.st_mode):
                _copy_local_restore_tree(source_entry, destination_entry)
            else:
                # Re-check immediately before copying so a replaced directory
                # cannot turn into a link after the directory scan.
                try:
                    current_info = os.lstat(source_entry)
                except OSError as exc:
                    raise _unsafe_local_restore_path(source_entry) from exc
                _validate_local_restore_entry(source_entry, current_info)
                if not stat.S_ISREG(current_info.st_mode):
                    raise _unsafe_local_restore_path(source_entry)
                shutil.copy2(
                    source_entry,
                    destination_entry,
                    follow_symlinks=False,
                )
                try:
                    after_info = os.lstat(source_entry)
                except OSError as exc:
                    raise _unsafe_local_restore_path(source_entry) from exc
                _validate_local_restore_entry(source_entry, after_info)
                if (
                    not stat.S_ISREG(after_info.st_mode)
                    or _local_restore_fingerprint(current_info)
                    != _local_restore_fingerprint(after_info)
                ):
                    raise ValueError(
                        f"local restore state changed during copy: {source_entry}"
                    )
    after = _local_restore_directory_state(source)
    if before != after:
        raise ValueError(f"local restore state changed during copy: {source}")
    shutil.copystat(source, destination, follow_symlinks=False)


def _copy_local_restore_state(data_root: Path, staging: Path) -> list[str]:
    """Copy current ``exports`` and ``models`` into restored staging."""

    data_root = data_root.expanduser()
    staging = staging.expanduser()
    try:
        root_info = os.lstat(data_root)
    except FileNotFoundError:
        return []
    _validate_local_restore_entry(data_root, root_info)
    if not stat.S_ISDIR(root_info.st_mode):
        raise _unsafe_local_restore_path(data_root)
    sources: list[tuple[str, Path]] = []
    for name in _LOCAL_RESTORE_NAMES:
        source = data_root / name
        try:
            info = os.lstat(source)
        except FileNotFoundError:
            continue
        _validate_local_restore_entry(source, info)
        if not stat.S_ISDIR(info.st_mode):
            raise _unsafe_local_restore_path(source)
        # Validate the complete source before creating any staging output. If
        # one tree contains an unsafe entry, the live source and staging are
        # both left untouched.
        _local_restore_tree_bytes(source)
        sources.append((name, source))

    if not sources:
        return []
    staging.mkdir(parents=True, exist_ok=True)
    for name, source in sources:
        _copy_local_restore_tree(source, staging / name)
    return [name for name, _source in sources]


def recover_interrupted_restore(
    data_root: Path,
    *,
    settings_target: Path | None = None,
) -> dict[str, Any]:
    """Finish or undo a restore that a process death left half applied.

    Deterministic and non-interactive. A ``*.recovery-*`` directory is never
    deleted, and staging is only discarded once ``data_root`` is known to be
    intact.
    """

    data_root = data_root.expanduser()
    journal_path = restore_journal_path(data_root)
    if not journal_path.is_file():
        return {"status": "PASS", "action": "none", "records": None, "recovery": None}
    try:
        journal = _load_restore_journal(journal_path, data_root)
    except ValueError as exc:
        return _fail(str(exc))

    staging = Path(journal["staging_path"])
    recovery = Path(journal["recovery_path"]) if journal.get("recovery_path") else None
    expected_records = journal["expected_records"]
    recovery_value = str(recovery) if recovery is not None else None

    def _settings_step() -> None:
        if journal.get("settings_payload_written") is True:
            return
        _finish_restored_settings(data_root, settings_target)

    if journal["stage"] == "swap_completed":
        try:
            _settings_step()
        except (OSError, ValueError) as exc:
            return _fail(f"restored settings could not be written: {exc}")
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "settings_completed",
            "records": expected_records,
            "recovery": recovery_value,
        }

    if data_root.exists():
        # Swap step A never happened: the live data is untouched and staging is
        # the only thing to drop.
        try:
            if staging.is_dir():
                shutil.rmtree(staging)
        except OSError as exc:
            return _fail(f"interrupted restore staging could not be removed: {exc}")
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "staging_discarded",
            "records": expected_records,
            "recovery": recovery_value,
        }

    try:
        promoted = _promote_staging(staging, data_root, expected_records)
    except OSError as exc:
        return _fail(f"interrupted restore staging could not be promoted: {exc}")
    if promoted:
        try:
            _settings_step()
        except (OSError, ValueError) as exc:
            return _fail(f"restored settings could not be written: {exc}")
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "completed",
            "records": expected_records,
            "recovery": recovery_value,
        }

    if recovery is not None and recovery.is_dir():
        try:
            recovery.replace(data_root)
        except OSError as exc:
            return _fail(f"interrupted restore could not be rolled back: {exc}")
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "rolled_back",
            "records": expected_records,
            "recovery": recovery_value,
        }

    return _fail(
        "interrupted restore left neither a usable staging directory nor a "
        "recovery directory; nothing was removed",
        recovery=recovery_value,
    )


def restore_backup(
    path: Path,
    data_root: Path,
    *,
    settings_target: Path | None = None,
) -> dict[str, Any]:
    verified = verify_backup(path)
    data_root = data_root.expanduser()
    data_root.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(
        data_root.parent,
        verified["expanded_bytes"],
        margin_bytes=BACKUP_FREE_SPACE_MARGIN_BYTES,
    )
    temporary = data_root.parent / f".{data_root.name}.restore-{uuid.uuid4().hex}"
    journal_path = restore_journal_path(data_root)
    recovery: Path | None = None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    settings_payload: dict[str, Any] | None = None
    dictionary_payload: bytes | None = None
    try:
        store = LocalStore(temporary)
        with zipfile.ZipFile(path) as archive:
            lines = archive.read("transcripts.jsonl").decode("utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                transcript = Transcript.from_dict(json.loads(line))
                source_name = Path(transcript.source_path).name if transcript.source_path else ""
                member = f"sources/{source_name}" if source_name else ""
                if member and member in archive.namelist():
                    target = temporary / "sources" / source_name
                    with archive.open(member) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                    transcript.source_path = str(target)
                    transcript.audio_retained = True
                else:
                    transcript.source_path = None
                    transcript.audio_retained = False
                store.save(transcript)
            if settings_target and "config/settings.json" in archive.namelist():
                candidate_settings = json.loads(archive.read("config/settings.json"))
                if not isinstance(candidate_settings, dict):
                    raise ValueError("backup settings must contain a JSON object")
                if "config/dictionary.json" in archive.namelist():
                    dictionary_payload = archive.read("config/dictionary.json")
                    candidate_settings["dictionary_path"] = str(
                        settings_target.parent / "dictionary.restored.json"
                    )
                Settings.from_dict(candidate_settings)
                settings_payload = candidate_settings

        restored_records = len(store.list(limit=1_000_000))
        if restored_records != verified["records"]:
            raise ValueError(
                "backup restore record count does not match manifest: "
                f"expected {verified['records']}, got {restored_records}"
            )
        audit = store.audit()
        if audit["status"] != "PASS":
            raise ValueError(
                "backup restore storage audit failed before replacing current data: "
                f"missing={len(audit['missing'])}, "
                f"hash_mismatches={len(audit['hash_mismatches'])}, "
                f"unsafe_paths={len(audit['unsafe_paths'])}"
            )
        for transcript in store.list(limit=1_000_000):
            if transcript.source_path:
                transcript.source_path = str(
                    data_root / "sources" / Path(transcript.source_path).name
                )
                store.save(transcript)
        has_settings_payload = settings_target is not None and settings_payload is not None
        if settings_target is not None and settings_payload is not None:
            _write_restore_sidecar(
                temporary,
                settings_target=settings_target,
                settings_payload=settings_payload,
                dictionary_payload=dictionary_payload,
                timestamp=timestamp,
            )
        # The swap below is two renames. A process death between them leaves no
        # data root at all, so record which directory holds the real data before
        # the first rename rather than after it.
        if data_root.exists():
            recovery_name = (
                f"{data_root.name}.recovery-{timestamp}-{uuid.uuid4().hex[:8]}"
            )
            recovery = data_root.parent / recovery_name
        journal = {
            "journal_version": RESTORE_JOURNAL_VERSION,
            "backup_version": BACKUP_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "data_root": str(data_root.resolve()),
            "staging_path": str(temporary),
            "recovery_path": str(recovery) if recovery is not None else None,
            "expected_records": int(verified["records"]),
            "settings_target": str(settings_target) if settings_target is not None else None,
            "settings_payload_written": not has_settings_payload,
            "stage": "swap_started",
        }
        _write_json_atomic(journal_path, journal)
        if recovery is not None:
            data_root.replace(recovery)
        try:
            temporary.replace(data_root)
        except BaseException:
            if recovery and recovery.exists() and not data_root.exists():
                recovery.replace(data_root)
                journal_path.unlink(missing_ok=True)
            elif recovery is None and not data_root.exists():
                journal_path.unlink(missing_ok=True)
            raise
        journal["stage"] = "swap_completed"
        _write_json_atomic(journal_path, journal)
        if settings_target is not None and settings_payload is not None:
            _apply_restored_settings(
                settings_target, settings_payload, dictionary_payload, timestamp
            )
        (data_root / RESTORE_SIDECAR_NAME).unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "PASS",
        "records": verified["records"],
        "data": str(data_root.resolve()),
        "recovery": str(recovery.resolve()) if recovery else None,
        "journal_cleared": not journal_path.exists(),
    }
