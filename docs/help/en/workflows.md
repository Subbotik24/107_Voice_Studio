# Main workflows

## Transcribe with Local Ollama

1. Start Ollama.
2. Select **Settings → Profiles → Local Ollama**.
3. On **Local AI**, select an audio-capable model and save.
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
  models and external originals are not included.

If a restore is interrupted by power loss or forced process termination, VOICE
Studio completes it or rolls it back on the next launch and reports the result
in the status bar. The recovery directory remains on disk in either case.
