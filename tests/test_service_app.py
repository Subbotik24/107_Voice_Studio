from pathlib import Path

from hermes_voice_studio.dictionary import DictionaryRule, TerminologyDictionary
from hermes_voice_studio.engines.base import EngineResult
from hermes_voice_studio.models import Segment
from hermes_voice_studio.service import TranscriptionService
from hermes_voice_studio.storage import LocalStore


class FakeEngine:
    name = "fake"
    model_name = "fake-v1"

    def transcribe(self, source: Path, language: str | None) -> EngineResult:
        assert source.exists()
        return EngineResult(
            engine=self.name,
            model=self.model_name,
            language=language or "uk",
            segments=[Segment(0, 1, "гермес готовий.")],
            audio_seconds=1.0,
            elapsed_seconds=0.25,
        )


def test_preserves_raw_and_applies_dictionary(tmp_path, make_wav):
    source = make_wav(tmp_path / "input.wav")
    store = LocalStore(tmp_path / "data")
    service = TranscriptionService(
        store,
        FakeEngine(),
        TerminologyDictionary([DictionaryRule("гермес", "Hermes")]),
    )
    result = service.run(source, "uk", "keep")
    assert result.raw_text == "гермес готовий."
    assert result.corrected_text == "Hermes готовий."
    assert result.segments[0].display_text == "Hermes готовий."
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
