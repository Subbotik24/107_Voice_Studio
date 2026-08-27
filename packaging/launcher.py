from __future__ import annotations

import importlib
import importlib.util
import json
import multiprocessing
import os
import tempfile
import traceback
from pathlib import Path

from voice_studio.app import main
from voice_studio.runtime_probe import run_frozen_worker_probe


def runtime_probe(output: Path) -> None:
    runtime_modules = (
        "av",
        "ctranslate2",
        "faster_whisper",
        "pynput",
        "sounddevice",
        "tkinter",
        "keyring",
        "openai",
    )
    development_only_modules = (
        "PIL",
        "psutil",
        "pytest",
    )
    imports: dict[str, str] = {}
    loaded: dict[str, object] = {}
    for name in runtime_modules:
        try:
            loaded[name] = importlib.import_module(name)
            imports[name] = "PASS"
        except Exception as exc:
            imports[name] = (
                f"FAIL: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
    excluded: dict[str, bool] = {}
    for name in development_only_modules:
        try:
            excluded[name] = importlib.util.find_spec(name) is None
        except (ImportError, ModuleNotFoundError):
            excluded[name] = True
    try:
        with tempfile.TemporaryDirectory(prefix="voice-studio-runtime-probe-") as temporary:
            model_value = os.environ.get(
                "VOICE_STUDIO_TRANSCRIPTION_PROBE_MODEL", ""
            ).strip()
            worker_probe: object = run_frozen_worker_probe(
                Path(temporary),
                faster_whisper_model=Path(model_value) if model_value else None,
            )
    except BaseException as exc:
        worker_probe = f"FAIL: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    payload = {
        "status": (
            "PASS"
            if (
                all(value == "PASS" for value in imports.values())
                and all(excluded.values())
                and isinstance(worker_probe, dict)
                and worker_probe.get("status") == "PASS"
            )
            else "FAIL"
        ),
        "runtime_imports": imports,
        "development_only_excluded": excluded,
        "frozen_worker_roundtrip": worker_probe,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if payload["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    probe_output = os.environ.get("VOICE_STUDIO_RUNTIME_PROBE_OUTPUT", "").strip()
    if probe_output:
        runtime_probe(Path(probe_output))
    else:
        main()
