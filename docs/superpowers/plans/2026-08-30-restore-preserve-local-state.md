# Restore-Preserved Local State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backup restore preserve the machine-local `models/` and `exports/` trees across normal restore and every existing journal-recovery path.

**Architecture:** Keep models and exports outside the backup format. Before the first root swap, journal the operation, validate and copy the two local trees into the already-audited staging store without following links or Windows reparse points, and include their byte count in the free-space preflight. The current root remains untouched until the copy is complete, so existing journal recovery can discard partial staging or promote complete staging without a new journal schema.

**Tech Stack:** Python 3.12, pathlib/os/stat/shutil, existing ZIP restore journal, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.2) and `docs/superpowers/plans/2026-08-28-completion-roadmap.md` (W2-C1).

## Global Constraints

- The program remains local/private by default; no upload or telemetry.
- The user original is never deleted.
- `models/` and `exports/` remain excluded from `.voice-backup` archives.
- `verify_backup(path)` and `restore_backup(path, data_root, *, settings_target)` signatures do not change.
- Journal version and exact field set do not change; no transcript text, settings payload, dictionary content, key material, or secrets enter the journal.
- A `*.recovery-*` tree is never recursively deleted; rollback may rename it back to `data_root` as the existing contract already permits.
- Every observed symlink, Windows junction/reparse point, and special file is rejected;
  ordinary concurrent tree changes are detected and abort before swap. A malicious
  same-account actor replacing a path between filesystem syscalls is outside this
  local/private product's threat boundary because that actor can already modify the
  user's private data directly; the implementation must not claim kernel-handle-level
  atomicity against that actor.
- Every code change is test-first and each task ends with compile, Ruff, focused pytest, and a commit; no push.

---

### Task 1: No-follow local-state inventory and copy primitives

**Files:**
- Modify: `src/voice_studio/backup.py` near the restore helpers before `restore_backup`
- Test: `tests/test_backup_app.py`

**Interfaces:**
- Produces: `_local_restore_bytes(data_root: Path) -> int`
- Produces: `_copy_local_restore_state(data_root: Path, staging: Path) -> list[str]`
- Raises: `ValueError("local restore state contains an unsafe path: ...")` for symlinks, Windows reparse points, and non-file/non-directory entries.
- Copies only top-level names `models` and `exports`; a missing source tree contributes zero bytes and is skipped.

- [ ] **Step 1: Add failing primitive tests**

Add tests that build nested model/export files, call the two helpers, and assert the exact byte total, exact copied bytes, and returned names `['exports', 'models']` in sorted order. Add a regular-file-at-top-level case and assert `ValueError`; neither `data_root` nor the external/unsafe object changes.

On Windows, create a junction with `cmd /c mklink /J` through `subprocess.run(..., check=True)` and skip only when the command is unavailable or denied. Assert both helpers reject it and an external sentinel remains unchanged. Keep the existing symlink test pattern for platforms where symlink creation is available.

- [ ] **Step 2: Run the focused tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_backup_app.py -k "local_restore_state or local_restore_bytes"
```

Expected: collection/import fails because the helpers do not exist.

- [ ] **Step 3: Implement no-follow inspection and copying**

Add one private predicate based on `os.lstat()` / `DirEntry.stat(follow_symlinks=False)`:

```python
def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)
```

Implement a recursive scanner that uses `os.scandir`, rejects `_is_reparse_point(info)`, accepts only `stat.S_ISDIR` and `stat.S_ISREG`, never calls `resolve()` on an entry, and sums only regular-file `st_size`. Implement the copy using the same validation on every level, `Path.mkdir(exist_ok=True)`, `shutil.copy2(..., follow_symlinks=False)` for files, and `shutil.copystat(..., follow_symlinks=False)` after children. Re-check the source with `os.lstat()` at recursive entry so a directory junction is rejected before descent.

`_copy_local_restore_state` iterates `('exports', 'models')`, requires an existing top-level source to be a real directory, copies into the corresponding empty staging directory, and returns the copied names. Do not delete or move the source.

- [ ] **Step 4: Run focused and backup tests to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_backup_app.py
.\.venv\Scripts\python.exe -m ruff check src\voice_studio\backup.py tests\test_backup_app.py
.\.venv\Scripts\python.exe -m compileall -q src tests
```

- [ ] **Step 5: Commit the safe primitives**

```powershell
git add src/voice_studio/backup.py tests/test_backup_app.py
git commit -m "feat(backup): copy local restore state safely"
```

---

### Task 2: Preserve models and exports inside the journaled restore

**Files:**
- Modify: `src/voice_studio/backup.py:521-651`
- Test: `tests/test_backup_app.py`

**Interfaces:**
- Consumes: `_local_restore_bytes(data_root)` and `_copy_local_restore_state(data_root, temporary)` from Task 1.
- Preserves: existing restore result schema, journal schema/version, public signatures, settings sidecar behavior, and recovery action names.

