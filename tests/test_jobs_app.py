import os
import time
from pathlib import Path
from typing import Any

import pytest

from voice_studio.dictionary import TerminologyDictionary
from voice_studio.engines.base import EngineResult
from voice_studio.jobs import JobCancelled, TranscriptionJobController
from voice_studio.models import Segment, Settings
from voice_studio.process_lifecycle import _dispose_queue, _stop_process
from voice_studio.storage import LocalStore


class StubbornProcess:
    def __init__(self):
        self.events = []
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.events.append("terminate")

    def join(self, timeout=None):
        self.events.append(("join", timeout))

    def kill(self):
        self.events.append("kill")
        self.alive = False


class RecordingQueue:
    def __init__(self):
        self.cancel_calls = 0
        self.close_calls = 0

    def cancel_join_thread(self):
        self.cancel_calls += 1

    def close(self):
        self.close_calls += 1


def test_stop_process_escalates_with_bounded_joins():
    process = StubbornProcess()

    _stop_process(process)

    assert process.events == ["terminate", ("join", 5), "kill", ("join", 2)]
    assert not process.is_alive()


def test_dispose_queue_is_idempotent_and_never_joins_feeder():
    queue_object = RecordingQueue()

    _dispose_queue(queue_object)
    _dispose_queue(queue_object)

    assert queue_object.cancel_calls == 1
    assert queue_object.close_calls == 1


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


def cleanup_worker(requests: Any, results: Any, _cache: str, _models: str) -> None:
    while True:
        request = requests.get()
        if request is None:
            return
        if request.get("action") == "cleanup":
            results.put(
                {
                    "job_id": request["job_id"],
                    "ok": True,
                    "proposal": {
                        "corrected_text": "Cleaned local result.",
                        "segments": [
                            {"segment_index": 0, "corrected_text": "Cleaned local result."}
                        ],
                        "changes": ["punctuation"],
                    },
                }
            )
            continue
        results.put(
            {
                "job_id": request["job_id"],
                "ok": True,
                "result": EngineResult(
                    engine="ollama",
                    model="gemma4:12b",
                    language=request["language"],
                    segments=[Segment(0, 1, "raw local result")],
                    audio_seconds=1,
                    elapsed_seconds=0.1,
                ),
            }
        )


def failing_cleanup_worker(requests: Any, results: Any, _cache: str, _models: str) -> None:
    while True:
        request = requests.get()
        if request is None:
            return
        if request.get("action") == "cleanup":
            results.put(
                {
                    "job_id": request["job_id"],
                    "ok": False,
                    "error": "RuntimeError: Ollama cleanup failed locally",
                }
            )
            continue
        results.put(
            {
                "job_id": request["job_id"],
                "ok": True,
                "result": EngineResult(
                    engine="ollama",
                    model="gemma4:12b",
                    language=request["language"],
                    segments=[Segment(0, 1, "raw local result")],
                    audio_seconds=1,
                    elapsed_seconds=0.1,
                ),
            }
        )


def hanging_cleanup_worker(requests: Any, results: Any, _cache: str, _models: str) -> None:
    request = requests.get()
    results.put(
        {
            "job_id": request["job_id"],
            "ok": True,
            "result": EngineResult(
                engine="ollama",
                model="gemma4:12b",
                language=request["language"],
                segments=[Segment(0, 1, "raw local result")],
                audio_seconds=1,
                elapsed_seconds=0.1,
            ),
        }
    )
    requests.get()
    time.sleep(30)


def test_local_ollama_profile_applies_cleanup_without_mutating_raw_text(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=cleanup_worker,
    )
    phases: list[str] = []
    try:
        transcript = controller.run(
            source,
            Settings(ollama_model="gemma4:12b"),
            TerminologyDictionary(),
            progress=lambda phase, _elapsed: phases.append(phase),
        )
    finally:
        controller.close()

    assert transcript.raw_text == "raw local result"
    assert transcript.segments[0].text == "raw local result"
    assert transcript.corrected_text == "Cleaned local result."
    assert transcript.segments[0].corrected_text == "Cleaned local result."
    assert transcript.metadata["automatic_cleanup"] == "applied"
    assert phases == [
        "importing",
        "loading",
        "transcribing",
        "saving",
        "cleaning",
        "completed",
    ]


def test_inconsistent_ollama_profile_cannot_start_cloud_cleanup(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(store, tmp_path / "cache")
    settings = Settings(
        cleanup_provider="openai",
        offline_only=False,
        ollama_model="gemma4:12b",
    )
    try:
        with pytest.raises(ValueError, match="inconsistent settings for profile"):
            controller.run(source, settings, TerminologyDictionary())
    finally:
        controller.close()

    assert store.list() == []


def test_local_cleanup_failure_keeps_the_saved_transcript(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=failing_cleanup_worker,
    )
    try:
        transcript = controller.run(
            source,
            Settings(ollama_model="gemma4:12b"),
            TerminologyDictionary(),
        )
    finally:
        controller.close()

    assert transcript.raw_text == "raw local result"
    assert transcript.corrected_text == "raw local result"
    assert transcript.metadata["automatic_cleanup"] == "failed"
    assert transcript.metadata["cleanup_warning"] == (
        "RuntimeError: Ollama cleanup failed locally"
    )
    assert store.get(transcript.id).raw_text == "raw local result"


def test_cancelling_local_cleanup_terminates_worker_but_returns_raw_transcript(
    tmp_path,
    make_wav,
):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=hanging_cleanup_worker,
    )
    phase = [""]
    try:
        transcript = controller.run(
            source,
            Settings(ollama_model="gemma4:12b"),
            TerminologyDictionary(),
            progress=lambda current, _elapsed: phase.__setitem__(0, current),
            cancelled=lambda: phase[0] == "cleaning",
        )
        assert controller._process is None
    finally:
        controller.close()

    assert transcript.raw_text == "raw local result"
    assert transcript.metadata["automatic_cleanup"] == "cancelled"
    assert store.get(transcript.id).raw_text == "raw local result"


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


def test_timeout_restarts_cleanly_for_next_job(tmp_path, make_wav, monkeypatch):
    from voice_studio import service as service_module

    # This test owns the inference-worker deadline. The real disposable media
    # probe has its own integration coverage and Windows spawn startup can
    # legitimately exceed this deliberately tiny 200 ms test budget.
    monkeypatch.setattr(service_module, "validate_media_file", lambda *a, **k: None)
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
    from voice_studio import operation
    from voice_studio import service as service_module

    # Keep the test clock authoritative. A real child uses wall-clock time and
    # would turn this budget test into a platform-dependent process-start test.
    monkeypatch.setattr(service_module, "validate_media_file", lambda *a, **k: None)

    class ConsumingStore(LocalStore):
        def import_source(self, path, budget=None, *, max_bytes=None):
            result = super().import_source(path, budget, max_bytes=max_bytes)
            clock[0] = 0.8
            return result

    source = make_wav(tmp_path / "original.wav")
    clock = [0.0]
    monkeypatch.setattr(operation, "_monotonic", lambda: clock[0])
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
