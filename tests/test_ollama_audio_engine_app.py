from __future__ import annotations

import io
import wave

import pytest

from voice_studio.engines.ollama_audio import (
    MAX_OLLAMA_AUDIO_SAMPLES,
    OllamaAudioEngine,
    _bounded_sample_count,
    audio_as_wav,
)
from voice_studio.models import Settings
from voice_studio.ollama_local import OllamaClient


def test_audio_chat_sends_wav_to_the_loopback_openai_compatible_endpoint(monkeypatch):
    client = OllamaClient()
    captured: dict[str, object] = {}
    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a local test.",
                    "reasoning": "private reasoning must never become transcript text",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    def capture(path, *, payload=None, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return response

    monkeypatch.setattr(client, "_request", capture)

    text = client.audio_chat("gemma4:12b", b"RIFFaudio", "Transcribe exactly.")

    assert text == "This is a local test."
    assert captured["path"] == "/v1/chat/completions"
    assert captured["timeout"] == 240.0
    payload = captured["payload"]
    assert payload["model"] == "gemma4:12b"
    assert payload["think"] is False
    assert payload["messages"][0]["content"] == [
        {"type": "text", "text": "Transcribe exactly."},
        {
            "type": "input_audio",
            "input_audio": {"data": "UklGRmF1ZGlv", "format": "wav"},
        },
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   ", "reasoning": "hallucinated"}}]},
    ],
)
def test_audio_chat_rejects_missing_or_empty_assistant_content(monkeypatch, response):
    client = OllamaClient()
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: response)

    with pytest.raises(
        RuntimeError,
        match="returned no transcript.*recognition language.*Local Whisper",
    ):
        client.audio_chat("gemma4:12b", b"RIFFaudio", "Transcribe")


def test_audio_conversion_produces_16khz_mono_pcm_wav(make_wav, tmp_path):
    source = make_wav(tmp_path / "source.wav")

    encoded, duration = audio_as_wav(source)

    with wave.open(io.BytesIO(encoded), "rb") as result:
        assert result.getframerate() == 16_000
        assert result.getnchannels() == 1
        assert result.getsampwidth() == 2
        assert result.getnframes() == 1_600
    assert duration == pytest.approx(0.1, abs=0.001)


def test_ollama_audio_conversion_rejects_over_30_minutes_before_buffer_growth():
    assert _bounded_sample_count(MAX_OLLAMA_AUDIO_SAMPLES - 1, 1) == (
        MAX_OLLAMA_AUDIO_SAMPLES
    )

    with pytest.raises(ValueError, match="30 minutes"):
        _bounded_sample_count(MAX_OLLAMA_AUDIO_SAMPLES, 1)


class FakeOllama:
    def __init__(self, *, capabilities=("completion", "audio"), text="Transcribed text."):
        self.capabilities = capabilities
        self.text = text
        self.prompts: list[str] = []

    def show_model(self, model):
        assert model == "gemma4:12b"
        return {"capabilities": list(self.capabilities)}

    def audio_chat(self, model, wav_bytes, prompt):
        assert model == "gemma4:12b"
        assert wav_bytes == b"RIFFfixture"
        self.prompts.append(prompt)
        return self.text


def test_ollama_engine_returns_one_segment_covering_the_real_duration(tmp_path):
    client = FakeOllama()
    source = tmp_path / "sample.m4a"

    result = OllamaAudioEngine(
        "gemma4:12b",
        client=client,
        converter=lambda path: (b"RIFFfixture", 2.5) if path == source else pytest.fail(),
    ).transcribe(source, "en")

    assert result.engine == "ollama"
    assert result.model == "gemma4:12b"
    assert result.language == "en"
    assert result.text == "Transcribed text."
    assert len(result.segments) == 1
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 2.5
    assert result.segments[0].text == "Transcribed text."
    assert result.metadata == {
        "provider": "ollama",
        "loopback_only": True,
        "timed_segments": False,
        "warning": "Ollama returned plain text without timed segments.",
    }
    assert "English" in client.prompts[0]


def test_ollama_engine_rejects_a_model_without_audio_capability(tmp_path):
    source = tmp_path / "sample.wav"
    engine = OllamaAudioEngine(
        "gemma4:12b",
        client=FakeOllama(capabilities=("completion",)),
        converter=lambda _path: (b"RIFFfixture", 1.0),
    )

    with pytest.raises(RuntimeError, match="does not report audio capability"):
        engine.transcribe(source, "auto")


def test_engine_manager_uses_the_saved_ollama_model(monkeypatch, tmp_path):
    from voice_studio.engines import registry

    created: list[str] = []

    class FakeEngine:
        def __init__(self, model):
            created.append(model)

    monkeypatch.setattr(registry, "OllamaAudioEngine", FakeEngine)
    manager = registry.EngineManager(tmp_path / "cache", tmp_path / "models")

    first = manager.get(Settings(ollama_model="gemma4:12b"))
    second = manager.get(Settings(ollama_model="gemma4:12b"))

    assert first is second
    assert created == ["gemma4:12b"]


def test_loopback_client_bypasses_system_proxy_variables(monkeypatch):
    import http.server
    import json as json_module
    import threading

    from voice_studio import ollama_local

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            payload = json_module.dumps({"models": [{"name": "gemma4:12b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A poisoned proxy environment must not affect the loopback client:
        # with plain urlopen these variables would route 127.0.0.1 through the
        # unreachable proxy and every call would fail.
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setattr(
            ollama_local, "OLLAMA_BASE_URL", f"http://127.0.0.1:{server.server_port}"
        )

        result = ollama_local.OllamaClient().list_models()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result == {"models": [{"name": "gemma4:12b"}]}
