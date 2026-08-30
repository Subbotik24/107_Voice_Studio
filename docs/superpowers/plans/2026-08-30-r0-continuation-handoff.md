# R0 continuation handoff after W3-H1

Status date: 2026-08-30. This is a restart plan, not authorization to implement
the remaining increments in the current session.

## Verified restart point

- Local branch: `main`.
- W3-H1 final production commit: `2819ecc`.
- W3-H1 final evidence commit: `39ab4b6`.
- Final W3-H1 focused scope: 151 passed.
- Final post-production source gate: compileall PASS; 605 passed, 9 Windows
  symlink-privilege skips.
- Fresh frozen console probe: cold spawned child completed with status `ok`
  under the bounded 5-second detector deadline.
- `docs/verification/R0_EXECUTION_LOG.md` is the authoritative no-repeat log.
  Reuse a green checkpoint until its recorded rerun trigger is met.
- No push was performed. Before resuming, compare local `main` with its remote
  and inspect the working tree; do not discard local commits or user changes.

## Scope boundary

Finish R0 only. Do not implement word timestamps, diarization, the full
split/retime segment editor, batch transcription, system-audio capture, live
streaming, or other R1/R2 items. They remain proposals in `FUTURE_GROWTH.md`.

The remaining in-repository order is fixed:

1. W3-V1 configurable VAD.
2. W3-E1 minimal subtitle consistency.
3. W2-E1 encrypted backup v2.
4. R0.10 integrated unsigned Test RC gate and documentation alignment.
5. External physical/signing acceptance, only when the required machines,
   credentials and approvals are available.

For every increment: write a bounded design/implementation plan, use strict
RED/GREEN TDD, run focused tests while coding, obtain independent review, and
run one full source gate only after the final production-code change. Append
the exact commit, command, result, skips, limitations and rerun trigger to the
R0 execution log.

## Increment 1 — W3-V1 configurable VAD

Goal: let a Local Whisper user disable VAD when it clips quiet speech while
preserving the current `vad_filter=True` default for existing settings.

Required contract:

- Add a persisted boolean setting whose default is enabled.
- Pass it only to `FasterWhisperEngine`; Ollama and OpenAI profiles ignore it.
- Use only bounded optional VAD parameters supported by the pinned
  faster-whisper version; do not expose arbitrary dictionaries or change
  `beam_size` as part of this increment.
- Add a clear readonly/boolean GUI control and CLI/config validation without
  loading model runtimes in the parent process.
- Preserve settings migration compatibility and all original/raw invariants.

Acceptance: enabled remains the default, disabled reaches exactly
`WhisperModel.transcribe(vad_filter=False)`, saved/reloaded settings preserve the
choice, and other engines behave exactly as before.

## Increment 2 — W3-E1 minimal subtitle consistency

Goal: after manual save, TXT/MD and SRT/VTT contain the same corrected wording
without inventing timecodes.

Use the approved rule in the R0 design and completion roadmap:

- An edit inside one segment changes only its editable text.
- An edit crossing segment boundaries merges exactly the affected segments and
  retains the outer existing `[start, end]` interval.
- Empty editable segments disappear.
- Never create, interpolate, split, or move a timestamp.
- Preserve every raw segment text and top-level `raw_text` unchanged.
- Store bounded undo metadata for the previous segment list and keep editor
  formatting metadata intact.
- Make dictionary correction derive consistently from segment results so a
  multiword rule cannot silently produce different document/subtitle text.

Acceptance must cover inside-segment edits, boundary insertions, cross-boundary
replacement, delete-all, replace-all, no segments, no-op save, undo, bundle
round-trip, and TXT/MD/SRT/VTT equality. Full split/retime editing remains R1.

## Increment 3 — W2-E1 encrypted backup v2

This is the largest remaining in-repository change and requires a separate
security design before code.

Required contract:

- Keep backup v1 readable and behaviorally unchanged through version-dispatched
  verification/restore.
- Define v2 authenticated encryption with the approved `cryptography`
  dependency; no custom cryptography.
- Never write a passphrase, derived key, plaintext private payload, or reusable
  secret to settings, restore journals, sidecars, diagnostics, CLI output or
  logs.
- Authentication failure is a hard, concrete error and never falls back to
  plaintext parsing.
- Preserve archive/version budgets with explicit ciphertext overhead and all
  ZIP/path/reparse protections.
- Preserve current models/exports through restore and never delete a user
  original.
- Update Windows lock, SBOM, PyInstaller collection and frozen-runtime probes.

Before implementation, resolve the public API/passphrase boundary and recovery
workflow explicitly. Add regression coverage for v1 compatibility, wrong
passphrase, tampering, interruption, secret scanning, budgets and frozen
dependency inclusion.

## Increment 4 — R0.10 integrated unsigned Test RC

After all production increments are review-clean:

- Reconcile README, Help, architecture, roadmap, implementation status,
  security and verification documents with actual behavior.
- Run the final source quality gate, build and inspect the wheel, run
  `pip check`, regenerate/check the deterministic SBOM, rebuild the unsigned
  Windows Test RC, execute frozen-runtime and clean-profile smoke probes, and
  produce checksums/release manifests.
- Verify no secrets, absolute user paths, model binaries or temporary probe
  files are tracked.
- Keep the normal windowed-launcher CLI-dispatch limitation explicit unless a
  separately approved increment fixes it.
- Record every unavailable macOS, clean-machine, microphone/hotkey, signing,
  notarization and 50-task check as `NOT_RUN`; never infer native acceptance
  from source tests.

## External completion gates

These cannot be completed by repository work alone:

- physical Windows 10/11 x64 and macOS Apple Silicon acceptance;
- 50 controlled tasks per OS with zero crashes;
- real microphone, hotkey, permissions, retention and format matrix;
- licensed closed uk/cs/en corpus before any production WER/CER claim;
- Apple/Azure signing credentials, notarization and clean-machine install;
- legal/license and release go/no-go approval.

Until those gates run, call the artifact an unsigned Test RC, not a signed
production release.

## First command sequence when work resumes

```powershell
git status --short --branch
git log -5 --oneline --decorate
git fetch --prune
git status --short --branch
Get-Content -Raw docs/verification/R0_EXECUTION_LOG.md
```

Do not run the full suite merely on resume. First compare the tracked head and
rerun triggers. Start W3-V1 with a new failing focused test; run the full gate
only after its last production-code change.
