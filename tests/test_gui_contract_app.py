"""Contracts for the Tk GUI layer.

Two kinds of test live here, and the difference matters when reading a green
run.

The first kind exercises real behaviour: the method is called against fakes and
the observable effect is asserted. Prefer this always.

The second kind asserts on the *source* of `_build_ui` and the dialog builders.
Those methods do nothing but construct Tk widgets, so there is no observable
effect to assert without a display and a real widget tree, and a headless
process cannot build one. These tests therefore prove only that a widget is
declared — not that it is packed, bound, or reachable by a user. They are a
tripwire against silent removal, not evidence that the GUI works. The physical
Windows and macOS acceptance scope recorded in `VERIFICATION.md` is what
covers that, and it is still NOT RUN.

Do not add new source-substring tests for anything that can be called. Editor
transition guards, for example, were once asserted here as strings and are now
covered properly in `tests/test_editor_state_app.py`.
"""

from __future__ import annotations

import inspect
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_studio import app as app_module
from voice_studio import backup as backup_module
from voice_studio.app import (
    VOICE_STUDIO_THEME,
    StudioLayout,
    VoiceStudioApp,
    initial_window_size,
    studio_content_metrics,
    studio_icon_pixel,
    studio_layout_for_width,
)
from voice_studio.hardware import HardwareDetectionResult
from voice_studio.models import Transcript
from voice_studio.storage import LocalStore

# --- behavioural ------------------------------------------------------------


def test_studio_layout_matches_the_approved_sidebar_breakpoint() -> None:
    assert studio_layout_for_width(1320) == StudioLayout(250, False, True)
    assert studio_layout_for_width(1200) == StudioLayout(250, False, True)
    assert studio_layout_for_width(1040) == StudioLayout(250, False, True)
    assert studio_layout_for_width(1039) == StudioLayout(250, False, False)
    assert studio_layout_for_width(900) == StudioLayout(250, False, False)
    assert studio_layout_for_width(759) == StudioLayout(88, True, False)


def test_initial_window_fits_the_current_screen_without_narrowing_the_sidebar() -> None:
    assert initial_window_size(1073, 852) == (1057, 820)
    assert initial_window_size(1920, 1080) == (1320, 820)


def test_reference_width_uses_compact_content_spacing_not_a_narrow_menu() -> None:
    assert studio_content_metrics(1057) == ((16, 18, 16, 20), 12, 340)
    assert studio_content_metrics(1320) == ((28, 22, 28, 24), 18, 560)


def test_voice_studio_theme_matches_approved_cream_reference() -> None:
    assert VOICE_STUDIO_THEME.canvas == "#f6eddc"
    assert VOICE_STUDIO_THEME.surface == "#fffaf1"
    assert VOICE_STUDIO_THEME.accent == "#e99016"
    assert VOICE_STUDIO_THEME.ink == "#2a2119"
    assert VOICE_STUDIO_THEME.primary == "#5b4332"
    assert VOICE_STUDIO_THEME.ui_font == "Bahnschrift"
    assert VOICE_STUDIO_THEME.mono_font == "Cascadia Mono"


def test_configured_theme_consumes_the_approved_theme_contract() -> None:
    source = inspect.getsource(VoiceStudioApp._configure_theme)

    assert "VOICE_STUDIO_THEME" in source
    assert 'background="#172641"' not in source
    assert 'background="#315eae"' not in source
    assert 'foreground=[("readonly", theme.ink)' in source


def test_reference_layout_uses_the_narrow_readiness_panel() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert "width=214" in source


def test_toolbar_uses_compact_reference_actions_to_avoid_label_clipping() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert source.count('style="CompactAction.TButton"') == 3


def test_brand_mark_uses_the_approved_rounded_vo_canvas() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert "self.brand_mark = tk.Canvas" in source
    assert "self.brand_mark.create_polygon" in source
    assert 'text="VO"' in source


def test_workspace_subtitle_wraps_beside_the_file_action() -> None:
    source = inspect.getsource(VoiceStudioApp._build_ui)

    assert "wraplength=560" in source


def test_full_sidebar_uses_reference_bullets_and_keeps_compact_glyphs() -> None:
    source = inspect.getsource(VoiceStudioApp._apply_studio_layout)

    assert 'symbol if layout.compact_sidebar else f"●  {self._t(key)}"' in source


def test_studio_icon_mask_forms_a_rounded_accent_square() -> None:
    assert studio_icon_pixel(0, 0) is False
    assert studio_icon_pixel(8, 0) is True
    assert studio_icon_pixel(16, 16) is True
    assert studio_icon_pixel(31, 31) is False


