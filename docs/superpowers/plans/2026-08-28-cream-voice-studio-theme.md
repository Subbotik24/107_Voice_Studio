# Cream VOICE Studio Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the existing VOICE Studio desktop interface to match the user-approved cream, orange, and dark-brown reference without changing application behavior.

**Architecture:** Add a small immutable theme contract in the existing Tk application module, then make every ttk style and direct Tk widget consume that contract. Preserve the current 250/88 px responsive shell, settings structure, translations, Ollama integration, and callbacks.

**Tech Stack:** Python 3.11/3.12, Tkinter/ttk, pytest, Ruff, PyInstaller.

**Spec:** `C:\Users\Subbota\AppData\Local\Temp\codex-clipboard-8233709a-c0f4-44b4-bb8c-f43a905168ce.png`, approved by the user on 2026-08-28.

## Global Constraints

- The product name and logo text remain VOICE Studio / VO.
- The sidebar remains 250 px at widths of 1040 px and above and 88 px below 1040 px.
- Interface languages remain Ukrainian, Czech, and English.
- Ollama discovery and selection remain available in Settings.
- No new runtime dependency or redistributed font/logo binary is introduced.
- Use installed Bahnschrift with Segoe UI fallback and installed Cascadia Mono with Consolas fallback.
- Application behavior, persistence, transcription, recording, history, export, backup, and model workflows remain unchanged.

---

### Task 1: Theme contract

**Files:**
- Modify: `src/voice_studio/app.py`
- Test: `tests/test_gui_contract_app.py`

**Interfaces:**
- Produces: `StudioTheme` and `VOICE_STUDIO_THEME` containing the approved colors and font families.
- Consumes: no external resources; values come from the approved screenshot.

- [ ] **Step 1: Write the failing contract test**

```python
def test_voice_studio_theme_matches_approved_cream_reference() -> None:
    assert VOICE_STUDIO_THEME.canvas == "#f6eddc"
    assert VOICE_STUDIO_THEME.surface == "#fffaf1"
    assert VOICE_STUDIO_THEME.accent == "#e99016"
    assert VOICE_STUDIO_THEME.ink == "#2a2119"
    assert VOICE_STUDIO_THEME.primary == "#5b4332"
    assert VOICE_STUDIO_THEME.ui_font == "Bahnschrift"
    assert VOICE_STUDIO_THEME.mono_font == "Cascadia Mono"
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `python -m pytest tests/test_gui_contract_app.py::test_voice_studio_theme_matches_approved_cream_reference -q`

Expected: collection fails because `VOICE_STUDIO_THEME` does not exist.

- [ ] **Step 3: Implement the immutable theme contract**

Add a frozen dataclass with fields for canvas, surface, accent, accent-soft, ink, muted ink, primary, border, disabled, selection, UI font, UI fallback, mono font, and mono fallback; instantiate it once as `VOICE_STUDIO_THEME`.

- [ ] **Step 4: Run the targeted test and confirm GREEN**

Run: `python -m pytest tests/test_gui_contract_app.py::test_voice_studio_theme_matches_approved_cream_reference -q`

Expected: PASS.

### Task 2: Complete Tk/ttk visual application

**Files:**
- Modify: `src/voice_studio/app.py`
- Test: `tests/test_gui_contract_app.py`

**Interfaces:**
- Consumes: `VOICE_STUDIO_THEME` and the existing widget tree.
- Produces: consistent cream main window, sidebar, controls, cards, editors, dialogs, hover/focus/disabled states, and VO mark.

- [ ] **Step 1: Write the failing style-consumption test**

```python
def test_configured_theme_consumes_the_approved_theme_contract() -> None:
    source = inspect.getsource(VoiceStudioApp._configure_theme)
    assert "VOICE_STUDIO_THEME" in source
    assert 'background="#172641"' not in source
    assert 'background="#315eae"' not in source
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `python -m pytest tests/test_gui_contract_app.py::test_configured_theme_consumes_the_approved_theme_contract -q`

Expected: FAIL because the current styles still use the old navy/blue literals.

- [ ] **Step 3: Apply the approved visual language**

Use `VOICE_STUDIO_THEME` in `_configure_theme`, direct `Text`/`Listbox` widgets, logo, navigation, main actions, readiness panel, notebook, inputs, status surfaces, and settings dialogs. Keep existing widget attributes, commands, grid/pack behavior, and responsive sizing.

- [ ] **Step 4: Run GUI contract and localization tests**

Run: `python -m pytest tests/test_gui_contract_app.py tests/test_i18n_app.py -q`

Expected: PASS.

### Task 3: Visual and Windows release verification

**Files:**
- Regenerate: ignored Windows distribution output under `dist/`.

**Interfaces:**
- Consumes: the final source tree and existing Windows packaging script.
- Produces: inspected source UI and a rebuilt Windows executable.

- [ ] **Step 1: Inspect source UI on Windows**

Launch the Tk application and inspect main desktop width, compact width, Settings, and Local AI/Ollama screens. Confirm the cream/orange/brown theme, 250/88 px sidebar behavior, legibility, and no clipping.

- [ ] **Step 2: Run quality checks**

Run: `python -m ruff check src tests scripts` and `python -m pytest -q`.

Expected: PASS with no failures.

- [ ] **Step 3: Build and smoke-test the Windows executable**

Run the existing `scripts/build_windows.ps1` workflow into a new release directory, launch the built `VOICE Studio.exe` outside the source tree, inspect it, close cleanly, and launch it a second time.

- [ ] **Step 4: Independent review and Git finalization**

Request a read-only Terra review of the final diff and evidence; confirm or reject findings, rerun affected checks, commit only release-related changes, and push normally if the remote remains fast-forwardable.
