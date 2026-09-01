from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import SUPPORTED_PROFILES, Settings


def apply_profile(settings: Settings, profile: str) -> Settings:
    """Return settings with one reusable engine/privacy profile applied."""

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile: {profile}")
    fields: dict[str, object]
    if profile == "ollama-local":
        fields = {
            "engine": "ollama",
            "cleanup_provider": "ollama",
            "automatic_cleanup": True,
            "offline_only": True,
        }
    elif profile == "whisper-local":
        fields = {
            "engine": "faster-whisper",
            "cleanup_provider": "none",
            "automatic_cleanup": False,
            "offline_only": True,
        }
    else:
        fields = {
            "engine": "openai-cloud",
            "cleanup_provider": "openai",
            "automatic_cleanup": False,
            "offline_only": False,
        }
    updated = replace(settings, profile=profile, **fields)
    updated.validate()
    return updated


def discover_ollama_model_catalog(*, client: Any | None = None) -> dict[str, list[str]]:
    """Return every installed Ollama model plus the audio-capable subset."""

    from .cloud_cleanup import list_ollama_models
    from .ollama_local import OllamaClient

    runtime = client or OllamaClient()
    all_models: list[str] = []
    audio_models: list[str] = []
    for model in list_ollama_models(client=runtime):
        all_models.append(model)
        try:
            details = runtime.show_model(model)
        except Exception:
            continue
        capabilities = details.get("capabilities") if isinstance(details, dict) else None
        if isinstance(capabilities, list) and "audio" in capabilities:
            audio_models.append(model)
    return {"audio": audio_models, "all": all_models}


def discover_ollama_audio_models(*, client: Any | None = None) -> list[str]:
    """Return installed Ollama models that explicitly advertise audio input."""

    return discover_ollama_model_catalog(client=client)["audio"]


def with_preferred_ollama_model(settings: Settings, models: list[str]) -> Settings:
    """Fill an empty Ollama selection without replacing a user's stored choice."""

    if settings.ollama_model or not models:
        return settings
    preferred = next(
        (model for model in models if "code" not in model.split(":", 1)[0].casefold()),
        models[0],
    )
    updated = replace(settings, ollama_model=preferred)
    updated.validate()
    return updated
