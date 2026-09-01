# VOICE Studio usability pack — execution log

This is the durable controller log for the user-approved usability package.
Read it before running a gate so an unchanged production HEAD is not verified
twice. Focused worker checks do not replace the controller's full gate.

## Baseline — 2026-09-01

- Base: `8239204b160918c5206ea1d4abff489105a4aed6`, synchronized `main`/`origin/main`.
- `python -m compileall -q src tests`: PASS.
- `pytest -q`: PASS, 880 passed / 9 skipped; all skips are Windows symlink-privilege boundaries (`WinError 1314`).
- `ruff check src tests scripts packaging`: PASS.
- wheel build: PASS, ignored artifact under `build/usability-baseline-wheel/`.
- `pip check`: PASS, no broken requirements.
- Packaged/native usability acceptance: NOT RUN.

## Increment records

Each increment appends base/commit, RED evidence, focused GREEN, controller
full gate, diff/secret checks, commit/push, and any explicit `NOT_RUN` items.

### Increment 1 — navigation and central pages

- Base: `8239204`; production commit: `f710aef` (`feat: add central workspace navigation`).
- RED: 4 failed / 21 passed — missing page dispatcher, missing History → Studio navigation, and missing localized Dashboard key.
- Initial focused GREEN: 135 passed.
- Review fix round 1: populated-transcript Discard regression RED 1 failed / 3 passed; GREEN 94 passed.
- Review fix round 2: no-current draft Discard regression RED 1 failed / 4 passed; GREEN 95 passed.
- Sol scoped re-review: Discard and Cancel findings ADDRESSED; no new Critical/Important findings.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 886 / 9 Windows symlink-privilege skips; CLI version PASS `0.3.0rc1`.
- `pip check`, `git diff --check`, changed-file whitespace and secret/private-path scan: PASS.
- Physical GUI and packaged/native acceptance: NOT RUN.

### Increment 2 — managed terminology dictionary

- Base: `e3f1704`; production changes were reviewed in one working-tree increment before controller commit.
- Task 2A RED/GREEN: missing repository/rule APIs initially failed collection; final focused dictionary proof: 27 passed, affected subset 49 passed / 73 deselected.
- Task 2A Sol review: four correction rounds closed bounded-read, CSV large-field/concurrency, atomicity, merge-accounting, shared-parser, and boundary-proof findings; final verdict CLEAN.
- Task 2B RED/GREEN: initial controller surface 9 failed; lifecycle review fixes recorded 7 failed / 12 passed plus 1 live-i18n failure; export dispatch fix recorded 2 failed; final focused set 80 passed and export subset 25 passed.
- Task 2B Sol review: Settings reconciliation, dirty close, live localization, load-error boundary, and explicit JSON/CSV export selection corrected; final verdict CLEAN.
- First controller full pytest exposed three legacy headless-close stub regressions: 936 passed / 3 failed / 9 skipped. A narrow regression fix then passed 7 relevant tests / 22 deselected.
- Final controller gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 940 / 9 Windows symlink-privilege skips.
- `pip check`, `git diff --check`, and changed-diff secret/private-path scan: PASS.
- Live Tk visual, physical GUI, and packaged/native acceptance: NOT RUN.

### Increment 3 — Local Whisper recognition hints

- Base: `00fb4b4`; implementation kept hints per-request and did not change transcript, Settings, backup, or model-cache schemas.
- RED: missing `TranscriptionHints`/worker serialization produced 2 failures; privacy review regressions produced 2 failures for dictionary-path boundary and echoed hint values; provider omission hardening was test-only.
- Focused GREEN: 14 hint-contract tests; related engine/service/job/cloud selection 106 passed.
- Sol review: worker error redaction, worker rejection of `dictionary_path`, sanitized cleanup settings, exact provider omission, constructor reuse, invalid payload bounds, Architecture signature, and benchmark fake were corrected; final verdict CLEAN.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 954 / 9 Windows symlink-privilege skips.
- `pip check`, `git diff --check`, and changed-diff secret/private-path scan: PASS.
- Live engine/model smoke, physical GUI, and packaged/native acceptance: NOT RUN.

### Increment 4 — Dashboard statistics and combined History filters

- Base: `1782fa7` plus handoff docs `79a3f4d`; slices 4A and 4B verified and committed together under one controller gate.
- Environment: Linux cloud container, CPython 3.12.3 virtualenv with full project dependencies; the platform boundary skips appear as 14 Linux junction skips instead of the 9 Windows `WinError 1314` skips recorded on the previous machine.
- 4A storage: new `voice_studio/dashboard.py` with immutable `DashboardStatistics` and `HistoryFilter` (pure `matches()`); streaming `LocalStore.statistics()` reads every row without the 250-row UI limit and without migration; `LocalStore.list(..., filters=...)` combines text/date/language/engine/model/status/retained-audio filters before `limit` while the legacy path stays byte-for-byte; invalid payload rows count only in total+invalid and never raise. Focused proof: 23 new tests; dashboard+storage subset 92 passed / 5 skipped, covering empty store, 261 rows beyond the UI limit, legacy 0.1 payloads, invalid rows, exact UTC 7/30-day boundaries, Unicode apostrophe/hyphen word tokens, failed records, weighted RTF (0.875 case) with bool/NaN/zero exclusions, filter combinations, and post-filter limit ordering.
- 4B interface: Dashboard placeholder replaced with a pure Tk/ttk KPI grid (totals, completed, failed, words, H:MM:SS audio duration, ×speed, retained audio, 7/30-day activity), top-3 language/engine/model rankings, an invalid-records row visible only above zero that points at `voice-studio storage audit`, and five clickable recent records honoring the dirty-editor guard; History page gained controls for every `HistoryFilter` field with translated display→raw value mapping and safe rejection of malformed dates; Dashboard refreshes on page open and after save/rename/delete/restore/import with no background polling; uk/cs/en catalogs extended (30 keys added, 2 placeholder keys removed, key parity enforced). 21 new headless lifecycle tests; a Linux Xvfb source-level smoke of the real GUI passed (this is not the packaged or physical acceptance).
- Controller full gate on the combined tree: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 993 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS.
- `git diff --check` and changed-diff secret/private-path scan: PASS.
- Help/docs synchronization for the new Dashboard and History behavior: deferred to Task 8 of the approved master plan.
- Physical Tk smoke and Windows/macOS packaged/native acceptance: NOT RUN.
