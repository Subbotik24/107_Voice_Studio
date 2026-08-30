# W2-S1 Task 2 — reusable transcription worker generations

Date: 2026-08-30
Base: `20ab677` on local `main`

## CHANGED

- Added frozen `_WorkerGeneration` snapshots with monotonic tokens.
- Added lifecycle `RLock` and public-run serialization `Lock`.
- Made `_ensure_worker()` single-start and reusable after `close()`/restart.
- Atomically detach generations before bounded process disposal; close races now
  report `JobCancelled("transcription worker was closed")`.
- Routed request submission and result waits through the generation snapshot so
  a later worker cannot consume an earlier job's response.
- Reused Task 1 `_stop_process` and `_dispose_queue` for unexpected exits,
  cancellation, timeout, restart and close.
- Added regressions for startup races, idempotent close, close during slow work,
  run-after-close, concurrent runs, stopped-worker queue cleanup and a
  subprocess resource-tracker smoke check.

## RED/GREEN EVIDENCE

The new startup/close tests failed on the untouched controller: concurrent
startup created two processes and repeated close left both queues undisposed.
After the implementation, the focused lifecycle selector passed (`7 passed,
12 deselected`), and the complete jobs plus CLI/GUI/recording integration set
passed (`84 passed`).

## VERIFIED

- `.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py`: `21 passed`
- `.venv\Scripts\python.exe -m pytest -q tests/test_jobs_app.py tests/test_cli_app.py tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py`: `86 passed`
- Ruff over `src tests scripts packaging`: PASS
- `compileall` over `src tests scripts packaging`: PASS
- `scripts\quality_gate.ps1` with repository `.venv` Python 3.12: `452 passed,
  3 skipped` (Windows symlink privilege boundaries)
- `git diff --check`: PASS

## REVIEW ROUND 1

- Added a lifecycle epoch captured after each run acquires the run lock.
- `close()` now advances the epoch even when no worker generation exists;
  prepare, loading and submission checkpoints treat a mismatch as cancellation.
- Added a deterministic blocked-prepare regression proving close prevents
  process creation, removes the prepared managed copy, and preserves the
  original source.
- The subprocess smoke now performs a successful fixture worker request/result
  before close, exercising both queue feeder paths.
- Review-round focused tests: `7 passed`; full quality gate rerun: `452 passed,
  3 skipped`.

## REVIEW ROUND 2

- `_ensure_worker(expected_epoch=...)` validates the captured run epoch under
  `_lifecycle_lock` before creating queues or a process; direct/restart callers
  retain the explicit no-epoch path.
- Added a deterministic checkpoint-to-ensure regression proving close prevents
  all queue/process allocation after the final pre-start checkpoint.
- Added an immediate-post-generation close regression proving detachment,
  bounded stop, and exactly-once queue disposal remain safe.
- Focused lifecycle selector: `7 passed, 14 deselected`; integration suite:
  `86 passed`; full quality gate: `452 passed, 3 skipped`.

## REVIEW ROUND 3

- Strengthened the checkpoint-to-ensure regression to assert that zero queues,
  as well as zero processes, are allocated after close.
- Focused job selector plus Ruff, compileall and diff checks: PASS.

## KNOWN ISSUES

- The bare global Python 3.13 command required by the repository start
  checklist cannot collect this project because it lacks `platformdirs` and
  the repository's supported script modules; supported `.venv` Python 3.12
  verification above was used.
- W2-S1 app/recorder/hotkey thread lifecycle and native acceptance remain open;
  this report covers only the transcription process/queue half.
