from __future__ import annotations

import queue
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BLOCK_FRAMES = 1_600
MAX_PENDING_BLOCKS = 64
MAX_RECORDING_SECONDS = 7_200


@dataclass(frozen=True, slots=True)
class RecordingResult:
    path: Path
    frames_written: int
    dropped_blocks: int
    status_messages: tuple[str, ...]
    limit_reached: bool
    degraded: bool = False
    warning: str = ""


class AudioRecorder:
    def __init__(self, sample_rate: int = 16_000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: queue.Queue[object] = queue.Queue(maxsize=MAX_PENDING_BLOCKS)
        self._stream: Any | None = None
        self._recording = threading.Event()
        self._writer_thread: threading.Thread | None = None
        self._writer_done = threading.Event()
        self._writer_error: BaseException | None = None
        self._state_lock = threading.Lock()
        self._frames_written = 0
        self._accepted_frames = 0
        self._dropped_blocks = 0
        self._status_messages: list[str] = []
        self._limit_reached = False
        self._destination: Path | None = None
        self._destination_owned = False
        self._sentinel = object()

    @property
    def recording(self) -> bool:
        return self._recording.is_set()

    @property
    def limit_reached(self) -> bool:
        return self._limit_reached

    @property
    def destination(self) -> Path | None:
        return self._destination

    def start(self, destination: Path) -> None:
        if self.recording or self._stream is not None or self._writer_thread is not None:
            return
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("sounddevice is not installed") from exc

        path = Path(destination)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing recording: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._drain_queue()
        with self._state_lock:
            self._destination = path
            self._destination_owned = True
            self._writer_error = None
            self._writer_done.clear()
            self._frames_written = 0
            self._accepted_frames = 0
            self._dropped_blocks = 0
            self._status_messages = []
            self._limit_reached = False

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(path,),
            name="audio-recorder-writer",
            daemon=True,
        )
        self._writer_thread.start()

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del time_info
            if status:
                with self._state_lock:
                    self._status_messages.append(str(status))
            if not self.recording or self._writer_done.is_set():
                return

            available = self.sample_rate * MAX_RECORDING_SECONDS
            with self._state_lock:
                remaining = available - self._accepted_frames
                accepted = min(max(int(frames), 0), len(indata), max(remaining, 0))
                if accepted:
                    self._accepted_frames += accepted
                reached_limit = accepted > 0 and accepted >= remaining
                if reached_limit:
                    self._limit_reached = True
                    self._recording.clear()

            if accepted <= 0:
                return
            if self._frames.full():
                self._record_drop()
                return
            payload = indata[:accepted].tobytes()
            try:
                self._frames.put_nowait((payload, accepted))
            except queue.Full:
                self._record_drop()

        self._recording.set()
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=BLOCK_FRAMES,
                callback=callback,
            )
            self._stream.start()
        except BaseException:
            self._recording.clear()
            try:
                self._close_stream()
            finally:
                self._finish_writer()
                self._remove_owned_partial()
                self._clear_session()
            raise

    def stop(self) -> RecordingResult:
        if self._stream is None and self._writer_thread is None:
            raise RuntimeError("recording is not active")
        self._recording.clear()
        stream_error: BaseException | None = None
        try:
            self._close_stream()
        except BaseException as exc:
            stream_error = exc
        self._finish_writer()
        writer_error = self._writer_error
        path = self._destination
        if stream_error is not None or writer_error is not None:
            self._remove_owned_partial()
            self._clear_session()
            raise stream_error or writer_error
        if path is None or self._frames_written <= 0 or not path.exists():
            self._remove_owned_partial()
            self._clear_session()
            raise RuntimeError("no audio was captured")

        with self._state_lock:
            status_messages = tuple(self._status_messages)
            dropped_blocks = self._dropped_blocks
            frames_written = self._frames_written
            limit_reached = self._limit_reached
        warning_parts: list[str] = []
        if dropped_blocks:
            warning_parts.append(
                f"Dropped {dropped_blocks} audio block(s) because the queue was full."
            )
        if status_messages:
            warning_parts.append("Sounddevice status: " + "; ".join(status_messages))
        if limit_reached:
            warning_parts.append("Maximum recording duration reached.")
        warning = " ".join(warning_parts)
        return RecordingResult(
            path=path,
            frames_written=frames_written,
            dropped_blocks=dropped_blocks,
            status_messages=status_messages,
            limit_reached=limit_reached,
            degraded=bool(warning_parts),
            warning=warning,
        )

    def cancel(self) -> None:
        self._recording.clear()
        try:
            self._close_stream()
        finally:
            self._drain_queue()
            self._finish_writer()
            self._remove_owned_partial()
            self._clear_session()

    def _writer_loop(self, path: Path) -> None:
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
                while True:
                    item = self._frames.get()
                    if item is self._sentinel:
                        break
                    payload, frame_count = item  # type: ignore[misc]
                    wav.writeframes(payload)
                    with self._state_lock:
                        self._frames_written += frame_count
        except BaseException as exc:
            with self._state_lock:
                self._writer_error = exc
        finally:
            self._writer_done.set()

    def _finish_writer(self) -> None:
        thread = self._writer_thread
        if thread is None:
            return
        while not self._writer_done.is_set():
            try:
                self._frames.put(self._sentinel, timeout=0.05)
                break
            except queue.Full:
                continue
        thread.join()
        self._writer_thread = None

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    def _record_drop(self) -> None:
        with self._state_lock:
            self._dropped_blocks += 1

    def _drain_queue(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def _remove_owned_partial(self) -> None:
        path = self._destination
        if self._destination_owned and path is not None and path.exists():
            path.unlink()

    def _clear_session(self) -> None:
        self._stream = None
        self._writer_thread = None
        self._destination = None
        self._destination_owned = False
        self._recording.clear()
