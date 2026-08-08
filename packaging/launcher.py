from __future__ import annotations

import importlib
import importlib.util
import json
import multiprocessing
import os
import traceback
from pathlib import Path

from hermes_voice_studio.app import main


def runtime_probe(output: Path) -> None:
    runtime_modules = (
        "av",
        "ctranslate2",
        "faster_whisper",
        "pynput",
        "sounddevice",
        "tkinter",
        "torch",
        "hermes_whisper.bundle",
        "hermes_whisper.decoding",
    )
    training_only_modules = (
        "hermes_whisper.trainer",
        "hermes_whisper.smoke",
        "hermes_whisper.data",
        "PIL",
        "psutil",
        "pytest",
        "tensorboard",
        "safetensors",
        "torch.utils.benchmark",
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
    for name in training_only_modules:
        try:
            excluded[name] = importlib.util.find_spec(name) is None
        except (ImportError, ModuleNotFoundError):
            excluded[name] = True
    torch_module = loaded.get("torch")
    tensor_ready = (
        torch_module is not None and torch_module.ones(2).sum().item() == 2
    )
    rpc_disabled = (
        torch_module is not None
        and not torch_module.distributed.rpc.is_available()
    )
    payload = {
        "status": (
            "PASS"
            if (
                all(value == "PASS" for value in imports.values())
                and all(excluded.values())
                and tensor_ready
                and rpc_disabled
            )
            else "FAIL"
        ),
        "runtime_imports": imports,
        "training_only_excluded": excluded,
        "torch_tensor_operation": "PASS" if tensor_ready else "FAIL",
        "torch_distributed_rpc": "disabled-in-inference-profile",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if payload["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    probe_output = os.environ.get("HVS_RUNTIME_PROBE_OUTPUT", "").strip()
    if probe_output:
        runtime_probe(Path(probe_output))
    else:
        main()
