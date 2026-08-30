from __future__ import annotations

from collections.abc import Callable
from typing import Any


def hotkey_from_tk_event(event: Any) -> str | None:
    """Convert one Tk key press into the syntax accepted by ``pynput.HotKey``.

    The settings dialog captures keys only while it is open.  A modifier by
    itself is not a usable push-to-talk shortcut, so capture waits for the
    final key of the combination.
    """

    keysym = str(event.keysym)
    modifier_keys = {
        "Control_L",
        "Control_R",
        "Alt_L",
        "Alt_R",
        "Option_L",
        "Option_R",
        "Shift_L",
        "Shift_R",
        "Command",
        "Command_L",
        "Command_R",
        "Meta_L",
        "Meta_R",
    }
    if keysym in modifier_keys:
        return None

    state = int(getattr(event, "state", 0))
    modifiers: list[str] = []
    if state & 0x0004:
        modifiers.append("<ctrl>")
    if state & 0x0008:
        modifiers.append("<alt>")
    if state & 0x0001:
        modifiers.append("<shift>")
    # Tk reports Command/Meta differently across macOS Tk builds.  This mask
    # covers the common Aqua mapping and remains harmless elsewhere.
    if state & 0x0010:
        modifiers.append("<cmd>")

    special = {"space": "<space>", "Return": "<enter>", "Escape": "<esc>", "Tab": "<tab>"}
    key = special.get(keysym, f"<{keysym.lower()}>")
    return "+".join([*modifiers, key])


class GlobalHotkey:
    def __init__(
        self,
        combination: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ):
        self.combination = combination
        self.on_press = on_press
        self.on_release = on_release
        self._listener: Any | None = None
        self._hotkey: Any | None = None
        self._keys: list[Any] = []
        self._active = False

    def start(self) -> None:
        from pynput import keyboard

        self._keys = keyboard.HotKey.parse(self.combination)

        self._hotkey = keyboard.HotKey(self._keys, lambda: None)
        self._listener = keyboard.Listener(
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self._listener.start()

    def _active_now(self) -> bool:
        return bool(self._hotkey) and all(key in self._hotkey._state for key in self._keys)

    def _handle_press(self, key: Any) -> None:
        # macOS can deliver an already queued event while Listener.stop() is
        # tearing down its native event tap.  Do not dereference cleared state.
        listener = self._listener
        hotkey = self._hotkey
        if listener is None or hotkey is None:
            return
        canonical = listener.canonical(key)
        was_active = self._active
        hotkey.press(canonical)
        self._active = self._active_now()
        if self._active and not was_active:
            self.on_press()

    def _handle_release(self, key: Any) -> None:
        listener = self._listener
        hotkey = self._hotkey
        if listener is None or hotkey is None:
            return
        canonical = listener.canonical(key)
        was_active = self._active
        hotkey.release(canonical)
        self._active = self._active_now()
        if was_active and not self._active:
            self.on_release()

    def stop(self) -> bool:
        listener = self._listener
        self._hotkey = None
        self._active = False
        if listener is None:
            return True

        listener.stop()
        if listener.is_alive():
            listener.join(timeout=1)

        if listener.is_alive():
            self._listener = listener
            return False

        self._listener = None
        return True
