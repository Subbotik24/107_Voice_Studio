import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from voice_studio.dictionary import TerminologyDictionary
from voice_studio.engines.base import EngineResult
from voice_studio.jobs import JobCancelled, TranscriptionJobController
from voice_studio.models import Segment, Settings
from voice_studio.operation import OperationBudget
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


class ControllerQueue:
    def __init__(self):
        self.items = []
        self.cancel_calls = 0
        self.close_calls = 0

    def put(self, value):
        self.items.append(value)

    def cancel_join_thread(self):
        self.cancel_calls += 1

    def close(self):
        self.close_calls += 1


class ControllerProcess:
    def __init__(self, context):
        self.context = context
        self.alive = False
        self.start_calls = 0
        self.join_calls = []

    def start(self):
        self.start_calls += 1
        if self.start_calls == 1 and len(self.context.processes) == 1:
            self.context.first_start_started.set()
            self.context.first_start_release.wait(timeout=5)
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)
        if timeout == 3:
            self.alive = False

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False


class ControllerContext:
    def __init__(self):
        self.processes = []
        self.queues = []
        self.first_start_started = threading.Event()
        self.first_start_release = threading.Event()

    def Queue(self):
        result = ControllerQueue()
        self.queues.append(result)
        return result

    def Process(self, **_kwargs):
        result = ControllerProcess(self)
        self.processes.append(result)
        return result


def test_concurrent_ensure_worker_returns_one_generation(tmp_path):
    context = ControllerContext()
    controller = TranscriptionJobController(LocalStore(tmp_path / "data"), tmp_path / "cache")
    controller._context = context
    generations = []

    def ensure():
        generations.append(controller._ensure_worker())

    first = threading.Thread(target=ensure)
    second = threading.Thread(target=ensure)
    first.start()
    assert context.first_start_started.wait(timeout=2)
    second.start()
    time.sleep(0.1)
    context.first_start_release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    try:
        assert generations[0] is generations[1]
        assert len(context.processes) == 1
        assert context.processes[0].start_calls == 1
    finally:
        controller.close()


def test_close_detaches_and_disposes_generation_once(tmp_path):
    context = ControllerContext()
    controller = TranscriptionJobController(LocalStore(tmp_path / "data"), tmp_path / "cache")
    controller._context = context
    controller._ensure_worker()

    controller.close()
    controller.close()

    assert context.queues[0].cancel_calls == 1
    assert context.queues[0].close_calls == 1
    assert context.queues[1].cancel_calls == 1
    assert context.queues[1].close_calls == 1
    assert controller._process is None


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


def test_close_during_slow_run_maps_to_job_cancelled(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=slow_worker,
    )
    running = threading.Event()
    outcome = []

    def run_job():
        try:
            controller.run(
                source,
                Settings(model="fixture"),
                TerminologyDictionary(),
                progress=lambda phase, _elapsed: running.set()
                if phase == "transcribing"
                else None,
            )
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run_job)
    thread.start()
    assert running.wait(timeout=10)
    controller.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], JobCancelled)
    assert source.exists()


def test_close_during_prepare_cancels_before_worker_creation(tmp_path, make_wav, monkeypatch):
    from voice_studio import service as service_module

    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(store, tmp_path / "cache")
    context = ControllerContext()
    controller._context = context
    prepare_started = threading.Event()
    release_prepare = threading.Event()
    original_prepare = service_module.TranscriptionService.prepare

    def blocked_prepare(service, *args, **kwargs):
        prepared = original_prepare(service, *args, **kwargs)
        prepare_started.set()
        assert release_prepare.wait(timeout=5)
        return prepared

    monkeypatch.setattr(service_module.TranscriptionService, "prepare", blocked_prepare)
    outcome = []

    def run_job():
        try:
            controller.run(source, Settings(model="fixture"), TerminologyDictionary())
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run_job)
    thread.start()
    assert prepare_started.wait(timeout=10)
    assert controller._generation is None
    controller.close()
    release_prepare.set()
    context.first_start_release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], JobCancelled)
    assert context.processes == []
    assert controller._generation is None
    assert list(store.sources.iterdir()) == []
    assert source.exists()


def test_close_between_loading_checkpoint_and_worker_creation_cancels_run(
    tmp_path,
    make_wav,
    monkeypatch,
):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(store, tmp_path / "cache")
    context = ControllerContext()
    context.first_start_release.set()
    controller._context = context
    ensure_started = threading.Event()
    release_ensure = threading.Event()
    original_ensure = controller._ensure_worker

    def paused_ensure(expected_epoch=None):
        ensure_started.set()
        assert release_ensure.wait(timeout=5)
        if expected_epoch is None:
            return original_ensure()
        return original_ensure(expected_epoch=expected_epoch)

    monkeypatch.setattr(controller, "_ensure_worker", paused_ensure)
    outcome = []

    def run_job():
        try:
            controller.run(source, Settings(model="fixture"), TerminologyDictionary())
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run_job)
    thread.start()
    assert ensure_started.wait(timeout=10)
    assert controller._generation is None
    controller.close()
    release_ensure.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], JobCancelled)
    assert context.queues == []
    assert context.processes == []
    assert controller._generation is None
    assert list(store.sources.iterdir()) == []
    assert source.exists()


