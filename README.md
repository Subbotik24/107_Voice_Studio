# VOICE Studio 0.3.0 Test RC

Privacy-first desktop transcription for macOS Apple Silicon and Windows x64.
Ollama is the default local engine: one installed audio-capable Ollama model
transcribes the recording and then improves the editable text locally. Audio,
transcripts, history and settings remain on the device unless a user explicitly
chooses the OpenAI cloud profile.
The current customer-facing brand is **VOICE Studio**. Existing
`voice_studio` package paths and the `voice-studio` CLI are retained for
compatibility.

This is an **unsigned Test RC**, not a production release. The W1 desktop
data-safety controls below still require native Windows/macOS acceptance.

Українська версія: [README.uk.md](README.uk.md).

User help: [Українська](docs/help/uk/quick-start.md) ·
[Čeština](docs/help/cs/quick-start.md) · [English](docs/help/en/quick-start.md).

## Quick start from source

Requires Python 3.11 or 3.12. The one-click launchers create a local virtual
environment on first run and do not upgrade dependencies on subsequent runs.

```bash
# macOS: install Python/Tk and PortAudio first, then
./run_mac.command

# Windows 10/11 x64
run_windows.bat
```

Start Ollama and make sure an installed model supports audio input. VOICE Studio
discovers those models without installing, updating or deleting them:

```bash
ollama list
voice-studio transcribe recording.wav --engine ollama --ollama-model gemma4:12b
```

Whisper is an optional local profile when timed subtitle segments are needed.
Install its model only after choosing that profile:

```bash
voice-studio models install tiny
voice-studio transcribe recording.wav --engine faster-whisper --model tiny
```

`small` usually gives better Whisper quality than `tiny` but needs substantially
more disk space. Offline import is available with
`voice-studio models install tiny --from-directory PATH`. Set
`VOICE_STUDIO_MODEL_REGISTRY_URL` to the published `models-v1` registry URL to
install the verified GitHub Release packs; no model binary belongs in Git.

## Saved profiles, interface language and local Ollama

Open **Settings → Profiles** to switch between **Local Ollama** (default),
**Local Whisper**, and **OpenAI cloud**. Profile, model, interface language and
the remaining settings are saved locally and restored on the next launch. No
first-run AI setup window is shown.

The interface and in-app Help switch together between Ukrainian, Czech and
English. When Ollama is running on its standard loopback endpoint,
**Settings → Local Ollama model** lists the audio-capable models already
installed on this computer. No API key or cloud-consent switch is needed.

For the Local Whisper profile, **Settings → Recognition** constrains hardware
choices to the supported `auto`/`cpu`/`cuda` and CTranslate2 compute-type
vocabulary. **Detect hardware** performs a bounded local advisory check in a
background worker; it does not load a model, contact the network, or change
saved settings. If the optional runtime cannot be checked, keep the safe
`auto/default` selection. Explicit device/compute combinations are checked
again against the installed CTranslate2 runtime before a Whisper model loads.

The same profile exposes a **VAD silence filter** setting, enabled by default.
Disable it in Settings or with `voice-studio transcribe --no-vad` when the
filter clips quiet speech; the Ollama and OpenAI profiles ignore it.

In the default profile, the selected Ollama model performs direct speech
transcription and automatic local cleanup. The immutable STT result remains in
`raw_text`; cleanup changes only `corrected_text`. A cleanup failure is shown to
the user and does not discard the saved transcript. Ollama input is bounded to
30 minutes per transcription; use the Local Whisper profile for longer media.

Explicit cleanup remains available from the CLI:

```bash
voice-studio cleanup TRANSCRIPT_ID --provider ollama --model MODEL_NAME
```

VOICE Studio never installs, updates or deletes Ollama models. If an installed
model cannot load, the app shows the error returned by the local Ollama runtime
so another installed model can be selected.

## Workspace: Dashboard, Studio, Dictionary, History

The left menu opens four local pages — **Dashboard**, **Studio**, **Dictionary**
and **History** — next to the Models, Backup, Settings and Help dialogs. The
dirty Save/Discard/Cancel prompt also guards navigation between pages.

**Dashboard** aggregates the whole local history on the device: totals,
completed and failed records, recognized words, total audio duration, average
speed, retained audio copies, activity over the last 7 and 30 days, and the most
used languages, engines and models. It streams every stored record instead of
the rows the History view shows, performs no network call, and counts records
whose payload cannot be read as invalid — those point at
`voice-studio storage audit` instead of breaking the page.

