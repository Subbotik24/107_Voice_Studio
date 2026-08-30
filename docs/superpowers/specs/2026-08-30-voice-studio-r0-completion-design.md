# VOICE Studio R0 Completion Design

## 1. Purpose

R0 completes the original VOICE Studio product idea: a privacy-first desktop
application that records or imports media, transcribes it locally by default,
keeps immutable STT output, permits safe editing, stores local history, exports
the supported formats, manages local models, and creates recoverable backups.

R0 is complete when the repository and unsigned Test RC are code-complete and
repeatably verifiable. Physical-device acceptance, signing, notarization and a
licensed WER/CER corpus remain external release gates and may never be reported
as passed without real evidence.

This design calls the completion line **R0** to match the product owner's
terminology. It includes the release-blocking work currently spread across P0
and the audited W2/W3/W4 hardening items. R1 and R2 are future product growth,
documented separately in `FUTURE_GROWTH.md` and excluded from implementation.

## 2. Product boundary

### Included in R0

1. Model-catalog self-healing after interrupted install or manifest writes.
2. Backup restore that preserves machine-local `models/` and `exports/` state.
3. Coordinated shutdown of transcription workers, queues, recorder threads and
   hotkey resources.
4. Storage audit and explicitly confirmed reconciliation of database,
   filesystem, model-catalog and export drift.
5. Early validation and bounded detection of Whisper `device` and
   `compute_type` settings.
6. Configurable VAD with the current enabled behavior preserved by default.
7. Correct synchronization between manual document edits and subtitle
   segments, following the rule “an edit does not create time”.
8. A reproducible SBOM included in release artifacts.
9. Encrypted backup v2 with backward-compatible read support for backup v1 and
   no key material in settings, journals, diagnostics or logs.
10. Final documentation alignment, source gates, wheel build, frozen-runtime
    probes, Windows unsigned Test RC rebuild and recorded external acceptance
    gaps.

### Excluded from R0

- speaker diarization;
- word-level timestamps;
- a full split/retime subtitle editor;
- batch transcription;
- per-project dictionary profiles;
- insertion into the active application;
- local REST/IPC automation;
- system-audio capture;
- live streaming transcription;
- automatic model-update delivery;
- team synchronization or cloud collaboration.

The excluded capabilities belong to R1/R2. The minimal subtitle synchronization
in R0 is a correctness repair for formats the product already exports; it is
not the future full segment editor.

## 3. Non-negotiable invariants

- The user's original media file is never deleted.
- `raw_text` is immutable STT output. Editing and cleanup change only
  `corrected_text` and the editable segment layer.
- Local/private behavior is the default. No telemetry, upload or cloud fallback
  is introduced.
- Cloud STT and cleanup retain their existing explicit consent gates.
- A model directory is never deleted without an explicit confirmed action.
- Corrupt manifests, restore recovery directories and backup recovery evidence
  are quarantined or retained, never guessed away.
- SHA-256 verifies integrity; it is not represented as publisher authenticity.
- Existing CLI signatures and backup v1 reads remain compatible unless a
  migration note and regression tests explicitly cover a change.
- No API key, encryption key, passphrase, transcript, private path, model binary
  or user database enters Git or diagnostics.
- New engines continue to implement `SpeechEngine` and return `EngineResult`.
- Recognition quality is not claimed without a licensed closed test set and
  measured WER/CER.

## 4. Increment architecture and order

Each increment is a separate RED→GREEN cycle, review and commit. The order
prevents later audit code from encoding an unfinished definition of healthy
state.

### R0.1 — Model-catalog reconciliation

Add an offline, idempotent `ModelCatalog.reconcile()` that adopts complete
orphaned model directories, drops only provably absent phantom entries,
quarantines corrupt manifests, retains ambiguous paths, removes only stale
well-formed staging directories, and avoids rehashing already catalogued model
weights. Use unique atomic manifest temporary files. Integrate reconciliation
only at GUI startup and inside the CLI `models` branch, with an explicit
`models reconcile` command.

The detailed acceptance contract in `NEXT_ANTIGRAVITY_TASK.md` remains the
source of truth for this increment.

### R0.2 — Restore preserves machine-local state

Extend the existing restore transaction and journal so `models/` and `exports/`
from the current data root are transferred into restored staging before the
swap. Do not place model weights in the backup archive. Recovery must complete
or roll back this transfer without deleting `*.recovery-*` evidence.

### R0.3 — Coordinated shutdown

Give `TranscriptionJobController` one idempotent close protocol that stops new
submissions, terminates or joins the persistent worker with bounded timeouts,
closes multiprocessing queues and joins queue feeder threads. Extend the same
bounded lifecycle to recorder, hotkey, AI-cleanup and model-download workers.
Tk's main thread must never perform an unbounded join.

### R0.4 — Audit and reconciliation

