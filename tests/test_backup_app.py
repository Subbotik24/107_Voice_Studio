import json
import zipfile

import pytest

from voice_studio import backup as backup_module
from voice_studio.archive import ZipBudget
from voice_studio.backup import create_backup, restore_backup, verify_backup
from voice_studio.config import save_settings
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore


def transcript(item_id: str, source_hash: str, source_path: str) -> Transcript:
    return Transcript(
        id=item_id,
        created_at="2026-07-27T00:00:00+00:00",
        source_name="safe.wav",
        source_sha256=source_hash,
        source_path=source_path,
        language="uk",
        engine="fixture",
        model="fixture",
        raw_text="raw",
        corrected_text="corrected",
    )


def test_backup_verify_and_recover_preserves_previous_data(tmp_path, make_wav):
    data = tmp_path / "data"
    store = LocalStore(data)
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(transcript("backed-up", digest, str(managed)))
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text('{"replacements":[]}', encoding="utf-8")
    settings_file = tmp_path / "config" / "settings.json"
    save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    backup = tmp_path / "safe.voice-backup"
    created = create_backup(store, backup, settings_file=settings_file)
    assert created["records"] == 1
    assert verify_backup(backup)["status"] == "PASS"

    store.save(
        Transcript(
            id="current",
            created_at="2026-07-27T00:01:00+00:00",
            source_name="current.wav",
            source_sha256="c" * 64,
            language="uk",
            engine="fixture",
            model="fixture",
            raw_text="current",
            corrected_text="current",
        )
    )
    restored = restore_backup(backup, data, settings_target=settings_file)
    assert restored["status"] == "PASS"
    assert restored["recovery"]
    assert LocalStore(data).get("backed-up") is not None
    assert LocalStore(data).get("current") is None
    restored_item = LocalStore(data).get("backed-up")
    assert restored_item is not None
    assert restored_item.source_path and restored_item.audio_retained
    assert original.exists()
    restored_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert restored_settings["dictionary_path"].endswith("dictionary.restored.json")


def test_backup_duplicate_member_is_rejected(tmp_path):
    store = LocalStore(tmp_path / "data")
    backup = tmp_path / "safe.voice-backup"
    create_backup(store, backup)
    with pytest.warns(UserWarning, match="Duplicate"):
        with zipfile.ZipFile(backup, "a") as archive:
            archive.writestr("manifest.json", "{}")
    with pytest.raises(ValueError, match="duplicate"):
        verify_backup(backup)


def test_restore_rejects_hash_mismatch_before_replacing_current_data(tmp_path):
    source_store = LocalStore(tmp_path / "backup-source")
    original = tmp_path / "original.wav"
    original.write_bytes(b"audio-content")
    managed, _digest = source_store.import_source(original)
    source_store.save(transcript("invalid", "0" * 64, str(managed)))
    backup = tmp_path / "invalid.voice-backup"
    create_backup(source_store, backup)
    assert verify_backup(backup)["status"] == "PASS"

    data = tmp_path / "current-data"
    current_store = LocalStore(data)
    current_store.save(
        Transcript(
            id="current",
            created_at="2026-07-27T00:01:00+00:00",
            source_name="current.wav",
            source_sha256="c" * 64,
            language="uk",
            engine="fixture",
            model="fixture",
            raw_text="current",
            corrected_text="current",
        )
    )

    with pytest.raises(ValueError, match="storage audit"):
        restore_backup(backup, data)

    assert LocalStore(data).get("current") is not None


def test_backup_verification_enforces_the_shared_zip_budget(tmp_path, monkeypatch):
    archive_path = tmp_path / "oversized.voice-backup"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("transcripts.jsonl", "")

    monkeypatch.setattr(
        backup_module,
        "BACKUP_ZIP_BUDGET",
        ZipBudget(max_members=1, max_central_directory_bytes=1024),
    )

    with pytest.raises(ValueError, match="max_members"):
        verify_backup(archive_path)


def test_restore_preflights_free_space_for_bounded_expansion(
    tmp_path, monkeypatch
):
    source_store = LocalStore(tmp_path / "source")
    backup = tmp_path / "safe.voice-backup"
    create_backup(source_store, backup)
    calls: list[tuple[object, int, int]] = []

    monkeypatch.setattr(
        backup_module,
        "require_free_space",
        lambda path, required, *, margin_bytes: calls.append(
            (path, required, margin_bytes)
        ) or required + margin_bytes,
    )

    restore_backup(backup, tmp_path / "restored")

    assert calls
    assert calls[0][1] > 0
    assert calls[0][2] > 0
