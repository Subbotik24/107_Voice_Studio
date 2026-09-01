"""Contracts for the Studio confidence-review panel."""

from __future__ import annotations

import pytest

from voice_studio import app as app_module
from voice_studio.app import EDITOR_CONFIDENCE_TAG, VoiceStudioApp
from voice_studio.editor_tools import ConfidenceEntry
from voice_studio.i18n import UI_LANGUAGE_CHOICES, translate
from voice_studio.models import Segment, Settings, Transcript


class FakeLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def configure(self, **kwargs: object) -> None:
        value = kwargs.get("text")
        if isinstance(value, str):
            self.text = value


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakePanel:
    def __init__(self) -> None:
        self.events: list[str] = []

    def pack(self, **_kwargs: object) -> None:
        self.events.append("pack")

    def pack_forget(self) -> None:
        self.events.append("pack_forget")


class FakeListbox:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.selection: tuple[int, ...] = ()

    def delete(self, first: int, last: str) -> None:
        assert (first, last) == (0, "end")
        self.rows = []

    def insert(self, index: str, value: str) -> None:
        assert index == "end"
        self.rows.append(value)

    def curselection(self) -> tuple[int, ...]:
        return self.selection


class FakeEditor:
    """The editor reduced to offsets, tag ranges and focus bookkeeping."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.tags: dict[str, list[tuple[str, str]]] = {}
        self.marks: dict[str, int] = {"insert": 0}
        self.seen: list[str] = []
        self.focused = 0

    def _offset(self, index: str) -> int:
        if index == "1.0":
            return 0
        if index in ("end", "end-1c"):
            return len(self.text)
        if index == "insert":
            return self.marks["insert"]
        if index.startswith("1.0+") and index.endswith("c"):
            return int(index[4:-1])
        raise AssertionError(f"unsupported index: {index}")

    def get(self, start: str, end: str) -> str:
        return self.text[self._offset(start) : self._offset(end)]

    def delete(self, start: str, end: str) -> None:
        first, last = self._offset(start), self._offset(end)
        self.text = self.text[:first] + self.text[last:]

    def insert(self, index: str, value: str) -> None:
        offset = self._offset(index)
        self.text = self.text[:offset] + value + self.text[offset:]

    def mark_set(self, name: str, index: str) -> None:
        self.marks[name] = self._offset(index)

    def see(self, index: str) -> None:
        self.seen.append(index)

    def focus_set(self) -> None:
        self.focused += 1

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags.setdefault(tag, []).append((str(start), str(end)))

    def tag_remove(self, tag: str, _start: str, _end: str) -> None:
        self.tags[tag] = []

    def tag_ranges(self, tag: str) -> tuple[str, ...]:
        return tuple(value for pair in self.tags.get(tag, []) for value in pair)


class ExplodingStore:
    def __getattr__(self, name: str):
        def fail(*_args: object, **_kwargs: object):
            raise AssertionError(f"the confidence panel must not call store.{name}")

        return fail


def _segment(text: str, confidence: object) -> Segment:
    item = Segment(start=0.0, end=1.0, text=text)
    item.confidence = confidence
    return item


def _transcript(segments: list[Segment]) -> Transcript:
    body = "\n".join(segment.text for segment in segments)
    return Transcript(
        id="id-1",
        created_at="2026-01-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text=body,
        corrected_text=body,
        segments=segments,
    )


def _confidence_app(
    segments: list[Segment] | None = None,
    *,
    ui_language: str = "en",
    text: str | None = None,
) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language=ui_language)
    app.current = None if segments is None else _transcript(segments)
    app.store = ExplodingStore()
    app.status = FakeVar("")
    app.editor = FakeEditor(
        text if text is not None else (app.current.corrected_text if app.current else "")
    )
    app.confidence_panel = FakePanel()
    app.confidence_panel_visible = False
    app.confidence_threshold_var = FakeVar(app_module.DEFAULT_CONFIDENCE_THRESHOLD)
    app.confidence_count_var = FakeVar("")
    app.confidence_list = FakeListbox()
    app._confidence_entries = ()
    app.editor_confidence_button = FakeLabel()
    app.editor_confidence_caption = FakeLabel()
    app._editor_confidence_button_keys = {
        FakeLabel(): key
        for key in ("editor_confidence_play", "editor_confidence_close")
    }
    return app


def _mixed_app(**kwargs: object) -> VoiceStudioApp:
    return _confidence_app(
        [
            _segment("перший сегмент", 0.90),
            _segment("другий сегмент", 0.42),
            _segment("третій сегмент", None),
            _segment("четвертий сегмент", 0.15),
        ],
        **kwargs,
    )


# --- filling the list -------------------------------------------------------


def test_refresh_lists_low_scores_first_then_the_unscored_segment() -> None:
    app = _mixed_app()

    app._refresh_confidence_panel()

    assert app.confidence_list.rows == [
        "0.15 · четвертий сегмент",
        "0.42 · другий сегмент",
        f"{translate('en', 'editor_confidence_no_score')} · третій сегмент",
    ]
    assert app._confidence_entries == (
        ConfidenceEntry(3, 0.15),
        ConfidenceEntry(1, 0.42),
        ConfidenceEntry(2, None),
    )
    assert app.confidence_count_var.get() == translate("en", "editor_confidence_count", count=3)


def test_a_long_snippet_is_bounded_and_single_line() -> None:
    body = "а" * 40 + "\n" + "б" * 40
    app = _confidence_app([_segment(body, 0.1)])

    app._refresh_confidence_panel()

    row = app.confidence_list.rows[0]
    assert "\n" not in row
    assert row == "0.10 · " + "а" * 40 + " " + "б" * 7 + "…"


def test_no_transcript_leaves_an_empty_list_and_the_empty_state_line() -> None:
    app = _confidence_app()

    app._refresh_confidence_panel()

    assert app.confidence_list.rows == []
    assert app._confidence_entries == ()
    assert app.confidence_count_var.get() == translate("en", "editor_confidence_empty")
    assert app.status.get() == ""


def test_every_segment_above_the_threshold_gives_the_empty_state() -> None:
    app = _confidence_app([_segment("високий бал", 0.95)])

    app._refresh_confidence_panel()

    assert app.confidence_list.rows == []
    assert app.confidence_count_var.get() == translate("en", "editor_confidence_empty")


def test_raising_the_threshold_refilters_the_list() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()

    app.confidence_threshold_var.set("0.20")
    app._refresh_confidence_panel()

    assert app.confidence_list.rows == [
        "0.15 · четвертий сегмент",
        f"{translate('en', 'editor_confidence_no_score')} · третій сегмент",
    ]


def test_an_invalid_threshold_reports_a_status_and_keeps_the_list() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()
    listed = list(app.confidence_list.rows)
    entries = app._confidence_entries

    for value in ("", "abc", "-0.1", "1.4"):
        app.confidence_threshold_var.set(value)
        app._refresh_confidence_panel()
        assert app.status.get() == translate("en", "editor_confidence_threshold_invalid")
        assert app.confidence_list.rows == listed
        assert app._confidence_entries == entries


def test_the_threshold_is_page_state_and_never_reaches_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda *_args, **_kwargs: pytest.fail("the threshold must never be persisted"),
    )
    app = _mixed_app()

    app._toggle_confidence_panel()
    app.confidence_threshold_var.set("0.85")
    app._refresh_confidence_panel()
    app.confidence_list.selection = (0,)
    app._select_confidence_entry()
    app._play_selected_segment()
    app._close_confidence_panel()

    assert app.settings.to_dict().get("confidence_threshold") is None


# --- selection and focus ----------------------------------------------------


def test_selecting_an_entry_highlights_and_focuses_the_segment() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()
    app.confidence_list.selection = (1,)

    app._select_confidence_entry()

    start = app.editor.text.index("другий сегмент")
    assert app.editor.tags[EDITOR_CONFIDENCE_TAG] == [
        (f"1.0+{start}c", f"1.0+{start + len('другий сегмент')}c")
    ]
    assert app.editor.marks["insert"] == start
    assert app.editor.seen == [f"1.0+{start}c"]
    assert app.editor.focused == 1
    assert app.status.get() == ""


def test_selecting_a_second_entry_replaces_the_previous_highlight() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()
    app.confidence_list.selection = (1,)
    app._select_confidence_entry()

    app.confidence_list.selection = (0,)
    app._select_confidence_entry()

    assert len(app.editor.tags[EDITOR_CONFIDENCE_TAG]) == 1
    assert app.editor.marks["insert"] == app.editor.text.index("четвертий сегмент")


def test_a_segment_missing_from_the_editor_text_reports_a_status() -> None:
    app = _mixed_app(text="повністю переписаний текст")
    app._refresh_confidence_panel()
    app.confidence_list.selection = (0,)

    app._select_confidence_entry()

    assert app.status.get() == translate("en", "editor_confidence_focus_missing")
    assert app.editor.tags.get(EDITOR_CONFIDENCE_TAG, []) == []


def test_selecting_nothing_focuses_nothing() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()

    app._select_confidence_entry()

    assert app.editor.focused == 0
    assert app.status.get() == ""


# --- the playback hook ------------------------------------------------------


def test_play_without_a_selection_reports_a_status() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()

    app._play_selected_segment()

    assert app.status.get() == translate("en", "editor_confidence_play_no_selection")


def test_play_routes_the_selected_segment_to_the_playback_hook() -> None:
    app = _mixed_app()
    app._refresh_confidence_panel()
    app.confidence_list.selection = (1,)
    requested: list[int] = []
    app._segment_play_requested = requested.append

    app._play_selected_segment()

    assert requested == [1]


def test_the_playback_hook_reports_that_playback_is_not_available_yet() -> None:
    app = _mixed_app()

    app._segment_play_requested(2)

    assert app.status.get() == translate("en", "editor_confidence_play_unavailable")


# --- panel lifecycle --------------------------------------------------------


def test_opening_the_panel_fills_it_and_closing_clears_the_highlight() -> None:
    app = _mixed_app()

    app._toggle_confidence_panel()

    assert app.confidence_panel_visible is True
    assert app.confidence_panel.events == ["pack"]
    assert len(app.confidence_list.rows) == 3
    app.confidence_list.selection = (0,)
    app._select_confidence_entry()

    app._toggle_confidence_panel()

    assert app.confidence_panel_visible is False
    assert app.confidence_panel.events == ["pack", "pack_forget"]
    assert app.editor.tags[EDITOR_CONFIDENCE_TAG] == []
    assert app.confidence_count_var.get() == ""


def test_show_result_refreshes_the_panel_only_while_it_is_visible() -> None:
    app = _mixed_app()
    app.raw_editor = _ReadonlyText()
    app.details = _ReadonlyText()
    app._apply_editor_formatting = lambda _formatting: None
    app._editor_formatting = dict
    replacement = _transcript([_segment("новий сегмент", 0.05)])

    app._show_result(replacement, refresh=False)
    assert app.confidence_list.rows == []

    app._toggle_confidence_panel()
    app.confidence_list.rows = []
    app._show_result(replacement, refresh=False)

    assert app.confidence_list.rows == ["0.05 · новий сегмент"]


class _ReadonlyText:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **_kwargs: object) -> None:
        return None

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _index: str, value: str) -> None:
        self.text = value


# --- retranslation ----------------------------------------------------------


@pytest.mark.parametrize("locale", [code for code, _label in UI_LANGUAGE_CHOICES])
def test_retranslate_relabels_the_panel_and_re_renders_the_rows(locale: str) -> None:
    app = _mixed_app(ui_language=locale)
    app._toggle_confidence_panel()

    app._refresh_confidence_ui_text()

    assert app.editor_confidence_button.text == translate(locale, "editor_confidence_button")
    assert app.editor_confidence_caption.text == translate(
        locale, "editor_confidence_threshold"
    )
    for button, key in app._editor_confidence_button_keys.items():
        assert button.text == translate(locale, key)
    assert app.confidence_list.rows[-1] == (
        f"{translate(locale, 'editor_confidence_no_score')} · третій сегмент"
    )
    assert app.confidence_count_var.get() == translate(
        locale, "editor_confidence_count", count=3
    )


def test_retranslate_clears_the_count_label_while_the_panel_is_hidden() -> None:
    app = _mixed_app()
    app.confidence_count_var.set("stale")

    app._refresh_confidence_ui_text()

    assert app.confidence_count_var.get() == ""
    assert app.confidence_list.rows == []
