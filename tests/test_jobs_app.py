import os
import time
from pathlib import Path
from typing import Any

import pytest

from hermes_voice_studio.dictionary import TerminologyDictionary
from hermes_voice_studio.engines.base import EngineResult
from hermes_voice_studio.jobs import JobCancelled, TranscriptionJobController
from hermes_voice_studio.models import Segment, Settings
from hermes_voice_studio.storage import LocalStore


def fixture_worker(requests: Any, results: Any, _cache: str, _models: str) -> None:
    while True:
        request = requests.get()
        if request is None:
            return
        results.put(
            {
                "job_id": request["job_id"],
                "ok": True,
                "result": EngineResult(
                    engine="fixture",
                    model="fixture",
                    language=request["language"],
                    segments=[Segment(0, 1, "local result")],
                    audio_seconds=1,
                    elapsed_seconds=0.1,
                    metadata={"worker_pid": os.getpid()},
                ),
            }
        )


def slow_worker(requests: Any, _results: Any, _cache: str, _models: str) -> None:
    requests.get()
    time.sleep(30)


def recording_worker(requests: Any, _results: Any, cache: str, _models: str) -> None:
    marker = Path(cache) / "started"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("started", encoding="utf-8")
    requests.get()


def idle_worker(requests: Any, _results: Any, _cache: str, _models: str) -> None:
    requests.get()


def test_spawn_worker_is_reused_for_completed_jobs(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=fixture_worker,
    )
    phases = []
    try:
        first = controller.run(
            source,
            Settings(model="fixture"),
            TerminologyDictionary(),
            progress=lambda phase, _elapsed: phases.append(phase),
        )
        first_pid = first.metadata["worker_pid"]
        second = controller.run(
            source,
            Settings(model="fixture"),
            TerminologyDictionary(),
        )
        assert second.metadata["worker_pid"] == first_pid
    finally:
        controller.close()
    assert phases == ["importing", "loading", "transcribing", "saving", "completed"]
    assert source.exists()


def test_cancel_terminates_worker_and_cleans_managed_copy(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=slow_worker,
    )
    started = time.monotonic()
    try:
        with pytest.raises(JobCancelled):
            controller.run(
                source,
                Settings(model="fixture"),
                TerminologyDictionary(),
                cancelled=lambda: time.monotonic() - started > 0.2,
            )
    finally:
        controller.close()
    assert source.exists()
    assert list(store.sources.iterdir()) == []


def test_timeout_restarts_cleanly_for_next_job(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=slow_worker,
    )
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            controller.run(
                source,
                Settings(model="fixture"),
                TerminologyDictionary(),
                timeout_seconds=0.2,
            )
        assert list(store.sources.iterdir()) == []
        controller.worker_target = fixture_worker
        recovered = controller.run(
            source,
            Settings(model="fixture"),
            TerminologyDictionary(),
        )
    finally:
        controller.close()

    assert recovered.raw_text == "local result"
    assert source.exists()


def test_cancel_during_prepare_never_starts_worker_request(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    marker = tmp_path / "cache" / "started"

    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=recording_worker,
    )
    try:
        with pytest.raises(JobCancelled, match="prepare|import"):
            controller.run(
                source,
                Settings(model="fixture"),
                TerminologyDictionary(),
                cancelled=lambda: True,
            )
    finally:
        controller.close()

    assert not marker.exists()
    assert source.exists()
    assert list(store.sources.iterdir()) == []


def test_prepare_consumed_deadline_is_not_reset_for_inference(tmp_path, make_wav, monkeypatch):
    from hermes_voice_studio import operation

    class ConsumingStore(LocalStore):
        def import_source(self, path, budget=None, *, max_bytes=None):
            result = super().import_source(path, budget, max_bytes=max_bytes)
            clock[0] = 0.8
            return result

    source = make_wav(tmp_path / "original.wav")
    clock = [0.0]
    monkeypatch.setattr(operation.time, "monotonic", lambda: clock[0])
    original_remaining = operation.OperationBudget.remaining

    def consume_remaining(self, phase, ceiling=None):
        result = original_remaining(self, phase, ceiling)
        if phase == "inference":
            clock[0] = 1.1
        return result

    monkeypatch.setattr(operation.OperationBudget, "remaining", consume_remaining)
    controller = TranscriptionJobController(
        ConsumingStore(tmp_path / "data"),
        tmp_path / "cache",
        worker_target=idle_worker,
    )
    try:
        with pytest.raises(TimeoutError, match="inference"):
            controller.run(
                source,
                Settings(model="fixture", task_timeout_seconds=1),
                TerminologyDictionary(),
            )
    finally:
        controller.close()

    assert source.exists()
