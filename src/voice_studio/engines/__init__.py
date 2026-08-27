from .base import EngineResult, SpeechEngine

__all__ = [
    "EngineManager",
    "EngineResult",
    "FasterWhisperEngine",
    "SpeechEngine",
]


def __getattr__(name: str):
    """Load concrete engine runtimes only in the process that needs them."""

    if name == "EngineManager":
        from .registry import EngineManager

        return EngineManager
    if name == "FasterWhisperEngine":
        from .faster_whisper import FasterWhisperEngine

        return FasterWhisperEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
