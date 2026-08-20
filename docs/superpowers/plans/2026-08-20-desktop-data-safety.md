# Desktop Data Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Execution gate:** `PROPOSED / NOT AUTHORIZED`. The current master-audit phase is
> read-only for product code. Do not execute any step until a later request explicitly
> approves implementation after design review.

**Goal:** Prevent malformed settings startup failures, silent editor data loss, default clipboard disclosure, unbounded microphone memory, silent capture degradation and orphaned microphone temp files.

**Architecture:** Keep the current `HermesVoiceApp`/`Settings` boundaries. Add strict type validation at the settings model, a small pure editor-snapshot module, a bounded producer/consumer WAV recorder, and explicit app-owned transition/temp lifecycle helpers. No database schema, dependency, cloud or model change.

**Tech Stack:** Python 3.11–3.12, dataclasses, Tkinter, `queue`, `threading`, `wave`, NumPy, pytest.

**Finding scope:** `COR-002`, `COR-004`, `PRV-003`, `REL-001`, and the microphone-temp lifecycle portion of `PRV-001`.

## Global Constraints

- Original user media is never deleted.
- `raw_text` remains immutable; only `corrected_text` and formatting are saved.
- New or missing settings use `auto_copy=False`; an explicit existing `true` remains enabled.
- Dirty transitions use Save / Discard / Cancel; save failure or Cancel aborts the transition.
- Recording uses 100 ms blocks, a bounded 64-block queue and an explicit 7,200-second safety ceiling.
- Capture degradation is not processed unless the user explicitly confirms it.
- Microphone temp cleanup is idempotent and covers success, error, cancellation and close.
- No dependency, database schema, telemetry, cloud-upload or packaging change.
- Git commits are attempted only when repository `user.name` and `user.email` exist; otherwise the ledger records `BLOCKED_IDENTITY` and implementation continues without inventing an identity.

---

### Task 1: Strict settings types and private clipboard default

**Files:**
- Modify: `src/hermes_voice_studio/models.py:82-142`
- Modify: `src/hermes_voice_studio/app.py:845-870`
- Modify: `tests/test_config_app.py`

**Interfaces:**
- Consumes: JSON values passed to `Settings.from_dict(data: dict[str, Any])`.
- Produces: `Settings.validate() -> None` raising field-specific `ValueError`; `Settings.auto_copy: bool = False`.

- [ ] **Step 1: Write failing settings tests**

Add parameterized cases to `tests/test_config_app.py`:

```python
@pytest.mark.parametrize(
    ("payload", "field", "expected"),
    [
        ({"model": 1}, "model", "string"),
        ({"task_timeout_seconds": "bad"}, "task_timeout_seconds", "integer"),
        ({"task_timeout_seconds": True}, "task_timeout_seconds", "integer"),
        ({"auto_copy": "yes"}, "auto_copy", "boolean"),
        ({"offline_only": 1}, "offline_only", "boolean"),
    ],
)
def test_settings_reject_wrong_json_types(payload, field, expected):
    with pytest.raises(ValueError, match=rf"{field}.*{expected}"):
        Settings.from_dict(payload)


def test_clipboard_copy_is_private_by_default():
    assert Settings().auto_copy is False
    assert Settings.from_dict({}).auto_copy is False
    assert Settings.from_dict({"auto_copy": True}).auto_copy is True
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_config_app.py -q`

Expected: wrong-type cases raise `TypeError`/`AttributeError` or wrong message, and the default-auto-copy assertion fails.

- [ ] **Step 3: Implement strict validation before semantic checks**

In `Settings`, add exact-type groups and validate them first:

```python
STRING_FIELDS: ClassVar[tuple[str, ...]] = (...)
BOOLEAN_FIELDS: ClassVar[tuple[str, ...]] = (
    "auto_copy", "insert_to_active_app", "offline_only"
)

def _validate_types(self) -> None:
    for name in STRING_FIELDS:
        if not isinstance(getattr(self, name), str):
            raise ValueError(f"settings.{name} must be a string")
    for name in BOOLEAN_FIELDS:
        if type(getattr(self, name)) is not bool:
            raise ValueError(f"settings.{name} must be a boolean")
    if type(self.task_timeout_seconds) is not int:
        raise ValueError("settings.task_timeout_seconds must be an integer")
```

Call `_validate_types()` at the start of `validate()` and set `auto_copy=False`. Preserve the legacy hotkey normalization and ignored unknown keys.

- [ ] **Step 4: Clarify the Settings label**

Change the checkbox copy to state that automatic copying may enter OS clipboard history/sync. Explicit Copy remains unchanged.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_config_app.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit or record identity blocker**

If `git config --get user.name` and `git config --get user.email` both succeed:

```bash
git add src/hermes_voice_studio/models.py src/hermes_voice_studio/app.py tests/test_config_app.py
git commit -m "fix: validate settings types and default clipboard private"
```

Otherwise leave files unstaged and record `BLOCKED_IDENTITY`; do not configure a fabricated identity.

### Task 2: Pure editor snapshot and safe transitions

