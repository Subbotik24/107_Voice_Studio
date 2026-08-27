from __future__ import annotations

import multiprocessing
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SUPPORTED_MEDIA_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
    ".opus",
    ".aac",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
}

# Untrusted media reaches a native parser. Bound what that parser is allowed to
# consume before it starts, and bound how long it may run.
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_MEDIA_SECONDS = 7_200
PROBE_TIMEOUT_SECONDS = 30.0

# Two hours of 16 kHz mono PCM s16le, matching MAX_MEDIA_SECONDS.
MAX_CANONICAL_OUTPUT_BYTES = 230_404_096
CONVERT_TIMEOUT_SECONDS = 900.0

# Only the head of a converter's diagnostics is kept, so a chatty failure cannot
# be turned into unbounded memory by a crafted input.
MAX_CONVERTER_STDERR_BYTES = 8 * 1024

# A probe that cannot be reaped politely is killed; give it a short grace period
# first so a normal exit is not escalated.
_PROBE_TERMINATE_GRACE_SECONDS = 2.0


class MediaValidationError(ValueError):
    pass


class MediaContainmentError(RuntimeError):
    """Raised when the disposable probe process cannot be established.

    This is deliberately fatal. Falling back to parsing untrusted media inside
    the GUI or CLI process is the exact exposure the containment exists to
    remove, so a containment failure must fail the import instead.
    """


def _probe_media(path_str: str, sender: object) -> None:
    """Decode one audio frame in a disposable child process.

    Module level and argument driven so that it survives `spawn` pickling on
    both Windows and macOS. Every outcome is sent as a plain tuple of built-in
    types: the parent must never unpickle an object chosen by untrusted input.
    """

    try:
        try:
            import av
        except ImportError as exc:
            sender.send(("unavailable", f"PyAV is required to validate media files: {exc}"))
            return
        try:
            with av.open(path_str) as container:
                audio_streams = [
                    stream for stream in container.streams if stream.type == "audio"
                ]
                if not audio_streams:
                    sender.send(("invalid", f"media file has no audio stream: {path_str}"))
                    return
                duration = container.duration
                seconds = None if duration is None else duration / 1_000_000
                decoded = next(iter(container.decode(audio_streams[0])), None)
                if decoded is None:
                    sender.send(
                        ("invalid", f"media file contains no decodable audio frames: {path_str}")
                    )
                    return
        except Exception as exc:
            sender.send(("invalid", f"cannot decode media file {path_str}: {exc}"))
            return
        sender.send(("ok", seconds))
    finally:
        sender.close()


def _start_probe(path_str: str) -> tuple[object, object]:
    """Start the disposable probe and return its read end and process.

    Kept as one seam so the parent-side deadline, kill and result handling can
    be tested against a fake child. The real child re-imports this module under
    `spawn`, so substituting the target function in the parent would not reach
    it.
    """

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_probe_media,
        args=(path_str, sender),
        name="hermes-media-probe",
    )
    process.start()
    # Drop the parent's copy of the write end, so a child that dies without
    # sending closes the pipe and surfaces as EOF rather than as a full timeout.
    sender.close()
    return receiver, process


