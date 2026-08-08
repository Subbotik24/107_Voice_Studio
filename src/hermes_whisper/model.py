from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .config import AudioConfig, ModelConfig

try:
    import torch
    import torch.nn.functional as F
    from torch import nn
except ImportError:  # pragma: no cover - dependency-light inspection path
    torch = None
    F = None
    nn = None


def require_torch() -> None:
    if torch is None:
        raise RuntimeError("PyTorch is required to instantiate Hermes Whisper")


if torch is not None:

    def _sinusoidal_positions(length: int, width: int, device: Any, dtype: Any) -> Any:
        if width % 2:
            raise ValueError("sinusoidal position width must be even")
        positions = torch.arange(length, device=device, dtype=torch.float32).unsqueeze(1)
        scales = torch.exp(
            -math.log(10_000.0)
            * torch.arange(0, width, 2, device=device, dtype=torch.float32)
            / width
        )
        values = torch.zeros(length, width, device=device, dtype=torch.float32)
        values[:, 0::2] = torch.sin(positions * scales)
        values[:, 1::2] = torch.cos(positions * scales)
        return values.to(dtype=dtype)

    def _length_mask(lengths: Any, maximum: int) -> Any:
        positions = torch.arange(maximum, device=lengths.device).unsqueeze(0)
        return positions >= lengths.unsqueeze(1)

    class SwiGLU(nn.Module):
        def __init__(self, d_model: int, hidden_size: int, dropout: float) -> None:
            super().__init__()
            self.input_projection = nn.Linear(d_model, 2 * hidden_size)
            self.output_projection = nn.Linear(hidden_size, d_model)
            self.dropout = nn.Dropout(dropout)

        def forward(self, inputs: Any) -> Any:
            value, gate = self.input_projection(inputs).chunk(2, dim=-1)
            return self.output_projection(self.dropout(value * F.silu(gate)))

    class ConformerConvolution(nn.Module):
        def __init__(self, d_model: int, kernel_size: int, dropout: float) -> None:
            super().__init__()
            self.norm = nn.LayerNorm(d_model)
            self.pointwise_in = nn.Conv1d(d_model, 2 * d_model, kernel_size=1)
            self.depthwise = nn.Conv1d(
                d_model,
                d_model,
                kernel_size=kernel_size,
                padding=kernel_size // 2,
                groups=d_model,
            )
            self.batch_norm = nn.BatchNorm1d(d_model)
            self.pointwise_out = nn.Conv1d(d_model, d_model, kernel_size=1)
            self.dropout = nn.Dropout(dropout)

        def forward(self, inputs: Any) -> Any:
            hidden = self.norm(inputs).transpose(1, 2)
            hidden = F.glu(self.pointwise_in(hidden), dim=1)
            hidden = F.silu(self.batch_norm(self.depthwise(hidden)))
            hidden = self.pointwise_out(hidden).transpose(1, 2)
            return self.dropout(hidden)

    class ConformerEncoderBlock(nn.Module):
        def __init__(self, config: ModelConfig) -> None:
            super().__init__()
            hidden_size = config.ffn_hidden_size
            self.ffn1_norm = nn.LayerNorm(config.d_model)
            self.ffn1 = SwiGLU(config.d_model, hidden_size, config.dropout)
            self.attention_norm = nn.LayerNorm(config.d_model)
            self.attention = nn.MultiheadAttention(
                config.d_model,
                config.attention_heads,
                dropout=config.dropout,
                batch_first=True,
            )
            self.attention_dropout = nn.Dropout(config.dropout)
            self.convolution = ConformerConvolution(
                config.d_model,
                config.convolution_kernel,
                config.dropout,
            )
            self.ffn2_norm = nn.LayerNorm(config.d_model)
            self.ffn2 = SwiGLU(config.d_model, hidden_size, config.dropout)
            self.final_norm = nn.LayerNorm(config.d_model)

        def forward(self, inputs: Any, padding_mask: Any) -> Any:
            hidden = inputs + 0.5 * self.ffn1(self.ffn1_norm(inputs))
            normalized = self.attention_norm(hidden)
            attended, _ = self.attention(
                normalized,
                normalized,
                normalized,
                key_padding_mask=padding_mask,
                need_weights=False,
            )
            hidden = hidden + self.attention_dropout(attended)
            hidden = hidden + self.convolution(hidden)
            hidden = hidden + 0.5 * self.ffn2(self.ffn2_norm(hidden))
            hidden = self.final_norm(hidden)
            return hidden.masked_fill(padding_mask.unsqueeze(-1), 0.0)

    class AudioEncoder(nn.Module):
        def __init__(self, audio: AudioConfig, config: ModelConfig) -> None:
            super().__init__()
            self.audio = audio
            self.config = config
            self.conv1 = nn.Conv1d(audio.n_mels, config.d_model, kernel_size=3, padding=1)
            self.conv2 = nn.Conv1d(
                config.d_model,
                config.d_model,
                kernel_size=3,
                stride=2,
                padding=1,
            )
            self.dropout = nn.Dropout(config.dropout)
            self.blocks = nn.ModuleList(
                ConformerEncoderBlock(config) for _ in range(config.encoder_layers)
            )
            self.norm = nn.LayerNorm(config.d_model)

        @staticmethod
        def output_lengths(input_lengths: Any) -> Any:
            return (input_lengths + 1) // 2

        def forward(self, mel: Any, mel_lengths: Any) -> tuple[Any, Any, Any]:
            if mel.ndim != 3 or mel.shape[1] != self.audio.n_mels:
                raise ValueError(f"mel must have shape [batch, {self.audio.n_mels}, frames]")
            hidden = F.gelu(self.conv1(mel))
            hidden = F.gelu(self.conv2(hidden)).transpose(1, 2)
            lengths = self.output_lengths(mel_lengths)
            padding_mask = _length_mask(lengths, hidden.shape[1])
            positions = _sinusoidal_positions(
                hidden.shape[1], hidden.shape[2], hidden.device, hidden.dtype
            )
            hidden = self.dropout(hidden + positions.unsqueeze(0))
            hidden = hidden.masked_fill(padding_mask.unsqueeze(-1), 0.0)
            for block in self.blocks:
                hidden = block(hidden, padding_mask)
            return self.norm(hidden), lengths, padding_mask

    class DecoderBlock(nn.Module):
        def __init__(self, config: ModelConfig) -> None:
            super().__init__()
            self.self_norm = nn.LayerNorm(config.d_model)
            self.self_attention = nn.MultiheadAttention(
                config.d_model,
                config.attention_heads,
                dropout=config.dropout,
                batch_first=True,
            )
            self.cross_norm = nn.LayerNorm(config.d_model)
            self.cross_attention = nn.MultiheadAttention(
                config.d_model,
                config.attention_heads,
                dropout=config.dropout,
                batch_first=True,
            )
            self.ffn_norm = nn.LayerNorm(config.d_model)
            self.ffn = SwiGLU(config.d_model, config.ffn_hidden_size, config.dropout)
            self.dropout = nn.Dropout(config.dropout)

        def forward(
            self,
            inputs: Any,
            memory: Any,
            *,
            token_padding_mask: Any,
            memory_padding_mask: Any,
            causal_mask: Any,
        ) -> Any:
            normalized = self.self_norm(inputs)
            attended, _ = self.self_attention(
                normalized,
                normalized,
                normalized,
                attn_mask=causal_mask,
                key_padding_mask=token_padding_mask,
                need_weights=False,
            )
            hidden = inputs + self.dropout(attended)
            attended, _ = self.cross_attention(
                self.cross_norm(hidden),
                memory,
                memory,
                key_padding_mask=memory_padding_mask,
                need_weights=False,
            )
            hidden = hidden + self.dropout(attended)
            return hidden + self.ffn(self.ffn_norm(hidden))

    class TextDecoder(nn.Module):
        def __init__(self, config: ModelConfig, pad_id: int) -> None:
            super().__init__()
            self.config = config
            self.pad_id = pad_id
            self.token_embedding = nn.Embedding(
                config.vocab_size,
                config.d_model,
                padding_idx=pad_id,
            )
            self.blocks = nn.ModuleList(DecoderBlock(config) for _ in range(config.decoder_layers))
            self.norm = nn.LayerNorm(config.d_model)
            self.output_bias = nn.Parameter(torch.zeros(config.vocab_size))
            self.dropout = nn.Dropout(config.dropout)

        def forward(
            self,
            token_ids: Any,
            memory: Any,
            memory_padding_mask: Any,
        ) -> Any:
            if token_ids.ndim != 2:
                raise ValueError("token_ids must have shape [batch, tokens]")
            if token_ids.shape[1] > self.config.max_text_tokens:
                raise ValueError("decoder sequence exceeds max_text_tokens")
            token_padding_mask = token_ids.eq(self.pad_id)
            positions = _sinusoidal_positions(
                token_ids.shape[1],
                self.config.d_model,
                token_ids.device,
                self.token_embedding.weight.dtype,
            )
            hidden = self.token_embedding(token_ids) * math.sqrt(self.config.d_model)
            hidden = self.dropout(hidden + positions.unsqueeze(0))
            causal_mask = torch.triu(
                torch.ones(
                    token_ids.shape[1],
                    token_ids.shape[1],
                    dtype=torch.bool,
                    device=token_ids.device,
                ),
                diagonal=1,
            )
            for block in self.blocks:
                hidden = block(
                    hidden,
                    memory,
                    token_padding_mask=token_padding_mask,
                    memory_padding_mask=memory_padding_mask,
                    causal_mask=causal_mask,
                )
            hidden = self.norm(hidden)
            return F.linear(hidden, self.token_embedding.weight, self.output_bias)

    @dataclass
    class ModelOutput:
        logits: Any
        ctc_logits: Any
        language_logits: Any
        encoder_memory: Any
        encoder_lengths: Any
        encoder_padding_mask: Any

    class HermesSpeechModel(nn.Module):
        """Conformer encoder + autoregressive decoder with CTC stabilization."""

        def __init__(self, audio: AudioConfig, config: ModelConfig, *, pad_id: int) -> None:
            super().__init__()
            config.validate(allow_derived_vocab=False)
            if not 0 <= pad_id < config.vocab_size:
                raise ValueError("pad_id lies outside the vocabulary")
            self.audio_config = audio
            self.model_config = config
            self.pad_id = pad_id
            self.encoder = AudioEncoder(audio, config)
            self.decoder = TextDecoder(config, pad_id)
            self.ctc_head = nn.Linear(config.d_model, config.vocab_size)
            self.language_head = nn.Linear(config.d_model, len(config.languages))
            self.apply(self._initialize)

        @staticmethod
        def _initialize(module: Any) -> None:
            if isinstance(module, (nn.Linear, nn.Conv1d)):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
                if module.padding_idx is not None:
                    with torch.no_grad():
                        module.weight[module.padding_idx].zero_()

        def encode(self, mel: Any, mel_lengths: Any) -> tuple[Any, Any, Any, Any]:
            memory, lengths, padding_mask = self.encoder(mel, mel_lengths)
            valid = (~padding_mask).unsqueeze(-1)
            pooled = (memory * valid).sum(dim=1) / valid.sum(dim=1).clamp(min=1)
            language_logits = self.language_head(pooled)
            return memory, lengths, padding_mask, language_logits

        def decode(self, token_ids: Any, memory: Any, memory_padding_mask: Any) -> Any:
            return self.decoder(token_ids, memory, memory_padding_mask)

        def forward(self, mel: Any, mel_lengths: Any, decoder_input_ids: Any) -> ModelOutput:
            memory, lengths, padding_mask, language_logits = self.encode(mel, mel_lengths)
            logits = self.decode(decoder_input_ids, memory, padding_mask)
            ctc_logits = self.ctc_head(memory)
            return ModelOutput(
                logits=logits,
                ctc_logits=ctc_logits,
                language_logits=language_logits,
                encoder_memory=memory,
                encoder_lengths=lengths,
                encoder_padding_mask=padding_mask,
            )

        @property
        def parameter_count(self) -> int:
            return sum(parameter.numel() for parameter in self.parameters())


else:

    @dataclass
    class ModelOutput:  # pragma: no cover
        logits: Any
        ctc_logits: Any
        language_logits: Any
        encoder_memory: Any
        encoder_lengths: Any
        encoder_padding_mask: Any

    class HermesSpeechModel:  # pragma: no cover
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            require_torch()
