from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_studio.app import VoiceStudioApp
from voice_studio.dictionary import DictionaryRule
from voice_studio.dictionary_store import DictionaryRepository
from voice_studio.models import Settings


def _app(tmp_path: Path, dictionary_path: str = "") -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(dictionary_path=dictionary_path)
    app.dictionary_repository = DictionaryRepository(tmp_path / "config")
    app._t = lambda key, **values: key.format(**values)
    app.status = SimpleNamespace(set=lambda _message: None)
    app._dictionary_status = lambda _message: None
    app._dictionary_refresh_widgets = lambda: None
    return app


def test_empty_configured_path_stays_in_memory_until_managed_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches opening an empty Dictionary page that eagerly creates dictionary.json."""

    app = _app(tmp_path)
    saved: list[Settings] = []
    monkeypatch.setattr("voice_studio.app.save_settings", lambda settings: saved.append(settings))

    VoiceStudioApp._reload_dictionary(app)

    assert app.dictionary.rules == []
    assert not app.dictionary_repository.managed_path.exists()
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "new"))
    assert VoiceStudioApp._save_dictionary(app) is True
    assert json.loads(app.dictionary_repository.managed_path.read_text(encoding="utf-8")) == {
        "replacements": [
            {
                "source": "old",
                "target": "new",
                "case_sensitive": False,
                "whole_word": True,
                "use_as_hint": True,
            }
        ]
    }
    assert app.settings.dictionary_path == str(app.dictionary_repository.managed_path)
    assert saved == [app.settings]


def test_external_dictionary_is_read_only_and_original_is_unchanged(tmp_path: Path) -> None:
    """Catches edit actions mutating a configured external dictionary without Import."""

    external = tmp_path / "external.json"
    original = '{"replacements":[{"source":"old","target":"external"}]}'
    external.write_text(original, encoding="utf-8")
    app = _app(tmp_path, str(external))

    VoiceStudioApp._reload_dictionary(app)

    assert app.dictionary_read_only is True
    assert VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("new", "value")) is False
    assert external.read_text(encoding="utf-8") == original


def test_rule_edits_reorder_and_search_are_in_memory_and_keep_canonical_order(
    tmp_path: Path,
) -> None:
    """Catches UI filtering or edits that reorder the underlying deterministic rules."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("alpha", "one"))
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("beta", "two"))
    assert VoiceStudioApp._edit_dictionary_rule(app, 0, DictionaryRule("alpha", "uno")) is True
    assert VoiceStudioApp._move_dictionary_rule(app, 1, -1) is True
    assert [rule.source for rule in app.dictionary.rules] == ["beta", "alpha"]
    assert [rule.target for rule in VoiceStudioApp._filtered_dictionary_rules(app, "uno")] == [
        "uno"
    ]
    assert [rule.source for rule in app.dictionary.rules] == ["beta", "alpha"]
    assert VoiceStudioApp._delete_dictionary_rule(app, 1) is True
    assert [rule.source for rule in app.dictionary.rules] == ["beta"]


def test_test_sentence_applies_only_current_in_memory_dictionary(tmp_path: Path) -> None:
    """Catches the test box writing rules or using stale persisted content."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("mistake", "fixed"))

    assert VoiceStudioApp._apply_dictionary_test_sentence(app, "A mistake.") == "A fixed."
    assert not app.dictionary_repository.managed_path.exists()


@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_merge_import_saves_managed_copy_updates_settings_and_blocks_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """Catches import writing an external input or writing despite a blocking conflict."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "local"))
    incoming = tmp_path / f"incoming.{suffix}"
    if suffix == "csv":
        incoming.write_text(
            "source,target,case_sensitive,whole_word,use_as_hint\nnew,value,false,true,true\n",
            encoding="utf-8",
        )
    else:
        incoming.write_text(
            '{"replacements":[{"source":"new","target":"value"}]}',
            encoding="utf-8",
        )
    monkeypatch.setattr("voice_studio.app.save_settings", lambda _settings: None)

    preview = VoiceStudioApp._prepare_dictionary_import(app, incoming, "merge")
    assert preview.added_count == 1
    assert VoiceStudioApp._commit_dictionary_import(app, preview, "merge", confirmed=True) is True
    assert app.settings.dictionary_path == str(app.dictionary_repository.managed_path)
    assert [rule.source for rule in app.dictionary.rules] == ["old", "new"]

    conflicting = tmp_path / "conflict.json"
    conflicting.write_text(
        '{"replacements":[{"source":"old","target":"remote"}]}', encoding="utf-8"
    )
    before = app.dictionary_repository.managed_path.read_text(encoding="utf-8")
    preview = VoiceStudioApp._prepare_dictionary_import(app, conflicting, "merge")
    assert preview.conflicts
    assert VoiceStudioApp._commit_dictionary_import(app, preview, "merge", confirmed=True) is False
    assert app.dictionary_repository.managed_path.read_text(encoding="utf-8") == before


