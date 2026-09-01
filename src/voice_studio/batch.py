"""Pure model for the batch transcription queue.

The queue holds an ordered, immutable list of media files the user wants
transcribed. It owns no threads, no timers and no engine: exactly one
transcription job runs at a time (see ``jobs.py``), and the GUI drives the
queue by asking for :meth:`BatchQueue.next_pending` and reporting the outcome
of each job back through the ``mark_*`` transitions.

Everything here is deterministic and side-effect free apart from the path
inspection that ``add_paths`` and ``add_folder`` need (``resolve``, ``is_file``
and ``iterdir``). Nothing in this module reads, moves or deletes a user file.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .media import SUPPORTED_MEDIA_EXTENSIONS

BatchStatus = Literal["pending", "running", "done", "failed", "skipped"]

# A queue is a convenience, not a job system: bound it so a stray "add folder"
# on a media library cannot build an unbounded in-memory list or a GUI list box
# the user can no longer manage.
MAX_BATCH_ITEMS = 500

# Engine errors can carry a whole traceback or a decoder's dump of the input.
# The queue keeps a short, displayable reason instead.
MAX_BATCH_ERROR_CHARS = 500

FINISHED_STATUSES: frozenset[str] = frozenset({"done", "failed", "skipped"})

# The only legal moves. Anything else is a bug in the caller's pump loop, so it
# raises instead of silently rewriting a finished item.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "skipped"}),
    "running": frozenset({"done", "failed"}),
    "done": frozenset(),
    "failed": frozenset(),
    "skipped": frozenset(),
}


@dataclass(frozen=True)
class BatchItem:
    path: Path
    status: BatchStatus = "pending"
    transcript_id: str | None = None
    error: str | None = None
    seconds: float = 0.0


@dataclass(frozen=True)
class BatchSummary:
    total: int
    done: int
    failed: int
    skipped: int
    pending: int
    running: int
    seconds: float


def _normalize(path: Path) -> Path:
    """Return the absolute, expanded, symlink-free spelling of ``path``.

    Resolving is what makes deduplication meaningful: the same file reached
    through a relative path, ``~`` or a symlink must be one queue entry, not
    three transcription jobs of the same audio.
    """

    return Path(path).expanduser().resolve()


def _coerce_seconds(value: float) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"job seconds must be a finite, non-negative number: {value!r}")
    return seconds


def _bound_error(error: str) -> str:
    return str(error)[:MAX_BATCH_ERROR_CHARS]


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS


def _scan_folder(folder: Path, *, recursive: bool) -> list[Path]:
    """List supported media under ``folder`` in a stable, sorted order.

    Symlinked sub-folders are never descended into: a link back to an ancestor
    would otherwise walk forever.
    """

    found: list[Path] = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            if recursive and not entry.is_symlink():
                found.extend(_scan_folder(entry, recursive=True))
            continue
        if _is_supported(entry):
            found.append(entry)
    return found


class BatchQueue:
    """An ordered queue of media files awaiting transcription."""

    def __init__(self) -> None:
        self._items: list[BatchItem] = []
        self._paused = False

    # -- inspection ---------------------------------------------------------

    @property
    def items(self) -> tuple[BatchItem, ...]:
        return tuple(self._items)

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def running(self) -> BatchItem | None:
        for item in self._items:
            if item.status == "running":
                return item
        return None

    def next_pending(self) -> BatchItem | None:
        """Return the first pending item without changing any state."""

        for item in self._items:
            if item.status == "pending":
                return item
        return None

    def summary(self) -> BatchSummary:
        counts: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
        }
        for item in self._items:
            counts[item.status] += 1
        return BatchSummary(
            total=len(self._items),
            done=counts["done"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            pending=counts["pending"],
            running=counts["running"],
            seconds=math.fsum(item.seconds for item in self._items),
        )

    # -- adding -------------------------------------------------------------

    def add_paths(self, paths: Iterable[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
        """Queue every acceptable path and report the rest.

        Returns ``(added, rejected)`` where ``added`` holds the resolved paths
        now in the queue and ``rejected`` pairs each refused path with a short,
        displayable reason.
        """

        added: list[Path] = []
        rejected: list[tuple[Path, str]] = []
        for raw in paths:
            try:
                candidate = _normalize(raw)
            except OSError as exc:
                rejected.append((Path(raw), f"cannot be resolved: {exc}"))
                continue
            # A symlink is only accepted once it has resolved to a regular
            # file; this also refuses folders, devices and missing paths.
            # Existence is checked before the suffix so that a link to a folder
            # is reported as what it is, not as a missing extension.
            try:
                is_file = candidate.is_file()
            except OSError as exc:
                rejected.append((candidate, f"cannot be inspected: {exc}"))
                continue
            if not is_file:
                rejected.append((candidate, "not a regular file"))
                continue
            if not _is_supported(candidate):
                rejected.append(
                    (candidate, f"unsupported media type: {candidate.suffix or '<none>'}")
                )
                continue
            if self._find(candidate) is not None:
                rejected.append((candidate, "already queued"))
                continue
            if len(self._items) >= MAX_BATCH_ITEMS:
                rejected.append((candidate, f"the queue is full: at most {MAX_BATCH_ITEMS} items"))
                continue
            self._items.append(BatchItem(path=candidate))
            added.append(candidate)
        return added, rejected

    def add_folder(
        self, folder: Path, *, recursive: bool = False
    ) -> tuple[list[Path], list[tuple[Path, str]]]:
        """Queue the supported media in ``folder``, sorted and deterministic.

        Unsupported files in the folder are ignored rather than reported: the
        user asked for a folder, not for each of its documents.
        """

        try:
            root = _normalize(folder)
        except OSError as exc:
            return [], [(Path(folder), f"cannot be resolved: {exc}")]
        try:
            if not root.is_dir():
                return [], [(root, "not a folder")]
            candidates = _scan_folder(root, recursive=recursive)
        except OSError as exc:
            return [], [(root, f"cannot be listed: {exc}")]
        return self.add_paths(candidates)

    # -- transitions --------------------------------------------------------

    def mark_running(self, path: Path) -> BatchItem:
        current = self.running()
        if current is not None:
            raise ValueError(f"another batch item is already running: {current.path}")
        return self._transition(path, "running")

    def mark_done(self, path: Path, transcript_id: str, seconds: float) -> BatchItem:
        if not isinstance(transcript_id, str) or not transcript_id.strip():
            raise ValueError(f"a finished batch item needs a transcript id: {transcript_id!r}")
        return self._transition(
            path,
            "done",
            transcript_id=transcript_id,
            error=None,
            seconds=_coerce_seconds(seconds),
        )

    def mark_failed(self, path: Path, error: str, seconds: float) -> BatchItem:
        return self._transition(
            path,
            "failed",
            error=_bound_error(error),
            seconds=_coerce_seconds(seconds),
        )

    def mark_skipped(self, path: Path) -> BatchItem:
        return self._transition(path, "skipped")

    # -- removal ------------------------------------------------------------

    def remove(self, path: Path) -> bool:
        """Drop one item. Returns ``False`` when it was not queued."""

        index = self._find(path)
        if index is None:
            return False
        item = self._items[index]
        if item.status == "running":
            raise ValueError(f"cannot remove the running batch item: {item.path}")
        del self._items[index]
        return True

    def clear_finished(self) -> int:
        """Drop every done, failed or skipped item and return how many went."""

        kept = [item for item in self._items if item.status not in FINISHED_STATUSES]
        removed = len(self._items) - len(kept)
        self._items = kept
        return removed

    def clear(self) -> None:
        current = self.running()
        if current is not None:
            raise ValueError(f"cannot clear the queue while an item is running: {current.path}")
        self._items.clear()

    # -- internals ----------------------------------------------------------

    def _find(self, path: Path) -> int | None:
        target = Path(path)
        for index, item in enumerate(self._items):
            if item.path == target:
                return index
        try:
            resolved = _normalize(target)
        except OSError:
            return None
        if resolved == target:
            return None
        for index, item in enumerate(self._items):
            if item.path == resolved:
                return index
        return None

    def _transition(self, path: Path, status: BatchStatus, **fields: object) -> BatchItem:
        index = self._find(path)
        if index is None:
            raise KeyError(f"path is not in the batch queue: {path}")
        current = self._items[index]
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"cannot move {current.path} from {current.status} to {status}")
        updated = replace(current, status=status, **fields)
        self._items[index] = updated
        return updated
