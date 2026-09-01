# VOICE Studio Usability Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development`. Workers never create branches and never run `git add`, `git commit`, or `git push`; the controller owns Git.

**Goal:** Add central Dashboard/Studio/Dictionary/History pages, a managed terminology dictionary with Local Whisper hints, dashboard/history analytics, safe editor review tools, and bounded local audio playback before final packaged acceptance.

**Architecture:** Preserve the current transcript, storage, backup, engine, and privacy contracts. Add a lightweight page host around the existing Tk workspace, keep domain behavior in focused modules, pass bounded transcription hints through the existing worker boundary, and keep playback in a bounded controller that reports through the Tk event queue.

**Tech stack:** Python 3.11/3.12, Tkinter/ttk, SQLite, PyAV, sounddevice, pytest.

**Spec:** The user-approved plan in the 2026-09-01 Codex task; the binding defaults and task breakdown are repeated below.

## Global constraints

- Work only on `main`; branch and worktree creation are forbidden.
- Only the controller may commit or push.
- Preserve `raw_text`, user originals, schema version, backup versions, local/private defaults, and current CLI compatibility.
- Add no runtime dependency, telemetry, cloud sync, dictionary profiles, diarization, word timestamps, or split/retime editing.
- New UI and Help text must remain equivalent in Ukrainian, Czech, and English.
- Every production behavior follows RED -> GREEN tests; workers run focused gates and the controller runs the full gate.

---

### Task 1: Navigation and central pages

Introduce a lightweight page host with central `dashboard`, `studio`, `dictionary`, and `history` pages. Dashboard is the startup page. Studio retains the existing transcription/editor/readiness workspace. History becomes a separate central page and opening a history item navigates to Studio. Models, Backup, Settings, and Help remain dialogs. Leaving Studio honors the existing dirty-editor guard. Preserve compact/responsive sidebar behavior and three-language labels.

### Task 2: Managed dictionary

Extend dictionary rules with `use_as_hint=True`, canonical serialization, bounded hint extraction, and a repository for `config_dir()/dictionary.json`. Add atomic JSON/CSV import/export, merge/replace conflict handling, and the central Dictionary editor page. External files remain unchanged and require explicit import to become editable.

### Task 3: Local Whisper hints

Add `TranscriptionHints` as a keyword-only engine input. Send at most 256 unique non-empty target terms and 8192 UTF-8 bytes through the worker request. Faster-whisper maps them to `hotwords`; Ollama and OpenAI ignore them. Do not persist or log terms, and do not reload the cached Whisper model when the dictionary changes.

### Task 4: Dashboard and history filters

Add streaming `LocalStore.statistics()` and pre-limit combined history filters without a migration. Implement KPI cards, 7/30-day activity, weighted RTF, language/engine/model distributions, invalid-record warning, recent items, and refresh after storage mutation. Add history filters for text, date, language, engine/model, status, and retained audio.

### Task 5: Editor tools

Add pure Unicode-aware find/replace, replacement preview, selection-to-dictionary, and preview-only filler cleanup. All saves use the existing editor-state path and one bounded manual undo. Default fillers: uk `ем`, `е-е`, `мм`; cs `ehm`, `hm`; en `um`, `uh`, `erm`, `hmm`.

### Task 6: Confidence review

Add a review panel for segments below a session-only threshold defaulting to 0.60. Sort lowest first; display missing confidence separately. Selecting a row focuses its corrected segment and exposes Play Segment without accuracy claims.

### Task 7: Local audio playback

Add a PyAV/sounddevice playback controller using bounded ~100 ms PCM chunks. Support play/pause, stop, +/-5 seconds, speeds 0.75/1/1.25/1.5/2, and segment start. Missing/deleted/unsafe audio disables playback. Page/transcript switches, restore, and shutdown stop the worker within two seconds.

### Task 8: Documentation and release gates

Align product docs and localized Help; complete full source gates, security scans, deterministic SBOM/packaging regressions, one final Windows packaged build and physical usability smoke. Record macOS and other unavailable native checks as `NOT_RUN` until actually executed.
