from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model_catalog import ModelCatalog
from ..models import Settings
from .base import SpeechEngine
from .faster_whisper import FasterWhisperEngine
from .hermes import HermesWhisperEngine
from .openai_cloud import OpenAICloudEngine


@dataclass(frozen=True)
class EngineKey:
    engine: str
    model: str
    bundle: str
    device: str
    compute_type: str
    cloud_model: str


class EngineManager:
    """Caches heavy model runtimes for the lifetime of the process."""

    def __init__(self, model_cache_directory: Path, model_catalog_directory: Path):
        self.model_cache_directory = model_cache_directory
        self.model_catalog = ModelCatalog(model_catalog_directory)
        self._engines: dict[EngineKey, SpeechEngine] = {}

    def get(self, settings: Settings) -> SpeechEngine:
        settings.validate()
        bundle = str(Path(settings.hermes_bundle).expanduser()) if settings.hermes_bundle else ""
        key = EngineKey(
            engine=settings.engine,
            model=settings.model,
            bundle=bundle,
            device=settings.device,
            compute_type=settings.compute_type,
            cloud_model=settings.openai_transcription_model,
        )
        engine = self._engines.get(key)
        if engine is not None:
            return engine
        if settings.engine == "faster-whisper":
            model_path = self.model_catalog.resolve(settings.model)
            engine = FasterWhisperEngine(
                str(model_path),
                device=settings.device,
                compute_type=settings.compute_type,
                display_name=settings.model,
            )
        elif settings.engine == "hermes-whisper":
            if not settings.hermes_bundle:
                raise ValueError("Select a trained .hws model bundle for Hermes Whisper")
            engine = HermesWhisperEngine(
                settings.hermes_bundle,
                cache_directory=self.model_cache_directory / "hermes-bundles",
                device=settings.device,
            )
        elif settings.engine == "openai-cloud":
            engine = OpenAICloudEngine(settings.openai_transcription_model)
        else:  # Settings.validate normally catches this.
            raise ValueError(f"unsupported engine: {settings.engine}")
        self._engines[key] = engine
        return engine

    def clear(self) -> None:
        self._engines.clear()
