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
