# NEXT ANTIGRAVITY TASK

TASK_ID: W2-R1-journaled-restore-recovery
BASELINE_COMMIT: 2170d41 (branch `claude/antigravity-post-stage-review-eedzf8`; no tag)

## GOAL

Make an interrupted `restore_backup` recoverable: persist a restore journal and
finish or roll back the interrupted restore automatically on the next start.

## CURRENT STATE

`src/voice_studio/backup.py::restore_backup` verifies the archive, builds a
complete store in a staging directory `<parent>/.<data>.restore-<hex>`, then
swaps: `data_root.replace(recovery)` followed by `temporary.replace(data_root)`.
An in-process failure between those two calls is rolled back by the existing
`except BaseException` handler. A process death between them is not: the next
launch sees no `data_root`, an orphaned `.<data>.restore-<hex>` staging tree and
an orphaned `<data>.recovery-<timestamp>-<hex>` directory, with nothing on disk
describing which of the two holds the user's data.

`IMPLEMENTATION_STATUS.md` lists "journaled restore/startup recovery" under
"Not implemented yet". The GUI restore lifecycle
(`VoiceStudioApp._queue_restore` / `_reload_after_restore`) and the free-space
preflight are already in place and must keep working unchanged.

## SCOPE

1. Write a restore journal file next to `data_root` before the swap begins,
   recording at minimum: journal version, staging path, recovery path,
   `data_root`, timestamp and the manifest record count. Remove it only after
   the swap and the settings/dictionary write have completed.
2. Add a recovery entry point that inspects the journal at startup and
   deterministically either completes the swap (staging tree is intact and its
   record count matches the journal) or rolls back to the recovery directory.
   It must never leave both directories claimed and never delete the surviving
   copy.
3. Call that entry point before the store is opened, in both
   `voice_studio.app.VoiceStudioApp.__init__` and the CLI paths that open
   `LocalStore(data_dir())`, and report the outcome to the user (status line /
   JSON) rather than failing silently.
4. Keep the journal free of secrets: no API keys, no transcript text.

## RELEVANT AREAS

- `src/voice_studio/backup.py`
- `src/voice_studio/storage.py` (audit helper reuse only)
- `src/voice_studio/app.py` (startup call site, status message, i18n keys)
- `src/voice_studio/cli.py` (startup call site for commands that open the store)
- `src/voice_studio/i18n.py` (new keys in all three catalogs)
- `tests/test_backup_app.py`, `tests/test_editor_state_app.py`

## CONSTRAINTS

- Never delete a user original and never delete the recovery directory
  automatically.
- `raw_text` stays immutable; restore must not rewrite transcript text.
- Backup format version stays `BACKUP_VERSION = 1`; do not change the archive
  layout or the `verify_backup` contract.
- The existing free-space preflight, ZIP budget and record-count/audit checks
  stay in force before any swap.
- No new runtime dependency; no cloud or network access on this path.
- All three i18n catalogs must keep identical key sets.

## DO NOT TOUCH

- The Ollama/OpenAI cleanup gating in `cli.py` and `app.py`.
- `docs/help/` content, the theme module, the responsive layout, and the
  PyInstaller spec.
- `src/voice_studio/media.py` containment logic.
- The Windows build scripts and `requirements-windows.lock`.

## ACCEPTANCE CRITERIA

- A test simulates a crash between `data_root.replace(recovery)` and
  `temporary.replace(data_root)` (e.g. by raising inside a patched `replace`
  with the journal already written) and then proves the recovery entry point
  restores a usable `data_root` with the expected record count.
- A test proves the rollback branch: a truncated/missing staging tree recovers
  the pre-restore data from the recovery directory, and the recovery directory
  is preserved on disk.
- A test proves the journal is removed after a successful restore and that
  the recovery entry point is a no-op when no journal exists.
- A test proves the journal file contains no transcript text and no key
  material.
- `verify_backup` / `restore_backup` public signatures and return keys are
  unchanged apart from additive fields.

## VERIFICATION

Run and report actual output:

```
python -m compileall -q src tests scripts packaging
python -m ruff check src tests scripts packaging
PYTHONPATH=src python scripts/check_help.py
PYTHONPATH=src python -m pytest -q
python -m build --wheel
python -m pip check
```

Plus `scripts/quality_gate.ps1` (or `scripts/quality_gate.sh`) on the machine
used, and a source GUI launch confirming startup is unchanged when no journal
is present.

## HANDOFF BACK

CHANGED
- ...

VERIFIED
- ...

KNOWN ISSUES
- ...
