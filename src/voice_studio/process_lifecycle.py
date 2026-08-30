"""Bounded cleanup helpers for owned worker processes and IPC queues."""

from __future__ import annotations

import weakref
from typing import Any

_DISPOSED_QUEUE_OBJECTS: weakref.WeakSet[Any] = weakref.WeakSet()
_DISPOSED_QUEUE_IDS: set[int] = set()


def _stop_process(process: Any, *, graceful_seconds: float = 0.0) -> None:
    """Stop *process* with bounded graceful, terminate, and kill waits."""

    if process is None:
        return
    try:
        if not process.is_alive():
            return
    except (AssertionError, AttributeError, OSError, ValueError):
        return

    if graceful_seconds > 0:
        try:
            process.join(timeout=graceful_seconds)
        except (AssertionError, AttributeError, OSError, ValueError):
            pass
        try:
            if not process.is_alive():
                return
        except (AssertionError, AttributeError, OSError, ValueError):
            return

    try:
        process.terminate()
    except (AssertionError, AttributeError, OSError, ValueError):
        pass
    try:
        process.join(timeout=5)
    except (AssertionError, AttributeError, OSError, ValueError):
        return
    try:
        if not process.is_alive():
            return
    except (AssertionError, AttributeError, OSError, ValueError):
        return

    try:
        process.kill()
    except (AssertionError, AttributeError, OSError, ValueError):
        pass
    try:
        process.join(timeout=2)
    except (AssertionError, AttributeError, OSError, ValueError):
        return
    # Inspect the final state too: callers must not assume kill/join succeeded.
    try:
        process.is_alive()
    except (AssertionError, AttributeError, OSError, ValueError):
        pass


def _dispose_queue(queue_object: Any | None) -> None:
    """Dispose an owned queue once, without invoking unbounded ``join_thread``."""

    if queue_object is None:
        return
    queue_id = id(queue_object)
    try:
        already_disposed = bool(getattr(queue_object, "_voice_studio_disposed", False))
    except (AttributeError, OSError, ValueError):
        already_disposed = False
    if already_disposed:
        return
    try:
        if queue_object in _DISPOSED_QUEUE_OBJECTS:
            return
    except (TypeError, AttributeError):
        if queue_id in _DISPOSED_QUEUE_IDS:
            return
    try:
        queue_object._voice_studio_disposed = True
    except (AttributeError, OSError, ValueError, TypeError):
        try:
            _DISPOSED_QUEUE_OBJECTS.add(queue_object)
        except (TypeError, AttributeError):
            _DISPOSED_QUEUE_IDS.add(queue_id)
    try:
        queue_object.cancel_join_thread()
    except (AttributeError, OSError, ValueError):
        pass
    try:
        queue_object.close()
    except (AttributeError, OSError, ValueError):
        pass
