# W2-C3 Task 1 — model-catalog inspection

## Scope

Implemented `ModelCatalog.inspect(root)` as a classmethod in
`src/voice_studio/model_catalog.py`. The inspector is read-only and never
constructs `ModelCatalog`, creates directories, calls reconciliation, hashes
model files, removes residue, quarantines a manifest, adopts an orphan, or
uses the network.

It reports the stable fields `status`, `manifest`, `missing`, `orphans`,
`blocked`, `staging`, and `residue`. Manifest parsing is direct and validates
the catalog version, model list, IDs, and contained relative paths. Root and
model paths are inspected with `lstat`/no-follow directory entries; model
completeness is limited to direct regular `model.bin` and `config.json`
checks. Staging and catalog temporary entries include deterministic age and
safety metadata without cleanup.

## TDD evidence

- RED: `pytest -q tests/test_model_catalog_app.py -k inspect` on the untouched
  implementation: 5 failed because `ModelCatalog.inspect` did not exist.
- GREEN: the same focused selection after implementation: 5 passed, 1 skipped
  (Windows symlink privilege boundary).

## Verification

- `ruff check src/voice_studio/model_catalog.py tests/test_model_catalog_app.py` — PASS.
- `pytest -q tests/test_model_catalog_app.py` — PASS, 32 passed, 2 skipped
  (Windows symlink privilege boundaries).
- `python -m compileall -q src tests` — PASS.
- Baseline before changes: full suite PASS, 483 passed, 3 skipped.

The new tests snapshot file bytes, file `st_mtime_ns`, and directory entry
names before and after inspection for absent, drifted, residue, staging,
invalid-manifest, and symlink cases.

## Fix round 1

Root cause: a decoded control character in a manifest path reached
`Path.lstat()`, which raises `ValueError` on Windows instead of `OSError`.
Inspection now rejects Unicode control/surrogate characters before creating a
filesystem path and catches path-boundary `ValueError`/`UnicodeError` as a
defense in depth. Catalogued root entries are also not reported a second time
by the shallow root scan after their manifest path has already been blocked.

Added regressions for an escaped `\\u0000` manifest path and real Windows
directory junctions for the root, a catalogued target, and a catalogued
ancestor. Junction tests skip only when the platform or `mklink` privilege is
unavailable and snapshot the external tree's entries, bytes, and mtimes.
