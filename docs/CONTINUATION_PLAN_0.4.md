# VOICE Studio 0.4 RC — continuation handoff

Date: 2026-08-24

This file is the tracked handoff for continuing work on another computer. The
authoritative branch after handoff is `main`; fetch and verify it before making
changes. No local worktree path, virtual-environment path, credential or model
binary is required from the previous computer.

## Restore the workspace

```bash
git clone https://github.com/Subbotik24/107_Voice_Studio.git
cd 107_Voice_Studio
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

Read `AGENTS.md`, its mandatory project/audit documents, and this continuation
plan before implementation. Create a fresh Python 3.12 environment using the
project instructions; do not copy the previous computer's virtual environment.

Establish the new-machine baseline:

```bash
python -m compileall -q src tests
PYTHONPATH=src python -m pytest -q
python -m ruff check .
python scripts/check_workflow_pins.py .github/workflows
git diff --check
```

On PowerShell, set `$env:PYTHONPATH = 'src'` before pytest instead of using the
inline POSIX assignment.

## Completed checkpoint

The following work is committed to `main` by this handoff:

1. W0 repository-executable baseline: pinned Actions, workflow policy and the
   signing/key-custody runbook, with external gates kept explicit.
2. W1 desktop data safety: strict settings and `auto_copy=False`, editor
   Save/Discard/Cancel, bounded recorder, owned microphone temps, original-file
   preservation and immutable-`raw_text` regressions.
3. W2-M1: one monotonic budget, cancellable one-pass managed copy/SHA-256,
   full-UUID immutable per-import targets, atomic no-overwrite promotion,
   bounded collision retry, structured residue errors and SQLite serialization
   between transcript save and managed cleanup.
4. W2-A1: bounded ZIP metadata inspection, classic EOCD and
   sentinel/nonsentinel ZIP64 preflight before `ZipFile`, portable member
   identities, hierarchy trie, bounded copy and free-space primitives.

Last exact integrated code checkpoint before this documentation update:
`c5462a99bb9d40d94cacdf18b0e9d8f40fb44ced`.

Evidence at that checkpoint:

- W1 focused: `82 passed`;
- W2-M1 focused: `47 passed`;
- W2-A1 focused: `55 passed`;
- full pytest: `272 passed, 2 skipped, 5 subtests passed`;
- compileall, Ruff, workflow policy and full-range diff check: PASS.

## Frozen design decisions

- User originals are never deleted and `raw_text` is immutable STT output.
- Managed imports are intentionally not deduplicated while active: each import
  owns a unique immutable target. SHA-256 remains separate metadata. The extra
  storage cost avoids shared-target overwrite/delete races.
- Managed-source save and cleanup use SQLite writer serialization. Save first
  preserves the file; cleanup first makes a later save reject missing audio.
- Archive inspection is generic. Product budgets and consumer wiring belong to
  their specific `.hws` and backup increments.
- Archive identities are portable across Windows/macOS semantics; hierarchy
  checks use one trie node per canonical component.
- Local/private by default, no telemetry, no background update checks and no
  silent cloud fallback remain mandatory.

## Next implementation sequence

Create one `codex/voice-*` branch/worktree per increment. Luna writes tracked
implementation changes through RED -> GREEN. Sol supplies the ticket, reviews
the exact range, integrates and reruns the exact merged tree.

### 1. W2-M2 — disposable media containment

- keep PyAV and FFmpeg out of Tk/CLI parent processes;
- establish POSIX process groups and a Windows kill-on-close Job Object;
- fail closed if containment cannot be established;
- enforce 2 GiB source, 7,200-second duration and 230,404,096-byte canonical
  output limits;
- terminate descendants and clean owned partial/canonical files on every
  handled exit;
- retain original-cloud upload only after explicit consent.

### 2. W2-A2 — connect archive limits

- define versioned `.hws` and backup budgets;
- consume `hermes_common.archive` from both readers;
- retain safe legacy read-only compatibility;
- reject bombs, unsupported methods, disk shortage and ambiguous members before
  extraction/promotion;
- never use `extractall()`.

### 3. W2-B1/B2 — encrypted backup v2

- add and freeze a reviewed `cryptography` dependency in a new environment;
- implement scrypt `n=65536,r=8,p=1`, 16-byte salt and 32-byte key;
- implement streaming AES-256-GCM with 12-byte nonce, 16-byte tag and canonical
  authenticated header;
- new backups are encrypted; plaintext v1 is restore/verify-only;
- use a hidden prompt or `--passphrase-file`, never a passphrase argv value;
- do not parse unauthenticated plaintext before GCM finalization.

### 4. W2-B3/L1/L2 — restore and lifecycle

- journaled component restore with private staging and startup recovery;
- do not swap/remove `models/` and `exports/` during transcript restore;
- coordinate mutation workers and defer close during critical promotion;
- dispose queues/processes and run restart soak;
- add SQLite/filesystem/catalog intents and conservative reconciliation;
- ambiguous state becomes `RECOVERY_REQUIRED`, never automatic deletion.

### 5. W3-W6

Continue the approved product plan: VAD/word timestamps/hardware profiles and
playback editor; reproducible locks/SBOM/licenses; signed Windows installer and
notarized macOS DMG; explicit signed updater; 50 real tasks per OS; final
independent go/no-go.

## Human/external gates still open

- Apple Developer ID and notarization credentials;
- Azure Trusted Signing and offline Ed25519 release key;
- physical Apple Silicon and Windows 10/11 x64 targets;
- trademark/legal/privacy/native redistribution review;
- real clean-machine install, update/rollback and 50-task evidence.

Keep these as `NOT_RUN` or `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` until real
evidence exists. Templates, fake tests and source inspection are not production
PASS.

## Stop conditions

Stop integration on original deletion, `raw_text` mutation, missing committed
managed audio, unreported cleanup residue, archive-budget bypass, live child
after handled cancellation, signature/trust failure, dirty-tree/evidence SHA
mismatch, silent network behavior or fabricated native acceptance.
