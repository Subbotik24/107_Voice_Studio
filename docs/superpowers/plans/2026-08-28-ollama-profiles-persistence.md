# Ollama Profiles and Persistent Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make direct local Ollama audio transcription the durable default and expose Ollama, Whisper, and OpenAI as reusable Settings profiles without silent fallback.

**Architecture:** Extend `Settings` with an explicit profile and Ollama engine, keep the existing engine registry boundary, and add an `OllamaAudioEngine` that sends an in-memory WAV to Ollama's loopback OpenAI-compatible endpoint. Apply validated cleanup after the raw transcript is saved, preserving `raw_text` on every success and failure path.

**Tech Stack:** Python 3.11/3.12, dataclasses, Tk/ttk, PyAV, urllib, multiprocessing spawn, pytest, Ruff, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-28-voice-local-settings-help-localization-design.md`

## Global Constraints

- Ollama is the default local model and direct speech engine.
- No startup setup popup, telemetry, model download, cloud fallback, or user-original deletion.
- `raw_text` and raw segment text are immutable after recognition.
- New engines implement `SpeechEngine` and return `EngineResult`.
- Existing settings without `profile` retain their selected engine through legacy classification.
- Retired product identifiers must not appear in code, UI, Help, metadata, or release artifacts.

---

### Task 1: Persistent settings and profile contract

**Files:**
- Modify: `src/voice_studio/models.py`
- Modify: `src/voice_studio/config.py`
- Create: `src/voice_studio/profiles.py`
- Test: `tests/test_config_app.py`
- Test: `tests/test_product_identity_app.py`

**Interfaces:**
- Produces: `SUPPORTED_PROFILES`, `Settings.profile`, `apply_profile(settings, profile) -> Settings`, `classify_legacy_profile(payload) -> str`, and `load_settings(..., create=True)`.
- Consumes: existing `save_settings(Settings, Path | None) -> Path` atomic replacement.

- [ ] **Step 1: Write failing tests for new-profile defaults and legacy classification**

```python
def test_missing_settings_file_is_created_with_local_ollama_profile(tmp_path):
    path = tmp_path / "settings.json"
    settings = load_settings(path)
    assert path.is_file()
    assert settings.profile == "ollama-local"
    assert settings.engine == "ollama"
    assert settings.cleanup_provider == "ollama"
    assert settings.offline_only is True

def test_legacy_whisper_settings_keep_the_whisper_profile():
    settings = Settings.from_dict({"engine": "faster-whisper", "model": "small"})
    assert settings.profile == "whisper-local"
    assert settings.engine == "faster-whisper"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `python -m pytest tests/test_config_app.py tests/test_product_identity_app.py -q`  
Expected: FAIL because `profile` and `ollama` engine/default creation do not exist.

- [ ] **Step 3: Add the profile mapping and durable defaults**

```python
SUPPORTED_PROFILES = ("ollama-local", "whisper-local", "openai-cloud")
SUPPORTED_ENGINES = ("ollama", "faster-whisper", "openai-cloud")

@dataclass
class Settings:
    profile: str = "ollama-local"
    engine: str = "ollama"
    offline_only: bool = True
```

In `Settings.from_dict`, detect whether the input contained `profile`; if not,
derive it from its existing `engine`. In `load_settings`, create a missing file
with `save_settings(defaults, target)` before returning it.

- [ ] **Step 4: Implement `apply_profile` as one pure replacement operation**

```python
def apply_profile(settings: Settings, profile: str) -> Settings:
    mapping = {
        "ollama-local": dict(engine="ollama", cleanup_provider="ollama", automatic_cleanup=True, offline_only=True),
        "whisper-local": dict(engine="faster-whisper", cleanup_provider="none", automatic_cleanup=False, offline_only=True),
        "openai-cloud": dict(engine="openai-cloud", cleanup_provider="openai", automatic_cleanup=False, offline_only=False),
    }
    return replace(settings, profile=profile, **mapping[profile])
```

Add `none` to supported cleanup providers, persist `automatic_cleanup`, and
validate that `profile`, `engine`,
cleanup provider, and offline state are consistent.

- [ ] **Step 5: Run targeted tests and commit the coherent contract**

Run: `python -m pytest tests/test_config_app.py tests/test_product_identity_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(settings): add persistent engine profiles"`

### Task 2: Loopback Ollama audio client and engine

**Files:**
- Modify: `src/voice_studio/ollama_local.py`
- Create: `src/voice_studio/engines/ollama_audio.py`
- Modify: `src/voice_studio/engines/__init__.py`
- Modify: `src/voice_studio/engines/registry.py`
- Test: `tests/test_ollama_audio_engine_app.py`

**Interfaces:**
- Consumes: `OllamaClient._request`, `SpeechEngine`, `EngineResult`, `Segment`, `Settings.ollama_model`.
- Produces: `OllamaClient.show_model(model)`, `OllamaClient.audio_chat(model, wav_bytes, prompt)`, `OllamaAudioEngine.transcribe(source, language)`.

- [ ] **Step 1: Write failing request-contract and EngineResult tests**

