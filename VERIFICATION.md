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
| `python -m pytest -q` | PASS, 297 tests |
| `python -m pip check` | PASS |
| clean wheel build from the release staging tree | PASS, 42 files and no retired package namespaces |
| PyInstaller runtime probe | PASS, 8 required runtime imports and frozen spawned-worker roundtrip |
| packaged spawned worker with downloaded `faster-whisper tiny`, CPU `int8`, silent WAV | PASS, actual model load/inference and transcript persistence |
| automated packaged GUI startup smoke | PASS |
| packaged GUI startup from a different working directory | PASS |
| packaged GUI clean exit and second launch | PASS |
| packaged main-window and Settings visual inspection | PASS at 1082 x 752 and 860 x 666 |
| local Ollama discovery | PASS, 2 installed models found |
| local Ollama structured cleanup with `gemma4-code:latest` | PASS |

The packaged Settings dialog displayed Ollama as the default AI cleanup
provider, selected `gemma4-code:latest`, and reported two installed local
models. Ukrainian was displayed as the active interface language. The same
language selector was also exercised with Czech and English in the source UI;
saving a new language refreshed the main interface immediately.

## Windows artifact

- Executable: `dist/0.3.0-test-rc1-windows-x64/VOICE Studio/VOICE Studio.exe`
- Executable size: 12,396,636 bytes
- Executable SHA-256: `DF028D642F312B270E20212BEA7CEE461B225EC2DDEA9D1B6CC2B8B4BE0E7541`
- Portable archive: `dist/0.3.0-test-rc1-windows-x64/VOICE-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
- Archive size: 105,846,626 bytes
- Archive SHA-256: `094B54DAB709F3E7BBF986B2E07F5CD4B672D6B2E26F2FC4635515DF66D9A3E9`

## Known verification limits

- The executable and portable archive are unsigned and may trigger Windows
  SmartScreen.
- A clean-machine Windows acceptance run was not available.
- Real-device microphone capture and the global-hotkey matrix were not run.
- Speech recognition quality on real speech and production WER/CER/RTF
  measurements were not run; the packaged real-model inference used a valid
  synthetic silent WAV to verify the runtime path deterministically.
- The installed `gemma4:12b` Ollama entry fails locally because one of its model
  components is unavailable; `gemma4-code:latest` was confirmed working.
