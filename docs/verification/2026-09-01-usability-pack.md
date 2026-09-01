# VOICE Studio usability pack — execution log

This is the durable controller log for the user-approved usability package.
Read it before running a gate so an unchanged production HEAD is not verified
twice. Focused worker checks do not replace the controller's full gate.

## Baseline — 2026-09-01

- Base: `8239204b160918c5206ea1d4abff489105a4aed6`, synchronized `main`/`origin/main`.
- `python -m compileall -q src tests`: PASS.
- `pytest -q`: PASS, 880 passed / 9 skipped; all skips are Windows symlink-privilege boundaries (`WinError 1314`).
- `ruff check src tests scripts packaging`: PASS.
- wheel build: PASS, ignored artifact under `build/usability-baseline-wheel/`.
- `pip check`: PASS, no broken requirements.
- Packaged/native usability acceptance: NOT RUN.

## Increment records

Each increment appends base/commit, RED evidence, focused GREEN, controller
full gate, diff/secret checks, commit/push, and any explicit `NOT_RUN` items.

### Increment 1 — navigation and central pages

- Base: `8239204`; production commit: `f710aef` (`feat: add central workspace navigation`).
- RED: 4 failed / 21 passed — missing page dispatcher, missing History → Studio navigation, and missing localized Dashboard key.
- Initial focused GREEN: 135 passed.
- Review fix round 1: populated-transcript Discard regression RED 1 failed / 3 passed; GREEN 94 passed.
- Review fix round 2: no-current draft Discard regression RED 1 failed / 4 passed; GREEN 95 passed.
- Sol scoped re-review: Discard and Cancel findings ADDRESSED; no new Critical/Important findings.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 886 / 9 Windows symlink-privilege skips; CLI version PASS `0.3.0rc1`.
- `pip check`, `git diff --check`, changed-file whitespace and secret/private-path scan: PASS.
- Physical GUI and packaged/native acceptance: NOT RUN.

### Increment 2 — managed terminology dictionary

- Base: `e3f1704`; production changes were reviewed in one working-tree increment before controller commit.
- Task 2A RED/GREEN: missing repository/rule APIs initially failed collection; final focused dictionary proof: 27 passed, affected subset 49 passed / 73 deselected.
- Task 2A Sol review: four correction rounds closed bounded-read, CSV large-field/concurrency, atomicity, merge-accounting, shared-parser, and boundary-proof findings; final verdict CLEAN.
- Task 2B RED/GREEN: initial controller surface 9 failed; lifecycle review fixes recorded 7 failed / 12 passed plus 1 live-i18n failure; export dispatch fix recorded 2 failed; final focused set 80 passed and export subset 25 passed.
- Task 2B Sol review: Settings reconciliation, dirty close, live localization, load-error boundary, and explicit JSON/CSV export selection corrected; final verdict CLEAN.
- First controller full pytest exposed three legacy headless-close stub regressions: 936 passed / 3 failed / 9 skipped. A narrow regression fix then passed 7 relevant tests / 22 deselected.
- Final controller gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 940 / 9 Windows symlink-privilege skips.
- `pip check`, `git diff --check`, and changed-diff secret/private-path scan: PASS.
- Live Tk visual, physical GUI, and packaged/native acceptance: NOT RUN.

### Increment 3 — Local Whisper recognition hints

- Base: `00fb4b4`; implementation kept hints per-request and did not change transcript, Settings, backup, or model-cache schemas.
- RED: missing `TranscriptionHints`/worker serialization produced 2 failures; privacy review regressions produced 2 failures for dictionary-path boundary and echoed hint values; provider omission hardening was test-only.
- Focused GREEN: 14 hint-contract tests; related engine/service/job/cloud selection 106 passed.
- Sol review: worker error redaction, worker rejection of `dictionary_path`, sanitized cleanup settings, exact provider omission, constructor reuse, invalid payload bounds, Architecture signature, and benchmark fake were corrected; final verdict CLEAN.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 954 / 9 Windows symlink-privilege skips.
- `pip check`, `git diff --check`, and changed-diff secret/private-path scan: PASS.
- Live engine/model smoke, physical GUI, and packaged/native acceptance: NOT RUN.

### Increment 4 — Dashboard statistics and combined History filters

