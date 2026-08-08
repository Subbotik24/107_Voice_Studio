from .base import EngineResult, SpeechEngine

__all__ = [
    "EngineManager",
    "EngineResult",
    "FasterWhisperEngine",
    "HermesWhisperEngine",
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
    if name == "HermesWhisperEngine":
        from .hermes import HermesWhisperEngine

        return HermesWhisperEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