**History** combines the text, date, language, engine, model, status and
retained-audio filters and applies them across the whole store before the
250-record display limit, so a match outside the newest records is still listed.

**Dictionary** manages the deterministic replacement rules. The managed
dictionary is stored locally beside the other application data and can be
imported or exported as JSON or CSV. In the Local Whisper profile the terms of
the rules marked as hints are additionally passed to recognition as bounded
per-request hotwords; they are never written into settings, transcripts,
metadata, diagnostics or logs.

## Studio editor tools

The editor toolbar adds four tools next to the formatting buttons:

- **Find and replace** — literal Unicode search with a match count and
  highlighting, replace from the cursor with wrap, and replace all.
- **Add to dictionary** — turns the selection into a managed dictionary rule and
  applies it to the open editor only. It is refused while an external read-only
  dictionary is loaded or the Dictionary page has unsaved changes.
- **Filler words** — a preview with one checkbox and context per match; only the
  checked matches are removed. The list follows the recording language and falls
  back to the recognition language in Settings when that is `auto`.
- **Confidence score** — a panel listing the segments below a threshold
  (0.60 by default, page state only, never written to settings or disk), lowest
  score first, with **no score** for segments the engine reported without one.
  The value is the engine's own confidence signal for that segment; it is not an
  error probability and not an accuracy claim.

These tools change only the text in the open editor. Nothing reaches storage
before **Save edits**, and `raw_text` stays immutable.

The playback bar under the editor plays **only the retained managed audio
copy**: play/pause, stop, ±5 s and 0.75–2× speed. The external original file is
never looked up or opened, and a record without a retained copy simply reports
that. Speed is implemented by resampling, so a faster pace also raises the
pitch. The confidence panel can start playback at a segment's start. Playback
stops when the page or the record changes, on restore and on close.

These usability features have source/headless test evidence on Linux; playback
through a real audio device and Windows/macOS packaged acceptance remain
**NOT RUN**.

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
- `voice-studio-sbom.cdx.json` (CycloneDX 1.6 dependency inventory)

macOS Gatekeeper and Windows SmartScreen warnings are expected because these
artifacts are unsigned. Download only from the project GitHub Release, verify
checksums, and test on non-critical data. No Python is required for the frozen
artifacts.

The reproducible SBOM is generated only from the pinned
`requirements-windows.lock` file and is staged beside the Test RC release
manifest and checksums as `voice-studio-sbom.cdx.json`. The verified artifact
contains 59 normalized components after W2-E1 added `cryptography==50.0.1`
and is byte-identical when generated from lock copies in different temporary
roots. Its scope is the pinned Windows x64
release environment, not necessarily the contents of a frozen runtime. It is
not license evidence, vulnerability evidence, or a publisher signature; the
Test RC remains unsigned, and native Windows/macOS and physical-device gates
remain **NOT RUN** for this increment.

Release-manifest ingestion opens the SBOM through a pinned, no-reparse
filesystem boundary and refuses root, ancestor, pathname or content changes.
Both release builders promote a completed staging directory with the host OS
atomic no-replace primitive and fail if the final destination already exists.

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
navigation or close. Save keeps TXT/MD and SRT/VTT wording consistent by
updating the editable segment layer without creating or moving timestamps;
`raw_text` and the original user file remain unchanged.

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

## Backups and restore

`voice-studio backup create out.voice-backup` writes a plaintext v1 backup by
default: history, settings, the dictionary and, unless `--without-audio`,
managed audio copies. v1 stays the default compatibility format and remains
readable as a plain ZIP.

Encryption is an explicit opt-in: `voice-studio backup create out.voice-backup
--encrypt` (or the GUI "Encrypt with passphrase" checkbox) creates an
encrypted v2 backup. The passphrase is asked interactively (twice on create,
once on verify/restore and on startup recovery after an interrupted restore);
it is never accepted via command-line arguments or environment variables and
is never written to settings, the restore journal, the backup sidecar,
diagnostics or logs. **A lost passphrase cannot be recovered** — without it
the v2 archive is unreadable. `backup verify` and `backup restore` detect v2
archives automatically and prompt once; a wrong passphrase is a hard error
with no plaintext fallback. The GUI follows the same contract with masked
dialogs; cancelling a prompt never deletes the recovery state.

## Project layout

```text
src/voice_studio/          GUI, CLI, local storage, engines, cloud adapters
tests/                     regression and contract tests
scripts/                   quality, model-release and packaging helpers
docs/                      architecture and data contracts
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before publishing or testing.
