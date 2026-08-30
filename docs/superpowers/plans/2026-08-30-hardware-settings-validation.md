# W3-H1 Hardware Settings Validation and Advisory Detection

> **Execution:** Implement this plan in one bounded R0 increment with strict red/green TDD. Use a standard executor model for code and stronger models for specification and final review. Do not push.

## Goal

Make local faster-whisper hardware settings safe and understandable without probing the network, loading a Whisper model, or blocking the Tk event loop. Invalid `device` and `compute_type` values must fail before a worker or model runtime starts. Detection is advisory: failures and timeouts report a concrete fallback to `auto/default` and never prevent application startup.

## Product boundary

- R0 only: validate the supported faster-whisper/CTranslate2 option vocabulary and expose bounded local capability detection.
- Keep `auto/default` as the persisted defaults. Detection must not silently rewrite settings.
- Keep `faster_whisper` and `ctranslate2` imports outside the GUI/CLI parent import boundary.
- No cloud probing, telemetry, benchmark, model load, GPU stress test, or automatic model selection.
- No R1/R2 work such as word timestamps, diarization, or a full segment editor.

## Supported settings contract

- `device`: `auto`, `cpu`, `cuda`.
- `compute_type`: `default`, `auto`, `int8`, `int8_float32`, `int8_float16`, `int8_bfloat16`, `int16`, `float16`, `float32`, `bfloat16`.
- Validation errors must name the invalid field and the allowed alternatives.
- The direct `FasterWhisperEngine` constructor must enforce the same contract before importing or loading faster-whisper.

## Task 1 — Test and implement the validation boundary

**Files:**

- Modify `src/voice_studio/models.py`
- Modify `src/voice_studio/engines/faster_whisper.py`
- Modify `src/voice_studio/cli.py`
- Modify `tests/test_config_app.py`
- Modify/add focused engine and CLI tests as appropriate

**RED:** Add behavior tests proving unsupported values are rejected by `Settings.validate`, direct engine construction rejects them without importing `faster_whisper`, and CLI parsing rejects unsupported overrides before controller construction. Confirm each failure is caused by the missing contract.

**GREEN:** Define reusable immutable option tuples and a validation helper in `models.py`; use them from `Settings`, `FasterWhisperEngine`, and argparse `choices`. Keep error messages concrete.

**Focused verification:** Run only the changed config, engine, CLI, and runtime-import-boundary tests.

## Task 2 — Test and implement bounded advisory detection

**Files:**

- Create `src/voice_studio/hardware.py`
- Create `tests/test_hardware_app.py`
- Update runtime-boundary tests if needed

**RED:** Add tests for CPU-only success, CUDA success, runtime import failure, malformed child response, timeout, child start failure, bounded cleanup, and the safe `auto/default` fallback. The parent process must not import `ctranslate2` or `faster_whisper` merely by importing or calling the public detector.

**GREEN:** Implement a spawn-based, time-bounded probe. The spawned child imports CTranslate2, calls its supported-compute-type and CUDA-device-count APIs, and sends a small validated payload. The parent validates/caps the payload, closes IPC endpoints, and terminates/kills an overdue child within one monotonic deadline. Return an immutable result with status, device capabilities, compute types, fallback, and a user-facing detail string.

**Focused verification:** Run hardware tests and the process/runtime import-boundary tests. Record expected Windows privilege skips, if any, rather than hiding them.

## Task 3 — Test and implement non-blocking GUI integration

**Files:**

- Modify `src/voice_studio/app.py`
- Modify `src/voice_studio/i18n.py`
- Modify `tests/test_gui_contract_app.py`
- Add a functional event-handling test if the existing GUI harness supports it

**RED:** Add tests proving device and compute controls are readonly choices, the Detect action uses the retained GUI worker registry, a second concurrent detection is not started, Tk callbacks do not run the native probe inline, and detection results update only advisory text/available choices without mutating persisted settings.

**GREEN:** Replace the two free-text entries with readonly comboboxes. Add an explicit localized Detect button. Run detection inside `_start_worker("hardware-detection", ...)`, publish one bounded `hardware_detection` event, and render success/degraded text in the settings footer. Clear dialog widget references on close. Do not auto-save or auto-select a detected value.

**Focused verification:** Run GUI contract/lifecycle tests plus hardware and settings tests.

## Task 4 — Documentation, evidence, and one final gate

**Files:**

- Modify `README.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_STATUS.md`, `ROADMAP.md`, `VERIFICATION.md`, and Help content only where the implemented behavior requires it
- Append one checkpoint to `docs/verification/R0_EXECUTION_LOG.md`
- Add a W3-H1 evidence file under `docs/verification/`

**Checks:**

1. Review the complete increment against this plan and repository invariants.
2. Run the mandatory gate once after the final production-code change:

   ```powershell
   .\.venv\Scripts\python.exe -m compileall -q src tests
   $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q
   ```

3. Build the wheel and run `pip check` in the established verification environment if packaging inputs changed or the final reviewer requests it.
4. Record command, commit, scope, result, skips, and rerun trigger in `R0_EXECUTION_LOG.md`; do not repeat the full gate for docs-only edits.
5. Obtain final independent review. Fix every verified Critical/Important finding with a new failing regression test, then rerun only invalidated focused checks and one full gate after the last production-code fix.

## Done when

- Invalid hardware settings cannot reach a worker or Whisper model load.
- GUI choices are constrained and detection never blocks Tk or changes settings silently.
- Probe failure/timeout is non-fatal and explicitly recommends `auto/default`.
- Parent runtime import boundaries remain intact.
- Focused tests and the one final gate pass, evidence/logs are current, and final review has no Critical/Important findings.
