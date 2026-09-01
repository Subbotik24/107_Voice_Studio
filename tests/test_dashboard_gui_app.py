"""Contracts for the dashboard page and the history filter controls."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from voice_studio.app import VoiceStudioApp
from voice_studio.dashboard import DashboardStatistics, HistoryFilter
from voice_studio.i18n import UI_LANGUAGE_CHOICES, translate
from voice_studio.models import Transcript


class FakeLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def configure(self, **kwargs: object) -> None:
        value = kwargs.get("text")
        if isinstance(value, str):
            self.text = value


class FakeGridWidget(FakeLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.visible: bool | None = None

    def grid(self) -> None:
        self.visible = True

    def grid_remove(self) -> None:
        self.visible = False


class FakeCombobox(FakeLabel):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def configure(self, **kwargs: object) -> None:
        values = kwargs.get("values")
        if isinstance(values, list):
            self.values = values


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeStore:
    def __init__(
        self,
        statistics: DashboardStatistics | None = None,
        recent: list[Transcript] | None = None,
    ) -> None:
        self._statistics = statistics if statistics is not None else DashboardStatistics()
        self._recent = recent if recent is not None else []
        self.calls: list[tuple[str, object]] = []

    def statistics(self) -> DashboardStatistics:
        self.calls.append(("statistics", None))
        return self._statistics

    def list(self, *args: object, **kwargs: object) -> list[Transcript]:
        self.calls.append(("list", (args, kwargs)))
        return list(self._recent)


def _transcript(identifier: str = "id-1") -> Transcript:
    return Transcript(
        id=identifier,
        created_at="2026-09-01T08:30:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="raw",
        corrected_text="edited",
    )


def _dashboard_app(
    statistics: DashboardStatistics | None = None,
    recent: list[Transcript] | None = None,
) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = SimpleNamespace(ui_language="en")
    app.store = FakeStore(statistics, recent)
    app.dashboard_title_label = FakeLabel()
    app.dashboard_kpi_captions = {
        key: FakeLabel()
        for key in (
            "total",
            "completed",
            "failed",
            "words",
            "duration",
            "speed",
            "retained",
            "last_7_days",
            "last_30_days",
        )
    }
    app.dashboard_kpi_values = {key: FakeLabel() for key in app.dashboard_kpi_captions}
    app.dashboard_top_captions = {
        key: FakeLabel() for key in ("languages", "engines", "models")
    }
    app.dashboard_top_values = {key: FakeLabel() for key in app.dashboard_top_captions}
    app.dashboard_invalid_frame = FakeGridWidget()
    app.dashboard_invalid_label = FakeLabel()
    app.dashboard_recent_caption = FakeLabel()
    app.dashboard_recent_empty_label = FakeGridWidget()
    app.dashboard_recent_buttons = [FakeGridWidget() for _ in range(5)]
    app._dashboard_recent_items = []
    return app


def _history_app() -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = SimpleNamespace(ui_language="en")
    app.status = SimpleNamespace(values=[], set=lambda value: app.status.values.append(value))
    app.search_var = FakeVar()
    app.history_model_var = FakeVar()
    app.history_from_var = FakeVar()
    app.history_to_var = FakeVar()
    app.history_reset_button = FakeLabel()
    app.history_filter_captions = {
        name: FakeLabel()
        for name in ("language", "engine", "status", "retained", "model", "from", "to")
    }
    app._history_filter_vars = {
        name: FakeVar() for name in ("language", "engine", "status", "retained")
    }
    app._history_filter_combos = {name: FakeCombobox() for name in app._history_filter_vars}
    app._history_filter_labels = {}
    for name in app._history_filter_vars:
        VoiceStudioApp._apply_history_filter_choices(app, name, reset=True)
    return app


def _select(app: VoiceStudioApp, name: str, raw: object) -> None:
    label = next(
        key for key, value in app._history_filter_labels[name].items() if value == raw
    )
    app._history_filter_vars[name].set(label)


def test_dashboard_refresh_renders_every_statistic_in_its_widget() -> None:
    """Catches a dashboard that silently drops a KPI, a ranking, or the recent list."""

    statistics = DashboardStatistics(
        total_records=12,
        completed_records=9,
        failed_records=2,
        invalid_records=0,
        audio_seconds_total=3661.0,
        word_count_total=4200,
        retained_audio_records=3,
        records_last_7_days=5,
        records_last_30_days=11,
        language_counts=(("uk", 12), ("cs", 4), ("en", 2), ("auto", 1)),
        engine_counts=(("ollama", 10),),
        model_counts=(("gemma4:12b", 10),),
        speed_multiplier=1.42,
    )
    app = _dashboard_app(statistics, [_transcript("a"), _transcript("b")])

    VoiceStudioApp._refresh_dashboard(app)

    assert app.dashboard_kpi_values["total"].text == "12"
    assert app.dashboard_kpi_values["completed"].text == "9"
    assert app.dashboard_kpi_values["failed"].text == "2"
    assert app.dashboard_kpi_values["words"].text == "4200"
    assert app.dashboard_kpi_values["duration"].text == "1:01:01"
    assert app.dashboard_kpi_values["speed"].text == "×1.4"
    assert app.dashboard_kpi_values["retained"].text == "3"
    assert app.dashboard_kpi_values["last_7_days"].text == "5"
    assert app.dashboard_kpi_values["last_30_days"].text == "11"
    assert app.dashboard_top_values["languages"].text == "uk — 12\ncs — 4\nen — 2"
    assert app.dashboard_top_values["engines"].text == "ollama — 10"
    assert app.dashboard_top_values["models"].text == "gemma4:12b — 10"
    assert app.dashboard_recent_buttons[0].text == "2026-09-01T08:30  [uk]  voice.wav"
    assert [button.visible for button in app.dashboard_recent_buttons] == [
        True,
        True,
        False,
        False,
        False,
    ]
    assert app.dashboard_recent_empty_label.visible is False
    assert app.store.calls == [("statistics", None), ("list", ((), {"limit": 5}))]


def test_dashboard_refresh_survives_an_empty_store_without_raising() -> None:
    """Catches a dashboard that formats a first run into a crash or a stale value."""

    app = _dashboard_app()

    VoiceStudioApp._refresh_dashboard(app)

    assert app.dashboard_kpi_values["total"].text == "0"
    assert app.dashboard_kpi_values["duration"].text == "0:00:00"
    assert app.dashboard_kpi_values["speed"].text == "—"
    assert app.dashboard_top_values["languages"].text == "—"
    assert app.dashboard_recent_empty_label.visible is True
    assert all(button.visible is False for button in app.dashboard_recent_buttons)


@pytest.mark.parametrize(("invalid", "visible"), [(0, False), (3, True)])
def test_invalid_record_row_appears_only_when_records_are_damaged(
    invalid: int, visible: bool
) -> None:
    """Catches a storage-audit hint that is hidden when needed or shown when not."""

    app = _dashboard_app(DashboardStatistics(total_records=3, invalid_records=invalid))

    VoiceStudioApp._refresh_dashboard(app)

    assert app.dashboard_invalid_frame.visible is visible
    if visible:
        assert "3" in app.dashboard_invalid_label.text
        assert "voice-studio storage audit" in app.dashboard_invalid_label.text


def test_recent_record_opens_studio_only_when_the_editor_guard_agrees() -> None:
    """Catches a dashboard shortcut that navigates past unsaved Studio edits."""

    app = _dashboard_app()
    app._dashboard_recent_items = [_transcript("a")]
    events: list[object] = []
    app._show_page = lambda page: events.append(("page", page)) or True
    app._try_show_result = lambda item, **kwargs: (
        events.append(("load", item.id, kwargs)) or True
    )

    VoiceStudioApp._open_dashboard_recent(app, 0)

    assert events == [
        ("load", "a", {"copy": False, "refresh": False}),
        ("page", "studio"),
    ]

    events.clear()
    app._try_show_result = lambda item, **_kwargs: events.append(("blocked", item.id)) or False

    VoiceStudioApp._open_dashboard_recent(app, 0)

    assert events == [("blocked", "a")]


def test_recent_record_click_ignores_a_row_without_a_record() -> None:
    """Catches a stale recent button that opens the wrong or a missing transcript."""

    app = _dashboard_app()
    app._dashboard_recent_items = []
    app._try_show_result = lambda *_args, **_kwargs: pytest.fail("no record to open")

    VoiceStudioApp._open_dashboard_recent(app, 2)


def test_opening_the_dashboard_page_refreshes_it_and_other_pages_do_not() -> None:
    """Catches a dashboard that keeps showing figures from before the last transcription."""

    calls: list[str] = []
    app = object.__new__(VoiceStudioApp)
    app._current_page = "history"
    app._page_frames = {
        page: SimpleNamespace(grid=lambda: None, grid_remove=lambda: None)
        for page in ("dashboard", "studio", "dictionary", "history")
    }
    app._page_buttons = {
        page: SimpleNamespace(configure=lambda **_kwargs: None) for page in app._page_frames
    }
    app.readiness_frame = SimpleNamespace(grid=lambda: None, grid_remove=lambda: None)
    app._apply_studio_layout = lambda *_args, **_kwargs: None
    app.winfo_width = lambda: 1200
    app._refresh_dashboard = lambda: calls.append("dashboard")

    assert VoiceStudioApp._show_page(app, "dashboard") is True
    assert calls == ["dashboard"]

    assert VoiceStudioApp._show_page(app, "history") is True
    assert calls == ["dashboard"]


def test_partially_built_app_refreshes_the_dashboard_without_touching_the_store() -> None:
    """Catches a refresh that assumes the dashboard widgets already exist."""

    app = object.__new__(VoiceStudioApp)
    app.store = FakeStore()

    VoiceStudioApp._refresh_dashboard(app)

    assert app.store.calls == []


def test_history_filter_reads_every_control_as_a_raw_value() -> None:
    """Catches a filter that compares translated combobox labels against stored data."""

    app = _history_app()
    app.search_var.set("  needle  ")
    _select(app, "language", "cs")
    _select(app, "engine", "faster-whisper")
    _select(app, "status", "failed")
    _select(app, "retained", True)
    app.history_model_var.set(" tiny ")
    app.history_from_var.set("2026-08-01")
    app.history_to_var.set("2026-08-31")

    assert VoiceStudioApp._build_history_filter(app) == HistoryFilter(
        text="needle",
        created_from=datetime(2026, 8, 1, tzinfo=UTC),
        created_to=datetime(2026, 8, 31, 23, 59, 59, 999999, tzinfo=UTC),
        language="cs",
        engine="faster-whisper",
        model="tiny",
        status="failed",
        retained_audio=True,
    )


def test_history_filter_defaults_to_an_unfiltered_query() -> None:
    """Catches an 'All' selection that leaks a literal label into the stored filter."""

    app = _history_app()

    assert VoiceStudioApp._build_history_filter(app) == HistoryFilter()


def test_history_filter_no_retained_selection_is_false_not_unset() -> None:
    """Catches a No selection that is dropped because False looks empty."""

    app = _history_app()
    _select(app, "retained", False)

    assert VoiceStudioApp._build_history_filter(app).retained_audio is False


@pytest.mark.parametrize("value", ["2026-13-01", "01.08.2026", "yesterday", "2026-08"])
def test_invalid_filter_date_reports_to_the_status_bar_instead_of_crashing(
    value: str,
) -> None:
    """Catches a date entry that raises out of the history refresh."""

    app = _history_app()
    app.history_from_var.set(value)

    assert VoiceStudioApp._build_history_filter(app) is None
    assert app.status.values == [translate("en", "history_filter_date_invalid")]


def test_history_refresh_passes_the_built_filter_and_keeps_the_selection() -> None:
    """Catches a history refresh that falls back to the legacy query argument."""

    app = _history_app()
    items = [_transcript("a"), _transcript("b")]
    app.store = FakeStore(recent=items)
    app.history = SimpleNamespace(
        inserted=[],
        selected=[],
        delete=lambda *_args: None,
        insert=lambda _where, value: app.history.inserted.append(value),
        selection_clear=lambda *_args: None,
        selection_set=lambda index: app.history.selected.append(index),
        see=lambda _index: None,
    )
    _select(app, "language", "uk")

    VoiceStudioApp._refresh_history(app, select_id="b")

    assert app.store.calls == [
        ("list", ((), {"limit": 250, "filters": HistoryFilter(language="uk")}))
    ]
    assert app.history.inserted == ["2026-09-01T08:30  [uk]  voice.wav"] * 2
    assert app.history.selected == [1]
    assert app._history_items == items


def test_history_refresh_aborts_on_an_invalid_date_without_clearing_the_list() -> None:
    """Catches a bad date entry that empties the history the user was reading."""

    app = _history_app()
    app.store = FakeStore(recent=[_transcript("a")])
    app._history_items = [_transcript("previous")]
    app.history = SimpleNamespace(delete=lambda *_args: pytest.fail("list was cleared"))
    app.history_to_var.set("not-a-date")

    VoiceStudioApp._refresh_history(app)

    assert [item.id for item in app._history_items] == ["previous"]
    assert app.store.calls == []
    assert app.status.values == [translate("en", "history_filter_date_invalid")]


def test_resetting_filters_clears_every_control_and_refreshes() -> None:
    """Catches a Reset that leaves a filter applied to the refreshed list."""

    app = _history_app()
    app.search_var.set("needle")
    app.history_model_var.set("tiny")
    app.history_from_var.set("2026-08-01")
    app.history_to_var.set("2026-08-31")
    _select(app, "language", "cs")
    _select(app, "retained", True)
    refreshed: list[str] = []
    app._refresh_history = lambda: refreshed.append("refresh")

    VoiceStudioApp._reset_history_filters(app)

    assert refreshed == ["refresh"]
    assert VoiceStudioApp._build_history_filter(app) == HistoryFilter()


@pytest.mark.parametrize("language", [code for code, _label in UI_LANGUAGE_CHOICES])
def test_retranslation_relabels_the_dashboard_and_the_filters(language: str) -> None:
    """Catches a new widget that keeps its startup language after a language change."""

    app = _dashboard_app(DashboardStatistics(total_records=1, invalid_records=1))
    app.settings = SimpleNamespace(ui_language=language)

    VoiceStudioApp._refresh_dashboard_ui_text(app)

    assert app.dashboard_title_label.text == translate(language, "dashboard_title")
    assert app.dashboard_kpi_captions["last_30_days"].text == translate(
        language, "dashboard_last_30_days"
    )
    assert app.dashboard_top_captions["engines"].text == translate(
        language, "dashboard_top_engines"
    )
    assert app.dashboard_recent_caption.text == translate(language, "dashboard_recent")
    assert app.dashboard_invalid_label.text == translate(
        language, "dashboard_invalid_records", count=1
    )

    history = _history_app()
    history.settings = SimpleNamespace(ui_language=language)
    _select(history, "status", "completed")

    VoiceStudioApp._refresh_history_filter_ui_text(history)

    assert history.history_filter_captions["retained"].text == translate(
        language, "history_filter_retained"
    )
    assert history.history_reset_button.text == translate(language, "history_filter_reset")
    assert history._history_filter_combos["retained"].values == [
        translate(language, "history_filter_all"),
        translate(language, "history_filter_yes"),
        translate(language, "history_filter_no"),
    ]
    assert VoiceStudioApp._build_history_filter(history).status == "completed"
