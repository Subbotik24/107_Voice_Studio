"""Contracts for the pure confidence-review logic."""

from __future__ import annotations

import math

import pytest

from voice_studio.editor_tools import ConfidenceEntry, confidence_entries
from voice_studio.models import Segment


def segment(confidence: object = None, text: str = "текст") -> Segment:
    item = Segment(start=0.0, end=1.0, text=text)
    item.confidence = confidence
    return item


def test_threshold_outside_the_unit_interval_is_refused() -> None:
    segments = [segment(0.5)]
    for threshold in (-0.01, 1.01, -1.0, 2.0):
        with pytest.raises(ValueError, match="threshold"):
            confidence_entries(segments, threshold)


def test_threshold_bounds_are_accepted() -> None:
    segments = [segment(0.0), segment(0.99)]

    assert confidence_entries(segments, 0.0) == ()
    assert confidence_entries(segments, 1.0) == (
        ConfidenceEntry(0, 0.0),
        ConfidenceEntry(1, 0.99),
    )


def test_boolean_and_non_numeric_thresholds_are_refused() -> None:
    segments = [segment(0.5)]
    for threshold in (True, False, "0.6", None, float("nan")):
        with pytest.raises(ValueError, match="threshold"):
            confidence_entries(segments, threshold)


def test_only_scores_strictly_below_the_threshold_are_listed() -> None:
    segments = [segment(0.59), segment(0.60), segment(0.61)]

    assert confidence_entries(segments, 0.60) == (ConfidenceEntry(0, 0.59),)


def test_entries_are_ordered_by_score_then_by_segment_index() -> None:
    segments = [segment(0.40), segment(0.10), segment(0.40), segment(0.25)]

    assert confidence_entries(segments, 0.60) == (
        ConfidenceEntry(1, 0.10),
        ConfidenceEntry(3, 0.25),
        ConfidenceEntry(0, 0.40),
        ConfidenceEntry(2, 0.40),
    )


def test_unscored_segments_follow_the_scored_ones_in_index_order() -> None:
    segments = [
        segment(None),
        segment(0.30),
        segment(float("nan")),
        segment(0.10),
        segment(float("inf")),
    ]

    entries = confidence_entries(segments, 0.60)

    assert entries == (
        ConfidenceEntry(3, 0.10),
        ConfidenceEntry(1, 0.30),
        ConfidenceEntry(0, None),
        ConfidenceEntry(2, None),
        ConfidenceEntry(4, None),
    )
    assert all(entry.confidence is None for entry in entries[2:])


def test_a_missing_score_is_never_treated_as_zero() -> None:
    entries = confidence_entries([segment(None), segment(0.5)], 0.60)

    assert entries[0] == ConfidenceEntry(1, 0.5)
    assert entries[1] == ConfidenceEntry(0, None)


def test_unscored_segments_are_listed_even_at_a_zero_threshold() -> None:
    assert confidence_entries([segment(0.0), segment(None)], 0.0) == (
        ConfidenceEntry(1, None),
    )


def test_boolean_confidence_counts_as_no_score() -> None:
    assert confidence_entries([segment(True), segment(False)], 0.60) == (
        ConfidenceEntry(0, None),
        ConfidenceEntry(1, None),
    )


def test_wrong_typed_confidence_counts_as_no_score() -> None:
    assert confidence_entries([segment("0.1"), segment(object())], 0.60) == (
        ConfidenceEntry(0, None),
        ConfidenceEntry(1, None),
    )


def test_integer_confidence_is_scored_as_a_float() -> None:
    entries = confidence_entries([segment(0), segment(1)], 0.60)

    assert entries == (ConfidenceEntry(0, 0.0),)
    assert isinstance(entries[0].confidence, float)


def test_negative_and_over_one_scores_are_still_compared_numerically() -> None:
    assert confidence_entries([segment(-0.5), segment(1.5)], 0.60) == (
        ConfidenceEntry(0, -0.5),
    )


def test_no_segments_and_all_segments_above_the_threshold_give_no_entries() -> None:
    assert confidence_entries([], 0.60) == ()
    assert confidence_entries([segment(0.9), segment(0.75)], 0.60) == ()


def test_entries_are_immutable_and_finite() -> None:
    entry = confidence_entries([segment(0.42)], 0.60)[0]

    assert math.isfinite(entry.confidence)
    with pytest.raises(AttributeError):
        entry.index = 5
