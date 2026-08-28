# Ollama-first profiles, persistent settings, and localized Help

Date: 2026-08-28  
Status: approved in chat for implementation

## Goal

Make VOICE Studio start quietly, persist every saved setting, use the user's
local Ollama model as the default speech and text-processing engine, offer
small reusable profiles for alternative engines, and render the complete Help
in the selected Ukrainian, Czech, or English interface language.

VOICE Studio remains a standalone local/private product. Hermes is not part of
the product. No telemetry, background cloud fallback, automatic model download,
or deletion of user originals is introduced.

## Confirmed current behavior and root causes

- `VoiceStudioApp` schedules `_first_run_model_prompt` on every launch. The
  prompt has no persisted dismissed state and repeats while the managed Whisper
  catalog is empty.
- A missing settings file returns in-memory defaults but is not written. Ollama
  discovery changes only a temporary dialog variable until **Save** is pressed.
- Previous packaged QA used an isolated `VOICE_STUDIO_CONFIG_DIR`; persistence
  worked there but correctly did not modify the real user profile.
- The current product exposes `faster-whisper` and `openai-cloud` as speech
  engines. Ollama is only a manual cleanup provider.
- The Help window controls are localized, but all canonical articles are
  Ukrainian.

## Verified local Ollama capability

The local runtime currently reports two installed models:

- `gemma4:12b`
- `gemma4-code:latest`

Both report the `audio` capability and both successfully transcribed a
deterministic synthetic English WAV through Ollama's loopback-only
OpenAI-compatible endpoint:

```text
POST http://127.0.0.1:11434/v1/chat/completions
content: text + input_audio (base64 WAV)
```

The native `/api/chat` audio field is not used because the installed Ollama
version can silently discard it. The engine sends no data outside
`127.0.0.1:11434`.

## Product behavior

### Quiet startup and persistence

- Remove the initial AI/model setup prompt completely.
- Startup never opens Models, Settings, or Help automatically.
- `load_settings` creates a valid settings file atomically when the profile is
  missing, so the defaults are durable from the first run.
- **Save** persists and applies every settings field. **Cancel** keeps the last
  saved values. A restart reproduces the saved state exactly.
- Ollama discovery never blocks the Tk main thread. On a new profile with no
  stored Ollama model, select and persist the preferred installed audio-capable
  model. Prefer a general audio model such as `gemma4:12b` over a `-code`
  variant; retain an existing stored selection even when it is temporarily
  unavailable.
- Missing/corrupt settings load safe in-memory defaults, preserve the corrupt
  file for diagnosis, and allow a valid Save to replace it.

### Reusable mini-profiles

Settings present three compact profile cards. Applying a card fills the related
engine, privacy, and cleanup fields; the user can still edit model details
before saving. The selected profile is persisted.

1. **Local Ollama** (default)
   - speech engine: `ollama`
   - transcription model: selected installed audio-capable Ollama model
   - text cleanup: the same local Ollama runtime/model
   - `offline_only`: enabled
2. **Local Whisper**
   - speech engine: `faster-whisper`
   - model/device/compute: saved Whisper values
   - automatic AI cleanup: disabled
   - `offline_only`: enabled
3. **OpenAI cloud**
   - speech engine: `openai-cloud`
   - manual text cleanup provider: OpenAI
   - automatic cleanup: disabled by default
   - `offline_only`: disabled
   - existing explicit upload consent and API-key requirements remain

There is no automatic fallback between profiles. In particular, an Ollama
failure never silently starts Whisper or uploads audio to OpenAI. `Codex` is not
misrepresented as a speech engine; the cloud profile uses the configured
OpenAI transcription model.

Existing `faster-whisper` and `openai-cloud` settings remain valid. If an older
file has no `profile`, it is classified from its existing engine rather than
silently replaced by the new default.

### Direct Ollama speech pipeline

```text
managed audio
  -> contained validation
  -> in-memory 16 kHz mono WAV conversion
  -> local Ollama /v1/chat/completions input_audio request
  -> immutable EngineResult/raw_text
  -> terminology dictionary/corrected_text
  -> optional second local Ollama cleanup pass
  -> LocalStore
```

- Add `OllamaAudioEngine`, implementing `SpeechEngine` and returning
  `EngineResult(engine="ollama", ...)`.
- The engine accepts the same validated source formats as the application and
  converts them to WAV in memory with the already bundled PyAV dependency.
- Bound encoded audio size and response size. Use a finite request timeout and
  the existing spawned transcription worker so cancellation can terminate an
  in-flight local request.
