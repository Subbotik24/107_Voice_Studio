from types import SimpleNamespace
from unittest.mock import Mock

from voice_studio.hotkey import (
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


class _FakeListener:
    def __init__(self, *, remains_alive: bool) -> None:
        self.remains_alive = remains_alive
        self.stop_calls = 0
        self.join_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self._alive = True

    def stop(self) -> None:
        self.stop_calls += 1

    def is_alive(self) -> bool:
        return self._alive

    def join(self, *args: object, **kwargs: object) -> None:
        self.join_calls.append((args, kwargs))
        if not self.remains_alive:
            self._alive = False


def test_stop_clears_listener_after_one_bounded_join() -> None:
    listener = _FakeListener(remains_alive=False)
    hotkey = GlobalHotkey("<f13>", Mock(), Mock())
    hotkey._listener = listener
    hotkey._hotkey = object()
    hotkey._active = True

    assert hotkey.stop() is True

    assert listener.stop_calls == 1
    assert listener.join_calls == [((), {"timeout": 1})]
    assert hotkey._listener is None
    assert hotkey._hotkey is None
    assert hotkey._active is False


def test_stop_retains_stubborn_listener_for_retry_and_clears_callbacks() -> None:
    listener = _FakeListener(remains_alive=True)
    hotkey = GlobalHotkey("<f13>", Mock(), Mock())
    hotkey._listener = listener
    hotkey._hotkey = object()
    hotkey._active = True

    assert hotkey.stop() is False
    assert hotkey._listener is listener
    assert hotkey._hotkey is None
    assert hotkey._active is False
    assert listener.stop_calls == 1
    assert listener.join_calls == [((), {"timeout": 1})]

    assert hotkey.stop() is False
    assert hotkey._listener is listener
    assert listener.stop_calls == 2
    assert listener.join_calls == [((), {"timeout": 1}), ((), {"timeout": 1})]
