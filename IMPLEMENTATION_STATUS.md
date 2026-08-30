# Implementation status — VOICE Studio 0.3.0 Test RC

Last reviewed: 2026-08-30.

The repository contains a verified unsigned Windows Test RC. It is a standalone
VOICE Studio product; there are no retired product names or startup AI wizard
in the supported flow.

## Verified product scope

| Area | Status | Evidence |
| --- | --- | --- |
| Ollama-first profiles | PASS | `ollama-local` is the persistent default; Local Whisper and OpenAI cloud are explicit alternatives |
| Saved settings | PASS | profile, engine, recognition/UI languages and exact `gemma4:12b` choice survived save and relaunch |
| Local Ollama audio | PASS | final packaged EXE transcribed a real synthetic English WAV and applied local Ollama cleanup |
| Raw/original safety | PASS | original files are preserved; `raw_text` remains immutable while edits use `corrected_text` |
| Localized UI and Help | PASS | Ukrainian, Czech and English catalogs and canonical Help trees have parity; Ukrainian and Czech were exercised in the packaged UI |
| Reference UI | PASS | cream palette, typography, wide 250 px navigation, compact controls and responsive main window visually inspected on Windows |
| Windows packaging | PASS | reproducible Python 3.12/PyInstaller gate, frozen runtime probe, GUI launch, wheel and exact Help payload assertions |
| Integrated checks | PASS | compile, Ruff, Help validation and 362 tests in the final build gate |
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
| W2-C1 restore preserves machine-local state (2026-08-30) | PASS source/headless | restore replaces backed-up transcripts, settings and sources while copying the current `models/` and `exports/` into staging before the first live-root rename; both trees remain outside `.voice-backup`; unsafe links/reparse points abort with `local restore state contains an unsafe path: ...` before the live root changes; focused restore coverage passed 10 tests with 2 Windows symlink-privilege skips and the full `.venv` gate passed 435 tests with 3 skips |
| W2-C2 model-catalog self-healing | PASS source/headless | `models reconcile` adopts complete orphan directories, drops only provably absent entries, retains incomplete or unsafe paths as blocked, quarantines corrupt manifests, cleans only bounded stale residue, and runs automatically at model-command and GUI-startup boundaries; 426 tests passed with one Windows symlink-privilege skip; source GUI smoke and complete evidence are recorded in `VERIFICATION.md` |
| W2-C3 storage audit and confirmed repair (2026-08-30) | PASS source/headless | end-to-end read-only `storage audit` validates the existing root and reads SQLite through a stable temporary database/WAL snapshot with bounded retry and root-identity checks; it additively reports missing transcript rows plus nested model/export drift while preserving the core top-level status used by restore; `storage repair-missing ID --expected-path PATH --yes` transactionally detaches only a proven missing managed reference and never unlinks or recreates audio; export candidates are never auto-deleted; related coverage passed 150 tests with 8 Windows symlink-privilege skips and the full `.venv` gate passed 533 tests with the same 8 skips |
| W4-B1 reproducible SBOM (2026-08-30) | PASS source/contract | deterministic CycloneDX 1.6 `voice-studio-sbom.cdx.json` generated from raw UTF-8 bytes of `requirements-windows.lock`; manifest reads use pinned handle/descriptor-relative no-reparse boundaries with byte/fingerprint revalidation, and both builders publish through atomic no-replace promotion; two temporary-root generations remained byte-identical with 58 components and no private/absolute paths; final-fix code commit `3e85aaf1ab6e8505925ef12a5a822181d6b0a4df` passed the single post-fix gate with 583 tests and 9 Windows symlink-privilege skips; wheel and `pip check` passed |
| W2-S1 coordinated worker shutdown (process/queue half, 2026-08-30) | PASS source/headless | transcription and model-download process generations have bounded terminate/kill cleanup, exactly-once queue disposal, reusable close, epoch/concurrency protection and resource-tracker regression evidence; the app/recorder/hotkey thread half remains open, so full W2-S1 is still IN PROGRESS |
| W2-S1 coordinated worker shutdown (app/recorder/hotkey/maintenance thread half, 2026-08-30) | PASS source/headless | GUI worker registry, one-budget bounded joins, late-event gating, recorder residue retention, bounded hotkey stop and maintenance refusal are covered by regression tests and a clean-profile source GUI close smoke; native physical and packaged acceptance remain open, so full W2-S1 is still IN PROGRESS |
| W1.5 evidence base | PASS | suite cannot hang (`pytest-timeout`, 120 s/test); CI compiles and lints the same `src tests scripts packaging` surface as the local gate; both workflow jobs bounded at 20 minutes |
| R0 W3-H1 hardware settings and advisory detection (2026-08-30) | PASS source/headless + temporary frozen probe | `device`/`compute_type` lexical validation fails before worker creation; runtime CTranslate2 preflight rejects unavailable CUDA and unsupported concrete pairs before WhisperModel load; bounded spawn detection returns immutable capabilities with `auto/default` fallback; GUI detection uses the retained worker registry and never mutates saved settings; a fresh console-only PyInstaller probe completed the cold no-parent-preload round-trip, while the normal windowed launcher remains GUI-only |
| Integrated source compilation | PASS | `python -m compileall -q src tests scripts packaging` |
| Integrated tests | PASS | `347 passed` on Linux/CPython 3.12, no skips; green on macOS-14 and windows-2022 x CPython 3.11/3.12 in CI run 33170283362 |
| Integrated lint/policy/diff | PASS | Ruff, workflow-pin policy and `git diff --check` |
| Integrated packaging/dependency gate | PASS | `python -m build --wheel`, `python -m pip check`, `python -m pip_audit` (no known vulnerabilities) |

