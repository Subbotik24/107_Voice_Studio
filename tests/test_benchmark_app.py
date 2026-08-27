import json

import pytest

from voice_studio.benchmark import run_benchmark
from voice_studio.engines.base import EngineResult
from voice_studio.models import Segment, Settings


class FakeEngine:
    def transcribe(self, _source, language):
        return EngineResult(
            engine="fixture",
            model="fixture",
            language=language,
            segments=[Segment(0, 1, "reference text")],
            audio_seconds=1,
            elapsed_seconds=0.1,
            metadata={"model_load_seconds": 0.01},
        )


class FakeManager:
    def __init__(self, _cache, _models):
        pass

    def get(self, _settings):
        return FakeEngine()


def test_benchmark_measures_without_claiming_accuracy(tmp_path, make_wav, monkeypatch):
    audio = make_wav(tmp_path / "safe.wav")
    manifest = tmp_path / "benchmark.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "audio": audio.name,
                "text": "reference text",
                "language": "en",
                "license": "CC0-1.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("voice_studio.benchmark.EngineManager", FakeManager)
    result = run_benchmark(
        manifest,
        Settings(model="fixture"),
        cache_directory=tmp_path / "cache",
        model_directory=tmp_path / "models",
    )
    assert result["status"] == "MEASURED"
    assert result["wer"] == 0
    assert result["cer"] == 0
    assert "No production accuracy claim" in result["claim"]


def test_benchmark_requires_license(tmp_path, make_wav):
    audio = make_wav(tmp_path / "safe.wav")
    manifest = tmp_path / "benchmark.jsonl"
    manifest.write_text(
        json.dumps({"audio": audio.name, "text": "text", "language": "uk"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="license"):
        run_benchmark(
            manifest,
            Settings(model="fixture"),
            cache_directory=tmp_path / "cache",
            model_directory=tmp_path / "models",
        )
