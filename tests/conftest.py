import wave
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def make_wav() -> Callable[[Path], Path]:
    def create(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16_000)
            handle.writeframes(b"\0\0" * 1_600)
        return path

    return create
