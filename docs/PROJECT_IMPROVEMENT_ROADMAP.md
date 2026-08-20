# 107 Voice Studio — improvement roadmap

## Operating gate

This is a design backlog, not implementation authorization. Every phase follows:

`AUDIT -> DESIGN -> USER APPROVAL -> IMPLEMENTATION -> VERIFICATION -> RELEASE REVIEW`

Prioritize product invariants and regression evidence. Do not merge desktop/faster-whisper release readiness with Hermes research readiness.

Complexity is relative: XS/S/M/L/XL, not calendar time.

## Phase 0 — critical correctness and security contracts

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Add OpenAI STT model contract/drift validation and an authorized live smoke plan; current `gpt-transcribe` default is officially supported | Hardening, not an open finding | S | supported defaults regression-tested; no unauthorized live call in CI |
| Preserve Hermes merged text and reconcile segment/timestamp overlap | ENG-001 | M | >30 s integration regression; no duplicate raw/SRT text |
| Strictly type and migrate settings errors into recoverable user messages | COR-002 | S | wrong-type JSON matrix passes GUI/CLI recovery tests |
| Add editor dirty-state/autosave or prompt before history switch/close | COR-004 | M | unsaved edits cannot be silently lost in navigation/close/crash cases |
| Make clipboard export explicit/previewable and document/disable auto-copy by default for sensitive workflows | PRV-003 | S | OS history/sync boundary is visible and testable |
| Bound recording memory/duration, surface capture status and guarantee crash/close temp cleanup | REL-001, PRV-001 | M | long/overflow/close/cancel tests plus private durable temp strategy |
| Make prepare validation/hash/copy cancellable and timeout-aware | REL-002 | M | large/slow/removable source tests |
| Move untrusted media probe/decoding into bounded disposable process; own FFmpeg tree | SEC-001 | L | malformed-media suite, time/size/duration caps, process-tree termination |
| Add complete resource ceilings for `.hws` and backup | SEC-003 | M | member/count/expanded-size/ratio/record/free-space tests |

Gate 0: no P0/P1 correctness path remains untriaged; all product changes require regression tests; original-file and raw-text invariants preserved.

## Phase 1 — test and engineering validation

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Create deterministic Hermes CI profile with Torch and explicit skip policy | QA-001 | M | mandatory model/config/forward/integration jobs on supported platform |
| Enforce manifest role, unique IDs, strict booleans and cross-split speaker/source/document/audio disjointness | ENG-002, IP-001 | L | adversarial leakage tests and immutable split report |
| Fingerprint audio content and corpus/license evidence, not mutable path only | ENG-003 | L | relocation-stable snapshot; in-place audio change invalidates digest |
| Reject NaN/Inf and invalid frequency/timestamp/config bounds | ENG-004 | S | property/boundary tests |
| Replace downsampling with sourced anti-aliasing implementation and parity tests | ENG-005 | M | frequency-response/aliasing fixtures and authoritative source |
| Detect CTC repeated-token infeasibility and count rejected/zeroed samples | ENG-006 | M | repeated-token boundary tests and training counters |
| Correct/freeze WER/CER/language/RTF/confidence semantics | ENG-007 | M | metric specification, golden cases, calibrated/renamed confidence |
| Add real behavioral tests for recorder, GUI state, backup shutdown, catalog corruption and cloud diagnostics | QA-002 | L | coverage report and failure-injection suite |

Gate 1: closed-test methodology is leakage-resistant; every release-critical calculation has source, units, golden examples and boundary evidence.

## Phase 2 — architecture stabilization

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Split Tk orchestration into UI state, async operations and domain services | ARC-001 | L | no direct heavy I/O/network/model operation on Tk thread |
| Coordinate shutdown with worker, recording and backup/restore state machine | REL-003 | L | deterministic close/recovery tests |
| Add filesystem/SQLite reconciliation for retention and model catalog | REL-004 | M | failure-injection proves recoverable consistent state |
| Close/join multiprocessing queues and define worker resource ownership | REL-005 | S | restart soak test, stable handle/thread count |
| Separate cloud vs local diagnostics readiness contracts | COR-003 | S | doctor tests for each engine and offline/key state |
| Remove/migrate unused public settings and stale compatibility paths | MNT-001 | S | migration note and backward-compatibility tests |

Gate 2: modules have explicit ownership, state transitions are recoverable, and UI remains responsive under worst supported operations.

