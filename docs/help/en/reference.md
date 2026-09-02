# Screens and settings reference

## Left menu

| Item | Purpose |
|---|---|
| **Dashboard** | Local whole-history statistics and the latest records. |
| **Studio** | Recording, transcription, and the editor. |
| **Dictionary** | The managed terminology dictionary with import/export. |
| **History** | Search and combined record filters; opens a record in the Studio. |
| **Models** | Manage Faster Whisper models only. |
| **Backup** | Create, verify, and restore backups. |
| **Settings** | Central page: profiles, languages, Ollama, Whisper, and OpenAI. |
| **Help** | Central page with this manual; also F1. |

Settings and Help open inside the main window, like Dashboard or Studio. Leaving
the Settings page with unsaved changes asks whether to save, discard, or stay.
Models and Backup remain separate windows.

Dashboard's **Dynamics** card adds two charts under the summary tiles: a
14-day daily activity bar chart (today rightmost, a value label over each
non-zero day) and a language/engine distribution chart of the top entries,
with the rest folded into "other". Both are drawn directly on the page and
redraw when the window is resized; with no activity in the window, a chart
shows an empty-state message instead of empty bars. The status bar on every
page shows a slim progress indicator while a job is running — indeterminate
by default, switching to a percent while a model downloads — and, while the
transcription queue is active, a compact `done/total` counter next to it.

## Profiles

| Profile | Recognition | AI cleanup | Network |
|---|---|---|---|
| **Local Ollama** | Ollama audio model | Ollama automatically | Loopback only |
| **Local Whisper** | Faster Whisper | Off | Local |
| **OpenAI cloud** | Saved OpenAI STT model | OpenAI manually | Explicit consent |

Engine settings sit directly under the profile cards and switch with the
selected profile: the Ollama model and local AI cleanup, the Faster Whisper
parameters, or the OpenAI models and key. There is no separate Local AI tab any
more. When no installed Ollama model advertises the `audio` capability, the list
falls back to every installed model with a warning, and the choice stays yours.

## General

| Field | Values / behavior |
|---|---|
| **Interface language** | `Українська`, `Čeština`, `English`; also changes Help. |
| **Audio retention** | `keep` or `delete_after_transcription`; never deletes the original. |
| **Global hotkey** | Default `<f13>`; capture a replacement in Settings. |
| **Automatically copy** | Off by default. |
| **Offline-only** | Controlled by the profile; local profiles block cloud actions. |

## Synchronisation

A local mirror for transcripts — the privacy-safe alternative to cloud sync:
the app never makes a network call of its own, it only writes files into a
chosen folder, which can point at any folder that a third-party client of
your own choice syncs (Google Drive, OneDrive, etc.).

| Field | Values / behavior |
|---|---|
| **Mirror transcripts to a folder** | Turns on auto-mirroring; requires a folder to be set. |
| **Sync folder** + **Choose…** | Picked through the system folder-choice dialog. |
| **Also copy audio** | Adds the retained managed audio copy to the mirror, when one exists. |
| **Sync all now** | Mirrors every stored transcript on a background worker and reports a summary on the status bar. |

Each transcript is written as a Markdown + JSON pair (deterministic file names
by date and id); the app never deletes anything from this folder and never
stores any API keys in it. An invalid folder (missing, a file instead of a
directory, a symlink, or one inside/around the private data folder) is
refused on Save with the reason shown. Mirroring also runs automatically
after a transcription finishes, after an editor save, after a speaker is
assigned, and after AI cleanup is applied; any mirroring failure only shows
on the status bar and never undoes the save that triggered it. The path is
stored as a resolved absolute path (a `~/Drive` entry becomes the full path),
and the folder check is repeated before every write: a folder that has gone
missing, been replaced by a symlink or moved inside the data folder only
produces a status-bar message, and the app never recreates it. Deleting a
record from History does not delete its mirrored files.

## Recognition

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
| **Local Ollama model** | Installed model that reports `audio` capability; with none, every installed model plus a warning. |
| **Refresh** | Rechecks local Ollama models in the background. |
| **OpenAI key** | Stored in the OS keychain, not settings.json. |

