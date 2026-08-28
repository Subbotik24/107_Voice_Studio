# Localized In-App Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make canonical in-app Help complete and language-matched for Ukrainian, Czech, and English in source, wheel, and frozen Windows builds.

**Architecture:** Upgrade the single Help manifest to a locale-indexed version while preserving the existing Markdown renderer. The application loads the exact selected locale and rebuilds an open Help window after a saved language change; validation and packaging require all locale trees.

**Tech Stack:** Python, JSON, Markdown files, Tk/ttk, pytest, setuptools data files, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-28-voice-local-settings-help-localization-design.md`

## Global Constraints

- `docs/help/` remains the single source of truth.
- Supported Help locales are exactly `uk`, `cs`, and `en`.
- A release cannot pass with missing topics, broken links/assets, or cross-locale links.
- In-app Help must not contain a hardcoded second manual.
- Instructions describe verified current behavior only.

---

### Task 1: Locale-aware manifest and loader

**Files:**
- Modify: `src/voice_studio/help_content.py`
- Modify: `docs/help/help-index.json`
- Modify: `tests/test_help_app.py`

**Interfaces:**
- Produces: `load_help_topics(root, language) -> tuple[HelpTopic, ...]` and locale-parity validation.
- Consumes: `SUPPORTED_UI_LANGUAGES` and existing safe path/link helpers.

- [ ] **Step 1: Replace fixture tests with a failing version-2 manifest contract**

```python
def test_help_catalog_loads_the_requested_locale(tmp_path):
    root = _write_localized_help_tree(tmp_path / "help")
    assert load_help_topics(root, "cs")[0].title == "Rychlý start"
    assert "Vyberte" in load_help_topics(root, "cs")[0].markdown

def test_help_validator_rejects_missing_locale_topic(tmp_path):
    root = _write_localized_help_tree(tmp_path / "help")
    (root / "en" / "workflows.md").unlink()
    assert "en/workflows.md" in "\n".join(validate_help_tree(root))
```

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_help_app.py -q`  
Expected: FAIL because the loader accepts only manifest version 1/Ukrainian.

- [ ] **Step 3: Implement the version-2 manifest**

```json
{
  "version": 2,
  "default_language": "uk",
  "languages": {
    "uk": {"topics": []},
    "cs": {"topics": []},
    "en": {"topics": []}
  }
}
```

Require identical slug order across locales, validate filenames inside the
locale directory, and reject unsupported/missing language codes.

- [ ] **Step 4: Extend link/asset validation across every locale**

Validate each locale's topics and Markdown files independently; reject a local
link whose resolved path escapes that locale except for `assets/<locale>/...`.

- [ ] **Step 5: Run the Help tests and commit**

Run: `python -m pytest tests/test_help_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(help): load locale-specific manuals"`

### Task 2: Complete Ukrainian, Czech, and English manuals

**Files:**
- Move/modify: `docs/help/quick-start.md` -> `docs/help/uk/quick-start.md`
- Move/modify: `docs/help/workflows.md` -> `docs/help/uk/workflows.md`
- Move/modify: `docs/help/reference.md` -> `docs/help/uk/reference.md`
- Move/modify: `docs/help/troubleshooting.md` -> `docs/help/uk/troubleshooting.md`
- Create: `docs/help/cs/quick-start.md`
- Create: `docs/help/cs/workflows.md`
- Create: `docs/help/cs/reference.md`
- Create: `docs/help/cs/troubleshooting.md`
- Create: `docs/help/en/quick-start.md`
- Create: `docs/help/en/workflows.md`
- Create: `docs/help/en/reference.md`
- Create: `docs/help/en/troubleshooting.md`
- Modify: `docs/help/README.md`

**Interfaces:**
- Consumes: verified profile/UI behavior from the Ollama plan.
- Produces: four matching topics per locale with locale-local links.

- [ ] **Step 1: Write/update all four Ukrainian canonical topics**

Cover first launch, Local Ollama quick start, file/microphone transcription,
editing, export, history/search, profiles/settings, local AI cleanup, backup,
Help, and confirmed troubleshooting. State that Ollama subtitles have one
duration segment and Whisper is needed for timed segments.

- [ ] **Step 2: Create Czech articles with the same slugs and task coverage**

Use exact Czech UI labels from `i18n.py`; keep filenames and relative links
parallel to Ukrainian.

- [ ] **Step 3: Create English articles with the same slugs and task coverage**

Use exact English UI labels from `i18n.py`; keep filenames and relative links
parallel to Ukrainian.