```python
def test_audio_chat_uses_openai_compatible_input_audio(monkeypatch):
    client = OllamaClient()
    captured = {}
    monkeypatch.setattr(client, "_request", lambda path, **kwargs: captured.update(path=path, **kwargs) or RESPONSE)
    assert client.audio_chat("gemma4:12b", b"RIFF....", "Transcribe") == "hello"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["payload"]["messages"][0]["content"][1]["type"] == "input_audio"

def test_ollama_engine_returns_one_duration_segment(tmp_path):
    engine = OllamaAudioEngine("gemma4:12b", client=FakeOllama(), converter=FakeWav())
    result = engine.transcribe(tmp_path / "sample.m4a", "en")
    assert result.engine == "ollama"
    assert result.text == "This is a test."
    assert (result.segments[0].start, result.segments[0].end) == (0.0, 2.5)
```

- [ ] **Step 2: Run the new test and confirm RED**

Run: `python -m pytest tests/test_ollama_audio_engine_app.py -q`  
Expected: collection/import FAIL because `OllamaAudioEngine` does not exist.

- [ ] **Step 3: Add bounded client methods and strict response parsing**

```python
def show_model(self, model: str) -> dict[str, Any]:
    return self._request("/api/show", payload={"model": model}, timeout=10.0)

def audio_chat(self, model: str, wav_bytes: bytes, prompt: str) -> str:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8192,
        "think": False,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"), "format": "wav"
            }},
        ]}],
    }
```

Reject empty model names, missing `audio` capability, oversized encoded audio,
non-object choices, and empty `message.content`. Never use `message.reasoning`.

- [ ] **Step 4: Implement in-memory PyAV conversion and the engine**

Convert the first audio stream to PCM s16le, 16 kHz, mono in a `BytesIO`; return
`(wav_bytes, duration_seconds)`. Build a language-specific transcription prompt
and return one `Segment(0.0, duration, text, language=language)` with metadata
`provider=ollama`, `loopback_only=True`, and `timed_segments=False`.

- [ ] **Step 5: Register and cache the engine by Ollama model**

Extend `EngineKey` with `ollama_model` and instantiate
`OllamaAudioEngine(settings.ollama_model)` for `settings.engine == "ollama"`.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m pytest tests/test_ollama_audio_engine_app.py tests/test_cloud_contracts.py tests/test_service_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(engine): transcribe audio with local Ollama"`

### Task 3: Automatic local cleanup without raw-text mutation

**Files:**
- Modify: `src/voice_studio/jobs.py`
- Modify: `src/voice_studio/storage.py`
- Modify: `src/voice_studio/models.py`
- Test: `tests/test_jobs_app.py`
- Test: `tests/test_cloud_contracts.py`

**Interfaces:**
- Consumes: `propose_cleanup`, `LocalStore.apply_ai_cleanup`, `Settings.cleanup_provider`.
- Produces: job progress phase `cleaning`; transcript metadata keys `automatic_cleanup`, `cleanup_warning`.

- [ ] **Step 1: Add failing success and failure-path tests**

```python
def test_ollama_profile_applies_cleanup_but_preserves_raw_text(...):
    transcript = controller.run(source, Settings(ollama_model="gemma4:12b"), dictionary)
    assert transcript.raw_text == "raw engine text"
    assert transcript.corrected_text == "cleaned text"
    assert transcript.segments[0].text == "raw engine text"
    assert transcript.metadata["automatic_cleanup"] == "applied"

def test_cleanup_failure_keeps_the_saved_transcript(...):
    transcript = controller.run(source, settings, dictionary)
    assert transcript.raw_text == "raw engine text"
    assert transcript.metadata["automatic_cleanup"] == "failed"
    assert "cleanup_warning" in transcript.metadata
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tests/test_jobs_app.py -q`  
Expected: FAIL because transcription returns immediately after `service.finalize`.

- [ ] **Step 3: Save raw transcript first, then use a second worker request for cleanup**

Return the engine result to the parent as today and call `finalize` so the raw
transcript is durable. When `settings.automatic_cleanup` is true, report
`cleaning` and enqueue a second request containing the serialized transcript,
provider, and model. The same spawned worker calls `propose_cleanup` and returns
only the validated proposal payload. The parent applies it through
`LocalStore.apply_ai_cleanup`. Catch cleanup-specific errors, persist a bounded
warning in transcript metadata, and return the saved transcript instead of
raising a transcription failure.

- [ ] **Step 4: Verify cancellation and no-fallback behavior**

Add a test that cancels during `cleaning` and confirms the worker is terminated,
plus a test that an Ollama error never asks `EngineManager` for Whisper/OpenAI.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_jobs_app.py tests/test_cloud_contracts.py tests/test_storage_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(workflow): apply safe automatic local cleanup"`

### Task 4: Mini-profile Settings UI and quiet startup

**Files:**
- Modify: `src/voice_studio/app.py`
- Modify: `src/voice_studio/i18n.py`
- Test: `tests/test_gui_contract_app.py`
- Test: `tests/test_i18n_app.py`

