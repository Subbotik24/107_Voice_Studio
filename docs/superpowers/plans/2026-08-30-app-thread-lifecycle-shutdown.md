# App Thread Lifecycle Shutdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GUI shutdown idempotent and bounded across recorder, hotkey, transcription, cleanup, model-download, discovery, and maintenance threads without late Tk events or silent handle loss.

**Architecture:** Bound recorder/hotkey teardown at their ownership boundaries, retaining explicit handles and residues when third-party code does not stop. Route every GUI worker through one registry and every worker event through a shutdown-aware gate; `_close()` cancels producers, closes the process controller, joins daemon workers against one deadline, records remaining names, and destroys Tk exactly once. Maintenance remains non-daemon and blocks close while an atomic backup/restore is active.

**Tech Stack:** Python 3.12 threading/queue/Tkinter, existing recorder/hotkey/controller abstractions, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.3) and `docs/superpowers/plans/2026-08-28-completion-roadmap.md` (W2-S1, app/thread half).

## Global Constraints

- No unbounded `Thread.join()` remains in production recorder, hotkey, or Tk shutdown paths.
- Python threads are cooperative: if third-party audio/network code ignores stop, retain and report the live handle; do not claim force-kill. GUI worker threads are daemon threads so they cannot keep the process alive after Tk exits.
- The non-daemon backup/verify/restore worker continues to block window close until it finishes; never interrupt the journaled storage swap.
- Shutdown prevents new background work and drops late worker events before they touch Tk state.
- `_close()` is idempotent, destroys Tk once, and does not raise when invoked during active transcription, model download, cleanup, discovery, recording, or hotkey teardown.
- Original files and `raw_text` invariants remain unchanged; recorder residue cleanup stays identity-safe and never deletes an ambiguous/foreign path.
- Existing process/queue lifecycle from `docs/superpowers/plans/2026-08-30-worker-process-queue-shutdown.md` remains intact.
- No runtime dependency, cloud behavior, branch/worktree, or push.

---

### Task 1: Bounded recorder writer shutdown with retained residue

**Files:**
- Modify: `src/voice_studio/recorder.py:354-366`
- Test: `tests/test_recorder_app.py`
- Test: `tests/test_recording_lifecycle_app.py`

**Interfaces:**
- Produces: `WRITER_STOP_TIMEOUT_SECONDS = 2.0`.
- `AudioRecorder._finish_writer() -> None` either joins and clears the writer or raises `TimeoutError("audio recorder writer did not stop within 2.0 seconds")` while retaining `_writer_thread`, destination ownership, and residue paths for retry/reporting.

- [ ] **Step 1: Add failing bounded-writer tests**

Use a fake writer whose `is_alive()` stays true and whose `join(timeout=...)` records a finite timeout. Fill the frame queue so sentinel insertion cannot succeed. Monkeypatch `WRITER_STOP_TIMEOUT_SECONDS` to `0.05`; assert `cancel()` returns/raises within 0.5 seconds, every join receives a finite timeout, `_writer_thread` is retained, and the owned partial file is retained rather than deleted while a writer may still own it.

Add a retry test: after the first timeout, switch the fake writer to stopped/set `_writer_done`, call `cancel()` again, and assert the thread reference clears and the identity-owned partial is removed; a foreign replacement remains preserved with the existing concrete cleanup error.

- [ ] **Step 2: Run focused tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_recorder_app.py tests/test_recording_lifecycle_app.py -k "writer_timeout or writer_retry"
```

Expected: current sentinel loop or `thread.join()` blocks/unbounds, and the retained-handle contract is absent.

- [ ] **Step 3: Implement one monotonic writer deadline**

Compute `deadline = time.monotonic() + WRITER_STOP_TIMEOUT_SECONDS`. Sentinel insertion uses `timeout=min(0.05, remaining)` and stops when remaining is zero. Join once with the remaining positive duration. Never set `_writer_done` artificially.

If the writer remains alive or `_writer_done` is unset, raise the exact timeout without clearing `_writer_thread` or session ownership. Only a confirmed stopped writer is set to `None`. In `stop()`/`cancel()`/start-failure cleanup, do not call `_remove_owned_partial()` or `_clear_session()` after this timeout; the next retry owns cleanup. Preserve current cleanup-error chaining for all non-timeout paths.

- [ ] **Step 4: Run recorder suites to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_recorder_app.py tests/test_recording_lifecycle_app.py
.\.venv\Scripts\python.exe -m ruff check src/voice_studio/recorder.py tests/test_recorder_app.py tests/test_recording_lifecycle_app.py
.\.venv\Scripts\python.exe -m compileall -q src tests
```

