# 107 Voice Studio — статус незалежного аудиту

## Висновок

`main@fffa50b6bc26fa2e7fa2150f2260ae873a5cf511` має добру local-first основу і помітні захисні контракти, але **не готовий до production/commercial release**. Підтверджених P0 не знайдено. P1-блокери охоплюють desktop correctness/resource/data-loss lifecycle, Hermes integration і scientific validity, untrusted files/model authenticity, sensitive-data protection, reproducible supply chain, native acceptance та комерційну операційну модель. Cloud model default підтверджено офіційним API; активний cloud diagnostics finding є P2.

У фінальному `main` цей етап змінив тільки документацію/Codex instructions. Product code, tests, dependencies, config, CI і packaging не змінені. Під час роботи була створена непроіндексована product-code чернетка в окремому linked worktree; після повторної перевірки read-only constraint її і тимчасову гілку повністю видалено, без коміту або перенесення в `main`. Інцидент зафіксований у ledger, а не прихований.

## Repository baseline

| Поле | Значення |
|---|---|
| Root | repository checkout root (`.`); host-specific absolute path intentionally not versioned |
| Repository | `https://github.com/Subbotik24/107_Voice_Studio.git` |
| Branch | `main`, tracking `origin/main` |
| Commit | `fffa50b6bc26fa2e7fa2150f2260ae873a5cf511` |
| Initial status | clean |
| Pre-existing user changes | none observed |
| Product tracks | desktop `hermes_voice_studio`; experimental `hermes_whisper` |
| Release claim | unsigned 0.3.0 Test RC; no production-authoritative release |

## Scope and method

Inspected repository structure, current docs, Git history, source, tests, manifests, workflows, build/packaging scripts and configuration shape. Traced desktop/media/cloud/storage/backup/model and Hermes data/train/evaluate/bundle flows. Performed independent desktop, security/privacy and Hermes/commercial passes, then reconciled results. External checks used current official OpenAI, GitHub, Python Packaging and PyPI sources where the repository contract could have drifted.

No production service, live OpenAI call, destructive migration, dependency update/install, commit, push, PR, release or deployment occurred.

## Verification ledger summary

| Check | Status | Evidence |
|---|---|---|
| `git status`, branch, SHA, remote | PASS | exact local baseline recorded |
| `python -m compileall -q src tests` | PASS | bundled Python 3.12.13; ~0.08 s |
| `pytest -q` | BLOCKED | `pytest` absent in available bundled runtime; dependencies deliberately not installed |
| Ruff/build/pip-audit locally | BLOCKED | modules absent; no mutation of environment |
| GitHub CI run `31278777131` | PASS, remote historical evidence | exact baseline SHA; macOS 14/Windows 2022, Python 3.11/3.12; compile/Ruff/pytest/wheel/pip check/pip-audit jobs succeeded |
| GitHub CodeQL run `31278777138` | PASS, remote historical evidence | exact baseline SHA |
| Scheduled CodeQL `31992968536` | PASS, remote historical evidence | exact baseline SHA; 2026-08-17 |
| Numerical spot checks | PASS/PARTIAL | mel inverse, frame count, sample-count resampling, WER/CER, timestamps, LR schedule and parameter estimate independently recomputed |
| NaN manifest probe | FAIL | non-finite duration was accepted |
| Native GUI/mic/hotkey/codecs/models | NOT RUN | requires real devices, models and supported OS acceptance environment |
| Live OpenAI STT/cleanup | NOT RUN | external side effect/credential not authorized |
| Hermes training/quality/performance | NOT RUN | no licensed corpus, production weights, Torch environment or closed benchmark |
| Frozen Windows/macOS artifacts | INCONCLUSIVE | repo evidence exists, but current physical acceptance/signing/notarization is incomplete |

Remote green CI is evidence for that resolved runner state, not a replacement for current local tests, deterministic Hermes coverage, physical device tests or signed artifact acceptance.

## Readiness ratings

Scale: `0` absent/unknown, `1` prototype, `2` major blockers, `3` credible Test-RC foundation, `4` production candidate with bounded gaps, `5` commercial evidence complete.

