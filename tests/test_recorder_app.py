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


def test_recorder_write_failure_removes_real_partial_wav(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "real-partial.wav"
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
    assert destination.exists() and destination.stat().st_size > 0

    with pytest.raises(OSError, match="write failed"):
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

    real_rename = recorder_module.os.rename

    def race_rename(source: Any, target: Any) -> Any:
        result = real_rename(source, target)
        if source == destination:
            destination.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(recorder_module.os, "rename", race_rename)

    with pytest.raises(OSError, match="write failed"):
        recorder.stop()

    assert destination.read_bytes() == b"replacement"
    _assert_no_writer_threads()


def test_cleanup_preserves_foreign_quarantine_when_destination_collides(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "quarantine-collision.wav"
    wrote = threading.Event()
    real_open = recorder_module.wave.open

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def write_then_fail(data: bytes) -> Any:
            writeframes(data)
            wrote.set()
            raise OSError("primary write failed")

        handle.writeframes = write_then_fail
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", fail_after_write)
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    assert wrote.wait(2)

    real_rename = recorder_module.os.rename

    def collision_rename(source: Any, target: Any) -> Any:
        result = real_rename(source, target)
        if source == destination:
            foreign = tmp_path / "foreign.bin"
            foreign.write_bytes(b"foreign quarantine")
            recorder_module.os.link(target, destination)
            target.unlink()
            real_rename(foreign, target)
        return result

    monkeypatch.setattr(recorder_module.os, "rename", collision_rename)
    with pytest.raises(OSError, match="primary write failed"):
        recorder.stop()

    assert destination.exists()
    assert destination.read_bytes().startswith(b"RIFF")
    assert recorder.quarantine_path is not None
    assert recorder.quarantine_path.read_bytes() == b"foreign quarantine"
    _assert_no_writer_threads()


def test_cleanup_does_not_depend_on_hard_links(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "no-hard-link.wav"
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
    monkeypatch.setattr(
        recorder_module.os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("hard links unsupported")),
    )
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    assert wrote.wait(2)
    foreign = tmp_path / "foreign-destination.bin"
    foreign.write_bytes(b"foreign destination")
    destination.unlink()
    foreign.replace(destination)

    with pytest.raises(OSError, match="write failed"):
        recorder.stop()

    assert recorder.quarantine_path is not None
    assert recorder.quarantine_path.exists()
    assert recorder.quarantine_path.read_bytes() == b"foreign destination"
    _assert_no_writer_threads()


def test_cleanup_does_not_delete_foreign_replacement_after_identity_check(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "post-stat-race.wav"
    wrote = threading.Event()
    real_open = recorder_module.wave.open

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def write_then_fail(data: bytes) -> Any:
            writeframes(data)
            wrote.set()
            raise OSError("primary write failed")

        handle.writeframes = write_then_fail
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", fail_after_write)
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    assert wrote.wait(2)

    real_file_identity = recorder_module.AudioRecorder._file_identity
    replaced = False

    def replace_after_identity_check(stat: Any) -> tuple[int, int]:
        nonlocal replaced
        identity = real_file_identity(stat)
        quarantine = recorder.quarantine_path
        if not replaced and quarantine is not None and quarantine.exists():
            foreign = tmp_path / "post-stat-foreign.bin"
            foreign.write_bytes(b"foreign replacement")
            quarantine.unlink()
            foreign.replace(quarantine)
            replaced = True
        return identity

    monkeypatch.setattr(
        recorder_module.AudioRecorder,
        "_file_identity",
        staticmethod(replace_after_identity_check),
    )

    with pytest.raises(OSError, match="primary write failed"):
        recorder.stop()

    assert replaced
    assert recorder.quarantine_path is not None
    assert recorder.quarantine_path.read_bytes() == b"foreign replacement"
    _assert_no_writer_threads()


def test_recorder_close_failure_propagates_and_cleans_partial(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "close-failed.wav"
    recorder = AudioRecorder()
    real_open_destination = recorder._open_destination

    class CloseFailingFile:
        def __init__(self, raw: Any) -> None:
            self._raw = raw

        def __getattr__(self, name: str) -> Any:
            return getattr(self._raw, name)

        def close(self) -> None:
            raise OSError("close before release failed")

    def open_close_failing(path: Any) -> Any:
        return CloseFailingFile(real_open_destination(path))

    monkeypatch.setattr(recorder, "_open_destination", open_close_failing)
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))

    with pytest.raises(OSError, match="close before release failed"):
        recorder.stop()

    assert not destination.exists()
    assert not recorder._writer_thread
    assert recorder._writer_done.is_set()
    _assert_no_writer_threads()


def test_cleanup_failure_does_not_mask_writer_error_and_session_restarts(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "cleanup-failed.wav"
    wrote = threading.Event()
    real_open = recorder_module.wave.open
    real_mkstemp = recorder_module.tempfile.mkstemp

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def write_then_fail(data: bytes) -> Any:
            writeframes(data)
            wrote.set()
            raise OSError("primary write error")

        handle.writeframes = write_then_fail
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", fail_after_write)
    recorder = AudioRecorder()
    recorder.start(destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    assert wrote.wait(2)
    monkeypatch.setattr(
        recorder_module.tempfile,
        "mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(OSError("cleanup unavailable")),
    )

    with pytest.raises(OSError, match="primary write error"):
        recorder.stop()

    assert destination.exists()
    assert recorder.destination is None
    assert recorder.recording is False
    _assert_no_writer_threads()

    monkeypatch.setattr(recorder_module.wave, "open", real_open)
    monkeypatch.setattr(recorder_module.tempfile, "mkstemp", real_mkstemp)
    next_destination = tmp_path / "restart.wav"
    recorder.start(next_destination)
    _stream().emit(np.zeros((1_600, 1), dtype=np.int16))
    recorder.cancel()
    assert not next_destination.exists()


def test_recorder_full_queue_writer_failure_stops_promptly(
    tmp_path: Any, fake_sounddevice: type[FakeInputStream], monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "full-queue-failed.wav"
    entered = threading.Event()
    release = threading.Event()
    real_open = recorder_module.wave.open

    def blocked_fail(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        writeframes = handle.writeframes

        def wait_then_fail(data: bytes) -> Any:
            entered.set()
            release.wait(2)
            writeframes(data)
            raise OSError("full queue write failed")

        handle.writeframes = wait_then_fail
        return handle

    monkeypatch.setattr(recorder_module.wave, "open", blocked_fail)
    recorder = AudioRecorder()
    recorder.start(destination)
    block = np.zeros((1_600, 1), dtype=np.int16)
    _stream().emit(block)
    assert entered.wait(2)
    for _ in range(200):
        _stream().emit(block)
    release.set()

    started = time.monotonic()
    with pytest.raises(OSError, match="full queue write failed"):
        recorder.stop()

    assert time.monotonic() - started < 2
    assert not destination.exists()
    _assert_no_writer_threads()
