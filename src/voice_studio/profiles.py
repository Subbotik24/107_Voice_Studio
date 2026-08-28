from __future__ import annotations

from dataclasses import replace

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
