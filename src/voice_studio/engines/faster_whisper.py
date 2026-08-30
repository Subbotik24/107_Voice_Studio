from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..models import Segment, validate_hardware_options
from .base import EngineResult


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(
        self,
        model: str,
        device: str = "auto",
        compute_type: str = "default",
        display_name: str | None = None,
    ):
        validate_hardware_options(device, compute_type)
        self.model_path = model
        self.model_name = display_name or Path(model).name
        self.device = device
        self.compute_type = compute_type
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Install the project with "
                "its default dependencies."
            ) from exc
        self._validate_runtime_hardware()
        self._model = WhisperModel(
            self.model_path,
            device=self.device,
            compute_type=self.compute_type,
            local_files_only=True,
        )
        return self._model

    def _validate_runtime_hardware(self) -> None:
        """Reject runtime-incompatible options before constructing WhisperModel."""

        try:
            import ctranslate2
        except ImportError as exc:
            raise RuntimeError(
                "CTranslate2 runtime is not installed; cannot validate local hardware"
            ) from exc
        try:
            cuda_devices = int(ctranslate2.get_cuda_device_count())
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"CTranslate2 hardware detection failed: {exc}") from exc
        if self.device == "cuda" and cuda_devices <= 0:
            raise RuntimeError("CUDA device was requested but no CUDA device is available")
        runtime_device = "cpu" if self.device == "auto" else self.device
        getter = ctranslate2.get_supported_compute_types
        try:
            try:
                supported = getter(runtime_device)
            except TypeError:
                supported = getter()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CTranslate2 compute-type detection failed for {runtime_device}: {exc}"
            ) from exc
        supported_values = {str(item) for item in supported}
        if self.compute_type not in supported_values and self.compute_type not in {
            "auto",
            "default",
        }:
            allowed = ", ".join(sorted(supported_values))
            raise RuntimeError(
                f"compute_type '{self.compute_type}' is not supported on "
                f"{runtime_device}; runtime supports: {allowed or 'none'}"
            )

    @staticmethod
    def _confidence(avg_logprob: Any) -> float | None:
        if avg_logprob is None:
            return None
        try:
            return max(0.0, min(1.0, math.exp(float(avg_logprob))))
        except (TypeError, ValueError, OverflowError):
            return None

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        was_loaded = self._model is not None
        load_started = time.perf_counter()
        model = self._load()
        model_load_seconds = 0.0 if was_loaded else time.perf_counter() - load_started
        started = time.perf_counter()
        parts, info = model.transcribe(
            str(source),
            language=None if language in {None, "auto"} else language,
            vad_filter=True,
            beam_size=5,
            word_timestamps=False,
        )
        segments: list[Segment] = []
        for part in parts:
            start = max(0.0, float(part.start))
            end = max(start, float(part.end))
            segments.append(
                Segment(
                    start=start,
                    end=end,
                    text=part.text.strip(),
                    language=getattr(info, "language", None),
                    confidence=self._confidence(getattr(part, "avg_logprob", None)),
                )
            )
        elapsed = time.perf_counter() - started
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        if duration <= 0 and segments:
            duration = max(segment.end for segment in segments)
        detected_language = str(getattr(info, "language", None) or language or "auto")
        metadata = {
            "language_probability": getattr(info, "language_probability", None),
            "device": self.device,
            "compute_type": self.compute_type,
            "model_load_seconds": model_load_seconds,
        }
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=detected_language,
            segments=segments,
            audio_seconds=duration,
            elapsed_seconds=elapsed,
            metadata=metadata,
        )
