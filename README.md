# Hermes Voice Studio 0.3.0 Test RC

Privacy-first desktop transcription for macOS Apple Silicon and Windows x64.
`faster-whisper` is the default local engine: audio, transcripts, history and
models remain on the device unless a user explicitly chooses a cloud action.

This is an **unsigned Test RC**, not a production release. Hermes Whisper is an
experimental research engine; it has no published accuracy claim or default
weights.

Українська версія: [README.uk.md](README.uk.md).

## Quick start from source

Requires Python 3.11 or 3.12. The one-click launchers create a local virtual
environment on first run and do not upgrade dependencies on subsequent runs.

```bash
# macOS: install Python/Tk, FFmpeg and PortAudio first, then
./run_mac.command

# Windows 10/11 x64
run_windows.bat
```

Install a local model only after choosing it:

```bash
hermes-voice models install tiny
hermes-voice transcribe recording.wav --engine faster-whisper --model tiny
```

`tiny` is the starter profile; `small` usually gives better quality but needs
substantially more disk space. Offline import is available with
`hermes-voice models install tiny --from-directory PATH`. Set
`HVS_MODEL_REGISTRY_URL` to the published `models-v1` registry URL to install
the verified GitHub Release packs; no model binary belongs in Git.

## Optional OpenAI features

Cloud use is opt-in, never a fallback. Configure a key in an environment
variable or the operating-system keychain:

```bash
export OPENAI_API_KEY="..."  # current shell only
hermes-voice cloud key set   # OS keychain instead
hermes-voice cloud key status
```

Cloud transcription requires explicit consent for every CLI invocation and
rejects files larger than 25 MB rather than silently splitting/compressing them:

```bash
hermes-voice transcribe recording.m4a --engine openai-cloud --allow-cloud-upload
```

AI cleanup sends only the editable `corrected_text` and segment indexes, never
immutable `raw_text`. It prints a proposal by default; saving additionally
requires `--apply`.

```bash
hermes-voice cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text
hermes-voice cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text --apply
hermes-voice cleanup-undo TRANSCRIPT_ID
```

`offline_only` blocks both cloud paths. The default cloud models are
`gpt-transcribe` and `gpt-4.1-mini-2025-04-14`; both are configurable settings,
not hardcoded credentials.

## Test RC artifacts

The planned release assets are:

- `Hermes-Voice-Studio-0.3.0-test-rc1-macos-arm64-unsigned.dmg`
- `Hermes-Voice-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
- wheel, release manifest and `SHA256SUMS.txt`

macOS Gatekeeper and Windows SmartScreen warnings are expected because these
artifacts are unsigned. Download only from the project GitHub Release, verify
checksums, and test on non-critical data. No Python is required for the frozen
artifacts.

## Development and checks

```bash
python -m pip install -e ".[dev,cloud]"
python -m compileall -q src tests
PYTHONPATH=src pytest -q
ruff check src tests
python -m build --wheel
python -m pip check
```

The CI workflows run those checks on Python 3.11/3.12 for macOS ARM64 and
Windows x64. Live cloud calls are intentionally excluded from CI.

## Data and privacy

The original user file is never deleted. `raw_text` is immutable STT output;
editing and AI cleanup operate on `corrected_text`. API keys are resolved from
`OPENAI_API_KEY` first, then OS Keychain/Credential Manager; they are not put in
settings, backups, worker messages, diagnostics or Git.

Use `hermes-voice diagnostics --export report.json` for a redacted forum report.
Do not share private audio, transcripts, databases, API keys, or full user paths.

## Project layout

```text
src/hermes_voice_studio/   GUI, CLI, local storage, engines, cloud adapters
src/hermes_whisper/        experimental trainable Hermes STT research runtime
tests/                     regression and contract tests
scripts/                   quality, model-release and packaging helpers
docs/                      architecture and data contracts
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before publishing or testing.
