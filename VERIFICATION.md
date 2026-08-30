# Verification record

Only checks that were actually run are recorded here. A source check does not
stand in for a packaged Windows executable check.

## W4-B1 reproducible SBOM — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12, local `main`.
The final-fix production-code commit verified before documentation edits was
`3e85aaf1ab6e8505925ef12a5a822181d6b0a4df`.

The generator was run twice against copies of `requirements-windows.lock` in
different temporary roots. The resulting bytes were compared directly, the
JSON was checked for exactly 58 components and no absolute/private path
material, and the first output was retained as
`build/w4-b1-sbom/voice-studio-sbom.cdx.json`.

| Check | Result |
| --- | --- |
| Canonical artifact comparison | PASS; byte-identical outputs, 58 components, path scan PASS |
| SBOM artifact | `build/w4-b1-sbom/voice-studio-sbom.cdx.json`, 11,159 bytes, SHA-256 `0e1d420fadbdcc4c78e8130c00b5f217c3a6374853f045d3c4cd73d22f300377` |
| `$env:PYTHON_BIN=(Resolve-Path '.\\.venv\\Scripts\\python.exe').Path; .\\scripts\\quality_gate.ps1` | PASS; compileall, Ruff, Help validation, 583 passed / 9 skipped, CLI `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w4-b1-final-fix-wheel` | PASS; `voice_studio-0.3.0rc1-py3-none-any.whl`, 701,107 bytes, SHA-256 `dadf53304aca7a31d297cc31c031f62b37cb53cf3e85d02b43e8d1c072a31a7a` |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` before documentation edits | PASS; no whitespace errors |

The final fix added regression coverage for bare-CR lock input, deterministic
in-place content mutation after descriptor close, release-root and nested
ancestor junction swaps that retained final-file identity, destination
creation after the publication precheck, exact staged `--output` binding and
Windows no-nesting publication. The focused release/SBOM/packaging set passed
60 tests with one final-symlink privilege skip. Windows junction and native
promotion coverage executed. Bash syntax, PowerShell AST parsing, focused
Ruff, compileall and diff checks passed before the code commit.

The full quality gate above was run exactly once after the final-fix production
code was complete and was not rerun after these documentation-only edits. The
earlier W4-B1 gate is superseded by this post-fix result. No physical Windows
or macOS Test RC build, clean-machine install, signing/notarization, or
physical-device acceptance was run for W4-B1. The macOS descriptor boundary
and `renameatx_np(RENAME_EXCL)` path received source/syntax verification, not a
native execution claim. The SBOM is a lock-only inventory of the pinned Windows
x64 release environment, not necessarily frozen-runtime contents. It does not
establish license coverage, vulnerability status, or a publisher signature.
The Test RC artifacts remain unsigned and native/physical gates are `NOT_RUN`.

The filename associated with the Test RC release artifacts is
`voice-studio-sbom.cdx.json`; release manifests use a relative path and record
the artifact size and SHA-256. No secrets, private paths, model weights or
generated release outputs were added to Git.

## W2-C3 storage audit and confirmed repair — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12, source tree on local
`main`, final production implementation `b7ba7fd`. This record closes W2-C3 at
source/headless level only. It does not claim packaged, physical-device,
clean-machine, signed-release or broader R0 acceptance.

The CLI `storage audit` route calls `LocalStore.audit_existing()` before restore
settlement or `LocalStore` construction, so it does not initialize, recover or
otherwise write the live store. It validates the existing root and SQLite
entries without following reparse points, fingerprints the database plus WAL
and SHM state, and copies the database and present WAL into an isolated
temporary directory outside the live tree. It retries a changing snapshot at
most three times, audits the stable temporary copy, rechecks the live root
identity after the scan and removes the temporary directory afterwards.

Regression tests verify database/WAL churn, live-root replacement and unsafe
temporary-parent refusal without live-tree mutation. The audit itself never
calls model reconciliation; model commands and GUI startup retain their
automatic reconciliation boundaries, while `models reconcile` remains the
direct explicit action. The existing top-level `status` still represents only
SQLite and managed-source health, so preserved model/export drift does not
reject backup restore staging. Nested `model_catalog` and `exports` sections
report drift independently; `canonical_stale` exports are conservative
inventory candidates and are never auto-deleted.

`storage repair-missing` requires `--yes`, optionally pins the audited path with
`--expected-path`, re-reads the row under `BEGIN IMMEDIATE`, and refuses outside,
reparse, reappeared or otherwise unsafe paths. Success changes only
`source_path=None` and `audio_retained=False`; regression tests preserve
`raw_text`, corrected text, segments, hash, source name and an external user
original. The repair path contains no unlink or audio recreation. Existing
confirmed orphan cleanup also rechecks database references inside its write
transaction before removing a direct regular managed child.

| Check | Result |
| --- | --- |
| Full source gate at `b7ba7fd` (`pytest`, compileall, Ruff, Help validation, diff check) | PASS; 533 passed / 8 skipped; CLI `0.3.0rc1`; final independent production review CLEAN |
| Related storage/model/backup/CLI tests at `b7ba7fd` | PASS; 150 passed / 8 skipped |
| `.\.venv\Scripts\python.exe scripts\check_help.py` after documentation correction | PASS; 13 Markdown files |
| `.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir build\w2-c3-wheel` | PASS after final production and Help corrections; `voice_studio-0.3.0rc1-py3-none-any.whl`, 700,821 bytes, SHA-256 `33441D2FB1932501241DA09E205C566EB6BD7DB392666ECAD29AFD0FEADBFCC8` |
| `.\.venv\Scripts\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |

