"""Tests for encrypted backup v2 settings sidecar + recovery (W2-E1 Slice C2a).

Covers the encrypted `.restore-settings-v2` sidecar and the
passphrase-aware `recover_interrupted_restore` contract. CLI/GUI
integration is out of scope (Slice C2b/D).
"""
from __future__ import annotations

import base64
import inspect
import json
import shutil
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

PASSPHRASE = "synthetic slice-c2a passphrase"
WRONG = "synthetic wrong passphrase"
MANIFEST_ERROR = "backup authentication failed: wrong passphrase or corrupted manifest"
SIDECAR = ".restore-settings-v2"

DICTIONARY_BYTES = b'{"replacements":[["abc","def"]]}'


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


def _make_v2(tmp_path: Path, make_wav, *, settings: bool = True):
    store = LocalStore(tmp_path / "store")
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(_transcript("rec-0", digest, str(managed)))
    settings_file = None
    if settings:
        dictionary = tmp_path / "dictionary.json"
        dictionary.write_bytes(DICTIONARY_BYTES)
        settings_file = tmp_path / "config" / "settings.json"
        save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)
    return store, backup, managed, original


def _journal_path(data_root: Path) -> Path:
    return backup_module.restore_journal_path(data_root)


def _crash_after_swap(tmp_path, make_wav, monkeypatch):
    """Interrupt the restore at settings application; return the pieces."""
    _store, backup, managed, original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    settings_target.write_text(
        json.dumps(Settings().to_dict()), encoding="utf-8"
    )

    def _die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(backup_module, "_apply_restored_settings", _die)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(
            backup, data, settings_target=settings_target, passphrase=PASSPHRASE
        )
    monkeypatch.undo()
    return data, settings_target, backup, managed, original


def _sidecar_files(data_root: Path) -> set[str]:
    sidecar = data_root / SIDECAR
    if not sidecar.is_dir():
        return set()
    return {
        str(path.relative_to(sidecar)).replace("\\", "/")
        for path in sidecar.rglob("*")
        if path.is_file()
    }


# 1. recover_interrupted_restore has a keyword-only passphrase.
def test_recover_signature_includes_keyword_only_passphrase():
    parameters = [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(
            recover_interrupted_restore
        ).parameters.items()
    ]
    assert parameters == [
        ("data_root", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("settings_target", inspect.Parameter.KEYWORD_ONLY, None),
        ("passphrase", inspect.Parameter.KEYWORD_ONLY, None),
    ]


# 2. v1 plaintext sidecar recovery is unchanged, passphrase ignored.
def test_v1_recovery_unchanged_with_or_without_passphrase(tmp_path, make_wav, monkeypatch):
    store = LocalStore(tmp_path / "store")
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(_transcript("rec-0", digest, str(managed)))
    settings_file = tmp_path / "config" / "settings.json"
    save_settings(Settings(), settings_file)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup, settings_file=settings_file)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"

    def _die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(backup_module, "_apply_restored_settings", _die)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(backup, data, settings_target=settings_target)
    monkeypatch.undo()
    assert (data / ".restore-settings.json").is_file()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    assert not (data / ".restore-settings.json").exists()


