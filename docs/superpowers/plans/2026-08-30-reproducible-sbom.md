# Reproducible CycloneDX SBOM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a deterministic CycloneDX 1.6 SBOM from the pinned Windows release lock and include it in release manifests, staged Test RC directories and checksums without private paths or invented metadata.

**Architecture:** A stdlib-only generator parses exact `name==version` rows into a narrow validated CycloneDX profile. It sorts normalized components, omits timestamps/random identifiers and hashes the canonical dependency set so CRLF/LF and checkout paths do not affect output. Existing manifest and Windows/macOS staging flows consume one explicit `voice-studio-sbom.cdx.json` artifact.

**Tech Stack:** Python 3.11/3.12 stdlib, CycloneDX JSON 1.6, PowerShell, Bash, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` (R0.8)

## Global Constraints

- Components come only from `requirements-windows.lock`; never inspect installed packages or resolve from the network.
- Accept only exact `name==version` rows. Reject options, URLs/VCS/editables, markers, extras, whitespace/control characters, empty versions and duplicate normalized names.
- Scope is the complete pinned Windows x64 release environment, including build/test tools; do not claim every item is embedded in the executable.
- Emit CycloneDX JSON `specVersion` `1.6`, deterministic LF UTF-8 bytes, with no timestamp or random serial number.
- Do not invent licenses, publishers, CPEs, hashes, runtime dependency edges or vulnerability status absent from the lock.
- No absolute path, username, secret, transcript, model binary or local machine inventory may enter the SBOM or Git.
- Release builders fail on SBOM generation/validation failure and remain offline after dependency installation.
- Update `docs/verification/R0_EXECUTION_LOG.md`; do not repeat a green full suite unless later production/package code invalidates it.
- Work on local `main`; no branch/worktree/push and no native/signing overclaim.

---

### Task 1: Deterministic lock parser and generator

**Files:**
- Create: `scripts/generate_sbom.py`
- Create: `tests/test_sbom_app.py`

**Interfaces:**
- Produces: `LockedComponent(name: str, version: str)`.
- Produces: `parse_locked_components(text: str) -> list[LockedComponent]`.
- Produces: `build_sbom(lock_text: str, *, project_name: str, project_version: str) -> dict[str, object]`.
- Produces: `validate_sbom_document(document: object) -> None` for the exact emitted profile.
- Produces CLI: `python scripts/generate_sbom.py --lock PATH --project-name voice-studio --project-version 0.3.0rc1 --output PATH`.

- [ ] **Step 1: Write RED parser, schema-profile and determinism tests**

Test LF versus CRLF equality, sorted normalized names, duplicate rejection, malformed/non-exact rows, no path strings, atomic preservation of an existing output after invalid input, and the repository lock's exact 58 components including `faster-whisper==1.2.1`.

```python
def test_sbom_is_deterministic_across_line_endings(tmp_path):
    lf = "# lock\nAlpha_Pkg==2.0\nzeta.pkg==1.0\n"
    first = build_sbom(lf, project_name="voice-studio", project_version="0.3.0rc1")
    second = build_sbom(
        lf.replace("\n", "\r\n"),
        project_name="voice-studio",
        project_version="0.3.0rc1",
    )
    assert first == second
    assert [item["name"] for item in first["components"]] == ["alpha-pkg", "zeta-pkg"]
    assert str(tmp_path) not in json.dumps(first)
```

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_sbom_app.py
```

Expected: import failure because the generator does not exist.

- [ ] **Step 3: Implement strict parsing**

Use a frozen ordered dataclass. Validate names with `^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$`, normalize `[-_.]+` to lowercase `-`, reject every unsupported requirements syntax, and sort by `(name, version)`.

- [ ] **Step 4: Emit and validate the exact profile**

Emit `$schema=https://cyclonedx.org/schema/bom-1.6.schema.json`, `bomFormat=CycloneDX`, `specVersion=1.6`, integer `version=1`, application metadata for `voice-studio@0.3.0rc1`, and properties `voice-studio:sbom-scope=windows-x64-release-environment`, `voice-studio:source-lock=requirements-windows.lock`, and a SHA-256 of canonical sorted `name==version\n` rows. Components are `type=library` with normalized name/version and identical percent-encoded PyPI `bom-ref`/`purl`.

The validator rejects extra top-level keys, wrong constants/types, unsorted or duplicate components/properties, malformed refs, absolute path-like strings and missing fields. Serialize with sorted keys, two-space indentation and one LF. Write through a unique sibling temporary file and `os.replace()` only after validation; remove owned residue on failure.

