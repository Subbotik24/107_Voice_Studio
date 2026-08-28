# Verification record

Only checks that were actually run are recorded here. A source check does not
stand in for a packaged Windows executable check.

## Final Windows verification — 2026-08-28

Environment: Windows 11 x64, Python 3.12 virtual environment.

| Check | Result |
|---|---|
| `PYTHON_BIN=.venv/Scripts/python.exe; scripts/quality_gate.ps1` | PASS |
| `python -m compileall -q src tests` | PASS |
| `ruff check src tests scripts` | PASS |
| clean Python 3.12 environment from `requirements-windows.lock` | PASS |
| `python -m pytest -q` | PASS, 304 tests |
| `python -m pip check` | PASS |
| clean wheel build from the release staging tree | PASS, 42 files and no retired package namespaces |
| PyInstaller runtime probe | PASS, 8 required runtime imports and frozen spawned-worker roundtrip |
| pre-theme packaged worker with downloaded `faster-whisper tiny`, CPU `int8`, silent WAV | PASS before the UI-only rebuild; actual model load/inference and transcript persistence |
| automated packaged GUI startup smoke | PASS |
| packaged GUI startup from a different working directory | PASS |
| packaged GUI clean exit and second launch | PASS |
| source main-window visual inspection | PASS at 1320 x 820, 1080 x 820, and compact 1000 x 800 |
| final packaged main-window, Settings, and Local AI visual inspection | PASS; reference palette/logo/font treatment applied, no responsive clipping found |
| local Ollama discovery | PASS, 2 installed models found |
| local Ollama structured cleanup with `gemma4-code:latest` | PASS |

The packaged Settings dialog displayed Ollama as the default AI cleanup
provider, selected `gemma4-code:latest`, and reported two installed local
models. Ukrainian was displayed as the active interface language. The same
language selector was also exercised with Czech and English in the source UI;
saving a new language refreshed the main interface immediately.

## Windows artifact

- Executable: `dist/0.3.0-test-rc1-windows-x64/VOICE Studio/VOICE Studio.exe`
- Executable size: 12,407,501 bytes
- Executable SHA-256: `C2883E709CAD8855B60496DF70F7BF5416520B6E3515F8A51E815FE7F06956BC`
- Portable archive: `dist/0.3.0-test-rc1-windows-x64/VOICE-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
- Archive size: 105,856,232 bytes
- Archive SHA-256: `ABAE70C3557023108A4FD3506EE2DE8D223CA824A2373AEDC48020E19E8FB3B7`

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
