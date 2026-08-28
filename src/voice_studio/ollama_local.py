from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MAX_OLLAMA_RESPONSE_BYTES = 16 * 1024**2
MAX_OLLAMA_ERROR_BYTES = 4 * 1024
MAX_OLLAMA_WAV_BYTES = 64 * 1024**2
EMPTY_TRANSCRIPTION_ERROR = (
    "Ollama returned no transcript. Try recognition language 'auto', verify that the "
    "selected model supports the spoken language, or choose Local Whisper."
)


class OllamaClient:
    """Minimal loopback-only client for an existing local Ollama runtime."""

    def _request(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = Request(
            f"{OLLAMA_BASE_URL}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed loopback URL
                body = response.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            error_body = exc.read(MAX_OLLAMA_ERROR_BYTES + 1)
            detail = ""
            if error_body:
                try:
                    error_payload = json.loads(error_body[:MAX_OLLAMA_ERROR_BYTES])
                    if isinstance(error_payload, dict) and isinstance(
                        error_payload.get("error"), str
                    ):
                        detail = error_payload["error"].strip()
                except (UnicodeDecodeError, json.JSONDecodeError):
                    detail = ""
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"Ollama returned HTTP {exc.code}{suffix}") from exc
        except (OSError, URLError) as exc:
            raise RuntimeError(
                "Local Ollama is unavailable at 127.0.0.1:11434. Start Ollama and retry."
            ) from exc
        if len(body) > MAX_OLLAMA_RESPONSE_BYTES:
            raise RuntimeError("Ollama response exceeded the local safety limit")
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama returned an invalid JSON response") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Ollama returned a non-object response")
        return result

    def list_models(self) -> dict[str, Any]:
        return self._request("/api/tags", timeout=3.0)

    def show_model(self, model: str) -> dict[str, Any]:
        model_name = model.strip()
        if not model_name:
            raise ValueError("Ollama model cannot be empty")
        return self._request("/api/show", payload={"model": model_name}, timeout=10.0)

    def audio_chat(self, model: str, wav_bytes: bytes, prompt: str) -> str:
        model_name = model.strip()
        if not model_name:
            raise ValueError("Ollama model cannot be empty")
        if not wav_bytes or len(wav_bytes) > MAX_OLLAMA_WAV_BYTES:
            raise ValueError(
                f"Ollama WAV input must be between 1 and {MAX_OLLAMA_WAV_BYTES} bytes"
            )
        result = self._request(
            "/v1/chat/completions",
            payload={
                "model": model_name,
                "temperature": 0,
                "max_tokens": 8192,
                "think": False,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": base64.b64encode(wav_bytes).decode("ascii"),
                                    "format": "wav",
                                },
                            },
                        ],
                    }
                ],
            },
            timeout=240.0,
        )
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError(EMPTY_TRANSCRIPTION_ERROR)
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError(EMPTY_TRANSCRIPTION_ERROR)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(EMPTY_TRANSCRIPTION_ERROR)
        return content.strip()

    def chat(self, **payload: object) -> dict[str, Any]:
        return self._request("/api/chat", payload=dict(payload), timeout=240.0)
