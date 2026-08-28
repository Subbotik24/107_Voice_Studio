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
Windows and macOS acceptance scope recorded in `VERIFICATION.md` is what
covers that, and it is still NOT RUN.

Do not add new source-substring tests for anything that can be called. Editor
transition guards, for example, were once asserted here as strings and are now
covered properly in `tests/test_editor_state_app.py`.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from voice_studio.app import (
    VOICE_STUDIO_THEME,
    StudioLayout,
    VoiceStudioApp,
    studio_icon_pixel,
    studio_layout_for_width,
)

# --- behavioural ------------------------------------------------------------


def test_studio_layout_matches_the_approved_sidebar_breakpoint() -> None:
    assert studio_layout_for_width(1320) == StudioLayout(250, False, True)
    assert studio_layout_for_width(1200) == StudioLayout(250, False, True)
    assert studio_layout_for_width(1199) == StudioLayout(250, False, False)
    assert studio_layout_for_width(1040) == StudioLayout(250, False, False)
    assert studio_layout_for_width(1039) == StudioLayout(88, True, False)


def test_voice_studio_theme_matches_approved_cream_reference() -> None:
    assert VOICE_STUDIO_THEME.canvas == "#f6eddc"
    assert VOICE_STUDIO_THEME.surface == "#fffaf1"
    assert VOICE_STUDIO_THEME.accent == "#e99016"
    assert VOICE_STUDIO_THEME.ink == "#2a2119"
    assert VOICE_STUDIO_THEME.primary == "#5b4332"
    assert VOICE_STUDIO_THEME.ui_font == "Bahnschrift"
    assert VOICE_STUDIO_THEME.mono_font == "Cascadia Mono"


def test_configured_theme_consumes_the_approved_theme_contract() -> None:
    source = inspect.getsource(VoiceStudioApp._configure_theme)

    assert "VOICE_STUDIO_THEME" in source
    assert 'background="#172641"' not in source
    assert 'background="#315eae"' not in source


def test_brand_mark_uses_the_approved_rounded_vo_canvas() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert "self.brand_mark = tk.Canvas" in source
    assert "self.brand_mark.create_polygon" in source
    assert 'text="VO"' in source


def test_workspace_subtitle_wraps_beside_the_file_action() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert "wraplength=560" in source


def test_full_sidebar_uses_reference_bullets_and_keeps_compact_glyphs() -> None:
    source = inspect.getsource(VoiceStudioApp._apply_studio_layout)

    assert 'symbol if layout.compact_sidebar else f"●  {self._t(key)}"' in source


def test_studio_icon_mask_forms_a_rounded_accent_square() -> None:
    assert studio_icon_pixel(0, 0) is False
    assert studio_icon_pixel(8, 0) is True
    assert studio_icon_pixel(16, 16) is True
    assert studio_icon_pixel(31, 31) is False


def test_settings_dialog_destroys_tk_window_before_restarting_global_hotkey() -> None:
    """The native listener must start only after Tk has released the dialog.

    Restarting the global hotkey while the modal still holds the grab lets the
    old shortcut fire into a half-torn-down dialog.
    """

    app = object.__new__(VoiceStudioApp)
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

    VoiceStudioApp._close_settings_dialog(app, dialog)

    assert order == ["grab_release", "destroy", "after_idle"]
    assert scheduled == [app._start_hotkey], "the real hotkey starter must be the deferred call"


def test_close_is_blocked_while_backup_or_restore_is_running(monkeypatch) -> None:
    app = object.__new__(VoiceStudioApp)
    app._confirm_editor_transition = lambda: True
    app._maintenance_thread = SimpleNamespace(is_alive=lambda: True)
    app._t = lambda _key: "Резервна копія виконується"
    app.destroy = lambda: pytest.fail("the window must stay alive during maintenance")
    warnings: list[str] = []
    monkeypatch.setattr(
        "voice_studio.app.messagebox.showwarning",
        lambda _title, message, **_kwargs: warnings.append(message),
    )

    VoiceStudioApp._close(app)

    assert warnings and "резерв" in warnings[0].lower()


# --- static widget-construction tripwires -----------------------------------
# See the module docstring: these prove declaration, not behaviour.


def test_backup_ui_is_declared_with_async_work_and_reversible_restore() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    backup_dialog = inspect.getsource(VoiceStudioApp._backup_dialog)
    event_handler = inspect.getsource(VoiceStudioApp._poll_events)

    assert 'self._t("backup")' in build_ui
    assert "threading.Thread" in backup_dialog
    assert "create_backup" in backup_dialog
    assert "verify_backup" in backup_dialog
    assert "restore_backup" in backup_dialog
    assert 'self._t("restore_backup_prompt")' in backup_dialog
    assert 'event == "backup_done"' in event_handler


def test_history_actions_continuous_recording_and_hotkey_capture_are_declared() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    settings_dialog = inspect.getsource(VoiceStudioApp._settings_dialog)

    assert 'self._t("continuous_record")' in build_ui
    assert 'self._t("rename")' in build_ui
    assert 'self._t("delete")' in build_ui
    assert 'self._t("capture_hotkey")' in settings_dialog
    assert "hotkey_from_tk_event" in settings_dialog


def test_editor_newline_bindings_and_basic_formatting_are_declared() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)

    assert 'self.editor.bind("<Return>"' in build_ui
    assert 'self.editor.bind("<Control-Return>"' in build_ui
    assert 'text="B"' in build_ui
    assert 'text="I"' in build_ui
