from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

from .models import Settings

APP_NAME = "HermesVoiceStudio"
APP_AUTHOR = "Hermes Voice Project"


def _directory_override(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    return Path(value).expanduser() if value else None


def config_dir() -> Path:
    return _directory_override("HVS_CONFIG_DIR") or Path(
        user_config_dir(APP_NAME, APP_AUTHOR)
    )


def data_dir() -> Path:
    return _directory_override("HVS_DATA_DIR") or Path(user_data_dir(APP_NAME, APP_AUTHOR))


def cache_dir() -> Path:
    return _directory_override("HVS_CACHE_DIR") or Path(
        user_cache_dir(APP_NAME, APP_AUTHOR)
    )


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    target = path or settings_path()
    if not target.exists():
        return Settings()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read settings {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("settings file must contain a JSON object")
    return Settings.from_dict(payload)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    settings.validate()
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
