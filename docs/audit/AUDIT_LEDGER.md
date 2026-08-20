# Audit ledger — 107 Voice Studio

## Baseline

Audit date: 2026-08-20. Repository state: `main@fffa50b6bc26fa2e7fa2150f2260ae873a5cf511`, initially clean, `origin=https://github.com/Subbotik24/107_Voice_Studio.git`. Product code in `main` remained frozen. A transient isolated-worktree incident is recorded below rather than hidden.

Status vocabulary: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, `INCONCLUSIVE`.

## Ledger

| Phase | Area | Status | Command/evidence | Result/findings | Confidence / next step |
|---|---|---|---|---|---|
| 0 | Git baseline | PASS | `git status --short --branch`; `git rev-parse HEAD`; `git remote -v` | clean `main...origin/main`, exact SHA/remote recorded | high; re-run at final gate |
| 0 | User changes | PASS | initial status and diff | no pre-existing modifications observed | high; preserve authorized docs only |
| 1 | Instructions | PASS | attachment master prompt; root `AGENTS.md` | read-only product phase and required outputs mapped | high |
| 1 | Codex capabilities | PASS | skill/plugin/tool inventory; bundled runtime discovery | existing capabilities sufficient; no install justified | high; matrix created |
| 2 | Repository inventory | PASS | `rg --files`, targeted line/file counts, pyproject/workflows/docs | desktop and Hermes tracks, entry points, build/test/docs mapped | high |
| 2 | Git history | PASS | targeted `git log`, commit/file statistics | current baseline primarily introduced in a large initial implementation; current behavior remains source of truth | medium; no authorship inference |
| 2 | Architecture | PASS static | source/docs/tests review | GUI/CLI→prepare→worker→engine→store/export/backup; Hermes data→train→evaluate→bundle traced | high |
| 2 | Data/privacy | PASS static | storage, config, backup, cloud, diagnostics | local/OS boundary, immutable raw text, opt-in cloud, plaintext data and redaction gaps | high |
| 3 | Python availability | PASS | bundled runtime discovery and version | Python 3.12.13 found; system alias unusable | high |
| 3 | Compile | PASS | bundled `python -m compileall -q src tests` | exit 0, ~0.08 s | high; pycache ignored |
| 3 | Pytest | BLOCKED | bundled `python -m pytest -q` probe | `No module named pytest`; no dependency install | high; execute in approved isolated dev env |
| 3 | Ruff | BLOCKED | `python -m ruff` probe | module absent | high |
| 3 | Build | BLOCKED | `python -m build` probe | module absent | high |
| 3 | pip-audit | BLOCKED | `python -m pip_audit` probe | module absent | high |
| 3 | Remote CI | PASS historical | GitHub Actions run `31278777131` | exact baseline SHA; four OS/Python jobs and defined checks successful | high for that run only |
| 3 | Remote CodeQL | PASS historical | runs `31278777138`, `31992968536` | exact baseline push/scheduled scans successful | high for those runs only |
| 3 | CI coverage review | FAIL gap | `.github/workflows/ci.yml`, tests | `[hermes]`/Torch not deterministic; no coverage/type gate; GUI/platform tests heavily fake/static | high; QA-001/002 |
| 4 | OpenAI contract | PASS static / live NOT RUN | `Settings` default; official current Audio API docs | `gpt-transcribe` is explicitly supported; earlier contrary hypothesis retracted; live account call not authorized | high for model list; add drift contract |
| 4 | Dependency versions | INCONCLUSIVE | official PyPI metadata on 2026-08-20; repo ranges | current versions identified, but no resolved lock/environment means exact vuln/license state unknown | high; SUP-001 |
| 4 | Actions pinning | FAIL hardening | workflows; official GitHub secure-use docs | movable major tags used; full-SHA recommendation not met | high; CI-001 |
| 4 | Packaging standard | FAIL gap | pyproject/lock inventory; Python Packaging docs | no standard lock/hashes/SBOM | high; SUP-001 |
| 5A/B | Desktop independent audit | PASS review | read-only subagent, anchored code/tests | no P0; settings, diagnostics, recording, lifecycle, UI blocking, atomicity findings | high; reconciled register |
| 5C | Security/privacy independent audit | PASS review | threat-model subagent, anchored source/workflows | no P0; model/native/archive/storage/release/supply-chain P1s | high; security docs created |
| 5D/J | Hermes/commercial independent audit | PASS review | read-only ML/calculation/commercial subagent | overlap, leakage, fingerprint, CTC, metrics, performance and IP blockers | high; calculation/commercial docs |
| 6 | Formula probes | PASS/PARTIAL | bundled Python direct imports and independent arithmetic | mel, frames, count, edit metrics, timestamp, LR, parameter estimate agree | high for tested examples |
| 6 | Non-finite boundary | FAIL | manifest record with NaN duration | invalid record accepted | high; ENG-004 |
| 6 | Standards/source review | INCONCLUSIVE | source comments/docs | feature/filterbank/calibration provenance incomplete | high; authoritative verification required |
| 7 | Native runtime | NOT RUN | no live GUI/device/model invocation | microphone/hotkey/codecs/permissions/real inference unverified | high; physical acceptance gate |
| 7 | Live OpenAI | NOT RUN | no credentials/network side effect authorized | privacy/compatibility/cost not runtime-verified | high |
| 7 | Hermes train/evaluate | NOT RUN | no Torch/corpus/weights | quality, convergence, measured resources unknown | high |
| 8 | Canonical docs | PASS | authorized Markdown changes | program, development, status, roadmap, commercial, calculations, audit/security/prompt docs created | verify links/content at final |
| 8 | Future implementation design | PASS docs-only | `docs/superpowers/specs/2026-08-20-desktop-data-safety-design.md`; paired plan | proposed remediation made reusable and explicitly marked `NOT AUTHORIZED`; no product step executed | high; requires a later user-approval gate |
| 8 | Existing security doc | PASS docs-only | `SECURITY.md` audit + final phrase scan | stale “cloud adapters absent” statement corrected; DOC-001 marked `RESOLVED_DOCS` | high; runtime cloud evidence remains separate |
| 9 | Second-order challenge | PASS review | independent read-only reviewer after initial synthesis | corrected OpenAI retraction drift and ambiguous anchors; added clipboard, unsaved-edit, capture-status, market-validation and brand-identity gaps; aligned severities/IDs/calculations | high; all material comments reconciled |
| 10 | Transient protected-scope incident | FAIL → REMEDIATED | linked worktree `codex/desktop-data-safety`; `git worktree list/status`; exact worktree/branch removal | an uncommitted product-code draft was created outside `main`; after re-reading the master read-only constraint it was identified, never committed/transferred, and the worktree plus branch were removed; `main` product files never changed | high; draft had no commit and is not recoverable |
| 10 | Final protected scope | PASS with test blocker | bundled Python `compileall -q src tests scripts packaging`; pytest probe; required-doc/link/finding-anchor scans; `git diff --check`; allowlist; status/diff; HEAD↔origin | compile PASS; pytest BLOCKED (`No module named pytest`); 12 required docs and links PASS; 33 roadmap IDs mapped; only `AGENTS.md`, `SECURITY.md`, `docs/**`; HEAD=origin/main, divergence 0/0 | high; no product code/config/test/dependency/CI/package change |

