"""GUI integration tests for encrypted backup v2 (W2-E1 Slice D1).

Behavioural tests run against ``object.__new__(VoiceStudioApp)`` stubs with
monkeypatched Tk dialogs, following the conventions of
``tests/test_gui_contract_app.py``. Two source tripwires are included only
where no observable effect exists without a display (checkbox declaration,
event wiring).
"""
from __future__ import annotations

import inspect
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio import backup as backup_module
from voice_studio.app import VoiceStudioApp
from voice_studio.backup import create_backup, restore_backup
from voice_studio.config import save_settings
from voice_studio.i18n import UI_LANGUAGE_CHOICES, translate
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic slice-d1 passphrase"
WRONG = "synthetic wrong passphrase"

REQUIRED_ERROR = "backup is encrypted; a passphrase is required"
MANIFEST_ERROR = "backup authentication failed: wrong passphrase or corrupted manifest"


# --- stubs and helpers -----------------------------------------------------


def _app_stub() -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.events = queue.Queue()
    app.status_values: list[str] = []
    app.status = SimpleNamespace(set=app.status_values.append)
    app.busy_values: list[bool] = []
    app._set_busy = lambda value: app.busy_values.append(value)
    app.reloads: list[str] = []
    app._reload_after_restore = lambda: app.reloads.append("reload")
    app.started_operations: list[tuple[str, object, object]] = []
    app._t = lambda key, **values: translate("uk", key, **values)
    return app


def _dialogs(monkeypatch, answers=None):
    """Replace Tk dialogs with recorders; return (prompts, errors)."""
    prompts: list[dict] = []
    errors: list[tuple[str, str]] = []
    queue_answers = list(answers) if answers is not None else []

    def fake_askstring(title, message, **kwargs):
        prompts.append(
            {
                "title": title,
                "message": message,
                "show": kwargs.get("show"),
                "thread": threading.current_thread(),
            }
        )
        if not queue_answers:
            raise AssertionError("unexpected passphrase prompt")
        return queue_answers.pop(0)

    def fake_showerror(title, message, **kwargs):
        errors.append((title, message))

    monkeypatch.setattr(app_module.simpledialog, "askstring", fake_askstring)
    monkeypatch.setattr(app_module.messagebox, "showerror", fake_showerror)
    return prompts, errors


def _forbid_dialogs(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected Tk dialog call")

    monkeypatch.setattr(app_module.simpledialog, "askstring", forbidden)
    monkeypatch.setattr(app_module.messagebox, "showerror", forbidden)
    monkeypatch.setattr(app_module.messagebox, "showwarning", forbidden)


def _run_worker_synchronously(app: VoiceStudioApp) -> None:
    """Make _start_backup_operation execute the worker inline."""

    def fake_start_worker(name, work, daemon=False):
        work()
        return SimpleNamespace(is_alive=lambda: False)

    app._start_worker = fake_start_worker
    app._assign_worker_alias = lambda *args: None


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


def _make_v2_backup(tmp_path: Path, make_wav) -> Path:
    store = LocalStore(tmp_path / "store")
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(_transcript("rec-gui-0", digest, str(managed)))
    settings_file = tmp_path / "config-src" / "settings.json"
    save_settings(Settings(), settings_file)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)
    return backup


def _crash_pending_v2(tmp_path: Path, make_wav, monkeypatch) -> Path:
    """Leave a real v2 restore interrupted after swap_completed."""
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
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
    return data


# --- create flow -----------------------------------------------------------


# Tripwire only: the checkbox exists and defaults off (no display available).
def test_backup_dialog_declares_encrypt_checkbox_default_off() -> None:
    source = inspect.getsource(VoiceStudioApp._backup_dialog)
    assert "backup_encrypt" in source
    assert "backup_encrypt_warning" in source
    assert "BooleanVar(value=False)" in source


def test_new_passphrase_two_masked_prompts_success(monkeypatch) -> None:
    app = _app_stub()
    prompts, errors = _dialogs(monkeypatch, [PASSPHRASE, PASSPHRASE])
    result = VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    assert result == PASSPHRASE
    assert [p["show"] for p in prompts] == ["*", "*"]
    assert errors == []


def test_new_passphrase_prompt_warns_about_unrecoverable_loss(monkeypatch) -> None:
    app = _app_stub()
    prompts, _errors = _dialogs(monkeypatch, [PASSPHRASE, PASSPHRASE])
    VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    warning = translate("uk", "backup_encrypt_warning")
    assert warning in prompts[0]["message"]


def test_new_passphrase_empty_is_localized_error(monkeypatch) -> None:
    app = _app_stub()
    prompts, errors = _dialogs(monkeypatch, ["", ""])
    result = VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    assert result is None
    assert len(prompts) == 1  # no second prompt after the empty first one
    assert errors == [(translate("uk", "backup"), translate("uk", "backup_passphrase_empty"))]