- [ ] **Step 1: Add failing normal-restore and archive-boundary tests**

Extend the main restore fixture with:

```python
(data / "models" / "tiny").mkdir(parents=True)
(data / "models" / "tiny" / "model.bin").write_bytes(b"model")
(data / "models" / "catalog.json").write_text('{"version":1,"models":[]}', encoding="utf-8")
(data / "exports" / "kept.txt").write_bytes(b"export")
```

After `restore_backup`, assert those exact bytes still exist while the previous transcript is replaced. Open the backup and assert no member begins with `models/` or `exports/`.

Add a free-space test asserting the required byte count equals `verified['expanded_bytes'] + _local_restore_bytes(data)` with the existing margin unchanged.

- [ ] **Step 2: Add failing interruption tests**

Add local-state fixtures to `test_interrupted_swap_is_completed_from_the_journal` and assert that journal promotion contains both trees while the displaced `*.recovery-*` directory remains.

Add `test_interrupted_local_state_copy_keeps_live_root`: monkeypatch the copy primitive to copy `exports` then raise `KeyboardInterrupt`, and monkeypatch only staging cleanup to simulate process death. Assert the live `data_root`, model sentinel, export sentinel, journal, and partial staging remain. Undo patches, call `recover_interrupted_restore(data)`, and assert action `staging_discarded`, both live sentinels unchanged, and no recovery directory was created.

- [ ] **Step 3: Run the new tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_backup_app.py -k "preserves_local or interrupted_local or interrupted_swap or free_space"
```

Expected: normal restore loses the local trees, interruption staging lacks them, and free-space accounting excludes them.

- [ ] **Step 4: Integrate preservation before the first rename**

Before `require_free_space`, compute `local_bytes = _local_restore_bytes(data_root)` and request `verified['expanded_bytes'] + local_bytes` with the existing margin.

Keep staging creation, backup extraction, audit, path rewrites, settings validation, recovery-path selection, and journal construction unchanged. Immediately after `_write_json_atomic(journal_path, journal)` and before `data_root.replace(recovery)`, call:

```python
_copy_local_restore_state(data_root, temporary)
```

Do not catch `BaseException` around the copy. A normal error or process death leaves the live root untouched and the existing `swap_started` recovery branch can safely discard partial staging. Do not add journal fields or a new stage.

- [ ] **Step 5: Run backup and integration suites to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_backup_app.py tests/test_model_catalog_app.py tests/test_cli_app.py tests/test_gui_contract_app.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts packaging
.\.venv\Scripts\python.exe -m compileall -q src tests scripts packaging
```

Confirm unchanged signature and journal-secret tests pass. Confirm every test that exercises deletion still asserts the original source or recovery tree survives.

- [ ] **Step 6: Commit restore integration**

```powershell
git add src/voice_studio/backup.py tests/test_backup_app.py
git commit -m "fix(backup): preserve models and exports on restore"
```

---

### Task 3: Help, status, and W2-C1 verification evidence

**Files:**
- Modify: `docs/help/uk/reference.md`
- Modify: `docs/help/cs/reference.md`
- Modify: `docs/help/en/reference.md`
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `VERIFICATION.md`
- Modify: `NEXT_ANTIGRAVITY_TASK.md`

**Interfaces:**
- Consumes: final restore behavior and evidence from Tasks 1-2.
- Produces: semantically aligned Help and an honest W2-C1 completion record; it does not mark broader R0 complete.

- [ ] **Step 1: Document restore-local behavior in all locales**

In each reference document, state that restore replaces backed-up transcripts/settings/sources but preserves the current machine's `models/` and `exports/`; these trees remain outside the archive. State that unsafe links/reparse points abort restore before the live root is changed and produce a concrete error.

- [ ] **Step 2: Record W2-C1 status without overstating native acceptance**

Add a dated W2-C1 row/evidence section to `IMPLEMENTATION_STATUS.md` and `VERIFICATION.md`. Update `NEXT_ANTIGRAVITY_TASK.md` to a completed W2-C1 audit record with actual commit/test counts while retaining its acceptance criteria. Do not claim physical power-loss, removable-media, antivirus, packaged, signed, or clean-machine verification unless actually run.

- [ ] **Step 3: Run the complete source gate and artifact checks**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-c1-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

- [ ] **Step 4: Commit documentation and evidence**

```powershell
git add docs/help IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md
git commit -m "docs: record restore local-state verification"
```

- [ ] **Step 5: Controller verification**

The controller independently runs:

```powershell
git status --short --branch
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
git diff e379b42..HEAD --check
```

It also verifies that archive fixtures contain no `models/` or `exports/`, no user-specific absolute paths or model weights entered Git, and local `main` remains unpushed.
