# Troubleshooting

## Ollama is unavailable

Start Ollama, check `ollama list`, then open **Settings → Local AI**, choose
**Refresh**, select a model, and save. VOICE Studio uses the fixed local address
`127.0.0.1:11434`.

## The Ollama model does not support audio

Select a model that Ollama reports with `audio` capability. VOICE Studio does
not install or update Ollama models and never switches to Whisper without your
selection.

## Ollama returned no transcript

In **Settings → Recognition**, select the language that matches the recording
or choose `auto`, then retry a short, clear sample. If the model does not
support the spoken language, select the **Local Whisper** profile. VOICE Studio
does not switch engines automatically.

## The Ollama recording is longer than 30 minutes

The Ollama profile stops local conversion at 30 minutes instead of growing an
unbounded WAV buffer. Select **Local Whisper** for a longer recording, or split
a working copy without overwriting the original.

## AI cleanup failed

If the transcript opened with a cleanup warning, the first audio transcription
was saved and is not lost. Check Ollama and run **AI cleanup…** manually, or edit
the text directly.

## Whisper model is not installed

This error applies only to **Local Whisper**. Open **Models**, import or download
a compatible model, verify it, and save its exact ID in Settings.

## The media file cannot be opened

Check the supported extension, audio stream, local playback, and the 2 GiB / two
hour limits. Convert an unusual codec to a separate WAV or MP3 copy; do not
overwrite the original.

## Settings did not persist

Choose **Save**, not **Cancel**. If settings JSON is damaged, review every tab
and save valid values. The same profile and models must appear after restart.

## SmartScreen or another unresolved issue

The Test RC is not digitally signed; verify the ZIP source and SHA-256. Create
a redacted support report with:

```text
voice-studio diagnostics --export report.json
```

Do not attach private audio, transcript text, API keys, a database, a backup,
or complete local paths.

## Restore was interrupted and history looks empty

If backup restore was interrupted and `*.restore-*` and `*.recovery-*`
directories are present beside storage, launch VOICE Studio again. The app uses
the restore journal to deterministically complete or roll back the operation
and reports the result in the status bar. If a journal warning appears, do not
delete anything manually; create a redacted diagnostics report. A
`*.recovery-*` directory is never removed automatically and should be deleted
only after history has been checked.
