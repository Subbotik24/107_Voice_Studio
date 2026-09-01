from __future__ import annotations

import queue
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.editor_state import snapshot_editor
from voice_studio.models import Segment, Transcript
from voice_studio.storage import LocalStore


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
        self._selection = {selected} if selected >= 0 else set()
        self.selection_events: list[object] = []
        self.inserted: list[str] = []

    @property
    def selected(self) -> int:
        return min(self._selection, default=-1)

    @selected.setter
    def selected(self, value: int) -> None:
        self._selection = {value} if value >= 0 else set()

    def curselection(self) -> tuple[int, ...]:
        return tuple(sorted(self._selection))

    def selection_clear(self, _start: int, _end: str) -> None:
        self.selection_events.append("clear")
        self._selection.clear()

    def selection_set(self, index: int) -> None:
        self.selection_events.append(("set", index))
        self._selection.add(index)

    def see(self, _index: int) -> None:
        return None

    def delete(self, _start: int, _end: str) -> None:
        self.inserted = []
        self._selection.clear()

    def insert(self, _where: str, value: str) -> None:
        self.inserted.append(value)


class FakeStore:
    def __init__(self, transcript: Transcript, *, fail: Exception | None = None) -> None:
        self.transcript = transcript
        self.fail = fail
        self.calls: list[tuple[str, object]] = []
        self.history = [transcript]
        self.cleanup_result: Transcript | None = None
        self.order: list[str] = []

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

    def update_editor_state(
        self,
        transcript_id: str,
        text: str,
        formatting: dict[str, list[tuple[str, str]]],
    ) -> Transcript:
        self.calls.append(("state", (transcript_id, text, formatting)))
        if self.fail:
            raise self.fail
        self.transcript.corrected_text = text
        self.transcript.metadata["editor_formatting"] = formatting
        return self.transcript

    def apply_ai_cleanup(self, *args: object, **kwargs: object) -> Transcript:
        self.calls.append(("cleanup", (args, kwargs)))
        self.order.append("apply")
        if self.cleanup_result is not None:
            self.transcript = self.cleanup_result
        return self.transcript

    def list(self, **_kwargs: object) -> list[Transcript]:
        return list(self.history)

    def delete(self, transcript_id: str, *, delete_audio: bool = False) -> None:
        self.calls.append(("delete", (transcript_id, delete_audio)))


class FakeReadonly:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **_kwargs: object) -> None:
        return None

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _start: str, text: str) -> None:
        self.text = text


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


def _app(*, text: str = "original", fail: Exception | None = None) -> VoiceStudioApp:
    transcript = _transcript(text)
    app = object.__new__(VoiceStudioApp)
    app.current = transcript
    app.editor = FakeEditor(text)
    app.editor.tags["bold"] = [("1.0", "1.4")]
    app.raw_editor = FakeReadonly()
    app.details = FakeReadonly()
    app.store = FakeStore(transcript, fail=fail)
    app.status = SimpleNamespace(values=[], set=lambda value: app.status.values.append(value))
    app._editor_baseline = snapshot_editor(text, app.editor.tags)
    app.settings = SimpleNamespace(
        auto_copy=False, openai_cleanup_model="test-model", ui_language="uk"
    )
    app._current_page = "dashboard"
    app.confidence_panel_visible = False
    app._history_items = [transcript, _transcript("other")]
    app.history = FakeHistory(0)
    app.search_var = SimpleNamespace(get=lambda: "")
    app.hotkey = SimpleNamespace(stop=lambda: setattr(app, "hotkey_stopped", True))
    app.recorder = SimpleNamespace(recording=False, cancel=lambda: None)
    app._cancel_event = SimpleNamespace(set=lambda: setattr(app, "cancelled", True))
    app.job_controller = SimpleNamespace(close=lambda: setattr(app, "controller_closed", True))
    app.destroy = lambda: setattr(app, "destroyed", True)
    app.after = lambda *_args: None
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


