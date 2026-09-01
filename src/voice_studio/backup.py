from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from . import backup_crypto
from .archive import (
    ZipBudget,
    _portable_member_identity,
    inspect_zip,
    require_free_space,
)
from .models import Settings, Transcript
from .storage import LocalStore

BACKUP_VERSION = 1
ENCRYPTED_BACKUP_VERSION = 2
_PRIVATE_INDEX_LIMIT_BYTES = 16 * 1024**2
RESTORE_JOURNAL_VERSION = 1
RESTORE_SIDECAR_VERSION = 1
RESTORE_SIDECAR_NAME = ".restore-settings.json"
RESTORE_V2_SIDECAR_NAME = ".restore-settings-v2"
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
_RESTORE_STAGES = ("staging_building", "swap_started", "swap_completed")
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


class _JsonlTranscriptStream:
    """Iterator-backed binary stream of JSONL transcript bytes.

    Feeds ``backup_crypto.encrypt_member`` line by line so the
    transcripts payload is never materialized as a single bytes object.
    Only explicitly sized, bounded reads are supported.
    """

    def __init__(self, transcripts: list[Transcript]) -> None:
        self._lines = iter(
            (
                json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            for item in transcripts
        )
        self._pending = bytearray()
        self._exhausted = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise ValueError(
                "unbounded reads are not supported for transcript streaming"
            )
        while len(self._pending) < size and not self._exhausted:
            try:
                self._pending += next(self._lines)
            except StopIteration:
                self._exhausted = True
        chunk = bytes(self._pending[:size])
        del self._pending[:size]
        return chunk


class _LimitedReader:
    """Binary reader enforcing a plaintext format ceiling while streaming."""

    def __init__(self, stream: BinaryIO, limit: int, member: str) -> None:
        self._stream = stream
        self._remaining = limit
        self._member = member

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise ValueError("unbounded reads are not supported for backup members")
        data = self._stream.read(size)
        self._remaining -= len(data)
        if self._remaining < 0:
            raise ValueError(
                f"backup member exceeds its format limit: {self._member}"
            )
        return data


class _HashingWriter:
    """Counting SHA-256 writer around a ZIP member stream."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self._digest.update(data)
        self.size += len(data)
        return self._stream.write(data)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


class _HashingReader:
    """Counting SHA-256 reader around a bounded ZIP member stream."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise ValueError("unbounded reads are not supported for backup members")
        data = self._stream.read(size)
        self._digest.update(data)
        self.size += len(data)
        return data

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


class _DiscardWriter:
    """Bounded counting sink for authenticated plaintext during verify."""

    def __init__(self) -> None:
        self.size = 0

    def write(self, data: bytes) -> int:
        self.size += len(data)
        return len(data)


@dataclass
class _VerifiedV2Context:
    """Internal authenticated v2 context shared by verify and restore.

    Never returned from a public API, never serialized, never written to
    the journal. Holds no passphrase; key bytes live only for the call.
    """

    path: Path
    manifest: dict[str, Any]
    members: dict[str, Any]
    index: dict[str, Any]
    mapping: dict[str, str]
    index_name: str
    master_key: bytes


def _decrypt_v2_payload(
    context: _VerifiedV2Context,
    archive: zipfile.ZipFile,
    opaque: str,
    sink: BinaryIO,
) -> None:
    """Decrypt one payload member into ``sink`` with hash verification."""

    metadata = context.members[opaque]
    member_key = backup_crypto.derive_member_key(context.master_key, opaque)
    with archive.open(opaque) as stream:
        reader = _HashingReader(stream)
        backup_crypto.decrypt_member(
            opaque,
            member_key,
            reader,
            sink,
            plaintext_size=metadata["plaintext_size"],
            chunk_count=metadata["chunks"],
        )
    if (
        reader.hexdigest() != metadata["sha256"]
        or reader.size != metadata["size"]
    ):
        raise ValueError(f"backup member integrity check failed: {opaque}")


_V2_OPAQUE_NAME = re.compile(r"payload/[0-9]{8}\.enc")
_V2_FIXED_LOGICAL_MEMBERS = frozenset(
    {"transcripts.jsonl", "config/settings.json", "config/dictionary.json"}
)


def _read_bounded_payload(path: Path, limit: int, member: str) -> bytes:
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"backup member exceeds its format limit: {member}")
    return payload


