from __future__ import annotations

import json
import math
import shutil
import wave
from pathlib import Path

import numpy as np

from .checkpoint import verify_checkpoint
from .config import AudioConfig, ExperimentConfig, ModelConfig, TrainingConfig
from .manifest import ManifestRecord, Provenance, write_manifest
from .tokenizer import HermesTokenizer
from .trainer import Trainer

TRAIN_EXAMPLES = (
    ("uk", "технічний опис", 220.0),
    ("uk", "проєкт готовий", 260.0),
    ("cs", "technický popis", 330.0),
    ("cs", "projekt je připraven", 390.0),
)
VALIDATION_EXAMPLES = (
    ("uk", "перевірка", 440.0),
    ("cs", "zkouška", 520.0),
)


def _write_tone(path: Path, frequency: float, sample_rate: int, seconds: float) -> None:
    sample_count = round(sample_rate * seconds)
    positions = np.arange(sample_count, dtype=np.float32) / sample_rate
    envelope = np.minimum(positions / 0.03, 1.0) * np.minimum((seconds - positions) / 0.03, 1.0)
    waveform = 0.2 * np.sin(2 * math.pi * frequency * positions) * envelope
    pcm = np.clip(waveform * 32767, -32768, 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def create_smoke_fixture(directory: str | Path) -> dict[str, Path]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    seconds = 1.0
    all_examples = TRAIN_EXAMPLES + VALIDATION_EXAMPLES
    tokenizer = HermesTokenizer.train(
        (text for _language, text, _frequency in all_examples),
        target_text_vocab_size=320,
        min_pair_frequency=1,
        languages=("uk", "cs"),
        timestamp_resolution=0.1,
        max_timestamp_seconds=1.2,
    )
    tokenizer_path = root / "tokenizer.json"
    tokenizer.save(tokenizer_path)

    provenance = Provenance(
        source="generated-smoke-fixture",
        license="CC0-1.0",
        consent=True,
        speaker_id="synthetic-tone",
    )
    train_records: list[ManifestRecord] = []
    validation_records: list[ManifestRecord] = []
    for split, examples, destination in (
        ("train", TRAIN_EXAMPLES, train_records),
        ("validation", VALIDATION_EXAMPLES, validation_records),
    ):
        for index, (language, text, frequency) in enumerate(examples):
            audio = root / "audio" / f"{split}-{index:02d}.wav"
            _write_tone(audio, frequency, sample_rate, seconds)
            destination.append(
                ManifestRecord(
                    audio=str(audio),
                    text=text,
                    language=language,
                    duration_seconds=seconds,
                    provenance=provenance,
                    record_id=f"{split}-{index:02d}",
                    split=split,
                )
            )
    train_manifest = root / "train.jsonl"
    validation_manifest = root / "validation.jsonl"
    write_manifest(train_records, train_manifest)
    write_manifest(validation_records, validation_manifest)
    config = ExperimentConfig(
        audio=AudioConfig(max_audio_seconds=1.2),
        model=ModelConfig(
            name="hermes-whisper-smoke",
            vocab_size=tokenizer.vocab_size,
            d_model=48,
            encoder_layers=1,
            decoder_layers=1,
            attention_heads=4,
            ffn_multiplier=2.0,
            convolution_kernel=7,
            dropout=0.0,
            max_text_tokens=64,
            languages=("uk", "cs"),
            ctc_weight=0.2,
            language_weight=0.05,
            label_smoothing=0.0,
        ),
        training=TrainingConfig(
            seed=17,
            max_steps=2,
            batch_size=2,
            gradient_accumulation_steps=1,
            learning_rate=1e-3,
            min_learning_rate_ratio=0.5,
            warmup_steps=1,
            weight_decay=0.0,
            gradient_clip_norm=1.0,
            validation_interval=2,
            checkpoint_interval=2,
            log_interval=1,
            num_workers=0,
            precision="fp32",
        ),
    )
    config_path = root / "config.json"
    config.save(config_path)
    return {
        "config": config_path,
        "tokenizer": tokenizer_path,
        "train_manifest": train_manifest,
        "validation_manifest": validation_manifest,
    }


def run_smoke_training(
    directory: str | Path,
    *,
    clean: bool = True,
    device: str = "auto",
) -> dict[str, object]:
    root = Path(directory)
    if clean and root.exists():
        shutil.rmtree(root)
    fixture = create_smoke_fixture(root / "fixture")
    config = ExperimentConfig.load(fixture["config"])
    tokenizer = HermesTokenizer.load(fixture["tokenizer"])
    from .manifest import load_manifest

    train_records = load_manifest(
        fixture["train_manifest"],
        allowed_languages=config.model.languages,
        require_audio_exists=True,
    )
    validation_records = load_manifest(
        fixture["validation_manifest"],
        allowed_languages=config.model.languages,
        require_audio_exists=True,
    )
    run_directory = root / "run"
    trainer = Trainer(
        config,
        tokenizer,
        train_records,
        validation_records,
        run_directory,
        device=device,
    )
    checkpoint = trainer.train()
    if checkpoint is None:
        raise RuntimeError("primary smoke trainer did not produce a checkpoint")
    metadata = verify_checkpoint(checkpoint)
    result = {
        "status": "PASS",
        "checkpoint": str(checkpoint.resolve()),
        "step": metadata["step"],
        "parameters": trainer.unwrapped_model.parameter_count,
        "metrics": metadata["metrics"],
    }
    (root / "smoke-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
