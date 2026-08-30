from __future__ import annotations

import os
import queue
import stat
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BLOCK_FRAMES = 1_600
MAX_PENDING_BLOCKS = 64
MAX_RECORDING_SECONDS = 7_200
MAX_STATUS_CATEGORIES = 32
MAX_STATUS_LENGTH = 200
WRITER_STOP_TIMEOUT_SECONDS = 2.0


class RecorderCleanupError(RuntimeError):
    """A cleanup operation retained one or more recorder residue paths."""

    def __init__(self, message: str, *, residue_paths: tuple[Path, ...] = ()) -> None:
        self.residue_paths = tuple(Path(path) for path in residue_paths)
        self.paths = self.residue_paths
        self.diagnostic = {
            "kind": "recorder_cleanup",
            "message": message,
            "residue_paths": tuple(str(path) for path in self.residue_paths),
        }
        super().__init__(message)


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
        self._lifecycle_lock = threading.RLock()
        self._writer_thread: threading.Thread | None = None
        self._writer_done = threading.Event()
        self._writer_error: BaseException | None = None
        self._state_lock = threading.Lock()
        self._frames_written = 0
        self._accepted_frames = 0
        self._dropped_blocks = 0
        self._status_counts: dict[str, int] = {}
        self._limit_reached = False
        self._destination: Path | None = None
        self._recording_directory: Path | None = None
        self._destination_owned = False
        self._expected_identity: tuple[int, int] | None = None
        self._destination_identity: tuple[int, int] | None = None
        self._quarantine_path: Path | None = None
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

    @property
    def quarantine_path(self) -> Path | None:
        return self._quarantine_path

    def start(self, recording_directory: Path) -> Path:
        """Start recording into a new private WAV under ``recording_directory``."""

        with self._lifecycle_lock:
            return self._start_impl(Path(recording_directory))

    def _start_impl(self, recording_directory: Path) -> Path:
        if self.recording or self._stream is not None or self._writer_thread is not None:
            if self._destination is None:
                raise RuntimeError("recorder lifecycle state is inconsistent")
            return self._destination

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("sounddevice is not installed") from exc

        directory = self._prepare_recording_directory(recording_directory)
        fd, name = tempfile.mkstemp(
            prefix=".voice-studio-",
            suffix=".wav",
            dir=str(directory),
        )
        path = Path(name)
        try:
            expected_identity = self._file_identity(os.fstat(fd))
        finally:
            os.close(fd)

        self._drain_queue()
        with self._state_lock:
            self._destination = path
            self._recording_directory = directory
            self._destination_owned = True
            self._expected_identity = expected_identity
            self._destination_identity = expected_identity
            self._quarantine_path = None
            self._writer_error = None
            self._writer_done.clear()
            self._frames_written = 0
            self._accepted_frames = 0
            self._dropped_blocks = 0
            self._status_counts = {}
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
                self._record_status(str(status))
            if not self.recording or self._writer_done.is_set():
                return

            available = self.sample_rate * MAX_RECORDING_SECONDS
            with self._state_lock:
                remaining = available - self._accepted_frames
                if remaining <= 0:
                    self._limit_reached = True
                    self._recording.clear()
                    return
                accepted = min(max(int(frames), 0), len(indata), remaining)
                if accepted:
                    self._accepted_frames += accepted
                reached_limit = accepted >= remaining
                if reached_limit:
                    self._limit_reached = True
                    self._recording.clear()

            if accepted <= 0:
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
        except BaseException as exc:
            self._recording.clear()
            stream_error: BaseException | None = None
            try:
                self._close_stream()
            except BaseException as close_exc:
                stream_error = close_exc
            self._finish_writer()
            writer_error = self._writer_error
            cleanup_error = self._remove_owned_partial()
            self._clear_session()
            self._raise_with_cleanup(stream_error or writer_error or exc, cleanup_error)
            raise AssertionError("unreachable") from exc
        return path

    def stop(self) -> RecordingResult:
        with self._lifecycle_lock:
            return self._stop_impl()

    def _stop_impl(self) -> RecordingResult:
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
            cleanup_error = self._remove_owned_partial()
            self._clear_session()
            self._raise_with_cleanup(stream_error or writer_error, cleanup_error)
            raise AssertionError("unreachable")
        if path is None or self._frames_written <= 0 or not path.exists():
            cleanup_error = self._remove_owned_partial()
            self._clear_session()
            if cleanup_error is not None:
                raise cleanup_error
            raise RuntimeError("no audio was captured")
        if not self._path_has_destination_identity():
            primary = FileExistsError(f"recording destination was replaced: {path}")
            cleanup_error = self._remove_owned_partial()
            self._clear_session()
            self._raise_with_cleanup(primary, cleanup_error)
            raise AssertionError("unreachable")

        with self._state_lock:
            status_messages = tuple(self._status_counts)
            status_counts = dict(self._status_counts)
            dropped_blocks = self._dropped_blocks
            frames_written = self._frames_written
            limit_reached = self._limit_reached
        warning_parts: list[str] = []
        if dropped_blocks:
            warning_parts.append(
                f"Dropped {dropped_blocks} audio block(s) because the queue was full."
            )
        if status_messages:
            rendered_status = "; ".join(
                f"{message} (x{status_counts[message]})"
                if status_counts[message] > 1
                else message
                for message in status_messages
            )
            warning_parts.append("Sounddevice status: " + rendered_status)
        if limit_reached:
            warning_parts.append("Maximum recording duration reached.")
        result = RecordingResult(
            path=path,
            frames_written=frames_written,
            dropped_blocks=dropped_blocks,
            status_messages=status_messages,
            limit_reached=limit_reached,
            degraded=bool(warning_parts),
            warning=" ".join(warning_parts),
        )
        self._destination_owned = False
        return result

    def cancel(self) -> None:
        with self._lifecycle_lock:
            self._cancel_impl()

    def _cancel_impl(self) -> None:
        if self._stream is None and self._writer_thread is None:
            return
        self._recording.clear()
        close_error: BaseException | None = None
        try:
            self._close_stream()
        except BaseException as exc:
            close_error = exc
        finally:
            self._drain_queue()
            self._finish_writer()
        cleanup_error = self._remove_owned_partial()
        self._clear_session()
        if close_error is not None:
            self._raise_with_cleanup(close_error, cleanup_error)
            raise AssertionError("unreachable")
        if cleanup_error is not None:
            raise cleanup_error

    def _writer_loop(self, path: Path) -> None:
        raw = None
        try:
            raw = self._open_destination(path)
            with wave.open(raw, "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
                while True:
                    item = self._frames.get()
                    if item is self._sentinel:
                        break
                    payload, frame_count = item  # type: ignore[misc]
                    wav.writeframes(payload)
                    raw.flush()
                    with self._state_lock:
                        self._frames_written += frame_count
        except BaseException as exc:
            with self._state_lock:
                self._writer_error = self._detach_error(exc)
        finally:
            try:
                if raw is not None:
                    raw.close()
            except BaseException as exc:
                with self._state_lock:
                    if self._writer_error is None:
                        self._writer_error = self._detach_error(exc)
                try:
                    if raw is not None:
                        os.close(raw.fileno())
                except BaseException:
                    pass
            finally:
                self._writer_done.set()

    @staticmethod
    def _detach_error(exc: BaseException) -> BaseException:
        try:
            detached = type(exc)(str(exc))
        except BaseException:
            detached = RuntimeError(str(exc))
        detached.__traceback__ = None
        detached.__cause__ = None
        detached.__context__ = None
        return detached

    def _open_destination(self, path: Path) -> Any:
        expected = self._expected_identity
        if expected is None:
            raise RuntimeError("recorder destination identity is unavailable")
        fd = os.open(str(path), os.O_RDWR)
        try:
            stat_result = os.fstat(fd)
            actual = self._file_identity(stat_result)
            if actual != expected or stat_result.st_size != 0:
                raise FileExistsError(f"recorder destination identity changed: {path}")
            raw = os.fdopen(fd, "r+b")
        except BaseException:
            os.close(fd)
            raise
        with self._state_lock:
            self._destination_identity = actual
            self._destination_owned = True
        return raw

    def _finish_writer(self) -> None:
        thread = self._writer_thread
        if thread is None:
            return

        deadline = time.monotonic() + WRITER_STOP_TIMEOUT_SECONDS
        while not self._writer_done.is_set() and thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("audio recorder writer did not stop within 2.0 seconds")
            try:
                self._frames.put(self._sentinel, timeout=min(0.05, remaining))
                break
            except queue.Full:
                continue

        remaining = deadline - time.monotonic()
        if remaining <= 0 and thread.is_alive():
            raise TimeoutError("audio recorder writer did not stop within 2.0 seconds")
        if remaining > 0:
            thread.join(timeout=remaining)

        if thread.is_alive() or not self._writer_done.is_set():
            raise TimeoutError("audio recorder writer did not stop within 2.0 seconds")
        self._writer_thread = None

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    def _record_status(self, status: str) -> None:
        category = status[:MAX_STATUS_LENGTH]
        with self._state_lock:
            if category in self._status_counts:
                self._status_counts[category] += 1
            elif len(self._status_counts) < MAX_STATUS_CATEGORIES:
                self._status_counts[category] = 1

    def _record_drop(self) -> None:
        with self._state_lock:
            self._dropped_blocks += 1

    @staticmethod
    def _file_identity(stat_result: os.stat_result) -> tuple[int, int]:
        return stat_result.st_dev, stat_result.st_ino

    @staticmethod
    def _prepare_recording_directory(recording_directory: Path) -> Path:
        directory = recording_directory.expanduser()
        if directory.is_symlink():
            raise ValueError("recording directory must not be a symbolic link")
        if directory.exists():
            if not directory.is_dir():
                raise NotADirectoryError(
                    f"recording directory is not a directory: {directory}"
                )
        else:
            directory.mkdir(parents=True, mode=0o700)
        if os.name != "nt":
            mode = stat.S_IMODE(directory.stat().st_mode)
            if mode & 0o077:
                raise PermissionError(
                    f"recording directory must be owner-only: {directory}"
                )
        return directory

    def _path_has_destination_identity(self) -> bool:
        path = self._destination
        identity = self._destination_identity
        if path is None or identity is None:
            return False
        try:
            lexical_entry = self._lexical_entry(path)
            if lexical_entry is None or stat.S_ISLNK(lexical_entry.st_mode):
                return False
            return self._file_identity(path.stat()) == identity
        except FileNotFoundError:
            return False

    def _drain_queue(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _lexical_entry(path: Path) -> os.stat_result | None:
        """Inspect a directory entry without following a final symlink."""

        try:
            return os.lstat(path)
        except FileNotFoundError:
            return None

    @classmethod
    def _lexical_entry_exists(cls, path: Path) -> bool:
        try:
            return cls._lexical_entry(path) is not None
        except OSError:
            # If the entry cannot be inspected, retain it conservatively.
            return True

    @staticmethod
    def _existing_residue_paths(*paths: Path | None) -> tuple[Path, ...]:
        result: list[Path] = []
        for path in paths:
            if (
                path is not None
                and AudioRecorder._lexical_entry_exists(path)
                and path not in result
            ):
                result.append(path)
        return tuple(result)

    def _remove_owned_partial(self) -> RecorderCleanupError | None:
        path = self._destination
        identity = self._destination_identity or self._expected_identity
        if not self._destination_owned or path is None or identity is None:
            return None

        self._quarantine_path = None
        try:
            lexical_entry = self._lexical_entry(path)
            if lexical_entry is None:
                self._destination_owned = False
                return None
            if stat.S_ISLNK(lexical_entry.st_mode):
                return self._make_cleanup_error(
                    f"recorder preserved foreign destination entry at {path}",
                    residue_paths=self._existing_residue_paths(path),
                )
            try:
                path_identity = self._file_identity(path.stat())
            except FileNotFoundError:
                return self._make_cleanup_error(
                    f"recorder preserved dangling destination entry at {path}",
                    residue_paths=self._existing_residue_paths(path),
                )
            if path_identity != identity:
                return self._make_cleanup_error(
                    f"recorder preserved foreign destination entry at {path}",
                    residue_paths=self._existing_residue_paths(path),
                )

            quarantine_fd, quarantine_name = tempfile.mkstemp(
                prefix=f".{path.name}.recorder-",
                dir=str(path.parent),
            )
            quarantine = Path(quarantine_name)
            self._quarantine_path = quarantine
            os.close(quarantine_fd)
            quarantine.unlink()
            os.rename(path, quarantine)

            # Check twice: the second check closes the deterministic race where a
            # foreign entry replaces the quarantine path during the first check.
            first_identity = self._file_identity(quarantine.stat())
            second_identity = self._file_identity(quarantine.stat())
            if first_identity == identity and second_identity == identity:
                quarantine.unlink()
                self._quarantine_path = None
                self._destination_owned = False
                return None
            return self._make_cleanup_error(
                f"recorder preserved ambiguous partial entry at {quarantine}",
                residue_paths=self._existing_residue_paths(path, quarantine),
            )
        except BaseException as exc:
            return self._make_cleanup_error(
                f"recorder preserved cleanup residue: {exc}",
                residue_paths=self._existing_residue_paths(path, self._quarantine_path),
            )

    def _make_cleanup_error(
        self, message: str, *, residue_paths: tuple[Path, ...]
    ) -> RecorderCleanupError:
        return RecorderCleanupError(message, residue_paths=residue_paths)

    @staticmethod
    def _raise_with_cleanup(
        primary: BaseException | None, cleanup: RecorderCleanupError | None
    ) -> None:
        if cleanup is None:
            if primary is None:
                raise RuntimeError("recorder operation failed")
            raise primary
        if primary is None:
            raise cleanup
        try:
            primary.cleanup_error = cleanup  # type: ignore[attr-defined]
            primary.residue_paths = cleanup.residue_paths  # type: ignore[attr-defined]
        except BaseException:
            pass
        raise primary from cleanup

    def _clear_session(self) -> None:
        self._stream = None
        self._writer_thread = None
        self._destination = None
        self._recording_directory = None
        self._destination_owned = False
        self._expected_identity = None
        self._destination_identity = None
        self._recording.clear()
