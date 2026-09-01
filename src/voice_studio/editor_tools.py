"""Pure text logic for the Studio editor: search, replace and filler cleanup.

Every offset here indexes the original document string, so the caller can map a
match straight onto a Tk text widget without re-deriving positions. No undo is
implemented in this module: the bounded manual-edit undo in
``storage.update_editor_state`` already covers edits saved through the editor.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .models import Segment
from .subtitles import editable_text

DEFAULT_FILLERS: dict[str, tuple[str, ...]] = {
    "uk": ("ем", "е-е", "мм"),
    "cs": ("ehm", "hm"),
    "en": ("um", "uh", "erm", "hmm"),
}


@dataclass(frozen=True)
class TextMatch:
    start: int
    end: int


@dataclass(frozen=True)
class FillerMatch:
    start: int
    end: int
    word: str


def _validate_spans(text: str, spans: Sequence[tuple[int, int]]) -> None:
    previous = 0
    for start, end in spans:
        if start > end:
            raise ValueError("match start must not exceed its end")
        if start < 0 or end > len(text):
            raise ValueError("match is outside the text bounds")
        if start < previous:
            raise ValueError("matches must be sorted and non-overlapping")
        previous = end


def find_matches(
    text: str,
    query: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> tuple[TextMatch, ...]:
    """Return the non-overlapping literal matches of ``query`` in ``text``."""

    if not query.strip():
        return ()
    pattern = re.escape(query)
    if whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return tuple(
        TextMatch(match.start(), match.end())
        for match in re.finditer(pattern, text, flags=flags)
    )


def apply_replacements(
    text: str, matches: Sequence[TextMatch], replacement: str
) -> str:
    """Replace every listed span with the literal ``replacement``."""

    _validate_spans(text, [(match.start, match.end) for match in matches])
    if not matches:
        return text
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(text[cursor : match.start])
        pieces.append(replacement)
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def segment_spans(
    document_text: str, segments: Sequence[Segment]
) -> tuple[tuple[int, int] | None, ...]:
    """Map every segment onto its span in the document, aligned sequentially."""

    spans: list[tuple[int, int] | None] = []
    cursor = 0
    for segment in segments:
        text = editable_text(segment)
        if not text:
            spans.append(None)
            continue
        position = document_text.find(text, cursor)
        if position < 0:
            spans.append(None)
            continue
        spans.append((position, position + len(text)))
        cursor = position + len(text)
    return tuple(spans)


def segment_index_for_offset(
    spans: Sequence[tuple[int, int] | None], offset: int
) -> int | None:
    """Return the index of the span containing ``offset``, or ``None``."""

    for index, span in enumerate(spans):
        if span is None:
            continue
        start, end = span
        if start <= offset < end:
            return index
    return None


def _filler_pattern(words: Sequence[str]) -> re.Pattern[str] | None:
    candidates: list[str] = []
    seen: set[str] = set()
    for word in words:
        value = word.strip()
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        candidates.append(value)
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    alternatives = "|".join(re.escape(value) for value in candidates)
    return re.compile(rf"(?<!\w)(?P<word>{alternatives})(?!\w),?", re.IGNORECASE)


def find_filler_matches(
    text: str, language: str, *, fillers: Sequence[str] | None = None
) -> tuple[FillerMatch, ...]:
    """Return whole-word filler spans, including a directly trailing comma."""

    words = DEFAULT_FILLERS.get(language, ()) if fillers is None else fillers
    pattern = _filler_pattern(words)
    if pattern is None:
        return ()
    return tuple(
        FillerMatch(match.start(), match.end(), match.group("word"))
        for match in pattern.finditer(text)
    )


def remove_matches(text: str, matches: Sequence[FillerMatch]) -> str:
    """Remove the listed spans, tidying whitespace only at each removal point."""

    _validate_spans(text, [(match.start, match.end) for match in matches])
    if not matches:
        return text
    out = ""
    cursor = 0
    for match in matches:
        out += text[cursor : match.start]
        end = match.end
        at_line_start = not out or out.endswith("\n")
        at_line_end = end >= len(text) or text[end] == "\n"
        if at_line_start:
            cursor = end + 1 if end < len(text) and text[end] == " " else end
        elif at_line_end:
            if out.endswith(" "):
                out = out[:-1]
            cursor = end
        elif out.endswith(" ") and text[end] == " ":
            cursor = end + 1
        else:
            cursor = end
    return out + text[cursor:]