def test_leaving_the_settings_page_releases_its_bindings_before_the_global_hotkey() -> None:
    """The native listener must start only after the page capture binding is gone.

    Restarting the global hotkey while the settings page still captures key
    presses lets the old shortcut fire into a page that is being left.
    """

    app = object.__new__(VoiceStudioApp)
    order: list[str] = []
    scheduled: list[object] = []
    app._settings_ollama_combo = object()
    app._settings_hardware_device_combo = object()
    app._settings_hardware_compute_combo = object()
    app._settings_info_var = object()
    app._settings_ollama_status_var = object()
    app._settings_capture_binding = "capture-1"
    app.unbind = lambda sequence, funcid: order.append(f"unbind:{sequence}:{funcid}")
    app.after_idle = lambda callback: (
        order.append("after_idle"),
        scheduled.append(callback),
    )

    VoiceStudioApp._leave_settings_page(app)

    assert order == ["unbind:<KeyPress>:capture-1", "after_idle"]
    assert scheduled == [app._start_hotkey], "the real hotkey starter must be the deferred call"
    assert app._settings_capture_binding is None
    assert app._settings_ollama_combo is None


def test_leaving_settings_records_the_deferred_hotkey_restart_handle() -> None:
    """The handle must be kept so a quick re-entry to Settings can cancel it."""

    app = object.__new__(VoiceStudioApp)
    app._settings_ollama_combo = None
    app._settings_hardware_device_combo = None
    app._settings_hardware_compute_combo = None
    app._settings_info_var = None
    app._settings_ollama_status_var = None
    app._settings_capture_binding = None
    app.after_idle = lambda _callback: "idle-handle-42"

    VoiceStudioApp._leave_settings_page(app)

    assert app._hotkey_restart_handle == "idle-handle-42"
    assert app._settings_info_var is None
    assert app._settings_ollama_status_var is None


class _FakeHotkey:
    def __init__(self, stop_outcomes: list[bool]) -> None:
        self.stop_outcomes = stop_outcomes
        self.stop_calls = 0
        self.start_calls = 0

    def stop(self) -> bool:
        self.stop_calls += 1
        return self.stop_outcomes.pop(0)

    def start(self) -> None:
        self.start_calls += 1


def _hotkey_app_stub(hotkey: object) -> tuple[VoiceStudioApp, list[str]]:
    app = object.__new__(VoiceStudioApp)
    statuses: list[str] = []
    app.hotkey = hotkey
    app.settings = SimpleNamespace(hotkey="<f13>")
    app.status = SimpleNamespace(set=statuses.append)
    app._t = lambda key, **values: f"{key}:{values.get('error', '')}"
    return app, statuses


def test_start_hotkey_retains_stubborn_listener_without_double_registering(monkeypatch) -> None:
    existing = _FakeHotkey([False, True])
    app, statuses = _hotkey_app_stub(existing)
    constructed: list[_FakeHotkey] = []

    def construct(*_args: object, **_kwargs: object) -> _FakeHotkey:
        replacement = _FakeHotkey([])
        constructed.append(replacement)
        return replacement

    monkeypatch.setattr(app_module, "GlobalHotkey", construct)

    VoiceStudioApp._start_hotkey(app)

    assert app.hotkey is existing
    assert existing.stop_calls == 1
    assert constructed == []
    assert statuses == ["hotkey_unavailable:hotkey_stop_retry:"]

    VoiceStudioApp._start_hotkey(app)

    assert existing.stop_calls == 2
    assert len(constructed) == 1
    assert app.hotkey is constructed[0]
    assert constructed[0].start_calls == 1


@pytest.mark.parametrize(
    ("language", "expected_detail"),
    [
        ("uk", "не зупинився"),
        ("cs", "nezastavil"),
        ("en", "did not stop"),
    ],
)
def test_start_hotkey_reports_localized_stubborn_listener_detail(
    language: str, expected_detail: str
) -> None:
    existing = _FakeHotkey([False])
    app = object.__new__(VoiceStudioApp)
    statuses: list[str] = []
    app.hotkey = existing
    app.settings = SimpleNamespace(hotkey="<f13>", ui_language=language)
    app.status = SimpleNamespace(set=statuses.append)
    app._t = lambda key, **values: app_module.translate(language, key, **values)

    VoiceStudioApp._start_hotkey(app)

    assert statuses
    assert expected_detail in statuses[0]
    assert app_module.translate(language, "hotkey_stop_retry") in statuses[0]


class _PageBuildStarted(Exception):
    pass


def _settings_page_stub(app: VoiceStudioApp, on_build: object = None) -> None:
    def winfo_children() -> list[object]:
        if callable(on_build):
            on_build()
        raise _PageBuildStarted()

    app.settings_page = SimpleNamespace(winfo_children=winfo_children)


def test_settings_page_retains_stubborn_listener_before_building_the_page() -> None:
    existing = _FakeHotkey([False])
    app, _statuses = _hotkey_app_stub(existing)
    _settings_page_stub(app)

    with pytest.raises(_PageBuildStarted):
        VoiceStudioApp._build_settings_page(app)

    assert existing.stop_calls == 1
    assert app.hotkey is existing


def test_settings_page_clears_stopped_listener_before_building_the_page() -> None:
    existing = _FakeHotkey([True])
    app, _statuses = _hotkey_app_stub(existing)
    _settings_page_stub(app, lambda: None if app.hotkey is None else pytest.fail("listener kept"))

    with pytest.raises(_PageBuildStarted):
        VoiceStudioApp._build_settings_page(app)

    assert existing.stop_calls == 1
    assert app.hotkey is None


