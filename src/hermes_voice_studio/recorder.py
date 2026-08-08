from __future__ import annotations

import queue
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np


class AudioRecorder:
    def __init__(self, sample_rate: int = 16_000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any | None = None
        self._recording = threading.Event()

    @property
    def recording(self) -> bool:
        return self._recording.is_set()

    def start(self) -> None:
        if self.recording:
            return
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("sounddevice is not installed") from exc
        while not self._frames.empty():
            self._frames.get_nowait()

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info, status
            if self.recording:
                self._frames.put(indata.copy())

        self._recording.set()
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=callback,
            )
            self._stream.start()
        except BaseException:
            self._recording.clear()
            self._stream = None
            raise

    def stop(self, destination: Path) -> Path:
        if not self.recording:
            raise RuntimeError("recording is not active")
        self._recording.clear()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        chunks: list[np.ndarray] = []
        while not self._frames.empty():
            chunks.append(self._frames.get_nowait())
        if not chunks:
            raise RuntimeError("no audio was captured")
        audio = np.concatenate(chunks, axis=0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(audio.tobytes())
        return destination

    def cancel(self) -> None:
        self._recording.clear()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        while not self._frames.empty():
            self._frames.get_nowait()
