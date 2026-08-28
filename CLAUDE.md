# Claude Code handoff — VOICE Studio

This is a privacy-first desktop transcription application. Treat this file and
`AGENTS.md` as the operating contract for further work.

## Start here

1. Read `README.md`, `README.uk.md`, `ARCHITECTURE.md`,
   `IMPLEMENTATION_STATUS.md`, `ROADMAP.md`, and `AGENTS.md`.
2. Inspect `git status --short --branch` before editing. Preserve user changes.
3. Run the baseline:

   ```bash
   python -m compileall -q src tests
   PYTHONPATH=src pytest -q
   ruff check src tests scripts
   python -m build --wheel
   python -m pip check
   ```

## Product and architecture

- `src/voice_studio/`: Tk GUI, CLI, local SQLite storage, exports,
  settings and STT engine adapters.
- `src/voice_studio/` is the standalone product runtime.
- Ollama is the default local audio/cleanup engine; `faster-whisper` is an optional profile.
- `openai-cloud` is explicit opt-in only; it is never a fallback from local.
- `raw_text` is immutable STT output. Users and AI cleanup may change only
  `corrected_text` and segment `corrected_text`.

## Non-negotiable privacy and safety rules

- Never delete a user original. Retention can affect only an imported managed
  copy; add a regression test for any storage/retention change.
- Do not serialize API keys into settings, jobs, backups, diagnostics, logs or
  transcript metadata. Resolve `OPENAI_API_KEY` first, then OS keychain.
- Cloud STT requires an explicit confirmation/CLI `--allow-cloud-upload` before
  any source read or network call. AI cleanup requires `--allow-cloud-text`.
- `offline_only` blocks all cloud activity. Do not add telemetry or cloud upload
  by default.
- Model archives require HTTPS, exact SHA-256, safe ZIP validation and atomic
  install. Do not commit model weights, audio, databases or backups.
- Do not claim recognition accuracy without a closed test set and measured WER/CER.

## Current verified state

The final Windows gate passed locally on 2026-08-28 with locked CPython 3.12.10:
compilation, Ruff, Help validation, 362 tests, dependency checks, wheel/frozen
payload assertions, the PyInstaller runtime probe and packaged GUI startup. The
final unsigned EXE completed a real local `gemma4:12b` synthetic-audio workflow.
See `VERIFICATION.md` for exact artifact hashes and unverified limits.

## Highest-priority next work

1. Run the remaining clean-machine Windows, physical microphone/hotkey and
   macOS Apple Silicon acceptance checklist in `RELEASE_ACCEPTANCE.md`; capture
   evidence without private data.
2. Create Tiny/Small `models-v1` release assets with
   `scripts/build_model_release.py`, including upstream revision, inventory,
   license/model card and SHA256SUMS. Never add those archives to Git.
3. Sign and clean-machine-test a release candidate before tagging or publishing
   it as a production release.

## Windows source launch

Install Python 3.11/3.12 and run `run_windows.bat`. Start Ollama and select an
installed audio-capable model in Settings; no setup popup is shown. The optional
Whisper profile manages its models separately. FFmpeg is recommended for all
supported media formats. See `README.md` for commands.

## Change discipline

Prefer a small change with a regression test over a rewrite. Keep public docs in
English and Ukrainian aligned. Never push, tag, create a GitHub Release, or make
a destructive migration without explicit user authorization.
When user-visible behavior changes, keep in-app Help and docs/help synchronized; use /sync-help when appropriate.
