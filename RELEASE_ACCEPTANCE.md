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
- 50 local tasks per OS, zero crashes and originals unchanged;
- one manual public-domain OpenAI STT call and cleanup proposal/apply/undo pass;
- checksums, release manifest and `models-v1` provenance are attached;
- release contains no API keys, user data, model weights in source Git, or files
  larger than GitHub's 2 GiB release-asset limit.

Live cloud credentials are never placed in GitHub Actions.
