"""Minimal subtitle/document consistency after manual edits.

Rule: an edit does not create time. The editable segment layer may change text
and may merge neighbouring segments into their existing outer interval, but it
never creates, interpolates, splits or moves a timestamp. A segment whose
editable text becomes empty disappears together with its interval.
"""

from __future__ import annotations

import difflib

from .models import Segment

_REPLACE_ALL_SIMILARITY_LIMIT = 0.5


def editable_text(segment: Segment) -> str:
    """Return the editable wording of one segment."""

    value = segment.corrected_text if segment.corrected_text is not None else segment.text
    return value.strip()


def document_text_from_segments(segments: list[Segment]) -> str:
    """Derive the corrected document from the editable segment layer."""

    return " ".join(text for text in (editable_text(s) for s in segments) if text)


def _fallback_merge(new_text: str, segments: list[Segment]) -> list[Segment]:
    """Map an unmappable document edit onto the outer existing interval.

    When the stored document cannot be aligned with the segment layer, the only
    honest mapping is the full outer interval; an empty result removes every
    segment. No timestamp is invented here either.
    """

    if not segments:
        return []
    text = new_text.strip()
    if not text:
        return []
    raw = " ".join(s.text.strip() for s in segments if s.text.strip())
    return [
        Segment(
            start=segments[0].start,
            end=segments[-1].end,
            text=raw,
            corrected_text=text,
            language=segments[0].language,
            confidence=None,
        )
    ]