| Area | Rating | Evidence-based rationale |
|---|---:|---|
| Product definition | 3.0/5 | workflows and local/private boundaries are clear; packaging/support promises incomplete |
| Desktop architecture | 3.0/5 | engine/service/store boundaries exist; large UI module and mixed sync/async lifecycle debt |
| Desktop correctness/reliability | 2.0/5 | settings crash paths, preparation cancel gap, temp/recording/restore lifecycle and cloud health-report defect |
| Security/privacy | 2.0/5 | explicit consent and archive controls are good; unsigned models, native parsing, plaintext data and mutable supply chain remain |
| Tests/QA | 2.5/5 | strong unit contracts and prior CI; many GUI tests are source assertions/fakes, local suite blocked, no physical/live/Hermes deterministic gate |
| Dependencies/release | 1.5/5 | CI/audits exist; no lock/hashes/SBOM/attestation/signing or symmetric current OS gate |
| Performance/resource efficiency | 1.5/5 | worker reuse exists; long recording/main-thread/archive/model/decoder resource risks lack measured budgets |
| Hermes engineering validity | 1.0/5 | formulas partly sound; integration overlap, leakage, fingerprint, metrics, CTC/resampling and provenance block claims |
| Operations/support | 1.5/5 | doctor/backup/diagnostics exist; logging, incident, updater/rollback, support and vulnerability channels incomplete |
| Commercial readiness | 1.0/5 | no signed product, entitlement, EULA/support lifecycle, IP/model proof or unit-economics evidence |

These values summarize evidence, not precise measurements.

## Architecture assessment

Observed separation between `SpeechEngine`, job controller, service and store is a sound direction. Immutable `raw_text`, user-original ownership, explicit cloud consent and reversible restore are valuable invariants. Risks concentrate at boundaries that cross UI thread/process/filesystem/archive/network and at the experimental model track.

The 1,219-line `app.py` owns UI, lifecycle, cloud dialogs, model management, backup and recording coordination. Several heavy/network operations execute on the Tk thread. Cancel/timeout begins only after media validation/hash/copy. SQLite/filesystem and catalog/directory updates are not atomic. These are stabilization concerns, not a reason for a wholesale rewrite.

## Correctness and reliability assessment

Highest-confidence issues:

- current default `gpt-transcribe` is present in the official OpenAI Audio API model list; live account behavior remains unverified and no automated API-drift contract exists;
- wrong JSON setting types escape intended damaged-settings recovery;
- Hermes computes merged overlap text but desktop stores a join of original chunks;
- continuous recording grows unbounded in RAM and doubles data at stop;
- sounddevice callback status is discarded, so capture overflow/dropout may be silent;
- mic temp cleanup depends on a Tk event that may never run after close;
- automatic clipboard expands the privacy boundary and unsaved editor changes can be overwritten/closed without a dirty prompt;
- backup restore daemon lifecycle and model-catalog/file operations can leave recoverable but inconsistent states;
- `doctor` applies local model readiness logic to cloud engine.

Full details and confidence labels: [audit/FINDINGS_REGISTER.md](audit/FINDINGS_REGISTER.md).

## Security and privacy assessment

No network listener, unauthenticated remote API, automatic cloud upload, `shell=True`, general `eval/exec` or observed SQL injection was found. Keys use environment/keychain and do not enter normal settings/backups. Model archive and restore logic have meaningful traversal/hash/staging protections.

Commercial blockers remain: SHA-256 proves bytes, not publisher; registry/upstream resolution can be mutable; native media parsing happens in the parent; `.hws`/backup lack complete resource ceilings; sensitive SQLite/audio/backups depend on OS defaults; release/dependency provenance is not reproducible or signed; diagnostics redaction can expose non-home/UNC paths.

Threat model: [audit/107_Voice_Studio-threat-model.md](audit/107_Voice_Studio-threat-model.md). Security review: [audit/SECURITY_REVIEW.md](audit/SECURITY_REVIEW.md).

## Tests and QA assessment

Strong areas: service/storage invariants, worker reuse/cancel/timeout, cloud privacy with fakes, archive traversal/integrity, restore staging and overlap helper.

