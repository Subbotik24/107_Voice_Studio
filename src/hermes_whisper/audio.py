from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .config import AudioConfig

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - exercised in dependency-light validation
    torch = None
    nn = None


def hz_to_mel(frequency: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(frequency) / 700.0)


def mel_to_hz(mels: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (np.power(10.0, np.asarray(mels) / 2595.0) - 1.0)


def mel_filterbank(config: AudioConfig) -> np.ndarray:
    config.validate()
    maximum = config.max_frequency or config.sample_rate / 2
    mel_points = np.linspace(
        hz_to_mel(config.min_frequency),
        hz_to_mel(maximum),
        config.n_mels + 2,
    )
    hz_points = mel_to_hz(mel_points)
    frequencies = np.linspace(0.0, config.sample_rate / 2, config.n_fft // 2 + 1)
    filters = np.zeros((config.n_mels, len(frequencies)), dtype=np.float32)
    for index in range(config.n_mels):
        left, center, right = hz_points[index : index + 3]
        rising = (frequencies - left) / max(center - left, np.finfo(np.float32).eps)
        falling = (right - frequencies) / max(right - center, np.finfo(np.float32).eps)
        filters[index] = np.maximum(0.0, np.minimum(rising, falling))
    # Slaney-style area normalization reduces frequency-dependent scale drift.
    scale = 2.0 / np.maximum(hz_points[2:] - hz_points[:-2], np.finfo(np.float32).eps)
    filters *= scale[:, None]
    return filters


def load_audio(path: str | Path, target_sample_rate: int = 16_000) -> np.ndarray:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        import soundfile as sf
    except ImportError:
        waveform, sample_rate = _load_pcm_wav(source)
    else:
        waveform, sample_rate = sf.read(source, dtype="float32", always_2d=True)
        waveform = waveform.mean(axis=1, dtype=np.float32)
    if sample_rate != target_sample_rate:
        waveform = resample_linear(waveform, sample_rate, target_sample_rate)
    if waveform.size == 0:
        raise ValueError(f"audio file is empty: {source}")
    if not np.isfinite(waveform).all():
        raise ValueError(f"audio contains NaN or infinity: {source}")
    return np.clip(waveform.astype(np.float32, copy=False), -1.0, 1.0)


def _load_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    if path.suffix.lower() != ".wav":
        raise RuntimeError("soundfile is not installed; only uncompressed PCM WAV can be read")
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
        compression = handle.getcomptype()
    if compression != "NONE":
        raise ValueError("compressed WAV is not supported by the built-in reader")
    if sample_width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        values = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values & 0x800000, values - 0x1000000, values)
        samples = values.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {sample_width}")
    if channels < 1 or samples.size % channels:
        raise ValueError("invalid WAV channel layout")
    samples = samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    return samples, sample_rate


def resample_linear(waveform: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    waveform = np.asarray(waveform, dtype=np.float32)
    if source_rate == target_rate:
        return waveform
    if waveform.size < 2:
        return np.repeat(waveform, max(1, round(waveform.size * target_rate / source_rate)))
    target_length = max(1, round(waveform.size * target_rate / source_rate))
    source_positions = np.arange(waveform.size, dtype=np.float64)
    target_positions = np.linspace(0, waveform.size - 1, target_length, dtype=np.float64)
    return np.interp(target_positions, source_positions, waveform).astype(np.float32)


def pad_or_trim(waveform: np.ndarray, length: int) -> tuple[np.ndarray, int]:
    if length <= 0:
        raise ValueError("length must be positive")
    waveform = np.asarray(waveform, dtype=np.float32)
    valid = min(len(waveform), length)
    if len(waveform) >= length:
        return waveform[:length], valid
    return np.pad(waveform, (0, length - len(waveform))), valid


def log_mel_spectrogram_numpy(waveform: np.ndarray, config: AudioConfig) -> np.ndarray:
    config.validate()
    waveform = np.asarray(waveform, dtype=np.float32)
    if waveform.ndim != 1:
        raise ValueError("waveform must be mono")
    if waveform.size < config.n_fft:
        waveform = np.pad(waveform, (0, config.n_fft - waveform.size))
    frame_count = 1 + (len(waveform) - config.n_fft) // config.hop_length
    shape = (frame_count, config.n_fft)
    strides = (waveform.strides[0] * config.hop_length, waveform.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        waveform, shape=shape, strides=strides, writeable=False
    )
    window = np.hanning(config.win_length + 1)[:-1].astype(np.float32)
    if config.win_length < config.n_fft:
        left = (config.n_fft - config.win_length) // 2
        padded_window = np.zeros(config.n_fft, dtype=np.float32)
        padded_window[left : left + config.win_length] = window
        window = padded_window
    spectrum = np.fft.rfft(frames * window[None, :], n=config.n_fft, axis=-1)
    power = np.square(np.abs(spectrum)).astype(np.float32)
    mel = mel_filterbank(config) @ power.T
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max(initial=-10.0) - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


def feature_frame_count(sample_count: int, config: AudioConfig) -> int:
    sample_count = max(sample_count, config.n_fft)
    return 1 + (sample_count - config.n_fft) // config.hop_length


if torch is not None:

    class LogMelFrontend(nn.Module):
        def __init__(self, config: AudioConfig) -> None:
            super().__init__()
            config.validate()
            self.config = config
            self.register_buffer(
                "window",
                torch.from_numpy(np.hanning(config.win_length + 1)[:-1].astype(np.float32)),
                persistent=False,
            )
            self.register_buffer(
                "filters",
                torch.from_numpy(mel_filterbank(config)),
                persistent=False,
            )

        def forward(self, waveform: Any) -> Any:
            if waveform.ndim == 1:
                waveform = waveform.unsqueeze(0)
            if waveform.ndim != 2:
                raise ValueError("waveform must have shape [batch, samples]")
            if waveform.shape[-1] < self.config.n_fft:
                waveform = torch.nn.functional.pad(
                    waveform, (0, self.config.n_fft - waveform.shape[-1])
                )
            spectrum = torch.stft(
                waveform,
                n_fft=self.config.n_fft,
                hop_length=self.config.hop_length,
                win_length=self.config.win_length,
                window=self.window.to(dtype=waveform.dtype),
                center=False,
                return_complex=True,
            )
            power = spectrum.abs().square()
            mel = torch.matmul(self.filters.to(dtype=power.dtype), power)
            log_spec = torch.log10(torch.clamp(mel, min=1e-10))
            maxima = log_spec.amax(dim=(-2, -1), keepdim=True)
            log_spec = torch.maximum(log_spec, maxima - 8.0)
            return (log_spec + 4.0) / 4.0

else:

    class LogMelFrontend:  # pragma: no cover - only raises without the dependency
        def __init__(self, config: AudioConfig) -> None:
            raise RuntimeError("PyTorch is required for LogMelFrontend")


def seconds_to_frames(seconds: float, config: AudioConfig) -> int:
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError("seconds must be finite and non-negative")
    return feature_frame_count(round(seconds * config.sample_rate), config)
