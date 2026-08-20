# Findings register — 107 Voice Studio

Baseline: `main@fffa50b6bc26fa2e7fa2150f2260ae873a5cf511`. `P0` confirmed: **none**.

Impact codes use `0–3`: `C` correctness, `S` security/privacy, `Cm` commercial, `P` performance/cost, `E` engineering accuracy, `M` maintainability. Evidence labels are `Observed`, `Inferred`, `Unknown` or `Blocked`.

## P1 — high / release blockers

### COR-002 — wrong JSON setting types escape recovery

- **Category / severity:** Correctness/reliability; P1. Impact `C3 S1 Cm2 P0 E0 M1`.
- **Evidence / affected:** Observed in `src/hermes_voice_studio/models.py:102-142`, `src/hermes_voice_studio/config.py:40-50`, `src/hermes_voice_studio/app.py:44-49`, `src/hermes_voice_studio/cli.py:457-463`; current tests cover malformed object/range but not types.
- **Observed / expected:** Valid JSON such as `{"model":1}` can raise `AttributeError`/`TypeError`, preventing GUI startup or producing CLI traceback; corrupted/incompatible settings should be normalized/migrated or produce one recoverable message.
- **Root hypothesis / confidence:** Dataclass construction trusts JSON values while validation assumes Python types; high.
- **Impact:** Paying customer can become unable to launch after manual edit, restore or version drift.
- **Recommended / alternative:** Strict typed decode with per-field migration/default and quarantined bad file; alternative schema validator before `Settings.from_dict`.
- **Verification / dependencies / commercial relevance:** Wrong-type/null/list/overflow matrix across GUI/CLI/backup restore; no external dependency; desktop RC blocker.

### COR-004 — unsaved editor changes can be silently lost

- **Category / severity:** Correctness/UX/data safety; P1. Impact `C3 S0 Cm3 P0 E0 M2`.
- **Evidence / affected:** Observed in `src/hermes_voice_studio/app.py:480-484,596-599,658-669,1204-1211`; selecting history replaces editor content, save is explicit, and close destroys the app without a dirty-state check. No regression tests cover unsaved navigation/close.
- **Observed / expected:** Manual customer edits can disappear when another record is selected or the app closes; edited work needs a visible dirty state and safe save/discard/cancel or durable autosave contract.
- **Root hypothesis / confidence:** Editor buffer and persisted `corrected_text` have no explicit synchronization/state machine; high.
- **Impact:** Paying customer loses work without an error, undermining transcript history as a dependable record.
- **Recommended / alternative:** Track persisted revision/dirty buffer and prompt on navigation/close with crash-safe draft; alternative autosave every validated change with versioned undo.
- **Verification / dependencies / commercial relevance:** Behavioral tests for switch/close/crash/save failure and AI cleanup/undo interaction; UI state design; desktop commercial blocker.

### ENG-001 — Hermes merged overlap text is discarded

- **Category / severity:** ML integration/correctness; P1, `ENG-HIGH`. Impact `C3 S0 Cm3 P1 E3 M1`.
- **Evidence / affected:** Observed: `src/hermes_whisper/decoding.py:181-200` computes merged text; `src/hermes_voice_studio/engines/hermes.py:56-70` emits original overlapping chunks; `src/hermes_voice_studio/engines/base.py:20-23` joins them; `src/hermes_voice_studio/service.py:44-45` persists immutable raw text.
- **Observed / expected:** Audio beyond one chunk can duplicate overlap text and subtitle time ranges; desktop must consume one canonical merged transcript/segment contract.
- **Root hypothesis / confidence:** Integration adapted segment output but ignored `result.text`; high.
- **Impact:** Incorrect customer transcript becomes immutable raw evidence; invalidates Hermes quality claims.
- **Recommended / alternative:** Return canonical merged text plus reconciled non-overlapping segments; alternatively disable chunked Hermes desktop use until alignment exists.
- **Verification / dependencies / commercial relevance:** >30 s golden integration/SRT fixture; depends on segment merge design; blocker for any Hermes-enabled release.

### ENG-002 — dataset leakage and split roles are not enforced

