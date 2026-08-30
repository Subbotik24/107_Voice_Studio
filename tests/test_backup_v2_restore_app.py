"""Tests for encrypted backup v2 data restore (W2-E1 Slice C1).

Covers data restore with the early ``staging_building`` journal. The
encrypted settings sidecar and passphrase settings recovery belong to
Slice C2 and are guarded in C1 with an exact temporary error.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import io
import json
import zipfile
from pathlib import Path

import pytest

from voice_studio import backup as backup_module
from voice_studio import backup_crypto
from voice_studio.backup import (
    create_backup,
    recover_interrupted_restore,
    restore_backup,
)
from voice_studio.config import save_settings
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic slice-c1 passphrase"
WRONG = "synthetic wrong passphrase"
REQUIRED_ERROR = "backup is encrypted; a passphrase is required"
MANIFEST_ERROR = "backup authentication failed: wrong passphrase or corrupted manifest"


def _transcript(item_id: str, source_hash: str, source_path: str) -> Transcript:
    return Transcript(
        id=item_id,
        created_at="2026-07-27T00:00:00+00:00",
        source_name="safe.wav",
        source_sha256=source_hash,
        source_path=source_path,
        language="uk",
        engine="fixture",
        model="fixture",
        raw_text=f"raw {item_id}",
        corrected_text=f"corrected {item_id}",
    )


def _seed(tmp_path: Path, make_wav, *, records: int = 2):
    store = LocalStore(tmp_path / "store")
    managed_files = []
    originals = []
    for index in range(records):
        original = make_wav(tmp_path / f"original-{index}.wav")
        managed, digest = store.import_source(original)
        managed_files.append(managed)
        originals.append(original)
        store.save(_transcript(f"rec-{index}", digest, str(managed)))
    return store, managed_files, originals


def _make_v2(tmp_path: Path, make_wav, *, settings: bool = False):
    store, managed_files, originals = _seed(tmp_path, make_wav)
    settings_file = None
    if settings:
        dictionary = tmp_path / "dictionary.json"
        dictionary.write_text('{"replacements":[]}', encoding="utf-8")
        settings_file = tmp_path / "config" / "settings.json"
        save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)
    return store, backup, managed_files, originals


def _journal_path(data_root: Path) -> Path:
    return backup_module.restore_journal_path(data_root)


def _staging_dirs(data_root: Path) -> list[Path]:
    prefix = f".{data_root.name}.restore-"
    if not data_root.parent.exists():
        return []
    result = []
    for entry in data_root.parent.iterdir():
        if not entry.name.startswith(prefix):
            continue
        suffix = entry.name[len(prefix) :]
        if len(suffix) == 32 and all(c in "0123456789abcdef" for c in suffix):
            result.append(entry)
    return result


# --- tamper helpers (Slice B2 patterns; production validation unchanged) ---


def _load_archive(path: Path):
    with zipfile.ZipFile(path) as archive:
        compression = {info.filename: info.compress_type for info in archive.infolist()}
        blobs = {name: archive.read(name) for name in compression}
    return compression, blobs


def _store_archive(path: Path, compression: dict[str, int], blobs: dict[str, bytes]):
    for name in blobs:
        compression.setdefault(name, zipfile.ZIP_STORED)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in blobs.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression[name]
            archive.writestr(info, data)


def _keys(manifest: dict, passphrase: str):
    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(passphrase, salt)
    return master_key, backup_crypto.derive_manifest_key(master_key)


def _retag(manifest: dict, manifest_key: bytes) -> dict:
    manifest = copy.deepcopy(manifest)
    manifest["encryption"]["manifest_tag_base64"] = ""
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["encryption"]["manifest_tag_base64"] = base64.b64encode(
        backup_crypto.compute_manifest_tag(manifest_key, canonical)
    ).decode("ascii")
    return manifest


def _decrypt_index(manifest: dict, blobs: dict, master_key: bytes) -> dict:
    name = manifest["index_member"]
    metadata = manifest["members"][name]
    member_key = backup_crypto.derive_member_key(master_key, name)
    out = io.BytesIO()
    backup_crypto.decrypt_member(
        name,
        member_key,
        io.BytesIO(blobs[name]),
        out,
        plaintext_size=metadata["plaintext_size"],
        chunk_count=metadata["chunks"],
    )
    return json.loads(out.getvalue())


def _rewrite_index(manifest: dict, blobs: dict, master_key: bytes, index) -> None:
    name = manifest["index_member"]
    plaintext = json.dumps(index, ensure_ascii=False, sort_keys=True).encode("utf-8")
    member_key = backup_crypto.derive_member_key(master_key, name)
    out = io.BytesIO()
    plaintext_size, chunks = backup_crypto.encrypt_member(
        name, member_key, io.BytesIO(plaintext), out
    )
    ciphertext = out.getvalue()
    blobs[name] = ciphertext
    manifest["members"][name] = {
        "sha256": hashlib.sha256(ciphertext).hexdigest(),
        "size": len(ciphertext),
        "plaintext_size": plaintext_size,
        "chunks": chunks,
    }


def _rewrite_member(
    manifest: dict,
    blobs: dict[str, bytes],
    master_key: bytes,
    name: str,
    plaintext: bytes,
) -> None:
    member_key = backup_crypto.derive_member_key(master_key, name)
    out = io.BytesIO()
    plaintext_size, chunks = backup_crypto.encrypt_member(
        name, member_key, io.BytesIO(plaintext), out
    )
    ciphertext = out.getvalue()
    blobs[name] = ciphertext
    manifest["members"][name] = {
        "sha256": hashlib.sha256(ciphertext).hexdigest(),
        "size": len(ciphertext),
        "plaintext_size": plaintext_size,
        "chunks": chunks,
    }


def _tampered_index_backup(tmp_path, make_wav, index_mutate) -> Path:
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    index = _decrypt_index(manifest, blobs, master_key)
    index_mutate(index)
    _rewrite_index(manifest, blobs, master_key, index)
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    target = tmp_path / "tampered.voice-backup"
    _store_archive(target, compression, blobs)
    return target


# 1. restore has a keyword-only passphrase parameter.
def test_restore_signature_includes_keyword_only_passphrase():
    parameters = [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(restore_backup).parameters.items()
    ]
    assert parameters == [
        ("path", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("data_root", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("settings_target", inspect.Parameter.KEYWORD_ONLY, None),
        ("passphrase", inspect.Parameter.KEYWORD_ONLY, None),
    ]


# 2/3. v1 restore is unchanged, with or without a passphrase.
@pytest.mark.parametrize("passphrase", [None, PASSPHRASE], ids=["none", "ignored"])
def test_v1_restore_unchanged(tmp_path, make_wav, passphrase):
    store, _managed, _originals = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    data = tmp_path / "data"
    result = restore_backup(backup, data, passphrase=passphrase)
    assert result["status"] == "PASS"
    assert result["records"] == 2
    assert len(LocalStore(data).list(limit=10)) == 2


# 4/5/6. v2 transcripts/audio round-trip with exact text and bytes.
def test_v2_restore_roundtrip(tmp_path, make_wav):
    store, backup, managed_files, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    result = restore_backup(backup, data, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert result["records"] == 2
    assert result["journal_cleared"] is True

    restored_store = LocalStore(data)
    restored = {item.id: item for item in restored_store.list(limit=10)}
    original = {item.id: item for item in store.list(limit=10)}
    assert set(restored) == set(original)
    for item_id, item in original.items():
        assert restored[item_id].raw_text == item.raw_text
        assert restored[item_id].corrected_text == item.corrected_text
        assert restored[item_id].source_sha256 == item.source_sha256
        assert restored[item_id].audio_retained is True
    for managed in managed_files:
        restored_audio = data / "sources" / managed.name
        assert restored_audio.read_bytes() == managed.read_bytes()
    assert restored_store.audit()["status"] == "PASS"
    assert not _journal_path(data).exists()
    assert _staging_dirs(data) == []


# 7. The original external audio file is never touched.
def test_original_external_audio_untouched(tmp_path, make_wav):
    _store, backup, _managed, originals = _make_v2(tmp_path, make_wav)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in originals}
    restore_backup(backup, tmp_path / "data", passphrase=PASSPHRASE)
    for path, (content, mtime_ns) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns


# 8. Existing models/ and exports/ survive the v2 restore.
def test_local_models_and_exports_survive(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    (data / "models" / "tiny").mkdir(parents=True)
    (data / "models" / "tiny" / "model.bin").write_bytes(b"model-bytes")
    (data / "exports").mkdir(parents=True)
    (data / "exports" / "result.txt").write_bytes(b"export-bytes")
    result = restore_backup(backup, data, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert (data / "models" / "tiny" / "model.bin").read_bytes() == b"model-bytes"
    assert (data / "exports" / "result.txt").read_bytes() == b"export-bytes"


# 9. A wrong passphrase creates no journal, staging or live change.
def test_wrong_passphrase_leaves_no_trace(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    data.mkdir()
    sentinel = data / "sentinel.txt"
    sentinel.write_bytes(b"live")
    with pytest.raises(ValueError) as excinfo:
        restore_backup(backup, data, passphrase=WRONG)
    assert str(excinfo.value) == MANIFEST_ERROR
    assert sentinel.read_bytes() == b"live"
    assert not _journal_path(data).exists()
    assert _staging_dirs(data) == []


def test_wrong_passphrase_does_not_create_destination_parent(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "missing-parent" / "data"
    with pytest.raises(ValueError) as excinfo:
        restore_backup(backup, data, passphrase=WRONG)
    assert str(excinfo.value) == MANIFEST_ERROR
    assert not data.parent.exists()


def test_missing_passphrase_requires_one(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    with pytest.raises(ValueError) as excinfo:
        restore_backup(backup, data)
    assert str(excinfo.value) == REQUIRED_ERROR
    assert not _journal_path(data).exists()


# 10. A tampered ciphertext never changes the live root.
def test_tampered_ciphertext_preserves_live_root(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    data = bytearray(blobs["payload/00000002.enc"])
    data[0] ^= 0x01
    blobs["payload/00000002.enc"] = bytes(data)
    tampered = tmp_path / "tampered.voice-backup"
    _store_archive(tampered, compression, blobs)
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "keep.txt").write_bytes(b"live")
    with pytest.raises(ValueError, match="authentication failed|does not match"):
        restore_backup(tampered, data_root, passphrase=PASSPHRASE)
    assert (data_root / "keep.txt").read_bytes() == b"live"
    assert _staging_dirs(data_root) == []
    assert not _journal_path(data_root).exists()


# 11. Restore never calls the standalone v2 verify (no double decrypt).
def test_restore_does_not_call_standalone_verify(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)

    def _forbidden(*args, **kwargs):
        raise AssertionError("standalone verify_backup must not run during restore")

    monkeypatch.setattr(backup_module, "verify_backup", _forbidden)
    result = restore_backup(backup, tmp_path / "data", passphrase=PASSPHRASE)
    assert result["status"] == "PASS"


# 12. Every payload is decrypted exactly once.
def test_each_payload_decrypted_exactly_once(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    calls: list[str] = []
    real_decrypt = backup_crypto.decrypt_member

    def spy(name, key, source, dest, **kwargs):
        calls.append(name)
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    result = restore_backup(backup, tmp_path / "data", passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert calls.count("payload/00000000.enc") == 1
    payloads = [name for name in calls if name != "payload/00000000.enc"]
    assert len(payloads) == len(set(payloads))
    assert len(payloads) == 3  # transcripts + 2 audio


def test_unreferenced_audio_payload_is_still_authenticated(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    index = _decrypt_index(manifest, blobs, master_key)

    transcripts_name = index["members"]["transcripts.jsonl"]
    metadata = manifest["members"][transcripts_name]
    transcript_key = backup_crypto.derive_member_key(master_key, transcripts_name)
    plaintext = io.BytesIO()
    backup_crypto.decrypt_member(
        transcripts_name,
        transcript_key,
        io.BytesIO(blobs[transcripts_name]),
        plaintext,
        plaintext_size=metadata["plaintext_size"],
        chunk_count=metadata["chunks"],
    )
    rows = []
    for line in plaintext.getvalue().decode("utf-8").splitlines():
        row = json.loads(line)
        row["source_path"] = None
        row["audio_retained"] = False
        rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    _rewrite_member(
        manifest,
        blobs,
        master_key,
        transcripts_name,
        ("\n".join(rows) + "\n").encode("utf-8"),
    )

    source_name = next(
        opaque
        for logical, opaque in index["members"].items()
        if logical.startswith("sources/")
    )
    tampered = bytearray(blobs[source_name])
    tampered[0] ^= 0x01
    blobs[source_name] = bytes(tampered)
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    target = tmp_path / "unreferenced-audio-tampered.voice-backup"
    _store_archive(target, compression, blobs)

    data = tmp_path / "data"
    with pytest.raises(ValueError, match="authentication failed|does not match"):
        restore_backup(target, data, passphrase=PASSPHRASE)
    assert not data.exists()
    assert not _journal_path(data).exists()
    assert _staging_dirs(data) == []


# 13. The staging_building journal exists before the first plaintext write.
def test_journal_exists_before_first_plaintext_write(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    observed = []
    real_decrypt = backup_crypto.decrypt_member

    def spy(name, key, source, dest, **kwargs):
        if name != "payload/00000000.enc":
            journal_file = _journal_path(data)
            observed.append(
                journal_file.is_file()
                and json.loads(journal_file.read_text(encoding="utf-8"))["stage"]
                == "staging_building"
            )
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    result = restore_backup(backup, data, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert observed and all(observed)


def _crash_during_first_payload(tmp_path, make_wav, monkeypatch):
    """Interrupt the restore mid-decrypt; return (data_root, journal dict)."""
    _store, backup, _managed, originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    real_decrypt = backup_crypto.decrypt_member
    triggered = []

    def spy(name, key, source, dest, **kwargs):
        if name != "payload/00000000.enc" and not triggered:
            triggered.append(name)
            dest.write(b"partial plaintext chunk")
            raise KeyboardInterrupt
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(backup, data, passphrase=PASSPHRASE)
    monkeypatch.undo()
    return data, backup, originals


# 14/16. Hard death leaves staging and a secret-free staging_building journal.
def test_hard_death_leaves_contained_staging_and_clean_journal(tmp_path, make_wav, monkeypatch):
    data, _backup, _originals = _crash_during_first_payload(tmp_path, make_wav, monkeypatch)
    journal_file = _journal_path(data)
    assert journal_file.is_file()
    journal_text = journal_file.read_text(encoding="utf-8")
    journal = json.loads(journal_text)
    assert journal["stage"] == "staging_building"
    assert journal["backup_version"] == 2
    assert journal["recovery_path"] is None
    assert set(journal) == set(backup_module.RESTORE_JOURNAL_FIELDS)
    staging = _staging_dirs(data)
    assert len(staging) == 1
    assert staging[0].name == Path(journal["staging_path"]).name
    for leaked in (
        PASSPHRASE,
        "rec-0",
        "raw rec-0",
        "corrected rec-0",
        "transcripts.jsonl",
        "config/settings.json",
        "sources/",
        "payload/0000",
    ):
        assert leaked not in journal_text


# 17. Recovery of a valid staging_building removes only staging + journal.
def test_recovery_discards_only_incomplete_staging(tmp_path, make_wav, monkeypatch):
    data, _backup, _originals = _crash_during_first_payload(tmp_path, make_wav, monkeypatch)
    data.mkdir(exist_ok=True)
    live = data / "live.txt"
    live.write_bytes(b"live")
    staging = _staging_dirs(data)[0]
    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert result["action"] == "staging_discarded"
    assert not staging.exists()
    assert not _journal_path(data).exists()
    assert live.read_bytes() == b"live"


def test_recovery_discards_staging_when_destination_never_existed(
    tmp_path, make_wav, monkeypatch
):
    data, _backup, _originals = _crash_during_first_payload(
        tmp_path, make_wav, monkeypatch
    )
    staging = _staging_dirs(data)[0]
    assert not data.exists()
    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert result["action"] == "staging_discarded"
    assert not staging.exists()
    assert not _journal_path(data).exists()


# 18. Recovery with an already-missing staging just clears the journal.
def test_recovery_with_missing_staging_clears_journal(tmp_path, make_wav, monkeypatch):
    data, _backup, _originals = _crash_during_first_payload(tmp_path, make_wav, monkeypatch)
    import shutil

    shutil.rmtree(_staging_dirs(data)[0])
    data.mkdir(exist_ok=True)
    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert result["action"] == "staging_discarded"
    assert not _journal_path(data).exists()


def _crafted_journal(data: Path, **overrides):
    staging = data.parent / f".{data.name}.restore-{'a' * 32}"
    journal = {
        "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
        "backup_version": 2,
        "created_at": "2026-08-30T00:00:00+00:00",
        "data_root": str(data.resolve()),
        "staging_path": str(staging),
        "recovery_path": None,
        "expected_records": 1,
        "settings_target": None,
        "settings_payload_written": True,
        "stage": "staging_building",
    }
    journal.update(overrides)
    backup_module._write_json_atomic(_journal_path(data), journal)
    return journal


# 19. A journal pointing outside the data directory deletes nothing.
def test_recovery_rejects_outside_staging_path(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_bytes(b"keep")
    _crafted_journal(data, staging_path=str(outside))
    result = recover_interrupted_restore(data)
    assert result["status"] == "FAIL"
    assert marker.read_bytes() == b"keep"
    assert _journal_path(data).exists()


# 20. A sibling with a wrong staging name is never touched.
def test_recovery_rejects_wrong_staging_name(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sibling = tmp_path / ".data.restore-not-a-uuid"
    sibling.mkdir()
    (sibling / "file.txt").write_bytes(b"keep")
    _crafted_journal(data, staging_path=str(sibling))
    result = recover_interrupted_restore(data)
    assert result["status"] == "FAIL"
    assert (sibling / "file.txt").read_bytes() == b"keep"
    assert _journal_path(data).exists()


# 21. staging_building is invalid for backup version 1.
def test_staging_building_rejected_for_v1(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _crafted_journal(data, backup_version=1)
    result = recover_interrupted_restore(data)
    assert result["status"] == "FAIL"
    assert _journal_path(data).exists()


# 22. staging_building must not carry a recovery path.
def test_staging_building_rejected_with_recovery_path(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    recovery = tmp_path / "data.recovery-20260830T000000Z-deadbeef"
    _crafted_journal(data, recovery_path=str(recovery))
    result = recover_interrupted_restore(data)
    assert result["status"] == "FAIL"
    assert _journal_path(data).exists()


# 23. A record-count mismatch never swaps the live root.
def test_record_count_mismatch_never_swaps(tmp_path, make_wav):
    backup = _tampered_index_backup(
        tmp_path, make_wav, lambda index: index.update(records=5)
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "keep.txt").write_bytes(b"live")
    with pytest.raises(ValueError, match="record count"):
        restore_backup(backup, data, passphrase=PASSPHRASE)
    assert (data / "keep.txt").read_bytes() == b"live"
    assert _staging_dirs(data) == []
    assert not _journal_path(data).exists()


# 24. A storage audit failure never swaps the live root.
def test_audit_failure_never_swaps(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    data.mkdir()
    (data / "keep.txt").write_bytes(b"live")
    real_audit = LocalStore.audit

    def failing_audit(self):
        if Path(self.root) != data.parent / "store":
            return {"status": "FAIL", "missing": ["x"], "hash_mismatches": [], "unsafe_paths": []}
        return real_audit(self)

    monkeypatch.setattr(LocalStore, "audit", failing_audit)
    with pytest.raises(ValueError, match="audit"):
        restore_backup(backup, data, passphrase=PASSPHRASE)
    assert (data / "keep.txt").read_bytes() == b"live"
    assert _staging_dirs(data) == []
    assert not _journal_path(data).exists()


# 25/26. A payload failure after successful members performs no live rename,
# and no plaintext transcript temp file survives.
def test_mid_payload_failure_leaves_live_root_untouched(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    data.mkdir()
    (data / "keep.txt").write_bytes(b"live")
    real_decrypt = backup_crypto.decrypt_member
    seen = []

    def spy(name, key, source, dest, **kwargs):
        if name != "payload/00000000.enc":
            seen.append(name)
            if len(seen) == 2:
                raise ValueError("simulated payload failure")
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    with pytest.raises(ValueError, match="simulated payload failure"):
        restore_backup(backup, data, passphrase=PASSPHRASE)
    assert (data / "keep.txt").read_bytes() == b"live"
    assert _staging_dirs(data) == []
    assert not _journal_path(data).exists()


def test_no_plaintext_temp_file_remains(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    result = restore_backup(backup, data, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    leftovers = [
        path
        for path in data.rglob("*")
        if path.is_file() and ".tmp" in path.name
    ]
    assert leftovers == []
    recovered = tmp_path / result["recovery"] if result["recovery"] else None
    assert recovered is None or not any(
        ".tmp" in path.name for path in recovered.rglob("*") if path.is_file()
    )


# 27. C2a: settings recovery works; the former C1 guard no longer fires.
def test_settings_recovery_replaces_c1_guard(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav, settings=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "keep.txt").write_bytes(b"live")
    settings_target = tmp_path / "config" / "settings.json"
    settings_target.parent.mkdir(parents=True, exist_ok=True)
    settings_target.write_text("{}", encoding="utf-8")
    result = restore_backup(
        backup, data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    # The previous live root is preserved untouched in the recovery directory.
    recovery = Path(result["recovery"])
    assert (recovery / "keep.txt").read_bytes() == b"live"
    assert not _journal_path(data).exists()
    assert _staging_dirs(data) == []
    assert not (data / ".restore-settings-v2").exists()


# 28. Config payloads authenticate into the discard sink without a target.
def test_config_authenticated_in_discard_sink(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav, settings=True)
    decrypted: list[str] = []
    real_decrypt = backup_crypto.decrypt_member

    def spy(name, key, source, dest, **kwargs):
        decrypted.append(name)
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    result = restore_backup(backup, tmp_path / "data", passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert decrypted.count("payload/00000002.enc") == 1  # config/settings.json
    assert decrypted.count("payload/00000003.enc") == 1  # config/dictionary.json


# 29. The restore result carries no secrets, index or mapping.
def test_restore_result_contains_no_secret_material(tmp_path, make_wav):
    _store, backup, _managed, _originals = _make_v2(tmp_path, make_wav)
    result = restore_backup(backup, tmp_path / "data", passphrase=PASSPHRASE)
    assert set(result) == {"status", "records", "data", "recovery", "journal_cleared"}
    serialized = json.dumps(result)
    assert PASSPHRASE not in serialized
    assert "transcripts" not in serialized
    assert "payload/" not in serialized


# 30. Recovery never deletes the user original or a recovery directory.
def test_recovery_preserves_originals_and_recovery_dirs(tmp_path, make_wav, monkeypatch):
    data, _backup, originals = _crash_during_first_payload(tmp_path, make_wav, monkeypatch)
    data.mkdir(exist_ok=True)
    recovery_dir = tmp_path / "data.recovery-20260830T000000Z-deadbeef"
    recovery_dir.mkdir()
    (recovery_dir / "keep.txt").write_bytes(b"recovery")
    before = {path: path.read_bytes() for path in originals}
    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert (recovery_dir / "keep.txt").read_bytes() == b"recovery"
    for path, content in before.items():
        assert path.read_bytes() == content
