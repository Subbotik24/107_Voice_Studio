# W2-C1 Task 3 report — Help, status, and verification evidence

Date: 2026-08-30

## Changes

- Aligned the Ukrainian, Czech and English reference Help pages on restore's
  local-state boundary: archived transcripts/settings/sources are replaced;
  current `models/` and `exports/` remain unchanged and outside `.voice-backup`.
- Documented the concrete unsafe-path error and the guarantee that links,
  reparse points and special entries abort before the live root changes.
- Added a dated W2-C1 status row and verification record without promoting
  native or physical acceptance to PASS.
- Recorded W2-C1 as complete in `NEXT_ANTIGRAVITY_TASK.md` while retaining the
  prior W2-C2 acceptance criteria and evidence.

## Verification

Environment: Windows x64, repository `.venv` CPython 3.12, base `dba3407`.

```text
focused restore tests: 10 passed, 2 skipped
quality_gate.ps1: PASS; compile, Ruff, Help validation (13 Markdown files),
  435 passed, 3 skipped, CLI version 0.3.0rc1
wheel build: PASS; `build\\w2-c1-wheel\\voice_studio-0.3.0rc1-py3-none-any.whl`, 687,353 bytes
pip check: PASS; No broken requirements found.
git diff --check: PASS
```

The two focused-test skips are Windows symlink-creation privilege boundaries
(`WinError 1314`); junction/reparse regressions ran. The source tests simulate
copy interruption and verify that the live root remains untouched. No physical
power-loss or forced-termination run, removable-media/antivirus race, signed or
clean-machine acceptance, or native macOS/Windows device acceptance was run.

The mandated bare global-Python startup check also could not collect tests
because that Python 3.13 interpreter lacks `platformdirs`; the repository
`.venv` results above are the supported evidence.

## Commit

Completion commit: `docs: record restore local-state verification`.
