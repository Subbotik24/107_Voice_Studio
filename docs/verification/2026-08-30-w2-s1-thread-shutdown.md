# W2-S1 Task 4 — app-thread shutdown verification

Date: 2026-08-30
Environment: Windows x64, repository `.venv` CPython 3.12, source tree on
`main`; implementation base `c8182aa` (`fix(ui): synchronize worker alias
cleanup`).

This record verifies the app/recorder/hotkey/maintenance thread half of W2-S1
at source/headless level. Together with the process/queue record in
`2026-08-30-w2-s1-process-shutdown.md`, this is a source/headless PASS for
coordinated shutdown. It does not claim native physical-device, packaged,
clean-machine, or broader R0 acceptance.

## Shutdown contract exercised

- `VoiceStudioApp._close()` is idempotent: a second call returns when
  `_closing` is already set. Before shutdown begins, an active non-daemon
  maintenance worker blocks close with the localized backup warning; it does
  not set the shutdown event or destroy Tk.
- Once close is accepted, `_shutdown_event` is set under the worker lock and
  `_cancel_event` is set. Hotkey stop is bounded by its one-second listener
  join; a stubborn listener is retained for retry and reported as the named
  `global-hotkey` residue.
- Recorder cancellation is attempted before GUI worker joins. A writer timeout
  retains the recorder-owned path and reports it; only tracked, direct-child
  temporary recording files are eligible for cleanup. A replacement or
  identity-ambiguous file is retained and surfaced as residue.
- The transcription/model-download process controller is closed using its
  separate bounded terminate/kill and exactly-once queue-disposal contract;
  see the process/queue record linked above.
- GUI workers are registered by role (`voice-studio-{role}`), and `_join_workers`
  takes one snapshot and one monotonic three-second budget for all daemon
  workers. Any still-live roles are sorted into `_shutdown_residue_threads`
  and shown to the user. Worker events are accepted only before shutdown;
  late events are dropped and the Tk event poller does not schedule another
  callback after shutdown starts.
- Cleanup and residue reporting run in `finally`, and Tk `destroy()` is reached
  once after shutdown work. Python cannot force-kill a blocked third-party
  thread (for example, an OS event tap or recorder backend); such a daemon
  thread is named as residue and the operating system ends it with the process.

## Evidence

| Check | Result |
| --- | --- |
| `python -m compileall -q src tests` | PASS |
| `PYTHONPATH=src .\\.venv\\Scripts\\python.exe -m pytest -q` | PASS; 483 passed, 3 skipped (Windows symlink privilege boundaries) |
| Focused app/recorder/hotkey shutdown regressions (`tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py tests/test_hotkey_app.py -k "worker_registry or shutdown or late_event or close or writer_timeout or stubborn or hotkey"`) | PASS; 22 passed, 54 deselected |
| `.\\scripts\\quality_gate.ps1` with `.venv\\Scripts\\python.exe` | PASS; compileall, Ruff, Help validation, 483 passed / 3 skipped, CLI `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w2-s1-thread-wheel` | PASS; `voice_studio-0.3.0rc1-py3-none-any.whl`, 691,474 bytes, SHA-256 `EFE7754E9F868D14964B5BC9DF17BCC8AFDF03C17AA3FCC570A6BA25ED969517` |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |

## Clean-profile source GUI smoke

The source GUI was launched with `VOICE_STUDIO_CONFIG_DIR`,
`VOICE_STUDIO_DATA_DIR`, and `VOICE_STUDIO_CACHE_DIR` under disposable
`build\\w2-s1-thread-smoke` directories. A controlled Tk callback invoked
`app._close` after startup. Verified PID `16356` exited with code `0` in
`2232 ms`; stdout and stderr were empty. The disposable profile created only
local settings/store directories and no user audio or model data.

This is source-only evidence. It does not cover a frozen executable,
clean-machine install, physical microphone, native macOS event tap,
antivirus/removable-media interference, real power loss or forced
termination, or a 50-task physical-device run. Those acceptance items remain
open. The documented third-party-thread limitation also remains: Python has
no safe general mechanism to kill a blocked thread in-process.
