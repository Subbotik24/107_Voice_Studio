from __future__ import annotations

import multiprocessing
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dictionary import TerminologyDictionary
from .engines.base import TranscriptionHints
from .media import MAX_SOURCE_BYTES
from .models import Settings, Transcript
from .operation import JobCancelled, OperationBudget
from .process_lifecycle import _dispose_queue, _stop_process
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
        using_recognition_hints = False
        is_inference = request.get("action") != "cleanup"
        try:
            worker_settings = request["settings"]
            if isinstance(worker_settings, dict) and "dictionary_path" in worker_settings:
                raise ValueError("worker settings must not include dictionary_path")
            settings = Settings.from_dict(worker_settings)
            if request.get("action") == "cleanup":
                from .cloud_cleanup import propose_cleanup

                transcript = Transcript.from_dict(request["transcript"])
                if settings.profile != "ollama-local" or not settings.automatic_cleanup:
                    raise ValueError("automatic worker cleanup is restricted to local Ollama")
                proposal = propose_cleanup(
                    transcript,
                    provider="ollama",
                    model=settings.ollama_model,
                )
                results.put(
                    {
                        "job_id": job_id,
                        "ok": True,
                        "proposal": proposal.to_dict(),
                    }
                )
                continue
            hint_terms = request.get("hints", ())
            if not isinstance(hint_terms, (list, tuple)):
                raise ValueError("worker transcription hints must be a term list")
            hints = TranscriptionHints(tuple(hint_terms))
            using_recognition_hints = bool(hints.terms)
            engine = manager.get(settings)
            result = engine.transcribe(
                Path(request["source"]), request["language"], hints=hints
            )
            results.put({"job_id": job_id, "ok": True, "result": result})
        except BaseException as exc:
            if is_inference and using_recognition_hints:
                error = (
                    f"{type(exc).__name__}: transcription engine failed while using "
                    "recognition hints"
                )
            else:
                error = f"{type(exc).__name__}: {exc}"
            results.put(
                {
                    "job_id": job_id,
                    "ok": False,
                    "error": error,
                }
            )


