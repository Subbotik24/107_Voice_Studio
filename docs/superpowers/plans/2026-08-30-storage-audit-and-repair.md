# Storage Audit and Confirmed Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend storage audit to report transcript-row, model-catalog, and export drift, and add one explicit confirmed repair that detaches a transcript from a missing managed source without deleting user data.

**Architecture:** Keep audit strictly read-only and preserve its top-level `status` as the existing SQLite/managed-source health signal used by restore recovery. Add a pure model-catalog inspector and conservative export inventory as nested sections; repair is a separate `BEGIN IMMEDIATE` operation that re-reads one row, proves its managed path is still absent, then updates only `source_path`/`audio_retained`. Existing orphan deletion is hardened by rechecking references inside its write transaction.

**Tech Stack:** Python 3.12, SQLite, pathlib/os/stat, existing model/storage abstractions, argparse CLI, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.4) and `docs/superpowers/plans/2026-08-28-completion-roadmap.md` (W2-C3).

## Global Constraints

- `LocalStore.audit()` and model inspection perform no filesystem or database writes; catalog bytes and mtimes remain unchanged.
- Top-level audit `status` retains its current meaning so backup staging/recovery acceptance does not fail merely because preserved models/exports drift.
- Existing audit keys and `missing: list[str]` remain compatible; new keys are additive.
- Audit never calls `ModelCatalog.reconcile()` and never deletes, quarantines, adopts, hashes, or rewrites model state.
- Export files have no provenance registry: report inventory and conservative canonical stale candidates only; never delete exports in this increment.
- `repair_missing_source()` requires `confirmed=True`, never unlinks a path, never changes `raw_text`, and refuses outside/unsafe/reappeared paths.
- Any deletion in `cleanup_orphans()` remains `confirmed=True`, limited to a real regular file directly under `sources/`, and is rechecked against SQLite under `BEGIN IMMEDIATE` immediately before unlink.
- No backup/journal/archive schema, public backup signature, runtime dependency, cloud behavior, branch/worktree, or push change.

---

### Task 1: Pure model-catalog drift inspection

**Files:**
- Modify: `src/voice_studio/model_catalog.py` before `reconcile`
- Test: `tests/test_model_catalog_app.py`

**Interfaces:**
- Produces: `ModelCatalog.inspect(root: Path) -> dict[str, Any]` as a `@classmethod` that does not instantiate `ModelCatalog` and therefore does not create `root` or `.downloads`.
- Result keys: `status`, `manifest`, `missing`, `orphans`, `blocked`, `staging`, `residue`; `status ∈ {"PASS", "ATTENTION", "FAIL"}`.
- `manifest` is `"absent"`, `"valid"`, or `"invalid"`; invalid JSON/version is `FAIL`, while drift with a readable/absent manifest is `ATTENTION`.

- [ ] **Step 1: Add failing no-write inspection tests**

Cover: absent root; empty root; valid manifest/real directory; missing manifest target; complete orphan; incomplete orphan; root/model symlink or Windows reparse point; invalid manifest; fresh/stale catalog tmp names; fresh/stale staging names. Snapshot every existing file's bytes and `st_mtime_ns` plus directory entry names before/after and assert exact equality.

Expected structured examples:

```python
assert ModelCatalog.inspect(root)["missing"] == [
    {"id": "tiny", "path": str(root / "tiny"), "reason": "catalogued model is absent"}
]
assert ModelCatalog.inspect(root)["orphans"] == [
    {"id": "small", "path": str(root / "small"), "complete": True}
]
```

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py -k inspect
```

Expected: `ModelCatalog.inspect` is missing.

- [ ] **Step 3: Implement a shallow, no-follow inspector**

Use `root.lstat()` and `os.scandir(root)` with `follow_symlinks=False`; reject `FILE_ATTRIBUTE_REPARSE_POINT` exactly as reconciliation does. Read `catalog.json` directly without `_load()` or constructor side effects. Validate version/list/entry IDs and contained relative paths, then lstat each catalogued target. Root-scan only valid model-ID directories; classify completeness by no-follow existence of direct `model.bin` and `config.json` regular files without SHA-256.

Inspect `.downloads` only if it is a real directory: report every direct entry with `{name, pattern_valid, stale, safe}` using `STAGING_PATTERN`, `_newest_mtime`, and the existing 172,800-second threshold, but never remove it. Report catalog tmp candidates with `{name, stale, safe}` and the 300-second threshold. Sort all lists deterministically.

- [ ] **Step 4: Run model tests to GREEN and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py
.\.venv\Scripts\python.exe -m ruff check src/voice_studio/model_catalog.py tests/test_model_catalog_app.py
.\.venv\Scripts\python.exe -m compileall -q src tests
git add src/voice_studio/model_catalog.py tests/test_model_catalog_app.py
git commit -m "feat(models): inspect catalog drift without repair"
```

