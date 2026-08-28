# Implementation status — VOICE Studio 0.3.0 Test RC

Last reviewed: 2026-08-28.

The repository contains a verified Windows Test RC, not a signed production
release. Current executable, test and native-verification evidence is recorded
in `VERIFICATION.md`; the reproducible build command is documented in
`WINDOWS_BUILD_README.md`.

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
| W2-M2 disposable media containment | PASS headless | PyAV runs in a disposable spawn child with a deadline, never in the parent; ffmpeg runs in its own process group and its descendants are killed with it; 2 GiB source, 7,200 s duration and 230,404,096-byte output ceilings enforced and wired |
| W2-R1 journaled restore recovery | PASS headless | restore writes an atomic journal before the swap; `recover_interrupted_restore()` runs before the first `LocalStore` in GUI and CLI and completes, rolls back, or discards staging deterministically; a `*.recovery-*` directory is never deleted; the journal carries no transcript text and no key material |
| W1.5 evidence base | PASS | suite cannot hang (`pytest-timeout`, 120 s/test); CI compiles and lints the same `src tests scripts packaging` surface as the local gate; both workflow jobs bounded at 20 minutes |
| Integrated source compilation | PASS | `python -m compileall -q src tests scripts packaging` |
| Integrated tests | PASS | `347 passed` on Linux/CPython 3.12, no skips |
| Integrated lint/policy/diff | PASS | Ruff, workflow-pin policy and `git diff --check` |
| Integrated packaging/dependency gate | PASS | `python -m build --wheel`, `python -m pip check`, `python -m pip_audit` (no known vulnerabilities) |

## Implemented but pending native acceptance

- W1 GUI/microphone lifecycle on physical Windows 10/11 x64 and macOS Apple
  Silicon;
- W2-M1 cross-process/file-lock behavior under antivirus, removable media and
  forced termination;
- W2-A1 real large-archive/disk-full behavior on both supported filesystems;
- W2-R1 recovery after a real power loss or forced termination on both
  supported filesystems; the headless suite simulates the interruption.

These remain `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`, never production PASS.

## Not implemented yet

- `cryptography` dependency and encrypted backup v2;
- coordinated shutdown and multiprocessing queue disposal;
- SQLite/filesystem/model-catalog reconciliation;
- all W3 VAD/timestamp/hardware/editor work;
- W4 SBOM, signed/notarized installers and updater;
- W5 physical 50-task acceptance per OS;
- W6 independent release go/no-go.

## Current environment limitations

The Windows x64 executable, locked Python 3.12 build environment, packaged
runtime probe and GUI launch were verified. Clean-machine acceptance, signing,
real microphone/hotkey coverage and production speech-quality measurements
remain open exactly as listed in `VERIFICATION.md`.

## Release rule

Do not call this unsigned Test RC a signed production release until the native,
signing and clean-machine gates in `VERIFICATION.md` are complete.