- [ ] **Step 5: Commit recorder lifecycle**

```powershell
git add src/voice_studio/recorder.py tests/test_recorder_app.py tests/test_recording_lifecycle_app.py
git commit -m "fix(recorder): bound writer shutdown"
```

---

### Task 2: Hotkey stop outcome and listener retention

**Files:**
- Modify: `src/voice_studio/hotkey.py:108-117`
- Test: `tests/test_hotkey_app.py`

**Interfaces:**
- Produces: `GlobalHotkey.stop() -> bool`; `True` means listener stopped, `False` means its handle remains live for retry/reporting.
- Callback state (`_hotkey`, `_active`) is cleared before native stop so queued macOS events stay harmless.

- [ ] **Step 1: Add failing stop-outcome tests**

With fake listeners, assert a normal stop calls `stop()`, performs only `join(timeout=1)`, clears `_listener`, and returns `True`. For a listener still alive after join, assert `False`, `_listener` retains the same object, callback state is cleared, and a second `stop()` retries. Assert no parameterless join occurs.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_hotkey_app.py -k stop
```

Expected: current method returns `None` and discards the live handle.

- [ ] **Step 3: Implement bounded retained stop**

Clear `_hotkey`/`_active`, call native `stop()`, join for one second only when alive, then re-check. Set `_listener = None` and return `True` only when stopped. Otherwise restore `_listener = listener` and return `False`; late callbacks remain no-ops because `_hotkey` is cleared.

- [ ] **Step 4: Run hotkey tests and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_hotkey_app.py tests/test_gui_contract_app.py
.\.venv\Scripts\python.exe -m ruff check src/voice_studio/hotkey.py tests/test_hotkey_app.py
git add src/voice_studio/hotkey.py tests/test_hotkey_app.py
git commit -m "fix(hotkey): retain listeners that outlive stop"
```

---

### Task 3: Shutdown-aware GUI worker registry

**Files:**
- Modify: `src/voice_studio/app.py:176-191,300-324,1450-1550,1715-1772,2070-2098,2880-2910,2965-2985,3072-3096`
- Modify: `src/voice_studio/i18n.py`
- Test: `tests/test_gui_contract_app.py`
- Test: `tests/test_recording_lifecycle_app.py`
- Test: `tests/test_i18n_app.py`

**Interfaces:**
- Produces: `VoiceStudioApp._start_worker(role: str, target: Callable[[], None], *, daemon: bool = True) -> threading.Thread | None`.
- Produces: `VoiceStudioApp._post_event(event: str, value: Any) -> bool`.
- Produces: `VoiceStudioApp._join_workers(timeout_seconds: float = 3.0) -> tuple[str, ...]`.
- State: `_shutdown_event`, `_worker_lock`, `_worker_threads`, `_closing`, `_shutdown_residue_threads`.

- [ ] **Step 1: Add failing registry/event tests**

Build lightweight `SimpleNamespace` app stubs and assert:

1. `_start_worker` registers a named thread, rejects starts after shutdown, and removes only its own current handle on completion;
2. `_post_event` enqueues before shutdown and returns `False`/does not enqueue after shutdown;
3. `_join_workers` uses one shared monotonic deadline, every join has a finite timeout, and returns names that remain alive;
4. `_poll_events` does not reschedule itself once shutdown starts;
5. source contracts route discovery, transcription, AI cleanup, model download, and maintenance through `_start_worker`, and worker event publication through `_post_event`.

- [ ] **Step 2: Add failing `_close()` scenario tests**

Test active transcription/model download with fake tracked daemon threads and a fake reusable job controller. Assert `_close()` sets cancellation/shutdown before controller close, invokes bounded joins, drops late events, records residue names, destroys once, and a second call is a no-op.

