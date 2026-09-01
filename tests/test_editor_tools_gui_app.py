"""Contracts for the Studio editor tools: find/replace, dictionary rules, fillers."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio.app import EDITOR_FIND_TAG, VoiceStudioApp
from voice_studio.dictionary import DictionaryRule, TerminologyDictionary
from voice_studio.editor_tools import FillerMatch, find_filler_matches
from voice_studio.i18n import UI_LANGUAGE_CHOICES, translate
from voice_studio.models import Settings


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


class FakeText:
    """A Tk text widget reduced to character offsets and tag ranges."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.tags: dict[str, list[tuple[str, str]]] = {
            "bold": [],
            "italic": [],
            EDITOR_FIND_TAG: [],
        }
        self.marks: dict[str, int] = {"insert": 0}
        self.selection: tuple[int, int] | None = None

    def _offset(self, index: str) -> int:
        if index == "1.0":
            return 0
        if index in ("end", "end-1c"):
            return len(self.text)
        if index == "insert":
            return self.marks["insert"]
        if index in ("sel.first", "sel.last"):
            if self.selection is None:
                raise tk.TclError("text doesn't contain any characters tagged with sel")
            return self.selection[0] if index == "sel.first" else self.selection[1]
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

    def index(self, name: str) -> str:
        return name

    def mark_set(self, name: str, index: str) -> None:
        self.marks[name] = self._offset(index)

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags.setdefault(tag, []).append((str(start), str(end)))

    def tag_remove(self, tag: str, _start: str, _end: str) -> None:
        self.tags[tag] = []

    def tag_ranges(self, tag: str) -> tuple[str, ...]:
        return tuple(value for pair in self.tags.get(tag, []) for value in pair)


