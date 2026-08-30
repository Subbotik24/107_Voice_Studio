import inspect
import json
import shutil
import zipfile
from pathlib import Path

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


def _seed_backup(tmp_path, make_wav, *, settings_file=None):
    """Build a store with one audio-backed record and a verified backup of it."""

    source_root = tmp_path / "backup-source"
    store = LocalStore(source_root)
    original = make_wav(tmp_path / "seed.wav")
    managed, digest = store.import_source(original)
    store.save(transcript("backed-up", digest, str(managed)))
    archive_path = tmp_path / "seed.voice-backup"
    create_backup(store, archive_path, settings_file=settings_file)
    assert verify_backup(archive_path)["status"] == "PASS"
    return archive_path


def _crash_between_swap_steps(monkeypatch, data_root):
    """Reproduce a process death between swap step A and swap step B.

    ``Path.replace`` fails exactly when a directory is moved *into* ``data_root``
    — that is swap step B and the in-process rollback. ``shutil.rmtree`` becomes
    a no-op so the ``finally`` cleanup cannot run either, leaving the on-disk
    state a killed process would leave.
    """

    original_replace = Path.replace
    target = data_root.resolve()

    def guarded(self, other):
        if Path(other).resolve() == target and self.resolve() != target:
            raise KeyboardInterrupt("simulated power loss between swap steps")
        return original_replace(self, other)

    monkeypatch.setattr(Path, "replace", guarded)
    monkeypatch.setattr(backup_module.shutil, "rmtree", lambda *args, **kwargs: None)


def _journal(data_root):
    return backup_module.restore_journal_path(data_root)


def _staging_directories(data_root):
    prefix = f".{data_root.name}.restore-"
    return sorted(
        path
        for path in data_root.parent.iterdir()
        if path.is_dir() and path.name.startswith(prefix)
    )


def _recovery_directories(data_root):
    prefix = f"{data_root.name}.recovery-"
    return sorted(
        path
        for path in data_root.parent.iterdir()
        if path.is_dir() and path.name.startswith(prefix)
    )