- Base: `1782fa7` plus handoff docs `79a3f4d`; slices 4A and 4B verified and committed together under one controller gate.
- Environment: Linux cloud container, CPython 3.12.3 virtualenv with full project dependencies; the platform boundary skips appear as 14 Linux junction skips instead of the 9 Windows `WinError 1314` skips recorded on the previous machine.
- 4A storage: new `voice_studio/dashboard.py` with immutable `DashboardStatistics` and `HistoryFilter` (pure `matches()`); streaming `LocalStore.statistics()` reads every row without the 250-row UI limit and without migration; `LocalStore.list(..., filters=...)` combines text/date/language/engine/model/status/retained-audio filters before `limit` while the legacy path stays byte-for-byte; invalid payload rows count only in total+invalid and never raise. Focused proof: 23 new tests; dashboard+storage subset 92 passed / 5 skipped, covering empty store, 261 rows beyond the UI limit, legacy 0.1 payloads, invalid rows, exact UTC 7/30-day boundaries, Unicode apostrophe/hyphen word tokens, failed records, weighted RTF (0.875 case) with bool/NaN/zero exclusions, filter combinations, and post-filter limit ordering.
- 4B interface: Dashboard placeholder replaced with a pure Tk/ttk KPI grid (totals, completed, failed, words, H:MM:SS audio duration, ×speed, retained audio, 7/30-day activity), top-3 language/engine/model rankings, an invalid-records row visible only above zero that points at `voice-studio storage audit`, and five clickable recent records honoring the dirty-editor guard; History page gained controls for every `HistoryFilter` field with translated display→raw value mapping and safe rejection of malformed dates; Dashboard refreshes on page open and after save/rename/delete/restore/import with no background polling; uk/cs/en catalogs extended (30 keys added, 2 placeholder keys removed, key parity enforced). 21 new headless lifecycle tests; a Linux Xvfb source-level smoke of the real GUI passed (this is not the packaged or physical acceptance).
- Controller full gate on the combined tree: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 993 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS.
- `git diff --check` and changed-diff secret/private-path scan: PASS.
- Help/docs synchronization for the new Dashboard and History behavior: deferred to Task 8 of the approved master plan.
- Physical Tk smoke and Windows/macOS packaged/native acceptance: NOT RUN.

### Increment 5 — Studio editor tools

- Base: `2e2a13e`; slices 5A and 5B verified and committed together under one controller gate in the same Linux/CPython 3.12.3 environment as Increment 4.
- 5A pure module: new `voice_studio/editor_tools.py` with `find_matches` (literal Unicode search via re.escape + IGNORECASE so offsets always index the original string), `apply_replacements` (slice-built, backreference-proof, validated spans), `segment_spans`/`segment_index_for_offset` (sequential alignment mirroring `sync_segments`), `DEFAULT_FILLERS` (uk: ем, е-е, мм; cs: ehm, hm; en: um, uh, erm, hmm — «ну», "like", "you know" deliberately absent), `find_filler_matches` (whole-word, longest-first alternation, trailing comma in span) and `remove_matches` with local whitespace tidying. No undo here: the bounded manual-edit undo in `storage.update_editor_state` covers editor saves. 33 focused tests.
- 5B interface: editor toolbar gained Find/Replace (collapsible panel, match count, highlight tag, replace-one from cursor with wrap, replace-all via reverse targeted delete/insert so formatting tags survive), «Додати до словника» from the selection (blocked on read-only or unsaved dictionary, persists through the managed `_save_dictionary` path with rollback on failure, applies the single rule to the editor text only and never writes storage — pinned by a test) and filler cleanup as a preview modal with one checkbox per match and ±30-char context; language resolves transcript-first with `auto` falling back to settings. 25 new i18n keys in uk/cs/en; help `reference.md` (uk/cs/en) documents the three tools. 29 new headless lifecycle tests.
- A Linux Xvfb source-level smoke of the real GUI exercised all three tools on a seeded transcript, including formatting-tag survival, filler modal Cancel/partial Apply, and uk/cs/en retranslation (not the packaged or physical acceptance).
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1055 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS.
- `git diff --check` and changed-diff secret/private-path scan: PASS.
- Physical Tk smoke and Windows/macOS packaged/native acceptance: NOT RUN.

### Increment 6 — confidence review panel

