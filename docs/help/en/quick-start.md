# Quick start

Goal: create your first private transcript with the installed local Ollama.

## 1. Start the application

Open **VOICE Studio.exe**. The main window opens directly; no initial AI setup
wizard is shown. For the portable build, extract the whole ZIP and keep the
neighboring **VOICE Studio** folder with the executable.

## 2. Check the local model

1. Start Ollama in Windows.
2. Open **Settings → Profiles**.
3. Keep **Local Ollama** selected.
4. Under the profile cards, check **Local Ollama model**.
5. If the list is empty, choose **Refresh**, then **Save**.

On first launch, VOICE Studio finds only models that Ollama reports as accepting
`audio`. When no model has been saved yet, it selects and persists a suitable
installed model. An existing saved choice is never silently replaced.

## 3. Prepare input

Use a voice recording in WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV,
or WEBM. It must contain an audio stream, be non-empty, and be no larger than
2 GiB or two hours. **Local Ollama** accepts up to 30 minutes per transcription;
select **Local Whisper** for a longer recording.

## 4. Transcribe

1. On **Studio**, choose **Transcribe file…**.
2. Select the media file.
3. Wait for import, transcription, saving, and local AI cleanup.
4. To stop, choose **Cancel**.

Audio is sent only to Ollama on `127.0.0.1:11434`. Whisper and OpenAI are not
used as hidden fallbacks.

## 5. Result

- **Corrected text** is the editable dictionary/AI-cleaned version;
- **Original** is the immutable first recognition result;
- **Data** shows engine, model, duration, and metadata;
- **History** stores the transcript locally.

If the second Ollama request for text cleanup fails, the transcript is still
saved and opened with a warning.

## 6. Next steps

[Record from the microphone](workflows.md#record-from-the-microphone)

[Switch profile](workflows.md#switch-profile)

[Edit and export](workflows.md#edit-and-export)

[Ollama is unavailable](troubleshooting.md#ollama-is-unavailable)
