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


def test_settings_dialog_destroys_tk_window_before_restarting_global_hotkey() -> None:
    """The native listener must start only after Tk has released the dialog.

    Restarting the global hotkey while the modal still holds the grab lets the
    old shortcut fire into a half-torn-down dialog.
    """

    app = object.__new__(VoiceStudioApp)
    order: list[str] = []
    scheduled: list[object] = []
    dialog = SimpleNamespace(
        grab_release=lambda: order.append("grab_release"),
        destroy=lambda: order.append("destroy"),
    )
    app.after_idle = lambda callback: (
        order.append("after_idle"),
        scheduled.append(callback),
    )

    VoiceStudioApp._close_settings_dialog(app, dialog)

    assert order == ["grab_release", "destroy", "after_idle"]
    assert scheduled == [app._start_hotkey], "the real hotkey starter must be the deferred call"


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
    assert statuses == [
        "hotkey_unavailable:listener did not stop within 1 second; retrying"
    ]

    VoiceStudioApp._start_hotkey(app)

    assert existing.stop_calls == 2
    assert len(constructed) == 1
    assert app.hotkey is constructed[0]
    assert constructed[0].start_calls == 1


def test_settings_dialog_retains_stubborn_listener_before_building_dialog(monkeypatch) -> None:
    existing = _FakeHotkey([False])
    app, _statuses = _hotkey_app_stub(existing)

    class DialogBuildStarted(Exception):
        pass

    monkeypatch.setattr(
        app_module.tk,
        "Toplevel",
        lambda _app: (_ for _ in ()).throw(DialogBuildStarted()),
    )

    with pytest.raises(DialogBuildStarted):
        VoiceStudioApp._settings_dialog(app)

    assert existing.stop_calls == 1
    assert app.hotkey is existing


def test_settings_dialog_clears_stopped_listener_before_building_dialog(monkeypatch) -> None:
    existing = _FakeHotkey([True])
    app, _statuses = _hotkey_app_stub(existing)

    class DialogBuildStarted(Exception):
        pass

    def build_dialog(_app: VoiceStudioApp) -> object:
        assert app.hotkey is None
        raise DialogBuildStarted()

    monkeypatch.setattr(app_module.tk, "Toplevel", build_dialog)

    with pytest.raises(DialogBuildStarted):
        VoiceStudioApp._settings_dialog(app)

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


# --- static widget-construction tripwires -----------------------------------
# See the module docstring: these prove declaration, not behaviour.


def test_backup_ui_is_declared_with_async_work_and_reversible_restore() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    backup_dialog = inspect.getsource(VoiceStudioApp._backup_dialog)
    event_handler = inspect.getsource(VoiceStudioApp._poll_events)

    assert 'self._t("backup")' in build_ui
    assert "threading.Thread" in backup_dialog
    assert "create_backup" in backup_dialog
    assert "verify_backup" in backup_dialog
    assert "restore_backup" in backup_dialog
    assert 'self._t("restore_backup_prompt")' in backup_dialog
    assert 'event == "backup_done"' in event_handler


def test_history_actions_continuous_recording_and_hotkey_capture_are_declared() -> None:
    build_ui = inspect.getsource(VoiceStudioApp._build_ui)
    settings_dialog = inspect.getsource(VoiceStudioApp._settings_dialog)

    assert 'self._t("continuous_record")' in build_ui
    assert 'self._t("rename")' in build_ui
    assert 'self._t("delete")' in build_ui
    assert 'self._t("capture_hotkey")' in settings_dialog
    assert "hotkey_from_tk_event" in settings_dialog


def test_settings_dialog_declares_all_three_reusable_engine_profiles() -> None:
    settings_dialog = inspect.getsource(VoiceStudioApp._settings_dialog)

    assert '"ollama-local"' in settings_dialog
    assert '"whisper-local"' in settings_dialog
    assert '"openai-cloud"' in settings_dialog


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