Extend `LocalStore.audit()` to report missing managed files, unmanaged media,
model-catalog drift and stale exports. Repair stays a separate explicit action.
Any destructive repair requires confirmation, and no repair may target a user
original.

### R0.5 — Hardware settings validation

Replace free-form `device` and `compute_type` values with validated supported
values. Detection is bounded and advisory: failure to inspect hardware produces
an actionable fallback rather than blocking startup. Invalid settings fail
before a model worker starts.

### R0.6 — Configurable VAD

Add a persisted boolean VAD setting and bounded optional parameters only where
the current faster-whisper adapter supports them. Existing users retain
`vad_filter=True`. Ollama and cloud profiles ignore Whisper-only VAD settings
without hidden behavior changes.

### R0.7 — Subtitle consistency

When manual edits remain inside one segment, update only that segment's editable
text. When an edit crosses segment boundaries, merge the affected segments and
retain the exact outer `[start, end]` interval. Never create, interpolate or
move a timestamp. Empty editable segments disappear. Preserve the immutable raw
segment text and provide undo metadata for the prior segment list.

TXT/MD and SRT/VTT must represent the same corrected wording after save. The
future ability to split or retime segments remains outside R0.

### R0.8 — Reproducible SBOM

Generate a deterministic CycloneDX or SPDX artifact from the pinned Windows
lock file, normalize ordering, exclude absolute paths, test the schema and add
the SBOM to the release manifest and packaged release directory.

### R0.9 — Encrypted backup v2

Introduce version-dispatched backup reading: v1 remains readable unchanged;
v2 encrypts private payloads with an authenticated construction from the
approved `cryptography` dependency. Define a key/passphrase boundary that never
serializes secret material into settings, restore journals, sidecars,
diagnostics or logs. Update archive budgets for ciphertext overhead and include
frozen-runtime dependency checks.

### R0.10 — Integrated release gate

Align README, Help, architecture, implementation status and verification with
the actual behavior. Run the full source quality gates, build and inspect the
wheel, rebuild the unsigned Windows Test RC, execute frozen-runtime and clean
profile smoke probes, create checksums and release manifest, and record every
physical/signing check that remains `NOT_RUN`.

## 5. Error handling and recovery rules

- Reconciliation distinguishes `present`, `absent` and `unknown`; only `absent`
  permits automatic manifest removal.
- Startup repair failures are visible and non-fatal when safe continuation is
  possible. CLI repair commands return structured JSON and non-zero status on
  failure.
- Shutdown methods are idempotent and safe when initialization was only
  partially completed.
- Restore and backup changes are journaled before filesystem swaps.
- Encryption authentication failure is a hard, specific error and never falls
  back to plaintext interpretation.
- Unsupported hardware configuration is rejected before inference with a
  concrete setting name and supported alternatives.
- Subtitle repair never fabricates timing information.

## 6. Verification strategy

Every increment follows test-driven development:

1. Add the smallest regression tests that fail on the untouched implementation.
2. Capture the expected failure reason.
3. Implement the smallest coherent change.
4. Run the focused tests to green.
5. Run related contract and privacy tests.
6. Run the complete quality gate before commit.

The repository-wide gate is:

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

For UI or startup changes, use isolated
`VOICE_STUDIO_CONFIG_DIR`, `VOICE_STUDIO_DATA_DIR` and
`VOICE_STUDIO_CACHE_DIR` values and verify a clean launch. For packaging, the
locked Python 3.12 Windows build gate and frozen worker probe are mandatory.

## 7. Model delegation and control

The primary high-capability model is the controller. It owns requirements,
architecture, implementation plans, task boundaries, review findings, security
and privacy decisions, cross-increment integration, final verification and Git
pushes.

Bounded implementation work is delegated sequentially to a cost-efficient
standard model. Each executor receives only one increment, its spec, exact
files, acceptance tests and forbidden areas. Executors do not change scope,
push, create branches or work concurrently in the shared tree. The controller
reviews the diff and verification evidence after every executor turn, requests
corrections when needed, and reruns the relevant tests independently.

If an increment changes public interfaces, security boundaries or more than one
subsystem unexpectedly, execution returns to the controller for a design check
instead of allowing the executor to improvise.

## 8. Definition of done

R0 is code-complete only when:

- R0.1–R0.10 are implemented with regression tests and descriptive commits;
- code, Help and product documentation agree;
- compile, Ruff, Help validation, all tests, wheel build and `pip check` pass;
- current Windows frozen-runtime and packaged GUI probes pass;
- CI and CodeQL are green on `main`;
- the worktree is clean and contains no secrets, user paths, model binaries or
  private artifacts;
- external physical-device, signing, legal and WER/CER gates are listed with
  their real evidence state and are never silently promoted to PASS.

R0 code completion produces an unsigned Test RC. Calling it a signed production
release still requires the external gates.
