"""Reviewable text cleanup through local Ollama or explicit OpenAI use."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .cloud_secrets import get_openai_api_key
from .models import Transcript
from .ollama_local import OllamaClient


@dataclass(frozen=True)
class CleanupProposal:
    corrected_text: str
    segments: list[dict[str, object]]
    changes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "corrected_text": self.corrected_text,
            "segments": self.segments,
            "changes": self.changes,
        }


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "corrected_text": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "segment_index": {"type": "integer", "minimum": 0},
                    "corrected_text": {"type": "string"},
                },
                "required": ["segment_index", "corrected_text"],
            },
        },
        "changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["corrected_text", "segments", "changes"],
}


def _openai_client_or_create(client: Any | None) -> Any:
    if client is not None:
        return client
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "AI cleanup requires the optional 'cloud' dependencies. "
            "Install voice-studio[cloud]."
        ) from exc
    return OpenAI(api_key=get_openai_api_key(), timeout=180.0, max_retries=2)


def _output_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("output_text", ""))
    return str(getattr(response, "output_text", ""))


def _ollama_output_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content", ""))


def list_ollama_models(*, client: Any | None = None) -> list[str]:
    response = (client or OllamaClient()).list_models()
    if not isinstance(response, dict) or not isinstance(response.get("models"), list):
        raise RuntimeError("Ollama returned an invalid model list")
    models: list[str] = []
    for item in response["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip() and name.strip() not in models:
            models.append(name.strip())
    return models


def validate_cleanup_payload(payload: object, transcript: Transcript) -> CleanupProposal:
    if not isinstance(payload, dict):
        raise ValueError("AI cleanup response must be an object")
    corrected_text = payload.get("corrected_text")
    segments = payload.get("segments")
    changes = payload.get("changes")
    if not isinstance(corrected_text, str) or not corrected_text.strip():
        raise ValueError("AI cleanup response has empty corrected_text")
    if not isinstance(segments, list) or not isinstance(changes, list):
        raise ValueError("AI cleanup response has invalid lists")
    expected = list(range(len(transcript.segments)))
    actual: list[int] = []
    normalized: list[dict[str, object]] = []
    for item in segments:
        if not isinstance(item, dict):
            raise ValueError("AI cleanup contains an invalid segment")
        index, text = item.get("segment_index"), item.get("corrected_text")
        if not isinstance(index, int) or not isinstance(text, str) or not text.strip():
            raise ValueError("AI cleanup contains an empty or invalid segment correction")
        actual.append(index)
        normalized.append({"segment_index": index, "corrected_text": text.strip()})
    if actual != expected:
        raise ValueError("AI cleanup must return exactly one correction for every existing segment")
    return CleanupProposal(
        corrected_text=corrected_text.strip(),
        segments=normalized,
        changes=[str(item).strip() for item in changes if str(item).strip()],
    )


def propose_cleanup(
    transcript: Transcript,
    *,
    model: str,
    provider: str = "openai",
    terminology_hints: list[str] | None = None,
    client: Any | None = None,
) -> CleanupProposal:
    """Create a proposal only; this function never changes local storage.

    Deliberately send `corrected_text` rather than immutable `raw_text`.
    """

    source = {
        "corrected_text": transcript.corrected_text,
        "segments": [
            {"segment_index": index, "corrected_text": segment.display_text}
            for index, segment in enumerate(transcript.segments)
        ],
        "terminology_hints": terminology_hints or [],
    }
    instructions = (
        "Correct only punctuation, spelling, grammar, and supplied terminology. "
        "Do not summarize, translate, add facts, remove content, merge segments, or change "
        "the number/order of segments. Return the required JSON object."
    )
    if provider == "ollama":
        response = (client or OllamaClient()).chat(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            ],
            format=_SCHEMA,
            stream=False,
            think=False,
            options={"temperature": 0},
        )
        output = _ollama_output_text(response)
    elif provider == "openai":
        response = _openai_client_or_create(client).responses.create(
            model=model,
            store=False,
            instructions=instructions,
            input=json.dumps(source, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "cleanup",
                    "strict": True,
                    "schema": _SCHEMA,
                }
            },
        )
        output = _output_text(response)
    else:
        raise ValueError(f"unsupported cleanup provider: {provider}")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError("AI cleanup returned invalid structured output") from exc
    return validate_cleanup_payload(payload, transcript)