def test_startup_does_not_schedule_a_first_run_model_prompt() -> None:
    source = inspect.getsource(VoiceStudioApp.__init__)

    assert "_first_run_model_prompt" not in source


def test_startup_settles_model_catalog_after_restore_report_before_history() -> None:
    source = inspect.getsource(VoiceStudioApp.__init__)
    restore = source.index("self._report_restore_recovery()")
    models = source.index("self._settle_model_catalog()")
    history = source.index("self._refresh_history()")
    assert source.count("self._settle_model_catalog()") == 1
    assert restore < models < history
    assert "_first_run_model_prompt" not in source


def test_model_catalog_repair_reaches_status_line(monkeypatch) -> None:
    recorded: list[str] = []
    stub = SimpleNamespace(
        store=SimpleNamespace(models=Path("models")),
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: f"{key}:{values}",
        after=lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module.ModelCatalog,
        "reconcile",
        lambda _self: {
            "status": "PASS", "action": "repaired", "adopted": ["tiny"],
            "dropped": [], "blocked": [], "catalog_quarantined": None,
        },
    )
    result = VoiceStudioApp._settle_model_catalog.__get__(stub)()
    assert result["action"] == "repaired"
    assert recorded and recorded[0].startswith("model_catalog_repaired")


def test_model_catalog_failure_does_not_abort_startup(monkeypatch) -> None:
    stub = SimpleNamespace(
        store=SimpleNamespace(models=Path("models")),
        status=SimpleNamespace(set=lambda _value: None),
        _t=lambda key, **values: f"{key}:{values.get('error', '')}",
        after=lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module.ModelCatalog,
        "reconcile",
        lambda _self: (_ for _ in ()).throw(OSError("disk")),
    )
    result = VoiceStudioApp._settle_model_catalog.__get__(stub)()
    assert result == {"status": "FAIL", "action": "attention", "error": "disk"}


@pytest.mark.parametrize(
    ("outcome", "reconcile_result", "expected_key", "warning", "needle"),
    [
        (
            "healthy",
            {"status": "PASS", "action": "none"},
            None,
            False,
            None,
        ),
        (
            "repaired",
            {
                "status": "PASS",
                "action": "repaired",
                "adopted": ["tiny"],
                "dropped": ["missing"],
                "catalog_quarantined": None,
            },
            "model_catalog_repaired",
            False,
            "tiny",
        ),
        (
            "attention",
            {
                "status": "PASS",
                "action": "attention",
                "blocked": [{"id": "broken", "reason": "incomplete"}],
                "catalog_quarantined": None,
            },
            "model_catalog_attention",
            True,
            "broken",
        ),
        (
            "quarantine",
            {
                "status": "PASS",
                "action": "repaired",
                "catalog_quarantined": "catalog.json.corrupt-1",
            },
            "model_catalog_rebuilt",
            False,
            "catalog.json.corrupt-1",
        ),
        (
            "attention with quarantine",
            {
                "status": "PASS",
                "action": "attention",
                "blocked": [{"id": "broken", "reason": "incomplete"}],
                "catalog_quarantined": "catalog.json.corrupt-2",
            },
            "model_catalog_rebuilt",
            True,
            "catalog.json.corrupt-2",
        ),
        (
            "failure",
            {"status": "FAIL", "action": "attention", "error": "disk"},
            "model_catalog_repair_failed",
            True,
            "disk",
        ),
    ],
)
def test_model_catalog_startup_reports_every_outcome(
    monkeypatch,
    outcome,
    reconcile_result,
    expected_key,
    warning,
    needle,
) -> None:
    recorded: list[str] = []
    scheduled: list[object] = []
    shown: list[tuple[str, str]] = []
    stub = SimpleNamespace(
        store=SimpleNamespace(models=Path("models")),
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: f"{key}:{values}",
        after=lambda _delay, callback: scheduled.append(callback),
    )
    monkeypatch.setattr(
        app_module.ModelCatalog,
        "reconcile",
        lambda _self: reconcile_result,
    )
    monkeypatch.setattr(
        app_module.messagebox,
        "showwarning",
        lambda title, message, **_kwargs: shown.append((title, message)),
    )

    result = VoiceStudioApp._settle_model_catalog.__get__(stub)()

    assert result == reconcile_result
    if expected_key is None:
        assert recorded == []
        assert scheduled == []
        assert shown == []
        return
    assert recorded and recorded[0].startswith(expected_key)
    assert len(scheduled) == (1 if warning else 0), outcome
    if warning:
        scheduled[0]()
        assert len(shown) == 1
        assert needle in shown[0][1]
    else:
        assert shown == []


