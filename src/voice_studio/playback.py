"""Local audio playback for the desktop UI.

This module turns one already-chosen media file into sound on the local
device. It decodes with PyAV, resamples to signed 16-bit PCM and pushes
bounded ~100 ms chunks to the output device; nothing is buffered beyond the
chunk currently in hand.

Path policy is the caller's responsibility. ``AudioPlayer`` opens exactly the
path it is handed and performs no lookup, no managed-source resolution and no
retention decision, so a caller that must restrict playback to managed copies
has to resolve and validate the path before calling ``play``.

Playback speed is implemented purely by resampling: the decoded audio is
resampled to ``rate / speed`` and then played at the original device rate, so a
speed change shifts pitch as well. There is no pitch-preserving DSP here.

The decode and device seams are injected (``PcmSource`` and ``PcmSink``) so the
controller can be exercised without a physical audio device.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from .audio_errors import friendly_device_error

CHUNK_SECONDS = 0.1
SUPPORTED_SPEEDS = (0.75, 1.0, 1.25, 1.5, 2.0)
PLAYBACK_THREAD_NAME = "voice-studio-playback"
PAUSE_POLL_SECONDS = 0.05
WORKER_STOP_TIMEOUT_SECONDS = 2.0
MAX_PLAYBACK_CHANNELS = 2
SAMPLE_BYTES = 2


class PcmSource(Protocol):
    """A pull-based source of signed 16-bit PCM chunks."""

    sample_rate: int
    channels: int
    duration: float | None

    def chunks(self, start: float, speed: float) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class PcmSink(Protocol):
    """A blocking output device for signed 16-bit PCM chunks."""

    def open(self, sample_rate: int, channels: int) -> None: ...

    def write(self, chunk: bytes) -> None: ...

    def abort(self) -> None: ...

    def close(self) -> None: ...


def _validated_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported playback speed: {speed!r}") from exc
    if value not in SUPPORTED_SPEEDS:
        supported = ", ".join(f"{option:g}" for option in SUPPORTED_SPEEDS)
        raise ValueError(f"unsupported playback speed {value:g}; supported speeds: {supported}")
    return value


def _validated_start(start: float) -> float:
    try:
        value = float(start)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"playback start must be a number of seconds: {start!r}") from exc
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"playback start must be a non-negative number of seconds: {start!r}")
    return value


def _describe(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


class AvPcmSource:
    """Decode one media file into signed 16-bit PCM chunks with PyAV.

    The container is opened eagerly so that a file without a usable audio
    stream fails before any device is opened. ``chunks`` may be called more than
    once: each call seeks the container back to the requested position.
    """

    def __init__(self, path: Path) -> None:
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(f"PyAV is required to play audio: {exc}") from exc

        self.path = Path(path)
        try:
            container = av.open(str(self.path))
        except Exception as exc:
            raise ValueError(f"cannot open audio file {self.path.name}: {exc}") from exc

        try:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise ValueError(f"media file has no audio stream: {self.path.name}")
            codec_context = stream.codec_context
            sample_rate = int(getattr(codec_context, "sample_rate", 0) or 0)
            if sample_rate <= 0:
                raise ValueError(f"media file has no usable audio sample rate: {self.path.name}")
            layout = getattr(codec_context, "layout", None)
            channels = int(getattr(layout, "nb_channels", 0) or 1)
        except BaseException:
            self._close_container(container)
            raise

        self._av = av
        self._container = container
        self._stream = stream
        self.sample_rate = sample_rate
        self.channels = MAX_PLAYBACK_CHANNELS if channels >= MAX_PLAYBACK_CHANNELS else 1
        self.duration = self._stream_duration()
        self._frame_bytes = self.channels * SAMPLE_BYTES
        self._closed = False

    def chunks(self, start: float, speed: float) -> Iterator[bytes]:
        if self._closed:
            raise ValueError(f"audio source is closed: {self.path.name}")
        output_rate = max(1, int(round(self.sample_rate / speed)))
        chunk_bytes = max(
            self._frame_bytes, int(self.sample_rate * CHUNK_SECONDS) * self._frame_bytes
        )
        layout = "stereo" if self.channels == MAX_PLAYBACK_CHANNELS else "mono"
        resampler = self._av.AudioResampler(format="s16", layout=layout, rate=output_rate)
        pending = bytearray()
        skip_bytes: int | None = None
        offset = self._stream_offset()
        time_base = self._stream.time_base

        try:
            if start > 0 and time_base:
                self._container.seek(
                    max(0, int((start + offset) / time_base)),
                    stream=self._stream,
                )
            for frame in self._container.decode(self._stream):
                if skip_bytes is None:
                    frame_time = frame.time
                    lead = (
                        0.0
                        if frame_time is None
                        else max(0.0, start - (float(frame_time) - offset))
                    )
                    skip_bytes = int(lead * output_rate) * self._frame_bytes
                for resampled in resampler.resample(frame):
                    pending += resampled.to_ndarray().tobytes()
                skip_bytes = self._drop(pending, skip_bytes)
                while len(pending) >= chunk_bytes:
                    yield bytes(pending[:chunk_bytes])
                    del pending[:chunk_bytes]
            for resampled in resampler.resample(None):
                pending += resampled.to_ndarray().tobytes()
        except (GeneratorExit, StopIteration):
            raise
        except Exception as exc:
            raise ValueError(f"cannot decode audio file {self.path.name}: {exc}") from exc

        skip_bytes = self._drop(pending, skip_bytes or 0)
        while len(pending) >= chunk_bytes:
            yield bytes(pending[:chunk_bytes])
            del pending[:chunk_bytes]
        tail = len(pending) - len(pending) % self._frame_bytes
        if tail:
            yield bytes(pending[:tail])

    def close(self) -> None:
        self._closed = True
        container, self._container = self._container, None
        if container is not None:
            self._close_container(container)

    @staticmethod
    def _drop(pending: bytearray, skip_bytes: int) -> int:
        if skip_bytes <= 0:
            return 0
        dropped = min(skip_bytes, len(pending))
        del pending[:dropped]
        return skip_bytes - dropped

    @staticmethod
    def _close_container(container: Any) -> None:
        try:
            container.close()
        except BaseException:
            pass

    def _stream_offset(self) -> float:
        start_time = getattr(self._stream, "start_time", None)
        time_base = getattr(self._stream, "time_base", None)
        if start_time is not None and time_base:
            return float(start_time * time_base)
        return 0.0

    def _stream_duration(self) -> float | None:
        duration = getattr(self._stream, "duration", None)
        time_base = getattr(self._stream, "time_base", None)
        if duration is not None and time_base:
            return float(duration * time_base)
        container_duration = getattr(self._container, "duration", None)
        if container_duration is not None:
            return max(0.0, float(container_duration) / 1_000_000 - self._stream_offset())
        return None


class SounddeviceSink:
    """Write PCM chunks to the default output device.

    Shutdown never raises into the caller: ``abort`` and ``close`` record what
    failed so a wedged or disappearing device cannot turn a stop into an
    exception on the Tk main thread.
    """

    def __init__(self) -> None:
        self._stream: Any | None = None
        self._failures: tuple[str, ...] = ()

    @property
    def failures(self) -> tuple[str, ...]:
        return self._failures

    def open(self, sample_rate: int, channels: int) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise RuntimeError(f"sounddevice is not available for playback: {exc}") from exc

        try:
            stream = sd.RawOutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="int16",
            )
            stream.start()
        except Exception as exc:
            raise friendly_device_error(exc, kind="output") from exc
        self._stream = stream

    def write(self, chunk: bytes) -> None:
        stream = self._stream
        if stream is None:
            raise RuntimeError("playback device is not open")
        stream.write(chunk)

    def abort(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.abort()
        except BaseException as exc:
            self._record(exc)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except BaseException as exc:
            self._record(exc)
        try:
            stream.close()
        except BaseException as exc:
            self._record(exc)

    def _record(self, exc: BaseException) -> None:
        self._failures = (*self._failures, _describe(exc))


class AudioPlayer:
    """Drive one playback worker over an injected decode and device seam.

    Exactly one worker thread exists at a time. Every operation that changes
    where or how fast playback runs stops the current worker first, so a seek or
    a speed change can never leave two writers on the device.

    ``stop`` is idempotent and safe to call from the Tk main thread: it aborts
    the device to unblock a write in progress and joins the worker within a
    bounded budget.
    """

    def __init__(
        self,
        source_factory: Callable[[Path], PcmSource] = AvPcmSource,
        sink_factory: Callable[[], PcmSink] = SounddeviceSink,
    ) -> None:
        self._source_factory = source_factory
        self._sink_factory = sink_factory
        # Two locks, as in the recorder: the lifecycle lock serialises whole
        # operations, while the short state lock is the one the worker takes for
        # every position update. Joining a worker under the state lock would
        # deadlock against that update.
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._stop_event.set()
        self._thread: threading.Thread | None = None
        self._sink: PcmSink | None = None
        self._generation = 0
        self._state = "idle"
        self._position = 0.0
        self._duration: float | None = None
        self._path: Path | None = None
        self._speed = 1.0
        self._last_error: str | None = None

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @property
    def position(self) -> float:
        with self._state_lock:
            return self._position

    @property
    def duration(self) -> float | None:
        with self._state_lock:
            return self._duration

    @property
    def path(self) -> Path | None:
        with self._state_lock:
            return self._path

    @property
    def speed(self) -> float:
        with self._state_lock:
            return self._speed

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def play(self, path: Path, *, start: float = 0.0, speed: float = 1.0) -> None:
        """Play ``path`` from ``start`` seconds at ``speed``.

        Arguments are validated before any current playback is disturbed and
        before either factory is called.
        """

        media = Path(path)
        checked_speed = _validated_speed(speed)
        checked_start = _validated_start(start)
        with self._lifecycle_lock:
            self._require_stopped()
            self._start_worker(media, checked_start, checked_speed, paused=False)

    def pause(self) -> None:
        with self._state_lock:
            if self._state != "playing":
                return
            self._state = "paused"
            self._pause_event.set()

    def resume(self) -> None:
        with self._state_lock:
            if self._state != "paused":
                return
            self._state = "playing"
            self._pause_event.clear()

    def toggle_pause(self) -> bool:
        """Flip pause when playing and report whether playback is now paused."""

        with self._state_lock:
            if self._state == "playing":
                self.pause()
            elif self._state == "paused":
                self.resume()
            return self._state == "paused"

    def stop(self, timeout: float = WORKER_STOP_TIMEOUT_SECONDS) -> bool:
        """Stop playback and report whether the worker terminated in time."""

        with self._lifecycle_lock:
            return self._stop_worker(timeout)

    def seek_by(self, delta: float) -> float:
        """Seek ``delta`` seconds from the current position and report it."""

        with self._lifecycle_lock:
            with self._state_lock:
                target = self._position + float(delta)
            return self._seek(target)

    def seek_to(self, position: float) -> float:
        """Seek to an absolute position, clamped, and report where it landed."""

        with self._lifecycle_lock:
            return self._seek(float(position))

    def set_speed(self, speed: float) -> None:
        """Validate ``speed`` and, when playing, restart at the new speed."""

        checked = _validated_speed(speed)
        with self._lifecycle_lock:
            with self._state_lock:
                media = self._path
                position = self._position
                paused = self._state == "paused"
                active = self._state != "idle"
            if not active or media is None:
                return
            self._require_stopped()
            self._start_worker(media, position, checked, paused=paused)

    def _seek(self, target: float) -> float:
        if not math.isfinite(target):
            raise ValueError(f"seek target must be a finite number of seconds: {target!r}")
        with self._state_lock:
            duration = self._duration
            media = self._path
            speed = self._speed
            paused = self._state == "paused"
            active = self._state != "idle"
        clamped = max(0.0, target)
        if duration is not None:
            clamped = min(clamped, max(0.0, duration))
        if not active or media is None:
            with self._state_lock:
                self._position = clamped
            return clamped
        self._require_stopped()
        self._start_worker(media, clamped, speed, paused=paused)
        return clamped

    def _require_stopped(self) -> None:
        if not self._stop_worker(WORKER_STOP_TIMEOUT_SECONDS):
            raise RuntimeError(
                "the previous playback worker did not stop within "
                f"{WORKER_STOP_TIMEOUT_SECONDS} seconds"
            )

    def _stop_worker(self, timeout: float) -> bool:
        with self._state_lock:
            thread = self._thread
            sink = self._sink
            self._stop_event.set()
            self._pause_event.clear()
        if sink is not None:
            try:
                sink.abort()
            except BaseException as exc:
                with self._state_lock:
                    self._last_error = _describe(exc)
        if thread is None:
            self._settle()
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=max(0.0, float(timeout)))
        if thread.is_alive():
            return False
        with self._state_lock:
            if self._thread is thread:
                self._thread = None
        self._settle()
        return True

    def _settle(self) -> None:
        with self._state_lock:
            self._sink = None
            self._state = "idle"

    def _start_worker(self, media: Path, start: float, speed: float, *, paused: bool) -> None:
        stop_event = threading.Event()
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            if media != self._path:
                self._duration = None
            self._path = media
            self._position = start
            self._speed = speed
            self._last_error = None
            self._sink = None
            self._stop_event = stop_event
            self._state = "paused" if paused else "playing"
            if paused:
                self._pause_event.set()
            else:
                self._pause_event.clear()
            thread = threading.Thread(
                target=self._run,
                args=(generation, media, start, speed, stop_event),
                name=PLAYBACK_THREAD_NAME,
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def _run(
        self,
        generation: int,
        media: Path,
        start: float,
        speed: float,
        stop_event: threading.Event,
    ) -> None:
        source: PcmSource | None = None
        sink: PcmSink | None = None
        try:
            source = self._source_factory(media)
            with self._state_lock:
                if self._generation == generation:
                    self._duration = source.duration
            rate = max(1, int(source.sample_rate))
            channels = max(1, int(source.channels))
            frame_bytes = channels * SAMPLE_BYTES
            sink = self._sink_factory()
            with self._state_lock:
                if self._generation == generation:
                    self._sink = sink
            sink.open(rate, channels)
            for chunk in source.chunks(start, speed):
                if stop_event.is_set():
                    break
                while self._pause_event.is_set() and not stop_event.is_set():
                    stop_event.wait(PAUSE_POLL_SECONDS)
                if stop_event.is_set():
                    break
                sink.write(chunk)
                advance = len(chunk) / (rate * frame_bytes) * speed
                with self._state_lock:
                    if self._generation != generation:
                        break
                    self._position += advance
        except BaseException as exc:
            if not stop_event.is_set():
                with self._state_lock:
                    if self._generation == generation:
                        self._last_error = _describe(exc)
        finally:
            self._release(generation, source, sink, stop_event)

    def _release(
        self,
        generation: int,
        source: PcmSource | None,
        sink: PcmSink | None,
        stop_event: threading.Event,
    ) -> None:
        if sink is not None:
            try:
                sink.close()
            except BaseException as exc:
                self._note_failure(generation, stop_event, exc)
        if source is not None:
            try:
                source.close()
            except BaseException as exc:
                self._note_failure(generation, stop_event, exc)
        with self._state_lock:
            if self._generation == generation:
                self._sink = None
                self._state = "idle"
                self._pause_event.clear()

    def _note_failure(
        self, generation: int, stop_event: threading.Event, exc: BaseException
    ) -> None:
        if stop_event.is_set():
            return
        with self._state_lock:
            if self._generation == generation and self._last_error is None:
                self._last_error = _describe(exc)


__all__ = [
    "CHUNK_SECONDS",
    "SUPPORTED_SPEEDS",
    "WORKER_STOP_TIMEOUT_SECONDS",
    "AudioPlayer",
    "AvPcmSource",
    "PcmSink",
    "PcmSource",
    "SounddeviceSink",
]
