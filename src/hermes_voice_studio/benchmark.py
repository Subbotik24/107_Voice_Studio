from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from hermes_whisper.metrics import ErrorRateAccumulator

from .engines import EngineManager
from .models import Settings


def _load_records(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid benchmark JSON on line {line_number}: {exc}") from exc
        required = ("audio", "text", "language", "license")
        missing = [field for field in required if not str(item.get(field, "")).strip()]
        if missing:
            raise ValueError(
                f"benchmark line {line_number} is missing: {', '.join(missing)}"
            )
        audio = (path.parent / item["audio"]).resolve()
        if not audio.is_file():
            raise FileNotFoundError(f"benchmark audio does not exist: {audio}")
        records.append(
            {
                "audio": str(audio),
                "text": str(item["text"]),
                "language": str(item["language"]),
                "license": str(item["license"]),
            }
        )
    if not records:
        raise ValueError("benchmark manifest is empty")
    return records


def run_benchmark(
    manifest: Path,
    settings: Settings,
    *,
    cache_directory: Path,
    model_directory: Path,
) -> dict[str, Any]:
    records = _load_records(manifest)
    manager = EngineManager(cache_directory, model_directory)
    engine = manager.get(settings)
    accumulator = ErrorRateAccumulator()
    predictions: list[dict[str, Any]] = []
    total_audio = 0.0
    total_elapsed = 0.0
    peak_rss: int | None = None
    try:
        import psutil

        process = psutil.Process()
    except ImportError:
        process = None
    started = time.perf_counter()
    for index, item in enumerate(records):
        task_started = time.perf_counter()
        result = engine.transcribe(Path(item["audio"]), item["language"])
        wall = time.perf_counter() - task_started
        hypothesis = result.text
        accumulator.update(item["text"], hypothesis)
        total_audio += result.audio_seconds
        total_elapsed += wall
        if process:
            peak_rss = max(peak_rss or 0, process.memory_info().rss)
        predictions.append(
            {
                "index": index,
                "audio": item["audio"],
                "language": item["language"],
                "reference": item["text"],
                "hypothesis": hypothesis,
                "audio_seconds": result.audio_seconds,
                "wall_seconds": wall,
                "real_time_factor": (
                    wall / result.audio_seconds if result.audio_seconds > 0 else None
                ),
                "model_load_seconds": result.metadata.get("model_load_seconds"),
            }
        )
    metrics = accumulator.as_dict()
    return {
        "status": "MEASURED",
        "engine": settings.engine,
        "model": settings.model,
        "records": len(records),
        "wer": metrics["wer"],
        "cer": metrics["cer"],
        "audio_seconds": total_audio,
        "elapsed_seconds": time.perf_counter() - started,
        "aggregate_rtf": total_elapsed / total_audio if total_audio > 0 else None,
        "peak_rss_bytes": peak_rss,
        "predictions": predictions,
        "claim": "No production accuracy claim is implied by this measurement.",
    }
