# VOICE Studio — статус незалежного аудиту

## Висновок

Integrated W1 state `672577ef63d6107b7f7a78910574924dc9f2775f` має добру
local-first основу і помітні захисні контракти, але **не готовий до
production/commercial release**. Підтверджених P0 не знайдено. P1-блокери
охоплюють незакриті desktop correctness/resource/data-loss lifecycle, Hermes
integration і scientific validity, untrusted files/model authenticity,
sensitive-data protection, reproducible supply chain, native acceptance та
комерційну операційну модель. W1 зафіксував лише headless implementation slice для
точно визначених finding portions; їхній стан —
`IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`. Cloud model default підтверджено
офіційним API; активний cloud diagnostics finding є P2.

Історичний audit `main` змінював лише документацію/Codex instructions. Перед
цим W1 integrated implementation tree змінив тільки дозволені product modules
і regression tests; поточний documentation increment змінює лише шість
дозволених Markdown-файлів. Під час історичного audit була створена
непроіндексована product-code чернетка в окремому linked worktree; після
повторної перевірки read-only constraint її і тимчасову гілку повністю
видалено, без коміту або перенесення в `main`. Інцидент зафіксований у ledger,
а не прихований.

## Repository baseline

| Поле | Значення |
|---|---|
| Root | repository checkout root (`.`); host-specific absolute path intentionally not versioned |
| Repository | `https://github.com/Subbotik24/107_Voice_Studio.git` |
| Source integration line | `codex/voice-studio-0.4-rc` |
| Documentation worktree | `codex/voice-data-safety-docs` |
| Commit | `672577ef63d6107b7f7a78910574924dc9f2775f` (W1 integrated implementation baseline) |
| Initial audit baseline | `main@fffa50b6bc26fa2e7fa2150f2260ae873a5cf511` (historical, docs-only audit) |
| Initial status | clean before W1 integration |
| Pre-existing user changes | none observed |
| Product tracks | desktop `hermes_voice_studio`; experimental `hermes_whisper` |
| Customer brand | `VOICE Studio` (compatibility package/CLI names remain `hermes_*` / `hermes-voice`) |
| Release claim | unsigned 0.3.0 Test RC; no production-authoritative release, signing, notarization or clean-machine acceptance |

## Scope and method

Inspected repository structure, current docs, Git history, source, tests, manifests, workflows, build/packaging scripts and configuration shape. Traced desktop/media/cloud/storage/backup/model and Hermes data/train/evaluate/bundle flows. Performed independent desktop, security/privacy and Hermes/commercial passes, then reconciled results. W1 additionally reviewed the integrated settings/editor/clipboard/recorder/temp behavior and its focused regression tests. External checks used current official OpenAI, GitHub, Python Packaging and PyPI sources where the repository contract could have drifted.

No production service, live OpenAI call, destructive migration, dependency update/install, native device acceptance, signed artifact acceptance, push, PR, release or deployment occurred in this audit update.

## Verification ledger summary

| Check | Status | Evidence |
|---|---|---|
| `git status`, branch, SHA, remote | PASS | W1 worktree and allowlisted docs scope recorded |
| frozen `python -m compileall -q src tests scripts packaging` | PASS | Python 3.12.13; exit 0, no output |
| frozen focused W1 suite | PASS | `tests/test_config_app.py tests/test_editor_state_app.py tests/test_gui_contract_app.py tests/test_recorder_app.py tests/test_recording_lifecycle_app.py`; `72 passed` |
| frozen `PYTHONPATH=src python -m pytest -q` | PASS | `179 passed, 2 skipped, 5 subtests passed` |
| frozen `python -m ruff check .` | PASS | `All checks passed!` |
| frozen `python -m build` | BLOCKED | `No module named build`; no installation attempted |
| frozen `python -m pip check` | BLOCKED | `No module named pip`; no installation attempted |
| frozen `python -m pip_audit` | BLOCKED | `No module named pip_audit`; no installation attempted |
| GitHub CI run `31278777131` | PASS, remote historical evidence | resolved against original audited SHA `fffa50b6bc26fa2e7fa2150f2260ae873a5cf511`; macOS 14/Windows 2022, Python 3.11/3.12; compile/Ruff/pytest/wheel/pip check/pip-audit jobs succeeded; not W1 `672577ef63d6107b7f7a78910574924dc9f2775f` evidence |
| GitHub CodeQL run `31278777138` | PASS, remote historical evidence | resolved against original audited SHA `fffa50b6bc26fa2e7fa2150f2260ae873a5cf511`; not W1 `672577ef63d6107b7f7a78910574924dc9f2775f` evidence |
| Scheduled CodeQL `31992968536` | PASS, remote historical evidence | resolved against original audited SHA `fffa50b6bc26fa2e7fa2150f2260ae873a5cf511`; 2026-08-17; not W1 `672577ef63d6107b7f7a78910574924dc9f2775f` evidence |
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
| Desktop correctness/reliability | 2.5/5 | W1 settings/editor/recorder/temp slices have headless evidence; preparation/restore lifecycle, native acceptance and cloud health-report gaps remain |
| Security/privacy | 2.0/5 | explicit consent and archive controls are good; unsigned models, native parsing, plaintext data and mutable supply chain remain |
| Tests/QA | 2.5/5 | strong unit contracts and prior CI; local compile, focused/full pytest and Ruff PASS, while build/pip check/pip-audit are BLOCKED; many GUI tests are source assertions/fakes and physical/live/Hermes gates remain |
| Dependencies/release | 1.5/5 | CI/audits exist; no lock/hashes/SBOM/attestation/signing or symmetric current OS gate |
| Performance/resource efficiency | 1.5/5 | recorder queue/duration bounds are implemented; native disk/device behavior and main-thread/archive/model/decoder resource budgets remain unmeasured |
| Hermes engineering validity | 1.0/5 | formulas partly sound; integration overlap, leakage, fingerprint, metrics, CTC/resampling and provenance block claims |
| Operations/support | 1.5/5 | doctor/backup/diagnostics exist; logging, incident, updater/rollback, support and vulnerability channels incomplete |
| Commercial readiness | 1.0/5 | no signed product, entitlement, EULA/support lifecycle, IP/model proof or unit-economics evidence |

