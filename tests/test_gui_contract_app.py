"""Contracts for the Tk GUI layer.

Two kinds of test live here, and the difference matters when reading a green
run.

The first kind exercises real behaviour: the method is called against fakes and
the observable effect is asserted. Prefer this always.

The second kind asserts on the *source* of `_build_ui` and the dialog builders.
Those methods do nothing but construct Tk widgets, so there is no observable
effect to assert without a display and a real widget tree, and a headless
process cannot build one. These tests therefore prove only that a widget is
declared — not that it is packed, bound, or reachable by a user. They are a
tripwire against silent removal, not evidence that the GUI works. The physical
Windows and macOS acceptance checklist in `docs/PROJECT_AUDIT_STATUS.md` is what
covers that, and it is still NOT RUN.

Do not add new source-substring tests for anything that can be called. Editor
transition guards, for example, were once asserted here as strings and are now
covered properly in `tests/test_editor_state_app.py`.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from hermes_voice_studio.app import HermesVoiceApp

# --- behavioural ------------------------------------------------------------


def test_settings_dialog_destroys_tk_window_before_restarting_global_hotkey() -> None:
    """The native listener must start only after Tk has released the dialog.

    Restarting the global hotkey while the modal still holds the grab lets the
    old shortcut fire into a half-torn-down dialog.
    """

    app = object.__new__(HermesVoiceApp)
    order: list[str] = []
    scheduled: list[object] = []
    dialog = SimpleNamespace(
        grab_release=lambda: order.append("grab_release"),
        destroy=lambda: order.append("destroy"),
    )
    app.after_idle = lambda callback: (
        order.append("after_idle"),
        scheduled.append(callback),
    )

    HermesVoiceApp._close_settings_dialog(app, dialog)

    assert order == ["grab_release", "destroy", "after_idle"]
    assert scheduled == [app._start_hotkey], "the real hotkey starter must be the deferred call"


# --- static widget-construction tripwires -----------------------------------
# See the module docstring: these prove declaration, not behaviour.


def test_backup_ui_is_declared_with_async_work_and_reversible_restore() -> None:
    build_ui = inspect.getsource(HermesVoiceApp._build_ui)
    backup_dialog = inspect.getsource(HermesVoiceApp._backup_dialog)
    event_handler = inspect.getsource(HermesVoiceApp._poll_events)

    assert "Резервна копія" in build_ui
    assert "threading.Thread" in backup_dialog
    assert "create_backup" in backup_dialog
    assert "verify_backup" in backup_dialog
    assert "restore_backup" in backup_dialog
    assert "recovery directory" in backup_dialog
    assert 'event == "backup_done"' in event_handler


def test_history_actions_continuous_recording_and_hotkey_capture_are_declared() -> None:
    build_ui = inspect.getsource(HermesVoiceApp._build_ui)
    settings_dialog = inspect.getsource(HermesVoiceApp._settings_dialog)

    assert "Постійний запис" in build_ui
    assert "Перейменувати" in build_ui
    assert "Видалити" in build_ui
    assert "Запам'ятати клавішу" in settings_dialog
    assert "hotkey_from_tk_event" in settings_dialog


def test_editor_newline_bindings_and_basic_formatting_are_declared() -> None:
    build_ui = inspect.getsource(HermesVoiceApp._build_ui)

    assert 'self.editor.bind("<Return>"' in build_ui
    assert 'self.editor.bind("<Control-Return>"' in build_ui
    assert 'text="B"' in build_ui
    assert 'text="I"' in build_ui
