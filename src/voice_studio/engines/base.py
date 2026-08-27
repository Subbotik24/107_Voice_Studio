from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..models import Segment


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

    def transcribe(self, source: Path, language: str | None) -> EngineResult: ...
