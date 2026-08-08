from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .audio import LogMelFrontend, load_audio
from .config import ExperimentConfig
from .tokenizer import HermesTokenizer

try:
    import torch
    import torch.nn.functional as F
except ImportError:  # pragma: no cover
    torch = None
    F = None


@dataclass(frozen=True)
class DecodedChunk:
    start: float
    end: float
    text: str
    language: str
    language_confidence: float
    token_confidence: float


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str
    chunks: tuple[DecodedChunk, ...]
    audio_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "audio_seconds": self.audio_seconds,
            "chunks": [asdict(chunk) for chunk in self.chunks],
        }


def select_device(requested: str = "auto") -> Any:
    if torch is None:
        raise RuntimeError("PyTorch is required for inference")
    if requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def greedy_decode_memory(
    model: Any,
    memory: Any,
    memory_padding_mask: Any,
    tokenizer: HermesTokenizer,
    *,
    language: str,
    max_new_tokens: int,
) -> tuple[str, float]:
    if torch is None:
        raise RuntimeError("PyTorch is required for inference")
    prefix = [
        tokenizer.bos_id,
        tokenizer.language_id(language),
        tokenizer.special_id("<transcribe>"),
        tokenizer.no_timestamps_id,
    ]
    tokens = torch.tensor([prefix], dtype=torch.long, device=memory.device)
    log_probabilities: list[float] = []
    for _ in range(max_new_tokens):
        logits = model.decode(tokens, memory, memory_padding_mask)[:, -1, :]
        scores = F.log_softmax(logits.float(), dim=-1)
        # In no-timestamp mode, only text tokens and EOS may be emitted.
        allowed_eos = scores[:, tokenizer.eos_id].clone()
        scores[:, tokenizer.text_vocab_size :] = -torch.inf
        scores[:, tokenizer.eos_id] = allowed_eos
        next_token = scores.argmax(dim=-1)
        next_score = scores.gather(1, next_token.unsqueeze(1)).squeeze(1)
        token_id = int(next_token.item())
        if token_id == tokenizer.eos_id:
            break
        tokens = torch.cat((tokens, next_token.unsqueeze(1)), dim=1)
        log_probabilities.append(float(next_score.item()))
    generated = tokens[0, len(prefix) :].tolist()
    confidence = (
        math.exp(sum(log_probabilities) / len(log_probabilities)) if log_probabilities else 0.0
    )
    return tokenizer.decode_text(generated).strip(), confidence


def _decode_chunk(
    model: Any,
    frontend: Any,
    waveform: np.ndarray,
    config: ExperimentConfig,
    tokenizer: HermesTokenizer,
    *,
    language: str,
    device: Any,
) -> tuple[str, str, float, float]:
    tensor = torch.from_numpy(waveform).to(device=device, dtype=torch.float32).unsqueeze(0)
    mel = frontend(tensor)
    mel_lengths = torch.tensor([mel.shape[-1]], dtype=torch.long, device=device)
    memory, _lengths, padding_mask, language_logits = model.encode(mel, mel_lengths)
    probabilities = F.softmax(language_logits.float(), dim=-1)
    predicted_index = int(probabilities.argmax(dim=-1).item())
    predicted_language = config.model.languages[predicted_index]
    language_confidence = float(probabilities[0, predicted_index].item())
    resolved_language = predicted_language if language == "auto" else language
    text, token_confidence = greedy_decode_memory(
        model,
        memory,
        padding_mask,
        tokenizer,
        language=resolved_language,
        max_new_tokens=config.model.max_text_tokens - 4,
    )
    return text, resolved_language, language_confidence, token_confidence


def transcribe_file(
    model: Any,
    config: ExperimentConfig,
    tokenizer: HermesTokenizer,
    audio_path: str | Path,
    *,
    language: str = "auto",
    device: str | Any = "auto",
    overlap_seconds: float = 1.0,
) -> Transcription:
    if torch is None:
        raise RuntimeError("PyTorch is required for inference")
    if language != "auto" and language not in config.model.languages:
        raise ValueError(f"language must be auto or one of {config.model.languages}")
    if not 0 <= overlap_seconds < config.audio.max_audio_seconds:
        raise ValueError("overlap_seconds must be smaller than max_audio_seconds")
    resolved_device = select_device(device) if isinstance(device, str) else device
    model.to(resolved_device).eval()
    frontend = LogMelFrontend(config.audio).to(resolved_device).eval()
    waveform = load_audio(audio_path, config.audio.sample_rate)
    chunk_samples = config.audio.max_samples
    overlap_samples = round(overlap_seconds * config.audio.sample_rate)
    stride = chunk_samples - overlap_samples
    chunks: list[DecodedChunk] = []
    with torch.inference_mode():
        for start_sample in range(0, len(waveform), stride):
            end_sample = min(start_sample + chunk_samples, len(waveform))
            chunk = waveform[start_sample:end_sample]
            if len(chunk) < config.audio.n_fft:
                chunk = np.pad(chunk, (0, config.audio.n_fft - len(chunk)))
            text, chunk_language, language_confidence, token_confidence = _decode_chunk(
                model,
                frontend,
                chunk,
                config,
                tokenizer,
                language=language,
                device=resolved_device,
            )
            chunks.append(
                DecodedChunk(
                    start=start_sample / config.audio.sample_rate,
                    end=end_sample / config.audio.sample_rate,
                    text=text,
                    language=chunk_language,
                    language_confidence=language_confidence,
                    token_confidence=token_confidence,
                )
            )
            if end_sample == len(waveform):
                break
    merged = ""
    for chunk in chunks:
        merged = merge_text_overlap(merged, chunk.text)
    languages = [chunk.language for chunk in chunks]
    dominant = (
        max(
            config.model.languages,
            key=lambda candidate: (
                languages.count(candidate),
                -config.model.languages.index(candidate),
            ),
        )
        if languages
        else config.model.languages[0]
    )
    return Transcription(
        text=merged,
        language=dominant,
        chunks=tuple(chunks),
        audio_seconds=len(waveform) / config.audio.sample_rate,
    )


def merge_text_overlap(previous: str, current: str, *, maximum_words: int = 24) -> str:
    previous = previous.strip()
    current = current.strip()
    if not previous:
        return current
    if not current:
        return previous
    left = previous.split()
    right = current.split()
    maximum = min(maximum_words, len(left), len(right))
    overlap = 0
    for size in range(maximum, 0, -1):
        if [word.casefold() for word in left[-size:]] == [word.casefold() for word in right[:size]]:
            overlap = size
            break
    return " ".join(left + right[overlap:])