- Base: `db98ca5`; one increment, one controller gate, same Linux/CPython 3.12.3 environment.
- Pure logic: `confidence_entries(segments, threshold)` in `editor_tools.py` — threshold validated 0.00–1.00 (bool rejected), scored entries strictly below the threshold sorted lowest-first with index tie-break, segments without a usable score (None, NaN, inf, bool, wrong type) listed after the scored ones in index order and never treated as 0.0. 14 focused tests including exact 0.00/1.00 boundaries.
- Interface: a collapsible Studio panel behind an «Оцінка впевненості» toolbar button — threshold Spinbox as page state only (never written to Settings or disk, pinned by a test), count label, lowest-first segment list with 48-char snippets and a "no score" label, row selection highlights and focuses the corrected segment via `segment_spans` with a status message when the segment text is no longer locatable, and a Play-segment control that is a hook for the later local-playback task (today it reports playback as not yet available). `_show_result` refreshes the panel only while visible; no polling. 11 i18n keys in uk/cs/en; wording presents the value as the engine's own confidence score — no error-probability or accuracy claims in UI or help. Help `reference.md` (uk/cs/en) gained a "Confidence review" subsection. 20 headless GUI tests; two existing headless fixtures gained one initialization line each.
- Linux Xvfb source smoke on a seeded 4-segment transcript (0.92/0.42/None/0.15): ordering, focus highlight, play placeholder, threshold refilter, invalid-threshold rejection, cs retranslation, no settings write (not the packaged or physical acceptance).
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1089 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS.
- `git diff --check` and changed-diff secret/private-path scan: PASS.
- Physical Tk smoke and Windows/macOS packaged/native acceptance: NOT RUN.

### Increment 7 — local audio playback

- Base: `8482c2b`; slices 7A (backend) and 7B (Studio wiring) verified and committed together under one controller gate on Linux/CPython 3.12.3. The 7A implementation ran under Opus, 7B was implemented directly by the primary controller after the Opus session limit interrupted the second slice.
- 7A backend: new `voice_studio/playback.py` on the existing PyAV + sounddevice dependencies only. Injected decode/device seams (`PcmSource`/`PcmSink`); `AvPcmSource` decodes to s16 mono/stereo and implements speed purely by resampling to `rate/speed` while reporting the source rate (pitch shifts by design, no pitch-preserving DSP), seeks via the stream time_base with sub-frame lead trimming, and yields bounded ~100 ms PCM chunks; `SounddeviceSink` opens lazily and never raises out of abort/close; `AudioPlayer` runs exactly one daemon worker with dual lifecycle/state locks mirroring the recorder, supports play/pause/resume/stop/seek±/absolute seek/speed set with generation-guarded state, aborts the device to unblock a blocked write and joins within the 2 s budget, and captures worker errors into `last_error` without ever crashing. 20 fake-seam tests including the blocked-write abort; the real PyAV adapter was additionally exercised out-of-tree against generated media (durations, speeds, mid-frame seek, error branches) — real-device output remains unexercised (no audio device in the container).
- 7B interface: a playback bar under the Studio editor (play/pause toggle, stop, ±5 s, 0.75–2× readonly speed selector, m:ss position label with a self-terminating 250 ms progress ticker that stops at idle and surfaces `last_error` once). Playback resolves ONLY the retained managed copy (`audio_retained` + `source_path` contained in the managed sources directory + real file) — an external original is never looked up, pinned by tests; the confidence-panel Play hook now starts playback at the segment start. Lifecycle stops: leaving the Studio page, switching to a different transcript, `_reload_after_restore`, and `_close` (a worker that misses the 2 s join budget is recorded as a `playback-worker` residue). 9 new i18n keys uk/cs/en, the obsolete `editor_confidence_play_unavailable` key removed; help `reference.md` (uk/cs/en) gained a "Local playback" subsection and the confidence subsection now describes real playback. 25 headless GUI lifecycle tests plus adjusted confidence-hook tests.
- Linux Xvfb source smoke with a real generated WAV imported as a managed source and a fake device sink: safe-path resolution, real decode through the bar, position label, segment-start playback, page-switch stop and sink release all confirmed (not the packaged or physical acceptance; no real audio device).
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1135 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS.
- `git diff --check` and changed-diff secret/private-path scan: PASS.
- Physical playback on a real audio device, physical Tk smoke, and Windows/macOS packaged/native acceptance: NOT RUN.

### Increment 8 — documentation alignment and final review

