from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

RESOURCE_LAYOUT = {
    "dictionary.example.json": Path("config/dictionary.json"),
}


def initialize_workspace(destination: Path, *, overwrite: bool = False) -> dict[str, Any]:
    root = destination.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    package_root = resources.files("voice_studio.resources")
    for resource_name, relative_target in RESOURCE_LAYOUT.items():
        target = root / relative_target
        if target.exists() and not overwrite:
            skipped.append(str(relative_target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = package_root.joinpath(resource_name).read_bytes()
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
        created.append(str(relative_target))
    return {
        "workspace": str(root.resolve()),
        "created": created,
        "skipped": skipped,
    }
