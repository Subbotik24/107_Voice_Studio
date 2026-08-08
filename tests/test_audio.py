import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from hermes_whisper.audio import (
    load_audio,
    log_mel_spectrogram_numpy,
    mel_filterbank,
    resample_linear,
)
from hermes_whisper.config import AudioConfig


class AudioTests(unittest.TestCase):
    def test_filterbank_and_features(self) -> None:
        config = AudioConfig(max_audio_seconds=1.0)
        filters = mel_filterbank(config)
        self.assertEqual(filters.shape, (80, 201))
        waveform = np.sin(2 * np.pi * 440 * np.arange(16_000, dtype=np.float32) / 16_000)
        features = log_mel_spectrogram_numpy(waveform, config)
        self.assertEqual(features.shape, (80, 98))
        self.assertTrue(np.isfinite(features).all())

    def test_pcm_wav_loading_and_resampling(self) -> None:
        samples = (0.1 * np.sin(2 * np.pi * 220 * np.arange(8_000) / 8_000) * 32767).astype("<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8_000)
                handle.writeframes(samples.tobytes())
            loaded = load_audio(path, 16_000)
        self.assertEqual(len(loaded), 16_000)
        self.assertLessEqual(np.abs(loaded).max(), 1.0)
        self.assertEqual(len(resample_linear(np.ones(10, dtype=np.float32), 10, 20)), 20)


if __name__ == "__main__":
    unittest.main()