def test_close_is_blocked_while_backup_or_restore_is_running(monkeypatch) -> None:
    app = object.__new__(VoiceStudioApp)
    app._confirm_editor_transition = lambda: True
    app._maintenance_thread = SimpleNamespace(is_alive=lambda: True)
    app._t = lambda _key: "Резервна копія виконується"
    app.destroy = lambda: pytest.fail("the window must stay alive during maintenance")
    warnings: list[str] = []
    monkeypatch.setattr(
        "voice_studio.app.messagebox.showwarning",
        lambda _title, message, **_kwargs: warnings.append(message),
    )

    VoiceStudioApp._close(app)

    assert warnings and "резерв" in warnings[0].lower()


def _worker_registry_stub() -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app._shutdown_event = threading.Event()
    app._worker_lock = threading.RLock()
    app._worker_threads = {}
    app._shutdown_residue_threads = ()
    app._cancel_event = threading.Event()
    app.events = queue.Queue()
    return app


def test_worker_registry_registers_and_removes_only_its_current_handle() -> None:
    app = _worker_registry_stub()
    entered = threading.Event()
    release = threading.Event()

    def work() -> None:
        entered.set()
        assert release.wait(1)

    thread = VoiceStudioApp._start_worker(app, "probe", work)

    assert thread is not None
    assert thread.name == "voice-studio-probe"
    assert entered.wait(1)
    assert app._worker_threads["probe"] is thread
    release.set()
    thread.join(1)
    assert not thread.is_alive()
    assert "probe" not in app._worker_threads


def test_worker_registry_rejects_new_work_after_shutdown() -> None:
    app = _worker_registry_stub()
    app._shutdown_event.set()

    assert VoiceStudioApp._start_worker(app, "late", lambda: None) is None
    assert app._worker_threads == {}


def test_immediate_discovery_worker_cannot_leave_a_dead_alias(monkeypatch) -> None:
    app = _worker_registry_stub()
    app._ollama_discovery_thread = None

    class ImmediateThread:
        def __init__(self, *, target, daemon, name) -> None:
            self._target = target
            self.daemon = daemon
            self.name = name
            self._alive = False

        def start(self) -> None:
            self._alive = True
            self._target()
            self._alive = False

        def is_alive(self) -> bool:
            return self._alive

    monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        app_module, "discover_ollama_model_catalog", lambda: {"audio": [], "all": []}
    )

    VoiceStudioApp._start_ollama_model_discovery(app)

    assert app._ollama_discovery_thread is None
    assert app._worker_threads == {}


def test_immediate_maintenance_worker_cannot_leave_a_dead_alias() -> None:
    app = _worker_registry_stub()
    app._maintenance_thread = None

    class ImmediateThread:
        def __init__(self, *, target, daemon, name) -> None:
            self._target = target
            self.daemon = daemon
            self.name = name
            self._alive = False

        def start(self) -> None:
            self._alive = True
            self._target()
            self._alive = False

        def is_alive(self) -> bool:
            return self._alive

    original_thread = app_module.threading.Thread
    app_module.threading.Thread = ImmediateThread
    try:
        thread = VoiceStudioApp._start_worker(app, "maintenance", lambda: None, daemon=False)
        VoiceStudioApp._assign_worker_alias(app, "maintenance", thread, "_maintenance_thread")
    finally:
        app_module.threading.Thread = original_thread

    assert app._maintenance_thread is None
    assert app._worker_threads == {}


@pytest.mark.parametrize(
    ("role", "attribute"),
    [
        ("maintenance", "_maintenance_thread"),
        ("ollama-model-discovery", "_ollama_discovery_thread"),
    ],
)
def test_live_worker_alias_is_assigned_and_cleared_by_identity(role: str, attribute: str) -> None:
    app = _worker_registry_stub()
    setattr(app, attribute, None)
    entered = threading.Event()
    release = threading.Event()

    def work() -> None:
        entered.set()
        assert release.wait(1)

    thread = VoiceStudioApp._start_worker(app, role, work, daemon=role != "maintenance")
    VoiceStudioApp._assign_worker_alias(app, role, thread, attribute)

    assert entered.wait(1)
    assert getattr(app, attribute) is thread
    release.set()
    thread.join(1)
    assert getattr(app, attribute) is None


def test_post_event_drops_events_after_shutdown() -> None:
    app = _worker_registry_stub()
    app.events = queue.Queue()

    assert VoiceStudioApp._post_event(app, "before", 1)
    assert app.events.get_nowait() == ("before", 1)
    app._shutdown_event.set()
    assert not VoiceStudioApp._post_event(app, "after", 2)
    with pytest.raises(queue.Empty):
        app.events.get_nowait()


def test_join_workers_uses_finite_shared_deadline_and_reports_live_roles() -> None:
    app = _worker_registry_stub()

    class LiveThread:
        def __init__(self) -> None:
            self.joins: list[float] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float) -> None:
            self.joins.append(timeout)

    first = LiveThread()
    second = LiveThread()
    app._worker_threads = {"first": first, "second": second}

    residue = VoiceStudioApp._join_workers(app, timeout_seconds=0.05)

    assert residue == ("first", "second")
    assert first.joins and second.joins
    assert all(0 < timeout <= 0.05 for timeout in first.joins + second.joins)