@pytest.mark.parametrize(
    "invalid_range",
    [
        (1, "1.4"),
        ("1.0", 4),
        ("1.0", "end"),
        ("0.0", "1.4"),
        ("1.0", "not-a-tk-index"),
        ("1.0", "1.04 chars"),
    ],
)
def test_editor_snapshot_ignores_noncanonical_tk_indices(
    invalid_range: tuple[object, object],
) -> None:
    """Catches coercing arbitrary two-item values into persisted formatting."""

    snapshot = snapshot_editor(
        "text", {"bold": [("1.0", "1.4"), invalid_range]}  # type: ignore[list-item]
    )

    assert snapshot.formatting == (("bold", (("1.0", "1.4"),)), ("italic", ()))


def test_dirty_transition_save_continues_only_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.editor.text = "edited"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    assert app._confirm_editor_transition() is True
    assert app.store.calls == [
        ("state", ("id-1", "edited", {"bold": [("1.0", "1.4")], "italic": []}))
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
    assert app.history.curselection() == (0,)
    assert app.history.selection_events.index("clear") < app.history.selection_events.index(
        ("set", 0)
    )


def test_history_cancel_clears_selection_when_current_is_not_in_filtered_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.editor.text = "edited"
    other = _transcript("other")
    other.id = "id-2"
    app._history_items = [other]
    app.history.selected = 0
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    app._select_history()
    assert app.history.curselection() == ()
    assert app.history.selection_events == ["clear"]


def test_delete_selected_history_stops_playback_and_clears_the_current_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches an inline editor reset that skips stopping playback or clearing cleanup state."""

    app = _app()
    app.current.audio_retained = False
    app.history.selected = 0
    app._confirm_editor_transition = lambda: True
    app._refresh_history = lambda: None
    app._refresh_dashboard = lambda: None
    app._cleanup_snapshot = snapshot_editor("stale", app.editor.tags)
    app._cleanup_transcript_id = app.current.id
    calls: list[object] = []
    app._stop_playback = lambda: calls.append("stop_playback")
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)

    app._delete_selected_history()

    assert calls == ["stop_playback"]
    assert app.store.calls == [("delete", ("id-1", False))]
    assert app.current is None
    assert app.editor.text == ""
    assert app.editor.tags == {"bold": [], "italic": []}
    assert app.raw_editor.text == ""
    assert app.details.text == ""
    assert app._cleanup_snapshot is None
    assert app._cleanup_transcript_id is None


def test_current_none_draft_is_dirty_and_save_cancel_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.current = None
    app.editor.text = "unsaveable draft"
    errors: list[tuple[object, ...]] = []
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    assert app._editor_is_dirty() is True
    assert app._confirm_editor_transition() is False
    assert errors and "транскрипт" in str(errors[0]).lower()
    assert app.editor.text == "unsaveable draft"


def test_close_current_none_draft_stays_open_when_save_cannot_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.current = None
    app.editor.text = "unsaveable draft"
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    app._close()
    with pytest.raises(AttributeError):
        object.__getattribute__(app, "destroyed")


def test_result_current_none_draft_is_not_replaced_when_save_cannot_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.current = None
    app.editor.text = "unsaveable draft"
    replacement = _transcript("replacement")
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    assert app._try_show_result(replacement) is False
    assert app.current is None
    assert app.editor.text == "unsaveable draft"


def test_show_result_captures_baseline_after_editor_load() -> None:
    app = _app()
    replacement = _transcript("replacement")
    app._show_result(replacement, refresh=False)
    assert app.current is replacement
    assert app.editor.text == "replacement"
    assert app._editor_is_dirty() is False
    assert app.raw_editor.text == "raw immutable"


def test_cleanup_cancel_does_not_apply_durable_stale_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.editor.text = "edited while cleanup ran"
    app._cleanup_snapshot = snapshot_editor("original", app.editor.tags)
    proposal = SimpleNamespace(corrected_text="cleanup", to_dict=lambda: {"segments": []})
    app.events = queue.Queue()
    app.events.put(("cleanup_proposal", (app.current, proposal)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: None)
    app._poll_events()
    assert not [call for call in app.store.calls if call[0] == "cleanup"]
    assert app.current.corrected_text == "original"
    assert app.current.raw_text == "raw immutable"


def test_cleanup_rejects_when_current_transcript_id_changes_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    target = _transcript("original")
    target.id = "id-1"
    app._cleanup_snapshot = snapshot_editor("original", app.editor.tags)
    app._cleanup_transcript_id = "id-1"
    app.current.id = "id-2"
    proposal = SimpleNamespace(corrected_text="cleanup", to_dict=lambda: {"segments": []})
    app.events = queue.Queue()
    app.events.put(("cleanup_proposal", (target, proposal)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)
    app._confirm_editor_transition = lambda: True
    app._poll_events()
    assert not [call for call in app.store.calls if call[0] == "cleanup"]
    assert app.current.id == "id-2"


def test_cleanup_rejects_when_editor_snapshot_changes_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    target = _transcript("original")
    app._cleanup_snapshot = snapshot_editor("original", app.editor.tags)
    app._cleanup_transcript_id = target.id
    app.editor.text = "edited during cleanup"
    proposal = SimpleNamespace(corrected_text="cleanup", to_dict=lambda: {"segments": []})
    app.events = queue.Queue()
    app.events.put(("cleanup_proposal", (target, proposal)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)
    app._confirm_editor_transition = lambda: True
    app._poll_events()
    assert not [call for call in app.store.calls if call[0] == "cleanup"]
    assert app.editor.text == "edited during cleanup"


def test_cleanup_success_guards_before_durable_apply_then_displays_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    target = app.current
    result = _transcript("cleanup result")
    result.id = target.id
    app.store.cleanup_result = result
    app.store.order = []
    app.store.history = [target, result]
    app._cleanup_snapshot = snapshot_editor("original", app.editor.tags)
    app._cleanup_transcript_id = target.id
    proposal = SimpleNamespace(corrected_text="cleanup", to_dict=lambda: {"segments": []})
    app.events = queue.Queue()
    app.events.put(("cleanup_proposal", (target, proposal)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)
    app._confirm_editor_transition = lambda: app.store.order.append("guard") or True
    original_snapshot_check = app._cleanup_result_is_current

    def record_snapshot_check(transcript: Transcript) -> bool:
        app.store.order.append("snapshot")
        return original_snapshot_check(transcript)

    app._cleanup_result_is_current = record_snapshot_check
    app._poll_events()
    assert app.store.order == ["guard", "snapshot", "apply"]
    assert app.store.calls[-1][0] == "cleanup"
    assert app.store.transcript is result
    assert app.current is result
    assert app.editor.text == "cleanup result"


def test_automatic_cleanup_failure_is_reported_without_hiding_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    transcript = _transcript("dictionary result")
    transcript.metadata.update(
        {
            "automatic_cleanup": "failed",
            "cleanup_warning": "Ollama did not return a cleanup proposal",
        }
    )
    app._t = lambda key, **values: f"{key}:{values.get('error', '')}"
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showwarning",
        lambda *args, **_kwargs: warnings.append(args),
    )

    assert app._report_automatic_cleanup_warning(transcript) is True

    assert app.status.values[-1].startswith("cleanup_automatic_failed")
    assert "Ollama did not return" in str(warnings[-1])
    assert transcript.corrected_text == "dictionary result"
    assert transcript.raw_text == "raw immutable"


def test_cleanup_real_store_persists_valid_proposal_and_displays_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalStore(tmp_path)
    old = _transcript("original")
    old.segments = [Segment(0.0, 1.0, "raw segment")]
    store.save(old)
    app = _app()
    app.current = store.get("id-1")
    app.editor = FakeEditor("original")
    app.editor.tags["bold"] = [("1.0", "1.4")]
    app._editor_baseline = snapshot_editor("original", app.editor.tags)
    app.store = store
    app._cleanup_snapshot = app._editor_baseline
    app._cleanup_transcript_id = old.id
    proposal = SimpleNamespace(
        corrected_text="cleaned",
        to_dict=lambda: {
            "corrected_text": "cleaned",
            "segments": [{"segment_index": 0, "corrected_text": "cleaned segment"}],
        },
    )
    app.events = queue.Queue()
    app.events.put(("cleanup_proposal", (old, proposal)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    order: list[str] = []
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: True)
    app._confirm_editor_transition = lambda: order.append("guard") or True
    original_snapshot_check = app._cleanup_result_is_current

    def record_snapshot_check(transcript: Transcript) -> bool:
        order.append("snapshot")
        return original_snapshot_check(transcript)

    app._cleanup_result_is_current = record_snapshot_check
    app._poll_events()
    persisted = store.get(old.id)
    assert order == ["guard", "snapshot"]
    assert persisted.corrected_text == "cleaned"
    assert persisted.segments[0].corrected_text == "cleaned segment"
    assert persisted.metadata["last_ai_cleanup"] == {
        "provider": "openai",
        "model": "test-model",
    }
    assert persisted.metadata["ai_cleanup_history"][0]["corrected_text"] == "original"
    assert persisted.raw_text == "raw immutable"
    assert app.current.corrected_text == "cleaned"
    assert app.editor.text == "cleaned"
    assert app.raw_editor.text == "raw immutable"


def test_background_cancel_refreshes_real_store_then_selects_completed_row(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalStore(tmp_path)
    old = _transcript("original")
    completed = _transcript("completed")
    completed.id = "id-2"
    completed.source_name = "completed.wav"
    store.save(old)
    store.save(completed)
    app = _app()
    app.store = store
    app.current = store.get(old.id)
    app.editor = FakeEditor("edited while background ran")
    app.editor.tags["bold"] = [("1.0", "1.4")]
    app._editor_baseline = snapshot_editor("original", app.editor.tags)
    result = store.get(completed.id)
    app.events = queue.Queue()
    app.events.put(("done", (result, None)))
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    app._confirm_editor_transition = lambda: False
    app._poll_events()
    assert app.current.id == old.id
    assert app.editor.text == "edited while background ran"
    completed_index = next(
        index for index, item in enumerate(app._history_items) if item.id == completed.id
    )
    app.history.selection_clear(0, "end")
    app.history.selection_set(completed_index)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *args, **kwargs: False)
    app._confirm_editor_transition = VoiceStudioApp._confirm_editor_transition.__get__(app)
    app._select_history()
    assert app.current.id == completed.id
    assert app.editor.text == "completed"
    assert app.history.curselection() == (completed_index,)


def test_background_result_cancel_refreshes_history(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    app.editor.text = "edited"
    app._set_busy = lambda _value: None
    app.after = lambda *_args: None
    result = _transcript("background")
    result.id = "id-2"
    app.store.history = [app.current, result]
    app.events = queue.Queue()
    app.events.put(("done", (result, None)))
    app._confirm_editor_transition = lambda: False
    app._poll_events()
    assert app.current.id == "id-1"
    assert app.editor.text == "edited"
    assert result in app._history_items
    assert result.source_name in app.history.inserted[-1]
    assert app.history.curselection() == (0,)


def test_restore_does_not_start_when_unsaved_editor_transition_is_cancelled() -> None:
    app = _app()
    app._confirm_editor_transition = lambda: False
    operations: list[tuple[str, object]] = []

    started = app._queue_restore(
        app_module.Path("restore.voice-backup"),
        lambda action, callback: operations.append((action, callback)),
    )

    assert started is False
    assert operations == []
    assert "controller_closed" not in app.__dict__


def test_restore_start_closes_runtime_only_after_editor_transition_is_resolved() -> None:
    app = _app()
    app._confirm_editor_transition = lambda: True
    operations: list[tuple[str, object]] = []

    started = app._queue_restore(
        app_module.Path("restore.voice-backup"),
        lambda action, callback: operations.append((action, callback)),
    )

    assert started is True
    assert app.controller_closed is True
    assert operations and operations[0][0] == "restore"
    assert callable(operations[0][1])


def test_restore_reload_clears_stale_transcript_and_editor_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(text="stale pre-restore edit")
    app._cleanup_snapshot = snapshot_editor("stale pre-restore edit", app.editor.tags)
    app._cleanup_transcript_id = app.current.id
    calls: list[str] = []
    restored_settings = SimpleNamespace(ui_language="uk")
    monkeypatch.setattr(app_module, "load_settings", lambda: restored_settings)
    app._restart_runtime = lambda: calls.append("runtime")
    app._refresh_history = lambda: calls.append("history")
    app._refresh_ui_text = lambda: calls.append("ui")
    app._start_hotkey = lambda: calls.append("hotkey")

    app._reload_after_restore()

    assert app.settings is restored_settings
    assert app.current is None
    assert app.editor.text == ""
    assert app.editor.tags == {"bold": [], "italic": []}
    assert app.raw_editor.text == ""
    assert app.details.text == ""
    assert app._cleanup_snapshot is None
    assert app._cleanup_transcript_id is None
    assert app._editor_is_dirty() is False
    assert calls == ["runtime", "history", "ui", "hotkey"]


def test_restore_reload_rebuilds_the_settings_page_instead_of_the_hotkey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restore while parked on Settings must not restart the hotkey behind a stale page."""

    app = _app()
    app._current_page = "settings"
    calls: list[str] = []
    restored_settings = SimpleNamespace(ui_language="uk")
    monkeypatch.setattr(app_module, "load_settings", lambda: restored_settings)
    app._restart_runtime = lambda: calls.append("runtime")
    app._refresh_history = lambda: calls.append("history")
    app._refresh_ui_text = lambda: calls.append("ui")
    app._start_hotkey = lambda: calls.append("hotkey")
    app._build_settings_page = lambda: calls.append("settings_page")

    app._reload_after_restore()

    assert "hotkey" not in calls
    assert "settings_page" in calls


def test_restore_reload_resets_the_help_page_when_ui_language_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.settings.ui_language = "uk"
    calls: list[str] = []
    restored_settings = SimpleNamespace(ui_language="cs")
    monkeypatch.setattr(app_module, "load_settings", lambda: restored_settings)
    app._restart_runtime = lambda: calls.append("runtime")
    app._refresh_history = lambda: calls.append("history")
    app._refresh_ui_text = lambda: calls.append("ui")
    app._start_hotkey = lambda: calls.append("hotkey")
    app._reset_help_page = lambda: calls.append("reset_help")

    app._reload_after_restore()

    assert calls.index("reset_help") < calls.index("ui")


@pytest.mark.parametrize(
    ("event", "value"),
    [
        (
            "backup_done",
            ("restore", {"records": 1, "recovery": "recovery-directory"}),
        ),
        ("backup_error", ("restore", RuntimeError("restore failed"))),
    ],
)
def test_restore_completion_and_failure_both_reload_visible_state(
    event: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app.events = queue.Queue()
    app.events.put((event, value))
    app._maintenance_thread = SimpleNamespace()
    app._set_busy = lambda _value: None
    app._reload_after_restore = lambda: setattr(app, "restore_reloaded", True)
    app.after = lambda *_args: None
    app._t = lambda key, **_values: key
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)

    app._poll_events()

    assert app.restore_reloaded is True
