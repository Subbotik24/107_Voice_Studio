"""Verified model-pack installation from a GitHub Release registry."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024


def registry_url() -> str | None:
    return os.environ.get("VOICE_STUDIO_MODEL_REGISTRY_URL", "").strip() or None


def fetch_registry(url: str, *, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "VOICE-Studio"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("models"), list)
    ):
        raise ValueError("model registry has an unsupported format")
    return payload


def find_asset(registry: dict[str, Any], model_id: str) -> dict[str, Any]:
    item = next(
        (candidate for candidate in registry["models"] if candidate.get("id") == model_id), None
    )
    required = {"id", "url", "sha256", "archive_bytes", "unpacked_bytes", "revision"}
    if not isinstance(item, dict) or not required.issubset(item):
        raise ValueError(f"model registry has no valid asset for {model_id}")
    if not isinstance(item["url"], str) or not str(item["url"]).startswith("https://"):
        raise ValueError("model registry asset URL must use HTTPS")
    if not isinstance(item["sha256"], str) or len(item["sha256"]) != 64:
        raise ValueError("model registry asset has invalid SHA-256")
    try:
        int(item["sha256"], 16)
    except (TypeError, ValueError) as exc:
        raise ValueError("model registry asset has invalid SHA-256") from exc
    try:
        archive_bytes = int(item["archive_bytes"])
        unpacked_bytes = int(item["unpacked_bytes"])
    except (TypeError, ValueError) as exc:
        raise ValueError("model registry asset has invalid numeric metadata") from exc
    if not 0 < archive_bytes <= MAX_ARCHIVE_BYTES or not 0 < unpacked_bytes <= MAX_UNPACKED_BYTES:
        raise ValueError("model registry asset exceeds the configured resource ceiling")
    return item


def download_asset(
    asset: dict[str, Any],
    destination: Path,
    *,
    timeout_seconds: int,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    expected_size = int(asset["archive_bytes"])
    if expected_size <= 0 or expected_size > MAX_ARCHIVE_BYTES:
        raise ValueError("model archive exceeds the configured resource ceiling")
    if shutil.disk_usage(destination.parent).free < expected_size + int(asset["unpacked_bytes"]):
        raise OSError("not enough free disk space for model archive and unpacked model")
    archive = destination / "model.zip"
    digest = hashlib.sha256()
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "VOICE-Studio"})
    with (
        urllib.request.urlopen(request, timeout=timeout_seconds) as response,
        archive.open("wb") as output,
    ):  # noqa: S310
        downloaded = 0
        while block := response.read(1024 * 1024):
            if cancelled and cancelled():
                raise RuntimeError("model download cancelled")
            downloaded += len(block)
            if downloaded > expected_size or downloaded > MAX_ARCHIVE_BYTES:
                raise ValueError("model archive exceeds declared size")
            digest.update(block)
            output.write(block)
            if progress:
                progress(downloaded, expected_size)
    if downloaded != expected_size or digest.hexdigest().lower() != str(asset["sha256"]).lower():
        raise ValueError("model archive integrity check failed")
    return archive


def unpack_verified_archive(archive: Path, destination: Path, *, expected_size: int) -> None:
    if expected_size <= 0 or expected_size > MAX_UNPACKED_BYTES:
        raise ValueError("model unpacked size exceeds the configured resource ceiling")
    seen: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        total = 0
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            path = Path(name)
            if not name or path.is_absolute() or ".." in path.parts or name in seen:
                raise ValueError("model archive contains an unsafe or duplicate member")
            seen.add(name)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("model archive may not contain symlinks")
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind and kind not in {stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("model archive may contain only regular files and directories")
            total += member.file_size
            if total > expected_size or total > MAX_UNPACKED_BYTES:
                raise ValueError("model archive exceeds declared unpacked size")
        if total != expected_size:
            raise ValueError("model archive unpacked size does not match the registry")
        bundle.extractall(destination)
