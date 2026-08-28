# Verification record

Only checks that were actually run are recorded here. A source check does not
stand in for a packaged Windows executable check.

## W2-R1 journaled restore recovery — 2026-08-28

Environment: Linux x86_64, CPython 3.12.3 virtual environment. This is a source
gate on a Linux container, not a Windows or macOS acceptance run.

| Check | Result |
|---|---|
| `PYTHON_BIN=.venv/bin/python scripts/quality_gate.sh` | PASS |
| `python -m compileall -q src tests scripts packaging` | PASS |
| `python -m ruff check src tests scripts packaging` | PASS |
| `python scripts/check_help.py` | PASS, manifest and 5 canonical Markdown topics |
| `python -m pytest -q` | PASS, 347 tests, no skips (baseline before this work: 320) |
| `python -m build --wheel` | PASS, `voice_studio-0.3.0rc1-py3-none-any.whl` |
| `python -m pip check` | PASS |
| `python -m pip_audit` | PASS, no known vulnerabilities |
| new recovery tests fail before the implementation | PASS, 9 failed / 6 passed on the untouched module |
| real GUI start under Xvfb, clean profile, no journal | PASS, startup unchanged, clean exit |
| real GUI start under Xvfb over a killed restore | PASS, see below |

The killed-restore run left no data directory, an orphaned staging directory, an
orphaned `*.recovery-*` directory and a `swap_started` journal. Starting the
real GUI over that state produced `action = "completed"`, one restored record in
history, the archived `auto_copy: true` settings with a
`settings.json.pre-restore-<timestamp>` snapshot kept, the journal and staging
gone, and the `*.recovery-*` directory still on disk.

Not run for this change: Windows and macOS execution, and recovery after a real
power loss or forced termination on a physical machine. The interruption is
simulated in-process.

## Final Windows verification — 2026-08-28

Environment: Windows 11 x64, Python 3.12 virtual environment.

| Check | Result |
|---|---|
| `PYTHON_BIN=.venv/Scripts/python.exe; scripts/quality_gate.ps1` | PASS |
| `python -m compileall -q src tests` | PASS |
| `ruff check src tests scripts` | PASS |
| clean Python 3.12 environment from `requirements-windows.lock` | PASS |
| `python -m pytest -q` | PASS, 316 tests |
| `python -m pip check` | PASS |
| `scripts/check_help.py` | PASS, manifest and 5 canonical Markdown topics |
| clean wheel build from the release staging tree | PASS, 42 files and no retired package namespaces |
| PyInstaller runtime probe | PASS, 8 required runtime imports and frozen spawned-worker roundtrip |
| pre-theme packaged worker with downloaded `faster-whisper tiny`, CPU `int8`, silent WAV | PASS before the UI-only rebuild; actual model load/inference and transcript persistence |
| automated packaged GUI startup smoke | PASS |
| packaged GUI startup from a different working directory | PASS |
| packaged GUI clean exit and second launch | PASS |
| source main-window visual inspection | PASS at 1320 x 820, 1080 x 820, and compact 1000 x 800 |
| final packaged main-window, Settings, and Local AI visual inspection | PASS; reference palette/logo/font treatment applied, no responsive clipping found |
| packaged in-app Help from `C:\Windows\Temp` | PASS; sidebar and `F1`, search/no-results, section links, screenshot assets, close/reopen |
| packaged language persistence | PASS; Czech applied immediately and remained selected after clean exit and second launch |
| packaged Help payload | PASS; canonical topics/assets present in the frozen app and wheel |
| local Ollama discovery | PASS, 2 installed models found |
| local Ollama structured cleanup with `gemma4-code:latest` | PASS |

The packaged Settings dialog displayed Ollama as the default AI cleanup
provider, selected `gemma4-code:latest`, and reported two installed local
models. Ukrainian was displayed as the initial interface language. The same
language selector was exercised with Czech and English in the source UI;
saving a new language refreshed the main interface immediately. The final
packaged executable was then switched to Czech, closed, and launched again
from `C:\Windows\Temp` with an isolated profile; Czech remained active and the
localized Help chrome opened over the canonical Ukrainian manual.

## Windows artifact

- Executable: `dist/0.3.0-test-rc1-windows-x64/VOICE Studio/VOICE Studio.exe`
- Executable size: 12,422,579 bytes
- Executable SHA-256: `2389A60C50904B40A898F062C90AF716F1D0EEB79142DAF09D2BAA92725D29BB`
- Portable archive: `dist/0.3.0-test-rc1-windows-x64/VOICE-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
- Archive size: 106,429,813 bytes
- Archive SHA-256: `C38A406539ACFE4D43B8EBDC4042CF9DD77F14069193EAB0A679864CB42080FD`

## Known verification limits

- The executable and portable archive are unsigned and may trigger Windows
  SmartScreen.
- A clean-machine Windows acceptance run was not available.
- Real-device microphone capture and the global-hotkey matrix were not run.
- The final UI-only rebuild was not rerun through downloaded-model inference;
  the frozen worker probe passed, and the preceding packaged build with the
  same runtime code completed the `faster-whisper tiny` inference path.
- Speech recognition quality on real speech and production WER/CER/RTF
  measurements were not run; the packaged real-model inference used a valid
  synthetic silent WAV to verify the runtime path deterministically.
- The installed `gemma4:12b` Ollama entry fails locally because one of its model
  components is unavailable; `gemma4-code:latest` was confirmed working.
