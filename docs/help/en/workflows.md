# Main workflows

## Transcribe with Local Ollama

1. Start Ollama.
2. Select **Settings → Profiles → Local Ollama**.
3. Under the profile cards, select an audio-capable model and save.
4. On **Studio**, choose **Transcribe file…** and select the file.

Ollama returns plain text without trustworthy timestamps. VOICE Studio creates
one segment from zero to the measured recording duration and does not invent
timing.

## Switch profile

Open **Settings → Profiles**, select a card, then choose **Save**.

- **Local Ollama** — default: Ollama transcribes audio and automatically cleans
  the text on the local machine.
- **Local Whisper** — Faster Whisper transcribes locally with timed segments;
  automatic AI cleanup is off.
- **OpenAI cloud** — cloud STT; before every upload, the app shows the file name
  and size and asks for explicit consent.

Profile selection fills the related privacy and engine values. Model details
are preserved across restarts. A failed profile never starts another engine
automatically.

## Record from the microphone

### Hold to record

Hold **● Hold to record**, speak, then release the button to process the audio.

### Continuous recording

Choose **● Continuous recording**, speak without holding, then choose
**■ Stop recording**.

A recording is limited to two hours. If the app reports a damaged recording,
the safe choice is to reject it and record again.

## Edit and export

1. Open the new result or a record in **History**.
2. Edit **Corrected text** and choose **Save edits**.
3. Export as **TXT**, **MD**, **JSON**, **SRT**, or **VTT**.

**Original** is read-only. Ollama-profile SRT/VTT contains one segment for the
whole duration; use **Local Whisper** when accurate timed segments are needed.
Saving a manual edit keeps TXT/MD and SRT/VTT wording aligned. An edit across
timed segment boundaries merges those cues into their existing outer interval;
it never creates, splits, or moves a timecode.

## Batch transcription queue

1. On **Studio**, choose **Queue** to open the **Transcription queue** panel
   above the editor.
2. Add files with **Add files…**, or **Add folder…** (check **Include
   subfolders** to add its files recursively). Only supported media formats
   are queued; the status bar reports how many files were added and how many
   were rejected.
3. Choose **Start**. Files run one after another with the current profile —
   the same engine, language, and settings as a single file; a cloud profile
   still asks for the same per-file consent. **Pause**/**Resume** stops the
   queue after the current file finishes, or continues it. **Skip** drops the
   selected files that are still queued. **Remove finished** clears completed
   rows. **Clear** is refused while a file is running.

Each result is saved to **History**. A failed file records its error in the
queue's **Error** column and the queue moves on to the next file, with no
error popup. Cancelling the running job marks that file **Cancelled** and
pauses the queue. When the queue ends, the status bar shows a summary, and
only the last successful transcript opens in the editor — never over unsaved
edits. Closing the app pauses the queue.

## Smart text

1. Open a transcript, then select the **Smart text** tab next to **Data**.
2. Set **Pause, s** (gap that starts a new paragraph) and **Paragraph, s**
   (maximum paragraph length), and check **Timestamps** or **Speakers** to
   include them. Choose **Refresh**, or press Enter in either field, to
   rebuild the preview.
3. Choose **Copy** to copy the rendered text, or **Export MD…** /
   **Export TXT…** to save it to a file.
4. To label a speaker, select a segment in the **Segments** list, choose
   **Assign speaker…**, and enter a name — leave it empty to remove the
   label.

An invalid **Pause, s** or **Paragraph, s** value is reported in the status
bar and clears the preview. Speaker labels are stored only in transcript
metadata; `raw_text` and every segment stay exactly as recognised.

## Manual AI cleanup

Save manual edits, choose **AI cleanup…**, review Before/After, and confirm.
**Undo AI cleanup** restores the previous corrected text. Immutable `raw_text`
is never changed. OpenAI text cleanup requires separate consent.

## History and backup

- Search by part of the source name and choose **Search** or press Enter.
- **Rename** changes the record name, not the audio file.
- **Delete** asks separately about the managed audio copy. The user's original
  file is never deleted.
- **Backup** creates, verifies, or restores `.voice-backup`. Ollama/Whisper
  models and external originals are not included. Managed audio copies are
  included by default; clear the include-audio option (or use CLI
  `--without-audio`) to omit them. By default a plaintext v1 backup is
  created. The **Encrypt with passphrase** checkbox opts into an
  encrypted v2 backup: after choosing the file, the app asks for the
  passphrase twice in masked fields. **A lost passphrase cannot be
  recovered** — without it the archive is unreadable. The passphrase is never
  stored in settings, the restore journal, or diagnostics. Verifying or
  restoring an encrypted archive prompts once; a wrong passphrase is an
  authentication error with no plaintext reading, and cancelling the prompt
  changes no data and deletes no recovery state.

If a restore is interrupted by power loss or forced process termination, VOICE
Studio completes it or rolls it back on the next launch and reports the result
in the status bar. The recovery directory remains on disk in either case.