## Known release limits

- The EXE and portable ZIP are unsigned and can trigger SmartScreen.
- Clean-machine acceptance, real microphone/hotkey coverage and a 50-task
  physical-device run were not available.
- Ollama audio-language support depends on the installed model. The installed
  `gemma4:12b` passed the English smoke sample but returned no transcript for a
  synthetic Cyrillic sample; use recognition language `auto` where appropriate
  or select the Local Whisper profile for unsupported languages.
- Ollama audio input is intentionally limited to 30 minutes and returns a
  single untimed segment; Local Whisper remains available for longer/timed work.
- Speech quality has no production WER/CER claim without a licensed closed test
  set.
- W1 GUI/microphone lifecycle on physical Windows 10/11 x64 and macOS Apple
  Silicon;
- W2-M1 cross-process/file-lock behavior under antivirus, removable media and
  forced termination;
- W2-A1 real large-archive/disk-full behavior on both supported filesystems;
- W2-R1 recovery after a real power loss or forced termination on both
  supported filesystems; the headless suite simulates the interruption.
- W2-C1 restore during a real power loss or forced termination, and unsafe-link
  handling on physical filesystems under antivirus or removable-media
  conditions, were not run; the source suite covers simulated interruptions and
  Windows junction/reparse cases, while symlink creation was privilege-skipped.
- W2-C3 audit/repair was not exercised against physical removable media,
  antivirus races or a packaged executable; Windows junction/reparse tests ran,
  while eight symlink cases were privilege-skipped in this environment.

These remain `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`, never production PASS.

## Not implemented yet

- `cryptography` dependency and encrypted backup v2;
- W2-S1 native physical and packaged acceptance (the process/queue and
  app/recorder/hotkey/maintenance thread halves are verified source/headless
  above);
- R0 W3-V1 configurable VAD with the current enabled default preserved;
- R0 W3-E1 minimal subtitle consistency for existing exports;
- R1-excluded word-level timestamps and the full split/retime subtitle editor;
- W4 signed/notarized installers and updater (the reproducible SBOM increment is complete; native signing remains open);
- W5 physical 50-task acceptance per OS;
- W6 independent release go/no-go.

## Current environment limitations

The Windows x64 executable, locked Python 3.12 build environment, packaged
runtime probe and GUI launch were verified in earlier Test RC work. W4-B1
verified the source/contract SBOM and Python build artifacts only; no physical
Windows/macOS Test RC build or acceptance run was performed for this
increment. Clean-machine acceptance, signing, real microphone/hotkey coverage
and production speech-quality measurements remain open exactly as listed in
`VERIFICATION.md`.

## Release rule

Call this build an unsigned Test RC, not a signed production release, until the
clean-machine, device-matrix and signing gates are complete.