def test_replace_cancel_and_export_do_not_change_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches cancelled replacement or export unexpectedly switching the active dictionary."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "local"))
    original_settings = app.settings
    incoming = tmp_path / "incoming.json"
    incoming.write_text('{"replacements":[{"source":"new","target":"value"}]}', encoding="utf-8")
    preview = VoiceStudioApp._prepare_dictionary_import(app, incoming, "replace")
    assert (
        VoiceStudioApp._commit_dictionary_import(app, preview, "replace", confirmed=False) is False
    )
    assert app.settings == original_settings
    monkeypatch.setattr("voice_studio.app.save_settings", lambda _settings: None)
    target = tmp_path / "export.csv"
    assert VoiceStudioApp._export_dictionary(app, target) is True
    assert target.read_text(encoding="utf-8").startswith("source,target,")
    assert app.settings == original_settings


@pytest.mark.parametrize("choice, expected", [(True, True), (False, True), (None, False)])
def test_dirty_dictionary_navigation_save_discard_or_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: bool | None, expected: bool
) -> None:
    """Catches navigation that loses dirty rules or changes page after Cancel."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "new"))
    app._current_page = "dictionary"
    app._page_frames = {
        name: SimpleNamespace(grid=lambda: None, grid_remove=lambda: None)
        for name in ("dashboard", "dictionary")
    }
    app._page_buttons = {
        name: SimpleNamespace(configure=lambda **_kwargs: None) for name in app._page_frames
    }
    app.readiness_frame = SimpleNamespace(grid=lambda: None, grid_remove=lambda: None)
    app._apply_studio_layout = lambda *_args, **_kwargs: None
    app.winfo_width = lambda: 1000
    app._confirm_editor_transition = lambda: True
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: choice
    )
    monkeypatch.setattr("voice_studio.app.save_settings", lambda _settings: None)

    assert VoiceStudioApp._show_page(app, "dashboard") is expected
    assert app._current_page == ("dashboard" if expected else "dictionary")
    if choice is False:
        assert app.dictionary.rules == []
    if choice is True:
        assert app.dictionary_repository.managed_path.exists()


def test_settings_dictionary_source_change_discards_dirty_rules_then_reloads_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches Settings retaining editable managed state after choosing an external source."""

    external = tmp_path / "external.json"
    external.write_text('{"replacements":[{"source":"remote","target":"value"}]}', encoding="utf-8")
    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("local", "draft"))
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: False
    )
    saved: list[Settings] = []
    monkeypatch.setattr("voice_studio.app.save_settings", lambda settings: saved.append(settings))
    app._refresh_after_settings_save = lambda _language: None

    assert VoiceStudioApp._apply_settings_update(
        app, replace(app.settings, dictionary_path=str(external))
    )
    assert app.dictionary_read_only is True
    assert [rule.source for rule in app.dictionary.rules] == ["remote"]
    assert saved == [app.settings]


def test_settings_dictionary_source_change_cancel_keeps_current_settings_and_dirty_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches Settings Save proceeding after a user cancels the dictionary dirty prompt."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("local", "draft"))
    previous = app.settings
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: None
    )

    assert not VoiceStudioApp._apply_settings_update(
        app, replace(app.settings, dictionary_path="other.json")
    )
    assert app.settings == previous
    assert app._dictionary_dirty is True


@pytest.mark.parametrize("choice, expected", [(True, True), (False, True), (None, False)])
def test_close_obeys_dictionary_dirty_save_discard_or_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: bool | None, expected: bool
) -> None:
    """Catches window shutdown bypassing unsaved Dictionary edits."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "new"))
    app._closing = False
    app._maintenance_thread = None
    app._confirm_editor_transition = lambda: True
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: choice
    )
    monkeypatch.setattr("voice_studio.app.save_settings", lambda _settings: None)
    if expected:
        app.destroy = lambda: None
        app._shutdown_event = SimpleNamespace(set=lambda: None)
        app._worker_lock = __import__("threading").RLock()
        app._cancel_event = SimpleNamespace(set=lambda: None)
        app.hotkey = None
        app.recorder = SimpleNamespace(cancel=lambda: None)
        app.job_controller = SimpleNamespace(close=lambda: None)
        app._join_workers = lambda: ()
        app.status = SimpleNamespace(set=lambda _message: None)
    VoiceStudioApp._close(app)

    assert app._closing is expected


@pytest.mark.parametrize("contents", [None, "not json"])
def test_unavailable_or_invalid_external_reload_preserves_dirty_state(
    tmp_path: Path, contents: str | None
) -> None:
    """Catches an external load failure escaping a page callback or erasing draft rules."""

    external = tmp_path / "external.json"
    if contents is not None:
        external.write_text(contents, encoding="utf-8")
    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("draft", "value"))
    app.settings = replace(app.settings, dictionary_path=str(external))
    messages: list[str] = []
    app._dictionary_status = messages.append

    assert VoiceStudioApp._reload_dictionary(app) is False
    assert [rule.source for rule in app.dictionary.rules] == ["draft"]
    assert app._dictionary_dirty is True
    assert messages and "dictionary_load_error" in messages[-1]


def test_discard_does_not_leave_dictionary_when_external_reload_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches Discard claiming success after the configured external source disappears."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("draft", "value"))
    app.settings = replace(app.settings, dictionary_path=str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: False
    )

    assert VoiceStudioApp._confirm_dictionary_transition(app) is False
    assert app._dictionary_dirty is True


@pytest.mark.parametrize("suffix", [".txt", ".yaml"])
def test_import_and_export_reject_unsupported_suffixes(tmp_path: Path, suffix: str) -> None:
    """Catches unknown file extensions silently being treated as dictionary JSON."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    app._dictionary_status = lambda _message: None

    with pytest.raises(ValueError, match="unsupported dictionary format"):
        VoiceStudioApp._prepare_dictionary_import(app, tmp_path / f"input{suffix}", "merge")
    assert VoiceStudioApp._export_dictionary(app, tmp_path / f"output{suffix}") is False


