from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .archive import ZipBudget, inspect_zip, require_free_space
from .models import Settings, Transcript
from .storage import LocalStore

BACKUP_VERSION = 1
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
        if data_root.exists():
            recovery_name = (
                f"{data_root.name}.recovery-{timestamp}-{uuid.uuid4().hex[:8]}"
            )
            recovery = data_root.parent / recovery_name
            data_root.replace(recovery)
        try:
            temporary.replace(data_root)
        except BaseException:
            if recovery and recovery.exists() and not data_root.exists():
                recovery.replace(data_root)
            raise
        if settings_target and settings_payload is not None:
            if settings_target.exists():
                preserved = settings_target.with_name(
                    f"{settings_target.name}.pre-restore-{timestamp}"
                )
                shutil.copy2(settings_target, preserved)
            settings_target.parent.mkdir(parents=True, exist_ok=True)
            if dictionary_payload is not None:
                dictionary_target = settings_target.parent / "dictionary.restored.json"
                dictionary_target.write_bytes(dictionary_payload)
            settings_target.write_text(
                json.dumps(settings_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "PASS",
        "records": verified["records"],
        "data": str(data_root.resolve()),
        "recovery": str(recovery.resolve()) if recovery else None,
    }
