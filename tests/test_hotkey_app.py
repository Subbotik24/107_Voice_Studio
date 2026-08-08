from types import SimpleNamespace
from unittest.mock import Mock

from hermes_voice_studio.hotkey import (
    GlobalHotkey,
    hotkey_from_tk_event,
)


def test_hotkey_capture_formats_tk_modifier_state_and_space() -> None:
    event = SimpleNamespace(keysym="space", state=0x0004 | 0x0008)
    assert hotkey_from_tk_event(event) == "<ctrl>+<alt>+<space>"


def test_hotkey_capture_ignores_modifier_only_and_supports_function_key() -> None:
    assert hotkey_from_tk_event(SimpleNamespace(keysym="Control_L", state=0x0004)) is None
    assert hotkey_from_tk_event(SimpleNamespace(keysym="F8", state=0)) == "<f8>"


def test_late_listener_event_is_ignored_after_stop() -> None:
    hotkey = GlobalHotkey("<f13>", Mock(), Mock())
    hotkey._listener = None
    hotkey._hotkey = None

    # The callback guard must make a late native keyboard event harmless.
    hotkey._handle_press(Mock())
    hotkey._handle_release(Mock())
