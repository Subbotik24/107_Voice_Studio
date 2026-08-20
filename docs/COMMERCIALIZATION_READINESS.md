# 107 Voice Studio — commercial readiness

## Current position

The repository is a credible local-first Test-RC foundation, not a sellable production package. Current readiness is **1/5**: the user workflow exists, but signed distribution, reproducible builds, entitlement, support lifecycle, privacy/IP evidence, unit economics and native acceptance are missing. Experimental Hermes adds future differentiation but currently increases legal, scientific and support risk.

## Recommended direction

### Preferred near-term technical hypothesis: signed local desktop

If market validation supports a paid product, ship the faster-whisper desktop as signed binaries with updates/support and a perpetual license or subscription/annual maintenance. Preserve offline use and do not require audio/transcript telemetry. This is the best technical fit for the current privacy/process architecture and does not depend on unproven Hermes weights; it is not evidence of demand, segment fit, competitive differentiation or willingness-to-pay.

Build a clean entitlement boundary so a later minimal cloud control plane can validate subscription/device state without receiving customer media. Enterprise offline/site licensing and managed signed model/dictionary packs are the strongest strategic extension.

### Keep Hermes as a separate product gate

Hermes/domain model packs may become defensible value only after:

- corpus and contributor rights audit;
- strict consent/license evidence and revocation process;
- immutable dataset/audio snapshots and leakage-resistant closed set;
- actual trained weights, model card, measured WER/CER/RTF/RAM;
- publisher-signed pack, compatibility/resource matrix and per-pack license.

Until then, describe Hermes as research/experimental. Do not make accuracy or commercial redistribution claims.

## Architecture options

| Option | Fit now | Required change | Security/privacy | Ops/cost | Recommendation |
|---|---:|---|---|---|---|
| A. Licensed local desktop | High technical fit; market fit unknown | signing, updater/rollback, entitlement, support, data/backup hardening | keeps media local; OS/device risk remains | lowest service cost; desktop support matrix | **validate as near-term hypothesis** |
| B. Enterprise offline/on-prem desktop | High strategic | signed offline/site entitlement, admin deployment, LTS/SLA | strongest privacy; customer endpoint governance | higher support/release complexity | build after A |
| C. Hybrid desktop + cloud control plane | Medium later | account/device entitlement, OIDC/admin portal, privacy-minimal API | new identity/network boundary; media can remain local | service availability, billing and incident cost | design-compatible, defer implementation |
| D. Managed multi-tenant SaaS STT | Low | new web/API, queues, object store, tenant DB, KMS, RBAC, quotas, billing, regional ops | highest data/controller/subprocessor burden | high compute/storage/egress/SRE cost | separate product/business case |
| E. OEM/SDK/model packs | Medium later | stable versioned SDK, redistribution/license terms, conformance suite | integrator trust and model supply chain | partner support and compatibility burden | only after contracts/evidence mature |

## Missing commercial product capabilities

- product/SKU/feature and support lifecycle definition;
- authenticated publisher identity and code/model signing;
- update channels, staged rollout, rollback and revocation;
- seat/device/site entitlement, activation, transfer, grace and recovery;
- EULA, privacy notice, security policy/contact and trademark/brand policy;
- deterministic supported OS/hardware/model matrix;
- onboarding, error/support path, diagnostics consent and incident handling;
- durable export/delete/retention/encrypted backup contract;
- release SBOM, notices, provenance and clean-build evidence;
- pricing inputs based on measured cost/support data.
- evidence-backed target segment, jobs-to-be-done, competitive alternatives, buyer/user distinction and willingness-to-pay.

No account, organization, role, quota, billing or tenant constructs exist today. That is acceptable for a local desktop, but it is not a SaaS foundation.

## Licensing and entitlement options

| Model | Design requirements | Main trade-off |
|---|---|---|
| Perpetual + paid updates | signed local license, major-version entitlement, transfer/revoke | simple/offline; revenue less recurring |
| Subscription desktop | periodic minimal validation, offline grace, clock/failure handling | recurring revenue; availability/privacy boundary |
| Device/seat | privacy-minimized device binding and recovery | enforceable; hardware changes/support friction |
| Floating/network | enterprise license service, cache/grace, admin reporting | flexible; service dependency and abuse surface |
| Site/offline enterprise | signed offline entitlement, expiry/renewal, audit contract | best privacy; manual operations and sales controls |
| SaaS/API usage | identity, tenant isolation, metering/quotas and billing ledger | scalable channel; highest platform/compliance cost |

Recommendation: offline-capable signed seat/device/site format with generous recovery/grace. Never bind entitlement to transcript/audio content.

## Billing readiness

There is no billing implementation, and none is recommended before a product decision. A subscription path would require plan/SKU versioning, provider integration, idempotent signed webhooks, tax/invoice/refund/cancellation/trial states, entitlement projection and audited reconciliation. Usage billing additionally requires a precise billable unit and privacy-safe metering semantics. Local minutes cannot be trusted as billable server usage without a deliberate anti-abuse/privacy design.

## Data, privacy and compliance gaps

Potential personal/confidential data: audio, voices, transcript contents, source/model paths, dictionary terms, benchmark references, backups and optional cloud request metadata.

Required before commercial promises:

- document controller/processor roles for each delivery model;
- data inventory, purpose, lawful basis, retention/export/delete and breach process;
- explicit cloud provider/subprocessor/data-region/retention disclosures;
- owner-only local ACL/mode checks and secure custom-directory warnings;
- authenticated encrypted portable backup with key recovery policy;
- privacy-safe diagnostics allowlist and customer-controlled submission;
- explicit clipboard behavior: no silent sensitive-text copy, clear OS history/sync warning and managed-device guidance;
- if SaaS: tenant-scoped storage/cache/queues/logs/KMS, regional/data deletion and DPA controls.

GDPR and other legal applicability depend on market, customer and processing role. Specialist legal review is required; this document does not assert compliance.

## Security gaps blocking sale

- models, registries and `.hws` prove integrity but not publisher authenticity;
- untrusted media reaches native parser in the parent process;
- backup/model archives lack complete count/expanded-size/ratio/free-space limits;
- SQLite/audio/backups are plaintext and rely on OS account defaults;
- CLI can place an API key in argv/history;
- diagnostics can expose absolute paths outside home;
- dependency/action/build graph is mutable and unsigned;
- no production code-signing/notarization/update trust root or incident/revocation drill.

See [audit/SECURITY_REVIEW.md](audit/SECURITY_REVIEW.md).

## IP and third-party readiness

The code is MIT-licensed, which permits redistribution under its terms but does not by itself create source exclusivity or prove ownership of every contribution, dependency, native binary, model, tokenizer corpus or training dataset. Commercial value must come from trusted binaries, brand, updates, support, enterprise deployment and separately licensed models/services.

Before distribution, produce a resolved per-artifact inventory covering direct/transitive Python packages, PyAV/FFmpeg build configuration, CTranslate2, PyTorch, sounddevice, pynput, keyring, OpenAI SDK, fonts/assets, packaged tokenizer corpus, faster-whisper model revision/license, Hermes corpus/weights and required notices/source-offer obligations. `THIRD_PARTY_NOTICES.md` is currently an index, not legal clearance.

Specialist review is required for FFmpeg LGPL/GPL implications, model/data derivative and redistribution rights, EULA compatibility, contributor IP chain and trademark use.

## Release, deployment and update requirements

Production artifact criteria:

1. clean reviewed source commit and protected release approval;
2. locked/hashes platform dependency graph and reproducible wheel/native inputs;
3. compile/lint/tests/security/Hermes-required jobs with no uncontrolled skips;
4. SBOM, license/notices payload and source/build/dependency attestation;
5. signed/notarized installers and publisher-signed models/registry;
6. staged update channel with rollback, key rotation and revocation;
7. physical Windows/macOS/device/codec/clean-machine acceptance;
8. support matrix, vulnerability channel, incident and data-recovery drill;
9. no open P0/P1, or explicit owner-signed residual-risk acceptance.

## Supportability

Positive: doctor/health concepts, version info, integrity verification, backups/recovery and diagnostics intent.

Missing: editor dirty-state/autosave protection, stable redacted diagnostics schema, local privacy-safe security event trail, crash consent, support correlation IDs, SLA/severity/on-call ownership, LTS/EOL, update/rollback and known-issues channels, migration/downgrade support, private vulnerability reporting and trained restore/key-loss procedures.

## Customer isolation

Local desktop isolation is the OS account/device. If multiple OS users share a directory, current app-level controls are insufficient. Enterprise shared storage requires explicit ACL, encryption and locking/reconciliation.

For future SaaS, every data plane component must be tenant-scoped: identity, DB/object keys, queues, caches, logs, backups, KMS, rate/usage records and support access. This cannot be safely added as a thin wrapper around current local SQLite.

## Unit economics

Do not price from configuration constants. Collect:

- cold/warm RTF and concurrent throughput per supported CPU/GPU;
- RAM/VRAM, install/model/download/storage sizes and update bandwidth;
- OpenAI/API calls, bytes/minutes and retry/error rates for opted-in features;
- build/signing/notarization/distribution and third-party license costs;
- support tickets, handling time, restore/activation/device-transfer frequency;
- training node-hours, checkpoint storage and corpus operations for Hermes;
- SaaS only: queue wait, compute occupancy, storage/egress, observability and abuse losses.

Useful formulas:

- local/cloud compute cost per audio hour ≈ `RTF × node cost/hour` plus storage/egress/support allocation;
- training cost ≈ `node count × node rate × wall-clock hours`;
- support allocation ≈ `support effort × fully loaded support cost / paid unit`.

Inputs are currently unknown; no monetary estimate is justified.

## Commercial go/no-go gate

Current decision: **NO-GO for public commercial production release; GO for controlled Test-RC hardening after explicit approval**.

Near-term desktop can move to commercial release review only after the Phase 0/1 desktop blockers, signed/reproducible distribution, privacy/IP inventory, native acceptance, support/update/rollback and entitlement decisions are evidenced **and** customer discovery validates a target segment, urgent job, alternatives, channel and willingness-to-pay. Hermes packs have an additional independent model/data/quality gate.

## Product identity decision

The repository/package/GUI currently use **Hermes Voice Studio**, while the GitHub repository and this audit use **107 Voice Studio**. Treat `107 Voice Studio` as the project/repository alias, not a confirmed customer-facing brand. Before signing, licensing or publishing, approve one product name, trademark/brand policy, executable/package/display-name mapping and migration plan; then update all surfaces atomically.
