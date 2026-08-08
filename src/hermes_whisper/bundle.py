from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO

from .checkpoint import resolve_checkpoint, verify_checkpoint

BUNDLE_FORMAT = "hermes-whisper"
BUNDLE_VERSION = 1
RUNTIME_MEMBERS = ("config.json", "tokenizer.json", "model.pt", "metadata.json")
REQUIRED_MEMBERS = {"bundle.json", *RUNTIME_MEMBERS}
SMALL_MEMBER_LIMIT = 64 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_stream(handle: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def create_model_bundle(checkpoint: str | Path, output: str | Path) -> dict[str, Any]:
    """Create an inference-only .hws bundle from a verified checkpoint.

    trainer.pt remains in the training checkpoint and is intentionally excluded
    from the runtime bundle. Runtime metadata is rewritten accordingly.
    """

    directory = resolve_checkpoint(checkpoint)
    checkpoint_metadata = verify_checkpoint(directory)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() != ".hws":
        raise ValueError("model bundle must use the .hws extension")

    runtime_paths = {
        name: directory / name for name in ("config.json", "tokenizer.json", "model.pt")
    }
    runtime_hashes = {name: _sha256(path) for name, path in runtime_paths.items()}
    runtime_metadata = dict(checkpoint_metadata)
    runtime_metadata["checkpoint_files"] = dict(checkpoint_metadata.get("files", {}))
    runtime_metadata["files"] = runtime_hashes
    runtime_metadata["runtime_bundle"] = True
    metadata_bytes = _json_bytes(runtime_metadata)

    member_manifest = {
        name: {"sha256": runtime_hashes[name], "size": runtime_paths[name].stat().st_size}
        for name in runtime_paths
    }
    member_manifest["metadata.json"] = {
        "sha256": _sha256_bytes(metadata_bytes),
        "size": len(metadata_bytes),
    }
    bundle_manifest = {
        "bundle_format": BUNDLE_FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "checkpoint_step": checkpoint_metadata["step"],
        "model_name": checkpoint_metadata["model_name"],
        "members": member_manifest,
    }

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in runtime_paths.items():
                archive.write(path, arcname=name)
            archive.writestr("metadata.json", metadata_bytes)
            archive.writestr("bundle.json", _json_bytes(bundle_manifest))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": str(target.resolve()),
        "size": target.stat().st_size,
        "sha256": _sha256(target),
        "model_name": checkpoint_metadata["model_name"],
        "checkpoint_step": checkpoint_metadata["step"],
        "bundle_version": BUNDLE_VERSION,
    }


