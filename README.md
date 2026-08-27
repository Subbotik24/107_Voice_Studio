# VOICE Studio 0.3.0 Test RC

Privacy-first desktop transcription for macOS Apple Silicon and Windows x64.
`faster-whisper` is the default local engine: audio, transcripts, history and
models remain on the device unless a user explicitly chooses a cloud action.
The current customer-facing brand is **VOICE Studio**. Existing
`voice_studio` package paths and the `voice-studio` CLI are retained for
compatibility.

This is an **unsigned Test RC**, not a production release. The W1 desktop
data-safety controls below still require native Windows/macOS acceptance.

Українська версія: [README.uk.md](README.uk.md).

## Quick start from source

Requires Python 3.11 or 3.12. The one-click launchers create a local virtual
environment on first run and do not upgrade dependencies on subsequent runs.

```bash
# macOS: install Python/Tk and PortAudio first, then
./run_mac.command

# Windows 10/11 x64
run_windows.bat
```

Install a local model only after choosing it:

```bash
voice-studio models install tiny
voice-studio transcribe recording.wav --engine faster-whisper --model tiny
```

`tiny` is the starter profile; `small` usually gives better quality but needs
substantially more disk space. Offline import is available with
`voice-studio models install tiny --from-directory PATH`. Set
`VOICE_STUDIO_MODEL_REGISTRY_URL` to the published `models-v1` registry URL to
install the verified GitHub Release packs; no model binary belongs in Git.

## Interface language and local Ollama

Open **Settings** to switch the interface between Ukrainian, Czech and English.
The change is saved locally and applied immediately.

Local Ollama is the default AI-cleanup provider. When Ollama is running on its
standard loopback endpoint, **Settings → Local Ollama model** lists the models
already installed on this computer. No API key or cloud-consent switch is
needed for that local path. You can refresh the list in the same dialog or use
the CLI:

```bash
voice-studio cleanup TRANSCRIPT_ID --provider ollama --model MODEL_NAME
```

VOICE Studio never installs, updates or deletes Ollama models. If an installed
model cannot load, the app shows the error returned by the local Ollama runtime
so another installed model can be selected.

## Optional OpenAI features

Cloud use is opt-in, never a fallback. Configure a key in an environment
variable or the operating-system keychain:

```bash
export OPENAI_API_KEY="..."  # current shell only
voice-studio cloud key set   # OS keychain instead
voice-studio cloud key status
```

Cloud transcription requires explicit consent for every CLI invocation and
rejects files larger than 25 MB rather than silently splitting/compressing them:

```bash
voice-studio transcribe recording.m4a --engine openai-cloud --allow-cloud-upload
```

AI cleanup sends only the editable `corrected_text` and segment indexes, never
immutable `raw_text`. It prints a proposal by default; saving additionally
requires `--apply`.

```bash
voice-studio cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text
voice-studio cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text --apply
voice-studio cleanup-undo TRANSCRIPT_ID
```

`offline_only` blocks both cloud paths. The default cloud models are
`gpt-transcribe` and `gpt-4.1-mini-2025-04-14`; both are configurable settings,
not hardcoded credentials.

## Test RC artifacts

On Windows 10/11 x64, install 64-bit Python 3.12 and run
`build_windows_exe.bat`. The locked clean build writes the executable and
portable archive to `dist\0.3.0-test-rc1-windows-x64\`; see
[WINDOWS_BUILD_README.md](WINDOWS_BUILD_README.md) for the short release notes.

The planned release assets are:

- `VOICE-Studio-0.3.0-test-rc1-macos-arm64-unsigned.dmg`
- `VOICE-Studio-0.3.0-test-rc1-windows-x64-unsigned.zip`
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

Clipboard disclosure is opt-in: `auto_copy` defaults to `false`; automatic
copying occurs only when the user explicitly enables `auto_copy`. Otherwise,
placing text in the OS clipboard requires the explicit Copy action. Clipboard
history, manager processes and OS sync can retain or disclose copied text.
Unsaved editor changes trigger a Save/Discard/Cancel prompt before history
navigation or close; Save persists only `corrected_text` and formatting.

Microphone capture is recorder-owned and private under the app cache. It streams
100 ms blocks through a bounded 64-block queue, stops at two hours, surfaces
overflow/status degradation, and rejects degraded capture by default. The app
cleans only tracked recorder-owned temporary files; an identity-ambiguous
cleanup residue is retained and reported for inspection. These contracts have
headless test evidence, while real microphones, device disconnects, OS
clipboard history/sync and physical Windows/macOS acceptance remain **NOT RUN**.
Cleanup is not secure deletion: a malicious same-account replacement after the
final identity check is an accepted residual outside the selected
OS-account/full-disk-encryption boundary, and no absolute delete-by-handle
guarantee is claimed.

Use `voice-studio diagnostics --export report.json` for a redacted forum report.
Do not share private audio, transcripts, databases, API keys, or full user paths.

## Project layout

```text
src/voice_studio/          GUI, CLI, local storage, engines, cloud adapters
tests/                     regression and contract tests
scripts/                   quality, model-release and packaging helpers
docs/                      architecture and data contracts
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before publishing or testing.