def _terminate(process: object) -> None:
    """Stop the probe and do not return while it is still alive."""

    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=_PROBE_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(timeout=_PROBE_TERMINATE_GRACE_SECONDS)


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the converter and everything it started.

    On POSIX the child leads its own session, so one signal reaches the whole
    group. Killing only the direct child would leave a descendant holding the
    output file and the CPU.
    """

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=_PROBE_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover - the kill is unconditional
        pass


def _run_contained(argv: list[str], *, timeout: float, stderr_path: Path) -> int:
    """Run an external converter so it cannot outlive or outlast this call.

    Establishes a process group (POSIX) or a new process group (Windows) before
    the child runs, bounds it with a deadline, and kills the whole group on
    expiry. Failing to establish containment is fatal rather than degraded.
    """

    creation: dict[str, object] = {}
    if os.name == "posix":
        creation["start_new_session"] = True
    else:  # pragma: no cover - exercised on Windows CI
        creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        handle = stderr_path.open("wb")
    except OSError as exc:
        raise MediaContainmentError(f"cannot capture converter diagnostics: {exc}") from exc
    try:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=handle,
                **creation,
            )
        except Exception as exc:
            raise MediaContainmentError(
                f"cannot establish a contained media converter, refusing to run it: {exc}"
            ) from exc
    finally:
        handle.close()

    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        raise MediaValidationError(
            f"media conversion exceeded {timeout} seconds and was stopped"
        ) from None
    finally:
        _kill_tree(process)


def validate_media_file(path: Path, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> None:
    """Validate untrusted media without parsing it in this process.

    The cheap checks run here. The native decode runs in a disposable child with
    a hard deadline, so a crafted or pathologically slow file cannot wedge the
    Tk event loop or the CLI, and a hung parser is killed rather than waited on.
    """

    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"media file is empty: {path}")
    if size > MAX_SOURCE_BYTES:
        raise MediaValidationError(
            f"media file is larger than the {MAX_SOURCE_BYTES} byte limit: {size} bytes"
        )
    if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported media extension: {path.suffix or '<none>'}")

    try:
        receiver, process = _start_probe(str(path))
    except Exception as exc:
        raise MediaContainmentError(
            f"cannot establish a contained media probe, refusing to parse in-process: {exc}"
        ) from exc

    try:
        if not receiver.poll(timeout):
            raise MediaValidationError(
                f"media validation exceeded {timeout} seconds and was stopped: {path}"
            )
        try:
            outcome, detail = receiver.recv()
        except EOFError as exc:
            raise MediaValidationError(
                f"contained media probe exited without a result for {path}"
            ) from exc
    finally:
        _terminate(process)
        receiver.close()

    if outcome == "unavailable":
        raise RuntimeError(detail)
    if outcome == "invalid":
        raise MediaValidationError(detail)
    if detail is not None and detail > MAX_MEDIA_SECONDS:
        raise MediaValidationError(
            f"media file is longer than the {MAX_MEDIA_SECONDS} second limit: {detail:.0f} seconds"
        )


@contextmanager
def canonical_wav(path: Path, *, sample_rate: int = 16_000) -> Iterator[Path]:
    """Yield a mono PCM WAV suitable for the Hermes PyTorch runtime.

    PCM WAV can be consumed directly. Other formats require an external ffmpeg
    executable; the temporary conversion is removed on exit.
    """

    validate_media_file(path)
    if path.suffix.lower() == ".wav":
        yield path
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required to use non-WAV media with the Hermes Whisper engine"
        )
    with tempfile.TemporaryDirectory(prefix="hermes-voice-media-") as directory:
        target = Path(directory) / "audio.wav"
        diagnostics = Path(directory) / "ffmpeg.err"
        returncode = _run_contained(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-c:a",
                "pcm_s16le",
                # Stop writing at the ceiling rather than discovering it after
                # the disk is already full.
                "-fs",
                str(MAX_CANONICAL_OUTPUT_BYTES),
                str(target),
            ],
            timeout=CONVERT_TIMEOUT_SECONDS,
            stderr_path=diagnostics,
        )
        if returncode != 0 or not target.is_file():
            raise RuntimeError(f"cannot convert media to WAV: {_converter_detail(diagnostics)}")
        produced = target.stat().st_size
        if produced >= MAX_CANONICAL_OUTPUT_BYTES:
            raise MediaValidationError(
                "converted audio reached the "
                f"{MAX_CANONICAL_OUTPUT_BYTES} byte ceiling and was rejected"
            )
        yield target


def _converter_detail(stderr_path: Path) -> str:
    """Return a bounded, human-readable head of the converter's diagnostics."""

    try:
        with stderr_path.open("rb") as handle:
            head = handle.read(MAX_CONVERTER_STDERR_BYTES)
    except OSError:
        return "unknown converter error"
    text = head.decode("utf-8", errors="replace").strip()
    return text or "unknown converter error"