- **Category / severity:** Data/ML validity; P1 pre-model-release, `ENG-HIGH`. Impact `C2 S2 Cm3 P1 E3 M1`.
- **Evidence / affected:** Observed in `src/hermes_whisper/manifest.py:39-70`, `src/hermes_whisper/cli.py:54-72,119-139,185-207`; stronger requirements exist only in `docs/DATASET_SCHEMA.md:91-100` and `docs/TRAINING_RUNBOOK.md:5-14`.
- **Observed / expected:** Train/validation/test, tokenizer inputs, unique record IDs and speaker/source/document/audio disjointness are not guaranteed; closed metrics require automated role and leakage gates.
- **Root hypothesis / confidence:** Governance was documented after a record-local validator design; high.
- **Impact:** Inflated WER/CER, data privacy/IP contamination and unusable commercial model evidence.
- **Recommended / alternative:** Snapshot-level validator with strict roles, stable IDs, group/content-hash/near-duplicate reports; alternative external immutable data registry that emits signed manifests.
- **Verification / dependencies / commercial relevance:** Adversarial cross-split fixtures and reviewer-signed split report; depends on dataset identity policy; model-pack blocker.

### ENG-003 — manifest fingerprint does not bind audio content

- **Category / severity:** Reproducibility/provenance; P1 pre-model-release, `ENG-HIGH`. Impact `C2 S1 Cm3 P1 E3 M1`.
- **Evidence / affected:** Observed in `src/hermes_whisper/manifest.py:158-168`; resume uses the digest in `src/hermes_whisper/trainer.py:259-260`.
- **Observed / expected:** In-place audio replacement keeps the digest while relocating identical data changes it; a dataset identity must be content-complete and relocation-stable.
- **Root hypothesis / confidence:** Metadata/path hash was treated as dataset fingerprint; high.
- **Impact:** Resume, benchmark and licensing evidence cannot prove which audio trained a model.
- **Recommended / alternative:** Hash normalized manifest plus each audio byte digest and provenance evidence; alternative content-addressed immutable dataset store.
- **Verification / dependencies / commercial relevance:** Move/replace/missing-file regression and deterministic snapshot inventory; depends on corpus storage policy; model release blocker.

### ENG-004 — non-finite numeric values pass manifest validation

- **Category / severity:** Numerical validation; P1, `ENG-HIGH`. Impact `C3 S0 Cm2 P1 E3 M1`.
- **Evidence / affected:** Observed by direct probe: `duration_seconds=NaN` accepted; comparisons in `src/hermes_whisper/manifest.py` and `src/hermes_whisper/data.py` do not reject non-finite values.
- **Observed / expected:** NaN can poison totals/RTF/metrics; every numeric config/record field must be finite and within a sourced resource domain.
- **Root hypothesis / confidence:** range comparisons assumed finite IEEE values; high.
- **Impact:** False PASS, unstable training/reporting and resource surprises.
- **Recommended / alternative:** Central finite/range schema validation; alternative typed validation library with explicit `isfinite` constraints.
- **Verification / dependencies / commercial relevance:** NaN/±Inf/negative/huge/property tests across config, manifests and tokenizer; no dependency; engineering evidence blocker.

### ENG-005 — linear downsampling lacks anti-aliasing

- **Category / severity:** DSP/model quality; P1 before production Hermes, `ENG-HIGH`. Impact `C2 S0 Cm3 P1 E3 M1`.
- **Evidence / affected:** Observed in `src/hermes_whisper/audio.py:104-115`; sample-count arithmetic is correct but no low-pass filter precedes downsampling.
- **Observed / expected:** High-frequency content can alias into features; production training/inference must share a sourced, anti-aliased resampler with defined frequency response.
- **Root hypothesis / confidence:** minimal dependency-free interpolation was mistaken for general resampling; high.
- **Impact:** Dataset/device-dependent quality loss and non-comparable metrics.
- **Recommended / alternative:** Reviewed band-limited resampler with parity fixtures; alternative require canonical 16 kHz input and reject all other rates.
- **Verification / dependencies / commercial relevance:** sine sweep/impulse/alias tests and authoritative source; dependency choice/legal packaging; Hermes blocker.

### ENG-006 — CTC infeasibility can silently become zero loss

- **Category / severity:** Training correctness; P1 pre-model-release, `ENG-HIGH`. Impact `C2 S0 Cm3 P0 E3 M1`.
- **Evidence / affected:** Observed: `src/hermes_whisper/data.py:161-168` checks only target≤encoder length; `src/hermes_whisper/losses.py:55-63` uses `zero_infinity=True`.
- **Observed / expected:** Adjacent repeated target tokens require extra frames; invalid samples may produce zero CTC gradient without an explicit counter.
- **Root hypothesis / confidence:** incomplete CTC feasibility condition plus convenience flag; high.
- **Impact:** Hidden training-data loss and misleading convergence/quality.
- **Recommended / alternative:** enforce `input >= target + adjacent repeats` and report rejected/zeroed counts; alternative remove CTC for infeasible batches with explicit policy.
- **Verification / dependencies / commercial relevance:** repeated-token boundary fixtures and monitored training smoke; Torch environment; model-quality blocker.

