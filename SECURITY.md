# Security policy

## Reporting a vulnerability

Report privately through GitHub:
**[Report a vulnerability](https://github.com/Subbotik24/107_Voice_Studio/security/advisories/new)**
(repository → Security → Advisories). This opens a private advisory visible only
to you and the maintainers. Do not open a public issue for a suspected
vulnerability or leaked secret.

Include the operating system, the application version, a minimal reproduction
and the impact. Do **not** attach private audio, transcript text, API keys,
backups, databases or absolute user paths.

Expect an acknowledgement within seven days. If the private advisory form is
unavailable to you, open a public issue that says only that you have a security
report and asks for a private channel — no details.

## Security defaults

- Local transcription is the default. The OpenAI STT and AI cleanup adapters are
  present, but every cloud operation requires explicit consent, and
  `offline_only` blocks them. The only exception is an explicit CLI
  `--engine openai-cloud` override combined with `--allow-cloud-upload`, which
  is itself the consent.
- API keys are read from `OPENAI_API_KEY` or the OS keychain only. They are
  never written to settings, jobs, backups, diagnostics, logs or transcript
  metadata.
- Source files, `raw_text`, model packs and backups are never uploaded by a
  background operation.
- Clipboard auto-copy is disabled by default (`auto_copy=false`). Copying is an
  explicit user action and must be treated as leaving the app boundary, because
  clipboard history, manager processes and OS sync are controlled by the host.
- `diagnostics --export` creates a redacted report.

## Data boundaries

- Customer-facing product name: **VOICE Studio**. The existing
  `voice_studio` package and `voice-studio` CLI names remain
  compatibility interfaces; this naming does not imply a separate cloud service.
- User original media is never deleted.
- `delete_after_transcription` removes only the managed copy, and only when no
  other transcript record uses it.
- Editor navigation and close use a dirty Save/Discard/Cancel prompt. Save
  persists the editable `corrected_text` layer and formatting; immutable
  `raw_text` is never rewritten.
- The Studio editor tools (find and replace, add selection to dictionary,
  filler-word cleanup, confidence review) change only the open editor buffer.
  They write nothing to storage before an explicit save, and `raw_text` is
  never rewritten. The confidence threshold is page state only and is never
  persisted to settings or disk.
- Terminology rules live in the locally managed dictionary file next to the
  other application data. Adding a rule from the editor selection writes only
  that file. Rule terms used as recognition hints are passed per request and
  are not written to settings, transcripts, metadata, diagnostics or logs.
- Dashboard statistics are aggregated locally from the local database. No
  network call, upload or telemetry is involved; a record whose payload cannot
  be read is counted as invalid instead of failing the page.
- Local playback resolves only the retained managed audio copy inside the
  managed sources directory. The external original file is never looked up or
  opened, and playback stops when the page or the record changes, on restore
  and on close.
- Microphone capture is recorder-owned under the private app-cache recordings
  directory. It streams 100 ms blocks through a bounded 64-block queue, has a
  two-hour limit, surfaces sounddevice status and queue-drop warnings, and
  rejects degraded capture by default. Cleanup is scoped to tracked
  recorder-owned paths; identity ambiguity retains and reports residue rather
  than guessing. A malicious same-account replacement after the final identity
  check is an accepted residual outside the selected OS-account and
  full-disk-encryption boundary. This is not secure deletion, and no absolute
  delete-by-handle guarantee is claimed.
- Source content carries SHA-256 provenance.
- Model archives require HTTPS, size checks, SHA-256, safe ZIP validation and
  atomic installation. ZIP members extract to fixed names, never to arbitrary
  paths.
- The sync folder is an explicit, off-by-default export boundary. When enabled,
  each stored transcript (`raw_text`, `corrected_text`, metadata including
  speaker labels, optionally the retained managed audio copy) is written as
  files into a folder the user chose, typically one a third-party cloud client
  synchronises. The app makes no network call, never reads an external
  original, never deletes anything there, writes no `source_path` and no keys,
  and stores the folder as a resolved absolute path. The folder must exist,
  must not be a symlink or reparse point and must lie outside the app data
  folder; that check runs on Save and again before every mirror write, so a
  hand-edited or restored `settings.json` cannot redirect the mirror. Deleting
  a record does not delete its mirrored files: what left the device under the
  cloud client's rules stays under those rules.
- The batch queue reuses the single-file job path, including the per-file cloud
  consent of the OpenAI profile, and is not persisted across restarts.
- The source launchers make no network call by default. Only
  `VOICE_STUDIO_AUTO_UPDATE=1` enables the developer self-update (fetch and
  check out `origin/main` before the launch); a folder with local edits is
  never overwritten, and the fetched code carries no publisher signature.
- Model files must never be committed to Git.

## Unresolved production issues

- Native acceptance is still **NOT RUN** on physical Windows and macOS: normal
  and continuous microphone capture, overflow and device disconnect, the
  two-hour limit, close during capture or transcription, local playback through
  a real audio device, and clipboard history and sync all remain unverified. The exact verified and remaining scope is in
  `VERIFICATION.md`.
- Native installers are not signed.
- The global hotkey depends on OS Accessibility permissions.
- Active-app insertion is disabled.
- There is no sandbox process isolation for the model runtime.
- There is no encrypted-at-rest storage for the live data directory; the
  only encrypted-at-rest artifact is the opt-in encrypted backup v2 (below).

### Encrypted backup v2 properties

- Threat scope: a v2 archive protects history, settings, dictionary and
  managed audio copies at rest inside `.voice-backup` against offline
  disclosure and tampering. It does not protect the live `data/` root.
- A wrong passphrase or authenticated-content tampering is a hard
  authentication error; structural or member-set violations are hard
  validation errors. Neither path has a plaintext fallback.
- Payload authentication is streaming and per chunk: plaintext from an
  authenticated chunk may be written only into the journaled, contained
  restore staging area before later chunks are checked. The live data root is
  not swapped until every payload has authenticated and the restored store has
  passed its audit. An ordinary later authentication failure removes staging;
  interruption recovery discards an incomplete `staging_building` directory.
- Only approved `cryptography` primitives are used: Argon2id (KDF), HKDF
  (key separation), chunked AES-256-GCM (payloads), HMAC-SHA-256 (manifest).
  No custom cryptography exists.
- KDF parameters are pinned to one profile with validated bounds
  (1-10 iterations, 1-256 MiB memory, 1-4 lanes), bounding KDF DoS.
- Secret material (passphrase, master/manifest/member keys) is never written
  to settings, the restore journal, the encrypted sidecar, logs or
  diagnostics; CPython cannot guarantee memory zeroization, so key bytes may
  persist in process memory until collection.
- A weak passphrase stays weak: Argon2id raises the cost of guessing but
  cannot make a short or reused passphrase safe.
- The plaintext manifest intentionally leaks the member count and member
  sizes. Fixed outer names (`manifest.json`, opaque `payload/NNNNNNNN.enc`)
  are visible; private logical member names and contents stay encrypted.
- AEAD/HMAC tags authenticate content against the passphrase-derived keys;
  they are not a publisher signature and prove nothing about who created an
  archive.
- v1 plaintext backup remains the default compatibility format; encryption
  is always an explicit opt-in.
- There is no structured runtime logging or audit event schema.

Unsigned Test RCs are not a trust or provenance guarantee. Verify release
checksums and test them in a non-critical environment.

The W1 controls above are documented and covered by the local headless suite,
but their finding state is `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`; they do not
close the broader signing, clean-machine, encryption or device-acceptance gaps.
