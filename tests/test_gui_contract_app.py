from __future__ import annotations

import inspect

from hermes_voice_studio.app import HermesVoiceApp


def test_gui_exposes_async_backup_and_reversible_restore() -> None:
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


def test_gui_exposes_history_actions_continuous_recording_and_hotkey_capture() -> None:
    build_ui = inspect.getsource(HermesVoiceApp._build_ui)
    settings_dialog = inspect.getsource(HermesVoiceApp._settings_dialog)

    assert "Постійний запис" in build_ui
    assert "Перейменувати" in build_ui
    assert "Видалити" in build_ui
    assert "Запам'ятати клавішу" in settings_dialog
    assert "hotkey_from_tk_event" in settings_dialog


def test_gui_editor_guarantees_new_lines_and_exposes_basic_formatting() -> None:
    build_ui = inspect.getsource(HermesVoiceApp._build_ui)

    assert 'self.editor.bind("<Return>"' in build_ui
    assert 'self.editor.bind("<Control-Return>"' in build_ui
    assert 'text="B"' in build_ui
    assert 'text="I"' in build_ui


def test_settings_dialog_destroys_tk_window_before_restarting_global_hotkey() -> None:
    close_helper = inspect.getsource(HermesVoiceApp._close_settings_dialog)

    assert close_helper.index("dialog.destroy()") < close_helper.index(
        "self.after_idle(self._start_hotkey)"
    )