### REL-001 — recording is unbounded and capture degradation can be silent

- **Category / severity:** Reliability/performance/privacy; P1. Impact `C2 S1 Cm2 P3 E0 M1`.
- **Evidence / affected:** Observed in `src/hermes_voice_studio/recorder.py:12-18,31-72`; continuous UI at `src/hermes_voice_studio/app.py:381-414`; callback ignores sounddevice `status` at `src/hermes_voice_studio/recorder.py:34-37`; no behavioral recorder tests.
- **Observed / expected:** All frames accumulate in an unbounded queue, then list/concatenation/bytes duplicate data; overflow/dropout status is discarded. Recording must be streamed, bounded/durable and surface capture integrity warnings.
- **Root hypothesis / confidence:** prototype in-memory callback was exposed as indefinite mode without a health/error channel; high.
- **Impact:** RAM exhaustion, lost recording, silently degraded transcript and sensitive data residue.
- **Recommended / alternative:** private streamed WAV with duration/byte/disk caps, status aggregation/visible failure and crash recovery; alternative remove continuous mode and cap clips.
- **Verification / dependencies / commercial relevance:** long-record/disk-full/status-overflow/stop/close tests and measured peak RAM; OS audio; desktop RC blocker.

### REL-002 — cancel/timeout does not cover prepare/hash/copy

- **Category / severity:** Reliability/UX; P1. Impact `C2 S0 Cm2 P2 E0 M1`.
- **Evidence / affected:** Observed in `src/hermes_voice_studio/jobs.py:117-146`, `src/hermes_voice_studio/service.py:30-35`, `src/hermes_voice_studio/storage.py:93-104`, `src/hermes_voice_studio/app.py:473-476`; tests cancel only after tiny prepare.
- **Observed / expected:** Large/slow/removable media can make cancellation ineffective and timeout starts late; all user-visible job phases need cancellation and one total deadline.
- **Root hypothesis / confidence:** worker lifecycle boundary starts after synchronous preparation; high.
- **Impact:** apparent hang and unnecessary I/O/storage work.
- **Recommended / alternative:** chunked cancellable hash/copy and bounded probe under job state/deadline; alternative separate visible import phase with independent cancel.
- **Verification / dependencies / commercial relevance:** slow filesystem/large input/cancel race tests; service/job redesign; supportability blocker.

### REL-003 — close/daemon lifecycle can strand temp audio or interrupt restore

- **Category / severity:** Reliability/privacy/data safety; P1. Impact `C3 S2 Cm3 P1 E0 M2`.
- **Evidence / affected:** Observed temp flow `src/hermes_voice_studio/app.py:402-469,277-315,1204-1211`; inferred restore window `src/hermes_voice_studio/app.py:1127-1193`, `src/hermes_voice_studio/backup.py:212-223`.
- **Observed / expected:** Tk destruction can prevent temp cleanup; daemon shutdown can stop restore between directory swaps. App close must own, await/cancel and recover every resource transition.
- **Root hypothesis / confidence:** cleanup is event-loop mediated and background tasks lack coordinated shutdown; high for temp, medium for interpreter restore timing.
- **Impact:** private residue, unavailable active data root and manual recovery burden.
- **Recommended / alternative:** explicit resource registry/state machine, non-daemon restore with close gate and startup recovery; alternative prohibit close during critical restore and clean orphan temp files at startup.
- **Verification / dependencies / commercial relevance:** forced close/crash at every transition; OS/process tests; data-safety blocker.

### SEC-001 — untrusted media parses in parent/native boundary

