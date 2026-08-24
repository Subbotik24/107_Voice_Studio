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
