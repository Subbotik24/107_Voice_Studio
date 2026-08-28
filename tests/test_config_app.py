import json

import pytest

from voice_studio.config import (
    cache_dir,
    config_dir,
    data_dir,
    load_settings,
    save_settings,
)
from voice_studio.models import Settings
from voice_studio.profiles import (
    apply_profile,
    discover_ollama_audio_models,
    with_preferred_ollama_model,
)


def test_missing_settings_file_is_created_with_local_ollama_profile(tmp_path):
    path = tmp_path / "settings.json"

    settings = load_settings(path)

    assert path.is_file()
    assert settings.profile == "ollama-local"
    assert settings.engine == "ollama"
    assert settings.cleanup_provider == "ollama"
    assert settings.automatic_cleanup is True
    assert settings.offline_only is True
    assert json.loads(path.read_text(encoding="utf-8")) == settings.to_dict()


@pytest.mark.parametrize(
    ("engine", "expected_profile"),
    [
        ("faster-whisper", "whisper-local"),
        ("openai-cloud", "openai-cloud"),
        ("ollama", "ollama-local"),
    ],
)
def test_settings_without_profile_keep_their_legacy_engine(engine, expected_profile):
    settings = Settings.from_dict({"engine": engine, "model": "small"})

    assert settings.engine == engine
    assert settings.profile == expected_profile
    assert {
        "faster-whisper": ("none", False, True),
        "openai-cloud": ("openai", False, False),
        "ollama": ("ollama", True, True),
    }[engine] == (
        settings.cleanup_provider,
        settings.automatic_cleanup,
        settings.offline_only,
    )


@pytest.mark.parametrize(
    (
        "engine",
        "expected_profile",
        "expected_cleanup",
        "expected_automatic_cleanup",
        "expected_offline_only",
    ),
    [
        ("faster-whisper", "whisper-local", "none", False, True),
        ("openai-cloud", "openai-cloud", "openai", False, False),
    ],
)
def test_full_pre_profile_settings_migrate_without_resetting_saved_choices(
    engine,
    expected_profile,
    expected_cleanup,
    expected_automatic_cleanup,
    expected_offline_only,
):
    legacy_payload = {
        "language": "cs",
        "ui_language": "en",
        "engine": engine,
        "model": "medium",
        "device": "cpu",
        "compute_type": "int8",
        "hotkey": "<f14>",
        "retention": "keep",
        "dictionary_path": "dictionary.json",
        "auto_copy": True,
        "offline_only": False,
        "task_timeout_seconds": 3600,
        "cloud_provider": "openai",
        "cleanup_provider": "ollama",
        "ollama_model": "gemma4:12b",
        "openai_transcription_model": "gpt-transcribe",
        "openai_cleanup_model": "gpt-4.1-mini-2025-04-14",
    }

    settings = Settings.from_dict(legacy_payload)

    assert settings.profile == expected_profile
    assert settings.engine == engine
    assert settings.cleanup_provider == expected_cleanup
    assert settings.automatic_cleanup is expected_automatic_cleanup
    assert settings.offline_only is expected_offline_only
    assert settings.language == "cs"
    assert settings.ui_language == "en"
    assert settings.model == "medium"
    assert settings.device == "cpu"
    assert settings.compute_type == "int8"
    assert settings.hotkey == "<f14>"
    assert settings.dictionary_path == "dictionary.json"
    assert settings.auto_copy is True
    assert settings.task_timeout_seconds == 3600
    assert settings.ollama_model == "gemma4:12b"


@pytest.mark.parametrize(
    "overrides",
    [
        {"cleanup_provider": "openai", "offline_only": False},
        {"engine": "faster-whisper"},
        {"automatic_cleanup": False},
        {"profile": "whisper-local"},
    ],
)
def test_settings_reject_inconsistent_profile_privacy_fields(overrides):
    with pytest.raises(ValueError, match="inconsistent settings for profile"):
        Settings(**overrides).validate()


