"""W3-V1 configurable VAD regression tests.

The Local Whisper user can disable the VAD filter when it clips quiet speech.
The persisted default stays enabled, the choice reaches exactly
``WhisperModel.transcribe(vad_filter=...)``, and the Ollama/OpenAI profiles
never receive the Whisper-only flag.
"""

import sys
from types import SimpleNamespace

import pytest

from voice_studio.engines import registry
from voice_studio.engines.faster_whisper import FasterWhisperEngine
from voice_studio.models import Settings


def _fake_runtime(monkeypatch, captured: dict):
    def transcribe(_source, **kwargs):
        captured.update(kwargs)
        part = SimpleNamespace(start=0.0, end=1.0, text=" hello ", avg_logprob=None)
        info = SimpleNamespace(language="en", duration=1.0, language_probability=1.0)
        return [part], info

    model = SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: 0,
            get_supported_compute_types=lambda _device=None: ("int8", "float32"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model),
    )


def test_settings_vad_filter_defaults_to_enabled():
    assert Settings().vad_filter is True


def test_legacy_settings_without_vad_filter_load_as_enabled():
    settings = Settings.from_dict({"engine": "faster-whisper", "model": "small"})

    assert settings.vad_filter is True


def test_settings_reject_a_non_boolean_vad_filter():
    with pytest.raises(ValueError, match="settings.vad_filter must be a boolean"):
        Settings.from_dict({"vad_filter": "no"})


def test_saved_and_reloaded_settings_preserve_the_disabled_choice():
    saved = Settings(vad_filter=False)
    restored = Settings.from_dict(saved.to_dict())

    assert restored.vad_filter is False
    assert restored.to_dict()["vad_filter"] is False


def test_engine_rejects_a_non_boolean_vad_filter_before_runtime_import():
    sys.modules.pop("faster_whisper", None)

    with pytest.raises(ValueError, match="vad_filter"):
        FasterWhisperEngine("tiny", vad_filter="no")

    assert "faster_whisper" not in sys.modules


def test_transcribe_uses_vad_filter_by_default(monkeypatch, tmp_path):
    captured: dict = {}
    _fake_runtime(monkeypatch, captured)
    source = tmp_path / "sample.wav"
    source.write_bytes(b"RIFF")

    FasterWhisperEngine("tiny").transcribe(source, "auto")

    assert captured["vad_filter"] is True


def test_transcribe_passes_the_disabled_vad_choice(monkeypatch, tmp_path):
    captured: dict = {}
    _fake_runtime(monkeypatch, captured)
    source = tmp_path / "sample.wav"
    source.write_bytes(b"RIFF")

    engine = FasterWhisperEngine("tiny", vad_filter=False)
    result = engine.transcribe(source, "auto")

    assert captured["vad_filter"] is False
    assert result.metadata["vad_filter"] is False


def test_engine_manager_passes_vad_only_to_faster_whisper(monkeypatch, tmp_path):
    created: list[dict] = []

    class FakeWhisper:
        def __init__(self, model, **kwargs):
            created.append(kwargs)

    class FakeOllama:
        def __init__(self, model):
            created.append({"ollama_model": model})

    class FakeCloud:
        def __init__(self, model):
            created.append({"cloud_model": model})

    monkeypatch.setattr(registry, "FasterWhisperEngine", FakeWhisper)
    monkeypatch.setattr(registry, "OllamaAudioEngine", FakeOllama)
    monkeypatch.setattr(registry, "OpenAICloudEngine", FakeCloud)
    manager = registry.EngineManager(tmp_path / "cache", tmp_path / "models")
    monkeypatch.setattr(
        manager.model_catalog, "resolve", lambda model: tmp_path / "models" / model
    )

    manager.get(
        Settings(
            profile="whisper-local",
            engine="faster-whisper",
            cleanup_provider="none",
            automatic_cleanup=False,
            vad_filter=False,
        )
    )
    manager.get(Settings(ollama_model="gemma4:12b", vad_filter=False))
    manager.get(
        Settings(
            profile="openai-cloud",
            engine="openai-cloud",
            cleanup_provider="openai",
            automatic_cleanup=False,
            offline_only=False,
            vad_filter=False,
        )
    )

    assert created[0].get("vad_filter") is False
    assert "vad_filter" not in created[1]
    assert "vad_filter" not in created[2]


def test_engine_manager_caches_per_vad_choice(monkeypatch, tmp_path):
    created: list[dict] = []

    class FakeWhisper:
        def __init__(self, model, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(registry, "FasterWhisperEngine", FakeWhisper)
    manager = registry.EngineManager(tmp_path / "cache", tmp_path / "models")
    monkeypatch.setattr(
        manager.model_catalog, "resolve", lambda model: tmp_path / "models" / model
    )
    base = dict(
        profile="whisper-local",
        engine="faster-whisper",
        cleanup_provider="none",
        automatic_cleanup=False,
    )

    enabled = manager.get(Settings(**base))
    disabled = manager.get(Settings(**base, vad_filter=False))

    assert enabled is not disabled
    assert manager.get(Settings(**base)) is enabled
    assert created[0]["vad_filter"] is True
    assert created[1]["vad_filter"] is False


def test_cli_transcribe_no_vad_overrides_the_saved_default():
    from voice_studio.cli import _load_effective_settings, build_parser

    args = build_parser().parse_args(["transcribe", "sample.wav", "--no-vad"])

    import voice_studio.cli as cli_module

    original = cli_module.load_settings
    cli_module.load_settings = lambda: Settings()
    try:
        settings = _load_effective_settings(args)
    finally:
        cli_module.load_settings = original

    assert settings.vad_filter is False


def test_cli_transcribe_without_vad_flags_keeps_the_saved_choice():
    from voice_studio.cli import _load_effective_settings, build_parser

    args = build_parser().parse_args(["transcribe", "sample.wav"])

    import voice_studio.cli as cli_module

    original = cli_module.load_settings
    cli_module.load_settings = lambda: Settings(vad_filter=False)
    try:
        settings = _load_effective_settings(args)
    finally:
        cli_module.load_settings = original

    assert settings.vad_filter is False


def test_settings_dialog_exposes_a_vad_control_and_saves_it():
    import inspect

    from voice_studio.app import VoiceStudioApp

    source = inspect.getsource(VoiceStudioApp._settings_dialog)

    assert '"vad_filter"' in source
    assert 'self._t("vad_filter")' in source


def test_vad_label_exists_in_every_interface_catalog():
    from voice_studio.i18n import _CATALOGS

    assert set(_CATALOGS) == {"uk", "cs", "en"}
    for language, catalog in _CATALOGS.items():
        assert "vad_filter" in catalog, language
        assert catalog["vad_filter"].strip()