Test recorder timeout and hotkey `False`: `_close()` reports/records concrete residue details but still reaches destroy. Test an active non-daemon maintenance worker: close is refused exactly as today, `_closing`/shutdown are not set, and destroy is not called.

- [ ] **Step 3: Run GUI RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py tests/test_i18n_app.py -k "worker_registry or shutdown or late_event or close"
```

Expected: direct thread starts/events bypass ownership; close is not idempotent and joins no daemon workers.

- [ ] **Step 4: Implement registry and shutdown order**

Initialize the five state fields before any startup worker. `_start_worker` checks `_shutdown_event`, wraps the target in `finally` removal guarded by `_worker_lock`, rejects a duplicate live role with a concrete `RuntimeError`, and always names threads `voice-studio-{role}`. Maintenance uses `daemon=False`; all other roles use `daemon=True`.

Replace direct `events.put` calls inside the five worker families with `_post_event`. `_poll_events` returns without another `after()` when shutdown is set. Do not route synchronous Tk callbacks through the gate.

`_join_workers` snapshots handles, excludes `threading.current_thread()`, and spends one three-second monotonic budget across finite `join(timeout=remaining)` calls. It returns sorted live role names and never discards their handles.

`_close()` order after maintenance/editor gates:

```python
if self._closing:
    return
self._closing = True
self._shutdown_event.set()
self._cancel_event.set()
hotkey_stopped = self.hotkey.stop() if self.hotkey else True
try:
    self.recorder.cancel()
except Exception as exc:
    self._report_recorder_error(exc)
self.job_controller.close()
alive = self._join_workers()
residues = set(alive)
if not hotkey_stopped:
    residues.add("global-hotkey")
self._shutdown_residue_threads = tuple(sorted(residues))
# existing owned-temp cleanup/reporting
self.destroy()
```

Ensure `destroy()` executes through a `finally` after shutdown begins, while pre-shutdown confirmation/maintenance gates still return normally. Add complete uk/cs/en `shutdown_residue` text with `{workers}` and store/set the concrete status before destroy when residues exist.

- [ ] **Step 5: Run GUI/thread integration and full gate**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py tests/test_recorder_app.py tests/test_hotkey_app.py tests/test_jobs_app.py tests/test_i18n_app.py
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
```

- [ ] **Step 6: Commit GUI lifecycle**

```powershell
git add src/voice_studio/app.py src/voice_studio/i18n.py tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py tests/test_i18n_app.py
git commit -m "fix(ui): coordinate background thread shutdown"
```

---

### Task 4: Complete W2-S1 evidence and clean-profile shutdown smoke

**Files:**
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `VERIFICATION.md`
- Modify: `NEXT_ANTIGRAVITY_TASK.md`

**Interfaces:**
- Consumes: process/queue half plus Tasks 1-3.
- Produces: W2-S1 source/headless PASS with explicit physical-device and uncooperative-third-party thread limitations; it does not mark broader R0 complete.

- [ ] **Step 1: Record exact shutdown behavior and limits**

Document process/queue disposal, recorder/hotkey retained residues, one-budget GUI joins, late-event gating, maintenance refusal, and tests. State that Python cannot force-kill a blocked third-party thread; daemon residue is named and the OS ends it with the process. Keep physical microphone, macOS event-tap, antivirus/removable-media, packaged and clean-machine acceptance open.

- [ ] **Step 2: Run the complete gate and artifacts**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-s1-thread-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

- [ ] **Step 3: Run clean-profile GUI close smoke**

Under disposable `build/w2-s1-thread-smoke` config/data/cache directories, launch the source GUI with repository Python, verify startup, request normal window close through a controlled Tk callback, and assert the exact process exits within 15 seconds. Record source-only evidence and terminate only that verified PID if the controlled close fails.

- [ ] **Step 4: Commit docs/evidence**

```powershell
git add IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md
git commit -m "docs: record coordinated shutdown verification"
```

- [ ] **Step 5: Controller verification**

```powershell
git status --short --branch
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
git diff c2bfcc9..HEAD --check
```

Inspect the scoped changes for unbounded production joins, direct background `events.put`, untracked direct `Thread(...).start()`, private paths, secrets, model weights, and any overstatement of native acceptance. Keep local `main` unpushed.