## Phase 3 — performance and resource optimization

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Benchmark cold/warm RTF, memory, VRAM, model load and export on support matrix | PERF-001 | M | versioned reproducible benchmark artifacts |
| Stream recording and backup JSONL; add disk/RAM budgets | REL-001, SEC-003 | M | bounded resource tests |
| Add Hermes decoder KV cache/batching strategy after correctness freeze | PERF-001 | L/XL | measured same-output performance gain |
| Define checkpoint best-model/retention/pruning and storage budget | PERF-001 | M | deterministic retention policy and recovery test |
| Validate BF16/device capability and DDP/padding normalization | ENG-008 | L | cross-device consistency and resource matrix |

Gate 3: performance claims have measured baselines; supported inputs cannot exceed documented budgets without a safe rejection.

## Phase 4 — commercial architecture foundation

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Freeze product/SKU/support model: local desktop first, optional enterprise/offline | COM-001 | M | approved product architecture decision record |
| Validate target segment/JTBD, buyer vs user, alternatives, channel and willingness-to-pay before treating paid desktop as market-ready | COM-001, PERF-001 | M | evidence-backed product hypothesis and go/no-go criteria |
| Resolve `107 Voice Studio` project alias vs `Hermes Voice Studio` customer-facing brand | COM-001, IP-001 | S | approved name/trademark/package/display mapping |
| Define signed update/model trust root, key rotation/revocation and rollback | SEC-002, REL-006 | L | threat-reviewed signing ceremony and recovery drill |
| Define customer data ownership/export/delete/retention/encrypted backup | PRV-002 | L | policy mapped to tested controls; legal review flagged |
| Complete third-party/native/model/corpus IP inventory | IP-001, SUP-001 | L | per-artifact notices, source obligations, specialist review |
| Create support matrix, diagnostics schema, incident/vulnerability channels | OPS-001 | M | support playbooks and redaction verification |

Gate 4: target delivery model, trust root, data policy and IP boundaries approved before entitlement or public release engineering.

## Phase 5 — licensing, entitlement and optional billing

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Design signed offline-capable seat/device/site entitlement with transfer/revoke/grace | COM-001 | L | threat model, clock/offline/failure/recovery tests |
| Define SKU/features without sending audio/transcripts for license validation | COM-001 | M | privacy review and feature-boundary tests |
| If subscription is approved, add minimal account/billing control plane | COM-001 | XL | auth/session/RBAC, invoices/tax/refund/provider webhook and audit design |
| If enterprise floating license is approved, design availability/offline behavior | COM-001 | XL | partition/failover/clock abuse and admin workflows |

Gate 5: entitlement cannot cause data loss or silently disable paid offline use; billing begins only after separate architecture/user approval.

## Phase 6 — production hardening

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Lock platform dependencies with hashes; produce SBOM/license payload | SUP-001 | L | repeatable clean build with exact graph |
| Pin CI actions and attest source/build/dependency identity | CI-001, REL-006 | M | signed provenance bound to clean commit |
| Signed/notarized installers, update rollback and model signatures | SEC-002, REL-006 | XL | independent signature verification and rollback drill |
| Add secret/history/artifact scan and private vulnerability reporting | SUP-001, OPS-001 | M | CI gate plus incident/revocation procedure |
| Execute physical Windows/macOS/device/codecs/cloud acceptance | QA-002, REL-006 | L | immutable evidence pack, no simulated PASS |
| Run backup corruption, crash, disk-full, upgrade/downgrade and clean-machine drills | REL-003 | L | restored customer data and documented recovery bounds |

Gate 6: release candidate is reproducible, signed, license-reviewed, recoverable and accepted on every supported platform.

## Phase 7 — commercial release readiness

| Work item | Findings | Complexity | Exit evidence |
|---|---|---:|---|
| Approve EULA/privacy/support/LTS/EOL/security policy with specialists | COM-001, PRV-002, IP-001 | M | legal/product/security sign-off |
| Establish support/onboarding/update/rollback/customer communication | OPS-001 | L | operational rehearsal and ownership roster |
| Measure unit economics and set sustainable pricing/limits | PERF-001 | M | measured cost model; no invented price inputs |
| For Hermes pack only: licensed corpus, closed benchmark, model card, signed pack and compatibility matrix | ENG-002..008, IP-001 | XL | measured WER/CER/RTF/RAM and reproducible provenance |
| Independent release review | all | M | no open P0/P1; accepted residual risk and signed evidence index |

Gate 7: commercial release is an explicit human decision. Passing source tests alone is insufficient.

## Ordering constraints

- Do not begin SaaS/multi-tenant work before the desktop commercial model is approved.
- Do not implement entitlement before product/SKU/offline/privacy decisions.
- Do not publish Hermes quality claims before leakage-resistant closed-test evidence.
- Do not solve signature/authenticity with hashes alone.
- Do not optimize inference before output semantics are frozen.
- Do not automate release of an unsigned or legally incomplete artifact.