def test_interrupted_swap_is_completed_from_the_journal(tmp_path, make_wav, monkeypatch):
    archive_path = _seed_backup(tmp_path, make_wav)
    data = tmp_path / "data"
    LocalStore(data).save(
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
    _crash_between_swap_steps(monkeypatch, data)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(archive_path, data)

    monkeypatch.undo()
    assert not data.exists()
    assert _journal(data).is_file()
    assert _staging_directories(data)
    assert _recovery_directories(data)

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "PASS"
    assert result["action"] == "completed"
    assert result["records"] == 1
    assert data.is_dir()
    store = LocalStore(data)
    assert len(store.list(limit=100)) == 1
    assert store.get("backed-up") is not None
    assert store.audit()["status"] == "PASS"
    assert not _journal(data).exists()
    assert _recovery_directories(data), "the displaced data must never be deleted"


def test_interrupted_swap_rolls_back_when_staging_is_gone(tmp_path, make_wav, monkeypatch):
    archive_path = _seed_backup(tmp_path, make_wav)
    data = tmp_path / "data"
    LocalStore(data).save(
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
    _crash_between_swap_steps(monkeypatch, data)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(archive_path, data)

    monkeypatch.undo()
    for staging in _staging_directories(data):
        shutil.rmtree(staging)

    recovery_before = _recovery_directories(data)
    assert recovery_before

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "PASS"
    assert result["action"] == "rolled_back"
    assert result["recovery"] == str(recovery_before[0])
    assert data.is_dir()
    store = LocalStore(data)
    assert store.get("current") is not None
    assert store.get("backed-up") is None
    # A rollback returns the displaced data to `data_root` by renaming the
    # recovery directory back. The directory is consumed, never deleted: the
    # pre-restore store is intact at its original location.
    assert not _recovery_directories(data)
    assert not _journal(data).exists()


def test_interrupted_settings_write_is_completed_from_the_journal(
    tmp_path, make_wav, monkeypatch
):
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text('{"replacements":[]}', encoding="utf-8")
    settings_file = tmp_path / "config" / "settings.json"
    save_settings(Settings(dictionary_path=str(dictionary), auto_copy=True), settings_file)
    archive_path = _seed_backup(tmp_path, make_wav, settings_file=settings_file)
    save_settings(Settings(dictionary_path=str(dictionary), auto_copy=False), settings_file)

    def refuse(*args, **kwargs):
        raise KeyboardInterrupt("simulated power loss before the settings write")

    monkeypatch.setattr(backup_module, "_apply_restored_settings", refuse)
    data = tmp_path / "data"
    with pytest.raises(KeyboardInterrupt):
        restore_backup(archive_path, data, settings_target=settings_file)

    monkeypatch.undo()
    assert data.is_dir()
    assert json.loads(settings_file.read_text(encoding="utf-8"))["auto_copy"] is False

    result = backup_module.recover_interrupted_restore(data, settings_target=settings_file)

    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    restored_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert restored_settings["auto_copy"] is True
    assert restored_settings["dictionary_path"].endswith("dictionary.restored.json")
    assert (settings_file.parent / "dictionary.restored.json").is_file()
    preserved = sorted(settings_file.parent.glob(f"{settings_file.name}.pre-restore-*"))
    assert preserved, "the pre-restore settings copy must not be lost"
    assert json.loads(preserved[0].read_text(encoding="utf-8"))["auto_copy"] is False
    assert not _journal(data).exists()
    assert not (data / ".restore-settings.json").exists()


def test_journal_without_a_swap_only_discards_staging(tmp_path, make_wav):
    data = tmp_path / "data"
    store = LocalStore(data)
    store.save(transcript("kept", "d" * 64, ""))
    staging = data.parent / f".{data.name}.restore-abcdef"
    LocalStore(staging)
    backup_module._write_json_atomic(
        _journal(data),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-28T00:00:00+00:00",
            "data_root": str(data.resolve()),
            "staging_path": str(staging.resolve()),
            "recovery_path": None,
            "expected_records": 1,
            "settings_target": None,
            "settings_payload_written": False,
            "stage": "swap_started",
        },
    )

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "PASS"
    assert result["action"] == "staging_discarded"
    assert not staging.exists()
    assert LocalStore(data).get("kept") is not None
    assert not _journal(data).exists()


def test_successful_restore_leaves_no_journal_and_recovery_is_a_noop(tmp_path, make_wav):
    archive_path = _seed_backup(tmp_path, make_wav)
    data = tmp_path / "data"

    restored = restore_backup(archive_path, data)

    assert restored["status"] == "PASS"
    assert restored["journal_cleared"] is True
    assert not _journal(data).exists()
    assert not (data / ".restore-settings.json").exists()
    assert backup_module.recover_interrupted_restore(data)["action"] == "none"


def test_recovery_is_idempotent(tmp_path, make_wav, monkeypatch):
    archive_path = _seed_backup(tmp_path, make_wav)
    data = tmp_path / "data"
    LocalStore(data)
    _crash_between_swap_steps(monkeypatch, data)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(archive_path, data)
    monkeypatch.undo()

    first = backup_module.recover_interrupted_restore(data)
    assert first["action"] == "completed"
    before = sorted(path.name for path in data.parent.iterdir())

    second = backup_module.recover_interrupted_restore(data)

    assert second["status"] == "PASS"
    assert second["action"] == "none"
    assert sorted(path.name for path in data.parent.iterdir()) == before


@pytest.mark.parametrize(
    "payload",
    ["{ not json", json.dumps({"journal_version": 99, "stage": "swap_started"})],
)
def test_unusable_journal_fails_without_deleting_anything(tmp_path, payload):
    data = tmp_path / "data"
    LocalStore(data).save(transcript("kept", "d" * 64, ""))
    staging = data.parent / f".{data.name}.restore-abcdef"
    LocalStore(staging)
    _journal(data).write_text(payload, encoding="utf-8")

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "FAIL"
    assert result["action"] == "none"
    assert result.get("error")
    assert staging.is_dir()
    assert LocalStore(data).get("kept") is not None
    assert _journal(data).is_file()


def test_restore_journal_carries_no_transcript_text_and_no_secrets(
    tmp_path, make_wav, monkeypatch
):
    secret = "sk-test-000000000000000000000000"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    source_root = tmp_path / "backup-source"
    store = LocalStore(source_root)
    original = make_wav(tmp_path / "seed.wav")
    managed, digest = store.import_source(original)
    item = transcript("secret-bearing", digest, str(managed))
    item.raw_text = "confidential dictated sentence"
    item.corrected_text = "confidential dictated sentence"
    store.save(item)
    archive_path = tmp_path / "seed.voice-backup"
    create_backup(store, archive_path)

    data = tmp_path / "data"
    LocalStore(data)
    _crash_between_swap_steps(monkeypatch, data)
    with pytest.raises(KeyboardInterrupt):
        restore_backup(archive_path, data)
    monkeypatch.undo()

    journal_text = _journal(data).read_text(encoding="utf-8")
    assert "confidential dictated sentence" not in journal_text
    assert secret not in journal_text
    assert "sk-" not in journal_text
    journal = json.loads(journal_text)
    assert set(journal) == {
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


def test_public_backup_signatures_are_unchanged():
    assert list(inspect.signature(verify_backup).parameters) == ["path"]
    assert [
        (name, parameter.kind)
        for name, parameter in inspect.signature(restore_backup).parameters.items()
    ] == [
        ("path", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("data_root", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("settings_target", inspect.Parameter.KEYWORD_ONLY),
    ]


def test_recovery_reports_failure_when_neither_copy_survives(tmp_path):
    """Losing both directories is reported, never papered over with an empty store."""

    data = tmp_path / "data"
    data.parent.mkdir(parents=True, exist_ok=True)
    staging = data.parent / f".{data.name}.restore-abcdef"
    recovery = data.parent / f"{data.name}.recovery-20260828T000000Z-abcdef12"
    backup_module._write_json_atomic(
        _journal(data),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-28T00:00:00+00:00",
            "data_root": str(data.resolve()),
            "staging_path": str(staging),
            "recovery_path": str(recovery),
            "expected_records": 1,
            "settings_target": None,
            "settings_payload_written": True,
            "stage": "swap_started",
        },
    )

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "FAIL"
    assert result["action"] == "none"
    assert "nothing was removed" in result["error"]
    assert not data.exists()
    assert _journal(data).is_file(), "the journal must survive for a later attempt"


@pytest.mark.parametrize("field", ["staging_path", "recovery_path"])
def test_recovery_refuses_paths_outside_the_data_directory(tmp_path, field):
    """A journal is a plain on-disk file; it must not promote a foreign directory."""

    data = tmp_path / "root" / "data"
    LocalStore(data).save(transcript("kept", "d" * 64, ""))
    elsewhere = tmp_path / "elsewhere" / "payload"
    LocalStore(elsewhere)
    journal = {
        "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
        "backup_version": backup_module.BACKUP_VERSION,
        "created_at": "2026-08-28T00:00:00+00:00",
        "data_root": str(data.resolve()),
        "staging_path": str(data.parent / f".{data.name}.restore-abcdef"),
        "recovery_path": None,
        "expected_records": 1,
        "settings_target": None,
        "settings_payload_written": True,
        "stage": "swap_started",
    }
    journal[field] = str(elsewhere.resolve())
    backup_module._write_json_atomic(_journal(data), journal)

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "FAIL"
    assert "outside the data directory" in result["error"]
    assert elsewhere.is_dir()
    assert LocalStore(data).get("kept") is not None


def test_recovery_refuses_a_journal_from_another_data_directory(tmp_path):
    data = tmp_path / "data"
    other = tmp_path / "other-data"
    LocalStore(data).save(transcript("kept", "d" * 64, ""))
    backup_module._write_json_atomic(
        _journal(data),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-28T00:00:00+00:00",
            "data_root": str(other.resolve()),
            "staging_path": str(tmp_path / f".{other.name}.restore-abcdef"),
            "recovery_path": None,
            "expected_records": 1,
            "settings_target": None,
            "settings_payload_written": True,
            "stage": "swap_started",
        },
    )

    result = backup_module.recover_interrupted_restore(data)

    assert result["status"] == "FAIL"
    assert "different data directory" in result["error"]
    assert LocalStore(data).get("kept") is not None
