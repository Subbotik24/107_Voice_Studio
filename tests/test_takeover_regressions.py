from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hermes_voice_studio.model_catalog import ModelCatalog
from hermes_voice_studio.models import Transcript
from hermes_voice_studio.storage import LocalStore
from hermes_whisper.checkpoint import resolve_checkpoint, verify_checkpoint

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_checkpoint_verification_requires_the_complete_hash_manifest(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "metadata.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "step": 1,
                "model_name": "unverified",
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash manifest"):
        verify_checkpoint(checkpoint)


def test_latest_checkpoint_cannot_escape_the_run_directory(tmp_path: Path) -> None:
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    (outside / "metadata.json").write_text("{}", encoding="utf-8")
    (run / "latest.json").write_text(
        json.dumps({"checkpoint": "../outside", "step": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inside the run directory"):
        resolve_checkpoint(run)


def test_distinct_managed_paths_with_same_hash_are_cleaned_independently(
    tmp_path: Path,
) -> None:
    store = LocalStore(tmp_path / "data")
    wav = tmp_path / "same.wav"
    mp3 = tmp_path / "same.mp3"
    wav.write_bytes(b"same-content")
    mp3.write_bytes(b"same-content")
    wav_managed, digest = store.import_source(wav)
    mp3_managed, _ = store.import_source(mp3)
    first = Transcript(
        id="1",
        created_at="2026-08-08T00:00:00+00:00",
        source_name="same.wav",
        source_sha256=digest,
        source_path=str(wav_managed),
        language="cs",
        engine="faster-whisper",
        model="small",
        raw_text="raw",
        corrected_text="raw",
    )
    second = Transcript.from_dict(first.to_dict())
    second.id = "2"
    second.source_name = "same.mp3"
    second.source_path = str(mp3_managed)
    store.save(first)
    store.save(second)

    store.delete_audio(first)

    assert not wav_managed.exists()
    assert mp3_managed.exists()


def test_model_import_rejects_a_source_that_contains_the_catalog(tmp_path: Path) -> None:
    catalog = ModelCatalog(tmp_path / "managed")
    (catalog.root / "model.bin").write_bytes(b"fixture model")
    (catalog.root / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot contain"):
        catalog.import_local("recursive", catalog.root)


def test_supported_python_and_training_dependencies_match_runtime_contract() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")

    assert project["requires-python"] == ">=3.11,<3.13"
    assert project["optional-dependencies"]["train"] == [
        "torch>=2.4,<3",
        "soundfile>=0.12,<1",
    ]
    assert ".venv/bin/python is missing" in launcher
