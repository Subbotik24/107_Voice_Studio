# Wide VOICE Studio Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved option 2 visual design to VOICE Studio with the Engineering Workflow sidebar proportions and a wide, usable settings dialog.

**Architecture:** Keep the existing Tkinter application and all working commands. Add one pure responsive-layout contract used by the root window, rebuild only widget composition and styles around existing callbacks, and regroup existing settings controls into wide tabs without changing persistence behavior.

**Tech Stack:** Python 3.11/3.12, Tkinter/ttk, pytest, Ruff, PyInstaller.

**Spec:** User-approved option 2 in the Codex task dated 2026-08-28; visual reference `Z:\10_CODER\PLANNER_G`; sidebar sizing reference `Z:\10_CODER\122_ENGINEERING_WORKFLOW\src\engineering_workflow\web_assets\app.css`.

## Global Constraints

- The application identity is VOICE Studio; unrelated product branding must not appear.
- Interface languages remain Ukrainian, Czech, and English.
- Ollama model discovery and selection remain available in Settings.
- The design reference is read-only and supplies visual language only.
- The sidebar is 250 px at window widths of 1040 px and above, and 88 px below 1040 px.
- Existing transcription, recording, history, export, backup, model, and settings behavior must remain intact.

---

### Task 1: Responsive sidebar contract

**Files:**
- Modify: `src/voice_studio/app.py`
- Test: `tests/test_gui_contract_app.py`

**Interfaces:**
- Produces: `StudioLayout(sidebar_width: int, compact_sidebar: bool)` and `studio_layout_for_width(width: int) -> StudioLayout`.
- Consumes: the current root-window width reported by Tk.

- [ ] **Step 1: Write the failing test**

```python
def test_studio_layout_matches_engineering_workflow_sidebar_breakpoint() -> None:
    assert studio_layout_for_width(1040) == StudioLayout(250, False)
    assert studio_layout_for_width(1039) == StudioLayout(88, True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui_contract_app.py::test_studio_layout_matches_engineering_workflow_sidebar_breakpoint -q`

Expected: FAIL because the layout contract has not been implemented.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class StudioLayout:
    sidebar_width: int
    compact_sidebar: bool


def studio_layout_for_width(width: int) -> StudioLayout:
    return StudioLayout(250, False) if width >= 1040 else StudioLayout(88, True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gui_contract_app.py::test_studio_layout_matches_engineering_workflow_sidebar_breakpoint -q`

Expected: PASS.

### Task 2: Option 2 application shell and settings

**Files:**
- Modify: `src/voice_studio/app.py`
- Modify: `src/voice_studio/i18n.py`
- Test: `tests/test_i18n_app.py`

**Interfaces:**
- Consumes: `studio_layout_for_width`, all existing callback methods, `UI_LANGUAGE_CHOICES`, and current `Settings` fields.
- Produces: a 250/88 px navigation shell, wide editor/history workspace, readiness card, and a 980x700 tabbed settings dialog.

- [ ] **Step 1: Recompose `_build_ui` around a fixed-width sidebar**

Use a root grid with a navy `self.sidebar` in column 0 and the workspace in column 1. Keep the existing widget attributes and callbacks so behavior remains wired.

- [ ] **Step 2: Apply responsive sidebar labels**

Bind root `<Configure>` to `_on_window_configure`; call `studio_layout_for_width`; show full localized labels at 250 px and short glyph labels at 88 px.

- [ ] **Step 3: Apply the approved visual hierarchy**

Use PLANNER_G colors, wide cards, a transcription heading, a primary file action, full-width editor/history sections, and a 260 px readiness card without changing business logic.

- [ ] **Step 4: Regroup Settings into wide tabs**

Create General, Recognition, and Local AI notebook pages. Reuse every current variable, picker, model refresh, key action, validation, save, and close callback.

- [ ] **Step 5: Add all new strings to Ukrainian, Czech, and English catalogs**

Add matching keys for navigation, workspace headings, readiness, privacy boundary, and settings tab names; preserve the exact catalog-key parity test.

- [ ] **Step 6: Run targeted tests**

Run: `python -m pytest tests/test_gui_contract_app.py tests/test_i18n_app.py -q`

Expected: PASS.

### Task 3: Visual, regression, and Windows artifact verification

**Files:**
- Modify: `VERIFICATION.md` only if evidence changes.
- Regenerate: ignored `dist/0.3.0-test-rc1-windows-x64/` release artifacts.

**Interfaces:**
- Consumes: the completed UI and existing Windows packaging scripts.
- Produces: visual evidence for main/settings/compact layouts and a rebuilt executable.

- [ ] **Step 1: Inspect the real Tk interface**

Launch the source application on Windows, inspect the main screen, Settings tabs, and a resize below 1040 px. Confirm 250/88 px behavior, no clipping, and only VOICE Studio branding.

- [ ] **Step 2: Run static and regression checks**

Run: `python -m ruff check src tests scripts` and `python -m pytest -q`.

Expected: both PASS.

- [ ] **Step 3: Build the Windows release**

Run the existing `scripts/build_windows.ps1` workflow using the documented release version and output location.

Expected: `dist/0.3.0-test-rc1-windows-x64/VOICE Studio/VOICE Studio.exe` exists.

- [ ] **Step 4: Smoke-test the packaged executable**

Launch the generated executable from outside the source tree, inspect its main/settings UI, close cleanly, and launch it a second time.

- [ ] **Step 5: Review, commit, and push**

Review the final diff, commit only the layout-related files with `ui: apply wide studio workspace`, and push normally to `origin/main` if the branch remains fast-forwardable.
