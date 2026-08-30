# Model Catalog Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every interrupted or inconsistent faster-whisper model-catalog state recoverable through deterministic offline APIs without deleting user model data implicitly.

**Architecture:** `ModelCatalog.reconcile()` is the single repair primitive. It performs one shallow root scan, quarantines unreadable manifests, adopts only complete orphan directories, drops only provably absent entries, retains ambiguous paths, and cleans only age-bounded temporary residue. CLI and GUI call this primitive at narrowly scoped model-management/startup boundaries; no general history/transcription command scans models.

**Tech Stack:** Python 3.11/3.12, pathlib/os.scandir, JSON, SHA-256 inventory, Tkinter, argparse, pytest/monkeypatch, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.1) and `NEXT_ANTIGRAVITY_TASK.md` (W2-C2 acceptance contract, with the current GUI integration point corrected by this plan).

## Global Constraints

- Work directly on `main`; do not create a branch or worktree.
- The user's original media and model source directories are never deleted.
- A managed or unmanaged model directory is deleted only by `remove(..., confirmed=True)`.
- A corrupt `catalog.json` is quarantined, never deleted.
- Reconciliation is deterministic, offline and idempotent; add no runtime dependency or network call.
- Preserve `CATALOG_VERSION = 1` and all existing public method signatures.
- `offline_only` remains the first behavioral guard in `install()`.
- Do not call `reconcile()` from `install`, `remove`, `get`, `resolve` or diagnostics.
- Do not modify backup recovery, AI-cleanup gates, media containment, packaging, workflows or quality-gate scripts.
- Keep Ukrainian, Czech and English i18n key sets identical.
- Use `.venv\Scripts\python.exe` (Python 3.12), not the unsupported global Python 3.13.
- Resolve the contradiction between W2-C2 acceptance items 2 and 14 in favor of
  the explicit unmanaged-removal contract: before reconciliation a stray model
  directory may be removed only with `confirmed=True`; after reconciliation it
  is a normal managed entry usable through `get`, `verify`, `resolve` and
  `remove`. No test may require both `FileNotFoundError` and successful confirmed
  unmanaged removal for the same pre-reconciliation state.

---

### Task 1: Core reconciliation and manifest recovery

**Files:**
- Modify: `src/voice_studio/model_catalog.py:24-176`
- Test: `tests/test_model_catalog_app.py`

**Interfaces:**
- Consumes: existing `ModelCatalog._load()`, `_inventory(Path)`, `sha256_file(Path)` and manifest entry dictionaries.
- Produces: `ModelCatalog.reconcile() -> dict[str, Any]` with stable keys `status`, `action`, `adopted`, `dropped`, `blocked`, `staging_removed`, `staging_kept`, `residue_removed`, `catalog_quarantined`, plus `error` only on failure.

- [ ] **Step 1: Add failing core reconciliation tests**

Append tests that construct real catalog states. Use `local_model()` and assert the public result, catalog contents and filesystem state:

```python
def test_reconcile_adopts_complete_orphan_and_preserves_provenance(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    orphan = local_model(catalog.root / "tiny-orphan")
    result = catalog.reconcile()
    entry = catalog.get("tiny-orphan")
    assert result["status"] == "PASS"
    assert result["action"] == "repaired"
    assert result["adopted"] == ["tiny-orphan"]
    assert entry is not None
    assert entry["source"] == "reconciled"
    assert entry["revision"] is None
    assert entry["reconciled"] is True
    assert catalog.verify("tiny-orphan")["status"] == "PASS"
    assert catalog.resolve("tiny-orphan") == orphan


def test_reconcile_drops_only_provably_absent_manifest_entry(tmp_path):
    source = local_model(tmp_path / "source")
    catalog = ModelCatalog(tmp_path / "managed")
    catalog.import_local("missing", source)
    shutil.rmtree(catalog.root / "missing")
    result = catalog.reconcile()
    assert result["dropped"] == ["missing"]
    assert catalog.list() == []


def test_reconcile_blocks_incomplete_orphan_without_mutating_it(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    incomplete = catalog.root / "broken"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}", encoding="utf-8")
    result = catalog.reconcile()
    assert result["action"] == "attention"
    assert result["adopted"] == []
    assert result["blocked"][0]["id"] == "broken"
    assert "model.bin" in result["blocked"][0]["reason"]
    assert incomplete.is_dir()


@pytest.mark.parametrize("payload", [b"{not-json", b'{"version":999,"models":[]}'])
def test_reconcile_quarantines_bad_manifest_and_rebuilds(tmp_path, payload):
    catalog = ModelCatalog(tmp_path / "managed")
    local_model(catalog.root / "recoverable")
    catalog.catalog_path.write_bytes(payload)
    result = catalog.reconcile()
    quarantine = Path(result["catalog_quarantined"])
    assert quarantine.read_bytes() == payload
    assert catalog.verify("recoverable")["status"] == "PASS"


def test_reconcile_is_idempotent_and_does_not_rehash_catalogued_models(
    tmp_path, monkeypatch
):
    source = local_model(tmp_path / "source")
    catalog = ModelCatalog(tmp_path / "managed")
    catalog.import_local("stable", source)
    before = catalog.catalog_path.stat().st_mtime_ns
    calls = 0
    original = model_catalog_module.sha256_file
    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)
    monkeypatch.setattr(model_catalog_module, "sha256_file", counted)
    result = catalog.reconcile()
    assert result["action"] == "none"
    assert calls == 0
    assert catalog.catalog_path.stat().st_mtime_ns == before


def test_reconcile_clean_profile_does_not_write_manifest(tmp_path, monkeypatch):
    catalog = ModelCatalog(tmp_path / "managed")
    monkeypatch.setattr(
        catalog,
        "_save",
        lambda _payload: pytest.fail("healthy reconciliation must not write"),
    )
    result = catalog.reconcile()
    assert result == {
        "status": "PASS",
        "action": "none",
        "adopted": [],
        "dropped": [],
        "blocked": [],
        "staging_removed": [],
        "staging_kept": [],
        "residue_removed": [],
        "catalog_quarantined": None,
    }
```

Add `import shutil`, `import os` when used and `from voice_studio import model_catalog as model_catalog_module`.

- [ ] **Step 2: Run the new tests and capture RED**

Run:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py -k reconcile
```

Expected: failures with `AttributeError: 'ModelCatalog' object has no attribute 'reconcile'`.

- [ ] **Step 3: Implement the reconciliation result and safe shallow scan**

Add constants and helpers in `model_catalog.py`:

```python
CATALOG_RESIDUE_MAX_AGE_SECONDS = 300
STAGING_MAX_AGE_SECONDS = 172_800
STAGING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*-[0-9a-f]{32}$")


def _reconcile_result() -> dict[str, Any]:
    return {
        "status": "PASS",
        "action": "none",
        "adopted": [],
        "dropped": [],
        "blocked": [],
        "staging_removed": [],
        "staging_kept": [],
        "residue_removed": [],
        "catalog_quarantined": None,
    }
```

Implement `reconcile()` so it:

1. creates this result;
2. loads the manifest once;
3. on `ValueError`, moves `catalog.json` with `Path.replace()` to
   `catalog.json.corrupt-<UTC compact timestamp>-<uuid8>` and starts an empty v1 manifest;
4. uses `os.scandir(self.root)` exactly once for root discovery;
5. ignores `.downloads`, the manifest and free files;
6. uses `entry.stat(follow_symlinks=False)`/`Path.lstat()` to distinguish a real directory, absence and ambiguous `OSError`;
7. calls `_inventory()` only for valid orphan directory names;
8. creates reconciled entries with `installed_at` from directory mtime,
   `source="reconciled"`, `revision=None`, `reconciled=True`, and a UTC `reconciled_at`;
9. removes manifest entries only when the path is provably absent;
10. saves once only if the manifest changed;
11. sets `action="attention"` when `blocked` is non-empty, otherwise `"repaired"` when any adoption/drop/quarantine occurred;
12. returns `status="FAIL"`, `action="attention"`, and `error` if quarantine itself fails, without scanning or modifying model directories.

Represent every blocked item as `{"id": str, "path": str, "reason": str}` so UI and CLI do not parse exception text.

- [ ] **Step 4: Run core tests to GREEN**

Run:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py -k "reconcile or integrity or import"
```

Expected: all selected tests pass, including the original import/integrity contracts.

- [ ] **Step 5: Commit the core primitive**

```powershell
git add src/voice_studio/model_catalog.py tests/test_model_catalog_app.py
git commit -m "fix(models): reconcile orphaned and corrupt catalog state"
```

---

### Task 2: Safe residue cleanup and explicit unmanaged removal

**Files:**
- Modify: `src/voice_studio/model_catalog.py:89-95,338-355` and the new reconciliation helpers
- Test: `tests/test_model_catalog_app.py`