def test_poll_events_does_not_schedule_after_shutdown() -> None:
    app = _worker_registry_stub()
    app.events = queue.Queue()
    app.after = lambda *_args: pytest.fail("shutdown must not reschedule event polling")
    app._shutdown_event.set()

    VoiceStudioApp._poll_events(app)


def test_background_worker_families_use_registry_and_event_gate() -> None:
    source = inspect.getsource(VoiceStudioApp)
    for method in (
        "_start_ollama_model_discovery",
        "_process",
        "_ai_cleanup",
        "_start_backup_operation",
    ):
        body = inspect.getsource(getattr(VoiceStudioApp, method))
        assert "_start_worker" in body, method
        assert "self.events.put(" not in body, method
    # Backup dialog workers go through the shared operation runner.
    assert "_start_backup_operation" in inspect.getsource(VoiceStudioApp._backup_dialog)
    assert "_post_event" in source


def test_close_cancels_producers_joins_residues_and_destroys_once(monkeypatch) -> None:
    app = _worker_registry_stub()
    app._closing = False
    app._confirm_editor_transition = lambda: True
    order: list[str] = []
    app.hotkey = _FakeHotkey([False])
    app.recorder = SimpleNamespace(
        cancel=lambda: (_ for _ in ()).throw(
            TimeoutError("audio recorder writer did not stop within 2.0 seconds")
        )
    )
    app.job_controller = SimpleNamespace(close=lambda: order.append("controller"))
    app._active_recording_path = None
    app._pending_microphone_files = set()
    app._t = lambda key, **values: key
    app.destroy = lambda: order.append("destroy")
    app._retain_unresolved_recorder_path = lambda _path: None
    app._report_recorder_error = lambda _error: order.append("recorder")
    app._report_recording_residues = lambda: order.append("residues")

    def join_workers() -> tuple[str, ...]:
        order.append("join")
        return ("transcription",)

    monkeypatch.setattr(VoiceStudioApp, "_join_workers", lambda _self: join_workers())

    VoiceStudioApp._close(app)
    VoiceStudioApp._close(app)

    assert app._closing is True
    assert app._shutdown_event.is_set()
    assert app._cancel_event.is_set()
    assert app._shutdown_residue_threads == ("global-hotkey", "transcription")
    assert order.count("controller") == 1
    assert order.count("destroy") == 1


def test_close_remains_blocked_by_non_daemon_maintenance_before_shutdown() -> None:
    app = _worker_registry_stub()
    app._closing = False
    app._maintenance_thread = SimpleNamespace(is_alive=lambda: True)
    app._t = lambda _key: "wait"
    app._confirm_editor_transition = lambda: pytest.fail("editor gate must not run")
    app.destroy = lambda: pytest.fail("maintenance keeps the window alive")
    warnings: list[str] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "voice_studio.app.messagebox.showwarning",
            lambda _title, message, **_kwargs: warnings.append(message),
        )
        VoiceStudioApp._close(app)

    assert warnings == ["wait"]
    assert not app._shutdown_event.is_set()
    assert app._closing is False


# --- static widget-construction tripwires -----------------------------------
# See the module docstring: these prove declaration, not behaviour.


def test_backup_ui_is_declared_with_async_work_and_reversible_restore() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    backup_dialog = inspect.getsource(VoiceStudioApp._backup_dialog)
    create_flow = inspect.getsource(VoiceStudioApp._queue_backup_create)
    event_handler = inspect.getsource(VoiceStudioApp._poll_events)

    assert 'self._t("backup")' in build_ui
    assert "threading.Thread" in backup_dialog
    assert "_queue_backup_create" in backup_dialog
    assert "create_backup" in create_flow
    assert "verify_backup" in backup_dialog
    assert "restore_backup" in backup_dialog
    assert 'self._t("restore_backup_prompt")' in backup_dialog
    assert 'event == "backup_done"' in event_handler


def test_history_actions_continuous_recording_and_hotkey_capture_are_declared() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    settings_page = inspect.getsource(VoiceStudioApp._build_settings_page)

    assert 'self._t("continuous_record")' in build_ui
    assert 'self._t("rename")' in build_ui
    assert 'self._t("delete")' in build_ui
    assert 'self._t("capture_hotkey")' in settings_page
    assert "hotkey_from_tk_event" in settings_page


def test_settings_page_declares_all_three_reusable_engine_profiles() -> None:
    settings_page = inspect.getsource(VoiceStudioApp._build_settings_page)

    assert '"ollama-local"' in settings_page
    assert '"whisper-local"' in settings_page
    assert '"openai-cloud"' in settings_page


class _FakeEnginePage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def grid(self, **_kwargs) -> None:
        self.calls.append("grid")

    def grid_remove(self) -> None:
        self.calls.append("grid_remove")

    @property
    def visible(self) -> bool:
        return bool(self.calls) and self.calls[-1] == "grid"


