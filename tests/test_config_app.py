import json

import pytest

from hermes_voice_studio.config import (
    cache_dir,
    config_dir,
    data_dir,
    load_settings,
    save_settings,
)
from hermes_voice_studio.models import Settings


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(language="auto", model="medium", auto_copy=False)
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_hermes_rejects_english():
    with pytest.raises(ValueError, match="supports only"):
        Settings(engine="hermes-whisper", language="en").validate()


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
    monkeypatch.setenv("HVS_CONFIG_DIR", str(config))
    monkeypatch.setenv("HVS_DATA_DIR", str(data))
    monkeypatch.setenv("HVS_CACHE_DIR", str(cache))

    assert config_dir() == config
    assert data_dir() == data
    assert cache_dir() == cache
