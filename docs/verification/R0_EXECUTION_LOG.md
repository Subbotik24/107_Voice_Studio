# R0 execution and verification log

This is the controller's cumulative ledger for R0 implementation. It prevents
repeating an already-green verification run when no relevant tracked files have
changed. Product-facing status remains in `IMPLEMENTATION_STATUS.md`; detailed
release evidence remains in `VERIFICATION.md`.

## Re-run policy

- Every entry records the verified Git commit, command/scope, result and known
  limitations.
- A focused result may be reused only while the files covered by that scope are
  unchanged after the recorded commit.
- A full quality gate runs once at each completed increment, after a fix that
  changes production code outside the previously verified scope, and before the
  integrated release conclusion.
- Documentation-only corrections use Help validation and diff checks unless
  they change a documented executable contract or package contents.
- Native, packaged, physical-device and signing checks are never inferred from
  source/headless results.

## Completed verification checkpoints

| Increment | Verified commit | Evidence | Result | Re-run trigger |
| --- | --- | --- | --- | --- |
| R0 continuation baseline | `bbc7d34` | `compileall -q src tests` + `PYTHONPATH=src pytest -q` | 609 passed, 9 Windows symlink-privilege skips in 35.32s | any production-code change; superseded per increment |
| W3-V1 configurable VAD | `a013cec` | RED: 12 failed / 1 passed on untouched code; GREEN focused `tests/test_vad_app.py` 13 passed; related suites (config, faster-whisper, CLI, GUI contract, runtime boundaries, i18n, ollama, hardware, jobs) 199 passed; one full gate after final production change; Help validation; Ruff | 622 passed, 9 Windows symlink-privilege skips in 39.78s | VAD settings/engine/registry/CLI/GUI/i18n code or corresponding tests change |
| W3-E1 minimal subtitle consistency | `8114a39` | incomplete K3 state exposed 10 focused failures; controller RED/GREEN regressions and independent read-only review; focused `tests/test_subtitle_sync_app.py` 21 passed; related storage/service/export/editor/dictionary/backup suites 156 passed / 6 skipped; final compileall + Ruff + pytest; Help validation; staged secret/path scan | 643 passed, 9 Windows symlink-privilege skips in 47.91s; review found no remaining Critical/Important issues | subtitle synchronization, manual-editor storage/undo, segment-derived dictionary correction, exporters, backup serialization or corresponding tests change |
| W2-E1 Slice 0 cryptography dependency/provenance boundary | `d649fdb` | controller-reviewed K3 diff; focused dependency/SBOM/packaging suite; compileall; Ruff; full pytest; `pip check`; two deterministic SBOM generations; staged diff check | focused 48 passed; full 647 passed, 9 Windows symlink-privilege skips in 45.24s; SBOM outputs byte-identical, 59 components, 11,348 bytes, SHA-256 `5f60473d50bff64a0a56036b0d3ab3f7fc8b6a68c4613ea1af0f44f866252e7a`; `cryptography==50.0.1`; no frozen build claim | dependency/lock, PyInstaller spec, SBOM generator/contracts, cryptography boundary tests, or later W2-E1 production/package changes; frozen runtime probe remains for R0.10 |
| W2-E1 Slice A cryptographic primitives | `3c42a33` + memory-budget correction `108eae8` | K3 RED collection error before the new module; controller line-by-line review; controller RED reproduced acceptance of trailing ciphertext, invalid short-read chunking and a two-chunk plaintext lookahead; GREEN focused crypto/dependency suite; compileall; Ruff; `pip check`; staged diff and secret/private-path scan | focused 31 passed in 6.42s; Argon2id/HKDF/AES-GCM/HMAC contracts covered; short reads are filled, decrypt rejects bytes beyond the authenticated chunk count, and encrypt retains one plaintext chunk plus one-byte finality lookahead; baseline before the slice remained 647 passed / 9 Windows skips; per the W2-E1 plan the full suite was not repeated after this isolated, not-yet-integrated module | `backup_crypto.py`, its tests, cryptography version, or any Slice B+ integration that calls these primitives changes; run the full source gate once after Slice D's final production change |
| W2-E1 Slice B1 encrypted v2 creation | `6e74d5d` | K3 RED 12 failed / 1 legacy-v1 pass; controller review and RED regressions for missing outer ZIP inspection, unbounded config reads, silently omitted oversized dictionary and unenforced manifest limit; related create/crypto/v1-backup/archive-safety gate; compileall; Ruff; `pip check`; staged diff and secret/private-path scan | related 126 passed, 2 Windows symlink-privilege skips in 12.96s; final signature check 1 passed; v1 omission path preserved; v2 uses opaque ZIP_STORED members, authenticated private index/manifest, bounded config/transcript streams, atomic publication and the unchanged outer ZIP budget; baseline before the slice was 674 passed / 9 Windows skips; full post-change suite deferred by the W2-E1 slice plan | `backup.py` create/v2 helpers, backup crypto, archive budgets/inspection, storage transcript serialization, or corresponding tests change; Slice B2 must rerun this related boundary and the full source gate remains due after Slice D |
| W2-C2 model catalog | `e379b42` | full quality gate | 426 passed, 1 Windows symlink-privilege skip | model catalog, CLI model dispatch, GUI startup or catalog tests change |
| W2-C1 restore preservation | `94ff8f1` | full quality gate, restore scope, wheel/pip/help | 435 passed, 3 Windows symlink-privilege skips; artifacts PASS | backup/restore, storage layout or archive safety changes |
| W2-S1 process/queue shutdown | `0d43e77` | full quality gate and process lifecycle scope | 452 passed, 3 Windows symlink-privilege skips | controller, multiprocessing queue or model-download lifecycle changes |
| W2-S1 thread/application shutdown | `12def08` | full quality gate and controlled GUI close | 483 passed, 3 Windows symlink-privilege skips; source GUI PID exited 0 in 1924 ms | app close, recorder, hotkey or maintenance worker lifecycle changes |
| W2-C3 storage audit/repair before read-only CLI correction | `bb3abee` | full quality gate, focused storage/model/backup/CLI, wheel, pip | 524 passed, 8 Windows symlink-privilege skips; wheel SHA-256 `10877B1165273174D0B8826B96B9A85B012DAA2712807AE6270D91276E5634E0` | storage/audit/CLI code changed at `69edd9f`, so the full result is superseded below |
| W2-C3 read-only CLI correction | `69edd9f` | full pytest, focused storage/model/backup/CLI, compile, Ruff, diff check | 528 passed, 8 Windows symlink-privilege skips; focused 145 passed, 8 skipped | superseded by the stable-snapshot corrections below |
| W2-C3 stable audit snapshot | `7e7f8e0` | full pytest, focused storage/model/backup/CLI, compile, Ruff, diff check | 531 passed, 8 Windows symlink-privilege skips; focused 148 passed, 8 skipped | root replacement and temp containment changed at `b7ba7fd` |
| W2-C3 final read-only audit | `b7ba7fd` | full pytest, focused storage/model/backup/CLI, focused independent review, compile, Ruff, diff check | 533 passed, 8 Windows symlink-privilege skips; related 150 passed, 8 skipped; final review CLEAN | storage/audit/CLI production code or corresponding tests change |
| W2-C3 final docs/artifact | `1c0f471` | Help validation, wheel rebuild, pip check, diff check | 13 Help files PASS; wheel 700,821 bytes, SHA-256 `33441D2FB1932501241DA09E205C566EB6BD7DB392666ECAD29AFD0FEADBFCC8`; dependencies PASS | Help/package inputs or release metadata change |
| W3-H1 hardware settings and advisory detection | `04c26a6` (docs: `c3c95fd`) | RED/GREEN review regressions; focused config/engine/hardware/CLI/GUI/runtime-boundary/i18n tests; one final source gate; temporary frozen console detector probe | Focused `154 passed in 3.73s`; final compileall + pytest `609 passed, 9 skipped in 41.75s`; cold no-parent-preload frozen child returned `ok` with CPU+CUDA and 7 compute types under 5 s | Any production change to hardware validation/detection, faster-whisper preflight, job settings boundary, CLI hardware dispatch, GUI settings worker/event handling, i18n catalogs, or packaging launcher/spec; docs-only edits require Help validation and `git diff --check` |
| W4-B1 reproducible SBOM final fix | `3e85aaf1ab6e8505925ef12a5a822181d6b0a4df` | RED/GREEN release boundary regressions, two lock-root generators, one post-fix full quality gate, wheel, pip check | SBOM 11,159 bytes, SHA-256 `0e1d420fadbdcc4c78e8130c00b5f217c3a6374853f045d3c4cd73d22f300377`; 58 components; focused 60 passed / 1 skipped; full 583 passed / 9 Windows symlink-privilege skips; wheel 701,107 bytes, SHA-256 `dadf53304aca7a31d297cc31c031f62b37cb53cf3e85d02b43e8d1c072a31a7a` | production generator/manifest/release-filesystem/staging code or corresponding tests change |

