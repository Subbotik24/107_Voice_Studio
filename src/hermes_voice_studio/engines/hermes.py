from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from hermes_whisper.bundle import load_model_bundle
from hermes_whisper.decoding import transcribe_file

from ..media import canonical_wav
from ..models import Segment
from .base import EngineResult


class HermesWhisperEngine:
    name = "hermes-whisper"

    def __init__(self, bundle: str | Path, cache_directory: Path, device: str = "auto"):
        self.bundle = Path(bundle).expanduser()
        self.cache_directory = cache_directory
        self.device = device
        self.model_name = self.bundle.stem or "hermes-whisper"
        self._runtime: tuple[Any, Any, Any, dict[str, Any]] | None = None

    def _load(self) -> tuple[Any, Any, Any, dict[str, Any]]:
        if self._runtime is not None:
            return self._runtime
        if not self.bundle.is_file():
            raise FileNotFoundError(f"Hermes model bundle does not exist: {self.bundle}")
        self._runtime = load_model_bundle(
            self.bundle,
            cache_directory=self.cache_directory,
            device=self.device,
        )
        self.model_name = str(self._runtime[3].get("model_name") or self.bundle.stem)
        return self._runtime

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        if language == "en":
            raise ValueError("Hermes Whisper 0.1 supports only Ukrainian and Czech")
        was_loaded = self._runtime is not None
        load_started = time.perf_counter()
        model, config, tokenizer, metadata = self._load()
        model_load_seconds = 0.0 if was_loaded else time.perf_counter() - load_started
        started = time.perf_counter()
        with canonical_wav(source, sample_rate=config.audio.sample_rate) as audio_path:
            result = transcribe_file(
                model,
                config,
                tokenizer,
                audio_path,
                language=language or "auto",
                device=self.device,
            )
        elapsed = time.perf_counter() - started
        segments = [
            Segment(
                start=chunk.start,
                end=chunk.end,
                text=chunk.text,
                language=chunk.language,
                confidence=chunk.token_confidence,
            )
            for chunk in result.chunks
        ]
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=result.language,
            segments=segments,
            audio_seconds=result.audio_seconds,
            elapsed_seconds=elapsed,
            metadata={
                "bundle": str(self.bundle.resolve()),
                "bundle_sha256": metadata.get("bundle_sha256"),
                "checkpoint_step": metadata.get("checkpoint_step", metadata.get("step")),
                "device": str(self.device),
                "model_load_seconds": model_load_seconds,
            },
        )
