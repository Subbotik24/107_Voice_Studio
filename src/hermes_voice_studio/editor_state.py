from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class EditorSnapshot:
    text: str
    formatting: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


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
                valid.append((str(item[0]), str(item[1])))
        except TypeError:
            pass
        normalized.append((tag, tuple(sorted(valid))))
    return EditorSnapshot(text=text, formatting=tuple(normalized))
