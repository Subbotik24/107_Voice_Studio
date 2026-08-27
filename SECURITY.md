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
  `offline_only` blocks them.
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
- Model files must never be committed to Git.

## Unresolved production issues

- Native acceptance is still **NOT RUN** on physical Windows and macOS: normal
  and continuous microphone capture, overflow and device disconnect, the
  two-hour limit, close during capture or transcription, and clipboard history
  and sync all remain unverified. The exact verified and remaining scope is in
  `VERIFICATION.md`.
- Native installers are not signed.
- The global hotkey depends on OS Accessibility permissions.
- Active-app insertion is disabled.
- There is no sandbox process isolation for the model runtime.
- There is no encrypted-at-rest storage.
- There is no structured runtime logging or audit event schema.

Unsigned Test RCs are not a trust or provenance guarantee. Verify release
checksums and test them in a non-critical environment.

The W1 controls above are documented and covered by the local headless suite,
but their finding state is `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`; they do not
close the broader signing, clean-machine, encryption or device-acceptance gaps.
