from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import queue
import re
import shutil
import stat
import time
import unicodedata
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
from .process_lifecycle import _dispose_queue, _stop_process
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
CATALOG_LOCK_TIMEOUT_SECONDS = 10.0
CATALOG_RESIDUE_MAX_AGE_SECONDS = 300
STAGING_MAX_AGE_SECONDS = 172_800
STAGING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*-[0-9a-f]{32}$")
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _reconcile_result() -> dict[str, Any]:
    return {
        "status": "PASS",
        "action": "none",
        "adopted": [],
        "dropped": [],
        "blocked": [],
        "staging_removed": [],
        "staging_kept": [],
        "residue_removed": [],
        "catalog_quarantined": None,
    }


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
        if (
            not isinstance(payload, dict)
            or payload.get("version") != CATALOG_VERSION
            or not isinstance(payload.get("models"), list)
        ):
            raise ValueError("unsupported or invalid model catalog")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        temporary = self.root / f"catalog.json.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.catalog_path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _exclusive_lock(self, timeout_seconds: float = CATALOG_LOCK_TIMEOUT_SECONDS):
        """Hold the cross-process catalog lock for one load-modify-save cycle.

        ``catalog.json`` writes are atomic on their own, but reconcile, promote
        and remove all read the catalog before writing it back; without mutual
        exclusion a concurrent process's committed entry is silently lost. The
        OS releases the lock with the process, and the lock file itself is
        never deleted, so a crash cannot leave the catalog locked.
        """

        lock_path = self.root / ".catalog.lock"
        handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(handle, 0, os.SEEK_SET)
                        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "model catalog is in use by another VOICE Studio "
                            f"process; try again later ({lock_path})"
                        ) from None
                    time.sleep(0.05)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(handle, 0, os.SEEK_SET)
                    msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(handle)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load()["models"], key=lambda item: item["id"])

    @staticmethod
    def _is_reparse_point(
        path: Path, path_stat: os.stat_result | None = None
    ) -> bool:
        if path_stat is None:
            path_stat = path.lstat()
        return stat.S_ISLNK(path_stat.st_mode) or bool(
            getattr(path_stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        )

    @staticmethod
    def _newest_mtime(directory: Path) -> float | None:
        """Return the newest non-following mtime in a staging tree."""
        try:
            root_stat = directory.lstat()
        except OSError:
            return None
        if not stat.S_ISDIR(root_stat.st_mode) or ModelCatalog._is_reparse_point(
            directory, root_stat
        ):
            return None
        newest = root_stat.st_mtime
        pending = [directory]
        try:
            while pending:
                current = pending.pop()
                with os.scandir(current) as iterator:
                    entries = list(iterator)
                for entry in entries:
                    entry_stat = entry.stat(follow_symlinks=False)
                    newest = max(newest, entry_stat.st_mtime)
                    if ModelCatalog._is_reparse_point(Path(entry.path), entry_stat):
                        return None
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append(Path(entry.path))
        except OSError:
            return None
        return newest

    def _cleanup_residue(
        self, root_entries: list[os.DirEntry[str]], result: dict[str, Any]
    ) -> None:
        cutoff = time.time() - CATALOG_RESIDUE_MAX_AGE_SECONDS
        for entry in root_entries:
            if entry.name != "catalog.json.tmp" and not (
                entry.name.startswith("catalog.json.") and entry.name.endswith(".tmp")
            ):
                continue
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(entry_stat.st_mode) or entry_stat.st_mtime >= cutoff:
                continue
            try:
                Path(entry.path).unlink()
            except OSError:
                continue
            result["residue_removed"].append(entry.name)

    def _cleanup_staging(self, result: dict[str, Any]) -> None:
        try:
            downloads_stat = self.downloads.lstat()
        except OSError:
            return
        if not stat.S_ISDIR(downloads_stat.st_mode) or self._is_reparse_point(
            self.downloads, downloads_stat
        ):
            return
        try:
            with os.scandir(self.downloads) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            return

        cutoff = time.time() - STAGING_MAX_AGE_SECONDS
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                result["staging_kept"].append(entry.name)
                continue
            if (
                self._is_reparse_point(path, entry_stat)
                or not stat.S_ISDIR(entry_stat.st_mode)
                or not STAGING_PATTERN.fullmatch(entry.name)
            ):
                result["staging_kept"].append(entry.name)
                continue
            newest = self._newest_mtime(path)
            if newest is None or newest >= cutoff:
                result["staging_kept"].append(entry.name)
                continue
            try:
                # Re-check the directory entry without following links before removal.
                if not stat.S_ISDIR(path.lstat().st_mode) or self._is_reparse_point(path):
                    result["staging_kept"].append(entry.name)
                    continue
                shutil.rmtree(path)
            except OSError:
                result["staging_kept"].append(entry.name)
            else:
                result["staging_removed"].append(entry.name)

    @classmethod
    def inspect(cls, root: Path) -> dict[str, Any]:
        """Inspect managed model state without constructing or modifying a catalog."""
        root = Path(root).expanduser()
        result: dict[str, Any] = {
            "status": "PASS",
            "manifest": "absent",
            "missing": [],
            "orphans": [],
            "blocked": [],
            "staging": [],
            "residue": [],
        }

        def blocked(model_id: str, path: Path, reason: str) -> None:
            result["blocked"].append(
                {"id": model_id, "path": str(path), "reason": reason}
            )

        try:
            root_stat = root.lstat()
        except FileNotFoundError:
            return result
        except OSError as exc:
            result["status"] = "FAIL"
            blocked("<root>", root, f"model root could not be inspected safely: {exc}")
            return result

        if cls._is_reparse_point(root, root_stat):
            reason = "model root is a symlink" if stat.S_ISLNK(root_stat.st_mode) else (
                "model root is a reparse point"
            )
            blocked("<root>", root, reason)
            result["status"] = "ATTENTION"
            return result
        if not stat.S_ISDIR(root_stat.st_mode):
            blocked("<root>", root, "model root is not a real directory")
            result["status"] = "ATTENTION"
            return result

        catalog_path = root / "catalog.json"
        payload: dict[str, Any] | None = None
        try:
            catalog_stat = catalog_path.lstat()
        except FileNotFoundError:
            catalog_stat = None
        except OSError:
            result["manifest"] = "invalid"
            result["status"] = "FAIL"
        if catalog_stat is not None:
            if cls._is_reparse_point(catalog_path, catalog_stat) or not stat.S_ISREG(
                catalog_stat.st_mode
            ):
                result["manifest"] = "invalid"
                result["status"] = "FAIL"
            else:
                try:
                    payload_candidate = json.loads(catalog_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    result["manifest"] = "invalid"
                    result["status"] = "FAIL"
                else:
                    if (
                        not isinstance(payload_candidate, dict)
                        or payload_candidate.get("version") != CATALOG_VERSION
                        or not isinstance(payload_candidate.get("models"), list)
                    ):
                        result["manifest"] = "invalid"
                        result["status"] = "FAIL"
                    else:
                        result["manifest"] = "valid"
                        payload = payload_candidate

        try:
            with os.scandir(root) as iterator:
                root_entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            result["status"] = "FAIL"
            blocked("<root>", root, f"model root could not be scanned safely: {exc}")
            return result

        catalogued_ids: set[str] = set()
        catalogued_root_names: set[str] = set()
        if payload is not None:
            for item in payload["models"]:
                if not isinstance(item, dict):
                    blocked("<invalid>", catalog_path, "invalid manifest entry")
                    continue
                model_id = item.get("id")
                relative_path = item.get("path")
                if not isinstance(model_id, str) or not MODEL_ID_PATTERN.fullmatch(model_id):
                    blocked(
                        str(model_id),
                        catalog_path,
                        "manifest entry has an invalid model id",
                    )
                    continue
                catalogued_ids.add(model_id)
                if not isinstance(relative_path, str) or any(
                    unicodedata.category(character) in {"Cc", "Cs"}
                    for character in relative_path
                ):
                    blocked(
                        model_id,
                        catalog_path,
                        "manifest entry has an invalid model path",
                    )
                    continue
                try:
                    relative = Path(relative_path)
                except (TypeError, ValueError):
                    blocked(
                        model_id,
                        catalog_path,
                        "manifest entry has an invalid model path",
                    )
                    continue
                if (
                    not relative_path
                    or relative.is_absolute()
                    or bool(relative.anchor)
                    or any(part in {"", ".", ".."} for part in relative.parts)
                ):
                    blocked(
                        model_id,
                        catalog_path,
                        "manifest entry has an invalid model path",
                    )
                    continue
                catalogued_root_names.add(relative.parts[0])
                target = root.joinpath(*relative.parts)
                parent = root
                unsafe_parent = False
                for component in relative.parts[:-1]:
                    parent = parent / component
                    try:
                        parent_stat = parent.lstat()
                    except FileNotFoundError:
                        break
                    except (OSError, UnicodeError, ValueError):
                        blocked(
                            model_id,
                            parent,
                            "model path could not be inspected safely",
                        )
                        unsafe_parent = True
                        break
                    if cls._is_reparse_point(parent, parent_stat):
                        blocked(
                            model_id,
                            parent,
                            "model path is a symlink"
                            if stat.S_ISLNK(parent_stat.st_mode)
                            else "model path is a reparse point",
                        )
                        unsafe_parent = True
                        break
                    if not stat.S_ISDIR(parent_stat.st_mode):
                        blocked(model_id, parent, "model path is not a real directory")
                        unsafe_parent = True
                        break
                if unsafe_parent:
                    continue
                try:
                    target_stat = target.lstat()
                except FileNotFoundError:
                    result["missing"].append(
                        {
                            "id": model_id,
                            "path": str(target),
                            "reason": "catalogued model is absent",
                        }
                    )
                    continue
                except (OSError, ValueError):
                    blocked(
                        model_id,
                        target,
                        "model path could not be inspected safely",
                    )
                    continue
                if cls._is_reparse_point(target, target_stat):
                    blocked(
                        model_id,
                        target,
                        "model path is a symlink"
                        if stat.S_ISLNK(target_stat.st_mode)
                        else "model path is a reparse point",
                    )
                    continue
                if not stat.S_ISDIR(target_stat.st_mode):
                    blocked(model_id, target, "model path is not a real directory")
                    continue
                if not cls._has_required_model_files(target):
                    blocked(
                        model_id,
                        target,
                        "model directory is incomplete; model.bin and config.json are required",
                    )

        for entry in root_entries:
            if entry.name in {"catalog.json", ".downloads"}:
                continue
            if entry.name.startswith("catalog.json") and entry.name.endswith(".tmp"):
                continue
            if not MODEL_ID_PATTERN.fullmatch(entry.name):
                continue
            path = root / entry.name
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                blocked(entry.name, path, "model path could not be inspected safely")
                continue
            if cls._is_reparse_point(path, entry_stat):
                if entry.name in catalogued_root_names:
                    continue
                blocked(
                    entry.name,
                    path,
                    "model path is a symlink"
                    if stat.S_ISLNK(entry_stat.st_mode)
                    else "model path is a reparse point",
                )
                continue
            if (
                not stat.S_ISDIR(entry_stat.st_mode)
                or entry.name in catalogued_ids
                or entry.name in catalogued_root_names
            ):
                continue
            result["orphans"].append(
                {
                    "id": entry.name,
                    "path": str(path),
                    "complete": cls._has_required_model_files(path),
                }
            )

        downloads = root / ".downloads"
        try:
            downloads_stat = downloads.lstat()
        except FileNotFoundError:
            downloads_stat = None
        except OSError:
            downloads_stat = None
            blocked("<downloads>", downloads, "staging directory could not be inspected safely")
        if downloads_stat is not None:
            if cls._is_reparse_point(downloads, downloads_stat):
                blocked(
                    "<downloads>",
                    downloads,
                    "staging directory is a symlink"
                    if stat.S_ISLNK(downloads_stat.st_mode)
                    else "staging directory is a reparse point",
                )
            elif stat.S_ISDIR(downloads_stat.st_mode):
                try:
                    with os.scandir(downloads) as iterator:
                        staging_entries = sorted(iterator, key=lambda entry: entry.name)
                except OSError:
                    staging_entries = []
                    blocked(
                        "<downloads>",
                        downloads,
                        "staging directory could not be scanned safely",
                    )
                cutoff = time.time() - STAGING_MAX_AGE_SECONDS
                for entry in staging_entries:
                    path = downloads / entry.name
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        result["staging"].append(
                            {
                                "name": entry.name,
                                "pattern_valid": bool(STAGING_PATTERN.fullmatch(entry.name)),
                                "stale": False,
                                "safe": False,
                            }
                        )
                        continue
                    pattern_valid = bool(STAGING_PATTERN.fullmatch(entry.name))
                    safe = not cls._is_reparse_point(path, entry_stat) and (
                        stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISREG(entry_stat.st_mode)
                    )
                    newest = cls._newest_mtime(path) if stat.S_ISDIR(entry_stat.st_mode) else (
                        entry_stat.st_mtime if safe else None
                    )
                    result["staging"].append(
                        {
                            "name": entry.name,
                            "pattern_valid": pattern_valid,
                            "stale": bool(safe and newest is not None and newest < cutoff),
                            "safe": safe
                            and (
                                newest is not None
                                if stat.S_ISDIR(entry_stat.st_mode)
                                else True
                            ),
                        }
                    )

        cutoff = time.time() - CATALOG_RESIDUE_MAX_AGE_SECONDS
        for entry in root_entries:
            if entry.name != "catalog.json.tmp" and not (
                entry.name.startswith("catalog.json.") and entry.name.endswith(".tmp")
            ):
                continue
            path = root / entry.name
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                safe = False
                stale = False
            else:
                safe = stat.S_ISREG(entry_stat.st_mode) and not cls._is_reparse_point(
                    path, entry_stat
                )
                stale = bool(safe and entry_stat.st_mtime < cutoff)
            result["residue"].append(
                {"name": entry.name, "stale": stale, "safe": safe}
            )

        for key in ("missing", "orphans", "blocked", "staging", "residue"):
            result[key].sort(key=lambda item: tuple(str(item[field]) for field in sorted(item)))
        if result["manifest"] == "invalid":
            result["status"] = "FAIL"
        elif any(result[key] for key in ("missing", "orphans", "blocked", "staging", "residue")):
            result["status"] = "ATTENTION"
        return result

    @staticmethod
    def _has_required_model_files(directory: Path) -> bool:
        for name in ("model.bin", "config.json"):
            try:
                path_stat = (directory / name).lstat()
            except OSError:
                return False
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or ModelCatalog._is_reparse_point(directory / name, path_stat)
            ):
                return False
        return True

    def reconcile(self) -> dict[str, Any]:
        with self._exclusive_lock():
            return self._reconcile_locked()

    def _reconcile_locked(self) -> dict[str, Any]:
        result = _reconcile_result()
        quarantined = False
        try:
            payload = self._load()
        except ValueError:
            quarantine = self.catalog_path.with_name(
                f"catalog.json.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
                f"-{uuid.uuid4().hex[:8]}"
            )
            try:
                self.catalog_path.replace(quarantine)
            except OSError as quarantine_exc:
                result.update(
                    status="FAIL",
                    action="attention",
                    error=f"cannot quarantine model catalog: {quarantine_exc}",
                )
                return result
            payload = {"version": CATALOG_VERSION, "models": []}
            result["catalog_quarantined"] = str(quarantine)
            quarantined = True

        try:
            with os.scandir(self.root) as iterator:
                root_entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            result.update(
                status="FAIL",
                action="attention",
                error=f"cannot scan model catalog: {exc}",
            )
            return result

        self._cleanup_residue(root_entries, result)
        self._cleanup_staging(result)

        models = payload["models"]
        catalogued_ids: set[str] = set()
        manifest_changed = quarantined
        retained_models: list[Any] = []
        for item in models:
            if not isinstance(item, dict):
                result["blocked"].append(
                    {
                        "id": "<invalid>",
                        "path": str(self.catalog_path),
                        "reason": "invalid manifest entry",
                    }
                )
                retained_models.append(item)
                continue
            model_id = item.get("id")
            relative_path = item.get("path")
            if not isinstance(model_id, str) or not MODEL_ID_PATTERN.fullmatch(model_id):
                result["blocked"].append(
                    {
                        "id": str(model_id),
                        "path": str(self.catalog_path),
                        "reason": "manifest entry has an invalid model id",
                    }
                )
                retained_models.append(item)
                continue
            catalogued_ids.add(model_id)
            if not isinstance(relative_path, str) or not relative_path:
                result["blocked"].append(
                    {
                        "id": model_id,
                        "path": str(self.root / str(relative_path)),
                        "reason": "manifest entry has an invalid model path",
                    }
                )
                retained_models.append(item)
                continue
            target = self.root / relative_path
            try:
                resolved_target = target.resolve()
                resolved_root = self.root.resolve()
            except OSError:
                result["blocked"].append(
                    {
                        "id": model_id,
                        "path": str(target),
                        "reason": "model path could not be inspected safely",
                    }
                )
                retained_models.append(item)
                continue
            try:
                resolved_target.relative_to(resolved_root)
            except ValueError:
                result["blocked"].append(
                    {
                        "id": model_id,
                        "path": str(target),
                        "reason": "manifest model path is outside the managed catalog",
                    }
                )
                retained_models.append(item)
                continue
            try:
                target_stat = target.lstat()
            except FileNotFoundError:
                result["dropped"].append(model_id)
                manifest_changed = True
            except OSError:
                result["blocked"].append(
                    {
                        "id": model_id,
                        "path": str(target),
                        "reason": "model path could not be inspected safely",
                    }
                )
                retained_models.append(item)
            else:
                if self._is_reparse_point(target, target_stat):
                    result["blocked"].append(
                        {
                            "id": model_id,
                            "path": str(target),
                            "reason": (
                                "model path is a symlink"
                                if stat.S_ISLNK(target_stat.st_mode)
                                else "model path is a reparse point"
                            ),
                        }
                    )
                elif not stat.S_ISDIR(target_stat.st_mode):
                    result["blocked"].append(
                        {
                            "id": model_id,
                            "path": str(target),
                            "reason": "model path is not a real directory",
                        }
                    )
                retained_models.append(item)

        payload["models"] = retained_models
        models = payload["models"]
        for entry in root_entries:
            if entry.name in {self.downloads.name, self.catalog_path.name}:
                continue
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                if MODEL_ID_PATTERN.fullmatch(entry.name) and entry.name not in catalogued_ids:
                    result["blocked"].append(
                        {
                            "id": entry.name,
                            "path": str(self.root / entry.name),
                            "reason": "model path could not be inspected safely",
                        }
                    )
                continue
            if self._is_reparse_point(self.root / entry.name, entry_stat):
                if MODEL_ID_PATTERN.fullmatch(entry.name) and entry.name not in catalogued_ids:
                    result["blocked"].append(
                        {
                            "id": entry.name,
                            "path": str(self.root / entry.name),
                            "reason": (
                                "model path is a symlink"
                                if stat.S_ISLNK(entry_stat.st_mode)
                                else "model path is a reparse point"
                            ),
                        }
                    )
                continue
            if not stat.S_ISDIR(entry_stat.st_mode):
                if stat.S_ISLNK(entry_stat.st_mode):
                    if MODEL_ID_PATTERN.fullmatch(entry.name) and entry.name not in catalogued_ids:
                        result["blocked"].append(
                            {
                                "id": entry.name,
                                "path": str(self.root / entry.name),
                                "reason": "model path is a symlink",
                            }
                        )
                continue
            if not MODEL_ID_PATTERN.fullmatch(entry.name) or entry.name in catalogued_ids:
                continue
            directory = self.root / entry.name
            try:
                files, size = self._inventory(directory)
            except ValueError:
                result["blocked"].append(
                    {
                        "id": entry.name,
                        "path": str(directory),
                        "reason": (
                            "model directory is incomplete; model.bin and config.json are required"
                        ),
                    }
                )
                continue
            except OSError:
                result["blocked"].append(
                    {
                        "id": entry.name,
                        "path": str(directory),
                        "reason": "model directory could not be inventoried safely",
                    }
                )
                continue
            installed_at = datetime.fromtimestamp(entry_stat.st_mtime, UTC).isoformat()
            models.append(
                {
                    "id": entry.name,
                    "path": entry.name,
                    "source": "reconciled",
                    "revision": None,
                    "installed_at": installed_at,
                    "size": size,
                    "files": files,
                    "reconciled": True,
                    "reconciled_at": datetime.now(UTC).isoformat(),
                }
            )
            result["adopted"].append(entry.name)
            manifest_changed = True

        if manifest_changed:
            try:
                self._save(payload)
            except OSError as exc:
                result.update(
                    status="FAIL",
                    action="attention",
                    error=f"cannot save repaired model catalog: {exc}",
                )
                return result
        if result["blocked"]:
            result["action"] = "attention"
        elif result["adopted"] or result["dropped"] or quarantined:
            result["action"] = "repaired"
        return result

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
        entry = {
            "id": model_id,
            "path": model_id,
            "source": source,
            "revision": revision,
            "installed_at": datetime.now(UTC).isoformat(),
            "size": size,
            "files": files,
        }
        with self._exclusive_lock():
            target = self.root / model_id
            if target.exists():
                raise FileExistsError(f"model is already installed: {model_id}")
            temporary.replace(target)
            payload = self._load()
            payload["models"] = [
                item for item in payload["models"] if item["id"] != model_id
            ]
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
        result_queue: Any | None = None
        process: Any | None = None
        try:
            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            process = context.Process(
                target=_download_worker,
                args=(value, str(temporary), revision, result_queue),
                name=f"model-download-{value}",
            )
            process.start()
            started = time.monotonic()
            while process.is_alive():
                if cancelled and cancelled():
                    raise RuntimeError(f"model download cancelled: {value}")
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError(
                        f"model download timed out after {timeout_seconds} seconds: {value}"
                    )
                if progress:
                    # The download child can rename/replace/remove entries
                    # (e.g. a `*.incomplete` file, or a whole subdirectory)
                    # between listing and descent, so any OSError raised while
                    # walking the tree — including from rglob's own recursive
                    # descent, not just the per-file stat below — must not
                    # abort the whole install. Just skip this progress tick.
                    try:
                        downloaded = 0
                        for path in temporary.rglob("*"):
                            try:
                                if path.is_file():
                                    downloaded += path.stat().st_size
                            except OSError:
                                continue
                        progress(downloaded, expected)
                    except OSError:
                        pass
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
            _stop_process(process)
            _dispose_queue(result_queue)
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
        with self._exclusive_lock():
            return self._remove_locked(value)

    def _remove_locked(self, value: str) -> dict[str, Any]:
        entry = self.get(value)
        if entry is None:
            target = self.root / value
            try:
                target.resolve().relative_to(self.root.resolve())
            except (OSError, ValueError) as exc:
                raise ValueError("refusing to remove a model outside the managed catalog") from exc
            try:
                target_stat = target.lstat()
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"model is not installed: {value}") from exc
            except OSError as exc:
                raise ValueError("cannot inspect unmanaged model safely") from exc
            if self._is_reparse_point(target, target_stat):
                raise ValueError("refusing to remove an unmanaged model reparse point")
            if not stat.S_ISDIR(target_stat.st_mode):
                raise ValueError("refusing to remove an unmanaged model that is not a directory")
            shutil.rmtree(target)
            return {"removed": True, "id": value, "unmanaged": True}
        target = self.root / entry["path"]
        try:
            target.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError("refusing to remove a model outside the managed catalog") from exc
        try:
            target_stat = target.lstat()
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise ValueError("cannot inspect model safely") from exc
        if target_stat is not None and self._is_reparse_point(target, target_stat):
            raise ValueError("refusing to remove a managed model reparse point")
        if target_stat is not None and not stat.S_ISDIR(target_stat.st_mode):
            raise ValueError("refusing to remove a model that is not a directory")
        if target_stat is not None:
            shutil.rmtree(target)
        payload = self._load()
        payload["models"] = [item for item in payload["models"] if item["id"] != value]
        self._save(payload)
        return {"removed": True, "id": value}