@pytest.mark.parametrize(
    ("profile", "engine", "cleanup_provider", "automatic_cleanup", "offline_only"),
    [
        ("ollama-local", "ollama", "ollama", True, True),
        ("whisper-local", "faster-whisper", "none", False, True),
        ("openai-cloud", "openai-cloud", "openai", False, False),
    ],
)
def test_applying_profile_sets_the_related_engine_and_privacy_fields(
    profile,
    engine,
    cleanup_provider,
    automatic_cleanup,
    offline_only,
):
    updated = apply_profile(Settings(ollama_model="gemma4:12b"), profile)

    assert updated.profile == profile
    assert updated.engine == engine
    assert updated.cleanup_provider == cleanup_provider
    assert updated.automatic_cleanup is automatic_cleanup
    assert updated.offline_only is offline_only
    assert updated.ollama_model == "gemma4:12b"


def test_new_profile_prefers_a_general_audio_model_over_a_code_variant():
    settings = with_preferred_ollama_model(
        Settings(),
        ["gemma4-code:latest", "gemma4:12b"],
    )

    assert settings.ollama_model == "gemma4:12b"


def test_discovery_never_replaces_a_stored_ollama_model():
    stored = Settings(ollama_model="my-audio-model:latest")

    assert with_preferred_ollama_model(stored, ["gemma4:12b"]) is stored


def test_ollama_discovery_returns_only_models_that_report_audio_capability():
    class Client:
        def list_models(self):
            return {
                "models": [
                    {"name": "text-only:latest"},
                    {"name": "gemma4-code:latest"},
                    {"name": "gemma4:12b"},
                ]
            }

        def show_model(self, model):
            return {
                "capabilities": (
                    ["completion"] if model == "text-only:latest" else ["completion", "audio"]
                )
            }

    assert discover_ollama_audio_models(client=Client()) == [
        "gemma4-code:latest",
        "gemma4:12b",
    ]


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(language="auto", model="medium", auto_copy=False)
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_unknown_engine_is_rejected():
    with pytest.raises(ValueError, match="unsupported engine"):
        Settings(engine="retired-engine").validate()


def test_settings_reject_non_object(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(["bad"]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_settings(path)


def test_legacy_default_hotkey_is_migrated():
    settings = Settings.from_dict({"hotkey": "<ctrl>+<alt>+space"})
    assert settings.hotkey == "<ctrl>+<alt>+<space>"


def test_default_hotkey_is_single_f13_key():
    assert Settings().hotkey == "<f13>"


def test_task_timeout_is_bounded():
    with pytest.raises(ValueError, match="task_timeout_seconds"):
        Settings(task_timeout_seconds=30).validate()


@pytest.mark.parametrize(
    ("payload", "field", "expected"),
    [
        ({"model": 1}, "model", "string"),
        ({"task_timeout_seconds": "bad"}, "task_timeout_seconds", "integer"),
        ({"task_timeout_seconds": True}, "task_timeout_seconds", "integer"),
        ({"auto_copy": "yes"}, "auto_copy", "boolean"),
        ({"offline_only": 1}, "offline_only", "boolean"),
    ],
)
def test_settings_reject_wrong_json_types(payload, field, expected):
    with pytest.raises(ValueError, match=rf"{field}.*{expected}"):
        Settings.from_dict(payload)


def test_clipboard_copy_is_private_by_default():
    assert Settings().auto_copy is False
    assert Settings.from_dict({}).auto_copy is False
    assert Settings.from_dict({"auto_copy": True}).auto_copy is True


def test_settings_ignores_unknown_and_classvar_keys():
    settings = Settings.from_dict(
        {
            "STRING_FIELDS": "not-a-setting",
            "BOOLEAN_FIELDS": {"not": "a-setting"},
            "future_setting": "ignored",
        }
    )
    assert settings == Settings()


def test_settings_from_a_release_that_still_had_the_removed_fields_still_loads():
    """A settings file written before output_dir/insert_to_active_app were removed.

    Both were serialised into every user's settings.json and type-validated on
    load, so an existing file still carries them. Dropping the fields must not
    make that file unloadable, and must not resurrect them on the object.
    """

    settings = Settings.from_dict(
        {
            "language": "cs",
            "output_dir": "/some/old/export/path",
            "insert_to_active_app": True,
        }
    )

    assert settings.language == "cs"
    assert not hasattr(settings, "output_dir")
    assert not hasattr(settings, "insert_to_active_app")
    assert "output_dir" not in settings.to_dict()
    assert "insert_to_active_app" not in settings.to_dict()


def test_application_directories_can_be_isolated_with_environment(monkeypatch, tmp_path):
    config = tmp_path / "config"
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(config))
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(cache))

    assert config_dir() == config
    assert data_dir() == data
    assert cache_dir() == cache