- **Category / severity:** Security/reliability; P1. Impact `C2 S3 Cm3 P2 E0 M2`.
- **Evidence / affected:** Observed architecture `src/hermes_voice_studio/service.py:30-35`, `src/hermes_voice_studio/media.py:29-99`, `src/hermes_voice_studio/jobs.py:68-105,125-170`, `src/hermes_voice_studio/engines/hermes.py:38-54`.
- **Observed / expected:** PyAV decodes before worker isolation; FFmpeg descendant lacks direct deadline/tree ownership. Untrusted native parsing should be disposable and resource-constrained.
- **Root hypothesis / confidence:** model worker was considered sufficient isolation, but prepare and conversion cross it; high architecture, exploitability unproven.
- **Impact:** crafted/corrupt media can hang/crash; native RCE is a dependency-contingent hypothesis.
- **Recommended / alternative:** restricted probe/convert subprocess, byte/duration/output caps, Job Object/process group and pinned native builds; alternative narrow accepted formats to safely parsed WAV.
- **Verification / dependencies / commercial relevance:** fuzz/malformed fixtures, descendant/process-limit tests, dependency advisories; commercial security blocker.

### SEC-002 — models and release channel lack publisher authenticity

- **Category / severity:** Security/supply chain; P1. Impact `C2 S3 Cm3 P1 E1 M2`.
- **Evidence / affected:** Observed in `src/hermes_voice_studio/model_release.py:20-59`, `src/hermes_voice_studio/model_catalog.py:43-57,104-116,214-238,293-336`, `src/hermes_whisper/bundle.py:172-201,274-303`; existing docs explicitly state SHA is not a signature.
- **Observed / expected:** Hash/size/path checks can validate a manifest produced by an attacker; registry/revision/load trust is not fully pinned. Commercial channel requires authenticated publisher and revocation.
- **Root hypothesis / confidence:** integrity controls delivered before signing/trust-root architecture; high.
- **Impact:** tampered/crashed/wrong model, transcript integrity compromise and untrusted native workload.
- **Recommended / alternative:** signed registry/packs with offline key, rotation/revocation, mandatory owner/revision/HTTPS and load-time verify; alternative distribute models only inside signed installer.
- **Verification / dependencies / commercial relevance:** bad signer/key rotation/revoked pack/rollback tests; signing operations/HSM; production blocker.

### SEC-003 — backup and `.hws` resource ceilings are incomplete

- **Category / severity:** Security/reliability; P1. Impact `C2 S2 Cm2 P3 E0 M1`.
- **Evidence / affected:** Observed in `src/hermes_whisper/bundle.py:112-151,172-191,218-270`, `src/hermes_voice_studio/backup.py:106-192`; stronger bounded pattern exists in `src/hermes_voice_studio/model_release.py:16-18,98-121`.
- **Observed / expected:** Model weights, member count/total expanded size/compression ratio/transcript line count/free space are not fully bounded; untrusted portable formats need versioned budgets before allocation/extraction.
- **Root hypothesis / confidence:** integrity/path validation was completed without a cross-format resource policy; high.
- **Impact:** RAM/disk/CPU exhaustion or interrupted restore.
- **Recommended / alternative:** central archive budget + streaming JSONL and tensor dimension preflight; alternative accept only locally created signed archives.
- **Verification / dependencies / commercial relevance:** zip-bomb/huge metadata/member/low-disk tests; format versioning; blocker if files are exchanged.

### PRV-002 — sensitive local data and backup lack application-level at-rest protection

- **Category / severity:** Privacy/security; P1 commercial. Impact `C1 S3 Cm3 P1 E0 M1`.
- **Evidence / affected:** Observed absence in `src/hermes_voice_studio/storage.py:33-50,93-104,135-169`, `src/hermes_voice_studio/backup.py:31-97`, `src/hermes_voice_studio/config.py:53-62`; plaintext SQLite/audio/backup and no explicit private ACL/mode enforcement.
- **Observed / expected:** Confidentiality depends on OS/profile defaults; commercial shared/custom path and portable backup contracts need explicit permissions and authenticated encryption.
- **Root hypothesis / confidence:** single-user local Test-RC threat model; high absence, environment-dependent impact.
- **Impact:** disclosure after shared directory/device loss/backup transfer and unsupported privacy promises.
- **Recommended / alternative:** owner-only permission checks, secure path warnings, encrypted backup with recovery/rotation; alternative mandate/document full-disk encryption and disallow shared paths while deferring portable encryption.
- **Verification / dependencies / commercial relevance:** supported-OS ACL/mode, key loss/rotation/restore tests; product/legal policy; commercial blocker for sensitive markets.

### PRV-003 — automatic clipboard copy expands the data boundary

