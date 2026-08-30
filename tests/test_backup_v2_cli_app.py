"""CLI integration tests for encrypted backup v2 (W2-E1 Slice C2b).

Covers the ``--encrypt`` flag, interactive getpass prompts, pending
encrypted recovery settlement and secret hygiene in the CLI layer.
GUI integration is out of scope (Slice D).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voice_studio import backup as backup_module
from voice_studio.backup import create_backup, restore_backup
from voice_studio.cli import build_parser, main
from voice_studio.config import save_settings
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic slice-c2b passphrase"
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


def _profile(tmp_path: Path, monkeypatch) -> Path:
    """Point the CLI at isolated data/config dirs; return the data dir."""
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    return data


def _seed_store(tmp_path: Path, make_wav) -> LocalStore:
    store = LocalStore(tmp_path / "store")
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(_transcript("rec-cli-0", digest, str(managed)))
    return store


def _make_v1_backup(tmp_path: Path, make_wav, *, settings: bool = True) -> Path:
    store = _seed_store(tmp_path, make_wav)
    settings_file = None
    if settings:
        settings_file = tmp_path / "config-src" / "settings.json"
        save_settings(Settings(), settings_file)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup, settings_file=settings_file)
    return backup


def _make_v2_backup(tmp_path: Path, make_wav, *, settings: bool = True) -> Path:
    store = _seed_store(tmp_path, make_wav)
    settings_file = None
    if settings:
        settings_file = tmp_path / "config-src" / "settings.json"
        save_settings(Settings(), settings_file)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)
    return backup


class _Tty:
    @staticmethod
    def isatty() -> bool:
        return True


class _NonTty:
    @staticmethod
    def isatty() -> bool:
        return False


def _interactive(monkeypatch, answers=None, error=None):
    """Make stdin a tty and replace getpass with a scripted spy."""
    monkeypatch.setattr(sys, "stdin", _Tty())
    calls: list[str] = []
    queue = list(answers or [])

    def fake_getpass(prompt=""):
        calls.append(prompt)
        if error is not None:
            raise error
        assert queue, "unexpected extra passphrase prompt"
        return queue.pop(0)

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    return calls


def _fail_on_prompt(monkeypatch):
    def forbidden(prompt=""):
        raise AssertionError(f"unexpected passphrase prompt: {prompt}")

    monkeypatch.setattr("getpass.getpass", forbidden)


def _noninteractive(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _NonTty())
    _fail_on_prompt(monkeypatch)


def _crash_pending_v2(tmp_path: Path, make_wav, monkeypatch) -> tuple[Path, Path]:
    """Leave a v2 restore interrupted after swap_completed; return data dir."""
    data = _profile(tmp_path, monkeypatch)
    backup = _make_v2_backup(tmp_path, make_wav)
    settings_target = tmp_path / "config" / "settings.json"
    settings_target.parent.mkdir(parents=True, exist_ok=True)
    settings_target.write_text(json.dumps(Settings().to_dict()), encoding="utf-8")

    def _die(*args, **kwargs):
        raise KeyboardInterrupt

    original_apply = backup_module._apply_restored_settings
    backup_module._apply_restored_settings = _die
    try:
        with pytest.raises(KeyboardInterrupt):
            restore_backup(
                backup, data, settings_target=settings_target, passphrase=PASSPHRASE
            )
    finally:
        backup_module._apply_restored_settings = original_apply
    assert backup_module.restore_journal_path(data).exists()
    assert (data / ".restore-settings-v2").is_dir()
    return data, backup


# 1. The parser accepts `backup create --encrypt` (default off).
def test_parser_accepts_encrypt_flag():
    args = build_parser().parse_args(["backup", "create", "out.voice-backup", "--encrypt"])
    assert args.encrypt is True
    default = build_parser().parse_args(["backup", "create", "out.voice-backup"])
    assert default.encrypt is False


# 2. No argv passphrase option exists anywhere in the backup commands.
def test_parser_has_no_passphrase_option():
    for argv in (
        ["backup", "create", "out", "--passphrase", "x"],
        ["backup", "verify", "f", "--passphrase", "x"],
        ["backup", "restore", "f", "--passphrase", "x"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(argv)


# 3. Plaintext create never prompts, even with a tty and without --encrypt.
def test_plaintext_create_never_prompts(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    store = _seed_store(tmp_path, make_wav)
    assert store.list()  # seeded
    _fail_on_prompt(monkeypatch)
    out = tmp_path / "plain.voice-backup"
    assert main(["backup", "create", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert out.is_file()


# 4. Encrypted create prompts exactly twice and succeeds.
def test_encrypted_create_prompts_twice(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    _seed_store(tmp_path, make_wav)
    prompts = _interactive(monkeypatch, [PASSPHRASE, PASSPHRASE])
    out = tmp_path / "enc.voice-backup"
    assert main(["backup", "create", str(out), "--encrypt"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["version"] == 2
    assert len(prompts) == 2
    assert PASSPHRASE not in captured.out + captured.err


# 5a. Mismatched confirmation is a concrete error; no archive is created.
def test_encrypted_create_mismatch(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    _seed_store(tmp_path, make_wav)
    _interactive(monkeypatch, ["one passphrase", "different passphrase"])
    out = tmp_path / "enc.voice-backup"
    assert main(["backup", "create", str(out), "--encrypt"]) == 2
    captured = capsys.readouterr()
    assert "do not match" in captured.err
    assert "one passphrase" not in captured.err
    assert not out.exists()


# 5b. An empty passphrase is a concrete error.
def test_encrypted_create_empty_passphrase(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    _seed_store(tmp_path, make_wav)
    _interactive(monkeypatch, ["", ""])
    out = tmp_path / "enc.voice-backup"
    assert main(["backup", "create", str(out), "--encrypt"]) == 2
    assert "must not be empty" in capsys.readouterr().err
    assert not out.exists()


# 5c. A non-interactive terminal is a concrete error with no fallback.
def test_encrypted_create_noninteractive(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    _seed_store(tmp_path, make_wav)
    _noninteractive(monkeypatch)
    out = tmp_path / "enc.voice-backup"
    assert main(["backup", "create", str(out), "--encrypt"]) == 2
    assert "interactive terminal" in capsys.readouterr().err
    assert not out.exists()


# 6. v1 verify never prompts.
def test_v1_verify_never_prompts(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    backup = _make_v1_backup(tmp_path, make_wav)
    _fail_on_prompt(monkeypatch)
    assert main(["backup", "verify", str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


# 7. v2 verify prompts exactly once and passes the passphrase through.
def test_v2_verify_prompts_once(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    backup = _make_v2_backup(tmp_path, make_wav)
    prompts = _interactive(monkeypatch, [PASSPHRASE])
    assert main(["backup", "verify", str(backup)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "PASS"
    assert prompts == ["Backup passphrase: "]
    assert PASSPHRASE not in captured.out + captured.err


# 8. A wrong verify passphrase prints only the contract error.
def test_v2_verify_wrong_passphrase(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    backup = _make_v2_backup(tmp_path, make_wav)
    _interactive(monkeypatch, [WRONG])
    assert main(["backup", "verify", str(backup)]) == 2
    captured = capsys.readouterr()
    assert MANIFEST_ERROR in captured.err
    assert WRONG not in captured.err + captured.out


# 9. v1 restore never prompts.
def test_v1_restore_never_prompts(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    backup = _make_v1_backup(tmp_path, make_wav)
    _fail_on_prompt(monkeypatch)
    assert main(["backup", "restore", str(backup)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["recovered_interrupted_restore"]["action"] == "none"


# 10. v2 restore prompts exactly once.
def test_v2_restore_prompts_once(tmp_path, make_wav, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    backup = _make_v2_backup(tmp_path, make_wav)
    prompts = _interactive(monkeypatch, [PASSPHRASE])
    assert main(["backup", "restore", str(backup)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert len(prompts) == 1
    assert PASSPHRASE not in captured.out + captured.err


# 11. Pending v2 settings recovery: one prompt, then a v1 restore proceeds.
def test_pending_recovery_prompts_and_completes(tmp_path, make_wav, monkeypatch, capsys):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    v1 = _make_v1_backup(tmp_path, make_wav, settings=False)
    prompts = _interactive(monkeypatch, [PASSPHRASE])
    assert main(["backup", "restore", str(v1)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "PASS"
    assert len(prompts) == 1
    assert not (data / ".restore-settings-v2").exists()
    assert not backup_module.restore_journal_path(data).exists()
    # The interrupted settings were applied during settlement.
    settings = json.loads(
        (tmp_path / "config" / "settings.json").read_text(encoding="utf-8")
    )
    assert isinstance(settings, dict)
    assert PASSPHRASE not in captured.out + captured.err


# 12. A wrong recovery passphrase blocks the new restore and keeps everything.
def test_wrong_recovery_passphrase_blocks_restore(tmp_path, make_wav, monkeypatch, capsys):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    v1 = _make_v1_backup(tmp_path, make_wav, settings=False)
    _interactive(monkeypatch, [WRONG])
    assert main(["backup", "restore", str(v1)]) == 2
    captured = capsys.readouterr()
    assert "authentication failed" in captured.err
    assert WRONG not in captured.err
    assert captured.out == ""  # the new restore never ran
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()
    # A retry with the correct passphrase still succeeds afterwards.
    _interactive(monkeypatch, [PASSPHRASE])
    assert main(["backup", "restore", str(v1)]) == 0
    assert not backup_module.restore_journal_path(data).exists()


# 13. Cancelling the recovery prompt keeps the journal and sidecar.
def test_cancelled_recovery_preserves_journal(tmp_path, make_wav, monkeypatch, capsys):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    v1 = _make_v1_backup(tmp_path, make_wav, settings=False)
    _interactive(monkeypatch, error=KeyboardInterrupt)
    assert main(["backup", "restore", str(v1)]) == 130
    assert "cancelled" in capsys.readouterr().err
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


# 13b. EOF on the recovery prompt is a concrete error, journal preserved.
def test_eof_recovery_preserves_journal(tmp_path, make_wav, monkeypatch, capsys):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    v1 = _make_v1_backup(tmp_path, make_wav, settings=False)
    _interactive(monkeypatch, error=EOFError)
    assert main(["backup", "restore", str(v1)]) == 2
    assert "cancelled" in capsys.readouterr().err
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


# 14. Pending encrypted recovery blocks a new restore when non-interactive.
def test_noninteractive_pending_recovery_blocks_restore(
    tmp_path, make_wav, monkeypatch, capsys
):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    v1 = _make_v1_backup(tmp_path, make_wav, settings=False)
    _noninteractive(monkeypatch)
    assert main(["backup", "restore", str(v1)]) == 2
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.err
    assert captured.out == ""  # the new restore never ran
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


# 15. Other commands report the pending recovery but never prompt.
def test_other_commands_report_pending_without_prompt(
    tmp_path, make_wav, monkeypatch, capsys
):
    data, _v2 = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    _fail_on_prompt(monkeypatch)
    assert main(["history"]) == 0
    captured = capsys.readouterr()
    assert "passphrase_required" in captured.err
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


def test_failed_settlement_blocks_new_restore(
    tmp_path, make_wav, monkeypatch, capsys
):
    data = _profile(tmp_path, monkeypatch)
    LocalStore(data).save(_transcript("live", "d" * 64, ""))
    backup = _make_v1_backup(tmp_path, make_wav, settings=False)
    journal = backup_module.restore_journal_path(data)
    journal.write_text("{ not valid json", encoding="utf-8")
    _fail_on_prompt(monkeypatch)

    assert main(["backup", "restore", str(backup)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "restore-journal:" in captured.err
    assert LocalStore(data).get("live") is not None
    assert LocalStore(data).get("rec-cli-0") is None
    assert journal.exists()


def test_missing_stdin_is_a_concrete_noninteractive_error(
    tmp_path, make_wav, monkeypatch, capsys
):
    _profile(tmp_path, monkeypatch)
    _seed_store(tmp_path, make_wav)
    monkeypatch.setattr(sys, "stdin", None)
    _fail_on_prompt(monkeypatch)
    output = tmp_path / "enc.voice-backup"

    assert main(["backup", "create", str(output), "--encrypt"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "interactive terminal" in captured.err
    assert not output.exists()
