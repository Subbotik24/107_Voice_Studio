from __future__ import annotations

import fractions
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import voice_studio.playback as playback_module
from voice_studio.playback import AudioPlayer, AvPcmSource, SounddeviceSink

SAMPLE_RATE = 16_000
CHANNELS = 1
CHUNK_BYTES = int(SAMPLE_RATE * playback_module.CHUNK_SECONDS) * CHANNELS * 2


class FakeSource:
    def __init__(
        self,
        *,
        chunk_count: int = 5,
        marker: int = 1,
        duration: float | None = None,
        fail_at: int | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.duration = (
            chunk_count * playback_module.CHUNK_SECONDS if duration is None else duration
        )
        self.marker = marker
        self.starts: list[float] = []
        self.speeds: list[float] = []
        self.closed = 0
        self._chunk_count = chunk_count
        self._fail_at = fail_at
        self._chunk_bytes = int(sample_rate * playback_module.CHUNK_SECONDS) * channels * 2

    @property
    def payload(self) -> bytes:
        return bytes([self.marker]) * self._chunk_bytes

    def chunks(self, start: float, speed: float) -> Iterator[bytes]:
        self.starts.append(start)
        self.speeds.append(speed)
        for index in range(self._chunk_count):
            if self._fail_at is not None and index == self._fail_at:
                raise RuntimeError("decode failed")
            yield self.payload

    def close(self) -> None:
        self.closed += 1


class FakeSink:
    def __init__(self, *, write_delay: float = 0.0, block_at: int | None = None) -> None:
        self.opened: list[tuple[int, int]] = []
        self.written: list[bytes] = []
        self.aborts = 0
        self.closes = 0
        self.wrote = threading.Event()
        self.blocked = threading.Event()
        self._write_delay = write_delay
        self._block_at = block_at
        self._released = threading.Event()

    def open(self, sample_rate: int, channels: int) -> None:
        self.opened.append((sample_rate, channels))

    def write(self, chunk: bytes) -> None:
        if self._block_at is not None and len(self.written) == self._block_at:
            self.blocked.set()
            if not self._released.wait(5):
                raise AssertionError("a blocked device write was never released")
        if self._write_delay:
            time.sleep(self._write_delay)
        self.written.append(chunk)
        self.wrote.set()

    def abort(self) -> None:
        self.aborts += 1
        self._released.set()

    def close(self) -> None:
        self.closes += 1
        self._released.set()


def build_player(
    sources: list[FakeSource], sinks: list[FakeSink]
) -> tuple[AudioPlayer, list[Path]]:
    source_queue = iter(sources)
    sink_queue = iter(sinks)
    requested: list[Path] = []

    def source_factory(path: Path) -> FakeSource:
        requested.append(path)
        return next(source_queue)

    def sink_factory() -> FakeSink:
        return next(sink_queue)

    return AudioPlayer(source_factory, sink_factory), requested


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def assert_no_playback_threads() -> None:
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith(playback_module.PLAYBACK_THREAD_NAME)
    ]


def live_playback_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith(playback_module.PLAYBACK_THREAD_NAME) and thread.is_alive()
    ]


def test_playback_writes_every_chunk_and_closes_both_seams_once(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=5)
    sink = FakeSink()
    player, requested = build_player([source], [sink])
    media = tmp_path / "managed.wav"

    player.play(media)

    assert wait_until(lambda: player.state == "idle")
    assert requested == [media]
    assert sink.opened == [(SAMPLE_RATE, CHANNELS)]
    assert sink.written == [source.payload] * 5
    assert source.starts == [0.0] and source.speeds == [1.0]
    assert source.closed == 1
    assert sink.closes == 1
    assert player.last_error is None
    assert player.position == pytest.approx(0.5)
    assert player.duration == pytest.approx(0.5)
    assert player.path == media
    assert player.stop() is True
    assert_no_playback_threads()


def test_playback_pause_halts_writes_and_resume_continues(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=2_000)
    sink = FakeSink(write_delay=0.002)
    player, _ = build_player([source], [sink])

    player.play(tmp_path / "managed.wav")
    assert wait_until(lambda: len(sink.written) >= 2)
    player.pause()

    assert player.state == "paused"
    time.sleep(0.05)
    paused_writes = len(sink.written)
    time.sleep(0.2)
    assert len(sink.written) == paused_writes

    assert player.toggle_pause() is False
    assert player.state == "playing"
    assert wait_until(lambda: len(sink.written) > paused_writes)
    assert player.toggle_pause() is True
    assert player.state == "paused"

    assert player.stop() is True
    assert_no_playback_threads()


