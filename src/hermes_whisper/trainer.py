from __future__ import annotations

import contextlib
import json
import math
import os
import random
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from .audio import LogMelFrontend
from .checkpoint import (
    load_trainer_state,
    resolve_checkpoint,
    save_checkpoint,
    verify_checkpoint,
)
from .config import ExperimentConfig
from .data import SpeechDataset, TrainingBatch, make_collate_fn
from .losses import compute_multitask_loss
from .manifest import ManifestRecord, manifest_fingerprint
from .model import HermesSpeechModel
from .tokenizer import HermesTokenizer

try:
    import torch
    from torch.nn.parallel import DistributedDataParallel
    from torch.utils.data import DataLoader, DistributedSampler
except ImportError:  # pragma: no cover
    torch = None
    DistributedDataParallel = None
    DataLoader = None
    DistributedSampler = None


def require_torch() -> None:
    if torch is None:
        raise RuntimeError("PyTorch is required for model training")


def initialize_distributed() -> tuple[int, int, int]:
    require_torch()
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    if world_size > 1 and not torch.distributed.is_initialized():
        backend = "nccl" if torch.cuda.is_available() else "gloo"
        torch.distributed.init_process_group(backend=backend)
    return rank, world_size, local_rank


def choose_training_device(local_rank: int, requested: str = "auto") -> Any:
    require_torch()
    if requested not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError(f"unsupported training device: {requested}")
    if requested == "mps":
        raise RuntimeError(
            "Hermes training on MPS is not supported because PyTorch CTC loss "
            "is unavailable. Use --device cpu."
        )
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        torch.cuda.set_device(local_rank)
        return torch.device("cuda", local_rank)
    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)
        return torch.device("cuda", local_rank)
    # MPS currently cannot execute the CTC loss used by Hermes training.
    return torch.device("cpu")


def seed_everything(seed: int, rank: int = 0) -> None:
    require_torch()
    resolved = seed + rank
    random.seed(resolved)
    np.random.seed(resolved)
    torch.manual_seed(resolved)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(resolved)


def learning_rate_multiplier(
    step: int,
    *,
    warmup_steps: int,
    max_steps: int,
    minimum_ratio: float,
) -> float:
    if warmup_steps and step < warmup_steps:
        return max(step, 1) / warmup_steps
    progress = (step - warmup_steps) / max(max_steps - warmup_steps, 1)
    progress = min(max(progress, 0.0), 1.0)
    cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
    return minimum_ratio + (1.0 - minimum_ratio) * cosine