These values summarize evidence, not precise measurements.

## Architecture assessment

Observed separation between `SpeechEngine`, job controller, service and store is a sound direction. Immutable `raw_text`, user-original ownership, explicit cloud consent and reversible restore are valuable invariants. Risks concentrate at boundaries that cross UI thread/process/filesystem/archive/network and at the experimental model track.

The 1,219-line `app.py` owns UI, lifecycle, cloud dialogs, model management, backup and recording coordination. Several heavy/network operations execute on the Tk thread. Cancel/timeout begins only after media validation/hash/copy. SQLite/filesystem and catalog/directory updates are not atomic. These are stabilization concerns, not a reason for a wholesale rewrite.

## Correctness and reliability assessment

Highest-confidence remaining issues and W1 boundaries:

- current default `gpt-transcribe` is present in the official OpenAI Audio API model list; live account behavior remains unverified and no automated API-drift contract exists;
- wrong JSON setting types are rejected by strict validation and surfaced as a recoverable settings warning; GUI/native recovery behavior remains NOT RUN;
- Hermes computes merged overlap text but desktop stores a join of original chunks;
- recorder capture is streamed through a bounded 64-block queue with 100 ms blocks and a two-hour limit; overflow/status is surfaced and degraded capture is rejected by default, but native microphone/device evidence is NOT RUN;
- microphone temp ownership is scoped to recorder-created private app-cache files with cleanup on success/error/cancel/close/preflight; identity ambiguity is retained and reported, while broader diagnostics disclosure remains open;
- clipboard auto-copy is disabled by default and editor navigation/close uses Save/Discard/Cancel; native clipboard history/sync and live Tk interaction remain NOT RUN;
- backup restore daemon lifecycle and model-catalog/file operations can leave recoverable but inconsistent states;
- `doctor` applies local model readiness logic to cloud engine.

The exact W1 finding states are `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` for
`COR-002`, `COR-004`, `PRV-003`, `REL-001`, and the microphone-temp portion of
`PRV-001` only. This does not close `REL-003` restore/shutdown behavior,
broader `PRV-001` diagnostics disclosure, or any W2+ finding.

Full details and confidence labels: [audit/FINDINGS_REGISTER.md](audit/FINDINGS_REGISTER.md).

## Security and privacy assessment

No network listener, unauthenticated remote API, automatic cloud upload, `shell=True`, general `eval/exec` or observed SQL injection was found. Keys use environment/keychain and do not enter normal settings/backups. Model archive and restore logic have meaningful traversal/hash/staging protections.

Commercial blockers remain: SHA-256 proves bytes, not publisher; registry/upstream resolution can be mutable; native media parsing happens in the parent; `.hws`/backup lack complete resource ceilings; sensitive SQLite/audio/backups depend on OS defaults; release/dependency provenance is not reproducible or signed; diagnostics redaction can expose non-home/UNC paths.

Threat model: [audit/107_Voice_Studio-threat-model.md](audit/107_Voice_Studio-threat-model.md). Security review: [audit/SECURITY_REVIEW.md](audit/SECURITY_REVIEW.md).

## Tests and QA assessment

Strong areas: service/storage invariants, worker reuse/cancel/timeout, cloud privacy with fakes, archive traversal/integrity, restore staging and overlap helper, typed-settings/editor guards, and bounded recorder/temp lifecycle tests.

