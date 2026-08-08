from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import ExperimentConfig
from .model import HermesSpeechModel
from .tokenizer import HermesTokenizer

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None


CHECKPOINT_FORMAT_VERSION = 1
CHECKPOINT_FILES = ("config.json", "tokenizer.json", "model.pt", "trainer.pt")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_torch_save(value: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(value, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json_save(value: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def save_checkpoint(
    run_directory: str | Path,
    *,
    step: int,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    scaler: Any,
    config: ExperimentConfig,
    tokenizer: HermesTokenizer,
    train_manifest_fingerprint: str,
    metrics: dict[str, float] | None = None,
) -> Path:
    if torch is None:
        raise RuntimeError("PyTorch is required for checkpoints")
    if step < 0:
        raise ValueError("checkpoint step cannot be negative")
    root = Path(run_directory)
    final_directory = root / "checkpoints" / f"step-{step:08d}"
    if final_directory.exists():
        raise FileExistsError(f"checkpoint already exists: {final_directory}")
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=f".step-{step:08d}.", dir=final_directory.parent)
    )
    try:
        tokenizer.save(temporary_directory / "tokenizer.json")
        config.save(temporary_directory / "config.json")
        _atomic_torch_save(model.state_dict(), temporary_directory / "model.pt")
        trainer_state = {
            "step": step,
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "scaler": scaler.state_dict() if scaler is not None else None,
        }
        _atomic_torch_save(trainer_state, temporary_directory / "trainer.pt")
        file_hashes = {
            name: _sha256(temporary_directory / name)
            for name in ("config.json", "tokenizer.json", "model.pt", "trainer.pt")
        }
        metadata = {
            "format_version": CHECKPOINT_FORMAT_VERSION,
            "step": step,
            "model_name": config.model.name,
            "config_fingerprint": config.fingerprint(),
            "train_manifest_fingerprint": train_manifest_fingerprint,
            "metrics": metrics or {},
            "files": file_hashes,
        }
        _atomic_json_save(metadata, temporary_directory / "metadata.json")
        os.replace(temporary_directory, final_directory)
    except BaseException:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        raise
    _atomic_json_save(
        {"checkpoint": str(final_directory.relative_to(root)), "step": step},
        root / "latest.json",
    )
    return final_directory


def resolve_checkpoint(path: str | Path) -> Path:
    source = Path(path)
    if source.is_dir() and (source / "metadata.json").is_file():
        return source
    latest = source / "latest.json"
    if source.is_dir() and latest.is_file():
        payload = json.loads(latest.read_text(encoding="utf-8"))
        resolved = (source / payload["checkpoint"]).resolve()
        try:
            resolved.relative_to(source.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError("latest checkpoint must stay inside the run directory") from exc
        if resolved.is_dir():
            return resolved
    raise FileNotFoundError(f"no checkpoint found at {source}")


def verify_checkpoint(path: str | Path) -> dict[str, Any]:
    directory = resolve_checkpoint(path)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("checkpoint metadata must be a JSON object")
    if metadata.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported checkpoint format")
    files = metadata.get("files")
    if not isinstance(files, dict) or set(files) != set(CHECKPOINT_FILES):
        raise ValueError(
            "checkpoint hash manifest must contain exactly: " + ", ".join(CHECKPOINT_FILES)
        )
    for filename in CHECKPOINT_FILES:
        expected = files[filename]
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise ValueError(f"checkpoint hash is invalid: {filename}")
        member = directory / filename
        if member.is_symlink() or not member.is_file():
            raise ValueError(f"checkpoint file is missing or unsafe: {filename}")
        actual = _sha256(member)
        if actual != expected:
            raise ValueError(f"checkpoint file hash mismatch: {filename}")
    return metadata


def load_model_checkpoint(
    path: str | Path,
    *,
    device: str | Any = "cpu",
) -> tuple[Any, ExperimentConfig, HermesTokenizer, dict[str, Any]]:
    if torch is None:
        raise RuntimeError("PyTorch is required to load a model")
    directory = resolve_checkpoint(path)
    metadata = verify_checkpoint(directory)
    tokenizer = HermesTokenizer.load(directory / "tokenizer.json")
    config = ExperimentConfig.load(directory / "config.json")
    if config.model.vocab_size != tokenizer.vocab_size:
        config = replace(config, model=config.model.with_vocab_size(tokenizer.vocab_size))
    model = HermesSpeechModel(
        config.audio,
        config.model,
        pad_id=tokenizer.pad_id,
    )
    state = torch.load(directory / "model.pt", map_location=device, weights_only=True)
    model.load_state_dict(state, strict=True)
    model.to(device)
    return model, config, tokenizer, metadata


def load_trainer_state(path: str | Path, *, device: str | Any = "cpu") -> dict[str, Any]:
    if torch is None:
        raise RuntimeError("PyTorch is required to load trainer state")
    directory = resolve_checkpoint(path)
    verify_checkpoint(directory)
    return torch.load(
        directory / "trainer.pt",
        map_location=device,
        weights_only=True,
    )
