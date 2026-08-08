from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


def normalize_text(text: str) -> str:
    """Conservative normalization that preserves case, accents, and punctuation."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return " ".join(text.split()).strip()


@dataclass(frozen=True)
class TimestampedText:
    start: float
    end: float
    text: str


class HermesTokenizer:
    """Deterministic UTF-8 byte BPE tokenizer trained only on supplied text.

    The first 256 tokens always map one-to-one to bytes. Therefore Ukrainian,
    Czech, mixed-language text, names, and unseen symbols remain lossless even
    before any BPE merges are trained.
    """

    FORMAT_VERSION = 1
    CORE_SPECIALS = (
        "<pad>",
        "<bos>",
        "<eos>",
        "<unk>",
        "<transcribe>",
        "<translate>",
        "<no_timestamps>",
    )

    def __init__(
        self,
        token_bytes: Sequence[bytes],
        merges: Sequence[tuple[int, int, int]],
        *,
        languages: Sequence[str] = ("uk", "cs"),
        timestamp_resolution: float = 0.02,
        max_timestamp_seconds: float = 30.0,
    ) -> None:
        if len(token_bytes) < 256:
            raise ValueError("token_bytes must contain the complete 256-byte alphabet")
        expected = [bytes([index]) for index in range(256)]
        if list(token_bytes[:256]) != expected:
            raise ValueError("the first 256 tokens must be the canonical byte alphabet")
        if not languages or len(set(languages)) != len(languages):
            raise ValueError("languages must be non-empty and unique")
        if timestamp_resolution <= 0 or max_timestamp_seconds <= 0:
            raise ValueError("timestamp settings must be positive")

        self.token_bytes = tuple(token_bytes)
        self.merges = tuple(tuple(item) for item in merges)
        self.languages = tuple(languages)
        self.timestamp_resolution = float(timestamp_resolution)
        self.max_timestamp_seconds = float(max_timestamp_seconds)
        self._validate_merges()

        specials = list(self.CORE_SPECIALS)
        specials.extend(f"<lang:{language}>" for language in self.languages)
        timestamp_count = round(self.max_timestamp_seconds / self.timestamp_resolution) + 1
        specials.extend(f"<ts:{index:04d}>" for index in range(timestamp_count))
        self.special_tokens = tuple(specials)
        self._special_to_id = {
            token: len(self.token_bytes) + index for index, token in enumerate(self.special_tokens)
        }

    def _validate_merges(self) -> None:
        available = 256
        for index, merge in enumerate(self.merges):
            if len(merge) != 3:
                raise ValueError(f"merge {index} must have left, right, and result ids")
            left, right, result = merge
            if not (0 <= left < available and 0 <= right < available):
                raise ValueError(f"merge {index} references a token not yet available")
            if result != available:
                raise ValueError("merge result ids must be contiguous and deterministic")
            expected_bytes = self.token_bytes[left] + self.token_bytes[right]
            if result >= len(self.token_bytes) or self.token_bytes[result] != expected_bytes:
                raise ValueError(f"merge {index} result bytes are inconsistent")
            available += 1
        if available != len(self.token_bytes):
            raise ValueError("every token after the byte alphabet must be created by one merge")

    @classmethod
    def byte_level(
        cls,
        *,
        languages: Sequence[str] = ("uk", "cs"),
        timestamp_resolution: float = 0.02,
        max_timestamp_seconds: float = 30.0,
    ) -> HermesTokenizer:
        return cls(
            [bytes([index]) for index in range(256)],
            [],
            languages=languages,
            timestamp_resolution=timestamp_resolution,
            max_timestamp_seconds=max_timestamp_seconds,
        )

    @property
    def text_vocab_size(self) -> int:
        return len(self.token_bytes)

    @property
    def vocab_size(self) -> int:
        return len(self.token_bytes) + len(self.special_tokens)

    @property
    def pad_id(self) -> int:
        return self.special_id("<pad>")

    @property
    def bos_id(self) -> int:
        return self.special_id("<bos>")

    @property
    def eos_id(self) -> int:
        return self.special_id("<eos>")

    @property
    def no_timestamps_id(self) -> int:
        return self.special_id("<no_timestamps>")

    def special_id(self, token: str) -> int:
        try:
            return self._special_to_id[token]
        except KeyError as exc:
            raise KeyError(f"unknown special token: {token}") from exc

    def language_id(self, language: str) -> int:
        if language not in self.languages:
            raise ValueError(f"unsupported language {language!r}; expected {self.languages}")
        return self.special_id(f"<lang:{language}>")

    def timestamp_id(self, seconds: float) -> int:
        if not math.isfinite(seconds):
            raise ValueError("timestamp must be finite")
        clipped = min(max(seconds, 0.0), self.max_timestamp_seconds)
        index = round(clipped / self.timestamp_resolution)
        return self.special_id(f"<ts:{index:04d}>")

    def timestamp_seconds(self, token_id: int) -> float | None:
        offset = token_id - len(self.token_bytes)
        if not 0 <= offset < len(self.special_tokens):
            return None
        token = self.special_tokens[offset]
        if not token.startswith("<ts:"):
            return None
        return float(Decimal(token[4:-1]) * Decimal(str(self.timestamp_resolution)))

    def encode_text(self, text: str, *, normalize: bool = True) -> list[int]:
        if normalize:
            text = normalize_text(text)
        token_ids = list(text.encode("utf-8"))
        for left, right, result in self.merges:
            if len(token_ids) < 2:
                break
            merged: list[int] = []
            index = 0
            while index < len(token_ids):
                if (
                    index + 1 < len(token_ids)
                    and token_ids[index] == left
                    and token_ids[index + 1] == right
                ):
                    merged.append(result)
                    index += 2
                else:
                    merged.append(token_ids[index])
                    index += 1
            token_ids = merged
        return token_ids

    def decode_text(self, token_ids: Iterable[int]) -> str:
        chunks: list[bytes] = []
        for token_id in token_ids:
            if 0 <= token_id < len(self.token_bytes):
                chunks.append(self.token_bytes[token_id])
        return b"".join(chunks).decode("utf-8", errors="replace")

    def encode_transcript(
        self,
        text: str,
        *,
        language: str,
        task: str = "transcribe",
        segments: Sequence[TimestampedText] | None = None,
    ) -> list[int]:
        if task not in {"transcribe", "translate"}:
            raise ValueError("task must be transcribe or translate")
        sequence = [
            self.bos_id,
            self.language_id(language),
            self.special_id(f"<{task}>"),
        ]
        if segments:
            previous_end = 0.0
            for segment in segments:
                if segment.start < previous_end or segment.end < segment.start:
                    raise ValueError("timestamped segments must be ordered and non-overlapping")
                sequence.append(self.timestamp_id(segment.start))
                sequence.extend(self.encode_text(segment.text))
                sequence.append(self.timestamp_id(segment.end))
                previous_end = segment.end
        else:
            sequence.append(self.no_timestamps_id)
            sequence.extend(self.encode_text(text))
        sequence.append(self.eos_id)
        return sequence

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": self.FORMAT_VERSION,
            "type": "utf8-byte-bpe",
            "languages": list(self.languages),
            "timestamp_resolution": self.timestamp_resolution,
            "max_timestamp_seconds": self.max_timestamp_seconds,
            "tokens_hex": [token.hex() for token in self.token_bytes],
            "merges": [list(merge) for merge in self.merges],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> HermesTokenizer:
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid tokenizer JSON {source}: {exc}") from exc
        if payload.get("format_version") != cls.FORMAT_VERSION:
            raise ValueError("unsupported tokenizer format version")
        if payload.get("type") != "utf8-byte-bpe":
            raise ValueError("unsupported tokenizer type")
        return cls(
            [bytes.fromhex(item) for item in payload["tokens_hex"]],
            [tuple(item) for item in payload["merges"]],
            languages=payload["languages"],
            timestamp_resolution=payload["timestamp_resolution"],
            max_timestamp_seconds=payload["max_timestamp_seconds"],
        )

    @classmethod
    def train(
        cls,
        texts: Iterable[str],
        *,
        target_text_vocab_size: int,
        min_pair_frequency: int = 2,
        languages: Sequence[str] = ("uk", "cs"),
        timestamp_resolution: float = 0.02,
        max_timestamp_seconds: float = 30.0,
    ) -> HermesTokenizer:
        if target_text_vocab_size < 256:
            raise ValueError("target_text_vocab_size cannot be below 256")
        if min_pair_frequency < 1:
            raise ValueError("min_pair_frequency must be positive")

        sequences = [
            list(normalized.encode("utf-8"))
            for text in texts
            if (normalized := normalize_text(text))
        ]
        if not sequences:
            raise ValueError("tokenizer training corpus is empty")

        token_bytes: list[bytes] = [bytes([index]) for index in range(256)]
        merges: list[tuple[int, int, int]] = []
        while len(token_bytes) < target_text_vocab_size:
            pair_counts: Counter[tuple[int, int]] = Counter()
            for sequence in sequences:
                pair_counts.update(zip(sequence, sequence[1:], strict=False))
            if not pair_counts:
                break
            (left, right), frequency = min(
                pair_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
            if frequency < min_pair_frequency:
                break
            result = len(token_bytes)
            token_bytes.append(token_bytes[left] + token_bytes[right])
            merges.append((left, right, result))
            sequences = [_replace_pair(sequence, left, right, result) for sequence in sequences]

        return cls(
            token_bytes,
            merges,
            languages=languages,
            timestamp_resolution=timestamp_resolution,
            max_timestamp_seconds=max_timestamp_seconds,
        )

    @classmethod
    def train_from_files(
        cls,
        paths: Sequence[str | Path],
        **kwargs: object,
    ) -> HermesTokenizer:
        return cls.train(_iter_text_files(paths), **kwargs)


def _replace_pair(sequence: Sequence[int], left: int, right: int, result: int) -> list[int]:
    output: list[int] = []
    index = 0
    while index < len(sequence):
        if index + 1 < len(sequence) and sequence[index] == left and sequence[index + 1] == right:
            output.append(result)
            index += 2
        else:
            output.append(sequence[index])
            index += 1
    return output


def _iter_text_files(paths: Sequence[str | Path]) -> Iterator[str]:
    for value in paths:
        path = Path(value)
        with path.open("r", encoding="utf-8") as handle:
            yield from handle
