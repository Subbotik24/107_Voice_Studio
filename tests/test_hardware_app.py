import json
import queue
import subprocess
import sys

from voice_studio import hardware


class _FakeQueue:
    def __init__(self):
        self.items = []
        self.closed = False
        self.cancelled = False

    def put(self, value):
        self.items.append(value)

    def get(self, timeout=None):
        del timeout
        if not self.items:
            raise queue.Empty
        return self.items.pop(0)

    def cancel_join_thread(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, target, args, *, run_target=True):
        self.target = target
        self.args = args
        self.run_target = run_target
        self.alive = True
        self.exitcode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.joins = []

    def start(self):
        if self.run_target:
            self.target(*self.args)
            self.alive = False
            self.exitcode = 0

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)

    def terminate(self):
        self.terminate_calls += 1
        self.alive = False

    def kill(self):
        self.kill_calls += 1
        self.alive = False


class _FakeContext:
    def __init__(self, *, run_target=True, start_error=None):
        self.queues = []
        self.processes = []
        self.run_target = run_target
        self.start_error = start_error

    def Queue(self):
        result = _FakeQueue()
        self.queues.append(result)
        return result

    def Process(self, *, target, args, name=None):
        del name
        process = _FakeProcess(target, args, run_target=self.run_target)
        if self.start_error is not None:
            process.start = lambda: (_ for _ in ()).throw(self.start_error)
        self.processes.append(process)
        return process


def _run_with_payload(monkeypatch, payload):
    context = _FakeContext()

    def worker(result_queue):
        result_queue.put(payload)

    monkeypatch.setattr(hardware, "_probe_worker", worker)
    monkeypatch.setattr(hardware.multiprocessing, "get_context", lambda _name: context)
    return hardware.detect_hardware(timeout_seconds=0.1), context


def test_cpu_only_detection_reports_local_capabilities(monkeypatch):
    result, context = _run_with_payload(
        monkeypatch,
        {"ok": True, "cuda_devices": 0, "compute_types": ["int8", "float32"]},
    )

    assert result.status == "ok"
    assert result.device_capabilities == ("cpu",)
    assert result.compute_types == ("int8", "float32")
    assert result.fallback == ("auto", "default")
    assert result.detail
    assert context.queues[0].closed and context.queues[0].cancelled


def test_cuda_detection_reports_cuda_capability(monkeypatch):
    result, _context = _run_with_payload(
        monkeypatch,
        {"ok": True, "cuda_devices": 2, "compute_types": ["int8", "float16"]},
    )

    assert result.status == "ok"
    assert result.device_capabilities == ("cpu", "cuda")
    assert result.compute_types == ("int8", "float16")


def test_runtime_failure_is_degraded_with_safe_fallback(monkeypatch):
    result, _context = _run_with_payload(
        monkeypatch,
        {"ok": False, "error": "ImportError: ctranslate2 is unavailable"},
    )

    assert result.status == "degraded"
    assert result.fallback == ("auto", "default")
    assert "ctranslate2" in result.detail


def test_malformed_child_response_is_degraded(monkeypatch):
    result, _context = _run_with_payload(monkeypatch, ["not", "a", "mapping"])

    assert result.status == "degraded"
    assert "response" in result.detail.lower()


def test_timeout_terminates_overdue_child_with_bounded_cleanup(monkeypatch):
    context = _FakeContext(run_target=False)
    monkeypatch.setattr(hardware.multiprocessing, "get_context", lambda _name: context)

    result = hardware.detect_hardware(timeout_seconds=0.01)

    process = context.processes[0]
    assert result.status == "degraded"
    assert result.fallback == ("auto", "default")
    assert "timed out" in result.detail.lower()
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert process.joins and all(timeout is not None for timeout in process.joins)
    assert all(item.closed and item.cancelled for item in context.queues)


def test_default_deadline_allows_a_measured_cold_start_response(monkeypatch):
    context = _FakeContext(run_target=False)
    clock = [0.0]

    class DelayedQueue(_FakeQueue):
        def get(self, timeout=None):
            del timeout
            if self.items and clock[0] == 0.0:
                clock[0] = 3.0
                raise queue.Empty
            return super().get(timeout=0)

    delayed = DelayedQueue()
    delayed.items.append({"ok": True, "cuda_devices": 0, "compute_types": ["int8"]})
    context.Queue = lambda: (context.queues.append(delayed), delayed)[1]
    monkeypatch.setattr(hardware.multiprocessing, "get_context", lambda _name: context)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: clock[0])

    result = hardware.detect_hardware()

    assert result.status == "ok"


def test_response_beyond_default_deadline_degrades_and_cleans_up(monkeypatch):
    context = _FakeContext(run_target=False)
    clock = [0.0]

    class NeverReadyQueue(_FakeQueue):
        def get(self, timeout=None):
            del timeout
            clock[0] = 6.0
            raise queue.Empty

    never_ready = NeverReadyQueue()
    context.Queue = lambda: (context.queues.append(never_ready), never_ready)[1]
    monkeypatch.setattr(hardware.multiprocessing, "get_context", lambda _name: context)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: clock[0])

    result = hardware.detect_hardware()

    assert result.status == "degraded"
    assert context.processes[0].terminate_calls == 1
    assert all(item.closed and item.cancelled for item in context.queues)


def test_child_start_failure_is_non_fatal_and_disposes_queue(monkeypatch):
    context = _FakeContext(start_error=OSError("process denied"))
    monkeypatch.setattr(hardware.multiprocessing, "get_context", lambda _name: context)

    result = hardware.detect_hardware(timeout_seconds=0.1)

    assert result.status == "degraded"
    assert "process denied" in result.detail
    assert all(item.closed and item.cancelled for item in context.queues)


def test_public_detector_does_not_import_model_runtimes():
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; from voice_studio.hardware import detect_hardware; "
                "print(json.dumps({name: name in sys.modules for name in "
                "('ctranslate2', 'faster_whisper')}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**dict(), "PYTHONPATH": "src"},
    )
    assert json.loads(process.stdout) == {
        "ctranslate2": False,
        "faster_whisper": False,
    }
