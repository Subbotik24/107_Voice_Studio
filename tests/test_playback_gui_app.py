from __future__ import annotations

from pathlib import Path

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.i18n import translate
from voice_studio.models import Segment, Settings, Transcript


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeButton:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs: object) -> None:
        text = kwargs.get("text")
        if isinstance(text, str):
            self.text = text


class FakePlayer:
    def __init__(self) -> None:
        self.state = "idle"
        self.position = 0.0
        self.duration: float | None = None
        self.speed = 1.0
        self.last_error: str | None = None
        self.play_calls: list[tuple[Path, float, float]] = []
        self.stop_calls = 0
        self.stop_result = True
        self.seek_deltas: list[float] = []
        self.seek_to_calls: list[float] = []
        self.speed_calls: list[float] = []
        self.toggle_calls = 0

    def play(self, path: Path, *, start: float = 0.0, speed: float = 1.0) -> None:
        self.play_calls.append((Path(path), start, speed))
        self.state = "playing"

    def toggle_pause(self) -> bool:
        self.toggle_calls += 1
        self.state = "paused" if self.state == "playing" else "playing"
        return self.state == "paused"

    def stop(self, timeout: float = 2.0) -> bool:
        self.stop_calls += 1
        if self.stop_result:
            self.state = "idle"
        return self.stop_result

    def seek_by(self, delta: float) -> float:
        self.seek_deltas.append(delta)
        self.position = max(0.0, self.position + delta)
        return self.position

    def seek_to(self, position: float) -> float:
        self.seek_to_calls.append(position)
        self.position = max(0.0, position)
        return self.position

    def set_speed(self, speed: float) -> None:
        self.speed_calls.append(speed)


class FakeScale:
    def __init__(self) -> None:
        self.state_value = "disabled"

    def configure(self, **kwargs: object) -> None:
        state = kwargs.get("state")
        if isinstance(state, str):
            self.state_value = state


class FakeStore:
    def __init__(self, sources: Path) -> None:
        self.sources = sources


def _transcript(source_path: str | None, *, retained: bool = True) -> Transcript:
    return Transcript(
        id="id-1",
        created_at="2026-01-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="hash",
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="перший другий",
        corrected_text="перший другий",
        segments=[
            Segment(start=0.0, end=4.0, text="перший"),
            Segment(start=4.0, end=9.0, text="другий"),
        ],
        audio_retained=retained,
        source_path=source_path,
    )


def _managed_file(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    media = sources / "voice.wav"
    media.write_bytes(b"pcm")
    return media


def _playback_app(tmp_path: Path, transcript: Transcript | None) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(ui_language="en")
    app._current_page = "dashboard"
    app.current = transcript
    app.store = FakeStore(tmp_path / "sources")
    app.status = FakeVar("")
    app.player = FakePlayer()
    app._playback_ticker = None
    app._playback_error_reported = None
    app.playback_toggle_button = FakeButton()
    app.playback_speed_label = FakeButton()
    app.playback_speed_var = FakeVar("1×")
    app.playback_position_var = FakeVar("0:00 / —")
    app.playback_seek_label = FakeButton()
    app.playback_seek_var = FakeVar(0.0)
    app.playback_seek_scale = FakeScale()
    app._playback_seek_dragging = False
    app._playback_button_keys = {
        FakeButton(): key
        for key in ("playback_stop", "playback_back_5", "playback_forward_5")
    }
    app._after_calls: list[object] = []
    app.after = lambda _delay, callback: app._after_calls.append(callback) or "tick-1"
    app._after_cancelled: list[str] = []
    app.after_cancel = app._after_cancelled.append
    return app


# --- safe-path resolution ---------------------------------------------------


def test_the_managed_retained_copy_is_playable(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))

    assert app._playable_source_path() == media.resolve()


def test_unretained_audio_is_not_playable(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media), retained=False))

    assert app._playable_source_path() is None


def test_a_missing_source_path_is_not_playable(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))

    assert app._playable_source_path() is None


def test_an_external_original_is_never_playable(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "original.wav"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"pcm")
    app = _playback_app(tmp_path, _transcript(str(outside)))

    assert app._playable_source_path() is None


def test_a_symlink_inside_sources_to_an_external_file_is_rejected(tmp_path: Path) -> None:
    import os

    import pytest

    outside = tmp_path / "outside" / "secret.wav"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"pcm")
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    link = sources / "evil.wav"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    app = _playback_app(tmp_path, _transcript(str(link)))

    assert app._playable_source_path() is None


