"""Contracts for the global status-bar progress indicator.

Covers ``_set_busy`` toggling the progress bar and the ``model_progress``
event switching it to a determinate percent, independent of the many other
things ``_set_busy``/``_poll_events`` also touch.
"""

from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

from voice_studio.app import VoiceStudioApp


class FakeButton:
    def __init__(self) -> None:
        self.state: str | None = None

    def configure(self, **kwargs: object) -> None:
        state = kwargs.get("state")
        if isinstance(state, str):
            self.state = state


class FakeStatus:
    def __init__(self) -> None:
        self.values: list[str] = []

    def set(self, value: str) -> None:
        self.values.append(value)


class FakeProgress:
    def __init__(self) -> None:
        self.visible = False
        self.start_calls: list[object] = []
        self.stop_calls = 0
        self.mode: str | None = None
        self.value: float | None = None
        self.maximum: float | None = None

    def pack(self, **_kwargs: object) -> None:
        self.visible = True

    def pack_forget(self) -> None:
        self.visible = False

    def configure(self, **kwargs: object) -> None:
        if "mode" in kwargs:
            self.mode = kwargs["mode"]
        if "value" in kwargs:
            self.value = kwargs["value"]
        if "maximum" in kwargs:
            self.maximum = kwargs["maximum"]

    def start(self, interval: object = None) -> None:
        self.start_calls.append(interval)

    def stop(self) -> None:
        self.stop_calls += 1


def _busy_app() -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    for name in (
        "file_button",
        "record_button",
        "continuous_record_button",
        "settings_button",
        "models_button",
        "backup_button",
        "rename_history_button",
        "delete_history_button",
        "cleanup_button",
        "undo_cleanup_button",
        "cancel_button",
    ):
        setattr(app, name, FakeButton())
    app.status_progress = FakeProgress()
    return app


# --- _set_busy ----------------------------------------------------------------


def test_set_busy_true_shows_and_starts_the_indeterminate_progress_bar() -> None:
    app = _busy_app()

    VoiceStudioApp._set_busy(app, True)

    assert app._busy is True
    assert app.status_progress.visible is True
    assert app.status_progress.mode == "indeterminate"
    assert app.status_progress.start_calls == [12]
    assert app.file_button.state == "disabled"
    assert app.cancel_button.state == "normal"


def test_set_busy_false_stops_and_hides_the_progress_bar() -> None:
    app = _busy_app()
    VoiceStudioApp._set_busy(app, True)

    VoiceStudioApp._set_busy(app, False)

    assert app._busy is False
    assert app.status_progress.stop_calls == 1
    assert app.status_progress.visible is False
    assert app.file_button.state == "normal"
    assert app.cancel_button.state == "disabled"


def test_set_busy_without_a_status_progress_widget_does_not_crash() -> None:
    """Catches a partially-built app (widgets not yet constructed) crashing here."""

    app = _busy_app()
    del app.status_progress

    VoiceStudioApp._set_busy(app, True)
    VoiceStudioApp._set_busy(app, False)


def test_a_new_busy_phase_resets_a_determinate_bar_back_to_indeterminate() -> None:
    """Catches a model-download percent bar that stays determinate for the next job."""

    app = _busy_app()
    VoiceStudioApp._set_busy(app, True)
    app.status_progress.configure(mode="determinate", value=42)

    VoiceStudioApp._set_busy(app, False)
    VoiceStudioApp._set_busy(app, True)

    assert app.status_progress.mode == "indeterminate"


# --- model_progress event ------------------------------------------------------


def _progress_event_app() -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = SimpleNamespace(ui_language="en")
    app.events = queue.Queue()
    app._shutdown_event = threading.Event()
    app.status = FakeStatus()
    app.status_progress = FakeProgress()
    app.after = lambda *_args, **_kwargs: None
    return app


def test_model_progress_event_switches_the_bar_to_a_determinate_percent() -> None:
    app = _progress_event_app()
    app.events.put(("model_progress", (50, 200)))

    VoiceStudioApp._poll_events(app)

    assert app.status_progress.mode == "determinate"
    assert app.status_progress.value == 25
    assert app.status_progress.maximum == 100


def test_model_progress_event_without_a_status_progress_widget_does_not_crash() -> None:
    app = _progress_event_app()
    del app.status_progress
    app.events.put(("model_progress", (1, 10)))

    VoiceStudioApp._poll_events(app)
