# W2-C1 Task 2 report — journaled restore preserves local state

Date: 2026-08-30

## RED

Tests were added before the restore integration. On the untouched restore path,
the normal-restore regression failed with `FileNotFoundError` for the preserved
`models/tiny/model.bin`; the interruption regressions showed that the swap
staging did not contain the displaced local trees and that the injected
local-state copy interruption was never reached. The free-space regression
showed the preflight required only `verified["expanded_bytes"]`.

The exact crash simulation is an injected `KeyboardInterrupt` with message:

```text
simulated power loss during local-state copy
```

The injected copy first completes `exports/` into staging, then raises before
the first `data_root.replace`. With staging cleanup disabled to model process
death, the live root remained present and the atomic journal recorded
`stage: "swap_started"`; recovery subsequently returned
`action: "staging_discarded"` and left both live sentinels unchanged.

## GREEN

`restore_backup` now counts `_local_restore_bytes(data_root)` in the existing
free-space request and calls `_copy_local_restore_state(data_root, temporary)`
immediately after the unchanged atomic `swap_started` journal write, before
either rename. Copy failures therefore leave the live root untouched; normal
cleanup or the existing recovery branch discards partial staging. The existing
two-rename swap, journal schema/version, settings sidecar, archive format and
public signatures are unchanged. A directory snapshot now uses uncached
`os.lstat` entry metadata, avoiding intermittent Windows stale `DirEntry.stat`
metadata while retaining cooperative-change checks.

## Verification

```text
focused restore tests: 5 passed, 24 deselected
backup + model catalog + CLI + GUI contract: 99 passed, 3 skipped
Ruff: passed
compileall -q src tests scripts packaging: passed
git diff --check: passed
```

The three skips are Windows symlink-creation privilege boundaries
(`WinError 1314`); the junction regressions remain exercised. Real power loss,
forced termination and filesystem/antivirus failure races were not run.

## Commit

`fix(backup): preserve models and exports on restore`

## Risks

The local-state copy remains best-effort no-follow validation. A malicious
same-account actor can still replace a path between filesystem syscalls; this
is outside the documented local/private threat boundary. Recovery after an
actual power loss on physical Windows/macOS remains pending native acceptance.
