"""Explicit OpenAI transcription adapter.

This module imports the SDK only when cloud transcription is actually used, so
local/offline workflows do not gain a cloud runtime dependency or network path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..cloud_secrets import get_openai_api_key
from ..models import Segment
from .base import EngineResult

MAX_CLOUD_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_OPENAI_MEDIA_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".webm"}


def _response_value(response: Any, name: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(name, default)
    return getattr(response, name, default)


def _duration_seconds(source: Path) -> float:
    try:
        import av

        with av.open(str(source)) as container:
            if container.duration:
                return max(0.0, float(container.duration) / 1_000_000)
    except Exception:
        pass
    return 0.0


class OpenAICloudEngine:
    name = "openai-cloud"

    def __init__(self, model: str, *, client: Any | None = None):
        self.model_name = model.strip()
        if not self.model_name:
            raise ValueError("OpenAI transcription model cannot be empty")
        self._client = client

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Cloud transcription requires the optional 'cloud' dependencies. "
                "Install voice-studio[cloud]."
            ) from exc
        self._client = OpenAI(api_key=get_openai_api_key(), timeout=180.0, max_retries=2)
        return self._client

    @staticmethod
    def validate_upload(source: Path) -> None:
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix.lower() not in SUPPORTED_OPENAI_MEDIA_EXTENSIONS:
            raise ValueError(
                "OpenAI transcription accepts mp3, mp4, m4a, wav, or webm files"
            )
        size = source.stat().st_size
        if size <= 0:
            raise ValueError("audio file is empty")
        if size > MAX_CLOUD_AUDIO_BYTES:
            raise ValueError(
                "OpenAI cloud transcription accepts files up to 25 MB. "
                "Use a local engine; VOICE Studio will not split or compress audio silently."
            )

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        self.validate_upload(source)
        started = time.perf_counter()
        request: dict[str, Any] = {"model": self.model_name}
        if language not in {None, "auto"}:
            request["language"] = language
        with source.open("rb") as audio:
            response = self._client_or_create().audio.transcriptions.create(file=audio, **request)
        text = str(_response_value(response, "text", "")).strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty transcription")
        duration = _duration_seconds(source)
        usage = _response_value(response, "usage")
        metadata = {
            "provider": "openai",
            "cloud_upload": True,
            "request_id": _response_value(response, "_request_id"),
            "usage": usage
            if isinstance(usage, (str, int, float, dict, list, type(None)))
            else None,
            "timed_segments": False,
            "warning": (
                "Cloud response has no timed segments; use a local engine for timed subtitles."
            ),
        }
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=language or "auto",
            segments=[Segment(start=0.0, end=duration, text=text, language=language)],
            audio_seconds=duration,
            elapsed_seconds=time.perf_counter() - started,
            metadata=metadata,
        )
