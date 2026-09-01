"""Pure "smart text" rendering: paragraphs from pauses, times and speakers.

The module is read-only over a transcript. ``raw_text`` and every segment are
only ever read here: paragraphs are derived from the editable segment layer
(:func:`voice_studio.subtitles.editable_text`), never written back. No timing is
invented either — a paragraph borrows the ``start`` of its first segment and the
``end`` of its last one, exactly as the engine reported them.

Speaker labels are manual: the caller stores them in
``transcript.metadata["speaker_labels"]`` as ``{"<segment index>": "name"}``.
Nothing here detects, guesses or scores who is speaking.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .models import Segment, Transcript
from .subtitles import editable_text

SPEAKER_LABELS_KEY = "speaker_labels"
MAX_SPEAKER_NAME_LENGTH = 64
MIN_PARAGRAPH_SECONDS = 5.0
MAX_PARAGRAPH_SECONDS = 3600.0
MAX_PARAGRAPH_GAP_SECONDS = 600.0

SpeakerLabels = Mapping[int, str]

_NO_SPEAKERS: SpeakerLabels = MappingProxyType({})
_BLANK_LINE = re.compile(r"\n[^\S\n]*\n")

__all__ = [
    "MAX_SPEAKER_NAME_LENGTH",
    "SPEAKER_LABELS_KEY",
    "Paragraph",
    "SmartTextOptions",
    "SpeakerLabels",
    "build_document_paragraphs",
    "build_paragraphs",
    "format_timestamp",
    "render_markdown",
    "render_plain",
    "speaker_labels_from_metadata",
    "speaker_labels_to_metadata",
]


def _finite_number(value: object) -> float | None:
    """Return ``value`` as a float, or ``None`` when it is not a real number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clean_name(value: object) -> str | None:
    """Normalise one manual speaker label, or return ``None`` when unusable."""

    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:MAX_SPEAKER_NAME_LENGTH].strip()
    return name or None


@dataclass(frozen=True)
class SmartTextOptions:
    """Reader-facing shaping options; every value is validated on creation."""

    paragraph_gap_seconds: float = 2.0
    timestamps: bool = True
    speakers: bool = True
    max_paragraph_seconds: float = 90.0

    def __post_init__(self) -> None:
        gap = _finite_number(self.paragraph_gap_seconds)
        if gap is None or not 0.0 <= gap <= MAX_PARAGRAPH_GAP_SECONDS:
            raise ValueError(
                "paragraph_gap_seconds must be a number between 0.0 and "
                f"{MAX_PARAGRAPH_GAP_SECONDS}"
            )
        limit = _finite_number(self.max_paragraph_seconds)
        if limit is None or not MIN_PARAGRAPH_SECONDS <= limit <= MAX_PARAGRAPH_SECONDS:
            raise ValueError(
                "max_paragraph_seconds must be a number between "
                f"{MIN_PARAGRAPH_SECONDS} and {MAX_PARAGRAPH_SECONDS}"
            )
        for name in ("timestamps", "speakers"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"smart text {name} must be a boolean")
        object.__setattr__(self, "paragraph_gap_seconds", gap)
        object.__setattr__(self, "max_paragraph_seconds", limit)


@dataclass(frozen=True)
class Paragraph:
    """One rendered block: its interval, its speaker and its source segments."""

    start: float
    end: float
    speaker: str | None
    text: str
    segment_indexes: tuple[int, ...]


def format_timestamp(seconds: float) -> str:
    """Render a paragraph start as ``m:ss``, or ``h:mm:ss`` from one hour on."""

    value = _finite_number(seconds)
    if value is None or value < 0:
        value = 0.0
    whole = int(value)
    hours, rest = divmod(whole, 3600)
    minutes, remainder = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02}:{remainder:02}"
    return f"{minutes}:{remainder:02}"


def speaker_labels_from_metadata(metadata: object) -> dict[int, str]:
    """Read manual speaker labels from transcript metadata, tolerantly.

    Unknown shapes, unparsable indexes, non-string or blank names are ignored
    rather than raising: metadata travels through backups and older payloads.
    """

    if not isinstance(metadata, Mapping):
        return {}
    stored = metadata.get(SPEAKER_LABELS_KEY)
    if not isinstance(stored, Mapping):
        return {}
    labels: dict[int, str] = {}
    for key, value in stored.items():
        if isinstance(key, bool):
            continue
        if isinstance(key, int):
            index = key
        elif isinstance(key, str):
            try:
                index = int(key.strip())
            except ValueError:
                continue
        else:
            continue
        if index < 0:
            continue
        name = _clean_name(value)
        if name is not None:
            labels[index] = name
    return labels


