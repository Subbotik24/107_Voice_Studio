from __future__ import annotations

import sys
import threading
import time
import wave
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import hermes_voice_studio.recorder as recorder_module
from hermes_voice_studio.recorder import AudioRecorder


class FakeInputStream:
    instances: list[FakeInputStream] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def emit(self, data: np.ndarray, status: Any = None) -> None:
        self.callback(data, len(data), None, status)


@pytest.fixture
def fake_sounddevice(monkeypatch: pytest.MonkeyPatch) -> type[FakeInputStream]:
    FakeInputStream.instances.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=FakeInputStream))
    return FakeInputStream


def _stream() -> FakeInputStream:
    assert FakeInputStream.instances
    return FakeInputStream.instances[-1]


def _assert_no_writer_threads() -> None:
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("audio-recorder-writer")
    ]


def test_recorder_streams_fixed_blocks_to_wav(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    destination = tmp_path / "recording.wav"
    recorder = AudioRecorder(sample_rate=16_000, channels=1)

    recorder.start(destination)
    stream = _stream()
    assert stream.kwargs["blocksize"] == 1_600
    samples = np.arange(1_600, dtype=np.int16).reshape(-1, 1)
    stream.emit(samples)

    result = recorder.stop()

    assert result.path == destination
    assert result.frames_written == 1_600
    assert result.dropped_blocks == 0
    with wave.open(str(destination), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        assert handle.readframes(1_600) == samples.tobytes()
    assert stream.stopped and stream.closed
    _assert_no_writer_threads()


def test_recorder_reports_sounddevice_status(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    recorder = AudioRecorder()
    recorder.start(tmp_path / "status.wav")
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16), status="input overflow")

    result = recorder.stop()

    assert result.status_messages == ("input overflow",)
    assert result.degraded is True
    assert "input overflow" in result.warning


def test_recorder_bounds_queue_and_reports_drops(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = recorder_module.wave.open

    def slow_open(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def delayed_writeframes(data: bytes) -> Any:
            time.sleep(0.002)
            return writeframes(data)

        handle.writeframes = delayed_writeframes
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", slow_open)
    recorder = AudioRecorder()
    recorder.start(tmp_path / "drops.wav")
    block = np.zeros((1_600, 1), dtype=np.int16)
    for _ in range(200):
        _stream().emit(block)

    result = recorder.stop()

    assert result.dropped_blocks > 0
    assert result.degraded is True
    assert "drop" in result.warning.lower()
    _assert_no_writer_threads()


def test_recorder_stops_at_duration_limit(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    recorder = AudioRecorder(sample_rate=1)
    recorder.start(tmp_path / "limited.wav")
    _stream().emit(np.zeros((7_201, 1), dtype=np.int16))

    result = recorder.stop()

    assert result.limit_reached is True
    assert recorder.limit_reached is True
    assert result.frames_written == 7_200
    assert result.degraded is True
    _assert_no_writer_threads()


def test_recorder_cancel_removes_partial_file(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    destination = tmp_path / "cancelled.wav"
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))

    recorder.cancel()
    recorder.cancel()

    assert not destination.exists()
    assert recorder.destination is None
    assert recorder.recording is False
    _assert_no_writer_threads()


def test_recorder_refuses_to_overwrite_existing_destination(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    destination = tmp_path / "user-owned.wav"
    original = b"user media"
    destination.write_bytes(original)
    recorder = AudioRecorder()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        recorder.start(destination)

    assert destination.read_bytes() == original
    assert recorder.destination is None
    _assert_no_writer_threads()


def test_recorder_accepts_precreated_empty_destination_and_cancel_preserves_success(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    destination = tmp_path / "precreated.wav"
    destination.touch()
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))

    result = recorder.stop()
    recorder.cancel()

    assert result.path == destination
    assert destination.exists()
    _assert_no_writer_threads()


def test_recorder_serializes_overlapping_starts(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    barrier = threading.Barrier(3)
    destinations = [tmp_path / "one.wav", tmp_path / "two.wav"]
    recorder = AudioRecorder()

    def start(destination: Any) -> None:
        barrier.wait()
        recorder.start(destination)

    threads = [threading.Thread(target=start, args=(destination,)) for destination in destinations]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(FakeInputStream.instances) == 1
    assert recorder.recording is True
    recorder.cancel()
    _assert_no_writer_threads()


def test_recorder_rejects_destination_replacement_before_writer_open(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "raced.wav"
    destination.touch()
    entered = threading.Event()
    release = threading.Event()
    recorder = AudioRecorder()
    real_open_destination = recorder._open_destination

    def delayed_open(path: Any) -> Any:
        entered.set()
        assert release.wait(2)
        return real_open_destination(path)

    monkeypatch.setattr(recorder, "_open_destination", delayed_open)
    recorder.start(destination)
    assert entered.wait(2)
    replacement = tmp_path / "raced-replacement.wav"
    replacement.write_bytes(b"replacement")
    destination.unlink()
    replacement.replace(destination)
    release.set()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        recorder.stop()

    assert destination.read_bytes() == b"replacement"
    _assert_no_writer_threads()


def test_recorder_serializes_stop_then_cancel_without_orphaning_writer(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    destination = tmp_path / "stop-cancel.wav"
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    stream = _stream()
    entered = threading.Event()
    release = threading.Event()
    original_stop = stream.stop

    def blocking_stop() -> None:
        entered.set()
        assert release.wait(2)
        original_stop()

    stream.stop = blocking_stop
    outcomes: list[Any] = []
    stop_thread = threading.Thread(target=lambda: outcomes.append(recorder.stop()))
    cancel_thread = threading.Thread(target=recorder.cancel)
    stop_thread.start()
    assert entered.wait(2)
    cancel_thread.start()
    time.sleep(0.05)
    assert cancel_thread.is_alive()
    release.set()
    stop_thread.join()
    cancel_thread.join()

    assert len(outcomes) == 1
    assert outcomes[0].path == destination
    assert destination.exists()
    _assert_no_writer_threads()


def test_recorder_bounds_persistent_status_metadata(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream]
) -> None:
    recorder = AudioRecorder()
    recorder.start(tmp_path / "status-stress.wav")
    stream = _stream()
    block = np.zeros((1_600, 1), dtype=np.int16)
    for _ in range(2_000):
        stream.emit(block, status="persistent input overflow")

    result = recorder.stop()

    assert result.status_messages == ("persistent input overflow",)
    assert "x2000" in result.warning
    assert len(result.status_messages) <= recorder_module.MAX_STATUS_CATEGORIES
    _assert_no_writer_threads()


def test_recorder_writer_failure_is_raised_and_partial_removed(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "failed.wav"

    def fail_open(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(recorder_module.wave, "open", fail_open)
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))

    with pytest.raises(OSError, match="disk full"):
        recorder.stop()

    assert not destination.exists()
    _assert_no_writer_threads()


def test_recorder_write_failure_cleans_partial_but_not_replacement(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "failed-after-write.wav"
    wrote = threading.Event()
    real_open = recorder_module.wave.open

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def write_then_fail(data: bytes) -> Any:
            writeframes(data)
            wrote.set()
            raise OSError("write failed")

        handle.writeframes = write_then_fail
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", fail_after_write)
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    assert wrote.wait(2)
    assert recorder._writer_done.wait(2)
    assert destination.exists() and destination.stat().st_size > 0

    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(b"replacement")
    destination.unlink()
    replacement.replace(destination)

    with pytest.raises(OSError, match="write failed"):
        recorder.stop()

    assert destination.read_bytes() == b"replacement"
    _assert_no_writer_threads()
