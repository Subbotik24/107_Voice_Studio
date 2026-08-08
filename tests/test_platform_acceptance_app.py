from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes_voice_studio.models import Segment, Transcript
from scripts.platform_acceptance import run_acceptance


class FixtureController:
    def __init__(self, _store, _cache: Path):
        self.index = 0

    def run(self, source, settings, _dictionary, *, progress):
        for phase in ("importing", "loading", "transcribing", "saving", "completed"):
            progress(phase, self.index / 100)
        self.index += 1
        return Transcript(
            id=f"fixture-{self.index}",
            created_at=datetime.now(UTC).isoformat(),
            source_name=source.name,
            source_sha256="a" * 64,
            language=settings.language,
            engine="fixture",
            model="fixture",
            raw_text="local fixture",
            corrected_text="local fixture",
            segments=[Segment(0, 1, "local fixture")],
            audio_retained=settings.retention == "keep",
        )

    def close(self):
        return None


def test_acceptance_log_contains_task_evidence_and_source_hashes(tmp_path):
    fixtures = []
    for suffix in (".wav", ".mp3", ".m4a", ".mp4"):
        path = tmp_path / f"fixture{suffix}"
        path.write_bytes(f"fixture-{suffix}".encode())
        fixtures.append(path)

    result = run_acceptance(
        "fixture",
        tmp_path / "run",
        tasks=50,
        fixtures=fixtures,
        controller_factory=FixtureController,
    )

    assert result["status"] == "PASS"
    assert result["tasks"] == 50
    assert result["crashes"] == 0
    assert result["originals_unchanged"]
    assert result["storage_audit"]["status"] == "PASS"
    assert len(result["task_results"]) == 50
    assert result["task_results"][0]["source"] == "external/fixture.wav"
    assert str(tmp_path) not in str(result["task_results"])
    assert result["task_results"][0]["source_sha256_before"] == result["task_results"][0][
        "source_sha256_after"
    ]
    assert [event["phase"] for event in result["task_results"][0]["phases"]] == [
        "importing",
        "loading",
        "transcribing",
        "saving",
        "completed",
    ]
    assert set(result["task_results"][0]["exports"]) == {"txt", "md", "json", "srt", "vtt"}


def test_acceptance_refuses_short_run(tmp_path):
    with pytest.raises(ValueError, match="at least 50"):
        run_acceptance("fixture", tmp_path, tasks=49, fixtures=[])
