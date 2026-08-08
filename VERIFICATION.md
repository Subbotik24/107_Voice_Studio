# Verification record

## Takeover audit — 2026-08-08

Робоча Synology File Provider копія містила online-only (`dataless`) files, тому
прямий повний `compileall`/`pytest` зависав на hydration. Щоб не перезаписувати
невідомі локальні дані, gate виконано на матеріалізованій source/build копії з
підтвердженим SHA-256 архіву; змінені файли скопійовано з робочої копії та
порівняно byte-for-byte перед запуском.

| Перевірка | Результат |
|---|---|
| compilation `src tests` | PASS |
| Ruff | PASS |
| `PYTHONPATH=src pytest -q` | `87 passed, 5 subtests passed` |
| `pip check` | PASS |
| `pip-audit -r requirements.txt` | PASS, no known vulnerabilities |
| wheel build | PASS |
| synthetic Hermes CPU smoke | PASS, 2 optimization steps |
| changed files direct `py_compile`/Ruff | PASS у робочій копії |
| static typecheck | NOT_RUN: mypy/pyright не налаштовано |
| native Tk visual/manual acceptance | NOT_RUN під час takeover audit |
| Windows runtime/installer acceptance | NOT_RUN |

## Test RC verification — 2026-07-29

Середовище: macOS 26.5.2, Apple M4 Pro, Python 3.11.15, PyTorch 2.13.0.
Windows ще потребує окремого запуску.

## Виконані перевірки

| Перевірка | Результат |
|---|---|
| compilation `src/tests/scripts/packaging` | PASS |
| Ruff | PASS |
| `PYTHONPATH=src pytest -q` | `82 passed, 5 subtests passed` |
| editable install без dependencies | PASS |
| wheel build | PASS |
| wheel entry point `hermes-voice --version` | PASS, `0.2.0` |
| final wheel install у чистий venv без `PYTHONPATH` | PASS |
| clean-wheel `doctor` до зміни default model | EXPECTED INCOMPLETE: `small` не встановлено |
| clean-wheel explicit `tiny` import/verify/live transcribe | PASS, CPU int8 |
| clean-wheel retention/original SHA/storage audit | PASS |
| clean-wheel backup create/verify | PASS |
| `hermes-voice model doctor` у build environment | PASS |
| synthetic Hermes training | PASS, 2 optimization steps |
| checkpoint creation and verification | PASS |
| `.hws` create and SHA‑256 verification | PASS |
| `.hws` extraction and PyTorch load | PASS |
| Hermes adapter transcription of synthetic WAV | PASS |
| user-original retention tests | PASS |
| shared-source deletion regression | PASS |
| failed-engine orphan cleanup regression | PASS |
| WAV/MP3/M4A/MP4 real decoding | PASS |
| GUI construction, Tk 8.6 | PASS |
| microphone InputStream, samples discarded | PASS |
| spawn worker reuse/cancel/timeout cleanup | PASS |
| model catalog/offline/integrity/remove | PASS |
| SQLite migration/audit/backup/recovery | PASS |
| Hermes auto smoke on Apple Silicon | PASS, CPU, 2 steps |
| explicit `tiny` install/verify | PASS, 78.2 MB, revision and file hashes recorded |
| live `faster-whisper tiny`, WAV/MP3/M4A/MP4 | PASS, uk/cs/en/auto |
| macOS platform acceptance | PASS, 50 tasks, 0 crashes |
| acceptance task-level evidence | PASS, 50 records; originals unchanged |
| synthetic benchmark harness | PASS, WER/CER/RTF/RAM emitted; not a production claim |
| frozen runtime imports і tensor operation | PASS |
| training-only Hermes modules/PIL/pytest/TensorBoard/safetensors | excluded |
| packaged GUI cold start у чистому профілі | PASS, 6 seconds |
| PyInstaller Apple Silicon `.app` | PASS, 984,523,297 bytes, 2,934 files |
| unsigned DMG checksum/runtime | PASS, 316,418,616 bytes |
| strict ad-hoc `codesign --verify --deep --strict` | PASS |
| `hdiutil verify` | PASS |
| Gatekeeper | EXPECTED FAIL, `spctl` exit 3: unsigned/ad-hoc |
| source secrets/private-path/model-binary scan | PASS |
| backup restore integrity gate | PASS: invalid restored storage is rejected before replacement |
| GUI/CLI parent runtime boundary | PASS: model runtimes are lazy-loaded in worker |
| dependency vulnerability audit | PASS: no known vulnerabilities found |
| settings/hotkey lifecycle stress | PASS: 12/12 GUI cycles without crash |
| Tk dialog teardown before native hotkey restart | PASS: regression test |
| corrected editor Enter/Ctrl+Enter and formatting persistence | PASS |
| clean wheel build with SPDX metadata | PASS, no deprecation warnings |
| atomic Test RC wheel build without `pip` dependency | PASS |
| Windows launcher/build static contracts | PASS |
| Windows source/build copy archive integrity | PASS |

## Test RC evidence

- release label: `0.2.0-test-rc2`;
- wheel SHA-256: `410b4c5a9a5668ca1458009124f7e4e29c7b3fcd75c6e40c7f4bf1ef0c27a5cd`;
- DMG SHA-256: `f0bd50b37653756c30545ba55779c81b44eb2706cafdfc8fe9fbf342da134964`;
- acceptance JSON SHA-256: `9a4c11a68c8b22d1f2bd235d15d221ed56a051adff0cec997121ded1abbaca98`;
- `.app` tree SHA-256: `fa590ef4297a8aed1bdd9cb7d0760139a390589eeaa7cdb432fec882bad0e1cd`;
- 50/50 tasks completed, 0 crashes, storage audit PASS, originals unchanged.

PyTorch 2.13 у frozen profile збирається source-only. Staging patch
fail-fast замінює один несумісний dotted import і вимикає лише eager
`torch.distributed.rpc`; tensor/autograd/nn та Hermes local inference imports
проходять runtime probe. Публічні й окремі internal `torch.testing` модулі
залишені, бо PyTorch 2.13 використовує їх у runtime.

## Не виконано в цьому середовищі

- окремий static typecheck: репозиторій не має конфігурації або команди
  `mypy`/`pyright`; compileall, Ruff і runtime annotations перевірені;
- Playwright UI/E2E: не застосовується до native Tk desktop UI; GUI перевірено
  через Tk integration tests, lifecycle stress і packaged cold start;
- global hotkey Accessibility event delivery та повна visual acceptance;
- реальний `.hws` adapter transcription усередині frozen `.app` (imports і
  tensor runtime PASS; source `.hws` cycle PASS);
- Windows launcher/package/microphone/hotkey acceptance;
- Windows PyInstaller `.exe` build/runtime probe: NOT_RUN на macOS; підготовлено
  one-click pipeline для запуску на Windows x64;
- Windows `.exe/.msi`, Developer ID signing і notarization;
- Windows 50-task run;
- WER/CER/RTF benchmark на реальному українському, чеському та англійському мовленні;
- продуктивне навчання Hermes.

## Висновок

Статус — **unsigned macOS Test RC**, не production release.