## External authoritative references consulted

- OpenAI Audio API model contract: <https://platform.openai.com/docs/api-reference/audio/createTranscription>
- OpenAI endpoint data policies: <https://platform.openai.com/docs/models/default-usage-policies-by-endpoint>
- GitHub Actions secure use/full-SHA pinning: <https://docs.github.com/en/actions/reference/security/secure-use?learn=getting_started&learnProduct=actions>
- Python `zipfile` security/resource cautions: <https://docs.python.org/3/library/zipfile.html>
- Python Packaging lock format/tool recommendations: <https://packaging.python.org/en/latest/specifications/pylock-toml/> and <https://packaging.python.org/en/latest/guides/tool-recommendations/>
- Official PyPI metadata for declared dependencies: <https://pypi.org/>

## Open questions

- What exact operating systems/hardware/codecs are the paid support matrix?
- Are shared workstations/custom network data directories in product scope?
- Which OpenAI account/data-region/retention terms apply to the intended market?
- What are the exact licenses/build configurations of packaged native dependencies and weights?
- Who owns contributor code, brand, corpus and future model rights?
- Which entitlement and update availability promises are acceptable offline?
- What measured RTF/RAM/VRAM/support volume and willingness-to-pay justify pricing?

## Evidence rule

Historical repo claims, CI artifacts, user reports and simulated harnesses must stay labeled. Only an executed check with captured output can be `PASS`; missing native/manual evidence cannot be replaced with a document or generated marker.