def _create_backup_v2(
    store: LocalStore,
    destination: Path,
    *,
    settings_file: Path | None,
    include_audio: bool,
    passphrase: str,
) -> dict[str, Any]:
    """Create an encrypted backup version 2 archive.

    Layout and cryptography follow the W2-E1 design contract
    (``docs/superpowers/plans/2026-08-30-w2-e1-encrypted-backup-design.md``
    section 5): ``manifest.json`` is the only plaintext member, every
    payload lives under a consecutive opaque ``payload/NNNNNNNN.enc``
    name stored with ``ZIP_STORED``, and the logical-name mapping exists
    only inside the encrypted private index. The passphrase and all keys
    are never written, logged or returned.
    """

    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    transcripts = store.list(limit=1_000_000)
    salt = os.urandom(backup_crypto.SALT_SIZE)
    master_key = backup_crypto.derive_master_key(passphrase, salt)
    del passphrase

    payload_openers: dict[str, Callable[[], BinaryIO]] = {
        "transcripts.jsonl": lambda: _LimitedReader(
            _JsonlTranscriptStream(transcripts),
            _FIXED_MEMBER_LIMITS["transcripts.jsonl"],
            "transcripts.jsonl",
        )
    }
    if settings_file and settings_file.is_file():
        settings_payload = _read_bounded_payload(
            settings_file,
            _FIXED_MEMBER_LIMITS["config/settings.json"],
            "config/settings.json",
        )
        payload_openers["config/settings.json"] = (
            lambda payload=settings_payload: io.BytesIO(payload)
        )
        dictionary_payload: bytes | None = None
        dictionary: Path | None = None
        try:
            settings = Settings.from_dict(json.loads(settings_payload))
            candidate = Path(settings.dictionary_path).expanduser()
            if settings.dictionary_path and candidate.is_file():
                dictionary = candidate
        except (OSError, ValueError, json.JSONDecodeError):
            dictionary = None
        if dictionary is not None:
            try:
                dictionary_payload = _read_bounded_payload(
                    dictionary,
                    _FIXED_MEMBER_LIMITS["config/dictionary.json"],
                    "config/dictionary.json",
                )
            except OSError:
                dictionary_payload = None
        if dictionary_payload is not None:
            payload_openers["config/dictionary.json"] = (
                lambda payload=dictionary_payload: io.BytesIO(payload)
            )
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
    for audio_name, audio_path in audio_files.items():
        payload_openers[audio_name] = lambda path=audio_path: path.open("rb")

    total_members = len(payload_openers) + 2  # manifest.json + encrypted index
    if total_members > BACKUP_ZIP_BUDGET.max_members:
        raise ValueError(
            f"backup exceeds the member budget: {total_members} members > "
            f"{BACKUP_ZIP_BUDGET.max_members}"
        )

    index_member = "payload/00000000.enc"
    mapping = {
        logical_name: f"payload/{position + 1:08d}.enc"
        for position, logical_name in enumerate(payload_openers)
    }
    index = {
        "version": ENCRYPTED_BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "records": len(transcripts),
        "include_audio": include_audio,
        "members": mapping,
    }
    index_plaintext = json.dumps(
        index, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    if len(index_plaintext) > _PRIVATE_INDEX_LIMIT_BYTES:
        raise ValueError("backup private index exceeds its format limit")

    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        manifest_members: dict[str, dict[str, Any]] = {}
        with temporary.open("xb") as backing, zipfile.ZipFile(backing, "w") as archive:

            def _write_encrypted_member(
                opaque_name: str, opener: Callable[[], BinaryIO]
            ) -> None:
                member_info = zipfile.ZipInfo(opaque_name)
                member_info.compress_type = zipfile.ZIP_STORED
                member_key = backup_crypto.derive_member_key(master_key, opaque_name)
                with archive.open(member_info, "w") as member_stream:
                    sink = _HashingWriter(member_stream)
                    source = opener()
                    try:
                        plaintext_size, chunks = backup_crypto.encrypt_member(
                            opaque_name, member_key, source, sink
                        )
                    finally:
                        close = getattr(source, "close", None)
                        if close is not None:
                            close()
                manifest_members[opaque_name] = {
                    "sha256": sink.hexdigest(),
                    "size": sink.size,
                    "plaintext_size": plaintext_size,
                    "chunks": chunks,
                }

            for logical_name, opener in payload_openers.items():
                _write_encrypted_member(mapping[logical_name], opener)
            _write_encrypted_member(
                index_member, lambda: io.BytesIO(index_plaintext)
            )

            manifest = {
                "version": ENCRYPTED_BACKUP_VERSION,
                "encryption": {
                    "algorithm": "AES-256-GCM-CHUNKED",
                    "kdf": "argon2id",
                    "kdf_params": {
                        "iterations": backup_crypto.ARGON2_ITERATIONS,
                        "memory_cost_kib": backup_crypto.ARGON2_MEMORY_COST_KIB,
                        "lanes": backup_crypto.ARGON2_LANES,
                    },
                    "salt_base64": base64.b64encode(salt).decode("ascii"),
                    "manifest_tag_base64": "",
                },
                "index_member": index_member,
                "members": manifest_members,
            }
            canonical = json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            manifest_key = backup_crypto.derive_manifest_key(master_key)
            manifest["encryption"]["manifest_tag_base64"] = base64.b64encode(
                backup_crypto.compute_manifest_tag(manifest_key, canonical)
            ).decode("ascii")
            manifest_payload = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            if len(manifest_payload) > _FIXED_MEMBER_LIMITS["manifest.json"]:
                raise ValueError(
                    "backup member exceeds its format limit: manifest.json"
                )
            archive.writestr("manifest.json", manifest_payload)
        inspect_zip(temporary, BACKUP_ZIP_BUDGET)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination.resolve()),
        "records": len(transcripts),
        "audio_files": len(audio_files),
        "version": ENCRYPTED_BACKUP_VERSION,
    }


