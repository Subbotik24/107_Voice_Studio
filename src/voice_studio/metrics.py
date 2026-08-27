from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


def evaluation_normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\s'-]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def edit_distance(reference: Sequence[T], hypothesis: Sequence[T]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            substitution = previous[hyp_index - 1] + (ref_item != hyp_item)
            deletion = previous[hyp_index] + 1
            insertion = current[hyp_index - 1] + 1
            current.append(min(substitution, deletion, insertion))
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_words = evaluation_normalize(reference).split()
    hyp_words = evaluation_normalize(hypothesis).split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return edit_distance(ref_words, hyp_words) / len(ref_words)


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref_chars = list(evaluation_normalize(reference))
    hyp_chars = list(evaluation_normalize(hypothesis))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return edit_distance(ref_chars, hyp_chars) / len(ref_chars)


@dataclass
class ErrorRateAccumulator:
    word_errors: int = 0
    reference_words: int = 0
    character_errors: int = 0
    reference_characters: int = 0
    utterances: int = 0

    def update(self, reference: str, hypothesis: str) -> None:
        ref_normalized = evaluation_normalize(reference)
        hyp_normalized = evaluation_normalize(hypothesis)
        ref_words = ref_normalized.split()
        hyp_words = hyp_normalized.split()
        self.word_errors += edit_distance(ref_words, hyp_words)
        self.reference_words += len(ref_words)
        self.character_errors += edit_distance(list(ref_normalized), list(hyp_normalized))
        self.reference_characters += len(ref_normalized)
        self.utterances += 1

    def as_dict(self) -> dict[str, float | int]:
        return {
            "wer": self.word_errors / max(self.reference_words, 1),
            "cer": self.character_errors / max(self.reference_characters, 1),
            "word_errors": self.word_errors,
            "reference_words": self.reference_words,
            "character_errors": self.character_errors,
            "reference_characters": self.reference_characters,
            "utterances": self.utterances,
        }
