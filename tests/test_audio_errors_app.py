from __future__ import annotations

from voice_studio.app import _plain_error_text
from voice_studio.audio_errors import (
    NO_INPUT_DEVICE,
    NO_OUTPUT_DEVICE,
    friendly_device_error,
    is_missing_device_error,
)


def test_portaudio_missing_device_is_reworded_per_direction() -> None:
    raw = RuntimeError("Error querying device -1")
    assert is_missing_device_error(raw)
    output = friendly_device_error(raw, kind="output")
    assert str(output) == NO_OUTPUT_DEVICE and output.__cause__ is raw
    assert str(friendly_device_error(raw, kind="input")) == NO_INPUT_DEVICE


def test_other_device_errors_are_left_untouched() -> None:
    raw = RuntimeError("Stream is already open")
    assert friendly_device_error(raw, kind="output") is raw


def test_batch_rows_show_the_message_without_the_exception_type() -> None:
    assert _plain_error_text("RuntimeError: Local Ollama is unavailable") == (
        "Local Ollama is unavailable"
    )
    assert _plain_error_text(ValueError("bad input")) == "bad input"
    assert _plain_error_text("no prefix here: kept") == "no prefix here: kept"
    assert _plain_error_text("RuntimeError: ") == "RuntimeError: "