---

### Task 2: Add structured missing rows and model/export audit sections

**Files:**
- Modify: `src/voice_studio/storage.py:502-543`
- Test: `tests/test_storage_app.py`
- Test: `tests/test_backup_app.py`

**Interfaces:**
- Consumes: `ModelCatalog.inspect(self.models)` through a local import to avoid the existing `model_catalog -> storage.sha256_file` import cycle.
- Adds: `missing_records: list[{"id": str, "path": str}]`.
- Adds: `model_catalog: dict[str, object]` from Task 1.
- Adds: `exports: {"files": list[str], "canonical_stale": list[str], "unmanaged": list[str], "blocked": list[dict[str,str]]}`.
- Preserves: existing top-level keys/status behavior and orphan non-failure behavior.

- [ ] **Step 1: Add failing audit schema/read-only tests**

Create two transcript rows whose managed files are removed and assert deterministic `missing_records` retains both IDs while legacy `missing` retains paths. Add exports named `<existing-id>.txt`, `<absent-uuid>.srt`, `custom-name.md`, a directory, and a symlink/reparse entry. Assert only the absent UUID + supported extension is `canonical_stale`, custom and existing-ID exports are `unmanaged`, non-regular/unsafe entries are `blocked`, and nothing changes on disk.

Seed model drift and assert nested `model_catalog` reports it while the top-level core `status` follows only DB/source health. Extend W2-C1 restore tests to prove a preserved orphan model/export does not make `_restored_store_is_sound()` reject staging.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_storage_app.py tests/test_backup_app.py -k "missing_records or export_drift or model_drift or preserved_local"
```

- [ ] **Step 3: Implement additive audit sections**

While decoding each row, retain transcript IDs and build `transcript_ids`. Append both the legacy missing path and `{id, path}`. Keep the current `passed` expression unchanged.

Scan only direct `self.exports` entries with no-follow stat. Supported canonical suffixes are `{'.txt', '.md', '.json', '.srt', '.vtt'}`. A regular file is a canonical stale candidate only when `path.stem` parses as UUID and is absent from `transcript_ids`; every other regular file is inventory/unmanaged. A directory, symlink, reparse point, or inspection error is `blocked` and never affects top-level status. Use relative names in nested export lists so reports contain no unnecessary absolute profile path.

- [ ] **Step 4: Run storage/backup tests and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_storage_app.py tests/test_model_catalog_app.py tests/test_backup_app.py
.\.venv\Scripts\python.exe -m ruff check src tests
git add src/voice_studio/storage.py tests/test_storage_app.py tests/test_backup_app.py
git commit -m "feat(storage): report model and export drift"
```

---

### Task 3: Confirmed missing-source repair and orphan recheck

**Files:**
- Modify: `src/voice_studio/storage.py` after `audit` and in `cleanup_orphans`
- Test: `tests/test_storage_app.py`

**Interfaces:**
- Produces:

```python
def repair_missing_source(
    self,
    transcript_id: str,
    *,
    confirmed: bool = False,
    expected_path: str | None = None,
) -> dict[str, object]:
```

- Success: `{"repaired": True, "id": id, "path": old_path, "action": "detached_missing_source"}`.
- Mutation: only the stored payload's `source_path=None` and `audio_retained=False`; raw/corrected text, segments, hash, source name, and original external file remain unchanged.

- [ ] **Step 1: Add failing repair tests**

Cover confirmation refusal, unknown ID, row with no source, outside path, symlink/reparse path, expected-path mismatch, path reappears before the transaction, successful missing managed path, and idempotent second attempt. Snapshot immutable transcript fields and an external original. Assert success changes only the two retention fields and never unlinks any path.