def _engine_page_switch(profiles: list[str]) -> dict[str, _FakeEnginePage]:
    pages = {profile: _FakeEnginePage() for profile in profiles}

    def show_engine_page(profile: str) -> None:
        for name, page in pages.items():
            if name == profile:
                page.grid()
            else:
                page.grid_remove()

    for profile in profiles:
        show_engine_page(profile)
    return pages


def test_the_settings_page_switches_engine_pages_with_the_chosen_profile() -> None:
    settings_page = inspect.getsource(VoiceStudioApp._build_settings_page)

    assert "engine_pages" in settings_page
    assert "def show_engine_page" in settings_page
    assert "show_engine_page(preset.profile)" in settings_page
    assert 'show_engine_page(str(variables["profile"].get()))' in settings_page
    assert "local_ai_page" not in settings_page
    assert 'self._t("local_ai_settings")' not in settings_page
    for profile, needle in (
        ("ollama-local", 'self._t("ollama_model")'),
        ("whisper-local", 'self._t("compute_type")'),
        ("openai-cloud", 'self._t("openai_cleanup_model")'),
    ):
        assert f'engine_pages["{profile}"]' in settings_page
        assert needle in settings_page


def test_exactly_one_engine_page_is_gridded_for_the_selected_profile() -> None:
    pages = _engine_page_switch(["ollama-local", "whisper-local", "openai-cloud"])

    assert [name for name, page in pages.items() if page.visible] == ["openai-cloud"]
    assert pages["ollama-local"].calls == ["grid", "grid_remove", "grid_remove"]
    assert pages["whisper-local"].calls == ["grid_remove", "grid", "grid_remove"]


class _FakeOllamaModelVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _ollama_event_stub(settings, *, ollama_model_variable: str | None = "") -> VoiceStudioApp:
    app = _worker_registry_stub()
    app.settings = settings
    app._installed_ollama_audio_models = []
    app._installed_ollama_all_models = []
    app._ollama_discovery_error = ""
    app._t = lambda key, **values: f"{key}:{values}" if values else key
    app.after = lambda *_args: None
    app._settings_hardware_device_combo = None
    app._settings_hardware_compute_combo = None
    app._settings_info_var = SimpleNamespace(
        values=[],
        set=lambda value: app._settings_info_var.values.append(value),
    )
    app._settings_ollama_status_var = SimpleNamespace(
        values=[],
        set=lambda value: app._settings_ollama_status_var.values.append(value),
    )
    app._settings_baseline = {}
    app._settings_variables = {}
    if ollama_model_variable is not None:
        variable = _FakeOllamaModelVar(ollama_model_variable)
        app._settings_variables["ollama_model"] = variable
        app._settings_baseline["ollama_model"] = ollama_model_variable
    else:
        variable = None

    class Combo:
        def __init__(self, linked_variable=None):
            self.values = ()
            self.selected = ""
            self._linked_variable = linked_variable

        def winfo_exists(self):
            return True

        def configure(self, **kwargs):
            self.values = kwargs["values"]

        def set(self, value):
            self.selected = value
            if self._linked_variable is not None:
                self._linked_variable.set(value)

    app._settings_ollama_combo = Combo(variable)
    return app


def test_installed_models_without_audio_capability_are_still_offered_with_a_warning() -> None:
    app = _ollama_event_stub(app_module.Settings(ollama_model=""))
    app.events.put(
        ("ollama_models", {"models": [], "all_models": ["llama4:latest"], "error": ""})
    )

    VoiceStudioApp._poll_events(app)

    assert app._installed_ollama_audio_models == []
    assert app._installed_ollama_all_models == ["llama4:latest"]
    assert app.settings.ollama_model == ""
    assert app._settings_ollama_combo.values == ("llama4:latest",)
    assert app._settings_info_var.values == ["ollama_no_audio_models"]
    assert app._settings_ollama_status_var.values == ["ollama_no_audio_models"]


def test_audio_capable_models_are_preferred_over_the_full_installed_list() -> None:
    app = _ollama_event_stub(app_module.Settings(ollama_model="gemma4:12b"))
    app.events.put(
        (
            "ollama_models",
            {
                "models": ["gemma4:12b"],
                "all_models": ["llama4:latest", "gemma4:12b"],
                "error": "",
            },
        )
    )

    VoiceStudioApp._poll_events(app)

    assert app._settings_ollama_combo.values == ("gemma4:12b",)
    assert app._settings_ollama_combo.selected == "gemma4:12b"
    assert app._settings_info_var.values == ["ollama_found:{'count': 1}"]
    assert app._settings_ollama_status_var.values == ["ollama_found:{'count': 1}"]


def test_an_unreachable_ollama_still_reports_its_error_in_the_settings_status() -> None:
    app = _ollama_event_stub(app_module.Settings(ollama_model=""))
    app.events.put(
        (
            "ollama_models",
            {"models": [], "all_models": [], "error": "connection refused"},
        )
    )

    VoiceStudioApp._poll_events(app)

    assert app._settings_info_var.values == ["connection refused"]
    assert app._settings_ollama_status_var.values == ["connection refused"]
    assert app._settings_ollama_combo.values == ()