- **Category / severity:** Privacy/product behavior; P1 commercial / P2 controlled personal use. Impact `C1 S3 Cm3 P0 E0 M1`.
- **Evidence / affected:** Observed: `Settings.auto_copy=True` at `src/hermes_voice_studio/models.py:94`; successful results are copied at `src/hermes_voice_studio/app.py:512-513,570-573`. Clipboard/history/sync behavior is controlled by the OS and other applications.
- **Observed / expected:** A transcript may leave the app’s storage boundary automatically after success; sensitive workflows need explicit action/default-off and a clear OS clipboard-history/sync disclosure.
- **Root hypothesis / confidence:** convenience default was modeled as local behavior without treating clipboard as external shared state; high for app behavior, environment-dependent disclosure.
- **Impact:** confidential transcript can be retained by clipboard manager, synchronized to another device or read by another process.
- **Recommended / alternative:** default-off explicit copy with preview/short-lived clear option and admin policy; alternative preserve auto-copy only in an explicitly named non-sensitive profile with first-use warning.
- **Verification / dependencies / commercial relevance:** settings migration, success/error/cancel tests, supported-OS clipboard/history guidance; product privacy decision; blocker for strong privacy-first commercial claim.

### QA-001 — Hermes path is not deterministic in CI

- **Category / severity:** QA/release; P1 before Hermes release. Impact `C2 S1 Cm3 P1 E3 M2`.
- **Evidence / affected:** Observed workflow installs `.[dev,cloud]`, not `[hermes]`; Torch-dependent tests can be skipped/uncontrolled; no train/evaluate/integration gate.
- **Observed / expected:** Green CI does not prove Hermes config/model/forward/desktop integration; a claimed feature requires explicit supported environment and skip-fail policy.
- **Root hypothesis / confidence:** desktop release track and research tests share one suite without deterministic dependency matrix; high.
- **Impact:** severe model/integration regression can ship behind green checks.
- **Recommended / alternative:** separate mandatory Hermes CI lane with pinned Torch/hardware-neutral smoke and integration; alternative formally exclude Hermes from distributed product/build.
- **Verification / dependencies / commercial relevance:** inspect collected/skipped tests and publish immutable job evidence; CI/Torch budget; model-pack blocker.

### REL-006 — artifact evidence is not bound to source/dependencies/builder

- **Category / severity:** Release/supply chain; P1. Impact `C2 S3 Cm3 P1 E1 M2`.
- **Evidence / affected:** Observed in `scripts/build_test_rc.sh:17-23,90-114`, `scripts/create_release_manifest.py:72-113`, `scripts/build_windows.ps1:97-131`, legacy `scripts/build_mac.sh:15-24`, `.github/workflows/release.yml`.
- **Observed / expected:** Superficial PASS JSON/checksums do not prove commit/tree/dependency/SBOM/builder identity; production evidence must be bound and signed.
- **Root hypothesis / confidence:** manual Test-RC workflow evolved without a reproducibility/attestation threat model; high.
- **Impact:** stale/fabricated acceptance or drifting build can be distributed and cannot be supported/revoked confidently.
- **Recommended / alternative:** one authoritative clean-tree builder, exact locks, per-OS manifest/SBOM/attestation/signing/approval; alternative reproducible external release service with verified source input.
- **Verification / dependencies / commercial relevance:** two clean builds, signature/source/SBOM compare and rollback drill; release infrastructure; production blocker.

### SUP-001 — no reproducible dependency/SBOM/license state

- **Category / severity:** Dependencies/IP/supply chain; P1. Impact `C2 S3 Cm3 P1 E0 M2`.
- **Evidence / affected:** Observed broad ranges in `pyproject.toml`, no lock/hashes/SBOM, `THIRD_PARTY_NOTICES.md` only an index; current local audit tools absent. Prior remote pip-audit applies only to one resolver snapshot.
- **Observed / expected:** Exact runtime vulnerability/license/native-binary graph cannot be reconstructed; each released artifact requires immutable dependencies, inventory, notices and source obligations.
- **Root hypothesis / confidence:** source-development convenience precedes distribution engineering; high.
- **Impact:** vulnerable or legally incomplete artifact, non-reproducible support failures.
- **Recommended / alternative:** per-platform lock/constraints with hashes, reviewed wheelhouse, CycloneDX/SPDX SBOM and license payload; alternative hermetic container/builder with signed resolved graph.
- **Verification / dependencies / commercial relevance:** clean offline build, `pip check`/audit/license scan and manual native/model review; specialist legal input; commercial blocker.

### IP-001 — corpus/model/asset rights evidence is insufficient