- [ ] **Step 5: Run GREEN and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_sbom_app.py
.\.venv\Scripts\python.exe -m ruff check scripts\generate_sbom.py tests\test_sbom_app.py
git diff --check
git add scripts\generate_sbom.py tests\test_sbom_app.py
git commit -m "feat(release): generate deterministic CycloneDX SBOM"
```

---

### Task 2: Release-manifest SBOM contract

**Files:**
- Modify: `scripts/create_release_manifest.py`
- Modify: `tests/test_release_manifest_app.py`
- Test: `tests/test_sbom_app.py`

**Interfaces:**
- Consumes `validate_sbom_document(document)`.
- Changes `create_manifest(..., sbom: Path) -> dict[str, Any]` to require one validated SBOM inside the release directory.
- Produces manifest field `sbom` with format, spec version, relative path, SHA-256 and size.
- Adds required CLI option `--sbom PATH`.

- [ ] **Step 1: Add RED tests**

Create a valid fixture with `build_sbom()`. Assert the manifest contains `{"format": "CycloneDX", "spec_version": "1.6", "path": "voice-studio-sbom.cdx.json", ...}` and no temporary path. Reject an external path, malformed/invalid JSON, wrong project version, a directory and missing file. Preserve the 50-task acceptance gate.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_release_manifest_app.py tests\test_sbom_app.py
```

- [ ] **Step 3: Implement validation and inventory**

Load UTF-8 JSON, validate the profile, require application version `0.3.0rc1`, and reuse `artifact_info()` for relative path/hash/size. Do not silently add arbitrary files to the ordinary artifact list.

- [ ] **Step 4: Run GREEN and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_release_manifest_app.py tests\test_sbom_app.py
.\.venv\Scripts\python.exe -m ruff check scripts\create_release_manifest.py tests\test_release_manifest_app.py
git diff --check
git add scripts\create_release_manifest.py tests\test_release_manifest_app.py tests\test_sbom_app.py
git commit -m "feat(release): bind SBOM into release manifests"
```

---

### Task 3: Windows and macOS staging integration

**Files:**
- Modify: `scripts/build_windows.ps1`
- Modify: `scripts/build_test_rc.sh`
- Modify: `tests/test_packaging_app.py`
- Modify: `tests/test_release_manifest_app.py`

**Interfaces:**
- Consumes the generator CLI and manifest `--sbom`.
- Produces `voice-studio-sbom.cdx.json` in both staged release directories and their `SHA256SUMS.txt`.

- [ ] **Step 1: Add RED build-contract tests**

Assert both scripts invoke the generator with `requirements-windows.lock`, exact project name/version and stage output. Assert Windows checksum targets include the SBOM, macOS passes `--sbom` to the manifest and `shasum` includes it. Assert neither pipeline uses `pip freeze`, installed-package discovery or a network URL.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_packaging_app.py tests\test_release_manifest_app.py -k "sbom or release_manifest"
```

- [ ] **Step 3: Integrate Windows staging**

After creating the stage, invoke the generator into `$StageDirectory\voice-studio-sbom.cdx.json` with the repository lock, exact project name/version and add that path to `$ChecksumTargets`. Preserve atomic final-directory behavior and cleanup.

- [ ] **Step 4: Integrate macOS staging and manifest**

Generate the same filename before manifest creation, pass `--sbom "$SBOM"`, and checksum it. Keep the scope label `windows-x64-release-environment`; do not relabel it as a macOS runtime inventory.

- [ ] **Step 5: Run GREEN and commit**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q tests\test_packaging_app.py tests\test_release_manifest_app.py tests\test_sbom_app.py
.\.venv\Scripts\python.exe -m ruff check scripts tests\test_packaging_app.py tests\test_release_manifest_app.py tests\test_sbom_app.py
git diff --check
git add scripts\build_windows.ps1 scripts\build_test_rc.sh tests\test_packaging_app.py tests\test_release_manifest_app.py
git commit -m "build(release): package and checksum the SBOM"
```

---

### Task 4: Documentation, one full gate and evidence

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `VERIFICATION.md`
- Modify: `NEXT_ANTIGRAVITY_TASK.md`
- Modify: `docs/verification/R0_EXECUTION_LOG.md`

**Interfaces:**
- Consumes final generator/manifest/build contracts.
- Produces W4-B1 evidence without signed/native/production overclaims.

- [ ] **Step 1: Generate and compare canonical artifacts**

Generate `build/w4-b1-sbom/voice-studio-sbom.cdx.json` twice from lock copies in different temporary roots. Assert byte identity, 58 components and no absolute/private paths; record byte size and SHA-256.

- [ ] **Step 2: Run exactly one post-code full gate**

```powershell
$env:PYTHON_BIN=(Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\quality_gate.ps1
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w4-b1-wheel
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Record the exact verified commit in the execution log. Do not rerun after documentation-only edits.

- [ ] **Step 3: Align documentation**

State that the SBOM inventories the pinned Windows release environment, not necessarily frozen-runtime contents, and is not license/vulnerability evidence or a signature. List the filename with Test RC artifacts and leave signing/native/physical gates `NOT_RUN`.

- [ ] **Step 4: Run doc-only checks and commit**

```powershell
.\.venv\Scripts\python.exe scripts\check_help.py
git diff --check
git status --short --branch
git add README.md ARCHITECTURE.md IMPLEMENTATION_STATUS.md VERIFICATION.md NEXT_ANTIGRAVITY_TASK.md docs\verification\R0_EXECUTION_LOG.md
git commit -m "docs: record reproducible SBOM verification"
```

- [ ] **Step 5: Controller checkpoint**

Confirm deterministic bytes, lock-only provenance, relative manifest/build/checksum references, no secrets/private paths/model weights/generated releases in Git, clean worktree and unpushed local `main`. Do not claim Windows/macOS package builds ran unless they actually did.

