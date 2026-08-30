# Worker Process and Queue Shutdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make transcription and model-download process shutdown bounded, race-safe, idempotent, queue-disposing, and reusable after settings/restore refresh.

**Architecture:** Give `TranscriptionJobController` one lifecycle lock, one run lock, and immutable snapshots of each worker generation. Detach a generation atomically before stopping it, translate close races to `JobCancelled`, then use one bounded process/queue disposal primitive; apply the same disposal primitive to model-download workers. Keep `close()` reusable because settings save and restore intentionally call it before later work.

**Tech Stack:** Python 3.12 multiprocessing spawn, threading locks, existing `OperationBudget`, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.3) and `docs/superpowers/plans/2026-08-28-completion-roadmap.md` (W2-S1, process/queue half).

## Global Constraints

- No unbounded process join or queue feeder join on the UI/main thread.
- After disposal, every owned process is not alive and every owned multiprocessing queue receives `cancel_join_thread()` and `close()` exactly once where supported.
- `close()` is idempotent and may be followed by another `run()`; settings save and restore rely on that behavior.
- Closing during active work raises `JobCancelled` (not `AttributeError`, `ValueError`, `EOFError`, or a hung wait) in the running caller.
- Concurrent worker startup creates at most one live generation; concurrent transcription calls are serialized so one caller cannot consume another job's result.
- Normal result delivery, cancellation cleanup, immutable `raw_text`, source-original preservation, timeout behavior, and CLI/UI contracts remain unchanged.
- Model-download cancel/timeout always escalates terminate → bounded join → kill → bounded join, then disposes the result queue.
- No new runtime dependency, no network/telemetry change, no branch/worktree, and no push.

---

### Task 1: Shared bounded process and queue disposal

**Files:**
- Modify: `src/voice_studio/jobs.py` near module helpers
- Modify: `src/voice_studio/model_catalog.py` imports and download cleanup
- Test: `tests/test_jobs_app.py`
- Test: `tests/test_model_catalog_app.py`

**Interfaces:**
- Produces: `_stop_process(process: Any, *, graceful_seconds: float = 0.0) -> None` in a new focused module `src/voice_studio/process_lifecycle.py`.
- Produces: `_dispose_queue(queue_object: Any | None) -> None` in the same module.
- `_stop_process` optionally waits for an already-requested graceful exit, then calls `terminate()`/`join(timeout=5)`, and when still alive calls `kill()`/`join(timeout=2)`.
- `_dispose_queue` is idempotent for `None`, calls `cancel_join_thread()` then `close()`, and suppresses only already-closed lifecycle errors (`ValueError`, `OSError`, `AttributeError`). It never calls unbounded `join_thread()`.

- [ ] **Step 1: Add failing helper tests with deterministic fakes**

Create fake processes recording `join` timeouts and requiring kill after terminate. Assert exact bounded sequence and final non-alive state. Create fake queues recording `cancel_join_thread` and `close`; call disposal twice and assert the fake observes one effective disposal without an exception.

Add a model-download test with a fake spawn context whose process remains alive after terminate and whose result queue records disposal. Trigger cancellation and assert terminate, `join(5)`, kill, `join(2)`, queue cancellation/close, staging cleanup, and original filesystem state.

- [ ] **Step 2: Run focused tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py tests/test_model_catalog_app.py -k "dispose or kill_fallback or download_process_cleanup"
```

Expected: lifecycle helpers are missing; model install never kills the stubborn child and never disposes its queue.

- [ ] **Step 3: Implement the focused lifecycle module**

Create `src/voice_studio/process_lifecycle.py` with the two private helpers. `_stop_process` must inspect `is_alive()` after every bounded join; do not assume `terminate()` succeeded. `_dispose_queue` records disposal on the queue object when possible (for example a private sentinel attribute) so repeated controller cleanup does not call lifecycle methods twice, but it must still work with objects that reject attribute assignment.

Use the helpers in `ModelCatalog.install()`'s `finally`: stop the child even after normal-result exceptions, dispose `result_queue`, then remove only the owned unique staging directory. Preserve its cancel/timeout messages and return schema.

- [ ] **Step 4: Run focused and regression tests to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py tests/test_model_catalog_app.py
.\.venv\Scripts\python.exe -m ruff check src/voice_studio/process_lifecycle.py src/voice_studio/jobs.py src/voice_studio/model_catalog.py tests/test_jobs_app.py tests/test_model_catalog_app.py
.\.venv\Scripts\python.exe -m compileall -q src tests
```

- [ ] **Step 5: Commit lifecycle helpers and model-download cleanup**