**Interfaces:**
- Consumes: `ModelCatalog.reconcile()` result schema from Task 1.
- Produces: unique atomic `_save()` temporary files and existing `remove(model_id, *, confirmed=False)` with optional response key `unmanaged=True`.

- [ ] **Step 1: Add failing cleanup, symlink and unmanaged-remove tests**

```python
def test_reconcile_removes_only_stale_well_formed_staging(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    stale = catalog.downloads / ("tiny-" + "a" * 32)
    fresh = catalog.downloads / ("small-" + "b" * 32)
    malformed = catalog.downloads / "keep-me"
    for path in (stale, fresh, malformed):
        path.mkdir()
        (path / "partial").write_bytes(b"x")
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(stale / "partial", (old, old))
    os.utime(stale, (old, old))
    result = catalog.reconcile()
    assert result["staging_removed"] == [stale.name]
    assert set(result["staging_kept"]) == {fresh.name, malformed.name}
    assert not stale.exists()
    assert fresh.exists() and malformed.exists()


def test_reconcile_handles_manifest_residue_by_age(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    catalog._save({"version": 1, "models": []})
    old = catalog.root / "catalog.json.tmp"
    fresh = catalog.root / ("catalog.json." + "a" * 32 + ".tmp")
    old.write_text("old", encoding="utf-8")
    fresh.write_text("fresh", encoding="utf-8")
    timestamp = time.time() - 301
    os.utime(old, (timestamp, timestamp))
    before = catalog.catalog_path.read_bytes()
    result = catalog.reconcile()
    assert result["residue_removed"] == [old.name]
    assert fresh.exists()
    assert catalog.catalog_path.read_bytes() == before


def test_reconcile_blocks_model_symlink(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    target = local_model(tmp_path / "outside")
    link = catalog.root / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    result = catalog.reconcile()
    assert result["blocked"][0]["reason"] == "symlink"
    assert link.is_symlink()


def test_remove_accepts_unmanaged_directory_only_with_confirmation(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    target = local_model(catalog.root / "stray")
    with pytest.raises(ValueError, match="--yes"):
        catalog.remove("stray")
    assert catalog.remove("stray", confirmed=True) == {
        "removed": True, "id": "stray", "unmanaged": True
    }
    assert not target.exists()


def test_atomic_catalog_save_removes_failed_temporary_file(tmp_path, monkeypatch):
    catalog = ModelCatalog(tmp_path / "managed")
    original = Path.replace
    def fail_tmp(path, target):
        if path.name.startswith("catalog.json.") and path.name.endswith(".tmp"):
            raise OSError("replace failed")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", fail_tmp)
    with pytest.raises(OSError, match="replace failed"):
        catalog._save({"version": 1, "models": []})
    assert list(catalog.root.glob("catalog.json*.tmp")) == []
```

- [ ] **Step 2: Run the focused tests and capture RED**

Run:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py -k "staging or residue or symlink or unmanaged or atomic"
```

Expected: assertions fail because residue is not cleaned, unmanaged removal raises `FileNotFoundError`, and `_save` leaves `catalog.json.tmp`.

- [ ] **Step 3: Implement bounded cleanup and atomic save**

Change `_save()` to:

```python
temporary = self.root / f"catalog.json.{uuid.uuid4().hex}.tmp"
try:
    temporary.write_text(..., encoding="utf-8")
    temporary.replace(self.catalog_path)
finally:
    temporary.unlink(missing_ok=True)
```

In `reconcile()`:

- remove only `catalog.json.tmp` and `catalog.json.*.tmp` older than 300 seconds;
- inspect `.downloads` entries without following symlinks;
- compute the newest mtime across a candidate staging tree;
- remove only a real directory whose name matches `STAGING_PATTERN` and whose newest mtime is older than 172,800 seconds;
- keep all other entries in `staging_kept` without changing `action` by itself.

In `remove()` keep the confirmation check first. If no manifest entry exists,
target `self.root / value`, retain the existing containment check, reject files
and symlinks, remove only a real directory, and return `unmanaged=True` without
rewriting an unchanged manifest.

- [ ] **Step 4: Run the complete model-catalog test file**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_model_catalog_app.py
```

Expected: all original and new model-catalog tests pass.

- [ ] **Step 5: Commit residue and removal safety**

