"""Behavioural contracts for the in-window Settings and Help pages."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp


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


class FakeHotkey:
    def __init__(self, stop_result: bool = True) -> None:
        self.stop_result = stop_result
        self.stop_calls = 0

    def stop(self) -> bool:
        self.stop_calls += 1
        return self.stop_result


def _page_app(current_page: str = "dashboard") -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app._current_page = current_page
    app._page_frames = {
        page: FakeFrame()
        for page in ("dashboard", "studio", "dictionary", "history", "settings", "help")
    }
    app._page_buttons = {page: FakeButton() for page in app._page_frames}
    app.readiness_frame = FakeFrame()
    app.statuses: list[str] = []
    app.status = SimpleNamespace(set=app.statuses.append)
    app._t = lambda key, **_values: key
    app._confirm_editor_transition = lambda: True
    app._apply_studio_layout = lambda *_args, **_kwargs: None
    app.winfo_width = lambda: 1200
    app._settings_variables = {}
    app._settings_baseline = {}
    app._settings_return_page = "dashboard"
    app.builds: list[str] = []
    app._build_settings_page = lambda: app.builds.append("settings")
    app._build_help_page = lambda: app.builds.append("help")
    app._leave_settings_page = lambda: app.builds.append("leave-settings")
    return app


def test_entering_settings_builds_the_page_and_remembers_the_previous_page() -> None:
    """Catches a Settings entry that keeps stale values or forgets where to return."""

    app = _page_app("history")

    assert VoiceStudioApp._show_page(app, "settings") is True
    assert app.builds == ["settings"]
    assert app._settings_return_page == "history"
    assert app._current_page == "settings"
    assert app._page_buttons["settings"].styles == ["SidebarActive.TButton"]


def test_a_running_job_blocks_the_settings_page_and_explains_why() -> None:
    """Catches a settings rebuild that races a running transcription."""

    app = _page_app("dashboard")
    app._busy = True

    assert VoiceStudioApp._show_page(app, "settings") is False
    assert app._current_page == "dashboard"
    assert app.builds == []
    assert app.statuses == ["task_already_running"]


def test_leaving_a_clean_settings_page_restarts_the_hotkey_without_a_prompt() -> None:
    """Catches a leave path that keeps the page bindings or asks about nothing."""

    app = _page_app("settings")

    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert app.builds == ["leave-settings"]
    assert app._current_page == "dashboard"


def test_building_the_settings_page_stops_the_global_hotkey() -> None:
    """Catches a page build that leaves the old shortcut armed while it is edited."""

    app = object.__new__(VoiceStudioApp)
    hotkey = FakeHotkey()
    app.hotkey = hotkey

    class Built(Exception):
        pass

    app.settings_page = SimpleNamespace(
        winfo_children=lambda: (_ for _ in ()).throw(Built()),
    )

    with pytest.raises(Built):
        VoiceStudioApp._build_settings_page(app)

    assert hotkey.stop_calls == 1
    assert app.hotkey is None


def test_reentering_settings_before_the_deferred_hotkey_restart_fires_cancels_it() -> None:
    """A quick re-entry to Settings must not let a stale after_idle restart the hotkey mid-edit."""

    app = object.__new__(VoiceStudioApp)
    hotkey = FakeHotkey()
    app.hotkey = hotkey
    app._hotkey_restart_handle = "pending-idle-1"
    cancelled: list[object] = []
    app.after_cancel = cancelled.append

    class Built(Exception):
        pass

    app.settings_page = SimpleNamespace(
        winfo_children=lambda: (_ for _ in ()).throw(Built()),
    )

    with pytest.raises(Built):
        VoiceStudioApp._build_settings_page(app)

    assert cancelled == ["pending-idle-1"]
    assert app._hotkey_restart_handle is None


def _dirty_settings_app(current_page: str = "settings") -> VoiceStudioApp:
    app = _page_app(current_page)
    app._settings_baseline = {"hotkey": "<f13>"}
    app._settings_variables = {"hotkey": SimpleNamespace(get=lambda: "<f14>")}
    app._confirm_settings_transition = VoiceStudioApp._confirm_settings_transition.__get__(app)
    return app


def test_unsaved_settings_can_be_saved_on_the_way_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a Yes answer that leaves the edited settings unsaved."""

    app = _dirty_settings_app()
    saves: list[str] = []
    app._settings_save = lambda: saves.append("save") or True
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: True)

    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert saves == ["save"]
    assert app._current_page == "dashboard"


