from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..models import Segment


@dataclass(frozen=True)
class TranscriptionHints:
    """Bounded, per-request recognition hints for local Whisper."""

    terms: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.terms, tuple):
            raise ValueError("transcription hints must be a tuple of strings")
        if any(type(term) is not str for term in self.terms):
            raise ValueError("transcription hints must contain only strings")
        if len(self.terms) > 256:
            raise ValueError("transcription hints exceed the maximum of 256 terms")
        if len(", ".join(self.terms).encode("utf-8")) > 8192:
            raise ValueError("transcription hints exceed the maximum payload size of 8192 bytes")


@dataclass(frozen=True)
class EngineResult:
    engine: str
    model: str
    language: str
    segments: list[Segment]
    audio_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        parts = (segment.text.strip() for segment in self.segments if segment.text.strip())
        return " ".join(parts).strip()

    @property
    def real_time_factor(self) -> float | None:
        if self.audio_seconds <= 0:
            return None
        return self.elapsed_seconds / self.audio_seconds


class SpeechEngine(Protocol):
    name: str
    model_name: str

    def transcribe(
        self,
        source: Path,
        language: str | None,
        *,
        hints: TranscriptionHints | None = None,
    ) -> EngineResult: ...
