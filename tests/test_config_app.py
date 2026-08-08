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