- [ ] **Step 4: Run locale parity and link validation**

Run: `python scripts/check_help.py`  
Expected: exit 0 with all 12 articles validated.

- [ ] **Step 5: Commit the manual set**

Commit: `git commit -m "docs(help): add Ukrainian Czech and English manuals"`

### Task 3: Refresh in-app Help after a language change

**Files:**
- Modify: `src/voice_studio/app.py`
- Modify: `tests/test_help_app.py`
- Modify: `tests/test_editor_state_app.py`

**Interfaces:**
- Consumes: `load_help_topics(root, self.settings.ui_language)`.
- Produces: `_close_help_window()` and language-change rebuild behavior.

- [ ] **Step 1: Add failing loader-language and open-window refresh tests**

```python
def test_help_dialog_requests_selected_interface_language(monkeypatch):
    app = object.__new__(VoiceStudioApp)
    app.settings = SimpleNamespace(ui_language="cs")
    # capture load_help_topics call through a lightweight dialog seam
    assert requested_language == "cs"

def test_saved_language_change_closes_existing_help_before_reopen(...):
    assert actions == ["destroy", "refresh-ui"]
```

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_help_app.py tests/test_editor_state_app.py -q`  
Expected: FAIL because `_help_dialog` does not pass a locale or rebuild content.

- [ ] **Step 3: Pass the selected language and add a reusable close seam**

Call `load_help_topics(help_root, self.settings.ui_language)`. Move current
image/window cleanup into `_close_help_window`, use it for Escape/WM close, and
close the Help window before `_refresh_ui_text()` when `ui_language` changes.

- [ ] **Step 4: Run targeted tests and commit**

Run: `python -m pytest tests/test_help_app.py tests/test_editor_state_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(help): follow the selected interface language"`

### Task 4: Package all locales and keep sync automation accurate

**Files:**
- Modify: `pyproject.toml`
- Modify: `packaging/voice_studio.spec`
- Modify: `scripts/build_windows.ps1`
- Modify: `tests/test_packaging_app.py`
- Modify: `.claude/skills/sync-help/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `docs/help/{uk,cs,en}` and `docs/help/assets/{uk,cs,en}`.
- Produces: recursive wheel/frozen Help assets and locale-aware incremental sync guidance.

- [ ] **Step 1: Add failing package-data tests**

Assert the wheel configuration names each locale directory, PyInstaller adds
the complete `docs/help` tree, and the Windows staging copy is recursive.

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_packaging_app.py -q`  
Expected: FAIL because setuptools currently includes only root Markdown/assets.

- [ ] **Step 3: Add explicit locale data-file groups**

Map `docs/help/uk/*.md`, `cs/*.md`, and `en/*.md` to corresponding
`share/voice-studio/help/<locale>` destinations and do the same for locale
assets. Keep the existing whole-tree PyInstaller collection.

- [ ] **Step 4: Update `/sync-help` for three-locale parity**

Require every affected topic to be updated in all three locales and validate
all links/build assets; do not copy the master prompt or reread unrelated code.

- [ ] **Step 5: Run packaging/Help checks and commit**

Run: `python -m pytest tests/test_packaging_app.py tests/test_help_app.py -q; python scripts/check_help.py`  
Expected: all checks pass.  
Commit: `git commit -m "build(help): package all localized manuals"`

### Task 5: Visual and frozen Help verification

**Files:**
- Modify: `VERIFICATION.md`
- Add localized screenshots only if each image contains locale-specific UI: `docs/help/assets/<locale>/*.png`

**Interfaces:**
- Consumes: final source and frozen app.
- Produces: factual locale verification evidence.

- [ ] **Step 1: Verify source Help in all three languages**

Open Settings, select Ukrainian/Czech/English in turn, save, open Help, search a
locale-specific word, follow one internal link, close with Escape, and confirm
no mixed-language content.

- [ ] **Step 2: Verify frozen Help in all three languages**

Repeat the same flow in the final `VOICE Studio.exe`; verify Help assets load
without the source tree.

- [ ] **Step 3: Capture only useful screenshots**

If UI screenshots materially help navigation, capture sanitized main/settings
screens in each locale and reference them with translated alt text. Otherwise
retain text-only instructions rather than duplicating identical imagery.

- [ ] **Step 4: Record evidence and commit**

Update `VERIFICATION.md` with PASS/ISSUES/NOT RUN for each actual check.  
Commit: `git commit -m "docs(help): record localized Help verification"`