**Files:**
- Create: `src/hermes_voice_studio/editor_state.py`
- Create: `tests/test_editor_state_app.py`
- Modify: `src/hermes_voice_studio/app.py:478-669,1204-1211`
- Modify: `tests/test_gui_contract_app.py`

**Interfaces:**
- Produces: immutable `EditorSnapshot(text: str, formatting: tuple[tuple[str, tuple[tuple[str, str], ...]], ...])`.
- Produces: `snapshot_editor(text: str, formatting: Mapping[str, Iterable[Sequence[str]]]) -> EditorSnapshot`.
- Produces: `HermesVoiceApp._editor_is_dirty() -> bool`, `_confirm_editor_transition() -> bool`, `_save_edits() -> bool`, `_try_show_result(...) -> bool`.

- [ ] **Step 1: Write RED tests for normalized snapshot equality**

```python
def test_editor_snapshot_detects_text_and_formatting_changes():
    baseline = snapshot_editor("text", {"bold": [("1.0", "1.4")]})
    assert snapshot_editor("text", {"bold": [("1.0", "1.4")]}) == baseline
    assert snapshot_editor("changed", {"bold": [("1.0", "1.4")]}) != baseline
    assert snapshot_editor("text", {"italic": [("1.0", "1.4")]}) != baseline
```

Run: `python -m pytest tests/test_editor_state_app.py -q`

Expected: import/function missing.

- [ ] **Step 2: Implement `EditorSnapshot` and canonical formatting normalization**

Only `bold` and `italic` are retained, ordered deterministically. Invalid entries are ignored in the same way as current formatting application.

- [ ] **Step 3: Verify snapshot tests GREEN**

Run: `python -m pytest tests/test_editor_state_app.py -q`

- [ ] **Step 4: Write RED behavior tests for Save / Discard / Cancel**

Construct `HermesVoiceApp` with `object.__new__`, fake editor/current/store/status, and monkeypatch `messagebox.askyesnocancel/showerror`. Cover:

```python
def test_dirty_transition_save_continues_only_after_success(...): ...
def test_dirty_transition_discard_continues_without_store_write(...): ...
def test_dirty_transition_cancel_aborts(...): ...
def test_dirty_transition_save_error_aborts_and_reports(...): ...
def test_close_cancel_keeps_controller_and_window_open(...): ...
```

Expected RED: transition helpers do not exist and `_save_edits` does not return a success flag.

- [ ] **Step 5: Implement transition helpers**

- `_show_result` captures a new baseline snapshot after loading the editor.
- `_editor_is_dirty` compares current live snapshot to baseline.
- `_save_edits` returns `True` on both writes succeeding; catches exceptions, shows the error and returns `False`.
- `_confirm_editor_transition` returns immediately when clean; otherwise uses `askyesnocancel` with parent app.
- `_try_show_result` calls the guard before `_show_result`.
- `_select_history` restores current selection on Cancel.
- `_close` calls the guard before any cancellation/destruction.
- completed background results use `_try_show_result`; Cancel keeps the old editor and refreshes history so the completed result remains stored.

- [ ] **Step 6: Run editor and GUI tests GREEN**

Run: `python -m pytest tests/test_editor_state_app.py tests/test_gui_contract_app.py -q`

- [ ] **Step 7: Commit or record identity blocker**

Commit message when identity exists: `fix: protect unsaved transcript edits`.

### Task 3: Bounded streaming recorder

**Files:**
- Rewrite focused internals: `src/hermes_voice_studio/recorder.py`
- Create: `tests/test_recorder_app.py`

**Interfaces:**
- Produces constants `BLOCK_FRAMES = 1_600`, `MAX_PENDING_BLOCKS = 64`, `MAX_RECORDING_SECONDS = 7_200`.
- Produces immutable `RecordingResult(path: Path, frames_written: int, dropped_blocks: int, status_messages: tuple[str, ...], limit_reached: bool)` with `degraded: bool` and `warning: str`.
- Changes `AudioRecorder.start(destination: Path) -> None` and `AudioRecorder.stop() -> RecordingResult`; `cancel() -> None` remains idempotent.
- Produces read-only `limit_reached: bool` and `destination: Path | None`.

- [ ] **Step 1: Write a fake sounddevice input stream**

The fake stores the callback and exposes `start/stop/close`; tests inject it through `sys.modules["sounddevice"]`.

- [ ] **Step 2: Write RED streamed-WAV tests**

Cover:

```python
def test_recorder_streams_fixed_blocks_to_wav(...): ...
def test_recorder_reports_sounddevice_status(...): ...
def test_recorder_bounds_queue_and_reports_drops(...): ...
def test_recorder_stops_at_duration_limit(...): ...
def test_recorder_cancel_removes_partial_file(...): ...
def test_recorder_writer_failure_is_raised_and_partial_removed(...): ...
```

Run: `python -m pytest tests/test_recorder_app.py -q`

Expected: current `start()` signature and in-memory implementation fail the contract.

- [ ] **Step 3: Implement producer/consumer WAV writing**

