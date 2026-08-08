from __future__ import annotations

import importlib
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import cache_dir, config_dir, data_dir
from .model_catalog import ModelCatalog
from .models import Settings


def _redact_paths(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _redact_paths(item) for key, item in value.items() if key != "paths"}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    if isinstance(value, str):
        # Diagnostics are intended for public forum reports; no local file paths.
        return value.replace(str(Path.home()), "<home>")
    return value


def export_redacted_diagnostics(report: dict[str, Any], target: Path) -> Path:
    """Write a shareable report with paths, transcript text and credentials absent."""

    payload = _redact_paths(report)
    if not isinstance(payload, dict):  # defensive: diagnostics always returns a dict
        raise ValueError("diagnostics report is invalid")
    payload["redacted"] = True
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def module_available(name: str) -> bool:
    try:
        importlib.import_module(name)
        if name == "tkinter":
            importlib.import_module("_tkinter")
        return True
    except Exception:
        return False


def module_error(name: str) -> str | None:
    try:
        importlib.import_module(name)
        if name == "tkinter":
            importlib.import_module("_tkinter")
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _hotkey_status(combination: str, available: bool) -> tuple[bool, str | None]:
    if not available:
        return False, "pynput is not importable"
    try:
        from pynput import keyboard

        keyboard.HotKey.parse(combination)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def _microphone_status(available: bool) -> tuple[bool, str | None]:
    if not available:
        return False, "sounddevice is not importable"
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        if not any(int(device["max_input_channels"]) > 0 for device in devices):
            return False, "no input audio device was found"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def diagnostics(settings: Settings | None = None) -> dict[str, Any]:
    selected = settings or Settings()
    module_names = (
        "numpy",
        "platformdirs",
        "faster_whisper",
        "sounddevice",
        "pynput",
        "soundfile",
        "torch",
        "tkinter",
    )
    errors = {name: module_error(name) for name in module_names}
    modules = {name: errors[name] is None for name in module_names}
    required = ["numpy", "platformdirs", "tkinter"]
    if selected.engine == "faster-whisper":
        required.append("faster_whisper")
    elif selected.engine == "hermes-whisper":
        required.extend(["torch", "soundfile"])
    missing = [name for name in required if not modules[name]]
    bundle_status: dict[str, Any] = {"configured": False, "valid": None}
    if selected.hermes_bundle:
        bundle_path = Path(selected.hermes_bundle).expanduser()
        bundle_status["configured"] = True
        bundle_status["path"] = str(bundle_path)
        if bundle_path.is_file():
            try:
                from hermes_whisper.bundle import verify_model_bundle

                bundle_status.update(verify_model_bundle(bundle_path))
                bundle_status["valid"] = True
            except Exception as exc:  # Diagnostics must report, not crash.
                bundle_status["valid"] = False
                bundle_status["error"] = str(exc)
        else:
            bundle_status["valid"] = False
            bundle_status["error"] = "file does not exist"
    hotkey_ready, hotkey_error = _hotkey_status(selected.hotkey, modules["pynput"])
    microphone_ready, microphone_error = _microphone_status(modules["sounddevice"])
    ffmpeg = shutil.which("ffmpeg")
    if selected.engine == "hermes-whisper":
        model_ready = bool(bundle_status["configured"] and bundle_status["valid"])
    else:
        model_path = Path(selected.model).expanduser()
        if model_path.is_dir():
            model_ready = True
        else:
            try:
                model_ready = ModelCatalog(data_dir() / "models").get(selected.model) is not None
            except ValueError:
                model_ready = False
    gui_ready = modules["tkinter"]
    runtime_ready = not missing and gui_ready and model_ready is not False
    return {
        "status": "ok" if runtime_ready else "incomplete",
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "selected_engine": selected.engine,
        "selected_model": selected.model,
        "dependencies": modules,
        "dependency_errors": {name: error for name, error in errors.items() if error},
        "missing_required": missing,
        "ffmpeg": ffmpeg,
        "capabilities": {
            "gui_ready": gui_ready,
            "microphone_ready": microphone_ready,
            "hotkey_ready": hotkey_ready,
            "ffmpeg_ready": ffmpeg is not None,
            "model_ready": model_ready,
        },
        "capability_errors": {
            name: error
            for name, error in {
                "microphone": microphone_error,
                "hotkey": hotkey_error,
            }.items()
            if error
        },
        "paths": {
            "config": str(config_dir()),
            "data": str(data_dir()),
            "cache": str(cache_dir()),
        },
        "hermes_bundle": bundle_status,
        "privacy_default": "local/private",
        "cloud_adapters": ["openai"] if module_available("openai") else [],
    }
