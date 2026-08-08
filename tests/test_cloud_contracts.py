from __future__ import annotations

from pathlib import Path

import pytest

from hermes_voice_studio.cloud_cleanup import propose_cleanup, validate_cleanup_payload
from hermes_voice_studio.engines.openai_cloud import MAX_CLOUD_AUDIO_BYTES, OpenAICloudEngine
from hermes_voice_studio.models import Segment, Settings, Transcript
from hermes_voice_studio.storage import LocalStore


def _transcript() -> Transcript:
    return Transcript(
        id="cloud-test",
        created_at="2026-01-01T00:00:00+00:00",
        source_name="fixture.wav",
        source_sha256="a" * 64,
        language="uk",
        engine="faster-whisper",
        model="tiny",
        raw_text="Незмінний оригінал",
        corrected_text="Поточний текст",
        segments=[Segment(0, 1, "Незмінний", "Поточний")],
    )


def test_cloud_engine_rejects_large_file_before_client_is_used(tmp_path: Path) -> None:
    audio = tmp_path / "large.wav"
    with audio.open("wb") as stream:
        stream.truncate(MAX_CLOUD_AUDIO_BYTES + 1)
    engine = OpenAICloudEngine("gpt-transcribe", client=object())
    with pytest.raises(ValueError, match="25 MB"):
        engine.transcribe(audio, "uk")


def test_cleanup_sends_corrected_text_not_raw_text() -> None:
    captured: dict[str, object] = {}

    class Responses:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "output_text": (
                        '{"corrected_text":"Виправлений текст","segments":'
                        '[{"segment_index":0,"corrected_text":"Виправлений"}],'
                        '"changes":["spelling"]}'
                    )
                },
            )()

    client = type("Client", (), {"responses": Responses()})()
    proposal = propose_cleanup(_transcript(), model="gpt-4.1-mini-2025-04-14", client=client)
    assert proposal.corrected_text == "Виправлений текст"
    assert "Поточний текст" in str(captured["input"])
    assert "Незмінний оригінал" not in str(captured["input"])
    assert captured["store"] is False


def test_cleanup_rejects_added_or_removed_segments() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_cleanup_payload(
            {"corrected_text": "x", "segments": [], "changes": []}, _transcript()
        )


def test_apply_and_undo_cleanup_preserves_raw_text(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "store")
    original = _transcript()
    store.save(original)
    applied = store.apply_ai_cleanup(
        original.id,
        {
            "corrected_text": "Виправлений текст",
            "segments": [{"segment_index": 0, "corrected_text": "Виправлений"}],
            "changes": ["spelling"],
        },
        provider="openai",
        model="gpt-4.1-mini-2025-04-14",
    )
    assert applied.raw_text == "Незмінний оригінал"
    assert applied.segments[0].text == "Незмінний"
    restored = store.undo_last_ai_cleanup(original.id)
    assert restored.raw_text == "Незмінний оригінал"
    assert restored.corrected_text == "Поточний текст"


def test_offline_only_rejects_cloud_engine() -> None:
    with pytest.raises(ValueError, match="offline_only"):
        Settings(engine="openai-cloud", offline_only=True).validate()
