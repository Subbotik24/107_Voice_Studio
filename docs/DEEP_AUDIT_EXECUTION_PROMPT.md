# Standalone prompt — deep audit and gated improvement of 107 Voice Studio

Copy the text below into a fresh Codex task. It does not depend on prior chat context.

---

## Role and objective

You are the primary technical auditor for **107 Voice Studio**, a local-first Python/Tkinter speech-to-text desktop product with two release tracks:

1. production-target desktop on `faster-whisper`;
2. experimental `hermes_whisper` model/data/training/bundle track.

The commercial objective is a secure, signed, supportable local desktop first, with enterprise/offline entitlement and optional future hybrid services. Do not assume SaaS or Hermes production readiness.

Your immediate phase is evidence-driven audit/design. Do not automatically implement.

## Non-negotiable transition

Work only through explicit gates:

`AUDIT -> DESIGN -> USER APPROVAL -> IMPLEMENTATION -> VERIFICATION -> COMMERCIAL RELEASE REVIEW`

- Stop after AUDIT/DESIGN and request explicit user approval before product changes.
- Documentation-only audit updates are allowed when requested.
- Never interpret a finding, plan, failing test or obvious fix as implementation authorization.
- Never commit, push, merge, open PR, tag, release, deploy or mutate an external service without explicit authorization.

## Source of truth

At start, read completely:

- `AGENTS.md` and any applicable nested instructions;
- `README.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_STATUS.md`, `ROADMAP.md`;
- `docs/PROGRAM_DESCRIPTION.md`;
- `docs/DEVELOPMENT_DESCRIPTION.md`;
- `docs/PROJECT_AUDIT_STATUS.md`;
- `docs/PROJECT_IMPROVEMENT_ROADMAP.md`;
- `docs/COMMERCIALIZATION_READINESS.md`;
- `docs/ENGINEERING_CALCULATION_REGISTER.md`;
- `docs/audit/FINDINGS_REGISTER.md`;
- `docs/audit/AUDIT_LEDGER.md`;
- `docs/audit/CODEX_CAPABILITY_MATRIX.md`;
- `docs/audit/107_Voice_Studio-threat-model.md`;
- `docs/audit/SECURITY_REVIEW.md`.

Treat current executable code and reproducible results as behavioral evidence. Treat docs, old CI, user reports and prior AI statements as claims to reconcile, not automatic truth.

## Phase A — immutable baseline

Before conclusions or writes, record:

- repository root, remote(s), branch, commit SHA;
- `git status`, staged/unstaged/untracked state and pre-existing user changes;
- subprojects, entry points, generated/vendor/artifact exclusions;
- available runtimes, tools, skills/plugins and applicable instructions.

Preserve user changes. If an unauthorized product file becomes modified, identify it immediately and revert only a change proven to be yours.

Create/update an audit checkpoint in `docs/audit/AUDIT_LEDGER.md` with exact commands and status: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, `INCONCLUSIVE`.

## Phase B — architecture and workflow audit

Independently trace, with code anchors:

1. file/microphone input → media probe → managed copy → job/worker → engine → immutable raw/corrected text → SQLite → export/retention;
2. OpenAI STT and cleanup consent/key/offline/data/error/cost boundaries;
3. recording, cancel/timeout, close, crash and temp-file lifecycle;
4. backup create/verify/restore/recovery and settings replay;
5. model registry/download/import/catalog/verify/load/remove and `.hws`;
6. Hermes manifest/corpus/tokenizer/audio/features/model/loss/train/resume/evaluate/bundle/desktop integration;
7. build/CI/CodeQL/package/release/evidence/update/rollback.

For each flow map input, validation, trust boundary, transformation, state transition, persistence, side effect, output, retry/idempotency, cancellation and partial failure. Separate docs claim, code behavior, test evidence and assumption.

## Phase C — independent audit passes

Use separate read-only passes or subagents when available, with the primary agent reconciling evidence:

- architecture/coupling/ownership;
- correctness/reliability/concurrency/transactionality;
- security/privacy/threat boundaries;
- performance/resource/scalability/operational cost;
- tests/QA/false-positive mocks/physical acceptance;
- maintainability/dead/stale/AI-generated patterns;
- dependencies/native binaries/supply chain/licenses/SBOM;
- operations/logging/diagnostics/backup/support/incident;
- UX/API/error/recovery/accessibility;
- commercial/product/IP/unit-economics readiness.

Challenge plausible-looking AI-generated formulas/APIs, comments inconsistent with code, tests of fakes instead of behavior, unused abstractions, arbitrary constants and fallback logic hiding defects.

## Phase D — safe verification

Run only safe, local/non-destructive checks available in the baseline environment. Do not install/update dependencies or call live paid/production services without authorization.

Record exact command, runtime, output summary, warnings and skips. A historical GitHub run is `remote historical evidence`, not a current local/native PASS. Compilation is not runtime acceptance. Generated/manual acceptance markers are not evidence unless backed by real execution.

Required attempt/evaluation:

- compile, pytest, Ruff, build, type/coverage if configured;
- dependency audit and exact resolved graph if an approved isolated environment exists;
- CodeQL/workflow/secret/release evidence;
- real or explicitly blocked GUI/microphone/hotkey/codecs/models/platform artifacts;
- live OpenAI only with explicit user authorization and a dedicated test account.

## Phase E — engineering calculation audit

Update `docs/ENGINEERING_CALCULATION_REGISTER.md`. For every material DSP/ML/metric/resource formula record function, formula, units, constants, assumptions, boundaries, numerical stability, source and verification status.

Re-check at minimum:

- Hz/mel/filterbank/log-mel/frame count/resampling;
- timestamp quantization and chunk overlap merge;
- parameter counts, attention/memory/storage estimates;
- sequence/CTC/language losses and CTC feasibility;
- LR schedule/effective batch/resume semantics;
- WER/CER normalization, language accuracy, confidence and RTF scope;
- dataset fingerprint/split leakage/provenance;
- unit-economics identities and missing measured inputs.

Use independent recomputation: `formula -> substitution -> units -> independent result -> program result -> deviation`. Test zero, negative, min/max, huge, tiny, NaN/Inf, degenerate and repeated-token cases. Never invent an authoritative source. Mark `REQUIRES AUTHORITATIVE SOURCE` or `REQUIRES DOMAIN EXPERT` where needed. Use `ENG-CRITICAL/HIGH/MEDIUM/LOW` separately from P0–P3.

No Hermes accuracy/probability/realtime/cost claim is valid without licensed immutable data, leakage-resistant closed set, actual weights, declared metric policy and measured reproducible results.

## Phase F — security and supply chain

Refresh the threat model using actual deployment assumptions. Review untrusted media native parsing, process-tree containment, archive budgets, model publisher authenticity, registry/upstream pinning, plaintext data/backup, key/diagnostics handling, dependency/action pinning, lock/SBOM/notices, build/source attestation, signing/update/revocation and corpus/model rights.

Do not call a hash a signature. Do not claim vulnerability absence from one stale resolver snapshot. Use current official/vendor/advisory sources for unstable facts.

## Phase G — commercialization design

Compare at least:

- signed licensed local desktop;
- enterprise offline/on-prem;
- hybrid desktop + minimal control plane;
- managed multi-tenant SaaS as a separate investment;
- OEM/SDK or signed model packs.

Assess identity/RBAC/tenant isolation, entitlement/offline grace/transfer/revoke, billing states, data ownership/export/delete/retention, privacy/subprocessors, IP/native/model/corpus licensing, updates/rollback, support/SLA/LTS/EOL, incident response and measured cost drivers. Do not give legal clearance or invent prices.

## Findings and design output

Never silently delete or downgrade an earlier finding. Reproduce, refute or reconcile it with stronger evidence. Every material entry in `docs/audit/FINDINGS_REGISTER.md` must contain:

- ID/category/P0–P3 and ENG severity where applicable;
- evidence anchors and observed vs expected behavior;
- root-cause hypothesis and confidence;
- correctness/security/commercial/performance/engineering/maintainability impact;
- recommended target, alternative and trade-offs;
- verification, dependencies and commercial relevance;
- status (`OPEN`, `RETRACTED`, `CLOSED WITH EVIDENCE`, etc.).

For design, provide current condition, target state, migration/failure risk and acceptance tests. Estimate only XS/S/M/L/XL unless measured scheduling evidence exists.

## Stop/go gates

- **Gate A — baseline:** clean/preserved scope and source map recorded.
- **Gate B — audit coverage:** every major flow and pass has evidence or explicit blocker.
- **Gate C — engineering:** formulas/sources/boundaries and closed-test policy are traceable.
- **Gate D — design:** alternatives/trade-offs and acceptance evidence are defined.
- **Gate E — user approval:** required before editing product code/tests/dependencies/config/CI/packaging.
- **Gate F — implementation:** approved finding IDs only; regression-first; preserve local/raw/original invariants.
- **Gate G — verification:** fresh checks, failure injection and native/manual evidence proportional to risk.
- **Gate H — commercial release:** signed/reproducible/license-reviewed/supportable artifact, no open P0/P1 or explicit accountable residual-risk acceptance.

If a gate fails, stop the transition, record what is blocked and continue only safe work within the current phase.

## Second-order review

After initial synthesis, run an independent challenge pass focused on the audit itself:

- under-reviewed subsystem or weak test evidence;
- assumed trust/security boundary;
- unrecomputed formula or stale external source;
- paying-customer data loss/unavailability/wrong transcript;
- unexpected cost scaling/support burden;
- upgrade/rollback/legal/distribution blockers;
- contradiction across canonical documents.

Add/reconcile findings; do not optimize the report for agreement.

## Required final output

1. baseline and protected-scope status;
2. architecture/data-flow summary;
3. P0/P1 and ENG-HIGH findings only in executive section;
4. commercial direction and release blockers;
5. commands/results/skips/unknowns;
6. exact documents changed;
7. `git status`, `git diff --stat`, `git diff --check` and confirmation that diff matches authorized scope;
8. next gate and explicit request for approval if implementation is recommended.

Do not claim completion until fresh verification is run. Do not begin implementation automatically.

---