- **Category / severity:** IP/data governance; P1 before model commercialization. Impact `C1 S2 Cm3 P0 E2 M1`.
- **Evidence / affected:** Observed: `src/hermes_whisper/manifest.py:13-24` accepts non-empty free text/truthy consent; `.hws` need not carry license/model card/provenance; no actual commercial corpus/weights. MIT covers repository code, not every dependency/data/model right.
- **Observed / expected:** Derivative-model/redistribution/territory/expiry/revocation and contributor ownership are not established; every commercial artifact needs attributable rights evidence.
- **Root hypothesis / confidence:** research schema is not a legal rights registry; high gap, legal compatibility unknown.
- **Impact:** inability to sell/distribute model, takedown/privacy/contract risk.
- **Recommended / alternative:** typed evidence registry, source/license/consent version, rights scope/revocation, model-card/pack-license payload and specialist audit; alternative train only on owned commissioned corpus.
- **Verification / dependencies / commercial relevance:** legal/data-owner sign-off and trace from each weight to immutable dataset; corpus acquisition; absolute model-pack blocker.

### COM-001 — commercial identity, entitlement and support system are absent

- **Category / severity:** Product/commercial architecture; P1 for sale. Impact `C1 S2 Cm3 P2 E0 M2`.
- **Evidence / affected:** Observed repository has local single-user desktop only; no account/org/role/entitlement/billing/update/support lifecycle constructs; release remains unsigned Test-RC.
- **Observed / expected:** A paying user needs trusted delivery, license recovery, updates/rollback, support, data policy and version/support matrix; SaaS additionally needs tenant platform capabilities.
- **Root hypothesis / confidence:** engineering prototype preceded approved business/delivery model; high.
- **Impact:** cannot reliably sell, activate, update or support the product.
- **Recommended / alternative:** approve local signed desktop first with offline-capable entitlement/support; alternative enterprise site-license process before automated entitlement.
- **Verification / dependencies / commercial relevance:** product/legal/security architecture decision and end-to-end purchase/activate/offline/transfer/update/recover rehearsal; business approval; core commercialization blocker.

## P2 — medium findings

### COR-003 — cloud engine health uses local-model rules

- **Category/severity:** correctness/operations P2; impact `C2 S0 Cm2 P0 E0 M1`.
- **Evidence/affected:** `src/hermes_voice_studio/diagnostics.py:99-139`; OpenAI dependency/key readiness is omitted and cloud model is looked up locally.
- **Observed/expected/root/confidence:** doctor can report a valid cloud setup incomplete and miss its real requirements; shared non-Hermes branch conflates engine types; high.
- **Impact/recommendation/alternative:** misleading support output; per-engine readiness providers, or mark cloud status unknown without live check.
- **Verification/dependencies/commercial:** table-driven engine/offline/key/SDK/model tests; none; supportability.

### ENG-007 — metric/confidence semantics can be misleading

- **Category/severity:** engineering metrics P2, `ENG-HIGH`; impact `C2 S0 Cm3 P0 E3 M1`.
- **Evidence/affected:** `src/hermes_whisper/evaluation.py:33-65`, `src/hermes_whisper/decoding.py:79-96`, `src/hermes_whisper/trainer.py:369-391`, `src/hermes_whisper/metrics.py`; reference-language accuracy is tautological, RTF scope differs, confidence uncalibrated, batch means biased.
- **Observed/expected/root/confidence:** labels imply stronger semantics than formulas; internal metrics grew without frozen measurement specification; high.
- **Impact/recommendation/alternative:** invalid quality/performance claims; define/golden-test metric scopes and calibrate or rename scores; alternatively omit unsupported values.
- **Verification/dependencies/commercial:** closed benchmark and calibration/reliability diagrams; trained model; blocks claims, not desktop local use.

### ENG-008 — padding/DDP, resume and timestamp contracts are incomplete

- **Category/severity:** ML reliability P2, `ENG-HIGH`; impact `C2 S0 Cm2 P2 E3 M2`.
- **Evidence/affected:** `src/hermes_whisper/data.py:131-163`, `src/hermes_whisper/model.py:55-76`, `src/hermes_whisper/trainer.py:116-147,277-301`, `src/hermes_whisper/checkpoint.py:94-100`, `src/hermes_whisper/tokenizer.py:149-154`; BatchNorm sees padding/rank-local stats, resume omits RNG/sampler position, timestamp may clip silently.
- **Observed/expected/root/confidence:** results can depend on batch/world/resume and mismatched maxima; training contracts are partial; medium-high.
- **Impact/recommendation/alternative:** non-reproducible quality; padding-safe normalization, exact resume state and config invariants, or restrict supported training mode.
- **Verification/dependencies/commercial:** continuous-vs-resume and cross-world golden runs; Torch/DDP hardware; model readiness.

