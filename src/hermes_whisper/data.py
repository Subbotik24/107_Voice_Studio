from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from .audio import feature_frame_count, load_audio
from .config import ExperimentConfig
from .manifest import ManifestRecord
from .tokenizer import HermesTokenizer

try:
    import torch
    from torch.utils.data import Dataset
except ImportError:  # pragma: no cover - dependency-light inspection path
    torch = None
    Dataset = object

DURATION_TOLERANCE_SECONDS = 0.1


def require_torch() -> None:
    if torch is None:
        raise RuntimeError("PyTorch is required for model training")


@dataclass
class TrainingBatch:
    waveforms: Any
    sample_lengths: Any
    mel_lengths: Any
    decoder_input_ids: Any
    decoder_labels: Any
    ctc_targets: Any
    ctc_target_lengths: Any
    language_targets: Any
    texts: list[str]
    record_ids: list[str]

    def to(self, device: Any) -> TrainingBatch:
        return TrainingBatch(
            waveforms=self.waveforms.to(device, non_blocking=True),
            sample_lengths=self.sample_lengths.to(device, non_blocking=True),
            mel_lengths=self.mel_lengths.to(device, non_blocking=True),
            decoder_input_ids=self.decoder_input_ids.to(device, non_blocking=True),
            decoder_labels=self.decoder_labels.to(device, non_blocking=True),
            ctc_targets=self.ctc_targets.to(device, non_blocking=True),
            ctc_target_lengths=self.ctc_target_lengths.to(device, non_blocking=True),
            language_targets=self.language_targets.to(device, non_blocking=True),
            texts=self.texts,
            record_ids=self.record_ids,
        )


class SpeechDataset(Dataset):
    def __init__(
        self,
        records: Sequence[ManifestRecord],
        tokenizer: HermesTokenizer,
        config: ExperimentConfig,
    ) -> None:
        require_torch()
        if not records:
            raise ValueError("dataset cannot be empty")
        self.records = tuple(records)
        self.tokenizer = tokenizer
        self.config = config
        self.language_to_index = {
            language: index for index, language in enumerate(config.model.languages)
        }
        if tokenizer.languages != config.model.languages:
            raise ValueError("tokenizer and model languages must match exactly")
        for record in self.records:
            if record.duration_seconds > config.audio.max_audio_seconds + 1e-6:
                raise ValueError(
                    f"record {record.record_id or record.audio} is {record.duration_seconds:.3f}s; "
                    f"pre-segment it to <= {config.audio.max_audio_seconds:.3f}s"
                )

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        waveform = load_audio(record.audio, self.config.audio.sample_rate)
        actual_duration = len(waveform) / self.config.audio.sample_rate
        if actual_duration > self.config.audio.max_audio_seconds + DURATION_TOLERANCE_SECONDS:
            raise ValueError(
                f"audio {record.record_id or record.audio} is actually "
                f"{actual_duration:.3f}s; pre-segment it to "
                f"<= {self.config.audio.max_audio_seconds:.3f}s"
            )
        if abs(actual_duration - record.duration_seconds) > DURATION_TOLERANCE_SECONDS:
            raise ValueError(
                f"duration mismatch for {record.record_id or record.audio}: "
                f"manifest={record.duration_seconds:.3f}s, actual={actual_duration:.3f}s"
            )
        token_ids = self.tokenizer.encode_transcript(
            record.text,
            language=record.language,
            segments=record.segments or None,
        )
        if len(token_ids) > self.config.model.max_text_tokens:
            raise ValueError(
                f"record {record.record_id or record.audio} uses {len(token_ids)} tokens; "
                f"limit is {self.config.model.max_text_tokens}"
            )
        ctc_ids = self.tokenizer.encode_text(record.text)
        return {
            "waveform": torch.from_numpy(waveform),
            "sample_length": len(waveform),
            "token_ids": torch.tensor(token_ids, dtype=torch.long),
            "ctc_ids": torch.tensor(ctc_ids, dtype=torch.long),
            "language_target": self.language_to_index[record.language],
            "text": record.text,
            "record_id": record.record_id or record.audio,
        }


def collate_speech_batch(
    samples: Sequence[dict[str, Any]],
    *,
    tokenizer: HermesTokenizer,
    config: ExperimentConfig,
) -> TrainingBatch:
    require_torch()
    if not samples:
        raise ValueError("cannot collate an empty batch")
    batch_size = len(samples)
    max_samples = max(max(item["sample_length"] for item in samples), config.audio.n_fft)
    max_tokens = max(len(item["token_ids"]) for item in samples)
    max_ctc = max(len(item["ctc_ids"]) for item in samples)

    waveforms = torch.zeros(batch_size, max_samples, dtype=torch.float32)
    token_ids = torch.full(
        (batch_size, max_tokens),
        tokenizer.pad_id,
        dtype=torch.long,
    )
    ctc_targets = torch.full(
        (batch_size, max_ctc),
        tokenizer.pad_id,
        dtype=torch.long,
    )
    sample_lengths: list[int] = []
    mel_lengths: list[int] = []
    ctc_lengths: list[int] = []
    language_targets: list[int] = []
    texts: list[str] = []
    record_ids: list[str] = []

    for batch_index, item in enumerate(samples):
        sample_length = item["sample_length"]
        sequence_length = len(item["token_ids"])
        ctc_length = len(item["ctc_ids"])
        waveforms[batch_index, :sample_length] = item["waveform"]
        token_ids[batch_index, :sequence_length] = item["token_ids"]
        ctc_targets[batch_index, :ctc_length] = item["ctc_ids"]
        sample_lengths.append(sample_length)
        mel_length = feature_frame_count(sample_length, config.audio)
        mel_lengths.append(mel_length)
        encoder_length = (mel_length + 1) // 2
        if ctc_length > encoder_length:
            raise ValueError(
                f"CTC target for {item['record_id']} is too long "
                f"({ctc_length} tokens > {encoder_length} encoder frames)"
            )
        ctc_lengths.append(ctc_length)
        language_targets.append(item["language_target"])
        texts.append(item["text"])
        record_ids.append(item["record_id"])

    return TrainingBatch(
        waveforms=waveforms,
        sample_lengths=torch.tensor(sample_lengths, dtype=torch.long),
        mel_lengths=torch.tensor(mel_lengths, dtype=torch.long),
        decoder_input_ids=token_ids[:, :-1],
        decoder_labels=token_ids[:, 1:],
        ctc_targets=ctc_targets,
        ctc_target_lengths=torch.tensor(ctc_lengths, dtype=torch.long),
        language_targets=torch.tensor(language_targets, dtype=torch.long),
        texts=texts,
        record_ids=record_ids,
    )


def make_collate_fn(
    tokenizer: HermesTokenizer,
    config: ExperimentConfig,
) -> Any:
    return partial(collate_speech_batch, tokenizer=tokenizer, config=config)
