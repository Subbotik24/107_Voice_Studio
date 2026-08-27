from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

from .dictionary import TerminologyDictionary
from .engines.base import EngineResult
from .jobs import TranscriptionJobController
from .models import Segment, Settings
from .storage import LocalStore


def _probe_engine_worker(
    requests: Any,
    results: Any,
    cache_directory: str,
    model_directory: str,
) -> None:
    del cache_directory, model_directory
    while True:
        request = requests.get()
        if request is None:
            return
        results.put(
            {
                "job_id": request["job_id"],
                "ok": True,
                "result": EngineResult(
                    engine="runtime-probe",
                    model="spawn-roundtrip",
                    language="en",
                    segments=[
                        Segment(
                            start=0.0,
                            end=0.1,
                            text="VOICE Studio worker probe",
                            language="en",
                            confidence=1.0,
                        )
                    ],
                    audio_seconds=0.1,
                    elapsed_seconds=0.01,
                ),
            }
        )


def _write_probe_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\x00\x00" * 1_600)


def run_frozen_worker_probe(
    root: Path,
    *,
    faster_whisper_model: Path | None = None,
) -> dict[str, object]:
    """Exercise the real spawn/media/store path used by the frozen GUI.

    The default deterministic worker verifies the frozen multiprocessing and
    queue roundtrip without downloading a model during a release build. A
    caller may supply a local faster-whisper directory to extend the same probe
    through actual inference.
    """

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "worker-probe.wav"
    _write_probe_wav(source)
    store = LocalStore(root / "data")
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if faster_whisper_model is None:
        settings = Settings(
            language="en",
            model="runtime-probe",
            device="cpu",
            compute_type="int8",
        )
        controller = TranscriptionJobController(
            store,
            cache,
            worker_target=_probe_engine_worker,
        )
        expected_engine = "runtime-probe"
        expected_text: str | None = "VOICE Studio worker probe"
    else:
        model = faster_whisper_model.expanduser().resolve()
        if not model.is_dir():
            raise FileNotFoundError(f"probe model directory does not exist: {model}")
        settings = Settings(
            language="en",
            model=str(model),
            device="cpu",
            compute_type="int8",
        )
        controller = TranscriptionJobController(store, cache)
        expected_engine = "faster-whisper"
        expected_text = None
    try:
        transcript = controller.run(
            source,
            settings,
            TerminologyDictionary(),
            timeout_seconds=300,
        )
    finally:
        controller.close()

    saved = store.get(transcript.id)
    if transcript.engine != expected_engine or saved is None:
        raise RuntimeError("frozen worker probe returned an invalid transcript")
    if expected_text is not None and transcript.raw_text != expected_text:
        raise RuntimeError("frozen worker probe changed the worker payload")
    return {
        "status": "PASS",
        "engine": transcript.engine,
        "raw_text": transcript.raw_text,
        "saved": True,
    }
