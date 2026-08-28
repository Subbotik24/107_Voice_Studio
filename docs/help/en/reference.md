# Screens and settings reference

## Left menu

| Item | Purpose |
|---|---|
| **Studio** | Recording, transcription, editor, and history. |
| **Models** | Manage Faster Whisper models only. |
| **Backup** | Create, verify, and restore backups. |
| **Settings** | Profiles, languages, Ollama, Whisper, and OpenAI. |
| **Help** | Opens this manual; also F1. |

## Profiles

| Profile | Recognition | AI cleanup | Network |
|---|---|---|---|
| **Local Ollama** | Ollama audio model | Ollama automatically | Loopback only |
| **Local Whisper** | Faster Whisper | Off | Local |
| **OpenAI cloud** | Saved OpenAI STT model | OpenAI manually | Explicit consent |

## General

| Field | Values / behavior |
|---|---|
| **Interface language** | `Українська`, `Čeština`, `English`; also changes Help. |
| **Audio retention** | `keep` or `delete_after_transcription`; never deletes the original. |
| **Global hotkey** | Default `<f13>`; capture a replacement in Settings. |
| **Automatically copy** | Off by default. |
| **Offline-only** | Controlled by the profile; local profiles block cloud actions. |

## Recognition and Local AI

| Field | Purpose |
|---|---|
| **Engine** | Current engine selected by the profile. |
| **Transcription language** | `auto`, `uk`, `cs`, `en`; independent of UI language. |
| **Model / Device / Compute type** | Saved Faster Whisper details. |
| **OpenAI STT model** | Used only by the cloud profile. |
| **Dictionary JSON** | Deterministic replacements after recognition. |
| **Local Ollama model** | Installed model that reports `audio` capability. |
| **Refresh** | Rechecks local Ollama models in the background. |
| **OpenAI key** | Stored in the OS keychain, not settings.json. |

## Formats and limits

Input: WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV, WEBM; up to 2 GiB
and two hours. Ollama has a separate 30-minute per-transcription limit. Export:
TXT, MD, JSON, SRT, VTT. Ollama output has one timed segment. OpenAI STT accepts
only its documented formats and up to 25 MB.

## Privacy

- Ollama uses the fixed loopback endpoint `127.0.0.1:11434`.
- Cloud is never a fallback and requires an explicit profile and consent.
- `raw_text` and raw segment text are immutable.
- API keys are absent from settings, transcript metadata, backups, and diagnostics.
- The user's original media file is never deleted.
