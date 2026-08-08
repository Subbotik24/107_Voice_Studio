from __future__ import annotations

import shutil
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


class MediaValidationError(ValueError):
    pass


def validate_media_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size <= 0:
        raise ValueError(f"media file is empty: {path}")
    if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported media extension: {path.suffix or '<none>'}")
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is required to validate audio and video files") from exc
    try:
        with av.open(str(path)) as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise MediaValidationError(f"media file has no audio stream: {path}")
            decoded = next(iter(container.decode(audio_streams[0])), None)
            if decoded is None:
                raise MediaValidationError(f"media file contains no decodable audio frames: {path}")
    except MediaValidationError:
        raise
    except Exception as exc:
        raise MediaValidationError(f"cannot decode media file {path}: {exc}") from exc


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
        process = subprocess.run(
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
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0 or not target.is_file():
            detail = process.stderr.strip() or "unknown ffmpeg error"
            raise RuntimeError(f"cannot convert media to WAV: {detail}")
        yield target