def test_new_passphrase_mismatch_is_localized_error(monkeypatch) -> None:
    app = _app_stub()
    _prompts, errors = _dialogs(monkeypatch, ["first passphrase", "second passphrase"])
    result = VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    assert result is None
    assert errors == [
        (translate("uk", "backup"), translate("uk", "backup_passphrase_mismatch"))
    ]


@pytest.mark.parametrize("answers", [[None], [PASSPHRASE, None]], ids=["first", "second"])
def test_new_passphrase_cancel_starts_nothing(monkeypatch, answers) -> None:
    app = _app_stub()
    _prompts, errors = _dialogs(monkeypatch, answers)
    result = VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    assert result is None
    assert errors == []


def test_queue_create_hands_passphrase_to_runner_without_callback_capture(
    monkeypatch, tmp_path
) -> None:
    app = _app_stub()
    app.store = object()
    captured: list[dict] = []
    monkeypatch.setattr(
        app_module,
        "create_backup",
        lambda *args, **kwargs: captured.append(kwargs) or {"status": "PASS"},
    )
    operations: list[tuple[str, object, object]] = []

    VoiceStudioApp._queue_backup_create(
        app,
        tmp_path / "encrypted.voice-backup",
        True,
        PASSPHRASE,
        lambda action, callback, passphrase=None: operations.append(
            (action, callback, passphrase)
        ),
    )

    action, callback, runner_passphrase = operations[0]
    assert action == "create"
    assert runner_passphrase == PASSPHRASE
    assert callback.__closure__ is not None
    assert all(cell.cell_contents != PASSPHRASE for cell in callback.__closure__)
    callback(runner_passphrase)
    assert captured == [
        {
            "settings_file": app_module.settings_path(),
            "include_audio": True,
            "passphrase": PASSPHRASE,
        }
    ]


# --- maintenance worker event flow -----------------------------------------


def test_v1_operation_never_posts_passphrase_event(monkeypatch) -> None:
    app = _app_stub()
    _run_worker_synchronously(app)
    _forbid_dialogs(monkeypatch)
    def callback(passphrase=None):
        return {"status": "PASS", "passphrase_seen": passphrase}
    VoiceStudioApp._start_backup_operation(app, "verify", callback)
    event, (action, result) = app.events.get_nowait()
    assert event == "backup_done"
    assert action == "verify"
    assert result["passphrase_seen"] is None
    assert app.busy_values == [True]


def test_v2_passphrase_required_posts_event_without_secret(monkeypatch) -> None:
    app = _app_stub()
    _run_worker_synchronously(app)
    _forbid_dialogs(monkeypatch)  # the worker must never touch Tk dialogs

    def callback(passphrase=None):
        raise ValueError(REQUIRED_ERROR)

    VoiceStudioApp._start_backup_operation(app, "verify", callback)
    event, (action, posted_callback) = app.events.get_nowait()
    assert event == "backup_passphrase_required"
    assert action == "verify"
    assert posted_callback is callback
    assert app.events.empty()  # no backup_error alongside


def test_wrong_passphrase_posts_backup_error_with_contract_message(monkeypatch) -> None:
    app = _app_stub()
    _run_worker_synchronously(app)

    def callback(passphrase=None):
        assert passphrase == WRONG
        raise ValueError(MANIFEST_ERROR)

    VoiceStudioApp._start_backup_operation(app, "verify", callback, passphrase=WRONG)
    event, (action, error) = app.events.get_nowait()
    assert event == "backup_error"
    assert action == "verify"
    assert str(error) == MANIFEST_ERROR
    assert WRONG not in str(error)


# --- main-thread prompt handler --------------------------------------------


def test_passphrase_handler_retries_operation_with_passphrase(monkeypatch) -> None:
    app = _app_stub()
    prompts, _errors = _dialogs(monkeypatch, [PASSPHRASE])
    app._start_backup_operation = lambda action, callback, passphrase=None: (
        app.started_operations.append((action, callback, passphrase))
    )
    callback = object()
    before = set(vars(app))
    VoiceStudioApp._handle_backup_passphrase_required(app, "verify", callback)
    assert [p["show"] for p in prompts] == ["*"]
    assert app.started_operations == [("verify", callback, PASSPHRASE)]
    # The passphrase is never stored on the app instance.
    assert set(vars(app)) == before  # no new attribute appeared
    leaked = [
        key
        for key in before
        if key not in {"started_operations", "status_values", "busy_values", "reloads"}
        and PASSPHRASE in str(vars(app)[key])
    ]
    assert leaked == []


def test_passphrase_handler_cancel_verify_stays_idle(monkeypatch) -> None:
    app = _app_stub()
    _dialogs(monkeypatch, [None])
    VoiceStudioApp._handle_backup_passphrase_required(app, "verify", object())
    assert app.started_operations == []
    assert app.reloads == []  # verify never closed the job controller
    assert app.status_values[-1] == translate("uk", "backup_cancelled")