Weak areas: GUI behavioral tests frequently inspect source strings; platform acceptance uses fake controllers/media; no restore shutdown/process-tree tests; no deterministic CI that installs and executes Hermes/Torch; no >30 s Hermes desktop integration; no coverage threshold/type checker; no real microphone/device disconnect, native clipboard history/sync, physical OS, codec, cloud or model acceptance. The local frozen runtime passed the W1 focused and full suites, but absent build/pip/pip-audit modules remain explicit blockers.

## Dependency and supply-chain assessment

Direct dependency ranges and upper bounds exist, and prior CI included `pip-audit`. There is no frozen resolved graph, hashes, wheelhouse provenance, SBOM or complete bundled license inventory. Current GitHub workflows reference action major tags; [GitHub recommends pinning third-party actions to a full commit SHA](https://docs.github.com/en/actions/reference/security/secure-use?learn=getting_started&learnProduct=actions). A past audit of one resolver snapshot cannot establish the vulnerability state of every future range resolution.

## Performance, scalability and cost

- recorder memory is bounded by a 64-block queue and durable writer, with a two-hour cap; native disk-full/device behavior and measured peak resource evidence remain NOT RUN;
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

## W1 native acceptance gate — NOT RUN

The following procedure is the required physical gate for the five W1 states.
It is a manual/native checklist, not a substitute for the frozen headless
suite. Run each OS on a disposable user profile with a short non-sensitive
fixture and a locally installed `faster-whisper` model; do not use production
audio or claim release/signing acceptance from this checklist alone. Record
the app build/commit, OS version, input device, permission result, timestamps,
visible messages and whether each expected file remained or was removed.

### Windows 10/11 x64 — NOT RUN

1. Start VOICE Studio from the candidate source/frozen app on a disposable
   profile. In Settings confirm `auto_copy` is off, select a local engine/model,
   and grant Microphone permission only when Windows asks. Do not enable
   clipboard history for the first run.
1a. **WIN-SET-01 wrong-type settings recovery:** quit the app, back up the
    disposable profile's settings JSON, and run three isolated launches. Before
    each launch edit one value to a valid JSON value of the wrong type: set
    `"model"` to `1`, then `"auto_copy"` to `"yes"`, then
    `"task_timeout_seconds"` to `null`. Launch VOICE Studio each time and
    record the visible settings warning, safe-default startup and absence of an
    unhandled traceback. Restore the backup between runs. This case is **NOT
    RUN** until Windows evidence exists.
1b. **WIN-EDIT-01 dirty navigation:** create/select a disposable transcript,
    change its editor text and apply one formatting tag, then select another
    history row in three separate runs. Choose Save and verify the first record
    reopens with the changed `corrected_text`/formatting; choose Discard and
    verify navigation proceeds without the edit; choose Cancel and verify the
    current row and edits remain. Record each prompt choice and result; all
    three transitions are **NOT RUN**.
1c. **WIN-EDIT-02 dirty close:** make an editor change and close with Alt+F4 in
    three separate runs. Save must persist before close, Discard must close
    without persisting the edit, and Cancel must leave the window open with the
    edit visible. Do not infer crash-draft or backup-restore safety; this case
    is **NOT RUN**.
2. **WIN-CAP-01 normal capture:** hold the capture button, speak for 5–10 s,
   release, and wait for the local transcript. Confirm the UI reports success,
   `raw_text` remains unchanged, the result is in history, and the private
   recording temp is gone after processing. Confirm a user-selected original
   file in a separate directory is unchanged.
3. **WIN-CAP-02 continuous capture:** click `Постійний запис`, speak for at
   least 30 s, click stop, and confirm the returned transcript and status. Do
   not treat a successful short run as proof of the two-hour limit; record the
   displayed status and peak behavior.
4. **WIN-CAP-03 overflow/device disconnect:** start continuous capture, then
   unplug the active USB microphone (or disable that input in
   Settings → System → Sound) for 3–5 s and reconnect it before stopping. If
   the recorder reports a drop/status warning, verify a visible degraded-capture
   prompt defaults to **No**; choose No and confirm the temp is cleaned and no
   transcription job starts. If no warning appears, record that as a failure,
   not as PASS. Repeat once with the warning accepted only if the user needs to
   verify the explicit opt-in path.
5. **WIN-CAP-04 duration limit:** start continuous capture with a stable input
   and leave it running until the UI reports the two-hour limit and forces a
   stop. Confirm the status says the limit was reached, the recording does not
   continue growing, and processing starts only after the degraded/limit policy
   is answered. This two-hour test is NOT RUN until physically executed.
6. **WIN-LIFE-01 close during capture:** begin capture, close the window with
   Alt+F4, and confirm the recorder is cancelled, only the tracked private temp
   is removed, and the original fixture is untouched. Reopen the app and verify
   no orphan private recording is silently processed.
7. **WIN-LIFE-02 close during transcription:** start a local transcription,
   close the window while the status shows processing, and reopen the app. Record
   whether cancellation is visible and whether any managed copy/history entry
   is consistent; do not infer restore/shutdown safety for backups from this
   test.
8. **WIN-CLIP-01 clipboard disclosure:** with `auto_copy` still off, complete a
   transcript and press `Win+V`. Confirm the new transcript was not copied by
   the app. Use the explicit Copy action, press `Win+V` again, and record the
   Windows clipboard-history entry. If clipboard sync is enabled, verify the
   disclosure on the paired device; do not enable sync without recording that
   it changes the privacy boundary.

### macOS Apple Silicon — NOT RUN

1. Start VOICE Studio with `./run_mac.command` (or the candidate app) on a
   disposable profile. In System Settings → Privacy & Security → Microphone,
   grant access only to the tested app; confirm `auto_copy` is off and select a
   local engine/model. Do not use production audio.
1a. **MAC-SET-01 wrong-type settings recovery:** quit the app, back up the
    disposable profile's settings JSON, and perform three isolated launches
    with one wrong-type value at a time: `"model": 1`,
    `"auto_copy": "yes"`, and `"task_timeout_seconds": null`. Launch after
    each edit, record the visible settings warning, safe-default startup and
    absence of an unhandled traceback, then restore the backup before the next
    run. This case is **NOT RUN** until macOS evidence exists.
1b. **MAC-EDIT-01 dirty navigation:** create/select a disposable transcript,
    edit text and one formatting tag, then select another history row in three
    runs. Save must persist the first record's `corrected_text`/formatting,
    Discard must navigate without the edit, and Cancel must keep the current row
    and edit. Record every prompt choice and outcome; all three transitions are
    **NOT RUN**.
1c. **MAC-EDIT-02 dirty close:** edit the current transcript and close the
    window or press Cmd+Q in three separate runs. Save must persist before close,
    Discard must close without persisting, and Cancel must keep the window open
    with the edit visible. Do not infer crash-draft or backup-restore safety;
    this case is **NOT RUN**.
2. **MAC-CAP-01 normal capture:** hold the capture button, speak for 5–10 s,
   release, and wait for the local transcript. Confirm success/history,
   immutable `raw_text`, removal of the private recorder temp, and no change to
   a separate user-selected original file.
3. **MAC-CAP-02 continuous capture:** click `Постійний запис`, speak for at
   least 30 s, stop it, and record the transcript/status and any warning. A
   short run does not prove the two-hour limit.
4. **MAC-CAP-03 overflow/device disconnect:** start continuous capture, unplug
   the active USB microphone (or revoke/disable its input in System Settings →
   Privacy & Security → Microphone) for 3–5 s, reconnect/restore it, and stop.
   Verify that any status/drop warning is visible, that the default **No** on
   the degraded-capture prompt removes the private temp and starts no job, and
   record a missing warning as a failure. Repeat with explicit acceptance only
   as a separate opt-in observation.
5. **MAC-CAP-04 duration limit:** leave continuous capture active until the
   two-hour limit is reported and capture is forced to stop. Confirm no further
   frames are accepted and that the status/processing decision is explicit. This
   remains NOT RUN until a physical two-hour observation exists.
6. **MAC-LIFE-01 close during capture:** close the window or press Cmd+Q during
   capture, confirm recorder cancellation and scoped removal of only the tracked
   private temp, then reopen and check for no silent orphan processing. Record
   any permission or device error verbatim.
7. **MAC-LIFE-02 close during transcription:** start local transcription, close
   while processing, reopen, and record cancellation visibility plus history/
   managed-copy consistency. This does not validate backup restore shutdown.
8. **MAC-CLIP-01 clipboard disclosure:** with `auto_copy` off, complete a
   transcript and inspect the current clipboard using an existing clipboard
   manager only if the test profile already has one; confirm the app did not
   copy automatically. Use explicit Copy and inspect the manager. If a second
   Apple device and Universal Clipboard are enabled, record any synchronized
   copy as an external boundary; macOS has no built-in history view equivalent
   to Windows `Win+V`.

All Windows and macOS cases above are currently **NOT RUN**. Passing the local
tests, generating a document, or observing a fake `sounddevice` stream cannot
close this gate.

## Unknowns and explicit non-claims

Unknown: live OpenAI compatibility beyond static contract comparison; actual dependency resolution today on release machines; real native codec/device behavior; Hermes WER/CER/RTF/RAM; actual model/corpus rights; frozen bundle license contents; customer demand/willingness-to-pay; operating/support cost; branch/tag protection and external release settings.

No claim is made that Hermes is accurate, the product is secure against a hostile local administrator, licenses are legally cleared, a current signed artifact exists, or production acceptance passed.
