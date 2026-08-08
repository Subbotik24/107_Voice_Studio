from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ModelConfig
from .data import TrainingBatch
from .model import ModelOutput

try:
    import torch
    import torch.nn.functional as F
except ImportError:  # pragma: no cover
    torch = None
    F = None


@dataclass
class LossOutput:
    total: Any
    sequence: Any
    ctc: Any
    language: Any

    def detached(self) -> dict[str, float]:
        return {
            "loss": float(self.total.detach().cpu()),
            "sequence_loss": float(self.sequence.detach().cpu()),
            "ctc_loss": float(self.ctc.detach().cpu()),
            "language_loss": float(self.language.detach().cpu()),
        }


def compute_multitask_loss(
    output: ModelOutput,
    batch: TrainingBatch,
    *,
    config: ModelConfig,
    pad_id: int,
) -> LossOutput:
    if torch is None:
        raise RuntimeError("PyTorch is required to compute loss")
    sequence_loss = F.cross_entropy(
        output.logits.reshape(-1, output.logits.shape[-1]),
        batch.decoder_labels.reshape(-1),
        ignore_index=pad_id,
        label_smoothing=config.label_smoothing,
    )
    flattened_targets = torch.cat(
        [
            batch.ctc_targets[index, : int(length)]
            for index, length in enumerate(batch.ctc_target_lengths)
        ]
    )
    ctc_loss = F.ctc_loss(
        output.ctc_logits.log_softmax(dim=-1).transpose(0, 1),
        flattened_targets,
        output.encoder_lengths,
        batch.ctc_target_lengths,
        blank=pad_id,
        reduction="mean",
        zero_infinity=True,
    )
    language_loss = F.cross_entropy(output.language_logits, batch.language_targets)
    total = sequence_loss + config.ctc_weight * ctc_loss + config.language_weight * language_loss
    return LossOutput(
        total=total,
        sequence=sequence_loss,
        ctc=ctc_loss,
        language=language_loss,
    )