## Studio editor tools

| Button | Action |
|---|---|
| **B**, **I** | Bold and italic for the selected text. |
| **Find and replace** | Opens a panel under the editor: match case, whole word, a match count, **Replace** and **Replace all**. |
| **Add to dictionary** | Turns the selection into a rule and saves the managed dictionary. |
| **Filler words** | Shows every match with its context; only the checked ones are removed. |
| **Confidence score** | Lists the segments with the lowest confidence score for review. |

These tools change only the text in the editor. Nothing reaches storage
until **Save edits**, and `raw_text` stays unchanged. **Add to dictionary** is
refused while an external read-only dictionary is open or while the Dictionary
page has unsaved changes. Filler words come from the list for the recording
language, or, when that is `auto`, from the recognition language in Settings.

### Confidence review

**Confidence score** opens a panel that lists the segments whose score is below
the threshold, the lowest score first. The score is the recognition engine's own
confidence signal for that segment: it is not an error probability and not a
guarantee of accuracy, so treat the list as a reading order, not a verdict.
Segments the engine reported without a score are listed after the scored ones
and marked **no score**.

The threshold starts at 0.60 and can be set between 0.00 and 1.00. It belongs to
the open page only: it is never written to settings or to disk, so the next
session starts at 0.60 again. Selecting a row highlights the matching segment in
the editor and moves the cursor there; when the segment text has already been
edited beyond recognition, the panel says so instead of jumping. **Play segment**
starts local playback of the retained audio from that segment's start.

### Local playback

The playback bar under the editor plays the retained managed audio copy:
play/pause, stop, ±5-second seeking and 0.75–2× speed. Speed is implemented by
resampling, so a faster pace also raises the pitch. A seek slider follows the
playing position and can be dragged to any point in the audio; it stays still
while you hold it and only jumps on release, and it is disabled when nothing
is playable. Only the managed copy in the application's storage is played; the
external original file is never looked up or opened. When a record has no
retained audio, the bar says so. Switching the page or the record, and
restoring from a backup, stop playback.

## Batch transcription queue

| Control | Purpose |
|---|---|
| **Queue** | Toggles the **Transcription queue** panel above the editor. |
| **Add files…** | Opens a file picker; only supported media formats are added. |
| **Add folder…**, **Include subfolders** | Adds every supported file in a folder, recursively when checked. |
| **Start** | Runs the pending files one after another with the current profile. |
| **Pause** / **Resume** | Stops the queue after the current file, or continues it. |
| **Skip** | Skips the selected files that are still queued. |
| **Remove finished** | Clears done, failed, skipped, and cancelled rows. |
| **Clear** | Empties the queue; refused while a file is running. |

The table columns are **File**, **Status**, **Seconds**, and **Error**. The
queue holds at most 500 files. A failed or cancelled item records its reason
in **Error** and the queue continues with the next file. Only the last
successful transcript opens in the editor, and never over unsaved edits;
closing the app pauses the queue.

## Smart text tab

| Control | Purpose |
|---|---|
| **Pause, s** | Gap between segments that starts a new paragraph (0–600 s, default 2.0). |
| **Paragraph, s** | Maximum paragraph length before it is split (5–3600 s, default 90). |
| **Timestamps**, **Speakers** | Include timestamps or speaker labels in the rendered text. |
| **Refresh** | Rebuilds the preview from the current transcript and options. |
| **Copy** | Copies the rendered text to the clipboard. |
| **Export MD…**, **Export TXT…** | Saves the rendered text as Markdown or plain text; the default file name is the source file's stem. |
| **Segments**, **Assign speaker…** | Lists every segment (index · timestamp · snippet); select one and enter a name to label it, or leave the name empty to remove the label. |

An invalid **Pause, s** or **Paragraph, s** value shows "The pause must be 0
to 600 s and a paragraph 5 to 3600 s." in the status bar and clears the
preview. Speaker labels are stored only in transcript metadata; `raw_text`
and segment text are never changed. With no transcript selected, the tab
shows "Select a transcript to see its smart text."

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