def test_a_deleted_managed_file_is_not_playable(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    media.unlink()
    app = _playback_app(tmp_path, _transcript(str(media)))

    assert app._playable_source_path() is None


def test_no_transcript_means_nothing_to_play(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)

    assert app._playable_source_path() is None


# --- controls ---------------------------------------------------------------


def test_toggle_without_safe_audio_reports_and_never_calls_the_player(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))

    app._toggle_playback()

    assert app.player.play_calls == []
    assert app.status.get() == translate("en", "playback_no_safe_audio")


def test_toggle_from_idle_plays_the_managed_file_at_the_selected_speed(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))
    app.playback_speed_var.set("1.5×")

    app._toggle_playback()

    assert app.player.play_calls == [(media.resolve(), 0.0, 1.5)]
    assert app._after_calls, "the position ticker must be armed"
    assert app.playback_toggle_button.text == translate("en", "playback_pause")


def test_toggle_while_playing_pauses_instead_of_replaying(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))
    app.player.state = "playing"

    app._toggle_playback()

    assert app.player.toggle_calls == 1
    assert app.player.play_calls == []


def test_segment_play_starts_from_the_segment_start(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))

    app._segment_play_requested(1)

    assert app.player.play_calls == [(media.resolve(), 4.0, 1.0)]


def test_segment_play_refuses_without_safe_audio(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))

    app._segment_play_requested(1)

    assert app.player.play_calls == []
    assert app.status.get() == translate("en", "playback_no_safe_audio")


def test_segment_play_with_an_out_of_range_index_is_safe(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))

    app._segment_play_requested(9)

    assert app.player.play_calls == []


def test_seek_passes_the_delta_only_while_active(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))
    app._seek_playback(-5.0)
    assert app.player.seek_deltas == []

    app.player.state = "playing"
    app._seek_playback(5.0)

    assert app.player.seek_deltas == [5.0]


def test_speed_change_reaches_the_player_only_while_active(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))
    app.playback_speed_var.set("2×")
    app._set_playback_speed()
    assert app.player.speed_calls == []

    app.player.state = "paused"
    app._set_playback_speed()

    assert app.player.speed_calls == [2.0]


def test_an_unparseable_speed_falls_back_to_normal(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))
    app.playback_speed_var.set("weird")

    assert app._selected_playback_speed() == 1.0


def test_stop_timeout_is_reported(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))
    app.player.stop_result = False

    app._stop_playback()

    assert app.player.stop_calls == 1
    assert app.status.get() == translate("en", "playback_stop_timeout")


# --- lifecycle stops --------------------------------------------------------


def test_leaving_the_studio_page_stops_playback(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    stops: list[str] = []
    app._stop_playback = lambda: stops.append("stop")
    app._current_page = "studio"
    app._page_frames = {"studio": None, "history": None}
    app._confirm_editor_transition = lambda: True
    app._confirm_dictionary_transition = lambda: True

    class _Frame:
        def grid(self) -> None: ...

        def grid_remove(self) -> None: ...

    app._page_frames = {"studio": _Frame(), "history": _Frame()}
    app._page_buttons = {"studio": FakeButton(), "history": FakeButton()}
    app.readiness_frame = _Frame()
    app._apply_studio_layout = lambda *_args, **_kwargs: None
    app.winfo_width = lambda: 1200

    assert VoiceStudioApp._show_page(app, "history") is True
    assert stops == ["stop"]


class FakeText:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def get(self, _start: str, _end: str) -> str:
        return self.text

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _start: str, text: str) -> None:
        self.text = text

    def configure(self, **_kwargs: object) -> None: ...


def _show_result_app(tmp_path: Path, current: Transcript | None) -> tuple[VoiceStudioApp, list]:
    app = _playback_app(tmp_path, current)
    stops: list[str] = []
    app._stop_playback = lambda: stops.append("stop")
    app.editor = FakeText()
    app.raw_editor = FakeText()
    app.details = FakeText()
    app._apply_editor_formatting = lambda _formatting: None
    app._editor_formatting = lambda: {"bold": [], "italic": []}
    app.confidence_panel_visible = False
    return app, stops