def create_backup(
    store: LocalStore,
    destination: Path,
    *,
    settings_file: Path | None = None,
    include_audio: bool = True,
    passphrase: str | None = None,
) -> dict[str, Any]:
    if passphrase is not None:
        if not isinstance(passphrase, str):
            raise TypeError("passphrase must be a str or None")
        if passphrase == "":
            raise ValueError("passphrase cannot be empty")
        return _create_backup_v2(
            store,
            destination,
            settings_file=settings_file,
            include_audio=include_audio,
            passphrase=passphrase,
        )
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
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as backing, zipfile.ZipFile(
            backing, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
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


def _read_manifest_for_dispatch(
    archive: zipfile.ZipFile, member_sizes: dict[str, int]
) -> tuple[list[str], dict[str, Any]]:
    """Run the shared duplicate/path/bounded-manifest checks and parse it."""

    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("backup contains duplicate members")
    if "manifest.json" not in names:
        raise ValueError("backup manifest is missing")
    for name in names:
        member = Path(name)
        if member.is_absolute() or ".." in member.parts or "\\" in name:
            raise ValueError(f"unsafe backup member: {name}")
    manifest_size = member_sizes["manifest.json"]
    manifest_limit = _FIXED_MEMBER_LIMITS["manifest.json"]
    if manifest_size > manifest_limit:
        raise ValueError(
            "backup member exceeds its format limit: "
            f"manifest.json ({manifest_size} bytes)"
        )
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as exc:
        raise ValueError(f"invalid backup manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("backup manifest must contain a JSON object")
    return names, manifest


def verify_backup(path: Path, *, passphrase: str | None = None) -> dict[str, Any]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    inspection = inspect_zip(path, BACKUP_ZIP_BUDGET)
    member_sizes = {member.name: member.expanded_bytes for member in inspection.members}
    with zipfile.ZipFile(path) as archive:
        names, manifest = _read_manifest_for_dispatch(archive, member_sizes)
        # Dispatch is keyed on the manifest version only: a v2 archive never
        # falls back into the v1 parser, whatever the failure.
        version = manifest.get("version")
        if type(version) is not int:
            raise ValueError(f"unsupported backup version: {version}")
        if version == ENCRYPTED_BACKUP_VERSION:
            return _verify_backup_v2(
                path, archive, member_sizes, names, manifest, passphrase
            )
        if version == BACKUP_VERSION:
            return _verify_backup_v1(
                path, archive, inspection, member_sizes, names, manifest
            )
        raise ValueError(f"unsupported backup version: {version}")


def _verify_backup_v1(
    path: Path,
    archive: zipfile.ZipFile,
    inspection: Any,
    member_sizes: dict[str, int],
    names: list[str],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    for name, size in member_sizes.items():
        if name in _FIXED_MEMBER_LIMITS and size > _FIXED_MEMBER_LIMITS[name]:
            raise ValueError(
                f"backup member exceeds its format limit: {name} ({size} bytes)"
            )
        if name not in _FIXED_MEMBER_LIMITS and not name.startswith("sources/"):
            raise ValueError(f"unsupported backup member: {name}")
    if type(manifest.get("version")) is not int or (
        manifest.get("version") != BACKUP_VERSION
    ):
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


def _v2_member_metadata_error(name: str) -> ValueError:
    return ValueError(f"encrypted backup member metadata is invalid: {name}")


def _verify_v2_member_metadata(
    name: str, expected: Any, member_sizes: dict[str, int]
) -> None:
    """Validate one manifest member entry against the authenticated contract."""

    if not isinstance(expected, dict) or set(expected) != {
        "sha256",
        "size",
        "plaintext_size",
        "chunks",
    }:
        raise _v2_member_metadata_error(name)
    expected_hash = expected["sha256"]
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise _v2_member_metadata_error(name)
    size = expected["size"]
    plaintext_size = expected["plaintext_size"]
    chunks = expected["chunks"]
    if (
        type(size) is not int
        or type(plaintext_size) is not int
        or type(chunks) is not int
        or size < backup_crypto.GCM_TAG_SIZE
        or plaintext_size < 0
        or chunks < 1
        or size != plaintext_size + backup_crypto.GCM_TAG_SIZE * chunks
        or chunks != max(1, -(-plaintext_size // backup_crypto.CHUNK_SIZE))
    ):
        raise _v2_member_metadata_error(name)
    if member_sizes.get(name) != size:
        raise ValueError(
            f"encrypted backup member size does not match the archive: {name}"
        )


def _strict_base64(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"encrypted backup {field} must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"encrypted backup {field} is not valid base64") from exc


def _prepare_v2_context(
    path: Path,
    archive: zipfile.ZipFile,
    member_sizes: dict[str, int],
    names: list[str],
    manifest: dict[str, Any],
    passphrase: str | None,
) -> _VerifiedV2Context:
    """Authenticate structure, KDF, manifest HMAC and the private index.

    Does not decrypt non-index payloads. The returned context is internal:
    it is never exposed from a public API, serialized or journaled, and
    its key bytes live only for the duration of the call.
    """

    if passphrase is None:
        raise ValueError("backup is encrypted; a passphrase is required")
    if not isinstance(passphrase, str):
        raise TypeError("passphrase must be a str or None")

    # --- Structural validation: no KDF or decryption before this completes.
    if set(manifest) != {"version", "encryption", "index_member", "members"}:
        raise ValueError("encrypted backup manifest has unexpected fields")
    if type(manifest["version"]) is not int or (
        manifest["version"] != ENCRYPTED_BACKUP_VERSION
    ):
        raise ValueError(f"unsupported backup version: {manifest['version']}")
    if manifest["index_member"] != "payload/00000000.enc":
        raise ValueError("encrypted backup index member is invalid")
    members = manifest["members"]
    if not isinstance(members, dict) or not members:
        raise ValueError("encrypted backup manifest members must be an object")
    encryption = manifest["encryption"]
    if not isinstance(encryption, dict) or set(encryption) != {
        "algorithm",
        "kdf",
        "kdf_params",
        "salt_base64",
        "manifest_tag_base64",
    }:
        raise ValueError("encrypted backup encryption metadata is invalid")
    if encryption["algorithm"] != "AES-256-GCM-CHUNKED":
        raise ValueError(
            f"unsupported backup encryption algorithm: {encryption['algorithm']}"
        )
    if encryption["kdf"] != "argon2id":
        raise ValueError(f"unsupported backup KDF: {encryption['kdf']}")
    salt = _strict_base64(encryption["salt_base64"], "salt")
    if not 16 <= len(salt) <= 32:
        raise ValueError("encrypted backup salt size is out of bounds")
    tag = _strict_base64(encryption["manifest_tag_base64"], "manifest tag")
    if len(tag) != backup_crypto.KEY_SIZE:
        raise ValueError("encrypted backup manifest tag must be 32 bytes")
    kdf_params = encryption["kdf_params"]
    if not isinstance(kdf_params, dict) or set(kdf_params) != {
        "iterations",
        "memory_cost_kib",
        "lanes",
    }:
        raise ValueError("encrypted backup KDF parameters are invalid")
    iterations = kdf_params["iterations"]
    memory_cost = kdf_params["memory_cost_kib"]
    lanes = kdf_params["lanes"]
    if (
        type(iterations) is not int
        or not 1 <= iterations <= 10
        or type(memory_cost) is not int
        or not 1024 <= memory_cost <= 262144
        or type(lanes) is not int
        or not 1 <= lanes <= 4
    ):
        raise ValueError("unsupported backup KDF parameters")
    if (iterations, memory_cost, lanes) != (
        backup_crypto.ARGON2_ITERATIONS,
        backup_crypto.ARGON2_MEMORY_COST_KIB,
        backup_crypto.ARGON2_LANES,
    ) or len(salt) != backup_crypto.SALT_SIZE:
        raise ValueError("unsupported backup KDF profile")
    for name in members:
        if _V2_OPAQUE_NAME.fullmatch(name) is None:
            raise ValueError(f"encrypted backup member name is invalid: {name}")
    if sorted(members) != [
        f"payload/{position:08d}.enc" for position in range(len(members))
    ]:
        raise ValueError("encrypted backup members must be consecutive from zero")
    if set(names) != {"manifest.json", *members}:
        raise ValueError("backup member set does not match manifest")
    for name in members:
        if archive.getinfo(name).compress_type != zipfile.ZIP_STORED:
            raise ValueError(f"encrypted backup member must be ZIP_STORED: {name}")
    for name, expected in members.items():
        _verify_v2_member_metadata(name, expected, member_sizes)
    index_name = manifest["index_member"]
    index_plaintext_size = members[index_name]["plaintext_size"]
    if index_plaintext_size > _PRIVATE_INDEX_LIMIT_BYTES:
        raise ValueError("encrypted backup private index exceeds its format limit")

    # --- Manifest authentication: no private index byte is parsed before this.
    master_key = backup_crypto.derive_master_key(passphrase, salt)
    del passphrase
    manifest_key = backup_crypto.derive_manifest_key(master_key)
    unsigned = json.loads(json.dumps(manifest))
    unsigned["encryption"]["manifest_tag_base64"] = ""
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    backup_crypto.verify_manifest_tag(manifest_key, canonical, tag)

    # --- Private index: authenticate, decrypt once, then parse and validate.
    index_metadata = members[index_name]
    index_key = backup_crypto.derive_member_key(master_key, index_name)
    with archive.open(index_name) as stream:
        reader = _HashingReader(stream)
        index_buffer = io.BytesIO()
        backup_crypto.decrypt_member(
            index_name,
            index_key,
            reader,
            index_buffer,
            plaintext_size=index_metadata["plaintext_size"],
            chunk_count=index_metadata["chunks"],
        )
    if (
        reader.hexdigest() != index_metadata["sha256"]
        or reader.size != index_metadata["size"]
    ):
        raise ValueError(f"backup member integrity check failed: {index_name}")
    try:
        index = json.loads(index_buffer.getvalue())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"encrypted backup private index is invalid: {exc}") from exc
    index_error = ValueError("encrypted backup private index is invalid")
    if not isinstance(index, dict) or set(index) != {
        "version",
        "created_at",
        "records",
        "include_audio",
        "members",
    }:
        raise index_error
    if type(index["version"]) is not int or (
        index["version"] != ENCRYPTED_BACKUP_VERSION
    ):
        raise index_error
    if not isinstance(index["created_at"], str) or not index["created_at"]:
        raise index_error
    if type(index["records"]) is not int or index["records"] < 0:
        raise index_error
    if type(index["include_audio"]) is not bool:
        raise index_error
    mapping = index["members"]
    if not isinstance(mapping, dict) or not all(
        isinstance(logical, str) and isinstance(opaque, str)
        for logical, opaque in mapping.items()
    ):
        raise index_error
    values = list(mapping.values())
    if len(set(values)) != len(values) or index_name in values:
        raise ValueError("encrypted backup private index mapping is not one-to-one")
    if set(values) != set(members) - {index_name}:
        raise ValueError(
            "encrypted backup private index mapping does not match the manifest"
        )
    if "transcripts.jsonl" not in mapping:
        raise ValueError(
            "encrypted backup private index is missing transcripts.jsonl"
        )
    logical_identities: set[tuple[str, ...]] = set()
    for logical in mapping:
        try:
            portable_identity = _portable_member_identity(logical, False)
        except ValueError:
            raise ValueError(
                "encrypted backup private index has an unsafe member"
            ) from None
        if portable_identity in logical_identities:
            raise ValueError(
                "encrypted backup private index has a portable member alias"
            )
        logical_identities.add(portable_identity)
        logical_path = Path(logical)
        if (
            logical_path.is_absolute()
            or ".." in logical_path.parts
            or "\\" in logical
        ):
            raise ValueError(
                "encrypted backup private index has an unsafe member"
            )
        if logical in _V2_FIXED_LOGICAL_MEMBERS:
            continue
        if logical.startswith("sources/"):
            filename = logical[len("sources/") :]
            if (
                not filename
                or Path(filename).name != filename
                or not index["include_audio"]
            ):
                raise ValueError(
                    "encrypted backup private index has an unsafe member"
                )
            continue
        raise ValueError(
            "encrypted backup private index has an unsupported member"
        )
    if (
        "config/dictionary.json" in mapping
        and "config/settings.json" not in mapping
    ):
        raise ValueError(
            "encrypted backup private index maps a dictionary without settings"
        )
    for logical, opaque in mapping.items():
        limit = _FIXED_MEMBER_LIMITS.get(logical)
        if limit is not None and members[opaque]["plaintext_size"] > limit:
            raise ValueError(f"backup member exceeds its format limit: {logical}")

    return _VerifiedV2Context(
        path=path,
        manifest=manifest,
        members=members,
        index=index,
        mapping=mapping,
        index_name=index_name,
        master_key=master_key,
    )


def _verify_backup_v2(
    path: Path,
    archive: zipfile.ZipFile,
    member_sizes: dict[str, int],
    names: list[str],
    manifest: dict[str, Any],
    passphrase: str | None,
) -> dict[str, Any]:
    """Fully authenticate an encrypted backup v2 archive.

    Order: structural validation, KDF bounds and exact profile, manifest
    HMAC, private index AEAD decryption and validation, then exactly one
    streaming chunk-authenticated pass over every payload member. A PASS
    is impossible after any partial check. No passphrase, key, private
    index or mapping value is returned.
    """

    context = _prepare_v2_context(
        path, archive, member_sizes, names, manifest, passphrase
    )
    # --- Payload verification: each non-index member exactly once, streaming
    # ciphertext through a bounded hashing reader into a discard sink.
    expanded_bytes = 0
    for opaque in context.mapping.values():
        metadata = context.members[opaque]
        sink = _DiscardWriter()
        _decrypt_v2_payload(context, archive, opaque, sink)
        if sink.size != metadata["plaintext_size"]:
            raise ValueError(f"backup member integrity check failed: {opaque}")
        expanded_bytes += metadata["plaintext_size"]
    return {
        "status": "PASS",
        "path": str(path.resolve()),
        "version": ENCRYPTED_BACKUP_VERSION,
        "records": context.index["records"],
        "members": len(context.mapping),
        "expanded_bytes": expanded_bytes,
        "manifest": context.manifest,
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


def _write_v2_settings_sidecar(
    staging: Path,
    archive: zipfile.ZipFile,
    context: _VerifiedV2Context,
) -> None:
    """Park the authenticated config ciphertext inside staging.

    The sidecar holds the plaintext manifest plus the raw ciphertext of
    the private index, settings and (optional) dictionary members. It
    contains no plaintext settings, no passphrase and no key material,
    so a restore interrupted after the swap never leaves secrets on disk.
    """

    sidecar = staging / RESTORE_V2_SIDECAR_NAME
    payload_dir = sidecar / "payload"
    payload_dir.mkdir(parents=True)
    (sidecar / "manifest.json").write_text(
        json.dumps(context.manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    members = {context.index_name, context.mapping["config/settings.json"]}
    dictionary_opaque = context.mapping.get("config/dictionary.json")
    if dictionary_opaque is not None:
        members.add(dictionary_opaque)
    for opaque in sorted(members):
        with archive.open(opaque) as source, (
            payload_dir / Path(opaque).name
        ).open("wb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)


class _SidecarV2Archive:
    """Read-only ``payload/*.enc`` member view of an encrypted sidecar.

    Provides the ``open`` interface ``_decrypt_v2_payload`` needs so the
    same authenticated decryption path serves archives and sidecars.
    """

    def __init__(self, payload_dir: Path) -> None:
        self._payload_dir = payload_dir

    def open(self, name: str) -> BinaryIO:
        if _V2_OPAQUE_NAME.fullmatch(name) is None:
            raise ValueError(f"unsafe backup member: {name}")
        return (self._payload_dir / Path(name).name).open("rb")

    def getinfo(self, name: str) -> zipfile.ZipInfo:
        if _V2_OPAQUE_NAME.fullmatch(name) is None:
            raise ValueError(f"unsafe backup member: {name}")
        info = zipfile.ZipInfo(name)
        info.compress_type = zipfile.ZIP_STORED
        return info


def _inspect_v2_settings_sidecar(
    sidecar: Path,
) -> tuple[Path, Path, dict[str, int]]:
    """Validate the sidecar tree without following reparse points."""

    info = os.lstat(sidecar)
    if _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("encrypted restore sidecar is not a safe directory")
    if {entry.name for entry in os.scandir(sidecar)} != {"manifest.json", "payload"}:
        raise ValueError("encrypted restore sidecar contains unexpected entries")
    manifest_file = sidecar / "manifest.json"
    manifest_info = os.lstat(manifest_file)
    if _is_reparse_point(manifest_info) or not stat.S_ISREG(manifest_info.st_mode):
        raise ValueError("encrypted restore sidecar manifest is not a safe file")
    if manifest_info.st_size > _FIXED_MEMBER_LIMITS["manifest.json"]:
        raise ValueError("encrypted restore sidecar manifest exceeds its limit")
    payload_dir = sidecar / "payload"
    payload_info = os.lstat(payload_dir)
    if _is_reparse_point(payload_info) or not stat.S_ISDIR(payload_info.st_mode):
        raise ValueError("encrypted restore sidecar payload is not a safe directory")
    payload_sizes: dict[str, int] = {}
    for entry in payload_dir.iterdir():
        entry_info = os.lstat(entry)
        opaque = f"payload/{entry.name}"
        if (
            _V2_OPAQUE_NAME.fullmatch(opaque) is None
            or _is_reparse_point(entry_info)
            or not stat.S_ISREG(entry_info.st_mode)
        ):
            raise ValueError(
                f"encrypted restore sidecar contains an unsafe member: {entry.name}"
            )
        payload_sizes[opaque] = entry_info.st_size
    return manifest_file, payload_dir, payload_sizes


def _remove_v2_settings_sidecar(data_root: Path) -> None:
    sidecar = data_root / RESTORE_V2_SIDECAR_NAME
    try:
        _inspect_v2_settings_sidecar(sidecar)
    except FileNotFoundError:
        return
    shutil.rmtree(sidecar)


def _recover_v2_settings(
    data_root: Path, settings_target: Path, passphrase: str
) -> None:
    """Re-authenticate the encrypted sidecar and apply parked settings.

    Raises ``ValueError``/``OSError`` on any mismatch; the sidecar is
    preserved so the user can retry with the correct passphrase. No
    plaintext fallback: a wrong passphrase or any tampering fails closed.
    """

    if not isinstance(passphrase, str):
        raise TypeError("passphrase must be a str or None")
    sidecar = data_root / RESTORE_V2_SIDECAR_NAME
    manifest_file, payload_dir, payload_sizes = _inspect_v2_settings_sidecar(sidecar)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"encrypted restore sidecar manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("encrypted restore sidecar manifest must contain a JSON object")
    declared_members = manifest.get("members")
    if not isinstance(declared_members, dict):
        raise ValueError("encrypted restore sidecar members must be an object")
    archive = _SidecarV2Archive(payload_dir)
    declared_sizes = {
        name: (
            metadata.get("size", -1)
            if isinstance(metadata, dict) and type(metadata.get("size")) is int
            else -1
        )
        for name, metadata in declared_members.items()
    }
    context = _prepare_v2_context(
        sidecar,
        archive,
        declared_sizes,
        ["manifest.json", *declared_members],
        manifest,
        passphrase,
    )
    members = context.members
    index_name = context.index_name
    mapping = context.mapping
    settings_opaque = mapping.get("config/settings.json")
    if not isinstance(settings_opaque, str) or settings_opaque not in members:
        raise ValueError("encrypted restore sidecar has no settings payload")
    dictionary_opaque = mapping.get("config/dictionary.json")
    if dictionary_opaque is not None and (
        not isinstance(dictionary_opaque, str) or dictionary_opaque not in members
    ):
        raise ValueError("encrypted restore sidecar dictionary payload is invalid")
    expected = {index_name, settings_opaque}
    if dictionary_opaque is not None:
        expected.add(dictionary_opaque)
    if set(payload_sizes) != expected:
        raise ValueError(
            "encrypted restore sidecar members do not match the authenticated index"
        )
    for logical, opaque in (
        ("config/settings.json", settings_opaque),
        ("config/dictionary.json", dictionary_opaque),
    ):
        if opaque is None:
            continue
        _verify_v2_member_metadata(opaque, members[opaque], payload_sizes)
        if members[opaque]["plaintext_size"] > _FIXED_MEMBER_LIMITS[logical]:
            raise ValueError(
                f"backup member exceeds its format limit: {logical}"
            )
    settings_buffer = io.BytesIO()
    _decrypt_v2_payload(context, archive, settings_opaque, settings_buffer)
    try:
        settings_payload = json.loads(settings_buffer.getvalue().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"backup settings are invalid: {exc}") from exc
    if not isinstance(settings_payload, dict):
        raise ValueError("backup settings must contain a JSON object")
    dictionary_payload: bytes | None = None
    if dictionary_opaque is not None:
        dictionary_buffer = io.BytesIO()
        _decrypt_v2_payload(context, archive, dictionary_opaque, dictionary_buffer)
        dictionary_payload = dictionary_buffer.getvalue()
        settings_payload["dictionary_path"] = str(
            settings_target.parent / "dictionary.restored.json"
        )
    Settings.from_dict(settings_payload)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    _apply_restored_settings(
        settings_target, settings_payload, dictionary_payload, timestamp
    )


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
    backup_version = journal.get("backup_version")
    if backup_version not in (BACKUP_VERSION, ENCRYPTED_BACKUP_VERSION):
        raise ValueError(
            f"unsupported restore journal backup version: {backup_version}"
        )
    stage = journal.get("stage")
    if stage not in _RESTORE_STAGES:
        raise ValueError(f"unsupported restore journal stage: {stage}")
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
    if stage == "staging_building":
        # staging_building exists only for encrypted backups before the swap:
        # no recovery directory yet, and the staging name must be exactly the
        # contained `.<data>.restore-<uuidhex>` the restore created.
        if backup_version != ENCRYPTED_BACKUP_VERSION:
            raise ValueError(
                "restore journal stage staging_building requires backup version 2"
            )
        if recovery is not None:
            raise ValueError(
                "restore journal staging_building must not have a recovery path"
            )
        staging_name = Path(staging).name
        prefix = f".{data_root.name}.restore-"
        suffix = (
            staging_name[len(prefix) :] if staging_name.startswith(prefix) else ""
        )
        if len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
            raise ValueError("restore journal staging path has an unexpected name")
    if (
        backup_version == ENCRYPTED_BACKUP_VERSION
        and journal.get("settings_payload_written") is False
        and (
            not isinstance(journal.get("settings_target"), str)
            or not journal["settings_target"]
        )
    ):
        raise ValueError("restore journal settings target is invalid")
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
                info = os.lstat(entry_path)
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
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Finish or undo a restore that a process death left half applied.

    Deterministic and non-interactive. A ``*.recovery-*`` directory is never
    deleted. Before a v2 swap starts, the contained incomplete staging can be
    discarded whether the live root existed before restore or not; after swap
    start, staging is discarded only once ``data_root`` is known to be intact.
    When an encrypted (v2) restore died with the settings payload pending,
    ``passphrase`` is required to re-authenticate the encrypted
    ``.restore-settings-v2`` sidecar; without it the call returns
    ``passphrase_required`` and changes nothing.
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

    def _settings_step() -> dict[str, Any] | None:
        if journal.get("settings_payload_written") is True:
            if journal.get("backup_version") == ENCRYPTED_BACKUP_VERSION:
                _remove_v2_settings_sidecar(data_root)
            return None
        if journal.get("backup_version") == ENCRYPTED_BACKUP_VERSION:
            target = settings_target
            if target is None and journal.get("settings_target"):
                target = Path(journal["settings_target"])
            if passphrase is None:
                # Report, mutate nothing, keep sidecar and journal for retry.
                return {
                    "status": "PASS",
                    "action": "passphrase_required",
                    "records": expected_records,
                    "recovery": recovery_value,
                }
            assert target is not None
            _recover_v2_settings(data_root, target.expanduser(), passphrase)
            journal["settings_payload_written"] = True
            _write_json_atomic(journal_path, journal)
            _remove_v2_settings_sidecar(data_root)
            return None
        _finish_restored_settings(data_root, settings_target)
        return None

    if journal["stage"] == "staging_building":
        # The live root was never renamed on this stage. Remove only the
        # strictly contained incomplete staging and the journal; never touch
        # the live data root, user originals or recovery directories.
        try:
            if staging.is_dir():
                shutil.rmtree(staging)
            elif staging.exists():
                return _fail(
                    "interrupted restore staging is not a directory; "
                    "nothing was removed",
                    recovery=recovery_value,
                )
        except OSError as exc:
            return _fail(f"interrupted restore staging could not be removed: {exc}")
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "staging_discarded",
            "records": expected_records,
            "recovery": recovery_value,
        }

    if journal["stage"] == "swap_completed":
        try:
            early = _settings_step()
        except (OSError, ValueError) as exc:
            return _fail(f"restored settings could not be written: {exc}")
        if early is not None:
            return early
        journal_path.unlink(missing_ok=True)
        return {
            "status": "PASS",
            "action": "settings_completed",
            "records": expected_records,
            "recovery": recovery_value,
        }

    promoted_sidecar_name = (
        RESTORE_V2_SIDECAR_NAME
        if journal.get("backup_version") == ENCRYPTED_BACKUP_VERSION
        else RESTORE_SIDECAR_NAME
    )
    settings_were_promoted = (
        journal.get("settings_payload_written") is False
        and not staging.exists()
        and (data_root / promoted_sidecar_name).exists()
        and _restored_store_is_sound(data_root, expected_records)
    )
    if settings_were_promoted:
        # The staging rename completed, but the process died before the
        # swap_completed journal write. The parked sidecar inside the sound
        # promoted store distinguishes this state from an untouched live
        # root, for the plaintext v1 sidecar and the authenticated v2 one
        # alike. Persist the transition before applying settings (v1) or
        # asking for/applying a passphrase (v2).
        journal["stage"] = "swap_completed"
        try:
            _write_json_atomic(journal_path, journal)
            early = _settings_step()
        except (OSError, ValueError) as exc:
            return _fail(f"restored settings could not be written: {exc}")
        if early is not None:
            return early
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
        journal["stage"] = "swap_completed"
        _write_json_atomic(journal_path, journal)
        try:
            early = _settings_step()
        except (OSError, ValueError) as exc:
            return _fail(f"restored settings could not be written: {exc}")
        if early is not None:
            return early
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
    passphrase: str | None = None,
) -> dict[str, Any]:
    # Dispatch on the manifest version only. A v1 archive with a passphrase
    # takes the unchanged v1 path (the passphrase is ignored); a v2 archive
    # without one fails inside v2 preparation with the exact contract error.
    if _peek_backup_version(path) == ENCRYPTED_BACKUP_VERSION:
        return _restore_backup_v2(
            path, data_root, settings_target=settings_target, passphrase=passphrase
        )
    verified = verify_backup(path)
    data_root = data_root.expanduser()
    data_root.parent.mkdir(parents=True, exist_ok=True)
    local_bytes = _local_restore_bytes(data_root)
    require_free_space(
        data_root.parent,
        verified["expanded_bytes"] + local_bytes,
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
        _copy_local_restore_state(data_root, temporary)
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


def _peek_backup_version(path: Path) -> Any:
    """Best-effort manifest version peek for restore dispatch.

    Any failure returns None so the caller falls into the standard path,
    which re-reads the archive under full validation and raises the
    proper concrete error.
    """

    try:
        with zipfile.ZipFile(path) as archive:
            if (
                archive.getinfo("manifest.json").file_size
                > _FIXED_MEMBER_LIMITS["manifest.json"]
            ):
                return None
            manifest = json.loads(archive.read("manifest.json"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError,
            UnicodeDecodeError):
        return None
    if isinstance(manifest, dict):
        return manifest.get("version")
    return None


_TRANSCRIPT_STAGING_TEMP_NAME = ".restore-transcripts.jsonl.tmp"


def _restore_v2_payloads(
    context: _VerifiedV2Context,
    archive: zipfile.ZipFile,
    store: LocalStore,
    staging: Path,
    settings_target: Path | None,
) -> tuple[dict[str, Any] | None, bytes | None]:
    """Decrypt each payload exactly once into authenticated staging.

    Transcripts stream into a fixed internal temp file inside staging,
    are parsed line by line, and the temp file is deleted before the
    audit. Audio streams directly into ``staging/sources/<safe name>``.
    When settings recovery is pending, the config payloads are decrypted
    once into bounded memory and validated like the v1 pre-swap path;
    every other payload is authenticated once into a discard sink
    (orphaned audio is never restored).
    """

    mapping = context.mapping
    settings_pending = (
        settings_target is not None and "config/settings.json" in mapping
    )
    settings_payload: dict[str, Any] | None = None
    dictionary_payload: bytes | None = None
    decrypted = {mapping["transcripts.jsonl"]}
    transcripts_temp = staging / _TRANSCRIPT_STAGING_TEMP_NAME
    with transcripts_temp.open("wb") as sink:
        _decrypt_v2_payload(context, archive, mapping["transcripts.jsonl"], sink)
    try:
        with transcripts_temp.open("rb") as stream:
            for raw_line in stream:
                line = raw_line.decode("utf-8")
                if not line.strip():
                    continue
                transcript = Transcript.from_dict(json.loads(line))
                source_name = (
                    Path(transcript.source_path).name if transcript.source_path else ""
                )
                logical = f"sources/{source_name}" if source_name else ""
                if logical and logical in mapping:
                    target = staging / "sources" / source_name
                    opaque = mapping[logical]
                    if opaque not in decrypted:
                        with target.open("wb") as sink:
                            _decrypt_v2_payload(context, archive, opaque, sink)
                        decrypted.add(opaque)
                    transcript.source_path = str(target)
                    transcript.audio_retained = True
                else:
                    transcript.source_path = None
                    transcript.audio_retained = False
                store.save(transcript)
    finally:
        transcripts_temp.unlink(missing_ok=True)
    for logical, opaque in mapping.items():
        if opaque in decrypted:
            continue
        if settings_pending and logical in (
            "config/settings.json",
            "config/dictionary.json",
        ):
            if context.members[opaque]["plaintext_size"] > _FIXED_MEMBER_LIMITS[logical]:
                raise ValueError(
                    f"backup member exceeds its format limit: {logical}"
                )
            buffer = io.BytesIO()
            _decrypt_v2_payload(context, archive, opaque, buffer)
            payload = buffer.getvalue()
            if logical == "config/settings.json":
                candidate = json.loads(payload.decode("utf-8"))
                if not isinstance(candidate, dict):
                    raise ValueError("backup settings must contain a JSON object")
                settings_payload = candidate
            else:
                dictionary_payload = payload
        else:
            _decrypt_v2_payload(context, archive, opaque, _DiscardWriter())
        decrypted.add(opaque)
    if settings_payload is not None:
        if dictionary_payload is not None and settings_target is not None:
            settings_payload["dictionary_path"] = str(
                settings_target.parent / "dictionary.restored.json"
            )
        Settings.from_dict(settings_payload)
    return settings_payload, dictionary_payload


def _restore_backup_v2(
    path: Path,
    data_root: Path,
    *,
    settings_target: Path | None,
    passphrase: str | None,
) -> dict[str, Any]:
    """Restore an encrypted backup v2 archive.

    Archive structure, the manifest and the private index are authenticated
    before the first filesystem mutation. The ``staging_building`` journal
    exists before the first plaintext byte is written; each payload chunk is
    authenticated before it is written into contained staging. The two-phase
    swap happens only after every payload decrypted exactly once, the record
    count matches and the staging store audits clean. When settings recovery
    is requested and possible, the config ciphertext is parked in an encrypted
    ``.restore-settings-v2`` sidecar inside staging before the swap and
    applied after ``swap_completed``; a hard process death
    (KeyboardInterrupt/SystemExit) leaves sidecar and journal for
    ``recover_interrupted_restore``, which needs the passphrase again.
    Ordinary exceptions clean staging and the journal.
    """

    path = path.expanduser()
    data_root = data_root.expanduser()

    # --- Authenticate structure, manifest and private index before mutation.
    # Non-index payloads are authenticated later, per chunk into staging; the
    # private index lives only in bounded memory at this point.
    inspection = inspect_zip(path, BACKUP_ZIP_BUDGET)
    member_sizes = {member.name: member.expanded_bytes for member in inspection.members}
    with zipfile.ZipFile(path) as archive:
        names, manifest = _read_manifest_for_dispatch(archive, member_sizes)
        version = manifest.get("version")
        if type(version) is not int or version != ENCRYPTED_BACKUP_VERSION:
            raise ValueError(f"unsupported backup version: {version}")
        context = _prepare_v2_context(
            path, archive, member_sizes, names, manifest, passphrase
        )

    # --- Settings recovery (C2): when the caller asked for settings and the
    # archive carries them, the decrypted payload is parked as ciphertext in
    # an encrypted sidecar inside staging before the swap and applied only
    # after the swap completes.
    settings_pending = (
        settings_target is not None and "config/settings.json" in context.mapping
    )

    expanded_bytes = sum(
        context.members[opaque]["plaintext_size"]
        for opaque in context.mapping.values()
    )
    local_bytes = _local_restore_bytes(data_root)
    require_free_space(
        data_root.parent,
        expanded_bytes + local_bytes,
        margin_bytes=BACKUP_FREE_SPACE_MARGIN_BYTES,
    )

    temporary = data_root.parent / f".{data_root.name}.restore-{uuid.uuid4().hex}"
    journal_path = restore_journal_path(data_root)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    journal = {
        "journal_version": RESTORE_JOURNAL_VERSION,
        "backup_version": ENCRYPTED_BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "data_root": str(data_root.resolve()),
        "staging_path": str(temporary),
        "recovery_path": None,
        "expected_records": int(context.index["records"]),
        "settings_target": str(settings_target) if settings_pending else None,
        "settings_payload_written": not settings_pending,
        "stage": "staging_building",
    }
    # The journal exists before the first plaintext byte hits the disk.
    _write_json_atomic(journal_path, journal)
    recovery: Path | None = None
    try:
        store = LocalStore(temporary)
        with zipfile.ZipFile(path) as archive:
            settings_payload, dictionary_payload = _restore_v2_payloads(
                context, archive, store, temporary, settings_target
            )
            if settings_pending:
                # Park the authenticated config ciphertext (index + settings +
                # dictionary) inside staging before the swap, so a hard death
                # after the swap never leaves plaintext settings behind.
                _write_v2_settings_sidecar(temporary, archive, context)
        restored_records = len(store.list(limit=1_000_000))
        if restored_records != context.index["records"]:
            raise ValueError(
                "backup restore record count does not match manifest: "
                f"expected {context.index['records']}, got {restored_records}"
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
        _copy_local_restore_state(data_root, temporary)
        # Every payload is authenticated and the staging store audited; only
        # now may the journal advance towards the swap.
        if data_root.exists():
            recovery = data_root.parent / (
                f"{data_root.name}.recovery-{timestamp}-{uuid.uuid4().hex[:8]}"
            )
        journal["recovery_path"] = str(recovery) if recovery is not None else None
        journal["stage"] = "swap_started"
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
        if settings_pending and settings_payload is not None:
            _apply_restored_settings(
                settings_target, settings_payload, dictionary_payload, timestamp
            )
            journal["settings_payload_written"] = True
            _write_json_atomic(journal_path, journal)
            # Once the completion marker is durable, recovery can safely
            # finish sidecar cleanup without asking for the passphrase again.
            _remove_v2_settings_sidecar(data_root)
        journal_path.unlink(missing_ok=True)
    except Exception:
        # Before the completed swap, ordinary failures remove incomplete
        # staging and its journal. Once the swap completed, keep the encrypted
        # sidecar and journal so settings recovery can be retried safely.
        if journal["stage"] == "swap_completed":
            raise
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        raise
    return {
        "status": "PASS",
        "records": context.index["records"],
        "data": str(data_root.resolve()),
        "recovery": str(recovery.resolve()) if recovery else None,
        "journal_cleared": not journal_path.exists(),
    }