The 533-test full result and 150-test related result are the final controller
verification on production HEAD `b7ba7fd`. They were not rerun after this
documentation-only correction; Help validation, wheel rebuild, `pip check` and
the diff check above were run after the documentation changed.

All eight skips are Windows symlink-creation cases denied with `WinError 1314`.
The suite did execute Windows junction/reparse coverage for model inspection,
export roots/entries, missing-source repair and cleanup boundaries. Not run for
this increment: a packaged executable, physical removable media, antivirus
races, clean-machine Windows/macOS acceptance, or real concurrent external
processes beyond the source/headless regression harness. No private paths,
secrets, model weights or generated wheel were added to Git, and local `main`
remained unpushed during this increment.

## W2-S1 coordinated worker shutdown — app/recorder/hotkey/maintenance thread half — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12, source tree on
`main`, implementation base `c8182aa`. This source/headless record covers the
GUI thread half and the clean-profile source GUI close smoke. Together with
the process/queue record below, W2-S1 is `PASS` at source/headless level only;
native physical-device, packaged and broader R0 acceptance remain open.

The exact shutdown contract is documented in the durable evidence record
[`docs/verification/2026-08-30-w2-s1-thread-shutdown.md`](docs/verification/2026-08-30-w2-s1-thread-shutdown.md).
In brief, `_close()` refuses while non-daemon maintenance is active, then sets
the shutdown/cancel events, stops hotkey and recorder owners, closes the
process controller, joins all daemon GUI workers against one monotonic
three-second budget, names any residues, drops late worker events, and reaches
Tk `destroy()` from a `finally` block. Close is idempotent. Recorder-owned
timeouts and identity-ambiguous files are retained and reported. Python cannot
force-kill a blocked third-party thread; a daemon residue is named and the OS
ends it with the process.

| Check | Result |
| --- | --- |
| `python -m compileall -q src tests` | PASS |
| `PYTHONPATH=src .\\.venv\\Scripts\\python.exe -m pytest -q` | PASS; 483 passed, 3 skipped (Windows symlink privilege boundaries) |
| Focused app/recorder/hotkey shutdown regressions | PASS; 22 passed, 54 deselected |
| `.\\scripts\\quality_gate.ps1` with `.venv\\Scripts\\python.exe` | PASS; compileall, Ruff, Help validation, 483 passed / 3 skipped, CLI `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w2-s1-thread-wheel` | PASS; `voice_studio-0.3.0rc1-py3-none-any.whl`, 691,474 bytes, SHA-256 `EFE7754E9F868D14964B5BC9DF17BCC8AFDF03C17AA3FCC570A6BA25ED969517` |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |

Clean-profile source GUI smoke used a fresh disposable run under
`build\\w2-s1-thread-smoke\\run-r1\\{config,data,cache}`. The parent set all
three `VOICE_STUDIO_*_DIR` overrides plus `PYTHONPATH=src`, then used
`System.Diagnostics.ProcessStartInfo` to launch the resolved repository
`.venv\\Scripts\\python.exe`. Its exact inline Python was
`from voice_studio.app import VoiceStudioApp; app=VoiceStudioApp(); app.after(1000, app._close); app.mainloop()`.
The child therefore instantiated `VoiceStudioApp`, entered Tk `mainloop()`,
and requested normal close through `after(1000, app._close)`.