def speaker_labels_to_metadata(labels: SpeakerLabels) -> dict[str, str]:
    """Render manual speaker labels for storage in transcript metadata."""

    stored: dict[str, str] = {}
    for key, value in sorted(
        (
            (key, value)
            for key, value in labels.items()
            if isinstance(key, int) and not isinstance(key, bool) and key >= 0
        ),
        key=lambda item: item[0],
    ):
        name = _clean_name(value)
        if name is not None:
            stored[str(key)] = name
    return stored


def build_paragraphs(
    segments: Sequence[Segment],
    options: SmartTextOptions,
    speakers: SpeakerLabels = _NO_SPEAKERS,
) -> tuple[Paragraph, ...]:
    """Group the editable segment layer into paragraphs.

    A new paragraph starts when the pause before a segment reaches
    ``paragraph_gap_seconds``, when the segment carries a speaker label that
    differs from the current one, or when the current paragraph already spans
    more than ``max_paragraph_seconds``. Segments whose editable text is empty
    are skipped, with their interval, exactly like an empty subtitle cue.

    Labels are sparse by nature: an unlabelled segment inherits the speaker
    that is currently running, so a pause never silently drops the speaker.
    Only an explicit, different label changes it.
    """

    paragraphs: list[Paragraph] = []
    indexes: list[int] = []
    texts: list[str] = []
    speaker: str | None = None
    start = 0.0
    end = 0.0

    def flush() -> None:
        if indexes:
            paragraphs.append(
                Paragraph(
                    start=start,
                    end=end,
                    speaker=speaker,
                    text=" ".join(texts),
                    segment_indexes=tuple(indexes),
                )
            )
        indexes.clear()
        texts.clear()

    for index, segment in enumerate(segments):
        text = editable_text(segment)
        if not text:
            continue
        segment_start = _finite_number(segment.start) or 0.0
        segment_end = _finite_number(segment.end) or 0.0
        label = _clean_name(speakers.get(index))
        if indexes:
            gap = segment_start - end
            too_long = end - start > options.max_paragraph_seconds
            different_speaker = label is not None and label != speaker
            if gap >= options.paragraph_gap_seconds or different_speaker or too_long:
                flush()
        if not indexes:
            if label is not None:
                speaker = label
            start = segment_start
            end = segment_end
        indexes.append(index)
        texts.append(text)
        end = max(end, segment_end)
    flush()
    return tuple(paragraphs)


def _document_paragraphs(text: str) -> tuple[Paragraph, ...]:
    """Split a transcript without segments into blank-line separated blocks."""

    blocks = []
    for block in _BLANK_LINE.split(text):
        collapsed = " ".join(block.split())
        if collapsed:
            blocks.append(
                Paragraph(
                    start=0.0,
                    end=0.0,
                    speaker=None,
                    text=collapsed,
                    segment_indexes=(),
                )
            )
    return tuple(blocks)


def build_document_paragraphs(
    transcript: Transcript, options: SmartTextOptions
) -> tuple[Paragraph, ...]:
    """Return the paragraphs of a whole transcript.

    With a segment layer the paragraphs are timed and can carry the manual
    speaker labels stored in the transcript metadata. Without one there is no
    honest timing at all, so the corrected document is only split on blank
    lines and the paragraphs carry no times.
    """

    if transcript.segments:
        return build_paragraphs(
            transcript.segments,
            options,
            speaker_labels_from_metadata(transcript.metadata),
        )
    return _document_paragraphs(transcript.corrected_text)


def _escape_markdown(name: str) -> str:
    """Escape a manual speaker name minimally for a Markdown bold prefix."""

    escaped = name.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")
    if escaped.startswith("#"):
        escaped = "\\" + escaped
    return escaped


def _blocks(
    transcript: Transcript, options: SmartTextOptions, *, markdown: bool
) -> list[str]:
    paragraphs = build_document_paragraphs(transcript, options)
    show_times = options.timestamps and bool(transcript.segments)
    blocks: list[str] = []
    for paragraph in paragraphs:
        pieces: list[str] = []
        if show_times:
            pieces.append(f"[{format_timestamp(paragraph.start)}]")
        if options.speakers and paragraph.speaker:
            name = _escape_markdown(paragraph.speaker) if markdown else paragraph.speaker
            pieces.append(f"**{name}:**" if markdown else f"{name}:")
        pieces.append(paragraph.text)
        blocks.append(" ".join(pieces))
    return blocks


def render_markdown(
    transcript: Transcript, options: SmartTextOptions, *, title: str | None = None
) -> str:
    """Render the transcript as a Markdown document with one heading."""

    heading = title if title is not None else transcript.source_name
    parts = [f"# {heading}"] + _blocks(transcript, options, markdown=True)
    return "\n\n".join(parts) + "\n"


def render_plain(transcript: Transcript, options: SmartTextOptions) -> str:
    """Render the transcript as plain paragraphs, without Markdown or a heading."""

    blocks = _blocks(transcript, options, markdown=False)
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"