def test_playback_stop_is_prompt_and_idempotent(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=5_000)
    sink = FakeSink(write_delay=0.002)
    player, _ = build_player([source], [sink])

    player.play(tmp_path / "managed.wav")
    assert sink.wrote.wait(5)
    started = time.monotonic()

    assert player.stop() is True

    assert time.monotonic() - started < 2.0
    assert sink.aborts >= 1
    assert player.state == "idle"
    assert source.closed == 1
    assert sink.closes == 1
    assert player.stop() is True
    assert_no_playback_threads()


def test_playback_stop_unblocks_a_blocked_device_write(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=50)
    sink = FakeSink(block_at=1)
    player, _ = build_player([source], [sink])

    player.play(tmp_path / "managed.wav")
    assert sink.blocked.wait(5)
    started = time.monotonic()

    assert player.stop() is True

    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert sink.aborts >= 1
    assert player.state == "idle"
    assert player.last_error is None
    assert len(sink.written) < 50
    assert_no_playback_threads()


def test_playback_stop_when_idle_returns_true_without_factories(tmp_path: Path) -> None:
    player, _ = build_player([], [])

    assert player.stop() is True
    assert player.state == "idle"
    assert player.path is None
    assert_no_playback_threads()


def test_playback_seek_clamps_at_zero_and_at_duration_when_idle(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=5)
    sink = FakeSink()
    player, _ = build_player([source], [sink])
    player.play(tmp_path / "managed.wav")
    assert wait_until(lambda: player.state == "idle")

    assert player.seek_by(-5.0) == pytest.approx(0.0)
    assert player.position == pytest.approx(0.0)
    assert player.seek_by(5.0) == pytest.approx(0.5)
    assert player.seek_to(1_000.0) == pytest.approx(0.5)
    assert player.state == "idle"
    assert source.starts == [0.0]
    assert_no_playback_threads()


def test_playback_seek_restarts_the_worker_at_the_clamped_position(tmp_path: Path) -> None:
    first = FakeSource(chunk_count=5_000, duration=5.0, marker=1)
    second = FakeSource(chunk_count=5, duration=5.0, marker=2)
    sinks = [FakeSink(write_delay=0.002), FakeSink()]
    player, _ = build_player([first, second], sinks)

    player.play(tmp_path / "managed.wav")
    assert sinks[0].wrote.wait(5)

    assert player.seek_to(1_000.0) == pytest.approx(5.0)

    assert second.starts == [5.0]
    assert second.speeds == [1.0]
    assert wait_until(lambda: player.state == "idle")
    assert first.closed == 1 and sinks[0].closes == 1
    assert sinks[1].written == [second.payload] * 5
    assert_no_playback_threads()


def test_playback_seek_by_from_playing_position_preserves_pause(tmp_path: Path) -> None:
    first = FakeSource(chunk_count=5_000, duration=600.0, marker=1)
    second = FakeSource(chunk_count=5_000, duration=600.0, marker=2)
    sinks = [FakeSink(write_delay=0.002), FakeSink(write_delay=0.002)]
    player, _ = build_player([first, second], sinks)

    player.play(tmp_path / "managed.wav")
    assert sinks[0].wrote.wait(5)
    player.pause()
    paused_position = player.position

    target = player.seek_by(5.0)

    assert target == pytest.approx(paused_position + 5.0)
    assert second.starts == [target]
    assert player.state == "paused"
    time.sleep(0.05)
    assert sinks[1].written == []
    assert player.position == pytest.approx(target)

    assert player.stop() is True
    assert_no_playback_threads()


def test_playback_position_advances_with_source_seconds_at_double_speed(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=10)
    sink = FakeSink()
    player, _ = build_player([source], [sink])

    player.play(tmp_path / "managed.wav", start=1.5, speed=2.0)

    assert wait_until(lambda: player.state == "idle")
    assert source.starts == [1.5]
    assert source.speeds == [2.0]
    assert player.speed == pytest.approx(2.0)
    assert player.position == pytest.approx(1.5 + 2.0, abs=1e-6)
    assert_no_playback_threads()