Weak areas: GUI behavioral tests frequently inspect source strings; platform acceptance uses fake controllers/media; no recorder lifecycle tests; no corrupt typed-settings regression; no restore shutdown/process-tree tests; no deterministic CI that installs and executes Hermes/Torch; no >30 s Hermes desktop integration; no coverage threshold/type checker; no real codec/device/cloud/model acceptance.

## Dependency and supply-chain assessment

Direct dependency ranges and upper bounds exist, and prior CI included `pip-audit`. There is no frozen resolved graph, hashes, wheelhouse provenance, SBOM or complete bundled license inventory. Current GitHub workflows reference action major tags; [GitHub recommends pinning third-party actions to a full commit SHA](https://docs.github.com/en/actions/reference/security/secure-use?learn=getting_started&learnProduct=actions). A past audit of one resolver snapshot cannot establish the vulnerability state of every future range resolution.

## Performance, scalability and cost

- long continuous recording has unbounded memory and an additional stop-time copy;
- bundle/model import/hash/network operations can freeze GUI;
- backup restore reads large members fully into memory;
- parent media probe and FFmpeg lifecycle lack complete time/resource/process-tree budgets;
- Hermes greedy decoding repeatedly recomputes prefix attention and has no production measurements;
- training checkpoint retention can consume large storage without pruning.

This is a single-user desktop; customer/tenant scalability does not apply today. Unit economics are unknown because RTF, cold/warm throughput, RAM/VRAM, package/model sizes, support volume and optional API consumption have not been measured on target hardware.

## Engineering calculation assessment

Basic algebra for mel conversion, frame counts, LR schedule, parameter count and corpus edit metrics was independently checked. Significant blockers are execution/integration and validation semantics: overlap merge discarded, data leakage gates absent, audio-content fingerprint missing, NaN duration accepted, linear downsampling lacks anti-aliasing, CTC infeasibility may silently zero losses, language accuracy can be tautological, confidence is heuristic/un-calibrated and timestamp contracts can silently clip.

Formula/source/units/status table: [ENGINEERING_CALCULATION_REGISTER.md](ENGINEERING_CALCULATION_REGISTER.md).

## Operations and observability

Diagnostics, doctor, versioning, backup/recovery and integrity tools give a usable Test-RC base. However, diagnostics redaction is incomplete and `SECURITY.md` was stale about cloud adapters. There is no minimal local security-event log, consented crash pipeline, update/rollback mechanism, incident runbook, private vulnerability contact, SLA/LTS/EOL process or artifact support matrix.

## Commercialization assessment

The closest viable product is signed local-first desktop on faster-whisper, sold with updates/support and offline-capable entitlement. Enterprise offline/on-prem is a strong strategic fit. Paid Hermes model packs are a later option only after corpus rights, signed provenance, closed benchmarks and production evidence. Multi-tenant SaaS requires new auth/tenant/KMS/queue/quota/billing/operations architecture and should be treated as a separate product investment.

## Major blockers

1. Correct current cloud and Hermes desktop correctness contracts.
2. Bound untrusted file/native/parser/resource and lifecycle behavior.
3. Enforce dataset identity, split isolation, consent/license provenance and metric validity.
4. Produce deterministic `[hermes]` and native desktop acceptance evidence.
5. Lock/attest/sign dependencies, builds, installers and model channel; complete license/SBOM inventory.
6. Define data protection, support/update/rollback/incident and entitlement policies.
7. Obtain specialist legal review for EULA, privacy/DPA, FFmpeg/native dependencies, corpus/model redistribution, trademark/contributor IP.

## Unknowns and explicit non-claims

Unknown: live OpenAI compatibility beyond static contract comparison; actual dependency resolution today on release machines; real native codec/device behavior; Hermes WER/CER/RTF/RAM; actual model/corpus rights; frozen bundle license contents; customer demand/willingness-to-pay; operating/support cost; branch/tag protection and external release settings.

No claim is made that Hermes is accurate, the product is secure against a hostile local administrator, licenses are legally cleared, a current signed artifact exists, or production acceptance passed.
