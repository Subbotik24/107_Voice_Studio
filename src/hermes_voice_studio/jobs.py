from __future__ import annotations

import multiprocessing
import queue
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .dictionary import TerminologyDictionary
from .media import MAX_SOURCE_BYTES
from .models import Settings, Transcript
from .operation import JobCancelled, OperationBudget
from .service import TranscriptionService
from .storage import LocalStore


def _engine_worker(
    requests: Any,
    results: Any,
    cache_directory: str,
    model_directory: str,
) -> None:
    # Keep optional model runtimes out of the GUI parent process. The spawn
    # worker owns their lifecycle and is terminated for cancellation/recovery.
    from .engines import EngineManager

    manager = EngineManager(Path(cache_directory), Path(model_directory))
    while True:
        request = requests.get()
        if request is None:
            return
        job_id = request["job_id"]
        try:
            settings = Settings.from_dict(request["settings"])
            engine = manager.get(settings)
            result = engine.transcribe(Path(request["source"]), request["language"])
            results.put({"job_id": job_id, "ok": True, "result": result})
        except BaseException as exc:
            results.put(
                {
                    "job_id": job_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )


class TranscriptionJobController:
    def __init__(
        self,
        store: LocalStore,
        cache_directory: Path,
        *,
        worker_target: Callable[..., None] = _engine_worker,
    ):
        self.store = store
        self.cache_directory = cache_directory
        self.worker_target = worker_target
        self._context = multiprocessing.get_context("spawn")
        self._requests: Any | None = None
        self._results: Any | None = None
        self._process: Any | None = None

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._requests = self._context.Queue()
        self._results = self._context.Queue()
        self._process = self._context.Process(
            target=self.worker_target,
            args=(
                self._requests,
                self._results,
                str(self.cache_directory),
                str(self.store.models),
            ),
            name="hermes-transcription-worker",
        )
        self._process.start()

    def restart(self) -> None:
        self._terminate_worker()
        self._ensure_worker()

    def _terminate_worker(self) -> None:
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        self._process = None
        self._requests = None
        self._results = None

    def close(self) -> None:
        if self._process is not None and self._process.is_alive() and self._requests is not None:
            self._requests.put(None)
            self._process.join(timeout=3)
        self._terminate_worker()

    def run(
        self,
        source: Path,
        settings: Settings,
        dictionary: TerminologyDictionary,
        *,
        timeout_seconds: int | None = None,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> Transcript:
        timeout = settings.task_timeout_seconds if timeout_seconds is None else timeout_seconds
        budget = OperationBudget(timeout, cancelled)
        service = TranscriptionService(self.store, engine=None, dictionary=dictionary)
        started = time.monotonic()

        def report(phase: str) -> None:
            if progress:
                progress(phase, time.monotonic() - started)

        prepared = None
        try:
            report("importing")
            # The byte ceiling has to be supplied here: import_source enforces it
            # while streaming, so an oversized source is refused during the copy
            # rather than after the disk has already taken it.
            prepared = service.prepare(
                source,
                settings.retention,
                budget,
                max_bytes=MAX_SOURCE_BYTES,
            )
            job_id = uuid.uuid4().hex
            budget.checkpoint("loading")
            self._ensure_worker()
            report("loading")
            budget.checkpoint("loading")
            self._requests.put(
                {
                    "job_id": job_id,
                    "settings": settings.to_dict(),
                    "source": str(prepared.managed),
                    "language": settings.language,
                }
            )
            report("transcribing")
            while True:
                wait_seconds = budget.remaining("inference", ceiling=0.1)
                if self._process is None or not self._process.is_alive():
                    exit_code = self._process.exitcode if self._process is not None else "unknown"
                    self._terminate_worker()
                    raise RuntimeError(f"transcription worker stopped unexpectedly: {exit_code}")
                try:
                    response = self._results.get(timeout=wait_seconds)
                except queue.Empty:
                    continue
                if response.get("job_id") != job_id:
                    continue
                if not response["ok"]:
                    raise RuntimeError(response["error"])
                report("saving")
                transcript = service.finalize(
                    prepared,
                    response["result"],
                    settings.language,
                    settings.retention,
                    budget=budget,
                )
                report("completed")
                return transcript
        except BaseException as exc:
            if isinstance(exc, (JobCancelled, TimeoutError)):
                self._terminate_worker()
            if prepared is not None:
                service.cleanup(prepared)
            raise
