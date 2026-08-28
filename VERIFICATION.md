# Verification record

Only checks that were actually run are recorded here. A source check does not
stand in for a packaged Windows executable check.

## Final Windows verification — 2026-08-28

Environment: Windows 11 x64, locked CPython 3.12.10 environment.

| Check | Result |
| --- | --- |
| `scripts/build_windows.ps1` with `.venv-windows-build` | PASS |
| compile + Ruff + Help validation | PASS; 13 localized Markdown files |
| `pytest -q` in the final build gate | PASS; 362 tests |
| locked dependency import and `pip check` gates | PASS |
| PyInstaller frozen runtime probe | PASS; profile, Ollama engine and Help imports plus spawned-worker roundtrip |
| frozen and wheel payload assertions | PASS; all Ukrainian/Czech/English Help topics present |
| automated packaged GUI clean-profile startup | PASS |
| packaged startup with saved profile | PASS; no setup wizard; `ollama / gemma4:12b` restored |
| final packaged Ollama workflow | PASS; synthetic WAV → exact `This is a local voice transcription test.` → automatic local cleanup → history |
| settings persistence | PASS; `ollama-local`, `ollama`, `gemma4:12b`, recognition `auto`, UI `uk` survived save/relaunch |
| localized packaged Help | PASS; `F1`, canonical content, close/reopen; Ukrainian and Czech UI/Help switch visually exercised |
| final UI visual inspection | PASS; 1066 × 852 main screen, Settings profiles/Recognition/Local AI, Help, no clipping or startup popup |
| local Ollama discovery | PASS; 2 installed models found; selected `gemma4:12b` reports audio capability |

The first packaged test intentionally exposed a language mismatch: recognition
was forced to `uk` while the sample was English, and Ollama returned no content.
The program rejected the empty result. After saving recognition language
`auto`, the same packaged EXE completed the workflow. Troubleshooting and the
runtime error now direct the user to check the recognition language or choose
Local Whisper; the application never switches engines silently.

## Windows artifact

- Executable: `dist/0.3.0-test-rc1-windows-x64/VOICE Studio/VOICE Studio.exe`
- Executable size: 12,438,507 bytes
- Executable SHA-256: `E56F1C0BFDE5D14A97FDE1518D5B7214C7603F1412681F84AC2D68ADE1676E1C`
- Portable archive: `dist/0.3.0-test-rc1-windows-x64/VOICE-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
- Archive size: 104,059,503 bytes
- Archive SHA-256: `71CBBE38AA075F43F658DD195DD3B5F5F3DB512B16AA35B97F2D8624A9AA3699`

## Known verification limits

- The artifact is unsigned and was not run on a separate clean Windows machine.
- Real microphone capture, the global-hotkey matrix and a 50-task physical run
  were not performed.
- The installed `gemma4:12b` passed the English audio workflow but did not
  transcribe the synthetic Cyrillic sample; model/language coverage is not a
  VOICE Studio accuracy claim.
- Production WER/CER, long-recording and high-DPI multi-monitor matrices were
  not run.