- Base: `bceb2b2`; docs-plus-hardening increment closing the source/headless part of the usability pack.
- Documentation aligned with increments 1–7: README.md / README.uk.md gained the workspace (Dashboard/Studio/Dictionary/History) and Studio editor-tools sections; ARCHITECTURE.md lists the new `dashboard`, `editor_tools` and `playback` modules; SECURITY.md records the editor-tools write boundary, local-only statistics, per-request hint terms and the managed-copy-only playback policy plus the real-audio-device NOT-RUN; IMPLEMENTATION_STATUS.md gained seven Usability rows with the exact per-increment gate counts; ROADMAP.md marks the usability pack implemented source/headless and names the remaining native/packaged work. Help `reference.md` (uk/cs/en) left-menu table now lists all central pages. Every claim is scoped to the Linux source/headless gate; no packaged, native, physical-device or accuracy claim was added.
- Final security & privacy review of the cumulative `1782fa7..HEAD` diff (adversarial, code-verified): no HIGH or MEDIUM findings. Verified clean: playback containment is resolve-then-check so a symlink inside managed sources cannot escape it; parameterized SQL only; invalid payloads surface as a count with no content leakage; user input is always re.escape-d before compilation with no catastrophic-backtracking pattern; no storage writes outside the explicit save path; the confidence threshold never persists; no network, telemetry or cloud path added; hint terms never reach the OpenAI request; single generation-guarded playback worker with bounded buffering; i18n `str.format` does not evaluate substituted values; new tests write only under tmp_path.
- Review fixes applied in this increment: playback error messages now carry the file basename instead of the absolute managed path (LOW); the editor quick-add dictionary rule no longer silently becomes a recognition hint — `use_as_hint` is False until the Dictionary page enables it (LOW); a symlink-escape regression test for `_playable_source_path` and a tmp_path cleanup were added to the playback GUI tests (LOW/INFO). Accepted with rationale: dashboard statistics recomputes synchronously on refresh triggers including UI-language change (bounded memory, local-scale DB; caching deferred), and the check-to-open TOCTOU on the managed copy matches the recorder's accepted same-account residual.
- Supply chain: `scripts/generate_sbom.py` produced byte-identical CycloneDX output on two consecutive runs against `requirements-windows.lock` (deterministic); `pip-audit` over the Linux gate environment reported findings only in the environment's own `pip` 24.0 tooling, which is not a product dependency and is not pinned in the Windows lock — product dependencies clean.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1136 passed / 14 Linux junction skips; wheel build PASS (`voice_studio-0.3.0rc1`); `pip check` PASS; `git diff --check` PASS.
- Windows 10/11 x64 packaged build and acceptance, macOS acceptance, physical Tk smoke, real-audio-device playback, signed artifacts: NOT RUN — these require the user's machines per RELEASE_ACCEPTANCE.md and cannot be exercised from this environment.

### Increment 9 — release preparation groundwork (docs only)

- Base: `c118c9e`. An attempt to build the Tiny/Small `models-v1` assets from this environment was stopped by fact: the egress proxy denies huggingface.co (CONNECT 403), and `scripts/build_model_release.py` deliberately performs no downloads, so the upstream snapshots cannot be obtained here. No archive was built and nothing model-related was committed.
- `RELEASE_ACCEPTANCE.md` gained a usability-pack smoke entry (Dashboard statistics and refresh, History filters past the 250 window, Dictionary + Local Whisper hotwords run, editor find/replace / add-to-dictionary / filler preview, confidence focus and segment playback, playback bar through a real audio device with the external original untouched).
- New `docs/release/MODELS_V1_RUNBOOK.md`: the manual on-machine procedure the build script expects — pin upstream Systran revisions, download the pinned snapshots over HTTPS with the model card included, build archives/`SHA256SUMS.txt`/`model-registry-v1.json` with `scripts/build_model_release.py`, publish to the `models-v1` release and re-verify checksums after upload; archives never enter Git.
- Checks for this docs-only increment: compileall PASS, Ruff PASS, `git diff --check` PASS. The full pytest/wheel gate was last run on the unchanged code at `c118c9e` (1136 passed / 14 skips) and was not repeated for a documentation-only diff.

### Increment 10 — settings profile UX and Ollama discovery fallback

