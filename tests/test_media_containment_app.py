"""SEC-001: untrusted media must not be parsed in the GUI or CLI process.

End-to-end cases drive the real `spawn` child. Failure paths drive a fake child
through the `_start_probe` seam, because a real hang or crash cannot be produced
on demand — the child re-imports this package, so substituting the probe target
in the parent would never reach it.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import pytest

from hermes_voice_studio import media as media_module
from hermes_voice_studio.media import (
    MediaContainmentError,
    MediaValidationError,
    validate_media_file,
)


def _wav(path: Path, *, seconds: float = 0.1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(16_000 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * frames)
    return path


class _FakeProcess:
    def __init__(self, *, alive: bool) -> None:
        self._alive = alive
        self.terminated = False
        self.killed = False

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:  # pragma: no cover - only if terminate is ignored
        self.killed = True
        self._alive = False

    def join(self, timeout: float | None = None) -> None:
        return None


class _FakeReceiver:
    def __init__(self, *, ready: bool, payload: object = None, eof: bool = False) -> None:
        self._ready = ready
        self._payload = payload
        self._eof = eof
        self.closed = False

    def poll(self, _timeout: float | None = None) -> bool:
        return self._ready

    def recv(self) -> object:
        if self._eof:
            raise EOFError("child closed the pipe")
        return self._payload

    def close(self) -> None:
        self.closed = True


# --- end to end, real contained child ---------------------------------------


def test_a_valid_file_passes_through_the_contained_probe(tmp_path):
    validate_media_file(_wav(tmp_path / "ok.wav"))


def test_the_parent_process_never_imports_the_native_parser(tmp_path):
    """`av` must stay out of the process that owns the Tk loop and the user's files.

    Run in a subprocess so this cannot be satisfied by import order in the test
    session, and assert on the interpreter that actually did the validation.
    """

    source = _wav(tmp_path / "probe.wav")
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from hermes_voice_studio.media import validate_media_file\n"
        f"validate_media_file(Path({str(source)!r}))\n"
        "print('av' in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )

    assert completed.stdout.strip() == "False", completed.stderr


def test_undecodable_media_is_rejected_by_the_child_verdict(tmp_path):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFF____WAVEnot-actually-audio")

    with pytest.raises(MediaValidationError):
        validate_media_file(broken)


# --- parent-side deadline, kill and fail-closed behaviour -------------------


def test_a_wedged_parser_is_bounded_and_killed(tmp_path, monkeypatch):
    """The SEC-001 failure mode: an inline decode with no deadline stops the UI."""

    process = _FakeProcess(alive=True)
    receiver = _FakeReceiver(ready=False)
    monkeypatch.setattr(media_module, "_start_probe", lambda _path: (receiver, process))

    with pytest.raises(MediaValidationError, match="exceeded"):
        validate_media_file(_wav(tmp_path / "slow.wav"), timeout=0.5)

    assert process.terminated, "a wedged probe must be killed, not left running"
    assert receiver.closed


def test_a_probe_that_dies_without_answering_is_reported_not_awaited(tmp_path, monkeypatch):
    process = _FakeProcess(alive=False)
    receiver = _FakeReceiver(ready=True, eof=True)
    monkeypatch.setattr(media_module, "_start_probe", lambda _path: (receiver, process))

    with pytest.raises(MediaValidationError, match="without a result"):
        validate_media_file(_wav(tmp_path / "crash.wav"))

    assert receiver.closed


def test_containment_failure_refuses_rather_than_parsing_in_process(tmp_path, monkeypatch):
    """Fail closed. A fallback to in-process parsing restores the exposure."""

    def _cannot_start(_path: str):
        raise OSError("cannot allocate a pipe")

    monkeypatch.setattr(media_module, "_start_probe", _cannot_start)

    with pytest.raises(MediaContainmentError, match="refusing to parse in-process"):
        validate_media_file(_wav(tmp_path / "contained.wav"))


def test_a_missing_parser_surfaces_as_a_dependency_error(tmp_path, monkeypatch):
    process = _FakeProcess(alive=False)
    receiver = _FakeReceiver(ready=True, payload=("unavailable", "PyAV is required"))
    monkeypatch.setattr(media_module, "_start_probe", lambda _path: (receiver, process))

    with pytest.raises(RuntimeError, match="PyAV is required"):
        validate_media_file(_wav(tmp_path / "nopyav.wav"))


# --- resource ceilings ------------------------------------------------------


def test_oversized_source_is_rejected_before_any_parser_starts(tmp_path, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(media_module, "MAX_SOURCE_BYTES", 16)
    monkeypatch.setattr(
        media_module, "_start_probe", lambda path: started.append(path) or (None, None)
    )

    with pytest.raises(MediaValidationError, match="larger than"):
        validate_media_file(_wav(tmp_path / "huge.wav"))

    assert started == [], "the size ceiling must be enforced before the parser runs"


def test_media_longer_than_the_duration_limit_is_rejected(tmp_path, monkeypatch):
    process = _FakeProcess(alive=False)
    receiver = _FakeReceiver(ready=True, payload=("ok", 7_201.0))
    monkeypatch.setattr(media_module, "_start_probe", lambda _path: (receiver, process))

    with pytest.raises(MediaValidationError, match="longer than"):
        validate_media_file(_wav(tmp_path / "long.wav"))


def test_media_of_unknown_duration_is_accepted(tmp_path, monkeypatch):
    """A container that does not declare a duration must not be rejected outright."""

    process = _FakeProcess(alive=False)
    receiver = _FakeReceiver(ready=True, payload=("ok", None))
    monkeypatch.setattr(media_module, "_start_probe", lambda _path: (receiver, process))

    validate_media_file(_wav(tmp_path / "unknown.wav"))


def test_unsupported_extension_and_empty_file_are_still_rejected(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        validate_media_file(empty)

    odd = tmp_path / "notes.txt"
    odd.write_bytes(b"hello")
    with pytest.raises(ValueError, match="unsupported media extension"):
        validate_media_file(odd)
