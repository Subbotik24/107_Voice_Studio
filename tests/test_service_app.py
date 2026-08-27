from pathlib import Path

import pytest

from voice_studio.dictionary import DictionaryRule, TerminologyDictionary
from voice_studio.engines.base import EngineResult
from voice_studio.models import Segment
from voice_studio.service import TranscriptionService
from voice_studio.storage import LocalStore


class FakeEngine:
    name = "fake"
    model_name = "fake-v1"

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        assert source.exists()
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=language or "uk",
            segments=[Segment(0, 1, "войс готовий.")],
            audio_seconds=1.0,
            elapsed_seconds=0.25,
        )


def test_preserves_raw_and_applies_dictionary(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    service = TranscriptionService(
        store,
        FakeEngine(),
        TerminologyDictionary([DictionaryRule("войс", "VOICE")]),
    )
    result = service.run(source, "uk", "keep")
    assert result.raw_text == "войс готовий."
    assert result.corrected_text == "VOICE готовий."
    assert result.segments[0].display_text == "VOICE готовий."
    assert result.engine == "fake"
    assert result.real_time_factor == 0.25
    assert result.audio_retained
    assert store.get(result.id) == result


def test_delete_after_transcription_removes_only_managed_copy(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    result = TranscriptionService(store, FakeEngine()).run(
        source, "cs", "delete_after_transcription"
    )
    assert source.exists()
    assert not result.audio_retained
    assert result.source_path is None


class FailingEngine:
    name = "failing"
    model_name = "failing-v1"

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        raise RuntimeError("synthetic failure")


def test_failed_transcription_does_not_leave_unreferenced_managed_copy(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    service = TranscriptionService(store, FailingEngine())
    try:
        service.run(source, "uk", "keep")
    except RuntimeError as exc:
        assert str(exc) == "synthetic failure"
    else:
        raise AssertionError("expected failure")
    assert source.exists()
    assert list(store.sources.iterdir()) == []


class InterruptedEngine:
    name = "interrupted"
    model_name = "interrupted-v1"

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        raise KeyboardInterrupt


def test_interrupted_transcription_cleans_managed_copy(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    service = TranscriptionService(store, InterruptedEngine())
    try:
        service.run(source, "uk", "keep")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected KeyboardInterrupt")
    assert source.exists()
    assert list(store.sources.iterdir()) == []


class FailingStore(LocalStore):
    def save(self, transcript):
        raise OSError("synthetic database failure")


def test_save_failure_cleans_managed_copy(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = FailingStore(tmp_path / "data")
    service = TranscriptionService(store, FakeEngine())
    try:
        service.run(source, "uk", "keep")
    except OSError as exc:
        assert str(exc) == "synthetic database failure"
    else:
        raise AssertionError("expected save failure")
    assert source.exists()
    assert list(store.sources.iterdir()) == []


def test_cancel_during_prepare_does_not_start_engine_request(tmp_path, make_wav):
    from voice_studio.jobs import JobCancelled
    from voice_studio.operation import OperationBudget

    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    requests = []

    class RecordingEngine(FakeEngine):
        def transcribe(self, source: Path, language: str | None) -> EngineResult:
            requests.append(source)
            return super().transcribe(source, language)

    budget = OperationBudget(10.0, cancelled=lambda: True)
    with pytest.raises(JobCancelled, match="prepare|import"):
        TranscriptionService(store, RecordingEngine()).run(
            source, "uk", "keep", budget=budget
        )

    assert requests == []
    assert source.exists()
    assert list(store.sources.iterdir()) == []


def test_store_commit_wins_after_pre_save_checkpoint(tmp_path, make_wav):
    from voice_studio.operation import OperationBudget

    source = make_wav(tmp_path / "input.wav")
    cancelled = False

    class CommitWinsStore(LocalStore):
        def save(self, transcript):
            nonlocal cancelled
            cancelled = True
            super().save(transcript)

    store = CommitWinsStore(tmp_path / "data")
    budget = OperationBudget(10.0, cancelled=lambda: cancelled)
    result = TranscriptionService(store, FakeEngine()).run(
        source, "uk", "keep", budget=budget
    )

    assert cancelled is True
    assert store.get(result.id).raw_text == result.raw_text
    assert result.audio_retained


def test_validation_after_import_cleans_only_unreferenced_managed_snapshot(
    tmp_path, make_wav, monkeypatch
):
    from voice_studio import service as service_module

    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")
    validated = []

    def reject_managed(path, **_kwargs):
        validated.append(path)
        if path.parent.resolve() == store.sources.resolve():
            raise ValueError("validation failed")

    monkeypatch.setattr(service_module, "validate_media_file", reject_managed)
    with pytest.raises(ValueError, match="validation failed"):
        TranscriptionService(store, FakeEngine()).run(source, "uk", "keep")

    assert len(validated) == 1
    assert validated[0].parent.resolve() == store.sources.resolve()
    assert source.exists()
    assert list(store.sources.iterdir()) == [unrelated]
