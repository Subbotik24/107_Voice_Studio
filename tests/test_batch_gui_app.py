"""Contracts for the Studio batch transcription queue panel.

The queue model itself is covered in ``tests/test_batch_app.py``. What is
asserted here is the GUI runner: which item is started, what the done, error
and cancel events do to it, and that exactly one transcription job is ever in
flight.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.batch import BatchQueue
from voice_studio.i18n import translate
from voice_studio.models import Settings, Transcript


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeWidget:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def configure(self, **kwargs: Any) -> None:
        value = kwargs.get("text")
        if isinstance(value, str):
            self.text = value


class FakePanel:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.text = ""

    def grid(self, **_kwargs: Any) -> None:
        self.events.append("grid")

    def grid_remove(self) -> None:
        self.events.append("grid_remove")

    def configure(self, **kwargs: Any) -> None:
        value = kwargs.get("text")
        if isinstance(value, str):
            self.text = value


class FakeTree:
    """A Treeview reduced to ordered rows, headings and a selection."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, ...]] = {}
        self.order: list[str] = []
        self.selected: tuple[str, ...] = ()
        self.headings: dict[str, str] = {}

    def get_children(self, _item: str = "") -> tuple[str, ...]:
        return tuple(self.order)

    def delete(self, *items: str) -> None:
        for iid in items:
            self.order.remove(iid)
            self.rows.pop(iid, None)

    def insert(self, _parent: str, _index: str, iid: str = "", values: tuple = ()) -> None:
        self.order.append(iid)
        self.rows[iid] = tuple(str(value) for value in values)

    def selection(self) -> tuple[str, ...]:
        return self.selected

    def heading(self, column: str, text: str = "") -> None:
        self.headings[column] = text

    def column_values(self, index: int) -> list[str]:
        return [self.rows[iid][index] for iid in self.order]


def _transcript(identifier: str, name: str) -> Transcript:
    return Transcript(
        id=identifier,
        created_at="2026-01-01T00:00:00+00:00",
        source_name=name,
        source_sha256="a" * 64,
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="текст",
        corrected_text="текст",
    )


def _media(tmp_path: Path, *names: str) -> list[Path]:
    created: list[Path] = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"RIFF")
        created.append(path.resolve())
    return created


def _app(tmp_path: Path, *names: str) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language="en")
    app.status = FakeVar("")
    app.current = None
    app._busy = False
    app._closing = False
    app.batch_queue = BatchQueue()
    app.batch_panel = FakePanel()
    app.batch_panel_visible = False
    app.batch_button = FakeWidget()
    app.batch_pause_button = FakeWidget()
    app.batch_recursive_var = FakeVar(False)
    app.batch_recursive_check = FakeWidget()
    app.batch_tree = FakeTree()
    app._batch_button_keys = {
        FakeWidget(): key
        for key in ("batch_add_files", "batch_add_folder", "batch_start")
    }
    app._batch_owned = False
    app._batch_started = None
    app._batch_last_transcript = None
    app.events = queue.Queue()
    app._shutdown_event = threading.Event()
    app._cancel_event = threading.Event()
    app.after_calls: list[tuple[int, Any]] = []

    def after(delay: int, callback: Any, *args: Any) -> str:
        app.after_calls.append((delay, callback))
        if delay == 0:
            callback(*args)
        return "after#0"

    app.after = after
    app.started: list[tuple[Path, bool, bool]] = []
    app.process_result = True

    def process(source: Path, *, cleanup: bool = False, batch: bool = False) -> bool:
        app.started.append((Path(source), cleanup, batch))
        if not app.process_result:
            app.status.set(translate("en", "processing_not_started"))
            return False
        app._busy = True
        app._batch_owned = batch
        return True

    app._process = process
    app.shown: list[Transcript] = []
    app.refreshed: list[str] = []

    def try_show_result(transcript: Transcript, **_kwargs: Any) -> bool:
        app.shown.append(transcript)
        app.current = transcript
        return True

    app._try_show_result = try_show_result
    app._refresh_history = lambda **_kwargs: app.refreshed.append("history")
    app._refresh_dashboard = lambda: app.refreshed.append("dashboard")
    app._cleanup_temp = lambda *_args: None

    def set_busy(value: bool) -> None:
        app._busy = value

    app._set_busy = set_busy
    app._report_automatic_cleanup_warning = lambda *_args: pytest.fail(
        "a batch item must not open a modal cleanup warning"
    )
    if names:
        app.batch_queue.add_paths(_media(tmp_path, *names))
    return app


