from __future__ import annotations

import multiprocessing
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
        name="voice-studio-media-probe",
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
