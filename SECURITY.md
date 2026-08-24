# Security and privacy

- Customer-facing product name: **VOICE Studio**. The existing
  `hermes_voice_studio` package and `hermes-voice` CLI names remain compatibility
  interfaces; this naming does not imply a separate cloud service.
- Local transcription є default. OpenAI STT та AI cleanup adapters присутні,
  але кожна cloud-операція вимагає явної згоди; `offline_only` її блокує.
- User original media не видаляється.
- `delete_after_transcription` видаляє лише managed copy, якщо її не використовує інший transcript record.
- Clipboard auto-copy is disabled by default (`auto_copy=false`). Copying is an
  explicit user action and must be treated as leaving the app boundary because
  clipboard history, manager processes and OS sync are controlled by the host.
- Editor navigation and close use a dirty Save/Discard/Cancel prompt. Save
  persists the editable `corrected_text` layer and formatting; immutable
  `raw_text` is never rewritten.
- Microphone capture is recorder-owned under the private app-cache recordings
  directory. It streams 100 ms blocks through a bounded 64-block queue, has a
  two-hour limit, surfaces sounddevice status/queue-drop warnings, and rejects
  degraded capture by default. Cleanup is scoped to tracked recorder-owned
  paths; identity ambiguity retains and reports residue rather than guessing.
- Source content має SHA‑256 provenance.
- training checkpoint вимагає exact SHA‑256 manifest для всіх обов'язкових
  files; `latest.json` не може посилатися за межі training run.
- `.hws` перевіряє internal member SHA‑256 і exact member set.
- ZIP member extraction виконується у фіксовані імена без довільних paths.
- Model files не повинні потрапляти у Git.

## Невирішені production‑питання

- Native acceptance is still **NOT RUN** on physical Windows/macOS: normal and
  continuous microphone capture, overflow/device disconnect, the two-hour
  limit, close during capture/transcription, and clipboard history/sync remain
  unverified. See the exact acceptance gate in
  `docs/PROJECT_AUDIT_STATUS.md`.
- `.hws` ще не має publisher signature;
- native installers не підписані;
- global hotkey залежить від OS Accessibility permissions;
- active-app insertion вимкнено;
- немає sandbox process isolation для model runtime;
- model-release ZIP має resource limits, але `.hws` і backup ще не мають
  повного набору member/count/expanded-size/compression-ratio limits;
- немає encrypted-at-rest storage.

Звіт про вразливість повинен містити ОС, версію програми, мінімальний сценарій відтворення і вплив. Не прикладати приватне аудіо.
# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability or secret. Send a
minimal reproduction without private audio, transcript text, API keys, backups,
databases or absolute user paths to the repository security contact once one is
configured. Until then, do not publish the finding.

## Security defaults

- Local transcription is the default; cloud use requires explicit consent.
- API keys are read from `OPENAI_API_KEY` or the OS keychain only.
- Source files, `raw_text`, model packs and backups are never uploaded by a
  background operation.
- Model archives require HTTPS, size checks, SHA-256, safe ZIP validation and
  atomic installation.
- `diagnostics --export` creates a redacted report.

Unsigned Test RCs are not a trust or provenance guarantee. Verify release
checksums and test them in a non-critical environment.

The W1 controls above are documented and covered by the local headless suite,
but their finding state is `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE`; they do not
close the broader release, diagnostics, restore/shutdown, encryption, Hermes,
or packaging gaps.
