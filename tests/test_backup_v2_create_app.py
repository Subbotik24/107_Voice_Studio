"""Tests for encrypted backup v2 creation (W2-E1 Slice B1).

Contract source:
docs/superpowers/plans/2026-08-30-w2-e1-encrypted-backup-design.md
and the Slice B1 task brief. Decryption in these tests uses only the
Slice A ``backup_crypto`` primitives. Successful ``verify_backup`` on a
v2 archive is explicitly out of scope (Slice B2).
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from voice_studio import backup as backup_module
from voice_studio import backup_crypto
from voice_studio.backup import create_backup
from voice_studio.backup_crypto import CHUNK_SIZE
from voice_studio.config import save_settings
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic slice-b1 passphrase"
CHUNK = CHUNK_SIZE


def _transcript(item_id: str, source_hash: str, source_path: str, raw: str = "raw") -> Transcript:
    return Transcript(
        id=item_id,
        created_at="2026-07-27T00:00:00+00:00",
        source_name="safe.wav",
        source_sha256=source_hash,
        source_path=source_path,
        language="uk",
        engine="fixture",
        model="fixture",
        raw_text=raw,
        corrected_text=f"corrected {item_id}",
    )


def _seed(tmp_path: Path, make_wav, *, records: int = 1, audio: bool = True):
    store = LocalStore(tmp_path / "store")
    managed_files: list[Path] = []
    digests: list[str] = []
    for index in range(records):
        original = make_wav(tmp_path / f"original-{index}.wav")
        managed, digest = store.import_source(original)
        managed_files.append(managed)
        digests.append(digest)
        store.save(_transcript(f"rec-{index}", digest, str(managed) if audio else ""))
    return store, managed_files, digests


def _with_settings(tmp_path: Path) -> Path:
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text('{"replacements":[["тех","технічний"]]}', encoding="utf-8")
    settings_file = tmp_path / "config" / "settings.json"
    save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    return settings_file


def _expected_jsonl(store: LocalStore) -> bytes:
    transcripts = store.list(limit=1_000_000)
    return (
        "\n".join(
            json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True)
            for item in transcripts
        )
        + ("\n" if transcripts else "")
    ).encode("utf-8")


def _read_v2(path: Path, passphrase: str):
    """Authenticate and decrypt a v2 archive with Slice A primitives only."""
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        encryption = manifest["encryption"]
        salt = base64.b64decode(encryption["salt_base64"])
        tag = base64.b64decode(encryption["manifest_tag_base64"])
        master_key = backup_crypto.derive_master_key(passphrase, salt)
        manifest_key = backup_crypto.derive_manifest_key(master_key)
        canonical_manifest = json.loads(json.dumps(manifest))
        canonical_manifest["encryption"]["manifest_tag_base64"] = ""
        canonical = json.dumps(
            canonical_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        backup_crypto.verify_manifest_tag(manifest_key, canonical, tag)

        def decrypt_member(name: str) -> bytes:
            metadata = manifest["members"][name]
            ciphertext = archive.read(name)
            assert hashlib.sha256(ciphertext).hexdigest() == metadata["sha256"]
            assert len(ciphertext) == metadata["size"]
            key = backup_crypto.derive_member_key(master_key, name)
            out = io.BytesIO()
            backup_crypto.decrypt_member(
                name,
                key,
                io.BytesIO(ciphertext),
                out,
                plaintext_size=metadata["plaintext_size"],
                chunk_count=metadata["chunks"],
            )
            return out.getvalue()

        index = json.loads(decrypt_member(manifest["index_member"]))
        payloads = {name: decrypt_member(name) for name in manifest["members"]}
        return manifest, index, payloads


# 1. create has a keyword-only passphrase parameter.
def test_create_signature_includes_keyword_only_passphrase():
    parameters = [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(create_backup).parameters.items()
    ]
    assert parameters == [
        ("store", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("destination", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("settings_file", inspect.Parameter.KEYWORD_ONLY, None),
        ("include_audio", inspect.Parameter.KEYWORD_ONLY, True),
        ("passphrase", inspect.Parameter.KEYWORD_ONLY, None),
    ]


# 2. passphrase=None keeps the v1 schema and behavior untouched.
def test_passphrase_none_keeps_v1_schema(tmp_path, make_wav):
    store, _, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    created = create_backup(store, backup)
    assert created["version"] == 1
    with zipfile.ZipFile(backup) as archive:
        assert set(archive.namelist()) == {"manifest.json", "transcripts.jsonl", *[
            name for name in archive.namelist() if name.startswith("sources/")
        ]}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 1
        assert manifest["records"] == 1
        assert archive.read("transcripts.jsonl") == _expected_jsonl(store)


# 3. Empty passphrase fails exactly and never touches the destination.
def test_empty_passphrase_fails_without_touching_destination(tmp_path, make_wav):
    store, _, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "existing.voice-backup"
    backup.write_bytes(b"previous-destination")
    with pytest.raises(ValueError) as excinfo:
        create_backup(store, backup, passphrase="")
    assert str(excinfo.value) == "passphrase cannot be empty"
    assert backup.read_bytes() == b"previous-destination"
    assert not (tmp_path / "existing.voice-backup.tmp").exists()


# 4/5/6. v2 manifest schema, member set and compression contract.
def test_v2_manifest_schema_and_member_set(tmp_path, make_wav):
    store, _, _ = _seed(tmp_path, make_wav)
    settings_file = _with_settings(tmp_path)
    backup = tmp_path / "enc.voice-backup"
    created = create_backup(
        store, backup, settings_file=settings_file, passphrase=PASSPHRASE
    )
    assert created["version"] == 2
    assert created["records"] == 1
    assert created["audio_files"] == 1

    with zipfile.ZipFile(backup) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        expected_members = {"manifest.json"} | {
            f"payload/{index:08d}.enc" for index in range(5)
        }
        assert set(names) == expected_members
        for info in archive.infolist():
            if info.filename.startswith("payload/"):
                assert info.compress_type == zipfile.ZIP_STORED
        manifest = json.loads(archive.read("manifest.json"))

    assert set(manifest) == {"version", "encryption", "index_member", "members"}
    assert manifest["version"] == 2
    assert manifest["index_member"] == "payload/00000000.enc"
    encryption = manifest["encryption"]
    assert encryption["algorithm"] == "AES-256-GCM-CHUNKED"
    assert encryption["kdf"] == "argon2id"
    assert encryption["kdf_params"] == {
        "iterations": 3,
        "memory_cost_kib": 65536,
        "lanes": 1,
    }
    assert len(base64.b64decode(encryption["salt_base64"])) == 16
    assert len(base64.b64decode(encryption["manifest_tag_base64"])) == 32
    assert set(manifest["members"]) == expected_members - {"manifest.json"}
    for metadata in manifest["members"].values():
        assert set(metadata) == {"sha256", "size", "plaintext_size", "chunks"}
        assert metadata["size"] == metadata["plaintext_size"] + 16 * metadata["chunks"]
        assert metadata["chunks"] >= 1


# 7. Neither ZIP names nor the manifest leak logical/source names or hashes.
def test_v2_archive_leaks_no_logical_names_or_hashes(tmp_path, make_wav):
    store, managed_files, digests = _seed(tmp_path, make_wav)
    settings_file = _with_settings(tmp_path)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)
    with zipfile.ZipFile(backup) as archive:
        names = archive.namelist()
        manifest_raw = archive.read("manifest.json")
    opaque = re.compile(r"^payload/\d{8}\.enc$")
    for name in names:
        assert name == "manifest.json" or opaque.match(name), name
    for forbidden in (
        "transcripts",
        "settings",
        "dictionary",
        "sources/",
        ".wav",
        "rec-0",
        "raw",
        "corrected",
    ):
        assert forbidden.encode() not in manifest_raw
        assert all(forbidden not in name for name in names)
    for digest in digests:
        assert digest.encode() not in manifest_raw
    for managed in managed_files:
        assert managed.name.encode() not in manifest_raw


# 8/9. Correct passphrase authenticates and restores the exact payload.
def test_correct_passphrase_decrypts_exact_payload(tmp_path, make_wav):
    store, managed_files, _ = _seed(tmp_path, make_wav, records=2)
    settings_file = _with_settings(tmp_path)
    dictionary_bytes = (tmp_path / "dictionary.json").read_bytes()
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)

    manifest, index, payloads = _read_v2(backup, PASSPHRASE)
    assert index["version"] == 2
    assert index["records"] == 2
    assert index["include_audio"] is True
    assert isinstance(index["created_at"], str)

    mapping = index["members"]
    non_index_members = set(manifest["members"]) - {manifest["index_member"]}
    assert set(mapping.values()) == non_index_members
    assert len(set(mapping.values())) == len(mapping)  # bijection
    assert manifest["index_member"] not in mapping.values()
    assert set(mapping) == {
        "transcripts.jsonl",
        "config/settings.json",
        "config/dictionary.json",
        f"sources/{managed_files[0].name}",
        f"sources/{managed_files[1].name}",
    }

    assert payloads[mapping["transcripts.jsonl"]] == _expected_jsonl(store)
    restored_settings = json.loads(payloads[mapping["config/settings.json"]])
    assert restored_settings["dictionary_path"] == str(tmp_path / "dictionary.json")
    assert payloads[mapping["config/dictionary.json"]] == dictionary_bytes
    for managed in managed_files:
        member = mapping[f"sources/{managed.name}"]
        assert payloads[member] == managed.read_bytes()


# 10. include_audio=False adds no audio mapping or member.
def test_include_audio_false_omits_audio_members(tmp_path, make_wav):
    store, managed_files, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    created = create_backup(store, backup, include_audio=False, passphrase=PASSPHRASE)
    assert created["audio_files"] == 0
    manifest, index, _payloads = _read_v2(backup, PASSPHRASE)
    assert index["include_audio"] is False
    assert all(not name.startswith("sources/") for name in index["members"])
    assert set(manifest["members"]) == {
        "payload/00000000.enc",
        "payload/00000001.enc",
    }
    assert index["members"] == {"transcripts.jsonl": "payload/00000001.enc"}


# 11. Two creates differ in salt/ciphertext but keep the same schema.
def test_two_creates_have_fresh_salts_and_ciphertext(tmp_path, make_wav):
    store, _, _ = _seed(tmp_path, make_wav)
    archives = []
    for name in ("one.voice-backup", "two.voice-backup"):
        backup = tmp_path / name
        create_backup(store, backup, passphrase=PASSPHRASE)
        archives.append(backup)
    manifests = []
    ciphertexts = []
    for backup in archives:
        with zipfile.ZipFile(backup) as archive:
            manifests.append(json.loads(archive.read("manifest.json")))
            ciphertexts.append(archive.read("payload/00000001.enc"))
    first, second = manifests
    assert first["encryption"]["salt_base64"] != second["encryption"]["salt_base64"]
    assert first["encryption"]["manifest_tag_base64"] != (
        second["encryption"]["manifest_tag_base64"]
    )
    assert ciphertexts[0] != ciphertexts[1]
    assert set(first) == set(second)
    assert first["encryption"]["kdf_params"] == second["encryption"]["kdf_params"]
    assert set(first["members"]) == set(second["members"])


# 12. Original managed audio bytes and mtime are never modified.
def test_original_audio_bytes_and_mtime_are_untouched(tmp_path, make_wav):
    store, managed_files, _ = _seed(tmp_path, make_wav)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in managed_files
    }
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, passphrase=PASSPHRASE)
    for path, (content, mtime_ns) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns


# 13. A limit violation keeps the previous destination and removes .tmp.
def test_limit_violation_preserves_destination_and_cleans_tmp(
    tmp_path, make_wav, monkeypatch
):
    store, _, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    backup.write_bytes(b"previous-destination")
    monkeypatch.setitem(backup_module._FIXED_MEMBER_LIMITS, "transcripts.jsonl", 8)
    with pytest.raises(ValueError, match="format limit"):
        create_backup(store, backup, passphrase=PASSPHRASE)
    assert backup.read_bytes() == b"previous-destination"
    assert not (tmp_path / "enc.voice-backup.tmp").exists()


def _budget_with(**overrides):
    current = backup_module.BACKUP_ZIP_BUDGET
    values = {
        "max_container_bytes": current.max_container_bytes,
        "max_members": current.max_members,
        "max_member_bytes": current.max_member_bytes,
        "max_total_bytes": current.max_total_bytes,
        "max_member_compression_ratio": current.max_member_compression_ratio,
        "max_total_compression_ratio": current.max_total_compression_ratio,
        "allow_directories": current.allow_directories,
        "allowed_compression_methods": current.allowed_compression_methods,
        "max_central_directory_bytes": current.max_central_directory_bytes,
    }
    values.update(overrides)
    return backup_module.ZipBudget(**values)


def test_v2_create_applies_outer_zip_budget_before_publish(
    tmp_path, make_wav, monkeypatch
):
    store, _, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    backup.write_bytes(b"previous-destination")
    monkeypatch.setattr(
        backup_module,
        "BACKUP_ZIP_BUDGET",
        _budget_with(max_member_bytes=1024),
    )

    with pytest.raises(ValueError, match="max_member_bytes"):
        create_backup(store, backup, passphrase=PASSPHRASE)

    assert backup.read_bytes() == b"previous-destination"
    assert not (tmp_path / "enc.voice-backup.tmp").exists()


def test_v2_settings_and_dictionary_are_read_with_explicit_bounds(
    tmp_path, make_wav, monkeypatch
):
    store, _, _ = _seed(tmp_path, make_wav)
    settings_file = _with_settings(tmp_path)
    dictionary = tmp_path / "dictionary.json"
    blocked = {settings_file.resolve(), dictionary.resolve()}
    real_read_bytes = Path.read_bytes

    def reject_unbounded_read(path):
        if path.resolve() in blocked:
            raise AssertionError(f"unbounded read_bytes() for {path.name}")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)

    manifest, index, payloads = _read_v2(backup, PASSPHRASE)
    mapping = index["members"]
    assert json.loads(payloads[mapping["config/settings.json"]])[
        "dictionary_path"
    ] == str(dictionary)
    assert payloads[mapping["config/dictionary.json"]] == (
        b'{"replacements":[["\xd1\x82\xd0\xb5\xd1\x85","\xd1\x82\xd0\xb5\xd1\x85\xd0\xbd\xd1\x96\xd1\x87\xd0\xbd\xd0\xb8\xd0\xb9"]]}'
    )
    assert manifest["version"] == 2


def test_oversized_dictionary_is_not_silently_omitted(
    tmp_path, make_wav, monkeypatch
):
    store, _, _ = _seed(tmp_path, make_wav)
    settings_file = _with_settings(tmp_path)
    dictionary = tmp_path / "dictionary.json"
    monkeypatch.setitem(
        backup_module._FIXED_MEMBER_LIMITS,
        "config/dictionary.json",
        8,
    )
    dictionary.write_bytes(b"123456789")
    backup = tmp_path / "enc.voice-backup"
    backup.write_bytes(b"previous-destination")

    with pytest.raises(ValueError, match="config/dictionary.json"):
        create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)

    assert backup.read_bytes() == b"previous-destination"
    assert not (tmp_path / "enc.voice-backup.tmp").exists()


def test_v2_manifest_plaintext_limit_is_enforced_before_publish(
    tmp_path, make_wav, monkeypatch
):
    store, _, _ = _seed(tmp_path, make_wav)
    monkeypatch.setitem(backup_module._FIXED_MEMBER_LIMITS, "manifest.json", 64)
    backup = tmp_path / "enc.voice-backup"
    backup.write_bytes(b"previous-destination")

    with pytest.raises(ValueError, match="manifest.json"):
        create_backup(store, backup, passphrase=PASSPHRASE)

    assert backup.read_bytes() == b"previous-destination"
    assert not (tmp_path / "enc.voice-backup.tmp").exists()


# 14. Transcript payload streams with bounded reads, never materialized.
class _GuardReader:
    def __init__(self, stream, reads: list[int]):
        self._stream = stream
        self._reads = reads

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= CHUNK, f"unbounded or oversized read: {size}"
        self._reads.append(size)
        return self._stream.read(size)


def test_transcript_payload_streams_with_bounded_reads(tmp_path, make_wav, monkeypatch):
    store = LocalStore(tmp_path / "store")
    for index in range(40):
        store.save(
            _transcript(f"rec-{index}", "0" * 64, "", raw="x" * 30_000)
        )
    reads: list[int] = []
    real_encrypt = backup_crypto.encrypt_member

    def spy(name, key, source, dest):
        return real_encrypt(name, key, _GuardReader(source, reads), dest)

    monkeypatch.setattr(backup_crypto, "encrypt_member", spy)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, passphrase=PASSPHRASE)
    assert len(_expected_jsonl(store)) > CHUNK
    assert reads, "transcript member was never encrypted"
    # Bounded streaming: every read is chunk-capped (the 1-byte lookahead the
    # Slice A contract uses to flag the final chunk is included), and a
    # multi-chunk payload is served through multiple reads, never one
    # materialized buffer.
    assert all(0 < size <= CHUNK for size in reads)
    expected_chunks = -(-len(_expected_jsonl(store)) // CHUNK)
    assert len(reads) >= expected_chunks
    _read_v2(backup, PASSPHRASE)


# 15. The manifest carries no passphrase, derived keys or private plaintext.
def test_manifest_contains_no_secret_material(tmp_path, make_wav):
    store, _, _ = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, passphrase=PASSPHRASE)
    with zipfile.ZipFile(backup) as archive:
        manifest_raw = archive.read("manifest.json")
        manifest = json.loads(manifest_raw)
    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    derived_keys = [
        master_key,
        backup_crypto.derive_manifest_key(master_key),
        backup_crypto.derive_member_key(master_key, "payload/00000000.enc"),
    ]
    assert PASSPHRASE.encode() not in manifest_raw
    for key in derived_keys:
        assert key not in manifest_raw
        assert base64.b64encode(key) not in manifest_raw
        assert key.hex().encode() not in manifest_raw
    for plaintext_marker in (b"corrected rec-0", b"dictionary.restored"):
        assert plaintext_marker not in manifest_raw