def test_a_user_edited_ollama_model_field_survives_a_discovery_event() -> None:
    """A running discovery must not clobber a choice the user is mid-editing."""

    app = _ollama_event_stub(
        app_module.Settings(ollama_model="gemma4:12b"),
        ollama_model_variable="llama4:custom",
    )
    app._settings_baseline["ollama_model"] = "gemma4:12b"
    app.events.put(
        (
            "ollama_models",
            {"models": ["gemma4:12b"], "all_models": ["gemma4:12b"], "error": ""},
        )
    )

    VoiceStudioApp._poll_events(app)

    assert app._settings_ollama_combo.selected == ""
    assert app._settings_variables["ollama_model"].get() == "llama4:custom"
    assert app._settings_baseline["ollama_model"] == "gemma4:12b"
    assert VoiceStudioApp._settings_page_is_dirty(app) is True


def test_an_untouched_ollama_model_field_adopts_the_auto_picked_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page the user has not touched should keep reflecting the discovered model."""

    app = _ollama_event_stub(app_module.Settings(ollama_model=""), ollama_model_variable="")
    monkeypatch.setattr(app_module, "save_settings", lambda _settings: None)
    app._refresh_ui_text = lambda: None
    app.events.put(
        (
            "ollama_models",
            {"models": ["gemma4:12b"], "all_models": ["gemma4:12b"], "error": ""},
        )
    )

    VoiceStudioApp._poll_events(app)

    assert app.settings.ollama_model == "gemma4:12b"
    assert app._settings_ollama_combo.selected == "gemma4:12b"
    assert app._settings_variables["ollama_model"].get() == "gemma4:12b"
    assert app._settings_baseline["ollama_model"] == "gemma4:12b"
    assert VoiceStudioApp._settings_page_is_dirty(app) is False


def test_settings_hardware_controls_are_readonly_and_detection_is_explicit() -> None:
    settings_page = inspect.getsource(VoiceStudioApp._build_settings_page)

    assert "SUPPORTED_DEVICES" in settings_page
    assert "SUPPORTED_COMPUTE_TYPES" in settings_page
    assert settings_page.count('state="readonly"') >= 4
    assert 'text=self._t("hardware_detect")' in settings_page
    assert "_start_hardware_detection" in settings_page


def test_hardware_event_updates_advisory_choices_without_selecting_settings() -> None:
    app = _worker_registry_stub()
    app._t = lambda key, **values: values.get("detail", key)
    app._settings_info_var = SimpleNamespace(
        values=[],
        set=lambda value: app._settings_info_var.values.append(value),
    )

    class Combo:
        def __init__(self):
            self.values = []

        def winfo_exists(self):
            return True

        def configure(self, **kwargs):
            self.values = list(kwargs["values"])

    app._settings_hardware_device_combo = Combo()
    app._settings_hardware_compute_combo = Combo()
    app.after = lambda *_args: None
    app.events.put(
        (
            "hardware_detection",
            HardwareDetectionResult(
                "ok", ("cpu", "cuda"), ("int8", "float16"), ("auto", "default"), "detected"
            ),
        )
    )

    VoiceStudioApp._poll_events(app)

    assert app._settings_info_var.values == ["detected"]
    assert app._settings_hardware_device_combo.values == ["auto", "cpu", "cuda"]
    assert app._settings_hardware_compute_combo.values == [
        "default",
        "auto",
        "int8",
        "float16",
    ]


def test_a_malformed_hardware_detection_event_reports_a_bounded_detail() -> None:
    """A payload that fails HardwareDetectionResult validation must not crash while reporting it."""

    from voice_studio.i18n import translate

    app = _worker_registry_stub()
    app._t = lambda key, **values: translate("en", key, **values)
    app._settings_info_var = SimpleNamespace(
        values=[],
        set=lambda value: app._settings_info_var.values.append(value),
    )

    class Combo:
        def __init__(self):
            self.values = []

        def winfo_exists(self):
            return True

        def configure(self, **kwargs):
            self.values = list(kwargs["values"])

    app._settings_hardware_device_combo = Combo()
    app._settings_hardware_compute_combo = Combo()
    app.after = lambda *_args: None
    app.events.put(("hardware_detection", "not-a-result"))

    VoiceStudioApp._poll_events(app)

    assert app._settings_info_var.values
    assert "not-a-result" in app._settings_info_var.values[0]


def test_hardware_detection_is_single_worker_and_probe_runs_off_tk_thread(monkeypatch):
    app = _worker_registry_stub()
    messages = []
    app._settings_info_var = SimpleNamespace(set=messages.append)
    app._t = lambda key, **_values: key
    entered = threading.Event()
    release = threading.Event()
    probe_threads = []

    def fake_detect():
        probe_threads.append(threading.current_thread())
        entered.set()
        release.wait(1)
        return HardwareDetectionResult("ok", ("cpu",), ("int8",), ("auto", "default"), "ok")

    monkeypatch.setattr(app_module, "detect_hardware", fake_detect)
    VoiceStudioApp._start_hardware_detection(app)
    assert entered.wait(1)
    VoiceStudioApp._start_hardware_detection(app)
    release.set()
    thread = app._worker_threads["hardware-detection"]
    thread.join(1)

    assert probe_threads and probe_threads[0] is not threading.current_thread()
    assert messages[-1] == "hardware_detection_busy"


def test_editor_newline_bindings_and_basic_formatting_are_declared() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)

    assert 'self.editor.bind("<Return>"' in build_ui
    assert 'self.editor.bind("<Control-Return>"' in build_ui
    assert 'text="B"' in build_ui
    assert 'text="I"' in build_ui


# --- interrupted-restore recovery at startup --------------------------------


def test_startup_recovery_settles_a_journal_before_the_store_is_opened(
    tmp_path, monkeypatch
):
    """`_settle_interrupted_restore` is a plain method; call it against a stub."""

    data = tmp_path / "data"
    staging = tmp_path / f".{data.name}.restore-abcdef"
    store = LocalStore(staging)
    store.save(
        Transcript(
            id="restored",
            created_at="2026-08-28T00:00:00+00:00",
            source_name="a.wav",
            source_sha256="a" * 64,
            language="uk",
            engine="fixture",
            model="fixture",
            raw_text="raw",
            corrected_text="corrected",
        )
    )
    backup_module._write_json_atomic(
        backup_module.restore_journal_path(data),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-28T00:00:00+00:00",
            "data_root": str(data.resolve()),
            "staging_path": str(staging.resolve()),
            "recovery_path": None,
            "expected_records": 1,
            "settings_target": None,
            "settings_payload_written": True,
            "stage": "swap_started",
        },
    )

    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    result = VoiceStudioApp._settle_interrupted_restore.__get__(SimpleNamespace())()

    assert result["status"] == "PASS"
    assert result["action"] == "completed"
    assert LocalStore(data).get("restored") is not None
    assert not backup_module.restore_journal_path(data).exists()


def test_startup_recovery_never_breaks_application_start(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("journal device failure")

    monkeypatch.setattr(app_module, "recover_interrupted_restore", explode)
    stub = SimpleNamespace()
    result = VoiceStudioApp._settle_interrupted_restore.__get__(stub)()

    assert result == {
        "status": "FAIL",
        "action": "none",
        "error": "journal device failure",
    }


@pytest.mark.parametrize(
    ("action", "expected_key"),
    [
        ("completed", "restore_recovered"),
        ("settings_completed", "restore_recovered"),
        ("rolled_back", "restore_rolled_back"),
        ("staging_discarded", "restore_staging_discarded"),
    ],
)
def test_startup_recovery_outcome_reaches_the_status_line(action, expected_key):
    recorded: list[str] = []
    stub = SimpleNamespace(
        _restore_recovery={"status": "PASS", "action": action, "records": 3},
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: f"{key}:{values.get('records', '')}",
        after=lambda *_args, **_kwargs: None,
    )
    VoiceStudioApp._report_restore_recovery.__get__(stub)()

    assert recorded == [f"{expected_key}:{3 if expected_key == 'restore_recovered' else ''}"]


def test_startup_recovery_stays_silent_when_there_was_no_journal():
    recorded: list[str] = []
    stub = SimpleNamespace(
        _restore_recovery={"status": "PASS", "action": "none", "records": None},
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: key,
        after=lambda *_args, **_kwargs: None,
    )
    VoiceStudioApp._report_restore_recovery.__get__(stub)()

    assert recorded == []


def test_startup_recovery_failure_warns_the_user():
    recorded: list[str] = []
    scheduled: list[object] = []
    stub = SimpleNamespace(
        _restore_recovery={"status": "FAIL", "action": "none", "error": "bad journal"},
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: f"{key}:{values.get('error', '')}",
        after=lambda _delay, callback: scheduled.append(callback),
    )
    VoiceStudioApp._report_restore_recovery.__get__(stub)()

    assert recorded == ["restore_recovery_failed:bad journal"]
    assert scheduled, "a failed journal must also raise a warning dialog"


def test_startup_settles_the_restore_journal_before_opening_the_store():
    """Ordering tripwire: `__init__` builds Tk widgets, so it cannot run headless.

    The guarantee under test is an ordering one — recovery must precede
    `LocalStore(data_dir())` — and there is no observable effect to assert
    without a display, so the source order is asserted instead.
    """

    source = inspect.getsource(VoiceStudioApp.__init__)
    recovery = source.index("self._settle_interrupted_restore()")
    opened = source.index("self.store = LocalStore(data_dir())")
    assert recovery < opened


def test_poll_events_reschedules_even_when_a_handler_raises() -> None:
    app = _worker_registry_stub()
    after_calls: list[tuple[int, object]] = []
    app.after = lambda delay, callback, *args: after_calls.append((delay, callback))
    app._record_stop = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    app.events.put(("record_stop", None))

    with pytest.raises(RuntimeError):
        VoiceStudioApp._poll_events(app)

    assert after_calls