```powershell
git add src/voice_studio/process_lifecycle.py src/voice_studio/model_catalog.py tests/test_jobs_app.py tests/test_model_catalog_app.py
git commit -m "fix(workers): bound process and queue disposal"
```

---

### Task 2: Race-safe reusable transcription controller generations

**Files:**
- Modify: `src/voice_studio/jobs.py:69-264`
- Test: `tests/test_jobs_app.py`

**Interfaces:**
- Consumes: `_stop_process` and `_dispose_queue` from Task 1.
- Produces: private immutable `_WorkerGeneration(process, requests, results, token)` dataclass.
- `TranscriptionJobController._ensure_worker() -> _WorkerGeneration` returns the current live generation or creates exactly one under `_lifecycle_lock`.
- `TranscriptionJobController.close() -> None` atomically detaches the generation, requests graceful exit, bounds shutdown, disposes queues, and remains reusable.

- [ ] **Step 1: Add failing lifecycle/concurrency tests**

Add tests for:

1. two threads synchronized at a barrier calling `_ensure_worker()` receive the same generation/process and only one process starts;
2. repeated `close()` disposes a real/fake generation once and never raises;
3. `close()` during a slow `run()` makes that caller finish within five seconds with `JobCancelled`, never an implementation exception;
4. `run()` after `close()` creates a new generation and succeeds;
5. two concurrent `run()` calls are serialized and both receive their own response (no response is discarded);
6. a stopped worker path disposes both queues before reporting the existing concrete error;
7. a subprocess that starts, completes and closes the controller exits without `resource_tracker`/semaphore-leak text on stderr.

- [ ] **Step 2: Run the new tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py -k "concurrent_worker or close_during or run_after_close or concurrent_run or disposes or resource_tracker"
```

Expected: double startup is possible, close races surface mutable-attribute errors, queues are not disposed, and concurrent runs can consume each other's results.

- [ ] **Step 3: Implement generation ownership**

Add `threading.RLock()` as `_lifecycle_lock`, `threading.Lock()` as `_run_lock`, and a monotonic integer token. Create/detach generations only under `_lifecycle_lock`; perform bounded process joins outside the lock so result/cancel code can observe detachment.

`_ensure_worker()` returns a generation snapshot. Submission accepts that snapshot, verifies under the lifecycle lock that its token is still current, and catches closed-queue lifecycle errors as `JobCancelled("transcription worker was closed")`. `_wait_for_result` receives the same snapshot instead of reading mutable controller attributes; if the generation was detached or queue access closes, raise the same `JobCancelled`. If the still-current process exits unexpectedly, atomically detach it, dispose it, and retain the existing concrete worker-exit error.

Wrap the full public `run()` body in `_run_lock` without changing its signature. Cancellation/timeout still force-detaches the generation and runs prepared-source cleanup. `restart()` calls `close()` then `_ensure_worker()` and remains bounded.

`close()` detaches once, best-effort `requests.put(None)` on its local snapshot, waits at most three seconds for graceful exit via `_stop_process(..., graceful_seconds=3)`, then disposes both queues. A concurrent second close sees no generation and returns.

- [ ] **Step 4: Run jobs and integration suites to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py tests/test_cli_app.py tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts packaging
.\.venv\Scripts\python.exe -m compileall -q src tests scripts packaging
```

- [ ] **Step 5: Commit controller coordination**

```powershell
git add src/voice_studio/jobs.py tests/test_jobs_app.py
git commit -m "fix(jobs): coordinate worker shutdown and queues"
```

---

### Task 3: W2-S1 process-half documentation and verification

**Files:**
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `VERIFICATION.md`
- Modify: `NEXT_ANTIGRAVITY_TASK.md`

**Interfaces:**
- Consumes: verified Task 1-2 behavior.
- Produces: an honest `W2-S1 process/queue half PASS`; full W2-S1 remains in progress until app/recorder/hotkey thread lifecycle is complete.

- [ ] **Step 1: Record bounded process/queue evidence**

Document the exact process/queue ownership, bounded terminate/kill timeouts, reusable close behavior, concurrency tests, and platform results. Keep the app/recorder/hotkey half explicitly open; do not mark full coordinated shutdown or broader R0 complete.

- [ ] **Step 2: Run the complete source gate**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-s1-process-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

- [ ] **Step 3: Commit evidence**

```powershell
git add IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md
git commit -m "docs: record worker process shutdown verification"
```

- [ ] **Step 4: Controller verification**

```powershell
git status --short --branch
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
git diff dba3407..HEAD --check
```

Confirm no model weights, private paths, secrets, or unbounded `join()` calls entered the scoped files and local `main` remains unpushed.

