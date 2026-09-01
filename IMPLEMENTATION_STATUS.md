# Implementation status — VOICE Studio 0.3.0 Test RC

Last reviewed: 2026-09-01.

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
| R0 W3-H1 hardware settings and advisory detection (2026-08-30) | PASS source/headless + temporary frozen probe | `device`/`compute_type` lexical validation fails at the job boundary before source preparation or worker startup; runtime CTranslate2 preflight selects the runtime device for `auto`, rejects unavailable CUDA and unsupported concrete pairs before WhisperModel load, and reports broken runtime imports concretely; bounded spawn detection returns immutable capabilities with `auto/default` fallback; GUI detection uses the retained worker registry and never mutates saved settings; a fresh console-only PyInstaller probe completed the cold no-parent-preload round-trip, while the normal windowed launcher remains GUI-only |
| R0 W3-V1 configurable VAD (2026-08-30) | PASS source/headless | enabled remains the migration-compatible default; Settings, GUI and mutually exclusive CLI flags preserve the explicit choice; only Faster Whisper receives `vad_filter`; focused 13 tests and the prior full gate passed |
| W2-E1 encrypted backup v2 (2026-08-31) | PASS source/headless | explicit opt-in `cryptography>=50,<51` dependency; Argon2id → HKDF → chunked AES-256-GCM (1 MiB chunks) with an HMAC-authenticated plaintext manifest, encrypted private index and opaque `payload/NNNNNNNN.enc` ZIP_STORED members; version-dispatched create/verify/restore with streaming budgets; encrypted `.restore-settings-v2` sidecar and passphrase-aware journal recovery; CLI `--encrypt` plus interactive getpass flows and GUI masked-prompt flows; wrong passphrase/tampering is a hard error with no plaintext fallback; secret-hygiene, frozen-style crypto probe, deterministic SBOM and structure proofs passed; packaged/native acceptance remains open for R0.10 |
| R0 W3-E1 minimal subtitle consistency (2026-08-30) | PASS source/headless | manual saves synchronize the editable segment layer without creating, splitting or moving timestamps; cross-boundary edits merge into existing outer intervals, empty cues disappear while their raw wording is absorbed into an adjacent honest outer interval, one bounded validated undo snapshot is retained, and dictionary correction derives consistently from segment results; focused 21 tests passed and the final compile/Ruff/pytest gate passed 643 tests with 9 Windows symlink-privilege skips |
| Integrated source compilation | PASS | `python -m compileall -q src tests scripts packaging` |
| Integrated tests | PASS | `347 passed` on Linux/CPython 3.12, no skips; green on macOS-14 and windows-2022 x CPython 3.11/3.12 in CI run 33170283362 |
| Integrated lint/policy/diff | PASS | Ruff, workflow-pin policy and `git diff --check` |
| Integrated packaging/dependency gate | PASS | `python -m build --wheel`, `python -m pip check`, `python -m pip_audit` (no known vulnerabilities) |
| Deep audit and fix round (2026-09-01) | PASS source/headless | four-lens full-repository correctness audit; all confirmed findings fixed with failing-first regression tests (GUI busy/polling, atomic temp writes, v1 restore promotion recovery, storage cleanup/reference/segment-raw gates, cross-process catalog lock, cleanup-submission outcome, HTTPS registry, cloud extension contract); Linux gate `875 passed`, 14 Windows-junction skips; evidence in `VERIFICATION.md` |
| Usability 1 central navigation (2026-09-01) | PASS source/headless | Dashboard, Studio, Dictionary and History pages behind one dispatcher; every navigation and the History → Studio hand-off pass through the dirty Save/Discard/Cancel guard; uk/cs/en key parity; gate `886 passed` with 9 Windows symlink-privilege skips |
| Usability 2 managed terminology dictionary (2026-09-01) | PASS source/headless | Dictionary page over a managed local dictionary file with atomic saves, merge accounting, bounded JSON/CSV import/export, read-only handling of an external dictionary and Settings reconciliation; gate `940 passed` with 9 Windows symlink-privilege skips |
| Usability 3 Local Whisper recognition hints (2026-09-01) | PASS source/headless | immutable per-request `TranscriptionHints` (≤256 terms, ≤8192 UTF-8 bytes) built in the parent from the already validated active dictionary; only faster-whisper receives them as `hotwords`, the worker rejects `dictionary_path`, terms stay out of settings, transcripts, metadata, diagnostics, logs and worker error detail, and the model cache key is unchanged; gate `954 passed` with 9 Windows symlink-privilege skips |
| Usability 4 Dashboard statistics and History filters (2026-09-01) | PASS source/headless | `dashboard.py` immutable `DashboardStatistics`/`HistoryFilter`; `LocalStore.statistics()` streams every row without the 250-row UI limit and without migration, counting unreadable payloads as invalid instead of raising; combined text/date/language/engine/model/status/retained-audio filters apply before `limit` while the legacy path stays byte-for-byte; Tk KPI grid, top-3 rankings and refresh without polling; gate `993 passed` with 14 Linux junction skips, wheel and `pip check` PASS; Xvfb source GUI smoke only |
| Usability 5 Studio editor tools (2026-09-01) | PASS source/headless | `editor_tools.py` literal Unicode `find_matches`/`apply_replacements` with validated spans, `segment_spans` alignment and whole-word filler matching; toolbar Find/Replace with match count and highlight, add-selection-to-dictionary through the managed save path (editor text only, never storage), and a per-match filler preview; all three edit only the open editor until Save and `raw_text` stays immutable; gate `1055 passed` with 14 Linux junction skips |
| Usability 6 confidence review panel (2026-09-01) | PASS source/headless | `confidence_entries()` sorts scored segments lowest-first below a validated 0.00–1.00 threshold and lists unusable scores after them as **no score**, never as 0.0; the panel threshold defaults to 0.60, is page state only and is never written to settings or disk (pinned by a test); wording presents the engine's own confidence signal with no error-probability or accuracy claim; gate `1089 passed` with 14 Linux junction skips |
| Usability 7 local audio playback (2026-09-01) | PASS source/headless | `playback.py` on the existing PyAV + sounddevice dependencies with injected decode/device seams, bounded ~100 ms PCM chunks, one daemon worker with generation-guarded play/pause/stop/seek/speed, device abort to unblock a blocked write and a 2 s join budget; speed is resampling only, so pitch shifts; playback resolves ONLY the retained managed copy and never looks up an external original (pinned by tests), starts at a segment from the confidence panel, and stops on page/transcript switch, restore and close; gate `1135 passed` with 14 Linux junction skips, wheel and `pip check` PASS; real audio device NOT RUN |

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
- Local playback plays only the retained managed audio copy; a record without
  one cannot be played, and the 0.75–2× speed is implemented by resampling, so
  a changed pace also shifts the pitch. Playback was exercised through fake and
  Xvfb source seams only; no real audio device was available in the
  verification environment.

These remain `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`, never production PASS.

## Not implemented yet

- W2-S1 native physical and packaged acceptance (the process/queue and
  app/recorder/hotkey/maintenance thread halves are verified source/headless
  above);
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

The 2026-09-01 usability pack (increments 1-7) was verified on Linux/CPython
3.12 source/headless only; its final controller gate recorded `1135 passed` with
14 Linux junction skips. Packaged and native usability acceptance, physical Tk
smoke and playback on a real audio device were **NOT RUN**; the per-increment
evidence is in `docs/verification/2026-09-01-usability-pack.md`.

## Release rule

Call this build an unsigned Test RC, not a signed production release, until the
clean-machine, device-matrix and signing gates are complete.
