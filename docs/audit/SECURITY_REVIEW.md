# Security and privacy review — 107 Voice Studio

## Result

Independent static review of `main@fffa50b6bc26fa2e7fa2150f2260ae873a5cf511` found no confirmed P0 and no tracked common OpenAI/GitHub/AWS/private-key secret pattern. The product remains **NO-GO for production commercial release** until the P1 trust/resource/data/release findings are remediated and verified.

## P1 findings

### SEC-002 — model integrity is not publisher authenticity

Evidence: `src/hermes_voice_studio/model_release.py:20-59`; `src/hermes_voice_studio/model_catalog.py:43-57,104-116,214-238,293-336`; `src/hermes_whisper/bundle.py:172-201,274-303`. Registry scheme/redirect/owner and upstream revision are not consistently pinned; `.hws` hash can be rebuilt by any producer; normal load does not always re-verify inventory.

Target: mandatory trusted HTTPS/redirect policy and upstream revision, publisher-signed registry/model manifests, key rotation/revocation, signer/source display and load-time verification.

### SEC-001 — native media is parsed before worker isolation

Evidence: `src/hermes_voice_studio/service.py:30-35`; `src/hermes_voice_studio/media.py:29-99`; `src/hermes_voice_studio/jobs.py:68-105,125-170`; `src/hermes_voice_studio/engines/hermes.py:38-54`. PyAV parses in the parent. FFmpeg has no direct timeout/process-tree ownership.

Target: disposable restricted media-probe process, byte/duration/decoded-output caps, direct FFmpeg timeout and descendant termination, reviewed native build and malformed-media/fuzz regressions.

### SEC-003 — untrusted archives lack complete resource ceilings

Evidence: `src/hermes_whisper/bundle.py:112-151,172-191,218-270`; `src/hermes_voice_studio/backup.py:106-192`; contrast `src/hermes_voice_studio/model_release.py:16-18,98-121`. `model.pt` lacks cap; backup lacks total/member/count/ratio/record/free-space bounds and reads whole JSONL/settings members.

Target: versioned ceilings and streaming restore with disk preflight.

### PRV-002 — sensitive storage depends on OS defaults

Evidence: `src/hermes_voice_studio/storage.py:33-50,93-104,135-169`; `src/hermes_voice_studio/backup.py:31-97`; `src/hermes_voice_studio/config.py:53-62`. SQLite/transcripts/audio/backups are plaintext; owner-only modes/ACL checks are not explicit.

Target: private permissions, custom/shared-path warning, authenticated encrypted portable backup, key recovery/rotation and documented full-disk-encryption assumptions.

### REL-006 — release evidence is not reproducibly bound

Evidence: `scripts/build_test_rc.sh:17-23,90-114`; `scripts/create_release_manifest.py:72-113`; `scripts/build_windows.ps1:97-131`; `scripts/build_mac.sh:15-24`; `.github/workflows/release.yml`. Manifest lacks clean tree/commit/dependency lock/SBOM/builder/signed attestation. Windows parity and current native acceptance are incomplete.

Target: one authoritative builder per OS, clean-commit binding, locked graph, SBOM/notices, signed manifest/artifact and protected approval.

### SUP-001 — dependency and CI graph is mutable

Evidence: `pyproject.toml`; launch/build scripts; `ci.yml`, `release.yml`, `codeql.yml`. Broad ranges resolve over time; launchers may install extras; GitHub Actions use movable tags.

Target: platform locks/hashes/wheelhouse, cloud/local profiles, pinned action SHAs, `persist-credentials: false`, artifact SBOM and native inventory.

### IP-001 — license/IP evidence is not release-complete

Evidence: `THIRD_PARTY_NOTICES.md:3-13`; `packaging/hermes_voice_studio.spec`; `scripts/build_model_release.py:25-82`; `RELEASE_ACCEPTANCE.md`. Current notices are an index, and model pack does not require license/model card/provenance payload.

Target: per-artifact resolved license inventory and specialist review of native builds, model/weights, corpus/tokenizer, notices/source obligations and OpenAI terms.

## Additional P1/P2 findings

- **P2 — Secret in argv:** `src/hermes_voice_studio/cli.py:85-93,227-240` accepts `cloud key set --value`; deprecate it in favor of hidden TTY/stdin descriptor.
- **P2 — Backup settings replay:** `src/hermes_voice_studio/backup.py:49-58,180-190,224-237` can restore machine-specific/custom/UNC paths; separate data/settings, reset and show diff.
- **P2 — Diagnostics redaction:** `src/hermes_voice_studio/diagnostics.py:17-38,87-174` only strips `paths` and literal home prefix; use allowlisted schema and sanitize all exceptions/custom paths.
- **P1 — Unbounded/silent recording:** `src/hermes_voice_studio/recorder.py:12-18,31-72` accumulates all frames, creates another concatenation and ignores capture status; stream to private file with duration/byte cap and surface overflow/dropout.
- **P1 commercial / P2 personal — Clipboard boundary:** `src/hermes_voice_studio/models.py:94`, `src/hermes_voice_studio/app.py:512-513,570-573` auto-copy successful text by default; make it explicit/default-off for sensitive use and document OS history/sync exposure.
- **Secret/incident operations:** public-tree helper scans a narrow token pattern; no general history/artifact secret scan or private security contact.
- **P1 pre-model-release — Hermes provenance enforcement:** `src/hermes_whisper/manifest.py:13-70,116-145` and `src/hermes_whisper/cli.py` do not enforce strict types, rights registry, unique IDs or split isolation.
- **Observability:** security-sensitive local actions lack privacy-safe audit events; no transcript/key/full path should enter such a log.

## Existing controls to preserve

- local engine default; no automatic OpenAI fallback;
- explicit STT/cleanup consent and `offline_only` block;
- 25 MB STT upload ceiling;
- keychain/environment secrets excluded from normal settings/backups/worker/diagnostics;
- structured cleanup proposal, immutable raw text and undo;
- user originals are copied and never deleted;
- parameterized SQLite and schema versioning;
- path/symlink/duplicate/size/hash controls for model release ZIP;
- exact `.hws` members, internal hashes and `weights_only=True`;
- staged reversible backup restore;
- worker timeout/cancel/restart;
- minimal workflow permissions, CodeQL, Dependabot and past pip-audit;
- unsigned artifacts are clearly labeled Test RC.

## Privacy review

Potentially sensitive data includes voice/audio, raw/corrected text, filenames/paths, dictionary terms, benchmark references and cloud request metadata. Current privacy boundary is local OS account plus explicit cloud action. Commercial requirements: data inventory/purpose, retention/export/delete, secure backup, support-report minimization, cloud subprocessor/region/retention disclosure and specialist legal review. No GDPR/compliance claim is made.

## Required verification

1. malformed/fuzzed media in disposable process, including process-tree cancellation;
2. adversarial archive limits and disk-full/restore interruption;
3. signature verification, key rotation/revocation and load-time model checks;
4. private permissions and encrypted backup/key-loss/recovery on supported OSes;
5. clean reproducible build with lock/hashes/SBOM/notices/attestation/signatures;
6. general secret/history/artifact scanning and private vulnerability flow;
7. diagnostics redaction tests for non-home, case variants, UNC and exception strings;
8. live OpenAI/privacy verification only in an explicitly authorized test account.

Canonical threat context: [107_Voice_Studio-threat-model.md](107_Voice_Studio-threat-model.md). Full cross-domain register: [FINDINGS_REGISTER.md](FINDINGS_REGISTER.md).