def test_switching_to_another_transcript_stops_playback(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    first = _transcript(str(media))
    second = _transcript(str(media))
    second.id = "id-2"
    app, stops = _show_result_app(tmp_path, first)

    VoiceStudioApp._show_result(app, second, refresh=False)

    assert stops == ["stop"]
    assert app.current is second


def test_reloading_the_same_transcript_does_not_stop_playback(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    first = _transcript(str(media))
    app, stops = _show_result_app(tmp_path, first)

    VoiceStudioApp._show_result(app, first, refresh=False)

    assert stops == []


def test_the_restore_reload_stops_playback(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    calls: list[str] = []
    app._stop_playback = lambda: calls.append("stop")
    app._restart_runtime = lambda: calls.append("restart")
    app._clear_current_transcript_view = lambda: calls.append("clear")
    app._refresh_history = lambda: calls.append("history")
    app._refresh_dashboard = lambda: calls.append("dashboard")
    app._refresh_ui_text = lambda: calls.append("text")
    app._start_hotkey = lambda: calls.append("hotkey")
    app_module.load_settings = app_module.load_settings  # unchanged reference

    original = app_module.load_settings
    app_module.load_settings = lambda: Settings(ui_language="en")
    try:
        VoiceStudioApp._reload_after_restore(app)
    finally:
        app_module.load_settings = original

    assert calls[0] == "stop"


# --- ticker -----------------------------------------------------------------


def test_the_ticker_re_arms_while_playing_and_stops_when_idle(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player.state = "playing"
    app.player.position = 61.0
    app.player.duration = 125.0

    app._playback_tick()
    assert app.playback_position_var.get() == "1:01 / 2:05"
    assert len(app._after_calls) == 1

    app.player.state = "idle"
    app._playback_tick()
    assert len(app._after_calls) == 1, "an idle tick must not re-arm"


def test_the_idle_tick_surfaces_a_worker_error_once(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player.state = "idle"
    app.player.last_error = "device gone"

    app._playback_tick()
    first = app.status.get()
    app.status.set("")
    app._playback_tick()

    assert first == translate("en", "playback_error", error="device gone")
    assert app.status.get() == ""


# --- seek slider -------------------------------------------------------------


def test_tick_updates_the_seek_scale_from_the_player_position(tmp_path: Path) -> None:
    """Catches a slider that keeps showing 0 while playback advances."""

    app = _playback_app(tmp_path, None)
    app.player.state = "playing"
    app.player.position = 25.0
    app.player.duration = 100.0

    app._playback_tick()

    assert app.playback_seek_var.get() == pytest.approx(250.0)


def test_seek_value_is_zero_without_a_known_duration(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player.state = "playing"
    app.player.position = 25.0
    app.player.duration = None

    app._playback_tick()

    assert app.playback_seek_var.get() == 0.0


def test_a_drag_in_progress_freezes_the_seek_scale(tmp_path: Path) -> None:
    """Catches a tick that fights the user's finger while they are dragging."""

    app = _playback_app(tmp_path, None)
    app.player.state = "playing"
    app.player.position = 10.0
    app.player.duration = 100.0
    app._press_playback_seek()
    app.playback_seek_var.set(777.0)

    app._playback_tick()

    assert app.playback_seek_var.get() == 777.0
    assert app._playback_seek_dragging is True


def test_releasing_the_seek_scale_seeks_to_the_absolute_position(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))
    app.player.state = "playing"
    app.player.duration = 200.0
    app._press_playback_seek()
    app.playback_seek_var.set(250.0)

    app._release_playback_seek()

    assert app.player.seek_to_calls == [50.0]
    assert app._playback_seek_dragging is False


def test_release_without_a_player_does_not_crash(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player = None
    app._press_playback_seek()

    app._release_playback_seek()

    assert app._playback_seek_dragging is False


def test_release_while_idle_does_not_seek(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player.state = "idle"

    app._release_playback_seek()

    assert app.player.seek_to_calls == []


def test_the_seek_scale_is_disabled_without_playable_audio(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, _transcript(None))

    app._sync_playback_toggle()

    assert app.playback_seek_scale.state_value == "disabled"


def test_the_seek_scale_is_enabled_once_audio_is_playable(tmp_path: Path) -> None:
    media = _managed_file(tmp_path)
    app = _playback_app(tmp_path, _transcript(str(media)))

    app._sync_playback_toggle()

    assert app.playback_seek_scale.state_value == "normal"


def test_shutdown_records_a_playback_residue_when_stop_times_out(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    app.player.stop_result = False
    app._playback_ticker = "tick-1"
    residues: set[str] = set()

    app._shutdown_playback(residues)

    assert app.player.stop_calls == 1
    assert residues == {"playback-worker"}
    assert app._after_cancelled == ["tick-1"]


def test_shutdown_is_clean_when_the_worker_stops_in_time(tmp_path: Path) -> None:
    app = _playback_app(tmp_path, None)
    residues: set[str] = set()

    app._shutdown_playback(residues)

    assert residues == set()


# --- retranslation ----------------------------------------------------------


def test_retranslate_relabels_the_playback_bar(tmp_path: Path) -> None:
    for locale in ("uk", "cs", "en"):
        app = _playback_app(tmp_path, None)
        app.settings = Settings(ui_language=locale)

        app._refresh_playback_ui_text()

        labels = {button.text for button in app._playback_button_keys}
        assert translate(locale, "playback_stop") in labels
        assert app.playback_speed_label.text == translate(locale, "playback_speed")
        assert app.playback_seek_label.text == translate(locale, "playback_seek")
        assert app.playback_toggle_button.text == translate(locale, "playback_play")
