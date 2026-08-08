"""Credential resolution for opt-in cloud features.

Credentials deliberately live outside Settings and transcript storage.  The
environment variable is useful for CI/manual CLI use; the OS keychain is the
interactive desktop fallback.
"""

from __future__ import annotations

import os

KEYRING_SERVICE = "org.hermesvoice.studio"
KEYRING_ACCOUNT = "openai-api-key"
OPENAI_KEY_ENV = "OPENAI_API_KEY"


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError(
            "OS keychain support is unavailable. Install the optional 'cloud' dependencies "
            "or configure OPENAI_API_KEY for this session."
        ) from exc
    return keyring


def get_openai_api_key() -> str:
    """Resolve an OpenAI key without exposing it to callers or serialized state."""

    value = os.environ.get(OPENAI_KEY_ENV, "").strip()
    if value:
        return value
    try:
        value = _keyring().get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception as exc:
        raise RuntimeError("OpenAI API key is not configured") from exc
    if value and value.strip():
        return value.strip()
    raise RuntimeError(
        "OpenAI API key is not configured. Set OPENAI_API_KEY or use 'cloud key set'."
    )


def openai_key_status() -> dict[str, object]:
    if os.environ.get(OPENAI_KEY_ENV, "").strip():
        return {"configured": True, "source": "environment"}
    try:
        configured = bool(_keyring().get_password(KEYRING_SERVICE, KEYRING_ACCOUNT))
    except Exception as exc:
        return {"configured": False, "source": "unavailable", "error": type(exc).__name__}
    return {"configured": configured, "source": "keychain" if configured else "none"}


def set_openai_api_key(value: str) -> None:
    key = value.strip()
    if not key:
        raise ValueError("API key cannot be empty")
    _keyring().set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)


def delete_openai_api_key() -> bool:
    try:
        _keyring().delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return True
    except Exception as exc:
        # Keyring uses a backend-specific exception when no credential exists.
        if type(exc).__name__ in {"PasswordDeleteError", "KeyringError"}:
            return False
        raise RuntimeError("Could not delete the OpenAI API key from the OS keychain") from exc
