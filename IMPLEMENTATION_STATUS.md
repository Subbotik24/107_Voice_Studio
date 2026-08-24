# Implementation status — VOICE Studio 0.4 RC work in progress

Last reviewed: 2026-08-24.

The repository is a verified source checkpoint, not a signed production
release. The canonical continuation instructions are in
`docs/CONTINUATION_PLAN_0.4.md`.

## Verified repository scope

| Area | Status | Exact evidence |
| --- | --- | --- |
| W0 workflow supply-chain baseline | PASS | all workflow `uses:` pinned to full commit SHA; repository policy test passes |
| W1 strict settings and clipboard default | PASS | malformed types rejected; `auto_copy=False` |
| W1 editor dirty-state protection | PASS | Save/Discard/Cancel and atomic editor-state persistence |
| W1 recorder safety | PASS | bounded queue and duration, degraded-capture rejection, owned-temp cleanup |
| W1 original/raw invariants | PASS | originals preserved; `raw_text` immutable regressions |
| W2-M1 cancellable managed import | PASS | one monotonic budget, one-pass copy/hash, unique immutable managed targets, bounded collision retry |
| W2-M1 save/cleanup serialization | PASS | SQLite `BEGIN IMMEDIATE` prevents committed records from referencing concurrently removed managed audio |
| W2-A1 generic ZIP safety primitives | PASS | bounded EOCD/ZIP64 preflight, portable member identities, hierarchy trie, bounded copy/free-space primitives |
| Integrated source compilation | PASS | `python -m compileall -q src tests scripts packaging` |
| Integrated tests | PASS | `272 passed, 2 skipped, 5 subtests passed` at code checkpoint `c5462a9` |
| Integrated lint/policy/diff | PASS | Ruff, workflow-pin policy and `git diff --check` |

## Implemented but pending native acceptance

- W1 GUI/microphone lifecycle on physical Windows 10/11 x64 and macOS Apple
  Silicon;
- W2-M1 cross-process/file-lock behavior under antivirus, removable media and
  forced termination;
- W2-A1 real large-archive/disk-full behavior on both supported filesystems.

These remain `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`, never production PASS.

## Not implemented yet

- disposable contained PyAV/FFmpeg media worker and process-tree termination;
- `.hws` and backup consumers wired to the generic archive limits;
- `cryptography` dependency and encrypted backup v2;
- journaled restore/startup recovery;
- coordinated shutdown and multiprocessing queue disposal;
- SQLite/filesystem/model-catalog reconciliation;
- all W3 VAD/timestamp/hardware/editor work;
- W4 reproducible locks, SBOM, signed/notarized installers and updater;
- W5 physical 50-task acceptance per OS;
- W6 independent release go/no-go.

## Current environment limitations

The verification interpreter used for this checkpoint did not contain
`build`, `pip`, `pip_audit` or `cryptography`. No packages were silently
installed into that frozen environment. These gates remain `BLOCKED` until the
next dependency increment creates and freezes its own environment.

## Release rule

Do not create `v0.4.0`, publish installers, or call this production-ready until
W2-W6 and the human signing/native/legal gates in
`docs/CONTINUATION_PLAN_0.4.md` are complete.