- Check `/api/show` for `audio` capability before transcription. A missing
  model/runtime or model without audio capability produces an actionable local
  error.
- Require non-empty assistant content. Do not use model reasoning text as the
  transcript.
- Ollama currently returns plain text without trustworthy timestamps. Create
  one segment from `0` to the measured media duration. Do not invent segment or
  word timing. The UI/Help describes this limitation for SRT/VTT output.
- `raw_text` and raw segment text remain immutable. The second local cleanup
  pass may update only `corrected_text` and segment `corrected_text` through the
  existing validated cleanup contract.
- If automatic cleanup fails, keep the successful saved transcript and its
  dictionary-corrected text, record a bounded warning, and report the warning
  without claiming the transcription was lost.
- `automatic_cleanup` is a persisted profile setting: enabled by default only
  for **Local Ollama**. Whisper and OpenAI profiles keep cleanup as an explicit
  user action unless the product later adds a separately consented option.
- Metadata records engine/model, local endpoint identity, conversion/timing
  limitations, and cleanup outcome. It never stores secrets or audio bytes.

### Settings information architecture

1. **Profiles**: three compact cards, active state, short privacy/quality note.
2. **General**: interface language, audio retention, hotkey, clipboard and
   offline privacy controls.
3. **Engine details**: transcription language; Ollama model/status; saved
   Whisper model/device/compute; OpenAI transcription/cleanup identifiers and
   key controls.

The ordinary readiness panel reports the selected profile and actual model.
Whisper settings remain visible only as details for the Whisper profile, not as
the default engine.

## Localized Help architecture

Canonical content remains in Git and becomes locale-aware:

```text
docs/help/
  help-index.json
  uk/*.md
  cs/*.md
  en/*.md
  assets/uk/*.png
  assets/cs/*.png
  assets/en/*.png
```

- `help-index.json` version 2 declares `uk`, `cs`, and `en` topic sets with
  matching slugs and topic order.
- `load_help_topics(root, language)` loads the exact locale. A release build
  must contain all three complete locales; no mixed-language fallback is
  shipped.
- Changing `ui_language` changes Help controls and article content. If Help is
  already open, it is rebuilt in the new language after Settings is saved.
- Search, topic titles, links, headings, alt text, screenshots, empty states,
  and troubleshooting text match the active locale.
- Validation checks locale parity, topic files, local links, fragments, images,
  and cross-locale targets.
- Frozen and wheel builds recursively include all locales and assets.
- `/sync-help` requires affected Ukrainian, Czech, and English topics to remain
  synchronized.

## Failure handling

- Ollama unavailable: retain the stored model and show how to start Ollama; no
  popup at application launch and no engine fallback.
- Ollama model missing or lacks audio: keep the selection, block transcription
  with an actionable Settings message, and list installed alternatives.
- Ollama cleanup failure after successful recognition: retain and show the
  transcript, with a localized warning.
- Missing managed Whisper model: report it only when the Whisper profile is
  selected and transcription starts; never download automatically.
- Missing Help locale or asset: fail validation/build. Runtime shows a localized
  Help-unavailable error instead of mixed-language content.

## Verification and acceptance criteria

- TDD regressions cover missing-profile creation, restart round-trip, legacy
  profile classification, retained missing Ollama selection, and quiet startup.
- Ollama engine tests cover capability checks, WAV conversion, request schema,
  response parsing, size/time bounds, single-duration segment, and errors.
- Job/service tests cover raw-text immutability, automatic local cleanup,
  cleanup failure with retained transcript, progress, and cancellation recovery.
- Profile/UI tests cover all three cards, saved application, and no silent
  fallback.
- Help tests cover three-locale parity, locale-specific search/content, links,
  anchors, assets, and already-open Help refresh after language change.
- Update UI labels, CLI settings output, README, architecture/status, and Help
  in all locales.
- Run compileall, Ruff, Help validation, full pytest, wheel inspection, Windows
  build, packaged runtime probe, and final EXE visual/restart verification.
- Verify on the final EXE: no startup popup; Local Ollama is the clean-profile
  default; the stored Ollama model survives a second launch; each interface
  language opens Help entirely in that language; Whisper and OpenAI are
  selectable profiles; no Hermes references exist.

Definition of done:

- a clean profile launches directly into the main window with a durable Local
  Ollama profile;
- direct local Ollama audio transcription works without Whisper or cloud;
- all saved settings survive restart;
- failures remain local, explicit, and never trigger another engine;
- Ukrainian, Czech, and English Help are complete and match the selected UI;
- the rebuilt Windows executable passes startup, profile, transcription, Help,
  and second-launch smoke checks.