### ARC-001 — Tk main thread owns heavy operations and oversized orchestration

- **Category/severity:** architecture/UX P2 (some operations P1 in use); impact `C2 S1 Cm2 P3 E0 M3`.
- **Evidence/affected:** `src/hermes_voice_studio/app.py` ~1,219 lines; network test `.hws` verify/model import/hash/catalog verify at `src/hermes_voice_studio/app.py:901-944,1018-1081` execute inline.
- **Observed/expected/root/confidence:** minutes-long work can freeze UI; feature growth concentrated in one controller; high.
- **Impact/recommendation/alternative:** perceived hang/race/maintenance cost; cancellable operation service + UI state machine, or disable expensive GUI actions and expose CLI only.
- **Verification/dependencies/commercial:** responsiveness/deadlock tests and measured event-loop latency; architecture stabilization; customer experience.

### REL-004 — filesystem and metadata transitions are not atomic

- **Category/severity:** reliability P2; impact `C2 S1 Cm2 P1 E0 M2`.
- **Evidence/affected:** storage retention `src/hermes_voice_studio/service.py:74-77`, `src/hermes_voice_studio/storage.py:365-392`; catalog promote/remove `src/hermes_voice_studio/model_catalog.py:149-176,338-355`.
- **Observed/expected/root/confidence:** failure can leave DB claiming retained missing audio or catalog pointing to missing/untracked directory; cross-resource actions lack journal/reconciliation; high.
- **Impact/recommendation/alternative:** inconsistent customer data/models; intent journal + startup reconciliation, or database-first tombstone/two-phase recoverable operations.
- **Verification/dependencies/commercial:** failure injection at every filesystem/SQLite/catalog step; schema/design approval; support/data integrity.

### REL-005 — multiprocessing queues are not explicitly closed/joined

- **Category/severity:** resource reliability P2; impact `C1 S0 Cm1 P2 E0 M1`.
- **Evidence/affected:** `src/hermes_voice_studio/jobs.py:68-105`; queues are replaced after terminate without explicit close/join/cancel.
- **Observed/expected/root/confidence:** long Windows sessions may accumulate feeder threads/handles; inferred medium confidence.
- **Impact/recommendation/alternative:** eventual resource exhaustion; explicit lifecycle closure/join, or reuse one bounded IPC channel.
- **Verification/dependencies/commercial:** cancel/timeout restart soak with handle/thread count; Windows; long-session quality.

### PRV-001 — diagnostics and temp lifecycle can expose private paths/audio

- **Category/severity:** privacy P2/P1 temp; impact `C1 S3 Cm2 P0 E0 M1`.
- **Evidence/affected:** `src/hermes_voice_studio/diagnostics.py:17-38,87-174`; mic temp lifecycle `src/hermes_voice_studio/app.py:402-469,277-315,1204-1211`.
- **Observed/expected/root/confidence:** non-home/UNC/error paths remain and close can strand raw WAV; literal-prefix/event-loop cleanup is incomplete; high.
- **Impact/recommendation/alternative:** support report or temp directory disclosure; allowlisted diagnostics + owned private temp registry/startup cleanup, or omit paths/mic temp feature.
- **Verification/dependencies/commercial:** custom path/case/UNC/error and forced-close tests; OS support; privacy promise.

### QA-002 — current tests over-rely on fakes/source assertions and lack quality gates

- **Category/severity:** QA P2; impact `C2 S1 Cm2 P1 E2 M2`.
- **Evidence/affected:** `tests/test_gui_contract_app.py` inspects source; `tests/test_platform_acceptance_app.py` uses fake controller/bytes; no coverage/type checker, live cloud/device/model tests.
- **Observed/expected/root/confidence:** green unit suite does not prove main user journeys/native integrations; Test-RC harness and real acceptance are correctly separate but incomplete; high.
- **Impact/recommendation/alternative:** false confidence; behavioral GUI/service tests plus immutable manual/native evidence, or narrow supported claim set.
- **Verification/dependencies/commercial:** coverage/mutation review and physical matrix; devices/models/accounts; release readiness.

### CI-001 — GitHub Actions are referenced by movable major tags

