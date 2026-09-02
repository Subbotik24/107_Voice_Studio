# Test RC release acceptance

Do not publish a tag until all entries have manual evidence attached to the
release issue:

- macOS Apple Silicon and Windows 10/11 x64: clean source launcher and frozen
  application smoke pass;
- microphone, WAV/MP3/M4A/MP4, model import/remove/export/retention pass;
- usability pack smoke on each OS: Dashboard statistics render for a non-empty
  history and refresh after save/delete; History combined filters find an old
  record past the newest 250; Dictionary add/import/export and a Local Whisper
  run with hotwords from `use_as_hint` rules; editor find/replace, add
  selection to dictionary and filler preview on a real transcript; confidence
  panel focus and segment playback; playback bar play/pause/seek/speed through
  a real audio device with an external original untouched on disk;
- feature-pack smoke on each OS: a queue of at least four mixed-format files
  with one corrupt file (error recorded, queue continues), Cancel mid-queue and
  Skip; Smart text with a speaker label and an MD export; the sync folder
  pointed at a real Google Drive or OneDrive client folder (files appear only
  through that client, no `source_path` in the JSON, a `~/...` entry is stored
  absolute, deleting a record leaves the mirror), Dashboard dynamics charts,
  the playback position slider, the status-bar progress during a queue, the
  in-window Settings/Help pages, and one launcher start with
  `VOICE_STUDIO_AUTO_UPDATE=1` online and one offline;
- 50 local tasks per OS, zero crashes and originals unchanged;
- one manual public-domain OpenAI STT call and cleanup proposal/apply/undo pass;
- checksums, release manifest and `models-v1` provenance are attached;
- release contains no API keys, user data, model weights in source Git, or files
  larger than GitHub's 2 GiB release-asset limit.

Live cloud credentials are never placed in GitHub Actions.
