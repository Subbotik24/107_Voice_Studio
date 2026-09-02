"""Human-readable wording for the audio device failures PortAudio reports."""

from __future__ import annotations

_NO_DEVICE_MARKERS = (
    "querying device -1",
    "no default output device",
    "no default input device",
    "invalid device",
    "device unavailable",
)

NO_OUTPUT_DEVICE = "No audio output device is available on this computer."
NO_INPUT_DEVICE = "No microphone (audio input device) is available on this computer."


def is_missing_device_error(exc: BaseException) -> bool:
    """True when ``exc`` is PortAudio saying there is no usable device at all."""

    text = str(exc).lower()
    return any(marker in text for marker in _NO_DEVICE_MARKERS)


def friendly_device_error(exc: BaseException, *, kind: str) -> BaseException:
    """Return ``exc`` unchanged, or a RuntimeError with plain wording.

    ``kind`` is ``"input"`` for recording and ``"output"`` for playback. The
    original exception stays attached as ``__cause__`` for diagnostics.
    """

    if not is_missing_device_error(exc):
        return exc
    message = NO_INPUT_DEVICE if kind == "input" else NO_OUTPUT_DEVICE
    friendly = RuntimeError(message)
    friendly.__cause__ = exc
    return friendly