@dataclass(frozen=True)
class _WorkerGeneration:
    process: Any
    requests: Any
    results: Any
    token: int


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
        self._lifecycle_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._generation: _WorkerGeneration | None = None
        self._generation_token = 0
        self._lifecycle_epoch = 0

    @property
    def _process(self) -> Any | None:
        with self._lifecycle_lock:
            generation = self._generation
            return generation.process if generation is not None else None

    @property
    def _requests(self) -> Any | None:
        with self._lifecycle_lock:
            generation = self._generation
            return generation.requests if generation is not None else None

    @property
    def _results(self) -> Any | None:
        with self._lifecycle_lock:
            generation = self._generation
            return generation.results if generation is not None else None

    @staticmethod
    def _dispose_generation(generation: _WorkerGeneration) -> None:
        _stop_process(generation.process)
        _dispose_queue(generation.requests)
        _dispose_queue(generation.results)

    def _detach_generation(
        self,
        expected: _WorkerGeneration | None = None,
    ) -> _WorkerGeneration | None:
        with self._lifecycle_lock:
            generation = self._generation
            if generation is None:
                return None
            if expected is not None and generation.token != expected.token:
                return None
            self._generation = None
            return generation

    def _epoch_cancelled(
        self,
        epoch: int,
        cancelled: Callable[[], bool] | None,
    ) -> bool:
        with self._lifecycle_lock:
            if self._lifecycle_epoch != epoch:
                return True
        return cancelled() if cancelled is not None else False

    def _ensure_worker(self, *, expected_epoch: int | None = None) -> _WorkerGeneration:
        stale: _WorkerGeneration | None = None
        failed: _WorkerGeneration | None = None
        generation: _WorkerGeneration | None = None
        startup_error: BaseException | None = None
        with self._lifecycle_lock:
            if (
                expected_epoch is not None
                and self._lifecycle_epoch != expected_epoch
            ):
                raise JobCancelled("transcription worker was closed")
            current = self._generation
            if current is not None:
                try:
                    alive = current.process.is_alive()
                except (AssertionError, AttributeError, OSError, ValueError):
                    alive = False
                if alive:
                    return current
                self._generation = None
                stale = current

            requests: Any | None = None
            results: Any | None = None
            try:
                requests = self._context.Queue()
                results = self._context.Queue()
                self._generation_token += 1
                generation = _WorkerGeneration(
                    process=self._context.Process(
                        target=self.worker_target,
                        args=(
                            requests,
                            results,
                            str(self.cache_directory),
                            str(self.store.models),
                        ),
                        name="voice-studio-transcription-worker",
                    ),
                    requests=requests,
                    results=results,
                    token=self._generation_token,
                )
                generation.process.start()
                self._generation = generation
            except BaseException as exc:
                if generation is not None:
                    failed = generation
                else:
                    if requests is not None:
                        _dispose_queue(requests)
                    if results is not None:
                        _dispose_queue(results)
                startup_error = exc

        if stale is not None:
            self._dispose_generation(stale)
        if failed is not None:
            self._dispose_generation(failed)
        if startup_error is not None:
            raise startup_error
        assert generation is not None
        return generation

    def _submit(self, generation: _WorkerGeneration, request: dict[str, Any]) -> None:
        with self._lifecycle_lock:
            if self._generation is not generation:
                raise JobCancelled("transcription worker was closed")
            try:
                generation.requests.put(request)
            except (AttributeError, OSError, ValueError) as exc:
                raise JobCancelled("transcription worker was closed") from exc

    def restart(self) -> None:
        self.close()
        self._ensure_worker()

    def _terminate_worker(self) -> None:
        generation = self._detach_generation()
        if generation is not None:
            self._dispose_generation(generation)

    def close(self) -> None:
        with self._lifecycle_lock:
            self._lifecycle_epoch += 1
            generation = self._generation
            self._generation = None
        if generation is None:
            return
        try:
            generation.requests.put(None)
        except (AttributeError, OSError, ValueError):
            pass
        _stop_process(generation.process, graceful_seconds=3)
        _dispose_queue(generation.requests)
        _dispose_queue(generation.results)

    def _wait_for_result(
        self,
        generation: _WorkerGeneration,
        job_id: str,
        budget: OperationBudget,
        phase: str,
    ) -> dict[str, Any]:
        while True:
            with self._lifecycle_lock:
                if self._generation is not generation:
                    raise JobCancelled("transcription worker was closed")
            try:
                wait_seconds = budget.remaining(phase, ceiling=0.1)
            except (JobCancelled, TimeoutError) as exc:
                with self._lifecycle_lock:
                    if self._generation is not generation:
                        raise JobCancelled("transcription worker was closed") from exc
                raise
            try:
                alive = generation.process.is_alive()
            except (AssertionError, AttributeError, OSError, ValueError):
                alive = False
            if not alive:
                detached = self._detach_generation(generation)
                if detached is None:
                    raise JobCancelled("transcription worker was closed")
                try:
                    exit_code = generation.process.exitcode
                except (AssertionError, AttributeError, OSError, ValueError):
                    exit_code = "unknown"
                self._dispose_generation(detached)
                raise RuntimeError(f"transcription worker stopped unexpectedly: {exit_code}")
            try:
                response = generation.results.get(timeout=wait_seconds)
            except queue.Empty:
                continue
            except (AttributeError, OSError, ValueError) as exc:
                raise JobCancelled("transcription worker was closed") from exc
            if response.get("job_id") == job_id:
                with self._lifecycle_lock:
                    if self._generation is not generation:
                        raise JobCancelled("transcription worker was closed")
                return response

    def _record_cleanup_outcome(
        self,
        transcript: Transcript,
        outcome: str,
        warning: str = "",
    ) -> Transcript:
        metadata = {**transcript.metadata, "automatic_cleanup": outcome}
        if warning:
            metadata["cleanup_warning"] = warning[:500]
        else:
            metadata.pop("cleanup_warning", None)
        transcript.metadata = metadata
        self.store.save(transcript)
        return transcript

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
        with self._run_lock:
            with self._lifecycle_lock:
                epoch = self._lifecycle_epoch
            return self._run_once(
                source,
                settings,
                dictionary,
                epoch=epoch,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                progress=progress,
            )

    def _run_once(
        self,
        source: Path,
        settings: Settings,
        dictionary: TerminologyDictionary,
        *,
        epoch: int,
        timeout_seconds: int | None = None,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> Transcript:
        settings.validate()
        timeout = settings.task_timeout_seconds if timeout_seconds is None else timeout_seconds
        budget = OperationBudget(
            timeout,
            lambda: self._epoch_cancelled(epoch, cancelled),
        )
        service = TranscriptionService(self.store, engine=None, dictionary=dictionary)
        hints = TranscriptionHints(tuple(dictionary.hint_terms()))
        worker_settings = settings.to_dict()
        worker_settings.pop("dictionary_path", None)
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
            budget.checkpoint("prepare")
            job_id = uuid.uuid4().hex
            budget.checkpoint("loading")
            generation = self._ensure_worker(expected_epoch=epoch)
            report("loading")
            budget.checkpoint("loading")
            self._submit(
                generation,
                {
                    "job_id": job_id,
                    "settings": worker_settings,
                    "source": str(prepared.managed),
                    "language": settings.language,
                    "hints": list(hints.terms),
                }
            )
            report("transcribing")
            response = self._wait_for_result(generation, job_id, budget, "inference")
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
            if settings.automatic_cleanup and transcript.engine == "ollama":
                report("cleaning")
                cleanup_job_id = uuid.uuid4().hex
                try:
                    self._submit(
                        generation,
                        {
                            "action": "cleanup",
                            "job_id": cleanup_job_id,
                            "settings": worker_settings,
                            "transcript": transcript.to_dict(),
                        }
                    )
                except (JobCancelled, TimeoutError) as exc:
                    # The worker was closed/cancelled between finalize() and the
                    # cleanup submission itself: the transcript is already
                    # committed to storage, so return it as-is rather than
                    # raising and losing the saved result. Record the outcome
                    # the same way the _wait_for_result branch below does, so
                    # the GUI can still tell the user cleanup did not run.
                    self._terminate_worker()
                    transcript = self._record_cleanup_outcome(
                        transcript,
                        "cancelled",
                        str(exc),
                    )
                else:
                    try:
                        cleanup_response = self._wait_for_result(
                            generation,
                            cleanup_job_id,
                            budget,
                            "cleaning",
                        )
                    except (JobCancelled, TimeoutError) as exc:
                        self._terminate_worker()
                        transcript = self._record_cleanup_outcome(
                            transcript,
                            "cancelled",
                            str(exc),
                        )
                    except Exception as exc:
                        transcript = self._record_cleanup_outcome(
                            transcript,
                            "failed",
                            str(exc),
                        )
                    else:
                        if cleanup_response["ok"]:
                            transcript = self.store.apply_ai_cleanup(
                                transcript.id,
                                cleanup_response["proposal"],
                                provider="ollama",
                                model=settings.ollama_model,
                            )
                            transcript = self._record_cleanup_outcome(transcript, "applied")
                        else:
                            transcript = self._record_cleanup_outcome(
                                transcript,
                                "failed",
                                str(cleanup_response["error"]),
                            )
            report("completed")
            return transcript
        except BaseException as exc:
            if isinstance(exc, (JobCancelled, TimeoutError)):
                self._terminate_worker()
            if prepared is not None:
                service.cleanup(prepared)
            raise