def sync_segments(
    old_text: str, new_text: str, segments: list[Segment]
) -> list[Segment]:
    """Return the segment list after a manual document edit.

    ``old_text`` is the document the editor opened, ``new_text`` the document
    being saved. Surviving segments keep their exact ``start``/``end``; merged
    segments receive only the outer ``[first.start, last.end]`` interval.
    """

    if new_text == old_text:
        return list(segments)
    if not segments:
        return []

    # Align every non-empty editable segment with the old document. Positions
    # are searched in order, so separators between segments are not assumed.
    entries: list[list[int]] = []  # [segment index, span start, span end)
    empty_from_start: list[int] = []
    cursor = 0
    for index, segment in enumerate(segments):
        text = editable_text(segment)
        if not text:
            # This segment's editable text was already empty before this edit
            # (e.g. a filler cleared by AI cleanup). It cannot be searched for
            # in old_text, but its raw wording and interval are not deleted by
            # this edit — they are absorbed into a neighbouring surviving cue
            # below, exactly like a cue whose text becomes empty by editing.
            empty_from_start.append(index)
            continue
        position = old_text.find(text, cursor)
        if position < 0:
            return _fallback_merge(new_text, segments)
        entries.append([index, position, position + len(text)])
        cursor = position + len(text)
    if not entries:
        return _fallback_merge(new_text, segments)

    matcher = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False)
    opcodes = matcher.get_opcodes()

    groups: list[list[int]] = [[k] for k in range(len(entries))]

    def owner_group(position: int) -> list[int] | None:
        for group in groups:
            if entries[group[0]][1] <= position <= entries[group[-1]][2]:
                return group
        return None

    def insertion_owner_group(position: int) -> list[int] | None:
        """Apply the boundary tie-break to the preceding segment group."""

        previous: list[int] | None = None
        for group in groups:
            start = entries[group[0]][1]
            end = entries[group[-1]][2]
            if position <= start:
                return previous if previous is not None else group
            if position <= end:
                return group
            previous = group
        return previous

    def merge_run(first_entry: int, last_entry: int) -> None:
        first_group = next(
            i for i, group in enumerate(groups) if first_entry in group
        )
        last_group = next(i for i, group in enumerate(groups) if last_entry in group)
        if first_group == last_group:
            return
        merged: list[int] = []
        for group in groups[first_group : last_group + 1]:
            merged.extend(group)
        groups[first_group : last_group + 1] = [merged]

    touched_entries: list[int] = []
    crossed_boundary = False
    for tag, i1, i2, _j1, _j2 in opcodes:
        if tag in {"equal", "insert"}:
            continue
        affected = [
            k
            for k, (_index, start, end) in enumerate(entries)
            if start < i2 and i1 < end
        ]
        if not affected:
            # Only separator characters were removed: the neighbours can no
            # longer stay separate without breaking the document join.
            separators = [
                k
                for k in range(len(entries) - 1)
                if i1 <= entries[k][2] < i2
            ]
            if separators:
                affected = list(range(separators[0], separators[-1] + 2))
        touched_entries.extend(affected)
        if len(affected) > 1:
            crossed_boundary = True
            merge_run(affected[0], affected[-1])

    # A replace-all edit can retain short coincidental substrings and separators,
    # making SequenceMatcher report several single-segment opcodes. Treat a
    # full-document change as replace-all only with additional evidence: a real
    # boundary-crossing opcode or low overall similarity. This preserves cues
    # for independent spelling edits in every segment while choosing the only
    # honest outer timing for a substantially new document.
    touched = set(touched_entries)
    replace_all = crossed_boundary or matcher.ratio() < _REPLACE_ALL_SIMILARITY_LIMIT
    if replace_all and touched == set(range(len(entries))) and len(entries) > 1:
        merge_run(0, len(entries) - 1)

    slots: list[tuple[int, list[Segment], str]] = []
    for group in groups:
        group_start = entries[group[0]][1]
        group_end = entries[group[-1]][2]
        pieces: list[str] = []
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                start = max(i1, group_start)
                end = min(i2, group_end)
                if start < end:
                    pieces.append(new_text[j1 + (start - i1) : j1 + (end - i1)])
            elif tag == "insert":
                if insertion_owner_group(i1) is group:
                    # SequenceMatcher may keep the old separator in the equal
                    # block and place an insertion at the following segment's
                    # start. The insertion still belongs to the preceding cue,
                    # so carry that separator into its editable text.
                    pieces.append(old_text[group_end:i1] + new_text[j1:j2])
            elif tag == "replace":
                if owner_group(i1) is group:
                    pieces.append(new_text[j1:j2])
        text = "".join(pieces).strip()
        member_segments = [segments[entries[k][0]] for k in group]
        slots.append((entries[group[0]][0], member_segments, text))
    for index in empty_from_start:
        slots.append((index, [segments[index]], ""))
    slots.sort(key=lambda slot: slot[0])
    candidates: list[tuple[list[Segment], str]] = [
        (member_segments, text) for _order, member_segments, text in slots
    ]

    surviving = [index for index, (_members, text) in enumerate(candidates) if text]
    if not surviving:
        return []

    # A deleted cue loses its editable text and distinct interval, but its raw
    # STT wording remains immutable. Absorb its raw segment into the preceding
    # surviving cue (or the following cue for a leading deletion) so the outer
    # interval remains composed only from existing endpoints.
    mutable_members = [list(members) for members, _text in candidates]
    for index, (_members, text) in enumerate(candidates):
        if text:
            continue
        previous = next((item for item in reversed(surviving) if item < index), None)
        following = next((item for item in surviving if item > index), None)
        target = previous if previous is not None else following
        assert target is not None
        if target < index:
            mutable_members[target].extend(mutable_members[index])
        else:
            mutable_members[target] = mutable_members[index] + mutable_members[target]

    result: list[Segment] = []
    for index in surviving:
        member_segments = mutable_members[index]
        text = candidates[index][1]
        first = member_segments[0]
        last = member_segments[-1]
        raw = " ".join(s.text.strip() for s in member_segments if s.text.strip())
        confidence = first.confidence if len(member_segments) == 1 else None
        result.append(
            Segment(
                start=first.start,
                end=last.end,
                text=raw,
                corrected_text=text,
                language=first.language,
                confidence=confidence,
            )
        )
    return result
