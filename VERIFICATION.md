# Verification record

Only checks that were actually run are recorded here. A source check does not
stand in for a packaged Windows executable check.

## W2-C1 restore preserves machine-local state — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12, base code commit
`dba3407`. The restore implementation preserves the current machine's
`models/` and `exports/` trees in restore staging before the first live-root
rename. The archive boundary remains unchanged: those trees are not archive
members. Unsafe links, Windows junctions/reparse points and special entries are
rejected with a concrete `local restore state contains an unsafe path: ...`
error before the live root changes.

| Check | Result |
| --- | --- |
| `$env:PYTHONPATH='src'; .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_backup_app.py -k "local_restore or preserves_local or interrupted_swap or interrupted_local or free_space"` | PASS, 10 passed, 2 skipped; the skips are symlink-creation privilege boundaries (`WinError 1314`), while junction/reparse regressions ran |
| `$env:PYTHON_BIN=(Resolve-Path '.\\.venv\\Scripts\\python.exe').Path; .\\scripts\\quality_gate.ps1` | PASS; compile, Ruff, Help validation (13 Markdown files), 435 tests passed / 3 skipped, and CLI version `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w2-c1-wheel` | PASS; `build\\w2-c1-wheel\\voice_studio-0.3.0rc1-py3-none-any.whl`, 687,353 bytes |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |

The source tests simulate copy interruption and assert that the live root is
untouched; they do not constitute a physical power-loss or forced-termination
acceptance run. Removable-media, antivirus and filesystem-failure races were
not run. This increment also does not add packaged, signed, clean-machine or
native macOS/Windows acceptance evidence.

## W2-C2 model-catalog self-healing — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12. The W2-C2 focused
suite covered adoption, phantom-entry removal, blocked incomplete/reparse paths,
bounded staging and residue cleanup, manifest quarantine, idempotence, CLI
boundaries, GUI startup outcomes and three-language message parity.

| Check | Result |
| --- | --- |
| `$env:PYTHONPATH='src'; .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_model_catalog_app.py tests/test_cli_app.py tests/test_gui_contract_app.py tests/test_i18n_app.py` | PASS, 87 passed, 1 skipped; the skip is directory symlink creation denied (`WinError 1314`) |
| `$env:PYTHON_BIN=(Resolve-Path '.\\.venv\\Scripts\\python.exe').Path; .\\scripts\\quality_gate.ps1` | PASS; compile, Ruff, Help validation (13 Markdown files), 426 tests passed / 1 skipped, and CLI version `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w2-c2-wheel` | PASS; `build\\w2-c2-wheel\\voice_studio-0.3.0rc1-py3-none-any.whl` (684,669 bytes) |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |
| Clean-profile source GUI smoke | PASS; launched `.venv\\Scripts\\python.exe -m voice_studio.app` with disposable `VOICE_STUDIO_CONFIG_DIR`, `VOICE_STUDIO_DATA_DIR` and `VOICE_STUDIO_CACHE_DIR` under `build\\w2-c2-smoke`; verified PID 40332 was the project Python, it remained alive after 5 seconds, then only that exact process was terminated; no stdout/stderr |

The source GUI smoke is not packaged/native acceptance. It did not exercise a
physical microphone, signed executable, clean machine, or Windows/macOS device
matrix. The mandated bare global-Python pytest invocation could not collect the
suite because that interpreter lacks `platformdirs`; the supported repository
`.venv` commands above are the recorded quality evidence.

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

Cross-platform CI on commit `72c42ae` (run 33170283362) is green on all four
jobs — macOS-14 and windows-2022 x CPython 3.11 and 3.12 — for every step:
workflow-pin policy, `compileall`, Ruff, `check_help.py`, `pytest -q`,
`build --wheel`, `pip check` and `pip_audit`.

Not run for this change: recovery after a real power loss or forced termination
on a physical machine. The interruption is simulated in-process, and CI runners
are hosted VMs, not the physical-device acceptance scope.

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
