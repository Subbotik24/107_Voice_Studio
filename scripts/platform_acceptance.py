from __future__ import annotations

import argparse
import itertools
import json
import math
import platform
import shutil
import subprocess
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from hermes_voice_studio.dictionary import TerminologyDictionary
from hermes_voice_studio.exporters import export_transcript
from hermes_voice_studio.jobs import TranscriptionJobController
from hermes_voice_studio.models import Settings
from hermes_voice_studio.storage import LocalStore, sha256_file


def evidence_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def write_tone(path: Path) -> None:
    sample_rate = 16_000
    samples = np.arange(sample_rate, dtype=np.float32)
    audio = (0.1 * np.sin(2 * math.pi * 440 * samples / sample_rate) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(audio.tobytes())


def convert_fixtures(root: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for platform acceptance")
    wav = root / "safe.wav"
    write_tone(wav)
    outputs = [wav]
    commands = (
        ("safe.mp3", ["-c:a", "libmp3lame"]),
        ("safe.m4a", ["-c:a", "aac"]),
        ("safe.mp4", ["-c:a", "aac"]),
    )
    for name, codec in commands:
        target = root / name
        process = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav),
                *codec,
                str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg fixture conversion failed: {process.stderr.strip()}")
        outputs.append(target)
    return outputs


def run_acceptance(
    model: str,
    root: Path,
    *,
    tasks: int = 50,
    fixtures: list[Path] | None = None,
    controller_factory: Callable[[LocalStore, Path], Any] = TranscriptionJobController,
) -> dict[str, Any]:
    if tasks < 50:
        raise ValueError("platform acceptance requires at least 50 tasks")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixtures = fixtures or convert_fixtures(root)
    expected_suffixes = {".wav", ".mp3", ".m4a", ".mp4"}
    actual_suffixes = {path.suffix.lower() for path in fixtures}
    if not expected_suffixes.issubset(actual_suffixes):
        raise ValueError("platform acceptance requires WAV, MP3, M4A and MP4 fixtures")
    original_hashes = {str(path.resolve()): sha256_file(path) for path in fixtures}
    store = LocalStore(root / "data")
    controller = controller_factory(store, root / "cache")
    formats = ("txt", "md", "json", "srt", "vtt")
    combinations = itertools.cycle(
        itertools.product(
            fixtures,
            ("uk", "cs", "en", "auto"),
            ("keep", "delete_after_transcription"),
        )
    )
    started = time.perf_counter()
    completed = 0
    task_results: list[dict[str, Any]] = []
    failure: str | None = None
    try:
        for index in range(tasks):
            source, language, retention = next(combinations)
            source_before = sha256_file(source)
            phases: list[dict[str, Any]] = []
            task_started = time.perf_counter()
            transcript = controller.run(
                source,
                Settings(
                    model=model,
                    language=language,
                    retention=retention,
                    task_timeout_seconds=7_200,
                ),
                TerminologyDictionary(),
                progress=lambda phase, elapsed, task_phases=phases: task_phases.append(
                    {"phase": phase, "elapsed_seconds": elapsed}
                ),
            )
            exports: dict[str, dict[str, Any]] = {}
            for fmt in formats:
                output = export_transcript(
                    transcript,
                    fmt,
                    root / "exports" / f"{index:03d}.{fmt}",
                )
                exports[fmt] = {
                    "path": evidence_path(output, root),
                    "size": output.stat().st_size,
                    "sha256": sha256_file(output),
                }
            if not source.exists():
                raise RuntimeError(f"user original was removed: {source}")
            source_after = sha256_file(source)
            if source_after != source_before:
                raise RuntimeError(f"user original was modified: {source}")
            task_results.append(
                {
                    "index": index,
                    "container": source.suffix.lower(),
                    "source": evidence_path(source, root),
                    "source_sha256_before": source_before,
                    "source_sha256_after": source_after,
                    "language_requested": language,
                    "language_detected": transcript.language,
                    "retention": retention,
                    "audio_retained": transcript.audio_retained,
                    "transcript_id": transcript.id,
                    "elapsed_seconds": time.perf_counter() - task_started,
                    "phases": phases,
                    "exports": exports,
                }
            )
            completed += 1
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        controller.close()
    audit = store.audit()
    originals_unchanged = all(
        Path(path).is_file() and sha256_file(Path(path)) == digest
        for path, digest in original_hashes.items()
    )
    passed = (
        failure is None
        and completed == tasks
        and originals_unchanged
        and audit["status"] == "PASS"
    )
    result = {
        "status": "PASS" if passed else "FAIL",
        "tasks": completed,
        "expected_tasks": tasks,
        "crashes": 0 if failure is None else 1,
        "failure": failure,
        "elapsed_seconds": time.perf_counter() - started,
        "formats": [path.suffix for path in fixtures],
        "languages": ["uk", "cs", "en", "auto"],
        "retention": ["keep", "delete_after_transcription"],
        "exports": list(formats),
        "originals_unchanged": originals_unchanged,
        "storage_audit": audit,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "task_results": task_results,
    }
    (root / "acceptance-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise RuntimeError(f"platform acceptance failed: {failure or audit}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="installed local model directory")
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--tasks", type=int, default=50)
    args = parser.parse_args()
    result = run_acceptance(
        args.model,
        args.work_directory,
        tasks=args.tasks,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
