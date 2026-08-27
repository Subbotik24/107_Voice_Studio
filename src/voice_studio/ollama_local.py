from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MAX_OLLAMA_RESPONSE_BYTES = 16 * 1024**2
MAX_OLLAMA_ERROR_BYTES = 4 * 1024


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

    def chat(self, **payload: object) -> dict[str, Any]:
        return self._request("/api/chat", payload=dict(payload), timeout=240.0)
