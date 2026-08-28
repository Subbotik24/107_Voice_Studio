from __future__ import annotations

import io
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import Segment
from ..ollama_local import OllamaClient
from .base import EngineResult


def audio_as_wav(source: Path) -> tuple[bytes, float]:
    """Decode media into an in-memory 16 kHz mono PCM WAV for Ollama."""

    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is required for local Ollama audio transcription") from exc

    buffer = io.BytesIO()
    sample_count = 0
    try:
        with av.open(str(source)) as input_container, av.open(
            buffer, mode="w", format="wav"
        ) as output_container:
            input_stream = next(
                (stream for stream in input_container.streams if stream.type == "audio"),
                None,
            )
            if input_stream is None:
                raise ValueError(f"media file has no audio stream: {source}")
            output_stream = output_container.add_stream("pcm_s16le", rate=16_000)
            output_stream.layout = "mono"
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=16_000
            )

            def encode(frames: Any) -> None:
                nonlocal sample_count
                for converted in frames:
                    sample_count += int(converted.samples)
                    for packet in output_stream.encode(converted):
                        output_container.mux(packet)

            for frame in input_container.decode(input_stream):
                encode(resampler.resample(frame))
            encode(resampler.resample(None))
            for packet in output_stream.encode(None):
                output_container.mux(packet)
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError(f"cannot convert audio for local Ollama: {exc}") from exc

    encoded = buffer.getvalue()
    if not encoded:
        raise RuntimeError("local Ollama audio conversion produced an empty WAV")
    return encoded, sample_count / 16_000


def _transcription_prompt(language: str | None) -> str:
    names = {"uk": "Ukrainian", "cs": "Czech", "en": "English"}
    requested = names.get(language or "")
    language_instruction = (
        f"The spoken language is {requested}." if requested else "Detect the spoken language."
    )
    return (
        "Transcribe the attached audio accurately. "
        f"{language_instruction} Return only the spoken words, without commentary, "
        "Markdown, reasoning, timestamps, or speaker labels."
    )


class OllamaAudioEngine:
    name = "ollama"

    def __init__(
        self,
        model: str,
        *,
        client: OllamaClient | None = None,
        converter: Callable[[Path], tuple[bytes, float]] = audio_as_wav,
    ):
        self.model_name = model.strip()
        if not self.model_name:
            raise ValueError(
                "Select an installed audio-capable Ollama model in Settings before transcription"
            )
        self._client = client or OllamaClient()
        self._converter = converter

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        started = time.perf_counter()
        details = self._client.show_model(self.model_name)
        capabilities = details.get("capabilities")
        if not isinstance(capabilities, list) or "audio" not in capabilities:
            raise RuntimeError(
                f"Ollama model {self.model_name!r} does not report audio capability. "
                "Choose an audio-capable model in Settings."
            )
        wav_bytes, duration = self._converter(source)
        text = self._client.audio_chat(
            self.model_name,
            wav_bytes,
            _transcription_prompt(language),
        )
        detected_language = language or "auto"
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=detected_language,
            segments=[
                Segment(
                    start=0.0,
                    end=max(0.0, duration),
                    text=text,
                    language=None if detected_language == "auto" else detected_language,
                )
            ],
            audio_seconds=max(0.0, duration),
            elapsed_seconds=time.perf_counter() - started,
            metadata={
                "provider": "ollama",
                "loopback_only": True,
                "timed_segments": False,
                "warning": "Ollama returned plain text without timed segments.",
            },
        )
