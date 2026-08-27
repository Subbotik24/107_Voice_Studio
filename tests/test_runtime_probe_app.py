from __future__ import annotations

from pathlib import Path

from voice_studio.runtime_probe import run_frozen_worker_probe


def test_frozen_worker_probe_exercises_spawn_media_and_job_paths(tmp_path: Path) -> None:
    result = run_frozen_worker_probe(tmp_path)

    assert result == {
        "status": "PASS",
        "engine": "runtime-probe",
        "raw_text": "VOICE Studio worker probe",
        "saved": True,
    }