def test_a_failed_save_on_the_way_out_keeps_the_user_on_the_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches navigation that discards rejected settings instead of staying put."""

    app = _dirty_settings_app()
    app._settings_save = lambda: False
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: True)

    assert VoiceStudioApp._show_page(app, "dashboard") is False
    assert app._current_page == "settings"
    assert app.builds == []


def test_discarding_unsaved_settings_rebuilds_the_page_before_leaving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a discard that keeps the edited values alive on the next visit."""

    app = _dirty_settings_app()
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: False)

    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert app.builds == ["settings", "leave-settings"]
    assert app._current_page == "dashboard"


def test_cancelling_the_unsaved_prompt_keeps_the_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches navigation that leaves Settings while the user asked to stay."""

    app = _dirty_settings_app()
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: None)

    assert VoiceStudioApp._show_page(app, "dashboard") is False
    assert app._current_page == "settings"
    assert app.builds == []


def test_a_saved_settings_page_is_clean_again() -> None:
    """Catches a save that keeps the old baseline and re-prompts on the way out."""

    app = _page_app("settings")
    variable = SimpleNamespace(get=lambda: "<f14>")
    app._settings_variables = {"hotkey": variable}
    app._settings_baseline = {"hotkey": "<f13>"}

    assert VoiceStudioApp._settings_page_is_dirty(app) is True

    app._settings_baseline = {"hotkey": "<f14>"}

    assert VoiceStudioApp._settings_page_is_dirty(app) is False


def test_help_is_built_on_entry_and_reused_on_the_next_visit() -> None:
    """Catches a Help page that rebuilds its topic tree on every navigation."""

    app = _page_app("dashboard")
    builds: list[str] = []
    app._help_page_built = False

    def build_help() -> None:
        if app._help_page_built:
            return
        builds.append("build")
        app._help_page_built = True

    app._build_help_page = build_help

    assert VoiceStudioApp._show_page(app, "help") is True
    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert VoiceStudioApp._show_page(app, "help") is True
    assert builds == ["build"]

    app._help_images = []
    app.help_page = SimpleNamespace(winfo_children=lambda: [])
    VoiceStudioApp._reset_help_page(app)
    build_help()

    assert builds == ["build", "build"]


def test_close_is_blocked_by_unsaved_settings_on_the_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches window shutdown bypassing unsaved Settings edits."""

    app = _dirty_settings_app()
    app._closing = False
    app._maintenance_thread = None
    app._confirm_dictionary_transition = lambda: True
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: None)

    VoiceStudioApp._close(app)

    assert app._closing is False


def test_close_proceeds_once_unsaved_settings_are_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _dirty_settings_app()
    app._closing = False
    app._maintenance_thread = None
    app._confirm_dictionary_transition = lambda: True
    app._settings_save = lambda: True
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *_a, **_k: True)
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

    assert app._closing is True


def test_the_help_shortcut_opens_the_central_help_page() -> None:
    """Catches an F1 binding that still reaches for a separate Help window."""

    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert 'self.bind_all("<F1>", lambda _event: self._show_page("help"), add="+")' in source
    assert 'command=lambda: self._show_page("settings")' in source
    assert 'command=lambda: self._show_page("help")' in source
    assert '"settings": self.settings_button' in source
    assert '"help": self.help_button' in source