## W3-H1 exact verification commands and rerun triggers

Focused command:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q tests/test_config_app.py tests/test_faster_whisper_app.py tests/test_hardware_app.py tests/test_jobs_app.py tests/test_cli_app.py tests/test_gui_contract_app.py tests/test_runtime_boundaries_app.py tests/test_i18n_app.py
```

Final source gate (run once after the final production-code change):

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q
```

The temporary console-probe build used an ephemeral `_w3_h1_probe_final.spec`
and launcher, both removed after verification. The cold no-parent-preload run
returned `ok` with CPU+CUDA and seven types under the production 5-second
deadline; the child log measured CTranslate2 import `2.858s`, and the exact
process run reported `elapsed=6,329s exit=0`. Instrumented comparison logged
parent import `3.783s` and parent-preloaded child import `0.121s`. The normal
windowed bundle remains GUI-only for CLI dispatch. Rerun the frozen probe after
packaging spec/launcher changes or on a host with a fresh packaged runtime.

The final review findings are resolved at production commit `04c26a6`:
full `Settings.validate()` now runs before source preparation and worker
startup; `auto` runtime preflight validates against CTranslate2's CUDA-or-CPU
selection; and CTranslate2 import/preflight precedes `faster_whisper` import
with a concrete broken-runtime error. The invalidated focused suite passed
154 tests, and the final source gate passed 609 tests with 9 documented
Windows symlink-privilege skips.

