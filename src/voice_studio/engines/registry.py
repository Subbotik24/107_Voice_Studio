from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model_catalog import ModelCatalog
from ..models import Settings
from .base import SpeechEngine
from .faster_whisper import FasterWhisperEngine
from .ollama_audio import OllamaAudioEngine
from .openai_cloud import OpenAICloudEngine


@dataclass(frozen=True)
class EngineKey:
    engine: str
    model: str
    device: str
    compute_type: str
    vad_filter: bool
    cloud_model: str
    ollama_model: str


class EngineManager:
    """Caches heavy model runtimes for the lifetime of the process."""

    def __init__(self, model_cache_directory: Path, model_catalog_directory: Path):
        self.model_cache_directory = model_cache_directory
        self.model_catalog = ModelCatalog(model_catalog_directory)
        self._engines: dict[EngineKey, SpeechEngine] = {}

    def get(self, settings: Settings) -> SpeechEngine:
        settings.validate()
        key = EngineKey(
            engine=settings.engine,
            model=settings.model,
            device=settings.device,
            compute_type=settings.compute_type,
            vad_filter=settings.vad_filter,
            cloud_model=settings.openai_transcription_model,
            ollama_model=settings.ollama_model,
        )
        engine = self._engines.get(key)
        if engine is not None:
            return engine
        if settings.engine == "ollama":
            engine = OllamaAudioEngine(settings.ollama_model)
        elif settings.engine == "faster-whisper":
            model_path = self.model_catalog.resolve(settings.model)
            engine = FasterWhisperEngine(
                str(model_path),
                device=settings.device,
                compute_type=settings.compute_type,
                display_name=settings.model,
                vad_filter=settings.vad_filter,
            )
        elif settings.engine == "openai-cloud":
            engine = OpenAICloudEngine(settings.openai_transcription_model)
        else:  # Settings.validate normally catches this.
            raise ValueError(f"unsupported engine: {settings.engine}")
        self._engines[key] = engine
        return engine

    def clear(self) -> None:
        self._engines.clear()