- fixed `blocksize=BLOCK_FRAMES` in `sd.InputStream`;
- callback converts only the accepted slice to bytes and uses `put_nowait`;
- bounded queue is never replaced by an unbounded structure;
- writer thread owns `wave.open`, frame count and exception capture;
- stop closes input stream, enqueues sentinel, joins writer, validates non-empty output and transfers path ownership;
- cancel drains/stops/joins and unlinks the partial path;
- queue overflow, sounddevice status and duration limit populate `RecordingResult`.

- [ ] **Step 4: Run recorder tests GREEN and repeat as a soak**

Run: `python -m pytest tests/test_recorder_app.py -q`

Run: `1..20 | ForEach-Object { python -m pytest tests/test_recorder_app.py -q; if ($LASTEXITCODE) { exit $LASTEXITCODE } }`

Expected: every run passes with no live writer thread after each test.

- [ ] **Step 5: Commit or record identity blocker**

Commit message when identity exists: `fix: stream and bound microphone recording`.

### Task 4: App recording and temp ownership integration

**Files:**
- Modify: `src/hermes_voice_studio/app.py:42-65,269-420,1204-1211`
- Create: `tests/test_recording_lifecycle_app.py`

**Interfaces:**
- Produces `HermesVoiceApp._new_recording_temp() -> Path`.
- Produces `HermesVoiceApp._cleanup_temp(path: str | Path | None) -> None`.
- Maintains `self._pending_microphone_files: set[Path]`.
- Consumes `RecordingResult` from Task 3.

- [ ] **Step 1: Write RED lifecycle tests**

Use `object.__new__(HermesVoiceApp)` plus fake recorder/controller/buttons/status. Test:

```python
def test_record_start_creates_and_tracks_temp_before_capture(...): ...
def test_degraded_recording_requires_confirmation(...): ...
def test_declined_degraded_recording_is_deleted(...): ...
def test_success_error_and_cancel_events_cleanup_pending_temp(...): ...
def test_close_cancels_recorder_then_deletes_all_pending_temp(...): ...
def test_duration_limit_is_polled_and_stopped(...): ...
```

- [ ] **Step 2: Run lifecycle tests RED**

Run: `python -m pytest tests/test_recording_lifecycle_app.py -q`

- [ ] **Step 3: Implement private temp ownership**

- create before `recorder.start` using `NamedTemporaryFile(delete=False)` and best-effort `chmod(0o600)`;
- add to pending set immediately;
- all event branches call `_cleanup_temp` instead of raw `Path.unlink`;
- `_record_stop` consumes `RecordingResult`; degraded audio uses `askyesno(..., default=messagebox.NO)`;
- limit polling triggers forced stop and a specific message;
- `_close` passes dirty guard, cancels recorder/controller, then cleans every pending path before destroy.

- [ ] **Step 4: Run lifecycle/editor/config/recorder tests GREEN**

Run: `python -m pytest tests/test_config_app.py tests/test_editor_state_app.py tests/test_recorder_app.py tests/test_recording_lifecycle_app.py tests/test_gui_contract_app.py -q`

- [ ] **Step 5: Commit or record identity blocker**

Commit message when identity exists: `fix: own microphone temp lifecycle`.

### Task 5: Documentation and complete verification

**Files:**
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `docs/PROGRAM_DESCRIPTION.md`
- Modify: `docs/PROJECT_AUDIT_STATUS.md`
- Modify: `docs/audit/FINDINGS_REGISTER.md`
- Modify: `docs/audit/AUDIT_LEDGER.md`

**Interfaces:**
- Consumes fresh implementation/test evidence.
- Produces finding states that distinguish product closure from manual/native evidence still open.

- [ ] **Step 1: Update user/security documentation**

Document default-off clipboard, dirty prompt, two-hour recording limit, capture warning and temp cleanup. Mark findings `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` until real microphone/OS evidence exists.

- [ ] **Step 2: Run the complete local suite**

```powershell
python -m compileall -q src tests scripts packaging
$env:PYTHONPATH = 'src'
python -m pytest -q
python -m ruff check .
python -m build
python -m pip check
python -m pip_audit
```

Record exact PASS/FAIL/BLOCKED results; do not install or upgrade project dependencies after the verification environment is frozen.

- [ ] **Step 3: Run structural and scope checks**

```powershell
git diff --check
git status --short --branch
git diff --stat
```

Confirm no database schema, dependency, CI, packaging, cloud or Hermes model change.

- [ ] **Step 4: Perform independent code review**

Review changed behavior against this plan and the five canonical finding IDs. Fix only in-scope defects and re-run the full focused + integrated suite.

- [ ] **Step 5: Record physical acceptance gate**

Provide exact manual Windows/macOS microphone instructions for normal capture, continuous capture, overflow/device disconnect, limit behavior, close during capture/transcription and clipboard history. Record `NOT RUN` until real evidence exists.

- [ ] **Step 6: Commit or report commit blocker**

If Git identity is configured, commit verified implementation and docs with `fix: harden desktop data safety`. Otherwise report the exact blocker; do not fabricate a commit or push.