```powershell
git add src/voice_studio/model_catalog.py tests/test_model_catalog_app.py
git commit -m "fix(models): clean stale residue and remove unmanaged models safely"
```

---

### Task 3: CLI reconciliation boundary

**Files:**
- Modify: `src/voice_studio/cli.py:134-150,316-341`
- Test: `tests/test_cli_app.py`

**Interfaces:**
- Consumes: `ModelCatalog.reconcile()` result from Tasks 1–2.
- Produces: `voice-studio models reconcile`; automatic model-branch repair notice prefixed `model-catalog:` on stderr only for non-trivial results.

- [ ] **Step 1: Add failing parser and command-boundary tests**

Use disposable config/data/cache directories and real orphan model fixtures:

```python
def test_models_reconcile_outputs_json_and_models_list_reports_repair(
    tmp_path, capsys, monkeypatch
):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    local_model(data / "models" / "orphan")
    assert main(["models", "reconcile"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adopted"] == ["orphan"]


def test_model_catalog_hook_is_limited_to_models_commands(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    local_model(data / "models" / "orphan")
    assert main(["history"]) == 0
    assert "model-catalog:" not in capsys.readouterr().err
    assert main(["models", "list"]) == 0
    assert "model-catalog:" in capsys.readouterr().err
```

If `local_model` is not shared across test modules, add a local `_write_model(path)`
helper containing `model.bin` and `config.json` rather than importing a test helper.

- [ ] **Step 2: Run CLI tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli_app.py -k "model_catalog or models_reconcile"
```

Expected: parser rejects `models reconcile` and `models list` does not repair/report the orphan.

- [ ] **Step 3: Implement one reconciliation call inside the models branch**

Add the parser:

```python
model_commands.add_parser("reconcile", help="repair local model catalog state")
```

Immediately after `catalog = ModelCatalog(store.models)`:

```python
reconciliation = catalog.reconcile()
if reconciliation["status"] != "PASS" or reconciliation["action"] != "none":
    print(
        "model-catalog:" + json.dumps(reconciliation, ensure_ascii=False),
        file=sys.stderr,
    )
if args.models_command == "reconcile":
    _json(reconciliation)
    return 0 if reconciliation["status"] == "PASS" else 2
```

Do not add this call before `if args.command == "models"`. The explicit command
reuses the automatic result; it must not call `reconcile()` twice.

- [ ] **Step 4: Run CLI and model tests to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli_app.py tests/test_model_catalog_app.py
```

- [ ] **Step 5: Commit the CLI boundary**

```powershell
git add src/voice_studio/cli.py tests/test_cli_app.py
git commit -m "feat(cli): reconcile model catalog in model commands"
```

---

### Task 4: GUI startup reporting and three-language messages

**Files:**
- Modify: `src/voice_studio/app.py:193-199,209-240`
- Modify: `src/voice_studio/i18n.py`
- Test: `tests/test_gui_contract_app.py`
- Test: `tests/test_i18n_app.py`

**Interfaces:**
- Consumes: `ModelCatalog.reconcile()` result schema.
- Produces: `VoiceStudioApp._settle_model_catalog() -> dict[str, Any]` and i18n keys `model_catalog_repaired`, `model_catalog_attention`, `model_catalog_rebuilt`, `model_catalog_repair_failed`.

- [ ] **Step 1: Add failing GUI order, outcome and i18n tests**

```python
def test_startup_settles_model_catalog_after_restore_report_before_history():
    source = inspect.getsource(VoiceStudioApp.__init__)
    restore = source.index("self._report_restore_recovery()")
    models = source.index("self._settle_model_catalog()")
    history = source.index("self._refresh_history()")
    assert restore < models < history
    assert "_first_run_model_prompt" not in source


def test_model_catalog_repair_reaches_status_line(monkeypatch):
    recorded = []
    stub = SimpleNamespace(
        store=SimpleNamespace(models=Path("models")),
        status=SimpleNamespace(set=recorded.append),
        _t=lambda key, **values: f"{key}:{values}",
        after=lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module.ModelCatalog,
        "reconcile",
        lambda _self: {
            "status": "PASS", "action": "repaired", "adopted": ["tiny"],
            "dropped": [], "blocked": [], "catalog_quarantined": None,
        },
    )
    result = VoiceStudioApp._settle_model_catalog.__get__(stub)()
    assert result["action"] == "repaired"
    assert recorded and recorded[0].startswith("model_catalog_repaired")


def test_model_catalog_failure_does_not_abort_startup(monkeypatch):
    stub = SimpleNamespace(
        store=SimpleNamespace(models=Path("models")),
        status=SimpleNamespace(set=lambda _value: None),
        _t=lambda key, **values: f"{key}:{values.get('error', '')}",
        after=lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module.ModelCatalog, "reconcile", lambda _self: (_ for _ in ()).throw(OSError("disk"))
    )
    result = VoiceStudioApp._settle_model_catalog.__get__(stub)()
    assert result == {"status": "FAIL", "action": "attention", "error": "disk"}


def test_model_catalog_messages_exist_in_every_locale():
    required = {
        "model_catalog_repaired", "model_catalog_attention",
        "model_catalog_rebuilt", "model_catalog_repair_failed",
    }
    for catalog in _CATALOGS.values():
        assert required <= set(catalog)
```

