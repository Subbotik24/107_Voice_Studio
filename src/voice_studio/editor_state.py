from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_CANONICAL_TK_INDEX = re.compile(r"^[1-9][0-9]*\.(?:0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class EditorSnapshot:
    text: str
    formatting: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


def _is_canonical_tk_index(value: object) -> bool:
    return isinstance(value, str) and _CANONICAL_TK_INDEX.fullmatch(value) is not None


def snapshot_editor(
    text: str, formatting: Mapping[str, Iterable[Sequence[str]]]
) -> EditorSnapshot:
    """Return a stable, immutable representation of the editable transcript."""

    normalized: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for tag in ("bold", "italic"):
        try:
            ranges = formatting.get(tag, ())
        except AttributeError:
            ranges = ()
        valid: list[tuple[str, str]] = []
        try:
            for item in ranges:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                if not _is_canonical_tk_index(item[0]) or not _is_canonical_tk_index(item[1]):
                    continue
                valid.append((item[0], item[1]))
        except TypeError:
            pass
        normalized.append((tag, tuple(sorted(valid))))
    return EditorSnapshot(text=text, formatting=tuple(normalized))
