# VOICE Studio Windows Finalization Plan

> **For agentic workers:** Production changes are owned by the main controller. Review agents are read-only.

**Goal:** Produce and verify an unsigned Windows Test RC executable while preserving local-first behavior, user originals, and immutable `raw_text`.

**Architecture:** Keep the Python/Tkinter, service, storage, and engine boundaries. Apply the PLANNER_G visual language through semantic Tk/ttk tokens and styles; fix only confirmed release-critical lifecycle/archive defects; use the existing PyInstaller pipeline.

**Tech Stack:** Python 3.12, Tkinter/ttk, SQLite, multiprocessing, PyAV/FFmpeg, faster-whisper, pytest, Ruff, PyInstaller, PowerShell.

**Spec:** User-approved final completion prompt in the current Codex task; visual reference is read-only at `Z:\10_CODER\PLANNER_G\`.

## Global Constraints

- Never delete a user original; retention applies only to a managed copy.
- `raw_text` remains immutable; edits use `corrected_text`.
- Local/private is the default; cloud actions remain explicit opt-in.
- Do not copy business logic, proprietary fonts, or unlicensed assets from the design reference.
- Build and smoke-test on Windows x64; do not claim signing or physical-device acceptance without evidence.

## Stages

### 1. Stabilize the Windows baseline and critical data boundaries

- Make worker-timeout tests independent of disposable media-probe startup latency while retaining a real probe integration test.
- Close the backup restore/GUI-close data-root window with an owned non-daemon restore lifecycle and recovery-focused tests.
- Wire the existing generic archive ceilings into backup readers with adversarial regression tests.
- Acceptance: focused RED/GREEN evidence, then compile, full pytest, Ruff, workflow policy, wheel, and `pip check` pass in Python 3.12.

### 2. Apply the design reference without changing product behavior

- Add one semantic theme module for the PLANNER_G palette, Segoe UI/system fonts, spacing, borders, focus/active/disabled states, and control roles.
- Restyle the main shell, history/editor cards, status, notebook, inputs, text areas, and dialogs within existing Tkinter/ttk constraints.
- Add behavior-oriented UI theme/layout tests and verify resize/minimum-window behavior.
- Acceptance: no clipped primary controls at minimum size; keyboard focus remains visible; core commands/events are unchanged.

### 3. First independent review gate

- Run a read-only GPT-5.6 Terra/high review over requirements, current diff, architecture, and test evidence.
- Confirm or reject every actionable finding; fix only confirmed P0/P1/relevant P2 and rerun targeted checks.

### 4. Reproducible Windows packaging

- Validate the current PyInstaller mechanism against current official PyInstaller documentation.
- Ensure app name, icon/assets, frozen multiprocessing, resource paths, clean-profile storage, runtime probe, checksum output, README command, and no-console GUI behavior are correct.
- Acceptance: one documented command creates the final directory and `.exe` without a development server or source-tree runtime dependency.

### 5. Verify the built executable and UI

- Run the final `.exe` twice against disposable profiles; verify startup, main window, settings/history/editor/export/error paths that do not require private data or a downloaded model, and clean exit.
- Inspect main, settings, models/backup dialog, validation/error state, resizing, and DPI with Windows UI screenshots; compare design-system consistency to PLANNER_G.

### 6. Fresh final review and Git finalization

- Run a fresh-context read-only Terra/high review of the final diff, acceptance criteria, test/build evidence, and artifact evidence.
- Resolve confirmed blockers, rerun the complete verification gate, review status/diff, commit, and push normally to `origin/main` if allowed.
