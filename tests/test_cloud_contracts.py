from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.error import HTTPError

import pytest

from voice_studio.cloud_cleanup import (
    list_ollama_models,
    propose_cleanup,
    validate_cleanup_payload,
)
from voice_studio.engines.openai_cloud import (
    MAX_CLOUD_AUDIO_BYTES,
    SUPPORTED_OPENAI_MEDIA_EXTENSIONS,
    OpenAICloudEngine,
)
from voice_studio.media import SUPPORTED_MEDIA_EXTENSIONS
from voice_studio.model_release import fetch_registry, find_asset, unpack_verified_archive
from voice_studio.models import Segment, Settings, Transcript
from voice_studio.ollama_local import OllamaClient
from voice_studio.storage import LocalStore


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


def test_ollama_cleanup_uses_a_local_model_and_structured_non_streaming_chat() -> None:
    captured: dict[str, object] = {}

    class LocalClient:
        def chat(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return {
                "message": {
                    "content": (
                        '{"corrected_text":"Виправлений текст","segments":'
                        '[{"segment_index":0,"corrected_text":"Виправлений"}],'
                        '"changes":["spelling"]}'
                    )
                }
            }

    proposal = propose_cleanup(
        _transcript(),
        provider="ollama",
        model="gemma4:12b",
        client=LocalClient(),
    )

    assert proposal.corrected_text == "Виправлений текст"
    assert captured["model"] == "gemma4:12b"
    assert captured["stream"] is False
    assert captured["format"]["type"] == "object"
    assert "Поточний текст" in str(captured["messages"])
    assert "Незмінний оригінал" not in str(captured["messages"])


def test_ollama_model_list_is_normalized_without_duplicates() -> None:
    class LocalClient:
        def list_models(self) -> object:
            return {
                "models": [
                    {"name": "gemma4:12b"},
                    {"model": "gemma4-code:latest"},
                    {"name": "gemma4:12b"},
                ]
            }

    assert list_ollama_models(client=LocalClient()) == [
        "gemma4:12b",
        "gemma4-code:latest",
    ]


def test_ollama_http_error_includes_bounded_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(
            "http://127.0.0.1:11434/api/chat",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b'{"error":"Failed to load local model component"}'),
        )

    monkeypatch.setattr("voice_studio.ollama_local._DIRECT_OPENER.open", fail)

    with pytest.raises(RuntimeError, match="Failed to load local model component"):
        OllamaClient().chat(model="broken-local-model")


def test_cleanup_provider_settings_allow_local_ollama() -> None:
    settings = Settings(cleanup_provider="ollama", ollama_model="gemma4:12b")

    settings.validate()

    assert settings.cleanup_provider == "ollama"
    assert settings.ollama_model == "gemma4:12b"


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


def test_model_registry_rejects_invalid_asset_metadata() -> None:
    registry = {
        "models": [
            {
                "id": "tiny",
                "url": "https://example.invalid/tiny.zip",
                "sha256": "z" * 64,
                "archive_bytes": 1,
                "unpacked_bytes": 1,
                "revision": "test",
            }
        ]
    }
    with pytest.raises(ValueError, match="SHA-256"):
        find_asset(registry, "tiny")


def test_cloud_media_extensions_are_a_subset_of_supported_media_extensions() -> None:
    # Any extension the cloud engine accepts must also be a format
    # validate_media_file() accepts locally, or the job passes consent and
    # then fails mid-job in validate_media_file for a "supported" format.
    assert SUPPORTED_OPENAI_MEDIA_EXTENSIONS <= SUPPORTED_MEDIA_EXTENSIONS


def test_fetch_registry_rejects_non_https_url_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("fetch_registry must not open a connection for a rejected URL")

    monkeypatch.setattr(
        "voice_studio.model_release.urllib.request.urlopen", _unexpected_urlopen
    )

    with pytest.raises(ValueError, match="HTTPS"):
        fetch_registry("http://example.invalid/model-registry-v1.json", timeout_seconds=5)


def test_model_archive_requires_exact_declared_unpacked_size(tmp_path: Path) -> None:
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("model.bin", b"a")
        bundle.writestr("config.json", b"{}")
    with pytest.raises(ValueError, match="unpacked size"):
        unpack_verified_archive(archive, tmp_path / "out", expected_size=999)
