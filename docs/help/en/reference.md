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

For Local Whisper, **Device** is limited to `auto`, `cpu`, or `cuda`, and
**Compute type** uses the supported CTranslate2 vocabulary. **Detect hardware**
performs a bounded local advisory check in a background worker; it does not
load a model or change saved settings. If detection is unavailable, keep the
safe `auto/default` selection. Explicit combinations are checked against the
installed runtime before the Whisper model loads.

**VAD silence filter** is enabled by default and applies only to Local Whisper.
Turn it off if the filter clips quiet speech; the CLI equivalents are `--vad`
and `--no-vad`. The Ollama and OpenAI profiles ignore this setting.
| **OpenAI STT model** | Used only by the cloud profile. |
| **Dictionary JSON** | Deterministic replacements after recognition. |
| **Local Ollama model** | Installed model that reports `audio` capability. |
| **Refresh** | Rechecks local Ollama models in the background. |
| **OpenAI key** | Stored in the OS keychain, not settings.json. |

## Studio editor tools

| Button | Action |
|---|---|
| **B**, **I** | Bold and italic for the selected text. |
| **Find and replace** | Opens a panel under the editor: match case, whole word, a match count, **Replace** and **Replace all**. |
| **Add to dictionary** | Turns the selection into a rule and saves the managed dictionary. |
| **Filler words** | Shows every match with its context; only the checked ones are removed. |

All three tools change only the text in the editor. Nothing reaches storage
until **Save edits**, and `raw_text` stays unchanged. **Add to dictionary** is
refused while an external read-only dictionary is open or while the Dictionary
page has unsaved changes. Filler words come from the list for the recording
language, or, when that is `auto`, from the recognition language in Settings.

## Formats and limits

Input: WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV, WEBM; up to 2 GiB
and two hours. Ollama has a separate 30-minute per-transcription limit. Export:
TXT, MD, JSON, SRT, VTT. Ollama output has one timed segment. OpenAI STT accepts
only its documented formats and up to 25 MB.

## Model catalog recovery

The GUI reconciles the local Faster Whisper catalog once during startup, and
model-management commands reconcile it before they run. This applies to
`models list`, `models install`, `models verify`, `models remove`, and the
explicit recovery command. Reconciliation is local and offline; it does not
download, update, or delete a model implicitly.

To inspect or request recovery directly, run:

```text
voice-studio models reconcile
```

The command prints one JSON object with `status` (`PASS` or `FAIL`), `action`
(`none`, `repaired`, or `attention`), `adopted`, `dropped`, `blocked` (each
blocked item has `id`, `path`, and `reason`), `staging_removed`,
`staging_kept`, `residue_removed`, and `catalog_quarantined` (a path or
`null`). A failure also includes `error`.
Normal `models` commands report a non-trivial recovery result on stderr with
the `model-catalog:` prefix; healthy, unchanged catalogs stay quiet.

## Storage audit and explicit repair

Run the read-only storage inspection with:

```text
voice-studio storage audit
```

This audit command never reconciles the model catalog and never changes the live
data tree. It reads SQLite from a stable temporary database/WAL snapshot outside
the data tree, removes that temporary snapshot afterwards, and scans managed
sources, models, and exports without writing to them. Its top-level `status`
continues to describe SQLite and managed source health. `missing_records`
identifies transcript rows with a missing managed source. The nested
`model_catalog` object reports manifest, missing, orphan, blocked, staging, and
residue drift. The nested `exports` object lists regular `files`, conservative
`canonical_stale` candidates, `unmanaged` files, and unsafe or non-regular
`blocked` entries. Export candidates are a report only and are never deleted
automatically.

Automatic reconciliation still occurs during GUI startup and before every
`models` command. `voice-studio models reconcile` is the direct explicit
command. If a transcript points to a managed source that is proven missing,
detach only that stale reference with:

```text
voice-studio storage repair-missing TRANSCRIPT_ID --expected-path PATH --yes
```

`--expected-path` guards against repairing a row that changed since the audit;
use the exact path from `missing_records`. The repair clears only the stored
source reference and retention flag. It never deletes or recreates audio, never
changes transcript text, and never touches the user's original media file.

## Backup and local state

Restore replaces the backed-up transcripts, settings, and saved sources. The
current machine's local `models/` and `exports/` trees are preserved unchanged;
they deliberately remain **outside the archive** and are never added to a
`.voice-backup`.

Before changing the live root, the program validates those trees without
following links. A symlink, junction, or other Windows reparse point (as well
as another unsafe special entry) aborts restore **before** the live root is
changed. The user receives a concrete error such as `local restore state
contains an unsafe path: <path>`; current data remains in place.

## Privacy

- Ollama uses the fixed loopback endpoint `127.0.0.1:11434`.
- Cloud is never a fallback and requires an explicit profile and consent.
- `raw_text` and raw segment text are immutable.
- API keys are absent from settings, transcript metadata, backups, and diagnostics.
- The user's original media file is never deleted.