def test_close_immediately_after_generation_creation_detaches_safely(
    tmp_path,
    make_wav,
    monkeypatch,
):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(store, tmp_path / "cache")
    context = ControllerContext()
    context.first_start_release.set()
    controller._context = context
    generation_created = threading.Event()
    release_ensure = threading.Event()
    original_ensure = controller._ensure_worker

    def paused_ensure(expected_epoch=None):
        generation = original_ensure(expected_epoch=expected_epoch)
        generation_created.set()
        assert release_ensure.wait(timeout=5)
        return generation

    monkeypatch.setattr(controller, "_ensure_worker", paused_ensure)
    outcome = []

    def run_job():
        try:
            controller.run(source, Settings(model="fixture"), TerminologyDictionary())
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run_job)
    thread.start()
    assert generation_created.wait(timeout=10)
    assert controller._generation is not None
    controller.close()
    release_ensure.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], JobCancelled)
    assert controller._generation is None
    assert context.processes[0].alive is False
    assert all(queue_object.cancel_calls == 1 for queue_object in context.queues)
    assert all(queue_object.close_calls == 1 for queue_object in context.queues)
    assert list(store.sources.iterdir()) == []
    assert source.exists()


def test_run_after_close_starts_a_new_generation(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    controller = TranscriptionJobController(
        LocalStore(tmp_path / "data"),
        tmp_path / "cache",
        worker_target=fixture_worker,
    )
    controller.close()
    try:
        transcript = controller.run(
            source,
            Settings(model="fixture"),
            TerminologyDictionary(),
        )
    finally:
        controller.close()
    assert transcript.raw_text == "local result"


def test_concurrent_runs_are_serialized_and_keep_their_results(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    controller = TranscriptionJobController(
        LocalStore(tmp_path / "data"),
        tmp_path / "cache",
        worker_target=fixture_worker,
    )
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def run_job():
        try:
            barrier.wait()
            results.append(
                controller.run(
                    source,
                    Settings(model="fixture"),
                    TerminologyDictionary(),
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_job) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    controller.close()

    assert not errors
    assert len(results) == 2
    assert {result.raw_text for result in results} == {"local result"}


def test_stopped_worker_disposes_both_queues_before_error(tmp_path):
    context = ControllerContext()
    controller = TranscriptionJobController(LocalStore(tmp_path / "data"), tmp_path / "cache")
    controller._context = context
    generation = controller._ensure_worker()
    generation.process.alive = False

    with pytest.raises(RuntimeError, match="worker stopped unexpectedly"):
        controller._wait_for_result(
            generation,
            "missing",
            OperationBudget(1),
            "inference",
        )

    assert context.queues[0].cancel_calls == 1
    assert context.queues[0].close_calls == 1
    assert context.queues[1].cancel_calls == 1
    assert context.queues[1].close_calls == 1


def test_controller_subprocess_closes_without_resource_tracker_warning(tmp_path):
    script = """
from pathlib import Path
import wave
from voice_studio.dictionary import TerminologyDictionary
from voice_studio.jobs import TranscriptionJobController
from voice_studio.models import Settings
from voice_studio.storage import LocalStore
from tests.test_jobs_app import fixture_worker

source = Path(r'{source}')
source.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(source), 'wb') as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b'\\0\\0' * 1600)
controller = TranscriptionJobController(
    LocalStore(Path(r'{data}')), Path(r'{cache}'), worker_target=fixture_worker
)
controller.run(source, Settings(model='fixture'), TerminologyDictionary())
controller.close()
""".format(
        source=tmp_path / "original.wav",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "resource_tracker" not in result.stderr
    assert "leaked semaphore" not in result.stderr


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


def test_cancelling_cleanup_submission_returns_committed_transcript(tmp_path, make_wav):
    source = make_wav(tmp_path / "original.wav")
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=cleanup_worker,
    )
    original_submit = controller._submit

    def flaky_submit(generation, request):
        if request.get("action") == "cleanup":
            raise JobCancelled("transcription worker was closed")
        return original_submit(generation, request)

    controller._submit = flaky_submit
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
    assert transcript.metadata["automatic_cleanup"] == "cancelled"
    assert store.get(transcript.id).raw_text == "raw local result"
    assert store.get(transcript.id).metadata["automatic_cleanup"] == "cancelled"


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


def test_run_rejects_mutated_hardware_settings_before_prepare_or_worker(
    tmp_path, make_wav, monkeypatch
):
    source = make_wav(tmp_path / "original.wav")
    settings = Settings(model="fixture")
    settings.device = "rocm"
    store = LocalStore(tmp_path / "data")
    controller = TranscriptionJobController(
        store,
        tmp_path / "cache",
        worker_target=recording_worker,
    )
    prepare_called = False
    worker_called = False

    def forbidden_prepare(*_args, **_kwargs):
        nonlocal prepare_called
        prepare_called = True
        pytest.fail("source preparation must not run for invalid settings")

    def forbidden_ensure(*_args, **_kwargs):
        nonlocal worker_called
        worker_called = True
        pytest.fail("worker startup must not run for invalid settings")

    monkeypatch.setattr("voice_studio.jobs.TranscriptionService.prepare", forbidden_prepare)
    monkeypatch.setattr(controller, "_ensure_worker", forbidden_ensure)
    with pytest.raises(ValueError, match="device.*rocm"):
        controller.run(source, settings, TerminologyDictionary())

    assert not prepare_called
    assert not worker_called
    assert source.exists()
    controller.close()


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
                Settings(model="fixture"),
                TerminologyDictionary(),
                timeout_seconds=1,
            )
    finally:
        controller.close()

    assert source.exists()