def test_settings_persistence_failure_keeps_active_dictionary_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a failed Settings write changing the active source in memory."""

    external = tmp_path / "external.json"
    external.write_text('{"replacements":[]}', encoding="utf-8")
    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    previous = app.settings
    monkeypatch.setattr(
        "voice_studio.app.save_settings", lambda _settings: (_ for _ in ()).throw(OSError("disk"))
    )

    assert not VoiceStudioApp._apply_settings_update(
        app, replace(previous, dictionary_path=str(external))
    )
    assert app.settings == previous


def test_replace_import_requires_confirmation_then_replaces_managed_dictionary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches confirmed Replace retaining old rules instead of committing the validated input."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "local"))
    incoming = tmp_path / "replace.json"
    incoming.write_text('{"replacements":[{"source":"new","target":"remote"}]}', encoding="utf-8")
    preview = VoiceStudioApp._prepare_dictionary_import(app, incoming, "replace")
    monkeypatch.setattr("voice_studio.app.save_settings", lambda _settings: None)

    assert VoiceStudioApp._commit_dictionary_import(app, preview, "replace", confirmed=True)
    assert [rule.source for rule in app.dictionary.rules] == ["new"]


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs: str) -> None:
        self.text = kwargs["text"]


class _FakeTable:
    def __init__(self) -> None:
        self.headings: dict[str, str] = {}

    def heading(self, column: str, **kwargs: str) -> None:
        self.headings[column] = kwargs["text"]


def test_live_dictionary_language_refresh_updates_retained_controls() -> None:
    """Catches a language switch that leaves Dictionary headings and actions in the old locale."""

    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language="en")
    app._t = lambda key: {
        "dictionary_title": "Dictionary",
        "dictionary_detail": "detail",
        "search": "Search",
        "dictionary_test": "Test",
        "dictionary_source": "Source",
        "dictionary_add": "Add",
    }[key]
    app.dictionary_title_label = _FakeLabel()
    app.dictionary_detail_label = _FakeLabel()
    app.dictionary_search_button = _FakeLabel()
    app.dictionary_test_button = _FakeLabel()
    app.dictionary_table = _FakeTable()
    app._dictionary_heading_keys = {"source": "dictionary_source"}
    button = _FakeLabel()
    app._dictionary_button_keys = {button: "dictionary_add"}
    app._dictionary_refresh_widgets = lambda: None

    VoiceStudioApp._refresh_dictionary_ui_text(app)

    assert app.dictionary_table.headings == {"source": "Source"}
    assert button.text == "Add"


def test_export_dialog_uses_selected_csv_format_for_suffixless_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches the export dialog defaulting a user-selected CSV export to JSON."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    VoiceStudioApp._add_dictionary_rule(app, DictionaryRule("old", "new"))
    destination = tmp_path / "dictionary-export"
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        "voice_studio.app.filedialog.asksaveasfilename", lambda **_kwargs: str(destination)
    )

    VoiceStudioApp._dictionary_export_dialog(app)

    exported = destination.with_suffix(".csv")
    assert exported.exists()
    assert exported.read_text(encoding="utf-8").startswith("source,target,")


def test_export_dialog_cancel_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a cancelled export-format question opening a write path."""

    app = _app(tmp_path)
    VoiceStudioApp._reload_dictionary(app)
    monkeypatch.setattr(
        "voice_studio.app.messagebox.askyesnocancel", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "voice_studio.app.filedialog.asksaveasfilename",
        lambda **_kwargs: pytest.fail("file dialog must not open after format cancel"),
    )

    VoiceStudioApp._dictionary_export_dialog(app)

    assert not list(tmp_path.glob("*"))


def test_dictionary_transition_without_dictionary_state_is_clean_for_legacy_stubs() -> None:
    """Catches headless close stubs recursing through Tk when no Dictionary page was built."""

    app = object.__new__(VoiceStudioApp)

    assert VoiceStudioApp._confirm_dictionary_transition(app) is True
