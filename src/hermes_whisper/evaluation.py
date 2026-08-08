from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import ExperimentConfig
from .decoding import select_device, transcribe_file
from .manifest import ManifestRecord
from .metrics import ErrorRateAccumulator
from .tokenizer import HermesTokenizer


def evaluate_records(
    model: Any,
    config: ExperimentConfig,
    tokenizer: HermesTokenizer,
    records: Iterable[ManifestRecord],
    *,
    device: str = "auto",
    output_jsonl: str | Path | None = None,
    language_mode: str = "auto",
) -> dict[str, Any]:
    if language_mode not in {"auto", "reference"}:
        raise ValueError("language_mode must be auto or reference")
    resolved_device = select_device(device)
    model.to(resolved_device).eval()
    accumulator = ErrorRateAccumulator()
    results: list[dict[str, Any]] = []
    audio_seconds = 0.0
    language_correct = 0
    record_count = 0
    started = time.perf_counter()
    for record in records:
        transcription = transcribe_file(
            model,
            config,
            tokenizer,
            record.audio,
            language="auto" if language_mode == "auto" else record.language,
            device=resolved_device,
        )
        accumulator.update(record.text, transcription.text)
        audio_seconds += record.duration_seconds
        language_correct += int(transcription.language == record.language)
        record_count += 1
        results.append(
            {
                "record_id": record.record_id or record.audio,
                "language": record.language,
                "reference": record.text,
                "hypothesis": transcription.text,
                "audio_seconds": record.duration_seconds,
            }
        )
    elapsed = time.perf_counter() - started
    metrics = accumulator.as_dict()
    metrics.update(
        {
            "audio_seconds": audio_seconds,
            "elapsed_seconds": elapsed,
            "real_time_factor": elapsed / max(audio_seconds, 1e-9),
            "language_accuracy": language_correct / max(record_count, 1),
            "language_mode": language_mode,
        }
    )
    if output_jsonl is not None:
        target = Path(output_jsonl)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in results) + "\n",
            encoding="utf-8",
        )
    return metrics
