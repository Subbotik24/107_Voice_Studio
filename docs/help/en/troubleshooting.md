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

## Model catalog recovery needs attention

Run the offline recovery command and inspect its JSON result:

```text
voice-studio models reconcile
```

An incomplete model directory is retained in place for manual inspection; it
is not adopted, moved, or deleted automatically. After checking the path, a
user-confirmed `voice-studio models remove MODEL_ID --yes` can remove an
unmanaged directory. A corrupt `catalog.json` is quarantined under a timestamped
`catalog.json.corrupt-*` name and rebuilt; the damaged manifest is retained and
is never deleted automatically. The `blocked` entries in the JSON identify the
path and reason requiring attention.

## A managed recording is reported as missing

Run `voice-studio storage audit` again and find the transcript ID and exact path
in `missing_records`. The audit is read-only: model and export drift is reported
in the nested `model_catalog` and `exports` objects but is not repaired or
deleted. A `canonical_stale` export is only a conservative candidate and is
never removed automatically.

First verify outside VOICE Studio that the managed copy is truly absent. If the
row should remain in history without retained audio, explicitly detach only the
missing reference:

```text
voice-studio storage repair-missing TRANSCRIPT_ID --expected-path PATH --yes
```

If the expected path does not match, the file has reappeared, or the path is
unsafe, the command refuses to change the row. A successful repair does not
delete or recreate audio and does not alter transcript text or the user's
original file. Model drift is repaired separately, when appropriate, with
`voice-studio models reconcile`.

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