# 3/4. Uninterrupted v2 settings + dictionary round-trip with backup preserved.
def test_uninterrupted_v2_settings_roundtrip(tmp_path, make_wav):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    previous = json.dumps(Settings().to_dict())
    settings_target.write_text(previous, encoding="utf-8")

    result = restore_backup(
        backup, data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert result["journal_cleared"] is True

    restored = json.loads(settings_target.read_text(encoding="utf-8"))
    assert restored["dictionary_path"] == str(
        settings_target.parent / "dictionary.restored.json"
    )
    dictionary_restored = settings_target.parent / "dictionary.restored.json"
    assert dictionary_restored.read_bytes() == DICTIONARY_BYTES
    backups = list(settings_target.parent.glob("settings.json.pre-restore-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == previous
    # No sidecar or journal survives a successful restore.
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()


# 5. The sidecar is fully created before the swap (it arrives with staging).
def test_sidecar_exists_before_settings_application(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    observed = []
    real_apply = backup_module._apply_restored_settings

    def spy(*args, **kwargs):
        observed.append((data / SIDECAR).is_dir())
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(backup_module, "_apply_restored_settings", spy)
    result = restore_backup(
        backup, data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert observed == [True]


# 6. Hard death after swap_completed leaves the encrypted sidecar + journal.
def test_hard_death_leaves_encrypted_sidecar_and_journal(tmp_path, make_wav, monkeypatch):
    data, settings_target, backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    sidecar = data / SIDECAR
    assert sidecar.is_dir()
    journal = json.loads(_journal_path(data).read_text(encoding="utf-8"))
    assert journal["stage"] == "swap_completed"
    assert journal["backup_version"] == 2
    assert journal["settings_target"] == str(settings_target)
    assert journal["settings_payload_written"] is False
    # Exact sidecar content: manifest + encrypted index + encrypted config only.
    assert _sidecar_files(data) == {
        "manifest.json",
        "payload/00000000.enc",
        "payload/00000002.enc",
        "payload/00000003.enc",
    }
    # No plaintext settings/dictionary markers, passphrase or key bytes.
    with zipfile.ZipFile(backup) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    import base64

    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    for path in sidecar.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_bytes()
        assert PASSPHRASE.encode() not in content
        assert b"abc" not in content or path.name == "manifest.json"
        assert b"dictionary.restored" not in content
        assert b"rec-0" not in content
        assert master_key not in content
        assert backup_crypto.derive_manifest_key(master_key) not in content
    manifest_in_sidecar = json.loads((sidecar / "manifest.json").read_bytes())
    assert manifest_in_sidecar == manifest


# 7. Recovery without a passphrase reports passphrase_required, zero mutations.
def test_recovery_without_passphrase_is_passphrase_required(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    before = settings_target.read_bytes()
    sidecar_before = _sidecar_files(data)
    result = recover_interrupted_restore(data, settings_target=settings_target)
    assert result["status"] == "PASS"
    assert result["action"] == "passphrase_required"
    assert PASSPHRASE not in json.dumps(result)
    assert settings_target.read_bytes() == before
    assert _sidecar_files(data) == sidecar_before
    assert _journal_path(data).exists()


# 8. A wrong or empty passphrase preserves sidecar, journal and settings.
@pytest.mark.parametrize("passphrase", [WRONG, ""], ids=["wrong", "empty"])
def test_recovery_with_bad_passphrase_preserves_everything(
    tmp_path, make_wav, monkeypatch, passphrase
):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    before = settings_target.read_bytes()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=passphrase
    )
    assert result["status"] == "FAIL"
    assert "authentication failed" in result["error"] or "passphrase" in result["error"]
    assert settings_target.read_bytes() == before
    assert not (settings_target.parent / "dictionary.restored.json").exists()
    assert _sidecar_files(data)
    assert _journal_path(data).exists()
    # A retry with the correct passphrase still succeeds afterwards.
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"


# 9. Correct passphrase completes settings and removes sidecar + journal.
def test_recovery_with_correct_passphrase_completes(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    restored = json.loads(settings_target.read_text(encoding="utf-8"))
    assert restored["dictionary_path"] == str(
        settings_target.parent / "dictionary.restored.json"
    )
    assert (settings_target.parent / "dictionary.restored.json").read_bytes() == (
        DICTIONARY_BYTES
    )
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()


def _mutate_sidecar(data: Path, mutate) -> None:
    sidecar = data / SIDECAR
    manifest = json.loads((sidecar / "manifest.json").read_text(encoding="utf-8"))
    mutate(sidecar, manifest)
    (sidecar / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# 10. A tampered sidecar manifest fails authentication.
def test_recovery_rejects_tampered_sidecar_manifest(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )

    def mutate(sidecar, manifest):
        manifest["members"]["payload/00000002.enc"]["sha256"] = "1" * 64

    _mutate_sidecar(data, mutate)
    before = settings_target.read_bytes()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert MANIFEST_ERROR in result["error"]
    assert settings_target.read_bytes() == before
    assert (data / SIDECAR).is_dir()


# 11. A tampered settings ciphertext fails member authentication.
def test_recovery_rejects_tampered_settings_ciphertext(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    member = data / SIDECAR / "payload" / "00000002.enc"
    content = bytearray(member.read_bytes())
    content[0] ^= 0x01
    member.write_bytes(bytes(content))
    before = settings_target.read_bytes()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert "backup member authentication failed: payload/00000002.enc" in result["error"]
    assert settings_target.read_bytes() == before
    assert (data / SIDECAR).is_dir()


# 12/13/14. Missing, extra or renamed sidecar members are rejected.
def test_recovery_rejects_missing_sidecar_member(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    (data / SIDECAR / "payload" / "00000003.enc").unlink()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert (data / SIDECAR).is_dir()
    assert _journal_path(data).exists()


def test_recovery_rejects_extra_sidecar_member(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    (data / SIDECAR / "payload" / "00000009.enc").write_bytes(b"\x00" * 16)
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert (data / SIDECAR).is_dir()


def test_recovery_rejects_renamed_sidecar_member(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    payload = data / SIDECAR / "payload"
    (payload / "00000003.enc").rename(payload / "00000007.enc")
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert (data / SIDECAR).is_dir()


def test_recovery_rejects_non_directory_sidecar(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _m, _o = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    shutil.rmtree(data / SIDECAR)
    (data / SIDECAR).write_bytes(b"not-a-directory")
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert (data / SIDECAR).read_bytes() == b"not-a-directory"


# 15. The uninterrupted restore decrypts every payload exactly once.
def test_uninterrupted_restore_decrypts_each_payload_once(
    tmp_path, make_wav, monkeypatch
):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    calls: list[str] = []
    real_decrypt = backup_crypto.decrypt_member

    def spy(name, key, source, dest, **kwargs):
        calls.append(name)
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    result = restore_backup(
        backup, tmp_path / "data",
        settings_target=tmp_path / "live" / "settings.json",
        passphrase=PASSPHRASE,
    )
    assert result["status"] == "PASS"
    assert len(calls) == len(set(calls))
    assert len(calls) == 5  # index + transcripts + settings + dictionary + audio


# 16. Without settings_target no sidecar is created.
def test_no_settings_target_no_sidecar(tmp_path, make_wav):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    result = restore_backup(backup, data, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert not (data / SIDECAR).exists()


# 17. An archive without settings never creates a sidecar.
def test_no_settings_member_no_sidecar(tmp_path, make_wav):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav, settings=False)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    result = restore_backup(
        backup, data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert not (data / SIDECAR).exists()
    assert not settings_target.exists()


# 18. staging_building recovery never needs a passphrase (C1 behavior kept).
def test_staging_building_recovery_needs_no_passphrase(tmp_path, make_wav, monkeypatch):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    real_decrypt = backup_crypto.decrypt_member
    triggered = []

    def spy(name, key, source, dest, **kwargs):
        if name != "payload/00000000.enc" and not triggered:
            triggered.append(name)
            raise KeyboardInterrupt
        return real_decrypt(name, key, source, dest, **kwargs)

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(
            backup, data,
            settings_target=tmp_path / "live" / "settings.json",
            passphrase=PASSPHRASE,
        )
    monkeypatch.undo()
    data.mkdir(exist_ok=True)
    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert result["action"] == "staging_discarded"
    assert not _journal_path(data).exists()


# 19. Recovery preserves originals, models/exports and recovery directories.
def test_recovery_preserves_local_state(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, managed, original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    recovery_dir = tmp_path / "data.recovery-20260830T000000Z-deadbeef"
    recovery_dir.mkdir()
    (recovery_dir / "keep.txt").write_bytes(b"recovery")
    original_before = (original.read_bytes(), original.stat().st_mtime_ns)
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert (recovery_dir / "keep.txt").read_bytes() == b"recovery"
    assert original.read_bytes() == original_before[0]
    assert original.stat().st_mtime_ns == original_before[1]
    assert managed.is_file()


def test_ordinary_settings_write_failure_keeps_recoverable_state(
    tmp_path, make_wav, monkeypatch
):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    before = json.dumps(Settings().to_dict())
    settings_target.write_text(before, encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError("simulated settings write failure")

    monkeypatch.setattr(backup_module, "_apply_restored_settings", refuse)
    with pytest.raises(OSError, match="settings write failure"):
        restore_backup(
            backup, data, settings_target=settings_target, passphrase=PASSPHRASE
        )
    monkeypatch.undo()

    assert settings_target.read_text(encoding="utf-8") == before
    assert (data / SIDECAR).is_dir()
    assert _journal_path(data).is_file()
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()


def test_swap_started_promotion_remains_recoverable_after_passphrase_prompt(
    tmp_path, make_wav, monkeypatch
):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    LocalStore(data).save(_transcript("old", "d" * 64, ""))
    settings_target = tmp_path / "live-config" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    before = json.dumps(Settings().to_dict())
    settings_target.write_text(before, encoding="utf-8")
    original_replace = Path.replace
    destination = data.resolve()

    def interrupt_promotion(self, other):
        if Path(other).resolve() == destination and self.resolve() != destination:
            raise KeyboardInterrupt("simulated power loss between swap steps")
        return original_replace(self, other)

    monkeypatch.setattr(Path, "replace", interrupt_promotion)
    monkeypatch.setattr(backup_module.shutil, "rmtree", lambda *args, **kwargs: None)
    with pytest.raises(KeyboardInterrupt, match="between swap steps"):
        restore_backup(
            backup, data, settings_target=settings_target, passphrase=PASSPHRASE
        )
    monkeypatch.undo()
    assert not data.exists()

    first = recover_interrupted_restore(data, settings_target=settings_target)
    assert first["status"] == "PASS"
    assert first["action"] == "passphrase_required"
    assert data.is_dir()
    journal = json.loads(_journal_path(data).read_text(encoding="utf-8"))
    assert journal["stage"] == "swap_completed"

    second = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert second["status"] == "PASS"
    assert second["action"] == "settings_completed"
    assert settings_target.read_text(encoding="utf-8") != before
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()


def test_promoted_store_is_recovered_when_swap_completed_journal_write_dies(
    tmp_path, make_wav, monkeypatch
):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    LocalStore(data).save(_transcript("old", "d" * 64, ""))
    settings_target = tmp_path / "live-config" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    before = json.dumps(Settings().to_dict())
    settings_target.write_text(before, encoding="utf-8")
    real_write = backup_module._write_json_atomic

    def interrupt_completed_write(path, payload):
        if payload.get("stage") == "swap_completed":
            raise KeyboardInterrupt("simulated death before completed journal write")
        return real_write(path, payload)

    monkeypatch.setattr(
        backup_module, "_write_json_atomic", interrupt_completed_write
    )
    with pytest.raises(KeyboardInterrupt, match="completed journal write"):
        restore_backup(
            backup, data, settings_target=settings_target, passphrase=PASSPHRASE
        )
    monkeypatch.undo()

    assert data.is_dir()
    assert (data / SIDECAR).is_dir()
    journal = json.loads(_journal_path(data).read_text(encoding="utf-8"))
    assert journal["stage"] == "swap_started"
    assert not Path(journal["staging_path"]).exists()
    assert Path(journal["recovery_path"]).is_dir()

    first = recover_interrupted_restore(data, settings_target=settings_target)
    assert first["status"] == "PASS"
    assert first["action"] == "passphrase_required"
    journal = json.loads(_journal_path(data).read_text(encoding="utf-8"))
    assert journal["stage"] == "swap_completed"

    second = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert second["status"] == "PASS"
    assert second["action"] == "settings_completed"
    assert settings_target.read_text(encoding="utf-8") != before
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()


def test_recovery_rejects_extra_sidecar_root_file(tmp_path, make_wav, monkeypatch):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    extra = data / SIDECAR / "unexpected.txt"
    extra.write_bytes(b"unexpected")
    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert extra.read_bytes() == b"unexpected"
    assert _journal_path(data).exists()


def test_recovery_rejects_authenticated_invalid_manifest_profile(
    tmp_path, make_wav, monkeypatch
):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    manifest_path = data / SIDECAR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    manifest["encryption"]["algorithm"] = "unsupported-test-algorithm"
    manifest["encryption"]["manifest_tag_base64"] = ""
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["encryption"]["manifest_tag_base64"] = base64.b64encode(
        backup_crypto.compute_manifest_tag(
            backup_crypto.derive_manifest_key(master_key), canonical
        )
    ).decode("ascii")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    result = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert result["status"] == "FAIL"
    assert "unsupported backup encryption algorithm" in result["error"]
    assert (data / SIDECAR).is_dir()
    assert _journal_path(data).exists()


def test_recovery_rejects_empty_pending_settings_target(
    tmp_path, make_wav, monkeypatch
):
    data, settings_target, _backup, _managed, _original = _crash_after_swap(
        tmp_path, make_wav, monkeypatch
    )
    journal_path = _journal_path(data)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["settings_target"] = ""
    backup_module._write_json_atomic(journal_path, journal)
    before = settings_target.read_bytes()

    result = recover_interrupted_restore(data)
    assert result["status"] == "FAIL"
    assert "settings target is invalid" in result["error"]
    assert settings_target.read_bytes() == before
    assert (data / SIDECAR).is_dir()
    assert journal_path.exists()


def test_applied_settings_are_marked_before_sidecar_cleanup(
    tmp_path, make_wav, monkeypatch
):
    _store, backup, _managed, _original = _make_v2(tmp_path, make_wav)
    data = tmp_path / "data"
    settings_target = tmp_path / "live-config" / "settings.json"
    real_rmtree = shutil.rmtree

    def interrupt_sidecar_cleanup(path, *args, **kwargs):
        if Path(path).name == SIDECAR:
            raise KeyboardInterrupt("simulated death before sidecar cleanup")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.shutil, "rmtree", interrupt_sidecar_cleanup)
    with pytest.raises(KeyboardInterrupt, match="before sidecar cleanup"):
        restore_backup(
            backup, data, settings_target=settings_target, passphrase=PASSPHRASE
        )
    monkeypatch.undo()

    assert settings_target.is_file()
    assert (data / SIDECAR).is_dir()
    journal = json.loads(_journal_path(data).read_text(encoding="utf-8"))
    assert journal["stage"] == "swap_completed"
    assert journal["settings_payload_written"] is True

    result = recover_interrupted_restore(data)
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    assert not (data / SIDECAR).exists()
    assert not _journal_path(data).exists()