def test_playback_set_speed_validates_and_restarts_from_current_position(
    tmp_path: Path,
) -> None:
    first = FakeSource(chunk_count=5_000, duration=600.0, marker=1)
    second = FakeSource(chunk_count=5, duration=600.0, marker=2)
    sinks = [FakeSink(write_delay=0.002), FakeSink()]
    player, _ = build_player([first, second], sinks)

    player.play(tmp_path / "managed.wav")
    assert sinks[0].wrote.wait(5)

    with pytest.raises(ValueError, match="playback speed"):
        player.set_speed(0.5)
    assert player.speed == pytest.approx(1.0)
    assert len(second.starts) == 0

    player.set_speed(1.5)

    assert player.speed == pytest.approx(1.5)
    assert second.speeds == [1.5]
    assert second.starts[0] > 0.0
    assert second.starts[0] == pytest.approx(len(sinks[0].written) * 0.1, abs=0.2)
    assert wait_until(lambda: player.state == "idle")
    assert player.position == pytest.approx(second.starts[0] + 5 * 0.1 * 1.5)
    assert_no_playback_threads()


def test_playback_set_speed_while_idle_only_validates(tmp_path: Path) -> None:
    player, _ = build_player([], [])

    with pytest.raises(ValueError, match="playback speed"):
        player.set_speed(3.0)
    player.set_speed(1.25)

    assert player.speed == pytest.approx(1.0)
    assert player.state == "idle"
    assert_no_playback_threads()


def test_playback_play_during_play_replaces_the_single_worker(tmp_path: Path) -> None:
    first = FakeSource(chunk_count=5_000, marker=1)
    second = FakeSource(chunk_count=5_000, marker=2)
    sinks = [FakeSink(write_delay=0.002), FakeSink(write_delay=0.002)]
    player, requested = build_player([first, second], sinks)
    one = tmp_path / "one.wav"
    two = tmp_path / "two.wav"

    player.play(one)
    assert sinks[0].wrote.wait(5)
    player.play(two)

    assert len(live_playback_threads()) == 1
    assert first.closed == 1
    assert sinks[0].closes == 1
    assert requested == [one, two]
    assert player.path == two
    assert sinks[1].wrote.wait(5)
    first_writes = len(sinks[0].written)

    assert player.stop() is True

    assert set(sinks[0].written) == {first.payload}
    assert len(sinks[0].written) == first_writes
    assert set(sinks[1].written) == {second.payload}
    assert_no_playback_threads()


def test_playback_source_failure_is_captured_without_escaping(tmp_path: Path) -> None:
    source = FakeSource(chunk_count=10, fail_at=3)
    sink = FakeSink()
    player, _ = build_player([source], [sink])

    player.play(tmp_path / "managed.wav")

    assert wait_until(lambda: player.state == "idle")
    assert player.last_error is not None
    assert "decode failed" in player.last_error
    assert len(sink.written) == 3
    assert sink.closes == 1
    assert source.closed == 1
    assert player.stop() is True
    assert_no_playback_threads()


def test_playback_source_factory_failure_is_captured(tmp_path: Path) -> None:
    def source_factory(path: Path) -> FakeSource:
        raise ValueError("media file has no audio stream")

    sinks: list[FakeSink] = []

    def sink_factory() -> FakeSink:
        sink = FakeSink()
        sinks.append(sink)
        return sink

    player = AudioPlayer(source_factory, sink_factory)
    player.play(tmp_path / "managed.wav")

    assert wait_until(lambda: player.state == "idle")
    assert player.last_error is not None
    assert "no audio stream" in player.last_error
    assert sinks == []
    assert_no_playback_threads()


def test_playback_validation_rejects_bad_arguments_without_touching_factories(
    tmp_path: Path,
) -> None:
    calls: list[Any] = []

    def source_factory(path: Path) -> FakeSource:
        calls.append(path)
        raise AssertionError("the source factory must not be reached")

    def sink_factory() -> FakeSink:
        calls.append("sink")
        raise AssertionError("the sink factory must not be reached")

    player = AudioPlayer(source_factory, sink_factory)
    media = tmp_path / "managed.wav"

    with pytest.raises(ValueError, match="start"):
        player.play(media, start=-1.0)
    with pytest.raises(ValueError, match="playback speed"):
        player.play(media, speed=0.5)
    with pytest.raises(ValueError, match="playback speed"):
        player.play(media, speed=2.5)

    assert calls == []
    assert player.state == "idle"
    assert player.path is None
    assert_no_playback_threads()