class Trainer:
    def __init__(
        self,
        config: ExperimentConfig,
        tokenizer: HermesTokenizer,
        train_records: Iterable[ManifestRecord],
        validation_records: Iterable[ManifestRecord],
        run_directory: str | Path,
        device: str = "auto",
    ) -> None:
        require_torch()
        if tokenizer.languages != config.model.languages:
            raise ValueError("tokenizer and model languages differ")
        self.config = replace(
            config,
            model=config.model.with_vocab_size(tokenizer.vocab_size),
        )
        self.config.validate(allow_derived_vocab=False)
        self.tokenizer = tokenizer
        self.train_records = tuple(train_records)
        self.validation_records = tuple(validation_records)
        if not self.train_records:
            raise ValueError("training records cannot be empty")
        if not self.validation_records:
            raise ValueError("validation records cannot be empty")
        self.run_directory = Path(run_directory)
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.rank, self.world_size, self.local_rank = initialize_distributed()
        self.is_primary = self.rank == 0
        self.device = choose_training_device(self.local_rank, device)
        seed_everything(self.config.training.seed, self.rank)

        self.frontend = LogMelFrontend(self.config.audio).to(self.device)
        base_model = HermesSpeechModel(
            self.config.audio,
            self.config.model,
            pad_id=tokenizer.pad_id,
        ).to(self.device)
        if self.world_size > 1:
            device_ids = [self.local_rank] if self.device.type == "cuda" else None
            self.model = DistributedDataParallel(base_model, device_ids=device_ids)
        else:
            self.model = base_model
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=self.config.training.learning_rate,
            weight_decay=self.config.training.weight_decay,
            betas=(0.9, 0.98),
            eps=1e-8,
        )
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            self.optimizer,
            lr_lambda=lambda step: learning_rate_multiplier(
                step,
                warmup_steps=self.config.training.warmup_steps,
                max_steps=self.config.training.max_steps,
                minimum_ratio=self.config.training.min_learning_rate_ratio,
            ),
        )
        scaler_enabled = self.config.training.precision == "fp16" and self.device.type == "cuda"
        self.scaler = torch.amp.GradScaler("cuda", enabled=scaler_enabled)
        self.global_step = 0
        self.resume_directory: Path | None = None
        self.train_fingerprint = manifest_fingerprint(self.train_records)
        self._write_run_metadata()

    @property
    def unwrapped_model(self) -> Any:
        return self.model.module if hasattr(self.model, "module") else self.model

    def _write_run_metadata(self) -> None:
        if not self.is_primary:
            return
        self.config.save(self.run_directory / "config.json")
        self.tokenizer.save(self.run_directory / "tokenizer.json")
        payload = {
            "model_name": self.config.model.name,
            "config_fingerprint": self.config.fingerprint(),
            "train_manifest_fingerprint": self.train_fingerprint,
            "parameters": self.unwrapped_model.parameter_count,
            "world_size": self.world_size,
        }
        (self.run_directory / "run.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _loader(
        self,
        records: tuple[ManifestRecord, ...],
        *,
        training: bool,
    ) -> tuple[Any, Any]:
        dataset = SpeechDataset(records, self.tokenizer, self.config)
        sampler = None
        if self.world_size > 1:
            sampler = DistributedSampler(
                dataset,
                num_replicas=self.world_size,
                rank=self.rank,
                shuffle=training,
                seed=self.config.training.seed,
                drop_last=training and len(dataset) >= self.config.training.batch_size,
            )
        loader = DataLoader(
            dataset,
            batch_size=self.config.training.batch_size,
            shuffle=training and sampler is None,
            sampler=sampler,
            num_workers=self.config.training.num_workers,
            collate_fn=make_collate_fn(self.tokenizer, self.config),
            pin_memory=self.device.type == "cuda",
            drop_last=training and len(dataset) >= self.config.training.batch_size,
            persistent_workers=self.config.training.num_workers > 0,
        )
        return loader, sampler

    def _autocast(self) -> Any:
        precision = self.config.training.precision
        if precision == "fp32":
            return contextlib.nullcontext()
        if self.device.type == "cuda":
            dtype = torch.float16 if precision == "fp16" else torch.bfloat16
            return torch.autocast("cuda", dtype=dtype)
        if self.device.type == "cpu" and precision == "bf16":
            return torch.autocast("cpu", dtype=torch.bfloat16)
        return contextlib.nullcontext()

    def _forward_loss(self, batch: TrainingBatch) -> Any:
        batch = batch.to(self.device)
        mel = self.frontend(batch.waveforms)
        output = self.model(mel, batch.mel_lengths, batch.decoder_input_ids)
        return compute_multitask_loss(
            output,
            batch,
            config=self.config.model,
            pad_id=self.tokenizer.pad_id,
        )

    def resume(self, checkpoint: str | Path) -> None:
        directory = resolve_checkpoint(checkpoint)
        metadata = verify_checkpoint(directory)
        saved_config = ExperimentConfig.load(directory / "config.json")
        saved_tokenizer = HermesTokenizer.load(directory / "tokenizer.json")
        if saved_config.fingerprint() != self.config.fingerprint():
            raise ValueError("resume config does not match the current run")
        if (
            saved_tokenizer.token_bytes != self.tokenizer.token_bytes
            or saved_tokenizer.merges != self.tokenizer.merges
            or saved_tokenizer.languages != self.tokenizer.languages
            or saved_tokenizer.timestamp_resolution != self.tokenizer.timestamp_resolution
            or saved_tokenizer.max_timestamp_seconds != self.tokenizer.max_timestamp_seconds
        ):
            raise ValueError("resume tokenizer does not match the current run")
        if metadata["train_manifest_fingerprint"] != self.train_fingerprint:
            raise ValueError("resume training manifest does not match the checkpoint")
        model_state = torch.load(
            directory / "model.pt",
            map_location=self.device,
            weights_only=True,
        )
        self.unwrapped_model.load_state_dict(model_state, strict=True)
        state = load_trainer_state(directory, device=self.device)
        self.optimizer.load_state_dict(state["optimizer"])
        self.scheduler.load_state_dict(state["scheduler"])
        if state.get("scaler") is not None:
            self.scaler.load_state_dict(state["scaler"])
        self.global_step = int(state["step"])
        if self.global_step > self.config.training.max_steps:
            raise ValueError("checkpoint step exceeds configured max_steps")
        self.resume_directory = directory

    def train(self) -> Path | None:
        if self.global_step == self.config.training.max_steps:
            return self.resume_directory if self.is_primary else None
        train_loader, sampler = self._loader(self.train_records, training=True)
        if len(train_loader) == 0:
            raise ValueError("training DataLoader is empty")
        iterator = iter(train_loader)
        epoch = 0
        accumulation = self.config.training.gradient_accumulation_steps
        self.optimizer.zero_grad(set_to_none=True)
        last_checkpoint: Path | None = None
        recent: dict[str, float] = {}
        micro_step = 0
        started = time.perf_counter()

        while self.global_step < self.config.training.max_steps:
            try:
                batch = next(iterator)
            except StopIteration:
                epoch += 1
                if sampler is not None:
                    sampler.set_epoch(epoch)
                iterator = iter(train_loader)
                batch = next(iterator)
            micro_step += 1
            should_step = micro_step % accumulation == 0
            sync_context = (
                contextlib.nullcontext()
                if should_step or not hasattr(self.model, "no_sync")
                else self.model.no_sync()
            )
            with sync_context, self._autocast():
                losses = self._forward_loss(batch)
                scaled_loss = losses.total / accumulation
            self.scaler.scale(scaled_loss).backward()
            recent = losses.detached()

            if not should_step:
                continue
            self.scaler.unscale_(self.optimizer)
            torch.nn.utils.clip_grad_norm_(
                self.model.parameters(),
                self.config.training.gradient_clip_norm,
            )
            self.scaler.step(self.optimizer)
            self.scaler.update()
            self.optimizer.zero_grad(set_to_none=True)
            self.scheduler.step()
            self.global_step += 1

            if self.is_primary and self.global_step % self.config.training.log_interval == 0:
                recent["step"] = self.global_step
                recent["learning_rate"] = self.optimizer.param_groups[0]["lr"]
                recent["elapsed_seconds"] = time.perf_counter() - started
                self._append_log(recent)

            if self.global_step % self.config.training.validation_interval == 0:
                validation = self.validate()
                recent.update({f"validation_{key}": value for key, value in validation.items()})

            if self.is_primary and self.global_step % self.config.training.checkpoint_interval == 0:
                last_checkpoint = save_checkpoint(
                    self.run_directory,
                    step=self.global_step,
                    model=self.unwrapped_model,
                    optimizer=self.optimizer,
                    scheduler=self.scheduler,
                    scaler=self.scaler,
                    config=self.config,
                    tokenizer=self.tokenizer,
                    train_manifest_fingerprint=self.train_fingerprint,
                    metrics=recent,
                )

        if self.is_primary and (
            last_checkpoint is None
            or int(last_checkpoint.name.removeprefix("step-")) != self.global_step
        ):
            last_checkpoint = save_checkpoint(
                self.run_directory,
                step=self.global_step,
                model=self.unwrapped_model,
                optimizer=self.optimizer,
                scheduler=self.scheduler,
                scaler=self.scaler,
                config=self.config,
                tokenizer=self.tokenizer,
                train_manifest_fingerprint=self.train_fingerprint,
                metrics=recent,
            )
        return last_checkpoint

    def validate(self) -> dict[str, float]:
        loader, _sampler = self._loader(self.validation_records, training=False)
        was_training = self.model.training
        self.model.eval()
        totals: dict[str, float] = {}
        batches = 0
        with torch.inference_mode():
            for batch in loader:
                with self._autocast():
                    losses = self._forward_loss(batch)
                for key, value in losses.detached().items():
                    totals[key] = totals.get(key, 0.0) + value
                batches += 1
        if was_training:
            self.model.train()
        if batches == 0:
            raise ValueError("validation DataLoader is empty")
        result = {key: value / batches for key, value in totals.items()}
        if self.world_size > 1:
            for key, value in result.items():
                tensor = torch.tensor(value, device=self.device)
                torch.distributed.all_reduce(tensor, op=torch.distributed.ReduceOp.SUM)
                result[key] = float(tensor.item() / self.world_size)
        return result

    def _append_log(self, payload: dict[str, float]) -> None:
        with (self.run_directory / "metrics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
