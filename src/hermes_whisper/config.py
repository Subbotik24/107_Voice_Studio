from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16_000
    n_fft: int = 400
    win_length: int = 400
    hop_length: int = 160
    n_mels: int = 80
    min_frequency: float = 0.0
    max_frequency: float | None = 8_000.0
    max_audio_seconds: float = 30.0

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not 0 < self.hop_length <= self.win_length <= self.n_fft:
            raise ValueError("expected hop_length <= win_length <= n_fft")
        if self.n_mels < 8:
            raise ValueError("n_mels must be at least 8")
        nyquist = self.sample_rate / 2
        if self.max_frequency is not None and self.max_frequency > nyquist:
            raise ValueError("max_frequency cannot exceed Nyquist frequency")
        if self.max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")

    @property
    def max_samples(self) -> int:
        return round(self.sample_rate * self.max_audio_seconds)

    @property
    def max_frames(self) -> int:
        if self.max_samples < self.n_fft:
            return 1
        return 1 + (self.max_samples - self.n_fft) // self.hop_length


@dataclass(frozen=True)
class ModelConfig:
    name: str = "hermes-whisper-150m"
    vocab_size: int = 0
    d_model: int = 512
    encoder_layers: int = 12
    decoder_layers: int = 8
    attention_heads: int = 8
    ffn_multiplier: float = 4.0
    convolution_kernel: int = 15
    dropout: float = 0.1
    max_text_tokens: int = 512
    languages: tuple[str, ...] = ("uk", "cs")
    ctc_weight: float = 0.2
    language_weight: float = 0.05
    label_smoothing: float = 0.1

    def validate(self, *, allow_derived_vocab: bool = True) -> None:
        if not self.name.strip():
            raise ValueError("model name cannot be empty")
        if self.vocab_size <= 0 and not allow_derived_vocab:
            raise ValueError("vocab_size must be set from the tokenizer")
        if self.d_model <= 0 or self.d_model % self.attention_heads:
            raise ValueError("d_model must be positive and divisible by attention_heads")
        if self.encoder_layers < 1 or self.decoder_layers < 1:
            raise ValueError("encoder_layers and decoder_layers must be positive")
        if self.ffn_multiplier < 1:
            raise ValueError("ffn_multiplier must be at least 1")
        if self.convolution_kernel < 3 or self.convolution_kernel % 2 == 0:
            raise ValueError("convolution_kernel must be odd and at least 3")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be in [0, 1)")
        if self.max_text_tokens < 16:
            raise ValueError("max_text_tokens must be at least 16")
        if not self.languages or len(set(self.languages)) != len(self.languages):
            raise ValueError("languages must be a non-empty unique list")
        if self.ctc_weight < 0 or self.language_weight < 0:
            raise ValueError("auxiliary loss weights cannot be negative")
        if not 0 <= self.label_smoothing < 1:
            raise ValueError("label_smoothing must be in [0, 1)")

    def with_vocab_size(self, vocab_size: int) -> ModelConfig:
        if vocab_size <= 0:
            raise ValueError("vocab_size must be positive")
        return replace(self, vocab_size=vocab_size)

    @property
    def ffn_hidden_size(self) -> int:
        return int(self.d_model * self.ffn_multiplier)

    def estimated_parameter_count(self, n_mels: int = 80) -> int:
        """Return the exact count implied by the implemented layer definitions."""
        d = self.d_model
        f = self.ffn_hidden_size
        v = max(self.vocab_size, 1)
        k = self.convolution_kernel
        language_count = len(self.languages)
        encoder_block = 6 * d * f + 4 * f + 7 * d * d + d * k + 22 * d
        encoder = 3 * n_mels * d + 3 * d * d + 4 * d + self.encoder_layers * encoder_block
        decoder_block = 8 * d * d + 3 * d * f + 2 * f + 15 * d
        decoder = v * d + self.decoder_layers * decoder_block + 2 * d + v
        auxiliary_heads = v * d + v + language_count * d + language_count
        return encoder + decoder + auxiliary_heads


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 17
    max_steps: int = 100_000
    batch_size: int = 8
    gradient_accumulation_steps: int = 4
    learning_rate: float = 3e-4
    min_learning_rate_ratio: float = 0.1
    warmup_steps: int = 5_000
    weight_decay: float = 0.1
    gradient_clip_norm: float = 1.0
    validation_interval: int = 1_000
    checkpoint_interval: int = 1_000
    log_interval: int = 20
    num_workers: int = 4
    precision: str = "bf16"

    def validate(self) -> None:
        positive_ints = {
            "max_steps": self.max_steps,
            "batch_size": self.batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "validation_interval": self.validation_interval,
            "checkpoint_interval": self.checkpoint_interval,
            "log_interval": self.log_interval,
        }
        for name, value in positive_ints.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.num_workers < 0:
            raise ValueError("num_workers cannot be negative")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if not 0 < self.min_learning_rate_ratio <= 1:
            raise ValueError("min_learning_rate_ratio must be in (0, 1]")
        if not 0 <= self.warmup_steps < self.max_steps:
            raise ValueError("warmup_steps must be in [0, max_steps)")
        if self.weight_decay < 0 or self.gradient_clip_norm <= 0:
            raise ValueError("weight_decay and gradient_clip_norm are invalid")
        if self.precision not in {"fp32", "fp16", "bf16"}:
            raise ValueError("precision must be fp32, fp16, or bf16")


@dataclass(frozen=True)
class ExperimentConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)

    def validate(self, *, allow_derived_vocab: bool = True) -> None:
        self.audio.validate()
        self.model.validate(allow_derived_vocab=allow_derived_vocab)
        self.training.validate()
        if self.model.languages != tuple(self.model.languages):
            raise ValueError("languages must be normalized to a tuple")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentConfig:
        model_data = dict(data.get("model", {}))
        if "languages" in model_data:
            model_data["languages"] = tuple(model_data["languages"])
        config = cls(
            audio=AudioConfig(**data.get("audio", {})),
            model=ModelConfig(**model_data),
            training=TrainingConfig(**data.get("training", {})),
        )
        config.validate()
        return config

    @classmethod
    def load(cls, path: str | Path) -> ExperimentConfig:
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON config {source}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("experiment config must be a JSON object")
        return cls.from_dict(data)
