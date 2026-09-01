from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import Transcript

WORD_PATTERN = re.compile(r"[^\W_]+(?:['’ʼ\-‐‑][^\W_]+)*")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _positive_number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    ):
        return float(value)
    return None


def count_words(text: str) -> int:
    """Count Unicode words, keeping internal apostrophes and hyphens joined."""

    return sum(1 for _ in WORD_PATTERN.finditer(text))


@dataclass(frozen=True)
class HistoryFilter:
    text: str = ""
    created_from: datetime | None = None
    created_to: datetime | None = None
    language: str | None = None
    engine: str | None = None
    model: str | None = None
    status: str | None = None
    retained_audio: bool | None = None

    def matches(self, transcript: Transcript) -> bool:
        needle = self.text.casefold()
        if needle:
            haystack = (
                transcript.source_name,
                transcript.raw_text,
                transcript.corrected_text,
            )
            if not any(
                isinstance(value, str) and needle in value.casefold() for value in haystack
            ):
                return False
        for actual, expected in (
            (transcript.language, self.language),
            (transcript.engine, self.engine),
            (transcript.model, self.model),
            (transcript.status, self.status),
        ):
            if expected is not None and actual != expected:
                return False
        if self.retained_audio is not None and transcript.audio_retained != self.retained_audio:
            return False
        if self.created_from is None and self.created_to is None:
            return True
        created = _parse_timestamp(transcript.created_at)
        if created is None:
            return False
        if self.created_from is not None and created < _as_utc(self.created_from):
            return False
        return not (self.created_to is not None and created > _as_utc(self.created_to))


@dataclass(frozen=True)
class DashboardStatistics:
    total_records: int = 0
    completed_records: int = 0
    failed_records: int = 0
    invalid_records: int = 0
    audio_seconds_total: float = 0.0
    word_count_total: int = 0
    retained_audio_records: int = 0
    records_last_7_days: int = 0
    records_last_30_days: int = 0
    language_counts: tuple[tuple[str, int], ...] = ()
    engine_counts: tuple[tuple[str, int], ...] = ()
    model_counts: tuple[tuple[str, int], ...] = ()
    weighted_real_time_factor: float | None = None
    speed_multiplier: float | None = None


def _ranked(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def aggregate_statistics(
    payloads: Iterable[str], *, now: datetime
) -> DashboardStatistics:
    """Summarise stored transcript payloads without ever raising on bad rows."""

    reference = _as_utc(now)
    cutoff_7 = reference - timedelta(days=7)
    cutoff_30 = reference - timedelta(days=30)
    total = 0
    completed = 0
    failed = 0
    invalid = 0
    audio_seconds_total = 0.0
    word_count_total = 0
    retained = 0
    last_7 = 0
    last_30 = 0
    languages: Counter[str] = Counter()
    engines: Counter[str] = Counter()
    models: Counter[str] = Counter()
    weighted_sum = 0.0
    weight_total = 0.0

    for payload in payloads:
        total += 1
        try:
            transcript = Transcript.from_dict(json.loads(payload))
            status = str(transcript.status)
            language = str(transcript.language)
            engine = str(transcript.engine)
            model = str(transcript.model)
            corrected_text = transcript.corrected_text
            words = count_words(corrected_text) if isinstance(corrected_text, str) else 0
            audio_seconds = _positive_number(transcript.audio_seconds)
            real_time_factor = _positive_number(transcript.real_time_factor)
            audio_retained = transcript.audio_retained is True
            created = _parse_timestamp(transcript.created_at)
        except Exception:
            invalid += 1
            continue

        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        languages[language] += 1
        engines[engine] += 1
        models[model] += 1
        word_count_total += words
        if audio_seconds is not None:
            audio_seconds_total += audio_seconds
            if real_time_factor is not None:
                weighted_sum += audio_seconds * real_time_factor
                weight_total += audio_seconds
        if audio_retained:
            retained += 1
        if created is not None:
            if created >= cutoff_7:
                last_7 += 1
            if created >= cutoff_30:
                last_30 += 1

    weighted_real_time_factor = weighted_sum / weight_total if weight_total > 0 else None
    speed_multiplier = (
        1 / weighted_real_time_factor
        if weighted_real_time_factor is not None and weighted_real_time_factor != 0
        else None
    )
    return DashboardStatistics(
        total_records=total,
        completed_records=completed,
        failed_records=failed,
        invalid_records=invalid,
        audio_seconds_total=audio_seconds_total,
        word_count_total=word_count_total,
        retained_audio_records=retained,
        records_last_7_days=last_7,
        records_last_30_days=last_30,
        language_counts=_ranked(languages),
        engine_counts=_ranked(engines),
        model_counts=_ranked(models),
        weighted_real_time_factor=weighted_real_time_factor,
        speed_multiplier=speed_multiplier,
    )