- [ ] **Step 2: Run GUI/i18n tests and capture RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_gui_contract_app.py tests/test_i18n_app.py -k model_catalog
```

Expected: missing method and missing i18n keys.

- [ ] **Step 3: Implement current-head startup integration**

In `VoiceStudioApp.__init__`, add exactly one call after restore reporting and
before history refresh:

```python
self._report_restore_recovery()
self._settle_model_catalog()
self._refresh_history()
```

Implement `_settle_model_catalog()` to catch every exception, convert it to
`{"status": "FAIL", "action": "attention", "error": str(exc)}`, update the
status line only for repaired/attention/failure outcomes, and schedule a warning
dialog only for FAIL or attention. If `catalog_quarantined` is present, use
`model_catalog_rebuilt`; otherwise use repaired/attention. A healthy
`action="none"` result is silent.

Add grammatically complete Ukrainian, Czech and English messages with the exact
format variables declared by the spec. Do not reintroduce a first-run model
prompt or startup wizard.

- [ ] **Step 4: Run GUI, i18n, CLI and catalog tests to GREEN**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests/test_gui_contract_app.py tests/test_i18n_app.py tests/test_cli_app.py tests/test_model_catalog_app.py
```

- [ ] **Step 5: Commit GUI and localization**

```powershell
git add src/voice_studio/app.py src/voice_studio/i18n.py tests/test_gui_contract_app.py tests/test_i18n_app.py
git commit -m "feat(ui): report model catalog recovery at startup"
```

---

### Task 5: User documentation, status and complete verification

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
- Consumes: final CLI command, GUI messages and reconciliation result semantics.
- Produces: synchronized three-language Help and evidence that reports only commands actually run.

- [ ] **Step 1: Document the user-visible recovery behavior in all locales**

In each `reference.md`, document `voice-studio models reconcile`, its JSON
result and that normal `models` commands reconcile automatically. In each
`troubleshooting.md`, state that an incomplete directory is retained for manual
inspection/removal and a corrupt manifest is quarantined, not deleted. Keep all
three documents semantically aligned.

- [ ] **Step 2: Mark W2-C2 complete using real evidence**

Update `IMPLEMENTATION_STATUS.md` with a W2-C2 row only after focused and full
tests pass. Update `NEXT_ANTIGRAVITY_TASK.md` header to `COMPLETE` and replace
its stale baseline with the actual completion commit/test count; preserve the
acceptance criteria as audit history. Add a dated `VERIFICATION.md` section
listing exact commands and results from the next steps.

- [ ] **Step 3: Run the full source quality gate**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
```

Expected: compile, Ruff, Help validation, all pytest tests and CLI version pass.

- [ ] **Step 4: Build the wheel and check dependencies**

```powershell
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-c2-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: wheel created, `No broken requirements found`, no whitespace errors.

- [ ] **Step 5: Run a clean-profile GUI smoke probe**

Use disposable config/data/cache directories under `build/w2-c2-smoke`, launch
the GUI with the project Python, confirm the process remains alive long enough
to finish startup, then terminate only that verified process. Record this as a
source GUI smoke, not a packaged native acceptance run.

- [ ] **Step 6: Commit documentation and evidence**

```powershell
git add docs/help IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md
git commit -m "docs: record model catalog self-healing verification"
```

- [ ] **Step 7: Controller final review**

The controller independently runs:

```powershell
git status --short --branch
git diff HEAD~5 --check
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
```

It then inspects that only the files listed by this plan changed, no model
weights/private paths entered Git, original five model-catalog tests retain
their expectations, and the branch remains local until the product owner
explicitly authorizes push.
