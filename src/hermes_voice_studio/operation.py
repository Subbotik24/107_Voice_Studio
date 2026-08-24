from __future__ import annotations

import time
from collections.abc import Callable


class JobCancelled(RuntimeError):
    """Raised when a caller explicitly cancels a bounded operation."""


class OperationTimeout(TimeoutError):
    """Raised when a bounded operation reaches its absolute deadline."""


OperationCancelled = JobCancelled


class OperationBudget:
    """One monotonic deadline shared by every phase of an operation."""

    def __init__(
        self,
        timeout_seconds: float | None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._deadline = (
            float("inf")
            if timeout_seconds is None
            else time.monotonic() + float(timeout_seconds)
        )
        self._cancelled = cancelled or (lambda: False)

    @property
    def deadline(self) -> float:
        return self._deadline

    def checkpoint(self, phase: str) -> None:
        """Raise a phase-labelled cancellation or timeout error if needed."""

        if self._cancelled():
            raise JobCancelled(f"operation cancelled during {phase}")
        if time.monotonic() >= self._deadline:
            raise OperationTimeout(f"operation timed out during {phase}")

    def remaining(self, phase: str, ceiling: float | None = None) -> float:
        """Return remaining seconds, optionally capped for a polling wait."""

        if self._cancelled():
            raise JobCancelled(f"operation cancelled during {phase}")
        now = time.monotonic()
        if now >= self._deadline:
            raise OperationTimeout(f"operation timed out during {phase}")
        remaining = self._deadline - now
        if ceiling is not None:
            remaining = min(remaining, max(0.0, float(ceiling)))
        return remaining


__all__ = [
    "JobCancelled",
    "OperationBudget",
    "OperationCancelled",
    "OperationTimeout",
]
