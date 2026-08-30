from __future__ import annotations

import multiprocessing
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import SUPPORTED_COMPUTE_TYPES

_FALLBACK = ("auto", "default")
_MAX_COMPUTE_TYPES = 32
_MAX_CUDA_DEVICES = 128


@dataclass(frozen=True)
class HardwareDetectionResult:
    status: str
    device_capabilities: tuple[str, ...]
    compute_types: tuple[str, ...]
    fallback: tuple[str, str]
    detail: str

    @property
    def devices(self) -> tuple[str, ...]:
        return self.device_capabilities

    @property
    def supported_compute_types(self) -> tuple[str, ...]:
        return self.compute_types

    @property
    def fallback_device(self) -> str:
        return self.fallback[0]

    @property
    def fallback_compute_type(self) -> str:
        return self.fallback[1]

    @property
    def message(self) -> str:
        return self.detail


def _degraded(detail: str) -> HardwareDetectionResult:
    return HardwareDetectionResult(
        status="degraded",
        device_capabilities=(),
        compute_types=(),
        fallback=_FALLBACK,
        detail=detail[:500],
    )


def _supported_compute_types(ctranslate2: Any, device: str) -> Any:
    getter = ctranslate2.get_supported_compute_types
    try:
        return getter(device)
    except TypeError:
        return getter()


def _probe_worker(results: Any) -> None:
    """Inspect the optional local runtime; this function runs only in spawn."""

    try:
        import ctranslate2

        cuda_devices = int(ctranslate2.get_cuda_device_count())
        if cuda_devices < 0:
            raise RuntimeError("CTranslate2 returned a negative CUDA device count")
        compute_types = set(_supported_compute_types(ctranslate2, "cpu"))
        if cuda_devices:
            compute_types.update(_supported_compute_types(ctranslate2, "cuda"))
        results.put(
            {
                "ok": True,
                "cuda_devices": cuda_devices,
                "compute_types": sorted(compute_types),
            }
        )
    except BaseException as exc:
        results.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _result_from_payload(payload: Any) -> HardwareDetectionResult:
    if not isinstance(payload, dict):
        return _degraded("hardware detection returned an invalid child response")
    if payload.get("ok") is not True:
        detail = str(payload.get("error", "hardware runtime is unavailable"))
        return _degraded(detail)
    cuda_devices = payload.get("cuda_devices")
    compute_types = payload.get("compute_types")
    if (
        type(cuda_devices) is not int
        or not 0 <= cuda_devices <= _MAX_CUDA_DEVICES
        or not isinstance(compute_types, (list, tuple))
        or not 0 < len(compute_types) <= _MAX_COMPUTE_TYPES
        or any(
            type(item) is not str or item not in SUPPORTED_COMPUTE_TYPES
            for item in compute_types
        )
    ):
        return _degraded("hardware detection returned an invalid capability response")
    unique_types = tuple(dict.fromkeys(compute_types))
    if not unique_types:
        return _degraded("hardware detection returned no supported compute types")
    devices = ("cpu", "cuda") if cuda_devices else ("cpu",)
    return HardwareDetectionResult(
        status="ok",
        device_capabilities=devices,
        compute_types=unique_types,
        fallback=_FALLBACK,
        detail=(
            "Local runtime detected: "
            f"{', '.join(devices)}; {len(unique_types)} supported compute type(s)."
        ),
    )


def _cleanup(
    process: Any | None,
    results: Any | None,
    deadline: float,
) -> None:
    if process is not None:
        try:
            alive = bool(process.is_alive())
        except (AssertionError, AttributeError, OSError, ValueError):
            alive = False
        if alive:
            try:
                process.terminate()
            except (AssertionError, AttributeError, OSError, ValueError):
                pass
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.join(timeout=remaining)
            except (AssertionError, AttributeError, OSError, ValueError):
                pass
            try:
                alive = bool(process.is_alive())
            except (AssertionError, AttributeError, OSError, ValueError):
                alive = False
            if alive:
                try:
                    process.kill()
                except (AssertionError, AttributeError, OSError, ValueError):
                    pass
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    process.join(timeout=remaining)
                except (AssertionError, AttributeError, OSError, ValueError):
                    pass
        else:
            try:
                process.join(timeout=max(0.0, min(0.05, deadline - time.monotonic())))
            except (AssertionError, AttributeError, OSError, ValueError):
                pass
    if results is not None:
        try:
            results.cancel_join_thread()
        except (AttributeError, OSError, ValueError):
            pass
        try:
            results.close()
        except (AttributeError, OSError, ValueError):
            pass


def detect_hardware(
    *,
    timeout_seconds: float = 2.0,
    context: Any | None = None,
    worker_target: Callable[[Any], None] | None = None,
) -> HardwareDetectionResult:
    """Return bounded local capability information as an advisory result."""

    timeout = max(0.0, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    results: Any | None = None
    process: Any | None = None
    try:
        ctx = context or multiprocessing.get_context("spawn")
        results = ctx.Queue()
        process = ctx.Process(
            target=worker_target or _probe_worker,
            args=(results,),
            name="voice-studio-hardware-detection",
        )
        process.start()
    except BaseException as exc:
        _cleanup(process, results, deadline)
        return _degraded(f"hardware detection process could not start: {exc}")

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _degraded("hardware detection timed out; using auto/default")
            try:
                payload = results.get(timeout=min(0.05, remaining))
            except queue.Empty:
                try:
                    alive = bool(process.is_alive())
                except (AssertionError, AttributeError, OSError, ValueError):
                    alive = False
                if not alive:
                    return _degraded("hardware detection child exited without a response")
                continue
            return _result_from_payload(payload)
    finally:
        _cleanup(process, results, deadline)


# Explicit alias for callers that prefer the probe terminology.
probe_hardware = detect_hardware
