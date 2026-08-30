# W2-S1 Task 1 — bounded recorder writer shutdown

Date: 2026-08-30
Base: `0d43e77` on local `main`

## CHANGED

- Added `WRITER_STOP_TIMEOUT_SECONDS = 2.0`.
- Bound `_finish_writer()` with one monotonic deadline shared by sentinel
  enqueue and the finite writer join.
- Removed the artificial `_writer_done.set()` on timeout. A live or otherwise
  unconfirmed writer now raises the concrete timeout while retaining its
  thread handle, destination ownership and partial-file residue for retry.
- A later cancel after the writer has stopped joins and clears the handle, then
  performs the existing identity-safe owned-partial cleanup. Foreign
  replacements remain on disk and return the existing `RecorderCleanupError`.
- Added focused regressions for bounded timeout, retry cleanup and foreign
  replacement preservation.

## RED/GREEN EVIDENCE

The focused tests initially failed because the timeout constant and bounded
writer contract were absent. After implementation, the focused selector passed
(`3 passed, 37 deselected`).

## VERIFIED

- `.venv\Scripts\python.exe -m pytest -q tests/test_recorder_app.py tests/test_recording_lifecycle_app.py -k "writer_timeout or writer_retry" --timeout=5`: `3 passed`
- `.venv\Scripts\python.exe -m pytest -q tests/test_recorder_app.py tests/test_recording_lifecycle_app.py --timeout=10`: `40 passed`
- `.venv\Scripts\python.exe -m pytest -q`: `455 passed, 3 skipped` (Windows symlink privilege boundaries)
- Ruff over recorder and recorder lifecycle tests: PASS
- `python -m compileall -q src tests`: PASS
- `git diff --check`: PASS

## KNOWN ISSUES

- The bare global Python 3.13 pytest command from the repository start
  checklist cannot collect this project because that interpreter lacks
  `platformdirs`; supported repository `.venv` Python 3.12 verification was
  used for the passing gates.
- App-wide shutdown, hotkey and maintenance-thread coordination remains in
  the subsequent W2-S1 tasks.