def _open_and_inspect(source: Path) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".hws":
        raise ValueError("Hermes model bundle must use the .hws extension")
    try:
        archive = zipfile.ZipFile(source, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid .hws ZIP container: {source}") from exc
    try:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("bundle contains duplicate member names")
        if set(names) != REQUIRED_MEMBERS:
            missing = REQUIRED_MEMBERS - set(names)
            extra = set(names) - REQUIRED_MEMBERS
            raise ValueError(
                f"bundle member set is invalid; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        bundle_info = archive.getinfo("bundle.json")
        if bundle_info.file_size > SMALL_MEMBER_LIMIT:
            raise ValueError("bundle.json is unreasonably large")
        manifest = json.loads(archive.read("bundle.json"))
        if manifest.get("bundle_format") != BUNDLE_FORMAT:
            raise ValueError("unsupported bundle format")
        if manifest.get("bundle_version") != BUNDLE_VERSION:
            raise ValueError("unsupported bundle version")
        declared = manifest.get("members")
        if not isinstance(declared, dict) or set(declared) != set(RUNTIME_MEMBERS):
            raise ValueError("bundle manifest member list is invalid")
        for name in RUNTIME_MEMBERS:
            expected_size = int(declared[name].get("size", -1))
            if expected_size < 0 or archive.getinfo(name).file_size != expected_size:
                raise ValueError(f"bundle member size mismatch: {name}")
            if name != "model.pt" and expected_size > SMALL_MEMBER_LIMIT:
                raise ValueError(f"bundle member is unreasonably large: {name}")
    except BaseException:
        archive.close()
        raise
    return archive, manifest


def _validate_runtime_metadata(
    manifest: dict[str, Any], metadata: dict[str, Any], config: dict[str, Any]
) -> None:
    if metadata.get("model_name") != manifest.get("model_name"):
        raise ValueError("bundle model name differs from runtime metadata")
    if metadata.get("step") != manifest.get("checkpoint_step"):
        raise ValueError("bundle checkpoint step differs from runtime metadata")
    files = metadata.get("files")
    if not isinstance(files, dict):
        raise ValueError("runtime metadata file hashes are missing")
    declared = manifest["members"]
    for name in ("config.json", "tokenizer.json", "model.pt"):
        if files.get(name) != declared[name].get("sha256"):
            raise ValueError(f"runtime metadata hash mismatch: {name}")
    if not isinstance(config, dict) or not isinstance(config.get("model", {}), dict):
        raise ValueError("bundle config is invalid")


def verify_model_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    archive, manifest = _open_and_inspect(source)
    small_payloads: dict[str, bytes] = {}
    try:
        for name in RUNTIME_MEMBERS:
            with archive.open(name, "r") as stream:
                actual_hash, actual_size = _sha256_stream(stream)
            expected = manifest["members"][name]
            if actual_size != int(expected["size"]):
                raise ValueError(f"bundle member size mismatch: {name}")
            if actual_hash != expected["sha256"]:
                raise ValueError(f"bundle member hash mismatch: {name}")
            if name in {"config.json", "metadata.json"}:
                small_payloads[name] = archive.read(name)
    finally:
        archive.close()
    metadata = json.loads(small_payloads["metadata.json"])
    config = json.loads(small_payloads["config.json"])
    _validate_runtime_metadata(manifest, metadata, config)
    model = config.get("model", {})
    return {
        "path": str(source.resolve()),
        "sha256": _sha256(source),
        "size": source.stat().st_size,
        "model_name": manifest["model_name"],
        "checkpoint_step": manifest["checkpoint_step"],
        "bundle_version": manifest["bundle_version"],
        "languages": model.get("languages", []),
    }


def _cached_files_match(target: Path, manifest: dict[str, Any]) -> bool:
    try:
        for name in RUNTIME_MEMBERS:
            path = target / name
            expected = manifest["members"][name]
            if not path.is_file() or path.stat().st_size != int(expected["size"]):
                return False
            if _sha256(path) != expected["sha256"]:
                return False
    except OSError:
        return False
    return True


def extract_model_bundle(path: str | Path, cache_directory: str | Path) -> Path:
    source = Path(path)
    archive, manifest = _open_and_inspect(source)
    bundle_sha256 = _sha256(source)
    cache_root = Path(cache_directory).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / bundle_sha256
    marker = target / "verified.json"
    if marker.is_file() and _cached_files_match(target, manifest):
        archive.close()
        return target

    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle_sha256[:12]}.", dir=cache_root))
    try:
        for name in RUNTIME_MEMBERS:
            destination = temporary / name
            expected = manifest["members"][name]
            digest = hashlib.sha256()
            size = 0
            with archive.open(name, "r") as source_stream, destination.open("wb") as output:
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size != int(expected["size"]):
                raise ValueError(f"bundle member size mismatch: {name}")
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError(f"bundle member hash mismatch: {name}")
        metadata = json.loads((temporary / "metadata.json").read_text(encoding="utf-8"))
        config = json.loads((temporary / "config.json").read_text(encoding="utf-8"))
        _validate_runtime_metadata(manifest, metadata, config)
        (temporary / "verified.json").write_text(
            json.dumps(
                {
                    "bundle_sha256": bundle_sha256,
                    "model_name": manifest["model_name"],
                    "checkpoint_step": manifest["checkpoint_step"],
                    "bundle_version": manifest["bundle_version"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        archive.close()
    return target


def load_model_bundle(
    path: str | Path,
    *,
    cache_directory: str | Path,
    device: str | Any = "cpu",
) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Verify, cache, and load a .hws bundle for inference."""

    try:
        import torch
    except ImportError as exc:  # pragma: no cover - dependency-specific path
        raise RuntimeError("PyTorch is required to load Hermes Whisper") from exc

    from .config import ExperimentConfig
    from .decoding import select_device
    from .model import HermesSpeechModel
    from .tokenizer import HermesTokenizer

    source = Path(path)
    directory = extract_model_bundle(source, cache_directory)
    marker = json.loads((directory / "verified.json").read_text(encoding="utf-8"))
    tokenizer = HermesTokenizer.load(directory / "tokenizer.json")
    config = ExperimentConfig.load(directory / "config.json")
    if config.model.vocab_size != tokenizer.vocab_size:
        config = replace(config, model=config.model.with_vocab_size(tokenizer.vocab_size))
    resolved_device = select_device(device) if isinstance(device, str) else device
    model = HermesSpeechModel(config.audio, config.model, pad_id=tokenizer.pad_id)
    state = torch.load(directory / "model.pt", map_location=resolved_device, weights_only=True)
    model.load_state_dict(state, strict=True)
    model.to(resolved_device).eval()
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata.update(
        {
            "bundle_sha256": marker["bundle_sha256"],
            "checkpoint_step": marker["checkpoint_step"],
            "bundle_path": str(source.resolve()),
        }
    )
    return model, config, tokenizer, metadata