- **Category/severity:** supply-chain P2; impact `C1 S2 Cm2 P0 E0 M1`.
- **Evidence/affected:** `.github/workflows/*.yml` uses tags such as `@v4/@v7`; official GitHub secure-use guidance says full commit SHA is immutable.
- **Observed/expected/root/confidence:** workflow code can change under same tag; action dependencies should be pinned and reviewed; high.
- **Impact/recommendation/alternative:** CI/release compromise; full SHA + Dependabot updates, or vendor reviewed actions.
- **Verification/dependencies/commercial:** workflow policy/static check; maintainer process; release trust.

### OPS-001 — support/security observability and lifecycle are incomplete

- **Category/severity:** operations P2; impact `C1 S2 Cm3 P2 E0 M2`.
- **Evidence/affected:** no structured runtime logging/audit framework/private vulnerability contact/update rollback; diagnostics/doctor exist but have gaps.
- **Observed/expected/root/confidence:** consent/model/install/restore/delete/integrity events cannot be correlated safely; Test-RC has no paying-customer operational contract; high.
- **Impact/recommendation/alternative:** slow incident/support recovery; minimal local privacy-safe event schema, incident/LTS/EOL/update/support playbooks, or initially manual enterprise support with explicit limits.
- **Verification/dependencies/commercial:** support simulation, redaction/rotation/export, incident/rollback drill; product operations; sale blocker when support promised.

### DOC-001 — security documentation contradicted implemented cloud paths — RESOLVED_DOCS

- **Category/severity:** documentation P2; impact `C1 S2 Cm2 P0 E0 M2`.
- **Evidence/affected:** baseline `SECURITY.md:3` said cloud adapters absent while code and later sections implement OpenAI STT/cleanup.
- **Observed/expected/root/confidence:** reader could mis-assess privacy boundary; docs drift; high.
- **Impact/recommendation/alternative:** incorrect security promise; canonical `SECURITY.md` was corrected in this audit; add a doc/code contract check, or remove cloud feature if policy changes.
- **Verification/dependencies/commercial:** current text compared to GUI/CLI/cloud modules and no stale phrase remains; product cloud behavior itself is unchanged; `RESOLVED_DOCS` does not close cloud runtime/privacy testing.

### MNT-001 — unused public settings and stale build paths drift

- **Category/severity:** maintainability P3/P2; impact `C1 S0 Cm1 P0 E0 M2`.
- **Evidence/affected:** `Settings.output_dir`, `insert_to_active_app`, `normalized_output_dir` unused; legacy mac build and Windows README reference prior release paths.
- **Observed/expected/root/confidence:** serialized options imply behavior not present and duplicate builders/docs drift; historical compatibility residue; high.
- **Impact/recommendation/alternative:** confusion/support mistakes; deprecate/migrate and consolidate authoritative builder, or document reserved fields explicitly.
- **Verification/dependencies/commercial:** config migration/build-doc link checks; release architecture; lowers support cost.

### PERF-001 — target performance and unit economics are unmeasured

- **Category/severity:** performance/commercial P2; impact `C0 S0 Cm3 P3 E1 M1`.
- **Evidence/affected:** no immutable target-hardware cold/warm RTF, RAM/VRAM, package/model/storage/API/support measurements; static Hermes decoder/checkpoint complexity indicates possible high cost.
- **Observed/expected/root/confidence:** no optimization or price claim is evidence-based; missing benchmark program; high.
- **Impact/recommendation/alternative:** unsustainable price/support matrix; versioned benchmark/cost telemetry in controlled tests, or limit hardware/models and publish no performance promise.
- **Verification/dependencies/commercial:** target matrix and measured cost inputs; hardware/business data; blocks pricing, not source Test-RC.

## Reconciliation notes

- **COR-001 retracted:** An early static/research pass suspected that `gpt-transcribe` was unsupported. The current official [OpenAI Audio API reference](https://platform.openai.com/docs/api-reference/audio/createTranscription) explicitly lists it. This is not a product defect on the audited date. Live service behavior remains `NOT RUN`; a contract/drift test is still recommended. Retaining this reconciliation prevents the discarded hypothesis from being silently revived.
- P1/P2 reflects current local desktop assumptions. Shared directories, exchanged archives and paid distribution increase security/privacy severity.
- Findings based on lifecycle/native exploitability are explicitly labeled inferred; no exploit was claimed.
- Documentation corrections do not close the underlying product finding. A finding closes only after approved implementation and required verification.
- Desktop/faster-whisper can reach release readiness independently. Hermes findings remain mandatory before Hermes is marketed or distributed as production capability.