**Interfaces:**
- Consumes: `apply_profile`, `list_ollama_models`, saved `Settings.profile`.
- Produces: three localized profile cards and asynchronous Ollama discovery events.

- [ ] **Step 1: Add failing behavioral/static GUI contracts**

```python
def test_startup_does_not_schedule_first_run_model_prompt():
    source = inspect.getsource(VoiceStudioApp.__init__)
    assert "_first_run_model_prompt" not in source

def test_settings_declares_all_engine_profile_cards():
    source = inspect.getsource(VoiceStudioApp._settings_dialog)
    assert '"ollama-local"' in source
    assert '"whisper-local"' in source
    assert '"openai-cloud"' in source
```

- [ ] **Step 2: Run the GUI/i18n tests and confirm RED**

Run: `python -m pytest tests/test_gui_contract_app.py tests/test_i18n_app.py -q`  
Expected: FAIL because startup still schedules the prompt and cards do not exist.

- [ ] **Step 3: Remove startup onboarding and build profile cards**

Delete the `after(400, self._first_run_model_prompt)` scheduling and the unused
method. Add a Profiles tab/card row using existing cream theme styles. Selecting
a card calls `apply_profile` into form variables and visually updates the active
card. Keep detailed engine fields below/inside an Engine Details tab.

- [ ] **Step 4: Move Ollama discovery off the Tk thread**

Start a daemon thread only when Settings opens, post `ollama_models` through the
existing event queue/`after`, refresh the combobox on Tk, and persist the
preferred model only during a valid Save or clean-profile initialization.

- [ ] **Step 5: Add Ukrainian/Czech/English copy and verify save/cancel**

Add exact labels/descriptions/errors for all three profile cards. Test the pure
profile-variable application and saved `Settings` result without requiring a
display; retain the static widget tripwire only for declaration.

- [ ] **Step 6: Run targeted tests and commit**

Run: `python -m pytest tests/test_gui_contract_app.py tests/test_i18n_app.py tests/test_editor_state_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "feat(ui): add reusable engine profile cards"`

### Task 5: Product text, CLI, readiness, and user documentation

**Files:**
- Modify: `src/voice_studio/app.py`
- Modify: `src/voice_studio/diagnostics.py`
- Modify: `src/voice_studio/cli.py`
- Modify: `README.md`
- Modify: `README.uk.md`
- Modify: `ARCHITECTURE.md`
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `ROADMAP.md`
- Modify: `VERIFICATION.md`
- Test: `tests/test_cli_app.py`
- Test: `tests/test_diagnostics_app.py`
- Test: `tests/test_product_identity_app.py`

**Interfaces:**
- Consumes: profile/settings and engine metadata from Tasks 1-4.
- Produces: accurate readiness text and documented limitations.

- [ ] **Step 1: Add failing checks for default CLI/settings identity**

Assert clean settings JSON reports `profile=ollama-local`, `engine=ollama`, and
no retired product identifier. Assert diagnostics checks Ollama only for the active
Ollama profile and managed Whisper only for the Whisper profile.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest tests/test_cli_app.py tests/test_diagnostics_app.py tests/test_product_identity_app.py -q`  
Expected: FAIL on old default/readiness assumptions.

- [ ] **Step 3: Update product surfaces and documentation**

Document the three profiles, local loopback endpoint, audio-capability check,
single-segment subtitle limitation, no fallback, and build/run prerequisites.
Remove claims that Faster Whisper is always the product default.

- [ ] **Step 4: Run targeted checks and commit**

Run: `python -m pytest tests/test_cli_app.py tests/test_diagnostics_app.py tests/test_product_identity_app.py -q`  
Expected: PASS.  
Commit: `git commit -m "docs: document Ollama-first engine profiles"`

### Task 6: Full verification and Windows executable

**Files:**
- Modify if required by a failing check: `packaging/voice_studio.spec`
- Modify if required by a failing check: `scripts/build_windows.ps1`
- Modify: `VERIFICATION.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified frozen application and exact artifact path.

- [ ] **Step 1: Run static and full test gates**

Run:

```powershell
python -m compileall -q src tests
python -m ruff check src tests scripts
$env:PYTHONPATH='src'; python scripts/check_help.py; python -m pytest -q
```

Expected: every command exits 0.

- [ ] **Step 2: Build and inspect the wheel**

Run: `python -m build --wheel` and inspect the archive for all Python modules
and Help content. Expected: exit 0 and no absolute user paths/model binaries.

- [ ] **Step 3: Build the Windows executable reproducibly**

Run: `powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1`  
Expected: exit 0 and a new `VOICE Studio.exe` under `dist/`.

- [ ] **Step 4: Smoke-test the frozen app with an isolated profile**

Verify first launch has no popup, settings JSON is created, Ollama profile and
selected model survive second launch, deterministic WAV transcribes through
Ollama, raw text is preserved, all three profiles can be selected, and the app
exits cleanly twice.

- [ ] **Step 5: Record evidence and commit**

Update only factual results in `VERIFICATION.md`.  
Commit: `git commit -m "release: verify Ollama-first Windows workflow"`