def test_passphrase_handler_cancel_restore_restarts_runtime(monkeypatch) -> None:
    app = _app_stub()
    _dialogs(monkeypatch, [None])
    VoiceStudioApp._handle_backup_passphrase_required(app, "restore", object())
    assert app.started_operations == []
    assert app.reloads == ["reload"]  # the closed job controller is rebuilt
    assert app.status_values[-1] == translate("uk", "backup_cancelled")


def test_dialogs_are_called_only_from_the_main_thread(monkeypatch) -> None:
    app = _app_stub()
    prompts, _errors = _dialogs(monkeypatch, [PASSPHRASE, PASSPHRASE, PASSPHRASE])
    VoiceStudioApp._prompt_new_backup_passphrase(app, parent=None)
    VoiceStudioApp._handle_backup_passphrase_required(app, "verify", object())
    assert prompts
    assert all(p["thread"] is threading.main_thread() for p in prompts)


# Tripwire only: the event loop dispatches to the main-thread handler.
def test_poll_events_wires_passphrase_required_to_main_thread_handler() -> None:
    source = inspect.getsource(VoiceStudioApp._poll_events)
    assert "backup_passphrase_required" in source
    assert "_handle_backup_passphrase_required" in source


# --- restore queue callback -------------------------------------------------


def test_queue_restore_callback_accepts_passphrase(monkeypatch, tmp_path) -> None:
    app = _app_stub()
    app._confirm_editor_transition = lambda: True
    closed: list[str] = []
    app.job_controller = SimpleNamespace(close=lambda: closed.append("closed"))
    captured: list[dict] = []
    monkeypatch.setattr(
        app_module,
        "restore_backup",
        lambda *args, **kwargs: captured.append(kwargs) or {"status": "PASS"},
    )
    operations: list[tuple[str, object]] = []
    source = tmp_path / "enc.voice-backup"
    assert VoiceStudioApp._queue_restore(app, source, lambda a, cb: operations.append((a, cb)))
    assert closed == ["closed"]
    action, callback = operations[0]
    assert action == "restore"
    callback(PASSPHRASE)
    assert captured[0]["passphrase"] == PASSPHRASE


# --- startup interrupted recovery ------------------------------------------


def test_startup_recovery_without_journal_never_prompts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    app = _app_stub()
    _forbid_dialogs(monkeypatch)
    result = VoiceStudioApp._settle_interrupted_restore(app)
    assert result["action"] == "none"


def test_startup_recovery_correct_passphrase_completes(
    monkeypatch, tmp_path, make_wav
) -> None:
    data = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    app = _app_stub()
    prompts, _errors = _dialogs(monkeypatch, [PASSPHRASE])
    result = VoiceStudioApp._settle_interrupted_restore(app)
    assert result["status"] == "PASS"
    assert result["action"] == "settings_completed"
    assert len(prompts) == 1
    assert prompts[0]["show"] == "*"
    assert PASSPHRASE not in json.dumps(result)
    assert not (data / ".restore-settings-v2").exists()
    assert not backup_module.restore_journal_path(data).exists()


def test_startup_recovery_cancel_keeps_sidecar_and_journal(
    monkeypatch, tmp_path, make_wav
) -> None:
    data = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    app = _app_stub()
    _dialogs(monkeypatch, [None])
    result = VoiceStudioApp._settle_interrupted_restore(app)
    assert result["action"] == "passphrase_required"
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


def test_startup_recovery_wrong_passphrase_preserves_everything(
    monkeypatch, tmp_path, make_wav
) -> None:
    data = _crash_pending_v2(tmp_path, make_wav, monkeypatch)
    app = _app_stub()
    _dialogs(monkeypatch, [WRONG])
    result = VoiceStudioApp._settle_interrupted_restore(app)
    assert result["status"] == "FAIL"
    assert "authentication failed" in result["error"]
    assert WRONG not in json.dumps(result)
    assert (data / ".restore-settings-v2").is_dir()
    assert backup_module.restore_journal_path(data).exists()


def test_recovery_report_shows_localized_passphrase_required() -> None:
    app = _app_stub()
    app._restore_recovery = {
        "status": "PASS",
        "action": "passphrase_required",
        "records": 1,
        "recovery": None,
    }
    VoiceStudioApp._report_restore_recovery(app)
    assert app.status_values[-1] == translate("uk", "restore_passphrase_required")


# --- i18n -------------------------------------------------------------------


def test_new_backup_v2_keys_exist_in_every_locale() -> None:
    required = {
        "backup_encrypt",
        "backup_encrypt_warning",
        "backup_passphrase_enter",
        "backup_passphrase_repeat",
        "backup_passphrase_empty",
        "backup_passphrase_mismatch",
        "backup_passphrase_required",
        "restore_passphrase_required",
        "backup_cancelled",
    }
    for code, _label in UI_LANGUAGE_CHOICES:
        for key in required:
            assert translate(code, key), f"{code}:{key} missing"
