# W2-C3 Task 3 — confirmed missing-source repair and orphan recheck

Date: 2026-08-30
Base: `0afe669` on local `main`

## CHANGED

- Added `LocalStore.repair_missing_source()` with the exact confirmed-repair
  signature and result contract.
- The repair takes `BEGIN IMMEDIATE`, re-reads the selected payload, checks an
  optional expected path, validates the real managed-sources root and all
  ancestors without following links, and accepts only a final-path
  `FileNotFoundError`.
- Existing, reappeared, outside, symlink, junction/reparse, malformed and
  otherwise uninspectable paths are refused with concrete `ValueError`
  messages. The operation updates only `source_path` and `audio_retained` in
  `payload_json`; it never calls `save()`, unlinks audio, or changes raw text.
- Hardened `cleanup_orphans()` with a write transaction, a fresh payload/reference
  recheck, direct-child containment, no-follow lstat and regular-file checks.
  Only successfully unlinked files are returned.
- Added repair and cleanup regressions for confirmation, unknown/no-source,
  expected-path mismatch, outside/reappeared/invalid paths, final symlinks,
  Windows source-root/ancestor/final junctions, idempotence, immutable
  payload fields, direct-child filtering and synchronized DB races.

## TDD EVIDENCE

- RED: focused repair/orphan tests failed on the untouched implementation
  because `repair_missing_source` did not exist.
- Review RED: injected NUL paths reached `lstat()` and raised the raw
  `embedded null character` `ValueError` instead of the recovery refusal.
- GREEN: after the implementation and invalid-path catch, focused repair,
  orphan, junction and race regressions passed.

## VERIFIED

- `pytest -q tests/test_storage_app.py tests/test_takeover_regressions.py tests/test_backup_app.py` — PASS, 88 passed, 6 skipped.
- `python -m compileall -q src tests` — PASS.
- `ruff check src tests` — PASS.
- `scripts/quality_gate.ps1` with repository `.venv` Python 3.12 — PASS, 518 passed, 8 skipped.
- `git diff --check` — PASS.

The skipped tests are Windows symlink-creation privilege boundaries; real
junction cases ran successfully. No cloud behavior, user originals, or
external files are mutated by this increment. Changes remain local on `main`
and were not pushed.

## REVIEW ROUND 2

- Corrupt transcript rows with NUL-containing source paths are now treated as
  uninspectable references; orphan cleanup skips all deletion for that
  transaction, preserving candidates without crashing. Path resolution catches
  `OSError`, `ValueError` and `UnicodeError`.
- The final Windows junction regression now supplies the lexical candidate path,
  exercising containment, no-follow lstat and reparse rejection directly.
- Storage/takeover/backup suite: 89 passed, 6 skipped; focused repair/orphan
  selector: 15 passed, 1 skipped.
