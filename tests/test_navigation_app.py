from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.editor_state import snapshot_editor
from voice_studio.models import Transcript


class FakeFrame:
    def __init__(self) -> None:
        self.events: list[str] = []

    def grid(self) -> None:
        self.events.append("grid")

    def grid_remove(self) -> None:
        self.events.append("grid_remove")


class FakeButton:
    def __init__(self) -> None:
        self.styles: list[str] = []

    def configure(self, **kwargs: object) -> None:
        style = kwargs.get("style")
        if isinstance(style, str):
            self.styles.append(style)


class FakeEditor:
    def __init__(self, text: str) -> None:
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


class FakeReadonly:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def configure(self, **_kwargs: object) -> None:
        return None

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _start: str, text: str) -> None:
        self.text = text


def _page_app(current_page: str = "dashboard") -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app._current_page = current_page
    app._page_frames = {
        page: FakeFrame() for page in ("dashboard", "studio", "dictionary", "history")
    }
    app._page_buttons = {page: FakeButton() for page in app._page_frames}
    app.readiness_frame = FakeFrame()
    app._confirm_editor_transition = lambda: True
    app._apply_studio_layout = lambda *_args, **_kwargs: None
    app.winfo_width = lambda: 1200
    return app


def test_page_host_selects_dashboard_and_hides_the_readiness_card() -> None:
    """Catches a startup page host that still exposes the Studio-only readiness card."""

    app = _page_app()

    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert app._current_page == "dashboard"
    assert app._page_frames["dashboard"].events == ["grid"]
    assert app._page_frames["studio"].events == ["grid_remove"]
    assert app.readiness_frame.events == ["grid_remove"]
    assert app._page_buttons["dashboard"].styles == ["SidebarActive.TButton"]
    assert app._page_buttons["studio"].styles == ["Sidebar.TButton"]


def test_cancelled_leave_of_studio_preserves_page_and_sidebar_state() -> None:
    """Catches navigation that changes the selected page before the dirty-editor guard agrees."""

    app = _page_app("studio")
    app._confirm_editor_transition = lambda: False

    assert VoiceStudioApp._show_page(app, "history") is False
    assert app._current_page == "studio"
    assert all(frame.events == [] for frame in app._page_frames.values())
    assert all(button.styles == [] for button in app._page_buttons.values())
    assert app.readiness_frame.events == []


def test_discarding_studio_edits_restores_persisted_editor_before_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches discard navigation that leaves unsaved text in the editor."""

    app = _page_app("studio")
    transcript = Transcript(
        id="id-1",
        created_at="2026-09-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="faster-whisper",
        model="tiny",
        raw_text="raw immutable",
        corrected_text="saved text",
        metadata={"editor_formatting": {"bold": [("1.0", "1.5")]}},
    )
    app.current = transcript
    app.editor = FakeEditor("discard this edit")
    app.raw_editor = FakeReadonly("raw immutable")
    app.details = FakeReadonly()
    app.status = SimpleNamespace(set=lambda _value: None)
    app.settings = SimpleNamespace(auto_copy=False)
    app.confidence_panel_visible = False
    app._editor_baseline = snapshot_editor("saved text", {"bold": [("1.0", "1.5")]})
    app._confirm_editor_transition = VoiceStudioApp._confirm_editor_transition.__get__(app)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_args, **_kwargs: False)

    assert VoiceStudioApp._show_page(app, "history") is True
    assert app.editor.text == "saved text"
    assert app.editor.tags["bold"] == [("1.0", "1.5")]
    assert app._editor_is_dirty() is False
    assert app.current.raw_text == "raw immutable"


def test_discarding_no_current_studio_draft_clears_editor_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches discard navigation that leaves a no-transcript draft visible in Studio."""

    app = _page_app("studio")
    app.current = None
    app.editor = FakeEditor("discard this draft")
    app.editor.tags["bold"] = [("1.0", "1.7")]
    app.editor.tags["italic"] = [("1.8", "1.13")]
    app.raw_editor = FakeReadonly("stale raw")
    app.details = FakeReadonly("stale details")
    app._editor_baseline = snapshot_editor("", {})
    app._confirm_editor_transition = VoiceStudioApp._confirm_editor_transition.__get__(app)
    app._t = lambda key, **_values: key
    storage_calls: list[str] = []
    app.store = SimpleNamespace(update_editor_state=lambda *_args: storage_calls.append("write"))
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_args, **_kwargs: False)

    assert VoiceStudioApp._show_page(app, "history") is True
    assert VoiceStudioApp._show_page(app, "studio") is True
    assert app.editor.text == ""
    assert app.editor.tags == {"bold": [], "italic": []}
    assert app._editor_is_dirty() is False
    assert app.raw_editor.text == ""
    assert app.details.text == ""
    assert storage_calls == []


def test_opening_history_item_loads_it_then_navigates_to_studio() -> None:
    """Catches a history selection that loads a transcript without returning the user to Studio."""

    app = object.__new__(VoiceStudioApp)
    transcript = Transcript(
        id="id-1",
        created_at="2026-09-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="faster-whisper",
        model="tiny",
        raw_text="raw",
        corrected_text="edited",
    )
    app.history = SimpleNamespace(curselection=lambda: (0,))
    app._history_items = [transcript]
    app._page_frames = {}
    events: list[object] = []
    app._try_show_result = lambda item, **_kwargs: events.append(("load", item.id)) or True
    app._show_page = lambda page: events.append(("page", page)) or True

    VoiceStudioApp._select_history(app)

    assert events == [("load", "id-1"), ("page", "studio")]