Rerun the focused command for changes within the verified scope; rerun one full
source gate after any production-code change outside this scope or after a
verified regression fix. Documentation-only edits require Help validation and
`git diff --check`, not a repeated full gate.

## W4-B1 current checkpoint

- Verified final-fix production code commit: `3e85aaf1ab6e8505925ef12a5a822181d6b0a4df`.
- Canonical artifact comparison passed: outputs from two temporary lock roots
  were byte-identical; the retained relative artifact has 58 components, no
  absolute/private paths, 11,159 bytes and SHA-256
  `0e1d420fadbdcc4c78e8130c00b5f217c3a6374853f045d3c4cd73d22f300377`.
- Exactly one post-final-fix full quality gate ran: 583 passed, 9 Windows
  symlink-privilege skips; wheel and `pip check` passed. It was not rerun after
  documentation-only edits. The wheel was 701,107 bytes with SHA-256
  `dadf53304aca7a31d297cc31c031f62b37cb53cf3e85d02b43e8d1c072a31a7a`.
- Focused RED captured eight failures in the reviewed boundary; focused GREEN
  passed 60 tests with one symlink-privilege skip. Windows junction swaps and
  atomic no-replace publication executed; macOS received source/syntax checks
  only and no physical Test RC build claim.
- The SBOM is lock-only provenance for the pinned Windows release environment,
  not necessarily frozen-runtime contents, and is not license/vulnerability/
  signature evidence. Source/contract/syntax checks passed; physical Windows or
  macOS Test RC builds, signing, clean-machine and physical-device gates remain
  `NOT_RUN`.
- After documentation edits, only Help validation, `git diff --check` and
  status checks are permitted; do not repeat the full suite.