- Base: `1b9dd34`; user-reported defect pair fixed under one controller gate.
- Root cause of "Ollama не працює": `discover_ollama_audio_models` kept only models whose `/api/show` capabilities include `audio`, which mainstream Ollama installs rarely advertise — the model list stayed empty and the engine was unusable from the UI. New `discover_ollama_model_catalog` returns the full installed list plus the audio subset (a failing `show_model` keeps a model in the full list); the settings combobox now falls back to all installed models with a dedicated `ollama_no_audio_models` warning when the audio subset is empty, while automatic model selection still draws from the audio subset only, so a non-audio model is never stored silently. The unreachable-Ollama error remains visible in the status line.
- Settings dialog restructured per the report: the «Локальний AI» tab is removed; the engine-specific settings now sit DIRECTLY UNDER the three profile cards and switch with the selected profile — Ollama (model combobox, refresh, local-cleanup caption, status), Whisper (model, device, compute type, VAD, hardware detect), OpenAI (STT and cleanup models, key management, consent note). Variables, validation and the save path are unchanged; a dedicated status var keeps the audio warning from being overwritten by the active-profile message.
- i18n: `ollama_no_audio_models` added, `local_ai_settings` removed (uk/cs/en). Help synced in reference, quick-start, workflows and troubleshooting for all three languages.
- Tests: 4 pure catalog/auto-selection tests and 5 headless GUI contract tests (exactly-one-page switching, fallback list + warning, audio list + found status, unreachable error). Xvfb smoke captured all three profile panels in both a no-audio and an audio-capable fake-Ollama run.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1145 passed / 14 Linux junction skips; wheel build PASS; `pip check` PASS; `git diff --check` PASS.
- Physical verification against a real local Ollama runtime: NOT RUN here (no Ollama in the container) — the discovery path is exercised through fake clients and the loopback error branch.

### Increment 11 — in-window Settings and Help, proxy-proof Ollama client

- Base: `5c560ae`; two user-reported defects fixed under one controller gate.
- "Ollama відповідає в чаті, а застосунок її не бачить": the loopback client used plain `urlopen`, which routes even 127.0.0.1 through HTTP(S)_PROXY environment variables (VPN/corporate proxy) while `ollama run` connects directly. `OllamaClient` now uses a dedicated opener with an empty ProxyHandler, so system proxies can never break the loopback runtime. Proven failing-first (a poisoned proxy env breaks plain urlopen and not the direct opener) and pinned by a regression test with a real local HTTP server plus poisoned proxy variables; the HTTP-error-detail contract test was retargeted at the new seam.
- "Все в одній програмі, в одній структурі": Settings and Help are no longer Toplevel windows — both are central pages of the main window alongside Dashboard/Studio/Dictionary/History (six pages total, sidebar active-state included, F1 opens the Help page). The Settings page rebuilds from the stored settings on every entry, keeps the profile-cards-plus-engine-block layout, stops the global hotkey while open and restarts it on leave, moves the hotkey-capture binding onto the main window, and gains an unsaved-changes guard (save / discard / stay) plus a busy-state refusal. Save keeps the page open with a fresh baseline; Скасувати returns to the previous page. The Help page builds lazily once, keeps rendered images alive, resets on interface-language change and shows load failures in-page. Models and Backup windows deliberately unchanged. 1 new i18n key (`settings_unsaved`); help reference.md (uk/cs/en) documents the in-window pages and the guard.
- Tests: 11 new settings/help page tests, navigation fixtures extended to six pages, help/gui-contract/vad tests retargeted; the proxy regression test as above.
- Xvfb smoke on the real app: sidebar entry, zero Toplevels, per-profile engine switching in-page, guard prompts (stay/discard), F1 help with four topics, language switch to English rebuilding Help, hotkey restart after leaving — captured in seven screenshots.
- Controller full gate: compileall PASS; Ruff PASS; Help validation PASS (13 Markdown files); pytest PASS 1158 passed / 14 Linux junction skips; wheel build PASS; `pip check` PASS; `git diff --check` PASS.
- Verification against a real local Ollama runtime and a real proxied Windows environment: NOT RUN here — covered by the fake-server regression test and the loopback error branch.

### Increment 12 — launchers always run the checked-out source

- Base: `45ed5eb`. User report: `run_windows.bat` kept starting the old version after `git pull`. Root cause: both launchers installed the package into `.venv` once ("first run only") and then always started the installed copy (`voice-studio gui` console script); when that first install was not editable — or pointed at another checkout — every later pull changed the sources but not what the launcher ran.
- Fix: `run_windows.bat` and `run_mac.command` now export `PYTHONPATH=<project>/src` and start `python -m voice_studio gui`, so the launcher always executes exactly the source tree sitting in the folder; the first-run dependency install is unchanged, and the launcher prints the resolved `voice_studio.__file__` so a wrong-folder launch is visible immediately. Verified in this environment that with the path set the import resolves to the checkout's `src` ahead of any installed copy; 2 new launcher contract tests pin the mechanism on both scripts.
- Checks: Ruff PASS; pytest PASS 1160 passed / 14 Linux junction skips; `git diff --check` PASS (script-and-test-only diff on top of the `45ed5eb` full gate).
- Real Windows/macOS double-click verification: NOT RUN here — the launchers are Windows/macOS scripts; the contract tests and the path-resolution proof cover the mechanism.
