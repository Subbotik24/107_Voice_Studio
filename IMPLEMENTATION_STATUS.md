# Implementation status — VOICE Studio 0.3.0 Test RC

Last reviewed: 2026-08-28.

The repository contains a verified unsigned Windows Test RC. It is a standalone
VOICE Studio product; there are no retired product names or startup AI wizard
in the supported flow.

## Verified product scope

| Area | Status | Evidence |
| --- | --- | --- |
| Ollama-first profiles | PASS | `ollama-local` is the persistent default; Local Whisper and OpenAI cloud are explicit alternatives |
| Saved settings | PASS | profile, engine, recognition/UI languages and exact `gemma4:12b` choice survived save and relaunch |
| Local Ollama audio | PASS | final packaged EXE transcribed a real synthetic English WAV and applied local Ollama cleanup |
| Raw/original safety | PASS | original files are preserved; `raw_text` remains immutable while edits use `corrected_text` |
| Localized UI and Help | PASS | Ukrainian, Czech and English catalogs and canonical Help trees have parity; Ukrainian and Czech were exercised in the packaged UI |
| Reference UI | PASS | cream palette, typography, wide 250 px navigation, compact controls and responsive main window visually inspected on Windows |
| Windows packaging | PASS | reproducible Python 3.12/PyInstaller gate, frozen runtime probe, GUI launch, wheel and exact Help payload assertions |
| Integrated checks | PASS | compile, Ruff, Help validation and 362 tests in the final build gate |

## Known release limits

- The EXE and portable ZIP are unsigned and can trigger SmartScreen.
- Clean-machine acceptance, real microphone/hotkey coverage and a 50-task
  physical-device run were not available.
- Ollama audio-language support depends on the installed model. The installed
  `gemma4:12b` passed the English smoke sample but returned no transcript for a
  synthetic Cyrillic sample; use recognition language `auto` where appropriate
  or select the Local Whisper profile for unsupported languages.
- Ollama audio input is intentionally limited to 30 minutes and returns a
  single untimed segment; Local Whisper remains available for longer/timed work.
- Speech quality has no production WER/CER claim without a licensed closed test
  set.

## Release rule

Call this build an unsigned Test RC, not a signed production release, until the
clean-machine, device-matrix and signing gates are complete.