def _finish(app: VoiceStudioApp, event: str, value: Any) -> None:
    app.events.put((event, value))
    app._poll_events()


# --- starting ---------------------------------------------------------------


def test_start_runs_the_first_pending_item_through_the_single_file_job(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")

    app._batch_start()

    first = app.batch_queue.items[0]
    assert first.status == "running"
    assert app.batch_queue.items[1].status == "pending"
    assert app.started == [(first.path, False, True)]
    assert app._batch_owned is True
    assert app.status.get() == translate("en", "batch_running_file", name="one.wav")


def test_start_is_refused_while_another_job_is_running(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")
    app._busy = True

    app._batch_start()

    assert app.started == []
    assert app.batch_queue.items[0].status == "pending"
    assert app.status.get() == translate("en", "batch_busy")


def test_an_item_that_cannot_start_is_failed_and_the_queue_moves_on(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app.process_result = False

    app._batch_start()

    statuses = [item.status for item in app.batch_queue.items]
    assert statuses == ["failed", "failed"]
    assert app.batch_queue.items[0].error == translate("en", "processing_not_started")
    assert app.status.get() == translate(
        "en", "batch_finished", done=0, failed=2, skipped=0
    )


# --- the job outcome --------------------------------------------------------


def test_a_done_event_marks_the_item_done_and_advances(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()

    _finish(app, "done", (_transcript("t-1", "one.wav"), None))

    first, second = app.batch_queue.items
    assert (first.status, first.transcript_id) == ("done", "t-1")
    assert first.seconds >= 0.0
    assert second.status == "running"
    assert len(app.started) == 2
    assert app.shown == []


def test_an_error_event_marks_the_item_failed_and_advances(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()

    _finish(app, "error", (RuntimeError("engine exploded"), None))

    first, second = app.batch_queue.items
    assert first.status == "failed"
    assert first.error == "engine exploded"
    assert second.status == "running"
    assert len(app.started) == 2


def test_an_error_in_a_batch_item_never_opens_a_modal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, "one.wav")
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *_args, **_kwargs: pytest.fail("a batch failure must not open a modal"),
    )
    app._batch_start()

    _finish(app, "error", (RuntimeError("engine exploded"), None))

    assert app.batch_queue.items[0].status == "failed"


def test_only_the_last_finished_transcript_reaches_the_editor(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()

    _finish(app, "done", (_transcript("t-1", "one.wav"), None))
    last = _transcript("t-2", "two.wav")
    _finish(app, "done", (last, None))

    assert app.shown == [last]
    assert app.status.get() == translate("en", "batch_finished", done=2, failed=0, skipped=0)
    assert "dashboard" in app.refreshed


def test_the_summary_counts_done_failed_and_skipped(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav", "three.wav")
    app.batch_queue.mark_skipped(app.batch_queue.items[2].path)
    app._batch_start()
    _finish(app, "done", (_transcript("t-1", "one.wav"), None))
    _finish(app, "error", (RuntimeError("boom"), None))

    assert app.status.get() == translate("en", "batch_finished", done=1, failed=1, skipped=1)


# --- pause, skip and cancel -------------------------------------------------


def test_pausing_stops_the_queue_after_the_running_item(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()

    app._batch_toggle_pause()
    _finish(app, "done", (_transcript("t-1", "one.wav"), None))

    assert app.batch_queue.paused is True
    assert [item.status for item in app.batch_queue.items] == ["done", "pending"]
    assert len(app.started) == 1
    assert app.batch_pause_button.text == translate("en", "batch_resume")
    assert app.status.get() == translate("en", "batch_paused", count=1)


def test_resuming_continues_with_the_next_pending_item(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()
    app._batch_toggle_pause()
    _finish(app, "done", (_transcript("t-1", "one.wav"), None))

    app._batch_toggle_pause()

    assert app.batch_queue.paused is False
    assert app.batch_queue.items[1].status == "running"
    assert app.batch_pause_button.text == translate("en", "batch_pause")


def test_skip_moves_the_selected_pending_items_out_of_the_way(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav", "three.wav")
    app.batch_tree.selected = ("1", "2")
    app._batch_refresh_view()

    app._batch_skip_selected()

    assert [item.status for item in app.batch_queue.items] == [
        "pending",
        "skipped",
        "skipped",
    ]


def test_skip_without_a_selection_reports_instead_of_skipping(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")

    app._batch_skip_selected()

    assert app.batch_queue.items[0].status == "pending"
    assert app.status.get() == translate("en", "batch_no_selection")


def test_skip_never_touches_a_running_item(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()
    app.batch_tree.selected = ("0", "1")

    app._batch_skip_selected()

    assert [item.status for item in app.batch_queue.items] == ["running", "skipped"]


def test_cancelling_fails_the_running_item_and_pauses_the_queue(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app._batch_start()

    app._cancel_current()
    _finish(app, "job_cancelled", None)

    first, second = app.batch_queue.items
    assert first.status == "failed"
    assert first.error == translate("en", "batch_status_cancelled")
    assert second.status == "pending"
    assert app.batch_queue.paused is True
    assert len(app.started) == 1


def test_closing_the_app_pauses_the_queue(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")
    app.recorder = SimpleNamespace(cancel=lambda: None)
    app.job_controller = SimpleNamespace(close=lambda: None)
    app.hotkey = None
    app._active_recording_path = None
    app._pending_microphone_files = set()
    app._ambiguous_microphone_files = set()
    app._recording_residue_diagnostics = []
    app._maintenance_thread = None
    app._confirm_editor_transition = lambda: True
    app._current_page = "studio"
    app.destroyed = False
    app.destroy = lambda: setattr(app, "destroyed", True)

    app._close()

    assert app.batch_queue.paused is True
    assert app.destroyed is True


# --- clearing and the view --------------------------------------------------


def test_clear_finished_removes_only_finished_items(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app.batch_queue.mark_skipped(app.batch_queue.items[0].path)

    app._batch_clear_finished()

    assert [item.path.name for item in app.batch_queue.items] == ["two.wav"]
    assert app.status.get() == translate("en", "batch_removed", count=1)


def test_clear_is_refused_while_an_item_is_running(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")
    app._batch_start()

    app._batch_clear()

    assert len(app.batch_queue.items) == 1
    assert app.status.get() == translate("en", "batch_busy")


def test_the_view_lists_the_localized_state_of_every_item(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav", "two.wav")
    app.batch_queue.mark_skipped(app.batch_queue.items[1].path)

    app._batch_refresh_view()

    assert app.batch_tree.column_values(0) == ["one.wav", "two.wav"]
    assert app.batch_tree.column_values(1) == [
        translate("en", "batch_status_pending"),
        translate("en", "batch_status_skipped"),
    ]


def test_adding_files_reports_what_was_added_and_what_was_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path)
    accepted = _media(tmp_path, "one.wav")[0]
    refused = tmp_path / "notes.txt"
    refused.write_text("no audio", encoding="utf-8")
    monkeypatch.setattr(
        app_module.filedialog,
        "askopenfilenames",
        lambda **_kwargs: (str(accepted), str(refused)),
    )

    app._batch_add_files()

    assert [item.path.name for item in app.batch_queue.items] == ["one.wav"]
    assert app.status.get() == (
        translate("en", "batch_added", count=1)
        + " "
        + translate("en", "batch_rejected", count=1)
    )


def test_adding_a_folder_honours_the_recursive_checkbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path)
    folder = tmp_path / "media"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    _media(folder, "top.wav")
    _media(nested, "deep.wav")
    monkeypatch.setattr(
        app_module.filedialog, "askdirectory", lambda **_kwargs: str(folder)
    )

    app._batch_add_folder()
    assert [item.path.name for item in app.batch_queue.items] == ["top.wav"]

    app.batch_recursive_var.set(True)
    app._batch_add_folder()
    assert sorted(item.path.name for item in app.batch_queue.items) == [
        "deep.wav",
        "top.wav",
    ]


def test_the_panel_toggles_without_a_central_page(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")

    app._toggle_batch_panel()
    assert app.batch_panel_visible is True
    assert app.batch_panel.events[-1] == "grid"
    assert app.batch_tree.column_values(0) == ["one.wav"]

    app._toggle_batch_panel()
    assert app.batch_panel_visible is False
    assert app.batch_panel.events[-1] == "grid_remove"


def test_retranslation_relabels_the_panel_and_the_queue_rows(tmp_path: Path) -> None:
    app = _app(tmp_path, "one.wav")
    app._batch_refresh_view()
    app.settings = Settings(ui_language="uk")

    app._refresh_batch_ui_text()

    assert app.batch_button.text == translate("uk", "batch_button")
    assert app.batch_panel.text == translate("uk", "batch_panel_title")
    assert app.batch_recursive_check.text == translate("uk", "batch_recursive")
    assert app.batch_tree.headings["status"] == translate("uk", "batch_column_status")
    assert app.batch_tree.column_values(1) == [translate("uk", "batch_status_pending")]
    for widget, key in app._batch_button_keys.items():
        assert widget.text == translate("uk", key)
