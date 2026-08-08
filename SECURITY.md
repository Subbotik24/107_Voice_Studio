# Security and privacy

- Хмарні adapters відсутні.
- User original media не видаляється.
- `delete_after_transcription` видаляє лише managed copy, якщо її не використовує інший transcript record.
- Source content має SHA‑256 provenance.
- training checkpoint вимагає exact SHA‑256 manifest для всіх обов'язкових
  files; `latest.json` не може посилатися за межі training run.
- `.hws` перевіряє internal member SHA‑256 і exact member set.
- ZIP member extraction виконується у фіксовані імена без довільних paths.
- Model files не повинні потрапляти у Git.

## Невирішені production‑питання

- `.hws` ще не має publisher signature;
- native installers не підписані;
- global hotkey залежить від OS Accessibility permissions;
- active-app insertion вимкнено;
- немає sandbox process isolation для model runtime;
- немає configurable archive/model resource limits;
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