For orphan cleanup, monkeypatch `audit()` to return a path that is actually referenced by a row at transaction time; assert confirmed cleanup preserves it. Add file/symlink/reparse/direct-child tests and concurrent DB recheck evidence.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_storage_app.py -k "repair_missing or orphan_recheck"
```

- [ ] **Step 3: Implement transactional detachment**

Require confirmation before DB access. Under one `_connect()` + `BEGIN IMMEDIATE`, select `payload_json`, parse `Transcript`, compare `expected_path` when supplied, and validate the current path. Resolve its missing final path with `strict=False`, require containment under the real `sources` root, require its parent chain to contain no symlink/reparse point, then `lstat()` the final path: only `FileNotFoundError` is repairable; any existing or unknown state is refused.

Serialize the modified transcript and execute `UPDATE transcripts SET payload_json = ? WHERE id = ?`; the denormalized immutable columns do not change. Do not call `save()` because its managed-source existence guard correctly rejects this special recovery state.

Harden `cleanup_orphans`: after the read-only audit candidate list, start `BEGIN IMMEDIATE`, re-read all payloads/references, then for each candidate require direct-child containment, no link/reparse, regular-file lstat, and still-unreferenced identity before unlink. Return only actually removed paths.

- [ ] **Step 4: Run storage/full safety tests and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_storage_app.py tests/test_takeover_regressions.py tests/test_backup_app.py
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m ruff check src tests
git add src/voice_studio/storage.py tests/test_storage_app.py
git commit -m "fix(storage): repair missing managed sources safely"
```

---

### Task 4: CLI audit/repair boundary

**Files:**
- Modify: `src/voice_studio/cli.py:163-170,353-360`
- Test: `tests/test_cli_app.py`

**Interfaces:**
- Produces: `voice-studio storage repair-missing TRANSCRIPT_ID [--expected-path PATH] --yes`.
- Existing `storage audit` JSON gains additive nested fields and keeps its existing success exit behavior.

- [ ] **Step 1: Add failing parser/behavior tests**

Create a stored row, remove its managed file, run `storage audit`, and assert `missing_records`, `model_catalog`, and `exports` JSON. Run repair without `--yes` and assert the existing top-level CLI error boundary gives a concrete confirmation message/nonzero exit. Run with wrong `--expected-path` and assert refusal/no mutation. Run with exact path + `--yes`, assert success JSON and the repaired row fields.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli_app.py -k "storage_audit_drift or repair_missing"
```

- [ ] **Step 3: Add parser and dispatch**

```python
storage_repair = storage_commands.add_parser(
    "repair-missing", help="detach a transcript from a confirmed missing managed source"
)
storage_repair.add_argument("transcript_id")
storage_repair.add_argument("--expected-path")
storage_repair.add_argument("--yes", action="store_true")
```

Dispatch only inside `args.command == 'storage'` and pass all values directly to `repair_missing_source`. Do not run model reconciliation or cleanup from `storage audit`.

- [ ] **Step 4: Run CLI/storage regressions and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli_app.py tests/test_storage_app.py tests/test_model_catalog_app.py tests/test_backup_app.py
git add src/voice_studio/cli.py tests/test_cli_app.py
git commit -m "feat(cli): repair missing storage rows explicitly"
```

---

### Task 5: Help, status, and W2-C3 verification

**Files:**
- Modify: `docs/help/uk/reference.md`
- Modify: `docs/help/cs/reference.md`
- Modify: `docs/help/en/reference.md`
- Modify: `docs/help/uk/troubleshooting.md`
- Modify: `docs/help/cs/troubleshooting.md`
- Modify: `docs/help/en/troubleshooting.md`
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `VERIFICATION.md`
- Modify: `NEXT_ANTIGRAVITY_TASK.md`

**Interfaces:**
- Consumes: final audit/repair JSON and CLI.
- Produces: semantically aligned documentation and W2-C3 completion evidence without marking broader R0/native acceptance complete.

- [ ] **Step 1: Document all three locales**

Explain nested model/export drift, read-only audit, conservative export candidates, explicit `models reconcile`, and `storage repair-missing ID --expected-path PATH --yes`. State repair detaches only the missing reference and never deletes/recreates audio; export candidates are never auto-deleted.

- [ ] **Step 2: Run full gate/artifacts**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-c3-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

- [ ] **Step 3: Record honest W2-C3 evidence and commit**

Update status/verification with actual commands and limitations, preserve W2-C1/C2/S1 history, and commit:

```powershell
git add docs/help IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md
git commit -m "docs: record storage audit verification"
```

- [ ] **Step 4: Controller verification**

```powershell
git status --short --branch
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
git diff c8182aa..HEAD --check
```

Confirm audit does not write, repair never unlinks, backup staging acceptance still uses core status, no private paths/secrets/model weights entered Git, and local `main` remains unpushed.