The parent captured PID `23088`, verified before waiting that
`Win32_Process.ExecutablePath` exactly matched the resolved repository `.venv`
Python and that its command line contained `voice_studio.app`, then called
`WaitForExit(15000)`. It redirected stdout/stderr to
`build\\w2-s1-thread-smoke\\run-r1\\gui.stdout.log` and
`gui.stderr.log`. Result: identity verified, controlled close `True`, exit
code `0`, elapsed `1924 ms` (<15 s), stdout `0` bytes and stderr `0` bytes.
The exact-PID-only timeout termination fallback was implemented and was not
needed. This does not verify the frozen executable, physical microphone or
native macOS event tap, antivirus/removable-media behavior, clean-machine
installation, real power loss/forced termination, or the 50-task physical-
device matrix.

## W2-S1 coordinated worker shutdown — process/queue half — 2026-08-30

Environment: Windows x64, repository `.venv` CPython 3.12, implementation base
`c2bfcc9` on local `main`. This record closes only the transcription and model-
download process/queue half. The app, recorder, hotkey and maintenance-thread
lifecycle remains open; full W2-S1 and broader R0 completion are not claimed.

The ownership and shutdown contract is:

- `TranscriptionJobController` owns one immutable spawn-generation snapshot at a
  time: the worker process, request queue, result queue and monotonic token. A
  lifecycle `RLock` protects generation attach/detach; a run `Lock` serializes
  public `run()` calls. A close increments the lifecycle epoch and detaches the
  snapshot before cleanup, so blocked prepare/loading checkpoints cannot allocate
  a process or either queue after close. `close()` is reusable: a later `run()`
  creates a fresh generation.
- A normal close first best-effort enqueues the `None` sentinel, then allows at
  most `join(timeout=3)` for graceful exit. The shared stop helper then performs
  `terminate()` followed by `join(timeout=5)`, and, if still alive, `kill()`
  followed by `join(timeout=2)`. It checks liveness after each bounded join and
  never performs an unbounded process join.
- Each owned multiprocessing queue is disposed by `cancel_join_thread()` then
  `close()`, with an idempotence marker/fallback identity set. Repeated cleanup
  does not repeat either lifecycle call, and `join_thread()` is never invoked.
- `ModelCatalog.install()` owns its spawn child, one result queue and its unique
  `.downloads/{model}-{uuid}` staging directory. The worker is polled with
  `join(timeout=0.25)` and its result is read with `get(timeout=2)`; every exit,
  cancellation and timeout reaches the same terminate/kill and queue-disposal
  `finally` path before removing only that staging directory.

The focused regressions cover one-generation startup under two synchronized
threads; exactly-once disposal under repeated close; close during slow work,
prepare and the final pre-start checkpoint; run-after-close recreation; two
serialized concurrent runs retaining both results; stopped-worker disposal before
the existing concrete error; and a successful subprocess close with no
`resource_tracker` or leaked-semaphore stderr. Model-download regressions cover
cancel and timeout escalation, queue disposal, start/worker failure cleanup and
staging cleanup while preserving the original source.

| Check | Result |
| --- | --- |
| `$env:PYTHONPATH='src'; .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_jobs_app.py -k "concurrent_worker or close_during or run_after_close or concurrent_run or disposes or resource_tracker or no_queues"` | PASS, 7 passed, 14 deselected |
| `$env:PYTHONPATH='src'; .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_jobs_app.py tests/test_cli_app.py tests/test_gui_contract_app.py tests/test_recording_lifecycle_app.py` | PASS, 86 passed |
| `$env:PYTHONPATH='src'; .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_jobs_app.py tests/test_model_catalog_app.py` | PASS, 48 passed / 1 skipped (Windows symlink privilege boundary) |
| `$env:PYTHON_BIN=(Resolve-Path '.\\.venv\\Scripts\\python.exe').Path; .\\scripts\\quality_gate.ps1` | PASS; compileall, Ruff, Help validation, 452 passed / 3 skipped (Windows symlink privilege boundaries), CLI version `0.3.0rc1` |
| `.\\.venv\\Scripts\\python.exe -m build --wheel --no-isolation --outdir build\\w2-s1-process-wheel` | PASS; `build\\w2-s1-process-wheel\\voice_studio-0.3.0rc1-py3-none-any.whl`, 689,296 bytes, SHA-256 `4CA44964FCB54074E07180D3618F5A8E0AB9E79327949CC287C2FA634A4BD5E3` |
| `.\\.venv\\Scripts\\python.exe -m pip check` | PASS; `No broken requirements found.` |
| `git diff --check` | PASS; no whitespace errors |

The supported source gate ran on Windows x64 only. No physical macOS or Windows
device matrix, packaged executable shutdown run, real power-loss/forced-
termination run, or native app/recorder/hotkey acceptance is included here.

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
