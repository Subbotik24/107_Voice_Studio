# Task 4 report — GUI startup reporting and three-language messages

## Result

Implemented startup model-catalog reconciliation reporting in the GUI. Startup
now calls `_settle_model_catalog()` exactly once after restore reporting and
before history refresh. Reconciliation failures are converted into a structured
attention result so a catalog defect cannot abort GUI startup. Repaired,
attention and failure outcomes update the status line; warning dialogs are
scheduled only for attention/failure outcomes. A healthy no-op remains silent.

Added complete Ukrainian, Czech and English messages for repaired, attention,
rebuilt/quarantined and failed catalog recovery outcomes.

## TDD evidence

- RED: `pytest -q tests/test_gui_contract_app.py tests/test_i18n_app.py -k model_catalog`
  failed with 4 expected failures: missing startup call/method and missing
  catalog message keys.
- GREEN: the same focused command passed with 4 tests.

## Verification

- Focused GUI/i18n/CLI/catalog suite: **80 passed, 1 skipped**.
- Skip is the existing Windows directory-symlink test, unavailable without the
  required `SeCreateSymbolicLinkPrivilege`.
- `scripts/quality_gate.ps1`: PASS.
- Ruff on `src tests`: PASS.
- `git diff --check`: PASS.
- Full source compile and pytest are included in the quality gate.

## Commit

`feat(ui): report model catalog recovery at startup` (implementation commit).

## Risks and limits

- GUI behavior is covered headlessly with controller stubs; native Tk startup
  and packaged GUI acceptance remain outside this source task.
- Warning dialogs are deferred with Tk's existing startup scheduling pattern.
- Recovery details are rendered from structured blocked entries and remain
  local; no network or telemetry path was added.