def test_playback_worker_failing_to_stop_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource(chunk_count=50)
    sink = FakeSink(block_at=0)
    sink.abort = lambda: None  # type: ignore[method-assign]
    player, _ = build_player([source], [sink])
    monkeypatch.setattr(playback_module, "WORKER_STOP_TIMEOUT_SECONDS", 0.05)

    player.play(tmp_path / "managed.wav")
    assert sink.blocked.wait(5)

    assert player.stop(timeout=0.05) is False
    with pytest.raises(RuntimeError, match="did not stop"):
        player.play(tmp_path / "managed.wav")

    sink.close()
    assert wait_until(lambda: player.stop(timeout=0.5) is True)
    assert_no_playback_threads()


def _write_wav(path: Path, duration_s: float, sample_rate: int = 16_000) -> Path:
    frame_count = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\0\0" * frame_count)
    return path


def _write_offset_mkv(
    path: Path, duration_s: float, offset_s: float, sample_rate: int = 16_000
) -> Path:
    """Mux mono PCM audio into an MKV whose audio stream pts starts at ``offset_s``.

    Reproduces the real-world case the auditor found: a container where the
    audio stream's ``start_time`` is not zero, so playback positions must be
    translated into the stream's own timestamp space before seeking.
    """

    import av

    container = av.open(str(path), mode="w")
    stream = container.add_stream("pcm_s16le", rate=sample_rate)
    stream.layout = "mono"
    total_samples = int(duration_s * sample_rate)
    offset_samples = int(offset_s * sample_rate)
    samples_per_frame = 1024
    written = 0
    while written < total_samples:
        count = min(samples_per_frame, total_samples - written)
        arr = np.zeros((1, count), dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(arr, format="s16", layout="mono")
        frame.sample_rate = sample_rate
        frame.pts = offset_samples + written
        frame.time_base = fractions.Fraction(1, sample_rate)
        for packet in stream.encode(frame):
            container.mux(packet)
        written += count
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return path


def _chunk_seconds(source: AvPcmSource, start: float) -> float:
    total_bytes = sum(len(chunk) for chunk in source.chunks(start, 1.0))
    return total_bytes / (source.sample_rate * source.channels * 2)


def test_av_source_seeks_correctly_when_stream_start_time_is_zero(tmp_path: Path) -> None:
    media = _write_wav(tmp_path / "sample.wav", duration_s=10.0)
    source = AvPcmSource(media)

    assert source.duration == pytest.approx(10.0, abs=0.05)
    seconds = _chunk_seconds(source, 2.0)
    source.close()

    assert seconds == pytest.approx(8.0, abs=playback_module.CHUNK_SECONDS)


def test_av_source_seek_and_duration_account_for_a_nonzero_stream_start_time(
    tmp_path: Path,
) -> None:
    media = _write_offset_mkv(tmp_path / "offset.mkv", duration_s=10.0, offset_s=5.0)
    source = AvPcmSource(media)

    # The reported duration must exclude the stream's own start-time offset.
    assert source.duration == pytest.approx(10.0, abs=0.05)
    seconds = _chunk_seconds(source, 6.0)
    source.close()

    assert seconds == pytest.approx(4.0, abs=playback_module.CHUNK_SECONDS)


def test_av_source_with_a_none_time_base_still_decodes_from_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = _write_wav(tmp_path / "sample.wav", duration_s=0.5)
    source = AvPcmSource(media)
    monkeypatch.setattr(type(source._stream), "time_base", property(lambda self: None))

    chunks = list(source.chunks(0.0, 1.0))
    source.close()

    assert chunks
    assert sum(len(chunk) for chunk in chunks) > 0


def test_av_source_reports_an_unopenable_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wav"

    with pytest.raises(ValueError, match="cannot open audio file"):
        AvPcmSource(missing)


def test_sounddevice_sink_requires_sounddevice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    sink = SounddeviceSink()

    with pytest.raises(RuntimeError, match="sounddevice"):
        sink.open(16_000, 1)

    sink.abort()
    sink.close()
    assert sink.failures == ()


def test_sounddevice_sink_swallows_shutdown_failures() -> None:
    class HostileStream:
        def abort(self) -> None:
            raise OSError("abort failed")

        def stop(self) -> None:
            raise OSError("stop failed")

        def close(self) -> None:
            raise OSError("close failed")

    sink = SounddeviceSink()
    sink._stream = HostileStream()

    sink.abort()
    sink.close()

    assert len(sink.failures) == 3
    assert "abort failed" in sink.failures[0]
    assert sink._stream is None
    sink.abort()
    sink.close()


def test_sounddevice_sink_rejects_a_write_without_an_open_device() -> None:
    sink = SounddeviceSink()

    with pytest.raises(RuntimeError, match="not open"):
        sink.write(b"\x00\x00")
