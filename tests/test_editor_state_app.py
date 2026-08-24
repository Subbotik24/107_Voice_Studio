from __future__ import annotations

import queue
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from hermes_voice_studio import app as app_module
from hermes_voice_studio.app import HermesVoiceApp
from hermes_voice_studio.editor_state import snapshot_editor
from hermes_voice_studio.models import Transcript


class FakeEditor:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.tags: dict[str, list[tuple[str, str]]] = {"bold": [], "italic": []}

    def get(self, _start: str, _end: str) -> str:
        return self.text

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _start: str, text: str) -> None:
        self.text = text

    def tag_ranges(self, tag: str) -> tuple[str, ...]:
        return tuple(value for pair in self.tags[tag] for value in pair)

    def tag_remove(self, tag: str, _start: str, _end: str) -> None:
        self.tags[tag] = []

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags[tag].append((start, end))


class FakeHistory:
    def __init__(self, selected: int = 0) -> None:
        self.selected = selected
        self.inserted: list[str] = []

    def curselection(self) -> tuple[int, ...]:
        return (self.selected,)

    def selection_clear(self, _start: int, _end: str) -> None:
        self.selected = -1

    def selection_set(self, index: int) -> None:
        self.selected = index

    def see(self, _index: int) -> None:
        return None

    def delete(self, _start: int, _end: str) -> None:
        self.inserted = []

    def insert(self, _where: str, value: str) -> None:
        self.inserted.append(value)


class FakeStore:
    def __init__(self, transcript: Transcript, *, fail: Exception | None = None) -> None:
        self.transcript = transcript
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    def update_corrected_text(self, transcript_id: str, text: str) -> Transcript:
        self.calls.append(("text", (transcript_id, text)))
        if self.fail:
            raise self.fail
        self.transcript.corrected_text = text
        return self.transcript

    def update_editor_formatting(
        self, transcript_id: str, formatting: dict[str, list[tuple[str, str]]]
    ) -> Transcript:
        self.calls.append(("formatting", (transcript_id, formatting)))
        if self.fail:
            raise self.fail
        self.transcript.metadata["editor_formatting"] = formatting
        return self.transcript


def _transcript(text: str = "original") -> Transcript:
    return Transcript(
        id="id-1",
        created_at="2026-01-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="faster-whisper",
        model="tiny",
        raw_text="raw immutable",
        corrected_text=text,
        metadata={"editor_formatting": {"bold": [("1.0", "1.4")]}},
    )


def _app(*, text: str = "original", fail: Exception | None = None) -> HermesVoiceApp:
    transcript = _transcript(text)
    app = object.__new__(HermesVoiceApp)
    app.current = transcript
    app.editor = FakeEditor(text)
    app.editor.tags["bold"] = [("1.0", "1.4")]
    app.raw_editor = object()
    app.details = object()
    app.store = FakeStore(transcript, fail=fail)
    app.status = SimpleNamespace(values=[], set=lambda value: app.status.values.append(value))
    app._editor_baseline = snapshot_editor(text, app.editor.tags)
    app.settings = SimpleNamespace(auto_copy=False)
    app._history_items = [transcript, _transcript("other")]
    app.history = FakeHistory(0)
    app.hotkey = SimpleNamespace(stop=lambda: setattr(app, "hotkey_stopped", True))
    app.recorder = SimpleNamespace(recording=False, cancel=lambda: None)
    app._cancel_event = SimpleNamespace(set=lambda: setattr(app, "cancelled", True))
    app.job_controller = SimpleNamespace(close=lambda: setattr(app, "controller_closed", True))
    app.destroy = lambda: setattr(app, "destroyed", True)
    return app


def test_editor_snapshot_detects_text_and_formatting_changes() -> None:
    baseline = snapshot_editor("text", {"bold": [("1.0", "1.4")]})
    assert snapshot_editor("text", {"bold": [("1.0", "1.4")]}) == baseline
    assert snapshot_editor("changed", {"bold": [("1.0", "1.4")]}) != baseline
    assert snapshot_editor("text", {"italic": [("1.0", "1.4")]}) != baseline


def test_editor_snapshot_is_immutable_and_normalizes_supported_ranges() -> None:
    source = {"italic": [("2.0", "2.4"), ("1.0", "1.2")], "unknown": [("x", "y")]}
    snapshot = snapshot_editor("text", source)
    source["italic"].append(("3.0", "3.4"))
    assert snapshot.formatting == (("bold", ()), ("italic", (("1.0", "1.2"), ("2.0", "2.4"))))
    with pytest.raises(FrozenInstanceError):
        snapshot.text = "changed"  # type: ignore[misc]


def test_dirty_transition_save_continues_only_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.editor.text = "edited"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    assert app._confirm_editor_transition() is True
    assert app.store.calls == [
        ("text", ("id-1", "edited")),
        ("formatting", ("id-1", {"bold": [("1.0", "1.4")], "italic": []})),
    ]
    assert app._editor_is_dirty() is False
    assert app.current.raw_text == "raw immutable"


def test_dirty_transition_discard_continues_without_store_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.editor.text = "edited"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: False)
    assert app._confirm_editor_transition() is True
    assert app.store.calls == []


def test_dirty_transition_cancel_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    app.editor.text = "edited"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    assert app._confirm_editor_transition() is False
    assert app.store.calls == []


def test_dirty_transition_save_error_aborts_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(fail=RuntimeError("disk full"))
    app.editor.text = "edited"
    errors: list[tuple[object, ...]] = []
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    assert app._confirm_editor_transition() is False
    assert errors and "disk full" in str(errors[0])


def test_close_cancel_keeps_controller_and_window_open(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    app.editor.text = "edited"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    app._close()
    for name in ("controller_closed", "destroyed", "cancelled"):
        with pytest.raises(AttributeError):
            object.__getattribute__(app, name)


def test_history_cancel_restores_current_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    app.editor.text = "edited"
    app.history.selected = 1
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    app._select_history()
    assert app.history.selected == 0


def test_background_result_cancel_refreshes_history(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    app.events = queue.Queue()
    app.events.put(("done", (_transcript("background"), None)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    refreshed: list[bool] = []
    app._refresh_history = lambda **_kwargs: refreshed.append(True)
    app._try_show_result = lambda *_args, **_kwargs: False
    app._poll_events()
    assert refreshed == [True]
