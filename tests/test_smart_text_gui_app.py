"""Contracts for the Studio «Smart text» tab.

The rendering itself is covered in ``tests/test_smart_text_app.py``; what is
asserted here is the tab: which text the preview shows, how the options change
it, how a manual speaker label is stored and how an export reaches disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.i18n import translate
from voice_studio.models import Segment, Settings, Transcript
from voice_studio.smart_text import SPEAKER_LABELS_KEY


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


class FakeReadonlyText:
    def __init__(self) -> None:
        self.text = ""
        self.state = "disabled"

    def configure(self, **kwargs: Any) -> None:
        state = kwargs.get("state")
        if isinstance(state, str):
            self.state = state

    def delete(self, _start: str, _end: str) -> None:
        assert self.state == "normal"
        self.text = ""

    def insert(self, _index: str, value: str) -> None:
        assert self.state == "normal"
        self.text += value


class FakeListbox:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.selected: tuple[int, ...] = ()

    def delete(self, first: int, last: str) -> None:
        assert (first, last) == (0, "end")
        self.rows = []

    def insert(self, index: str, value: str) -> None:
        assert index == "end"
        self.rows.append(value)

    def curselection(self) -> tuple[int, ...]:
        return self.selected


class FakeEditor:
    def __init__(self) -> None:
        self.text = ""

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _index: str, value: str) -> None:
        self.text += value

    def get(self, _start: str, _end: str) -> str:
        return self.text

    def tag_ranges(self, _tag: str) -> tuple[str, ...]:
        return ()

    def tag_remove(self, *_args: Any) -> None:
        return None

    def tag_add(self, *_args: Any) -> None:
        return None


class RecordingStore:
    """Records the one storage call the tab is allowed to make."""

    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.calls: list[tuple[str, dict[int, str]]] = []

    def update_speaker_labels(
        self, transcript_id: str, labels: dict[int, str]
    ) -> Transcript:
        self.calls.append((transcript_id, dict(labels)))
        stored = {str(index): name for index, name in sorted(labels.items())}
        metadata = {key: value for key, value in self.transcript.metadata.items()}
        if stored:
            metadata[SPEAKER_LABELS_KEY] = stored
        else:
            metadata.pop(SPEAKER_LABELS_KEY, None)
        self.transcript.metadata = metadata
        return self.transcript

    def __getattr__(self, name: str):
        def fail(*_args: object, **_kwargs: object):
            raise AssertionError(f"the smart text tab must not call store.{name}")

        return fail


def _transcript(*, metadata: dict[str, Any] | None = None) -> Transcript:
    segments = [
        Segment(start=0.0, end=2.0, text="перший"),
        Segment(start=2.5, end=4.0, text="друхий", corrected_text="другий"),
        Segment(start=70.0, end=73.0, text="третій"),
    ]
    return Transcript(
        id="t-1",
        created_at="2026-01-01T00:00:00+00:00",
        source_name="нарада.wav",
        source_sha256="a" * 64,
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="перший друхий третій",
        corrected_text="перший другий третій",
        segments=segments,
        metadata=dict(metadata or {}),
    )


def _app(transcript: Transcript | None) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language="en")
    app.current = transcript
    app.status = FakeVar("")
    app.store = RecordingStore(transcript) if transcript is not None else RecordingStore(
        _transcript()
    )
    app.smart_text_view = FakeReadonlyText()
    app.smart_speaker_list = FakeListbox()
    app.smart_gap_var = FakeVar("2.0")
    app.smart_max_var = FakeVar("90")
    app.smart_timestamps_var = FakeVar(True)
    app.smart_speakers_var = FakeVar(True)
    app._smart_text_rendered = ""
    app.smart_text_gap_label = FakeWidget()
    app.smart_text_max_label = FakeWidget()
    app.smart_timestamps_check = FakeWidget()
    app.smart_speakers_check = FakeWidget()
    app.smart_speaker_caption = FakeWidget()
    app._smart_text_button_keys = {
        FakeWidget(): key
        for key in (
            "smart_text_refresh",
            "smart_text_copy",
            "smart_text_export_md",
            "smart_text_export_txt",
            "smart_text_assign_speaker",
        )
    }
    app.copied: list[str] = []
    app._copy_to_clipboard = lambda text: app.copied.append(text)
    return app


# --- rendering --------------------------------------------------------------


def test_the_preview_renders_the_editable_segment_layer_not_the_raw_text() -> None:
    app = _app(_transcript())

    app._refresh_smart_text()

    assert app.smart_text_view.text == "[0:00] перший другий\n\n[1:10] третій\n"
    assert "друхий" not in app.smart_text_view.text


def test_turning_timestamps_off_drops_them_from_the_preview() -> None:
    app = _app(_transcript())
    app.smart_timestamps_var.set(False)

    app._refresh_smart_text()

    assert app.smart_text_view.text == "перший другий\n\nтретій\n"


def test_a_larger_pause_keeps_the_whole_transcript_in_one_paragraph() -> None:
    app = _app(_transcript())
    app.smart_gap_var.set("120")

    app._refresh_smart_text()

    assert app.smart_text_view.text == "[0:00] перший другий третій\n"


def test_turning_speakers_off_hides_the_stored_labels() -> None:
    app = _app(_transcript(metadata={SPEAKER_LABELS_KEY: {"0": "Оля"}}))

    app._refresh_smart_text()
    assert app.smart_text_view.text.startswith("[0:00] Оля: перший другий")

    app.smart_speakers_var.set(False)
    app._refresh_smart_text()
    assert app.smart_text_view.text.startswith("[0:00] перший другий")


def test_an_out_of_range_option_reports_and_leaves_no_stale_preview() -> None:
    app = _app(_transcript())
    app._refresh_smart_text()
    assert app.smart_text_view.text

    for variable, value in (
        (app.smart_gap_var, "-1"),
        (app.smart_gap_var, "abc"),
        (app.smart_max_var, "1"),
        (app.smart_max_var, "99999"),
    ):
        previous = variable.get()
        variable.set(value)
        app._refresh_smart_text()
        assert app.smart_text_view.text == ""
        assert app.status.get() == translate("en", "smart_text_invalid")
        variable.set(previous)


def test_without_a_transcript_the_tab_shows_its_hint_and_no_segments() -> None:
    app = _app(None)

    app._refresh_smart_text()

    assert app.smart_text_view.text == translate("en", "smart_text_empty")
    assert app.smart_speaker_list.rows == []
    assert app._smart_text_rendered == ""


def test_the_segment_list_shows_index_start_and_the_stored_label() -> None:
    app = _app(_transcript(metadata={SPEAKER_LABELS_KEY: {"2": "Оля"}}))

    app._refresh_smart_text()

    assert app.smart_speaker_list.rows == [
        "0 · 0:00 · перший",
        "1 · 0:02 · другий",
        "2 · 1:10 · третій — Оля",
    ]


def test_showing_a_result_re_renders_the_tab_for_the_new_transcript() -> None:
    app = _app(None)
    app.editor = FakeEditor()
    app.raw_editor = FakeReadonlyText()
    app.details = FakeReadonlyText()
    app.confidence_panel_visible = False
    app._stop_playback = lambda: None
    app._refresh_history = lambda **_kwargs: None
    app._refresh_dashboard = lambda: None
    transcript = _transcript(metadata={SPEAKER_LABELS_KEY: {"0": "Оля"}})

    app._show_result(transcript, refresh=False)

    assert app.smart_text_view.text.startswith("[0:00] Оля: перший другий")
    assert app.smart_speaker_list.rows[0].endswith("— Оля")


# --- speaker labels ---------------------------------------------------------


def test_assigning_a_speaker_persists_it_and_re_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_transcript())
    app._refresh_smart_text()
    app.smart_speaker_list.selected = (1,)
    monkeypatch.setattr(
        app_module.simpledialog, "askstring", lambda *_args, **_kwargs: "  Оля  "
    )

    app._assign_smart_speaker()

    assert app.store.calls == [("t-1", {1: "Оля"})]
    assert app.current.metadata[SPEAKER_LABELS_KEY] == {"1": "Оля"}
    assert "**Оля:**" not in app.smart_text_view.text
    assert "Оля: другий" in app.smart_text_view.text
    assert app.smart_speaker_list.rows[1].endswith("— Оля")


def test_an_empty_answer_clears_the_stored_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_transcript(metadata={SPEAKER_LABELS_KEY: {"1": "Оля"}}))
    app._refresh_smart_text()
    app.smart_speaker_list.selected = (1,)
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: "")

    app._assign_smart_speaker()

    assert app.store.calls == [("t-1", {})]
    assert SPEAKER_LABELS_KEY not in app.current.metadata
    assert "Оля" not in app.smart_text_view.text


def test_cancelling_the_dialog_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_transcript())
    app._refresh_smart_text()
    app.smart_speaker_list.selected = (1,)
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: None)

    app._assign_smart_speaker()

    assert app.store.calls == []


def test_assigning_without_a_selected_segment_reports_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_transcript())
    app._refresh_smart_text()
    monkeypatch.setattr(
        app_module.simpledialog,
        "askstring",
        lambda *_a, **_k: pytest.fail("no segment is selected"),
    )

    app._assign_smart_speaker()

    assert app.store.calls == []
    assert app.status.get() == translate("en", "smart_text_select_segment")


# --- copy and export --------------------------------------------------------


def test_copy_puts_the_rendered_paragraphs_on_the_clipboard() -> None:
    app = _app(_transcript())
    app._refresh_smart_text()

    app._copy_smart_text()

    assert app.copied == ["[0:00] перший другий\n\n[1:10] третій\n"]
    assert app.status.get() == translate("en", "copied")


def test_markdown_export_writes_the_rendered_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(_transcript(metadata={SPEAKER_LABELS_KEY: {"0": "Оля"}}))
    destination = tmp_path / "out.md"
    monkeypatch.setattr(
        app_module.filedialog, "asksaveasfilename", lambda **_kwargs: str(destination)
    )

    app._export_smart_text("md")

    content = destination.read_text(encoding="utf-8")
    assert content.startswith("# нарада.wav\n\n[0:00] **Оля:** перший другий")
    assert app.status.get() == translate("en", "smart_text_exported", name="out.md")
    assert list(tmp_path.iterdir()) == [destination]


def test_plain_export_writes_the_preview_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(_transcript())
    destination = tmp_path / "out.txt"
    monkeypatch.setattr(
        app_module.filedialog, "asksaveasfilename", lambda **_kwargs: str(destination)
    )

    app._export_smart_text("txt")

    assert destination.read_text(encoding="utf-8") == (
        "[0:00] перший другий\n\n[1:10] третій\n"
    )


def test_a_cancelled_export_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(_transcript())
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_kwargs: "")

    app._export_smart_text("txt")

    assert list(tmp_path.iterdir()) == []


def test_export_without_a_transcript_reports_and_never_opens_a_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(None)
    monkeypatch.setattr(
        app_module.filedialog,
        "asksaveasfilename",
        lambda **_kwargs: pytest.fail("there is nothing to export"),
    )

    app._export_smart_text("md")

    assert app.status.get() == translate("en", "smart_text_empty")


# --- localization -----------------------------------------------------------


def test_the_options_are_page_state_and_never_reach_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda *_a, **_k: pytest.fail("smart text options must never be persisted"),
    )
    app = _app(_transcript())

    app.smart_gap_var.set("5")
    app.smart_max_var.set("120")
    app.smart_timestamps_var.set(False)
    app._refresh_smart_text()

    assert app.settings.to_dict().get("smart_text_gap_seconds") is None


def test_retranslation_relabels_the_tab_and_re_renders() -> None:
    app = _app(_transcript())
    app._refresh_smart_text()
    app.settings = Settings(ui_language="uk")

    app._refresh_smart_text_ui_text()

    assert app.smart_text_gap_label.text == translate("uk", "smart_text_gap")
    assert app.smart_text_max_label.text == translate("uk", "smart_text_max")
    assert app.smart_timestamps_check.text == translate("uk", "smart_text_timestamps")
    assert app.smart_speakers_check.text == translate("uk", "smart_text_speakers")
    assert app.smart_speaker_caption.text == translate("uk", "smart_text_speaker_list")
    for widget, key in app._smart_text_button_keys.items():
        assert widget.text == translate("uk", key)