class ExplodingStore:
    """Any storage write during an editor-tool flow is a defect."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def fail(*_args: object, **_kwargs: object):
            self.calls.append(name)
            raise AssertionError(f"editor tools must not call store.{name}")

        return fail


class FakeDictionaryRepository:
    def __init__(self, managed_path: Path) -> None:
        self.managed_path = managed_path
        self.saved: list[list[DictionaryRule]] = []
        self.error: Exception | None = None

    def is_managed(self, path: str | Path) -> bool:
        return Path(path) == self.managed_path

    def save_managed(self, dictionary: TerminologyDictionary) -> None:
        if self.error is not None:
            raise self.error
        self.saved.append(list(dictionary.rules))


def _editor_app(
    text: str = "",
    *,
    ui_language: str = "en",
    language: str = "uk",
    current: object | None = None,
) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language=ui_language, language=language)
    app.current = current
    app.store = ExplodingStore()
    app.status = FakeVar("")
    app.editor = FakeText(text)
    app.find_panel = FakePanel()
    app.find_panel_visible = False
    app.editor_find_var = FakeVar("")
    app.editor_replace_var = FakeVar("")
    app.editor_find_case_var = FakeVar(False)
    app.editor_find_word_var = FakeVar(False)
    app.editor_find_count_var = FakeVar("")
    app.editor_find_button = FakeLabel()
    app.editor_add_rule_button = FakeLabel()
    app.editor_filler_button = FakeLabel()
    app.editor_find_caption = FakeLabel()
    app.editor_replace_caption = FakeLabel()
    app.editor_find_case_check = FakeLabel()
    app.editor_find_word_check = FakeLabel()
    app._editor_find_button_keys = {
        FakeLabel(): key
        for key in (
            "editor_find_action",
            "editor_find_replace_one",
            "editor_find_replace_all",
            "editor_find_close",
        )
    }
    return app


def _dictionary_app(
    tmp_path: Path,
    text: str,
    *,
    rules: list[DictionaryRule] | None = None,
    read_only: bool = False,
    dirty: bool = False,
) -> VoiceStudioApp:
    app = _editor_app(text)
    app.dictionary = TerminologyDictionary(rules or [])
    app.dictionary_read_only = read_only
    app._dictionary_dirty = dirty
    app.dictionary_repository = FakeDictionaryRepository(tmp_path / "dictionary.json")
    app.dictionary_banner_var = FakeVar("")
    app._dictionary_refresh_widgets = lambda: None
    return app


def _tag_offsets(app: VoiceStudioApp) -> list[tuple[str, str]]:
    return app.editor.tags[EDITOR_FIND_TAG]


# --- find and replace -------------------------------------------------------


def test_find_reports_the_count_and_highlights_every_match() -> None:
    app = _editor_app("ем один ем два ЕМ")
    app.editor_find_var.set("ем")

    matches = app._find_in_editor()

    assert [(match.start, match.end) for match in matches] == [(0, 2), (8, 10), (15, 17)]
    assert app.editor_find_count_var.get() == translate("en", "editor_find_count", count=3)
    assert _tag_offsets(app) == [
        ("1.0+0c", "1.0+2c"),
        ("1.0+8c", "1.0+10c"),
        ("1.0+15c", "1.0+17c"),
    ]


def test_find_honours_case_sensitive_and_whole_word_flags() -> None:
    app = _editor_app("кіт кітик Кіт")
    app.editor_find_var.set("кіт")
    app.editor_find_case_var.set(True)
    app.editor_find_word_var.set(True)

    matches = app._find_in_editor()

    assert [(match.start, match.end) for match in matches] == [(0, 3)]


def test_empty_query_clears_the_count_label_and_the_highlight() -> None:
    app = _editor_app("ем один ем два")
    app.editor_find_var.set("ем")
    app._find_in_editor()

    app.editor_find_var.set("   ")
    assert app._find_in_editor() == ()
    assert app.editor_find_count_var.get() == ""
    assert _tag_offsets(app) == []
    assert app.editor.text == "ем один ем два"


def test_empty_query_makes_both_replacements_no_ops() -> None:
    app = _editor_app("ем один ем два")
    app.editor_replace_var.set("so")

    assert app._replace_one_in_editor() is False
    assert app._replace_all_in_editor() == 0
    assert app.editor.text == "ем один ем два"


def test_replace_one_replaces_the_first_match_after_the_cursor() -> None:
    app = _editor_app("ем один ем два")
    app.editor_find_var.set("ем")
    app.editor_replace_var.set("so")
    app.editor.marks["insert"] = 3

    assert app._replace_one_in_editor() is True
    assert app.editor.text == "ем один so два"
    assert app.editor.marks["insert"] == 10


def test_replace_one_wraps_to_the_first_match() -> None:
    app = _editor_app("ем один ем два")
    app.editor_find_var.set("ем")
    app.editor_replace_var.set("so")
    app.editor.marks["insert"] = len(app.editor.text)

    assert app._replace_one_in_editor() is True
    assert app.editor.text == "so один ем два"
    assert app.editor.marks["insert"] == 2


def test_replace_all_replaces_every_match_and_reports_the_count() -> None:
    app = _editor_app("ем один ем два ем")
    app.editor_find_var.set("ем")
    app.editor_replace_var.set("emm")

    assert app._replace_all_in_editor() == 3
    assert app.editor.text == "emm один emm два emm"
    assert app.status.get() == translate("en", "editor_find_replaced", count=3)


def test_replace_all_with_an_empty_replacement_deletes_the_matches() -> None:
    app = _editor_app("ab-ab")
    app.editor_find_var.set("ab")

    assert app._replace_all_in_editor() == 2
    assert app.editor.text == "-"


def test_replacing_keeps_formatting_tags_outside_the_span() -> None:
    app = _editor_app("ем один ем два")
    app.editor.tags["bold"] = [("1.0+3c", "1.0+7c")]
    app.editor_find_var.set("ем")
    app.editor_replace_var.set("so")

    app._replace_all_in_editor()

    assert app.editor.tags["bold"] == [("1.0+3c", "1.0+7c")]


def test_closing_the_panel_hides_it_and_clears_the_highlight() -> None:
    app = _editor_app("ем один ем два")
    app.editor_find_var.set("ем")
    app._toggle_find_panel()
    app._find_in_editor()

    app._close_find_panel()

    assert app.find_panel_visible is False
    assert app.find_panel.events == ["pack", "pack_forget"]
    assert _tag_offsets(app) == []
    assert app.editor_find_count_var.get() == ""


# --- add the selection to the dictionary ------------------------------------


def test_add_to_dictionary_without_a_selection_reports_a_status(tmp_path) -> None:
    app = _dictionary_app(tmp_path, "ем один")

    app._add_selection_to_dictionary()

    assert app.status.get() == translate("en", "editor_add_rule_no_selection")
    assert app.dictionary.rules == []
    assert app.dictionary_repository.saved == []


def test_add_to_dictionary_is_refused_for_a_read_only_dictionary(
    tmp_path, monkeypatch
) -> None:
    app = _dictionary_app(tmp_path, "трансформатор", read_only=True)
    app.editor.selection = (0, 13)
    monkeypatch.setattr(
        app_module.simpledialog,
        "askstring",
        lambda *_a, **_k: pytest.fail("no dialog may open for a read-only dictionary"),
    )

    app._add_selection_to_dictionary()

    assert app.status.get() == translate("en", "editor_add_rule_read_only")
    assert app.dictionary.rules == []
    assert app.dictionary_repository.saved == []


def test_add_to_dictionary_is_refused_while_the_dictionary_is_dirty(
    tmp_path, monkeypatch
) -> None:
    app = _dictionary_app(tmp_path, "трансформатор", dirty=True)
    app.editor.selection = (0, 13)
    monkeypatch.setattr(
        app_module.simpledialog,
        "askstring",
        lambda *_a, **_k: pytest.fail("no dialog may open over unsaved dictionary edits"),
    )

    app._add_selection_to_dictionary()

    assert app.status.get() == translate("en", "editor_add_rule_unsaved")
    assert app.dictionary.rules == []
    assert app.dictionary_repository.saved == []
    assert app._dictionary_dirty is True


def test_add_to_dictionary_saves_the_rule_and_rewrites_only_the_editor(
    tmp_path, monkeypatch
) -> None:
    app = _dictionary_app(tmp_path, "інвертор і ще інвертор", rules=[])
    app.editor.selection = (0, 8)
    prompts: list[tuple[tuple, dict]] = []
    saved_settings: list[Settings] = []

    def askstring(*args: object, **kwargs: object) -> str:
        prompts.append((args, kwargs))
        return " Інвертор "

    monkeypatch.setattr(app_module.simpledialog, "askstring", askstring)
    monkeypatch.setattr(app_module, "save_settings", saved_settings.append)

    app._add_selection_to_dictionary()

    assert prompts and prompts[0][1]["initialvalue"] == "інвертор"
    assert app.dictionary.rules == [
        DictionaryRule(
            source="інвертор",
            target="Інвертор",
            case_sensitive=False,
            whole_word=True,
            use_as_hint=False,
        )
    ]
    assert app.dictionary_repository.saved == [app.dictionary.rules]
    assert saved_settings[-1].dictionary_path == str(app.dictionary_repository.managed_path)
    assert app.editor.text == "Інвертор і ще Інвертор"
    assert app.status.get() == translate(
        "en", "editor_add_rule_saved", source="інвертор", target="Інвертор"
    )
    assert app.store.calls == []


def test_add_to_dictionary_applies_only_the_new_rule(tmp_path, monkeypatch) -> None:
    existing = DictionaryRule(source="кабель", target="КАБЕЛЬ")
    app = _dictionary_app(tmp_path, "кабель і інвертор", rules=[existing])
    app.editor.selection = (9, 17)
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: "Інвертор")
    monkeypatch.setattr(app_module, "save_settings", lambda _settings: None)

    app._add_selection_to_dictionary()

    assert app.editor.text == "кабель і Інвертор"
    assert app.dictionary.rules[0] == existing
    assert len(app.dictionary.rules) == 2


def test_add_to_dictionary_cancel_changes_nothing(tmp_path, monkeypatch) -> None:
    app = _dictionary_app(tmp_path, "інвертор")
    app.editor.selection = (0, 8)
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda _settings: pytest.fail("cancel must not persist anything"),
    )

    app._add_selection_to_dictionary()

    assert app.dictionary.rules == []
    assert app.dictionary_repository.saved == []
    assert app.editor.text == "інвертор"


def test_add_to_dictionary_rejects_an_empty_replacement(tmp_path, monkeypatch) -> None:
    app = _dictionary_app(tmp_path, "інвертор")
    app.editor.selection = (0, 8)
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: "   ")
    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda _settings: pytest.fail("an empty rule must not be persisted"),
    )

    app._add_selection_to_dictionary()

    assert app.status.get() == translate("en", "editor_add_rule_empty")
    assert app.dictionary.rules == []


def test_add_to_dictionary_rolls_the_rule_back_when_saving_fails(
    tmp_path, monkeypatch
) -> None:
    app = _dictionary_app(tmp_path, "інвертор")
    app.editor.selection = (0, 8)
    app.dictionary_repository.error = OSError("disk full")
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: "Інвертор")
    monkeypatch.setattr(app_module, "save_settings", lambda _settings: None)

    app._add_selection_to_dictionary()

    assert app.dictionary.rules == []
    assert app.editor.text == "інвертор"


# --- filler cleanup ---------------------------------------------------------


def test_filler_collection_prefers_the_transcript_language() -> None:
    app = _editor_app("ehm ano, um yes", language="en", current=SimpleNamespace(language="cs"))

    matches = app._collect_filler_matches()

    assert [match.word for match in matches] == ["ehm"]


def test_filler_collection_falls_back_to_the_settings_language() -> None:
    app = _editor_app("um yes", language="en", current=SimpleNamespace(language="auto"))

    assert [match.word for match in app._collect_filler_matches()] == ["um"]


def test_filler_collection_is_empty_when_no_language_is_concrete() -> None:
    app = _editor_app("um yes", language="auto", current=SimpleNamespace(language="auto"))

    assert app._collect_filler_matches() == ()


def test_filler_dialog_status_when_nothing_is_found() -> None:
    app = _editor_app("um yes", language="auto", current=None)

    app._open_filler_dialog()

    assert app.status.get() == translate("en", "editor_filler_none")
    assert app.editor.text == "um yes"


def test_apply_filler_removal_removes_only_the_selected_matches() -> None:
    text = "um one um two um three"
    app = _editor_app(text, language="en")
    matches = find_filler_matches(text, "en")
    assert len(matches) == 3

    app._apply_filler_removal(matches, [True, False, True])

    assert app.editor.text == "one um two three"
    assert app.status.get() == translate("en", "editor_filler_removed", count=2)
    assert app.store.calls == []


def test_apply_filler_removal_with_nothing_selected_is_a_no_op() -> None:
    text = "um one um two"
    app = _editor_app(text, language="en")
    matches = find_filler_matches(text, "en")

    app._apply_filler_removal(matches, [False, False])

    assert app.editor.text == text
    assert app.status.get() == ""


def test_apply_filler_removal_refuses_when_the_editor_text_changed_since_the_snapshot() -> None:
    text = "um one um two"
    app = _editor_app(text, language="en", current=SimpleNamespace(id="t1"))
    matches = find_filler_matches(text, "en")
    snapshot = ("t1", text)
    app.editor.text = "a completely different transcript now"

    app._apply_filler_removal(matches, [True, True], snapshot=snapshot)

    assert app.editor.text == "a completely different transcript now"
    assert app.status.get() == translate("en", "editor_filler_stale")


def test_apply_filler_removal_refuses_when_the_current_transcript_changed() -> None:
    text = "um one um two"
    app = _editor_app(text, language="en", current=SimpleNamespace(id="t1"))
    matches = find_filler_matches(text, "en")
    snapshot = ("t1", text)
    app.current = SimpleNamespace(id="t2")

    app._apply_filler_removal(matches, [True, True], snapshot=snapshot)

    assert app.editor.text == text
    assert app.status.get() == translate("en", "editor_filler_stale")


def test_apply_filler_removal_with_a_matching_snapshot_still_applies() -> None:
    text = "um one um two"
    app = _editor_app(text, language="en", current=SimpleNamespace(id="t1"))
    matches = find_filler_matches(text, "en")
    snapshot = ("t1", text)

    app._apply_filler_removal(matches, [True, True], snapshot=snapshot)

    assert app.editor.text == "one two"
    assert app.status.get() == translate("en", "editor_filler_removed", count=2)


def test_filler_context_snippet_is_bounded_and_single_line() -> None:
    text = "a" * 60 + "\num\n" + "b" * 60
    match = FillerMatch(61, 63, "um")

    snippet = VoiceStudioApp._filler_context(text, match)

    assert "\n" not in snippet
    assert "[um]" in snippet
    assert snippet == "…" + "a" * 29 + " [um] " + "b" * 29 + "…"


# --- retranslation ----------------------------------------------------------


@pytest.mark.parametrize("locale", [code for code, _label in UI_LANGUAGE_CHOICES])
def test_editor_tools_retranslate_relabels_every_persistent_widget(locale: str) -> None:
    app = _editor_app("ем один", ui_language=locale)
    app.editor_find_var.set("ем")
    app._toggle_find_panel()

    app._refresh_editor_tools_ui_text()

    assert app.editor_find_button.text == translate(locale, "editor_find_button")
    assert app.editor_add_rule_button.text == translate(locale, "editor_add_rule_button")
    assert app.editor_filler_button.text == translate(locale, "editor_filler_button")
    assert app.editor_find_caption.text == translate(locale, "editor_find_label")
    assert app.editor_replace_caption.text == translate(locale, "editor_replace_label")
    assert app.editor_find_case_check.text == translate(locale, "editor_find_case")
    assert app.editor_find_word_check.text == translate(locale, "editor_find_whole_word")
    for button, key in app._editor_find_button_keys.items():
        assert button.text == translate(locale, key)
    assert app.editor_find_count_var.get() == translate(locale, "editor_find_count", count=1)


def test_retranslate_clears_the_count_label_while_the_panel_is_hidden() -> None:
    app = _editor_app("ем один")
    app.editor_find_count_var.set("stale")

    app._refresh_editor_tools_ui_text()

    assert app.editor_find_count_var.get() == ""
