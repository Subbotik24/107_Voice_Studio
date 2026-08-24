from __future__ import annotations

from pathlib import Path

from .dictionary import TerminologyDictionary
from .engines.base import EngineResult, SpeechEngine
from .media import validate_media_file
from .models import Segment, Transcript, utc_now
from .operation import OperationBudget
from .storage import LocalStore


class PreparedSource:
    def __init__(
        self,
        original: Path,
        managed: Path,
        sha256: str,
        ownership: object | None = None,
    ):
        self.original = original
        self.managed = managed
        self.sha256 = sha256
        self.ownership = ownership


class TranscriptionService:
    def __init__(
        self,
        store: LocalStore,
        engine: SpeechEngine | None,
        dictionary: TerminologyDictionary | None = None,
    ):
        self.store = store
        self.engine = engine
        self.dictionary = dictionary or TerminologyDictionary()

    def prepare(
        self,
        source: Path,
        retention: str,
        budget: OperationBudget | None = None,
        *,
        max_bytes: int | None = None,
    ) -> PreparedSource:
        prepared: PreparedSource | None = None
        try:
            if budget is not None:
                budget.checkpoint("prepare")
            if retention not in {"keep", "delete_after_transcription"}:
                raise ValueError(f"unsupported retention policy: {retention}")
            import_kwargs = {}
            if budget is not None:
                import_kwargs["budget"] = budget
            if max_bytes is not None:
                import_kwargs["max_bytes"] = max_bytes
            retained_source, source_hash = self.store.import_source(source, **import_kwargs)
            prepared = PreparedSource(
                source,
                retained_source,
                source_hash,
                self.store.source_ownership_token(retained_source),
            )
            if budget is not None:
                budget.checkpoint("prepare")
            validate_media_file(prepared.managed)
            if budget is not None:
                budget.checkpoint("prepare")
            return prepared
        except BaseException:
            if prepared is not None:
                self.cleanup(prepared)
            raise

    def finalize(
        self,
        prepared: PreparedSource,
        result: EngineResult,
        language: str,
        retention: str,
        budget: OperationBudget | None = None,
    ) -> Transcript:
        raw = result.text
        corrected = self.dictionary.apply(raw)
        corrected_segments = [
            Segment(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                corrected_text=self.dictionary.apply(segment.text),
                language=segment.language,
                confidence=segment.confidence,
            )
            for segment in result.segments
        ]
        transcript = Transcript(
            id=self.store.new_id(),
            created_at=utc_now(),
            source_name=prepared.original.name,
            source_sha256=prepared.sha256,
            source_path=str(prepared.managed),
            language=result.language or language,
            engine=result.engine,
            model=result.model,
            raw_text=raw,
            corrected_text=corrected,
            segments=corrected_segments,
            dictionary_version=self.dictionary.version,
            audio_seconds=result.audio_seconds,
            real_time_factor=result.real_time_factor,
            metadata=dict(result.metadata),
        )
        if budget is not None:
            budget.checkpoint("saving")
        self.store.save(transcript)
        if retention == "delete_after_transcription":
            self.store.delete_audio(transcript)
        return transcript

    def cleanup(self, prepared: PreparedSource) -> None:
        self.store.remove_unreferenced_source(
            prepared.managed,
            prepared.sha256,
            ownership=prepared.ownership,
        )

    def run(
        self,
        source: Path,
        language: str,
        retention: str,
        budget: OperationBudget | None = None,
        *,
        max_bytes: int | None = None,
    ) -> Transcript:
        if self.engine is None:
            raise RuntimeError("a speech engine is required for synchronous transcription")
        prepared = self.prepare(source, retention, budget, max_bytes=max_bytes)
        try:
            if budget is not None:
                budget.checkpoint("inference")
            result = self.engine.transcribe(prepared.managed, language)
            return self.finalize(prepared, result, language, retention, budget=budget)
        except BaseException:
            self.cleanup(prepared)
            raise
