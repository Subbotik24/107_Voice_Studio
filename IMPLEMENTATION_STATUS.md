# Implementation status — 0.3.0 Test RC preparation

Last reviewed: 2026-08-08.

## Verified automatically

| Area | Status | Evidence |
| --- | --- | --- |
| Source compilation | PASS | `python -m compileall -q src tests` |
| Tests | PASS | `95 passed, 5 subtests passed` |
| Lint | PASS | `ruff check src tests scripts` |
| Wheel | PASS | `python -m build --wheel` |
| Dependency consistency | PASS | `python -m pip check` |
| Known dependency vulnerabilities | PASS | `pip-audit` |
| Local default behavior | Implemented | `faster-whisper`; no automatic cloud fallback |
| OpenAI STT consent contract | Tested with fakes | CLI/UI explicit consent, 25 MB limit, offline block |
| AI cleanup privacy contract | Tested with fakes | proposal/apply/undo; immutable `raw_text` |
| Model release installer hardening | Tested | SHA-256, size, archive traversal/symlink/duplicate checks |
| GitHub Actions matrix | PASS | macOS ARM64 + Windows x64, Python 3.11/3.12, run `31278657773` |
| CodeQL | PASS | run `31278657771` |

## Implemented but requires external/manual verification

| Area | Status | Required evidence |
| --- | --- | --- |
| Windows 10/11 x64 source launcher | NOT_RUN on a physical target | `run_windows.bat`, mic/media/local model workflow |
| Windows frozen ZIP | NOT_BUILT | `scripts/build_windows.ps1` plus clean-profile acceptance |
| macOS ARM64 Test RC | NOT_BUILT for 0.3.0 | build script, Gatekeeper behavior and clean-profile smoke |
| Live OpenAI STT/cleanup | NOT_RUN | manual public-domain fixture; never add keys to CI |
| Tiny/Small `models-v1` assets | NOT_CREATED | upstream provenance, inventory, license and SHA256SUMS |

## Explicitly out of scope for this RC

- signing/notarization;
- Intel Mac builds;
- local LLM text cleanup;
- OpenAI Realtime API or multi-provider cloud;
- Hermes production weights or accuracy claims.

## Release rule

This repository is source/Test RC preparation, not a production release. Follow
`RELEASE_ACCEPTANCE.md` before creating a tag or publishing artifacts.
