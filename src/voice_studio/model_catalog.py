from __future__ import annotations

import json
import multiprocessing
import queue
import re
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model_release import (
    download_asset,
    fetch_registry,
    find_asset,
    registry_url,
    unpack_verified_archive,
)
from .storage import sha256_file

CATALOG_VERSION = 1
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MODEL_DOWNLOAD_ESTIMATES = {
    "tiny": 80_000_000,
    "tiny.en": 80_000_000,
    "base": 160_000_000,
    "base.en": 160_000_000,
    "small": 520_000_000,
    "small.en": 520_000_000,
    "medium": 1_600_000_000,
    "medium.en": 1_600_000_000,
    "large-v2": 3_200_000_000,
    "large-v3": 3_200_000_000,
    "large-v3-turbo": 1_700_000_000,
}
TRANSIENT_MODEL_DIRECTORIES = {".cache", ".locks"}
TRANSIENT_MODEL_SUFFIXES = {".incomplete", ".lock", ".metadata", ".tmp"}


def _download_worker(
    model_id: str,
    destination: str,
    revision: str | None,
    result_queue: Any,
) -> None:
    try:
        from faster_whisper.utils import download_model

        download_model(
            model_id,
            output_dir=destination,
            local_files_only=False,
            revision=revision,
        )
        result_queue.put({"ok": True})
    except BaseException as exc:
        result_queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class ModelCatalog:
    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.downloads = self.root / ".downloads"
        self.catalog_path = self.root / "catalog.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_model_id(model_id: str) -> str:
        value = model_id.strip()
        if not MODEL_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid model id: {model_id}")
        return value

    def _load(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {"version": CATALOG_VERSION, "models": []}
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read model catalog {self.catalog_path}: {exc}") from exc
        if payload.get("version") != CATALOG_VERSION or not isinstance(payload.get("models"), list):
            raise ValueError("unsupported or invalid model catalog")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.catalog_path)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load()["models"], key=lambda item: item["id"])

    def get(self, model_id: str) -> dict[str, Any] | None:
        value = self._validate_model_id(model_id)
        return next((item for item in self.list() if item["id"] == value), None)

    def resolve(self, model: str) -> Path:
        candidate = Path(model).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        entry = self.get(model)
        if entry is None:
            raise FileNotFoundError(
                f"model '{model}' is not installed; run: voice-studio models install {model}"
            )
        target = self.root / entry["path"]
        if not target.is_dir():
            raise FileNotFoundError(f"installed model directory is missing: {target}")
        return target

    @staticmethod
    def _remove_transient_files(directory: Path) -> None:
        transient_directories = sorted(
            (
                path
                for path in directory.rglob("*")
                if path.is_dir() and path.name in TRANSIENT_MODEL_DIRECTORIES
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in transient_directories:
            shutil.rmtree(path)
        for path in directory.rglob("*"):
            if path.is_file() and (
                path.name == ".DS_Store" or path.suffix in TRANSIENT_MODEL_SUFFIXES
            ):
                path.unlink()

    @staticmethod
    def _inventory(directory: Path) -> tuple[dict[str, str], int]:
        files: dict[str, str] = {}
        size = 0
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            relative = path.relative_to(directory).as_posix()
            files[relative] = sha256_file(path)
            size += path.stat().st_size
        if "model.bin" not in files or "config.json" not in files:
            raise ValueError("faster-whisper model must contain model.bin and config.json")
        return files, size

    def _promote(
        self,
        model_id: str,
        temporary: Path,
        *,
        source: str,
        revision: str | None,
    ) -> dict[str, Any]:
        self._remove_transient_files(temporary)
        files, size = self._inventory(temporary)
        target = self.root / model_id
        if target.exists():
            raise FileExistsError(f"model is already installed: {model_id}")
        temporary.replace(target)
        entry = {
            "id": model_id,
            "path": model_id,
            "source": source,
            "revision": revision,
            "installed_at": datetime.now(UTC).isoformat(),
            "size": size,
            "files": files,
        }
        payload = self._load()
        payload["models"] = [item for item in payload["models"] if item["id"] != model_id]
        payload["models"].append(entry)
        self._save(payload)
        return entry

    def import_local(self, model_id: str, source: Path) -> dict[str, Any]:
        value = self._validate_model_id(model_id)
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"local model directory does not exist: {source}")
        temporary = self.downloads / f"{value}-{uuid.uuid4().hex}"
        try:
            temporary.resolve().relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError("local model directory cannot contain the managed model catalog")
        try:
            shutil.copytree(
                source,
                temporary,
                ignore=shutil.ignore_patterns(
                    ".cache",
                    ".locks",
                    ".DS_Store",
                    "*.incomplete",
                    "*.lock",
                    "*.metadata",
                    "*.tmp",
                ),
            )
            return self._promote(
                value,
                temporary,
                source=str(source.resolve()),
                revision=None,
            )
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def install(
        self,
        model_id: str,
        *,
        revision: str | None = None,
        offline_only: bool = False,
        timeout_seconds: int = 1_800,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        registry: str | None = None,
    ) -> dict[str, Any]:
        value = self._validate_model_id(model_id)
        if offline_only:
            raise RuntimeError("offline-only mode blocks model downloads")
        if self.get(value):
            raise FileExistsError(f"model is already installed: {value}")
        release_registry = registry or registry_url()
        if release_registry:
            return self._install_from_release_registry(
                value,
                release_registry,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                progress=progress,
            )
        expected = MODEL_DOWNLOAD_ESTIMATES.get(value, 3_200_000_000)
        free = shutil.disk_usage(self.root).free
        required = expected + max(512_000_000, expected // 10)
        if free < required:
            raise OSError(f"not enough free space for {value}: need {required} bytes, have {free}")
        temporary = self.downloads / f"{value}-{uuid.uuid4().hex}"
        temporary.mkdir(parents=True)
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_download_worker,
            args=(value, str(temporary), revision, result_queue),
            name=f"model-download-{value}",
        )
        process.start()
        started = time.monotonic()
        try:
            while process.is_alive():
                if cancelled and cancelled():
                    process.terminate()
                    process.join(timeout=5)
                    raise RuntimeError(f"model download cancelled: {value}")
                if time.monotonic() - started > timeout_seconds:
                    process.terminate()
                    process.join(timeout=5)
                    raise TimeoutError(
                        f"model download timed out after {timeout_seconds} seconds: {value}"
                    )
                if progress:
                    downloaded = sum(
                        path.stat().st_size for path in temporary.rglob("*") if path.is_file()
                    )
                    progress(downloaded, expected)
                process.join(timeout=0.25)
            try:
                result = result_queue.get(timeout=2)
            except queue.Empty:
                result = None
            if process.exitcode != 0 or not result or not result["ok"]:
                detail = result["error"] if result else f"worker exit code {process.exitcode}"
                raise RuntimeError(f"cannot install model {value}: {detail}")
            return self._promote(
                value,
                temporary,
                source=f"Systran/faster-whisper-{value}",
                revision=revision,
            )
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if temporary.exists():
                shutil.rmtree(temporary)

    def _install_from_release_registry(
        self,
        model_id: str,
        url: str,
        *,
        timeout_seconds: int,
        cancelled: Callable[[], bool] | None,
        progress: Callable[[int, int], None] | None,
    ) -> dict[str, Any]:
        registry = fetch_registry(url, timeout_seconds=timeout_seconds)
        asset = find_asset(registry, model_id)
        temporary = self.downloads / f"{model_id}-{uuid.uuid4().hex}"
        temporary.mkdir(parents=True)
        try:
            archive = download_asset(
                asset,
                temporary,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                progress=progress,
            )
            unpacked = temporary / "unpacked"
            unpacked.mkdir()
            unpack_verified_archive(archive, unpacked, expected_size=int(asset["unpacked_bytes"]))
            archive.unlink()
            return self._promote(
                model_id,
                unpacked,
                source="GitHub Release models-v1",
                revision=str(asset["revision"]),
            )
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def verify(self, model_id: str) -> dict[str, Any]:
        entry = self.get(model_id)
        if entry is None:
            raise FileNotFoundError(f"model is not installed: {model_id}")
        directory = self.root / entry["path"]
        files, size = self._inventory(directory)
        if files != entry["files"] or size != entry["size"]:
            raise ValueError(f"model integrity check failed: {model_id}")
        return {"status": "PASS", **entry}

    def remove(self, model_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        value = self._validate_model_id(model_id)
        if not confirmed:
            raise ValueError("model removal requires --yes")
        entry = self.get(value)
        if entry is None:
            raise FileNotFoundError(f"model is not installed: {value}")
        target = self.root / entry["path"]
        try:
            target.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError("refusing to remove a model outside the managed catalog") from exc
        if target.is_dir():
            shutil.rmtree(target)
        payload = self._load()
        payload["models"] = [item for item in payload["models"] if item["id"] != value]
        self._save(payload)
        return {"removed": True, "id": value}
