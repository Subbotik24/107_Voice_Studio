from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.recorder import RecorderCleanupError, RecordingResult


class FakeButton:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}

    def configure(self, **kwargs: Any) -> None:
        self.config.update(kwargs)


class FakeStatus:
    def __init__(self) -> None:
        self.values: list[str] = []

    def set(self, value: str) -> None:
        self.values.append(value)


class FakeJobController:
    def __init__(self, result: object = object()) -> None:
        self.result = result
        self.sources: list[Path] = []
        self.closed = False

    def run(self, source: Path, *_args: Any, **_kwargs: Any) -> object:
        self.sources.append(Path(source))
        return self.result

    def close(self) -> None:
        self.closed = True


class FakeRecorder:
    def __init__(self, *, root: Path, result: object | None = None) -> None:
        self.root = root
        self.result = result
        self.recording = False
        self.limit_reached = False
        self.destination: Path | None = None
        self.quarantine_path: Path | None = None
        self.start_directories: list[Path] = []
        self.stop_calls = 0
        self.cancel_calls = 0
        self.cancel_order: list[str] = []
        self.stop_error: BaseException | None = None
        self.cancel_error: BaseException | None = None
        self._next_number = 0

    def start(self, directory: Path) -> Path:
        self.start_directories.append(Path(directory))
        path = Path(directory) / f"capture-{self._next_number}.wav"
        self._next_number += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wav")
        self.destination = path
        self.recording = True
        self.limit_reached = False
        return path

    def stop(self) -> object:
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        self.recording = False
        result = self.result
        if result is None:
            result = SimpleNamespace(
                path=self.destination,
                degraded=False,
                warning="",
                limit_reached=False,
            )
        return result

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancel_order.append("cancel")
        self.recording = False
        if self.cancel_error is not None:
            raise self.cancel_error


def _app(tmp_path: Path, recorder: FakeRecorder) -> VoiceStudioApp:
    app = object.__new__(VoiceStudioApp)
    app.recorder = recorder
    app._pending_microphone_files = set()
    app._active_recording_path = None
    app._ambiguous_microphone_files = set()
    app._recording_residue_diagnostics = []
    app._continuous_recording = False
    app._busy = False
    app.status = FakeStatus()
    app.continuous_record_button = FakeButton()
    app.file_button = FakeButton()
    app.record_button = FakeButton()
    app.settings_button = FakeButton()
    app.models_button = FakeButton()
    app.backup_button = FakeButton()
    app.rename_history_button = FakeButton()
    app.delete_history_button = FakeButton()
    app.cleanup_button = FakeButton()
    app.undo_cleanup_button = FakeButton()
    app.cancel_button = FakeButton()
    app.after_calls: list[tuple[int, object, tuple[object, ...]]] = []
    app.after = lambda delay, callback, *args: app.after_calls.append((delay, callback, args))
    app.events = queue.Queue()
    app.settings = SimpleNamespace(
        engine="faster-whisper",
        offline_only=False,
        dictionary_path=None,
        task_timeout_seconds=5,
    )
    app._cancel_event = threading.Event()
    app.job_controller = FakeJobController()
    app.hotkey = None
    app.current = None
    app._confirm_editor_transition = lambda: True
    app.destroyed = False
    app.destroy = lambda: setattr(app, "destroyed", True)
    app._refresh_history = lambda **_kwargs: None
    app._try_show_result = lambda *_args, **_kwargs: True
    app._copy_to_clipboard = lambda *_args: None
    return app


def _recording_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setattr(app_module, "cache_dir", lambda: cache)
    return cache / "recordings"


def _start_tracked(app: VoiceStudioApp, root: Path) -> Path:
    path = app.recorder.start(root)
    app._pending_microphone_files.add(path)
    app._active_recording_path = path
    return path


def test_record_start_creates_and_tracks_temp_before_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches starting capture without the app capability root or pending ownership."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)

    app._record_start()

    assert recorder.start_directories == [root]
    assert recorder.destination in app._pending_microphone_files
    assert app._active_recording_path == recorder.destination
    assert app.after_calls


def test_record_stop_rejects_result_path_mismatch_without_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches trusting an unverified RecordingResult.path and processing another file."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    expected = _start_tracked(app, root)
    wrong = root / "not-the-active-recording.wav"
    wrong.write_bytes(b"foreign")
    recorder.result = SimpleNamespace(path=wrong, degraded=False, warning="", limit_reached=False)
    recorder.recording = True
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox, "showerror", lambda *args, **kwargs: errors.append(args)
    )

    app._record_stop(force=True)

    assert app.job_controller.sources == []
    assert wrong.exists()
    assert not expected.exists()
    assert errors and "шлях" in str(errors[0]).lower()


def test_degraded_recording_requires_explicit_acceptance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches silently transcribing a recorder result marked degraded."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    path = _start_tracked(app, root)
    recorder.result = SimpleNamespace(
        path=path,
        degraded=True,
        warning="Dropped audio blocks.",
        limit_reached=False,
    )
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    prompts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "askyesno",
        lambda *args, **kwargs: prompts.append(kwargs) or True,
    )

    app._record_stop(force=True)

    assert prompts and prompts[0]["default"] == app_module.messagebox.NO
    assert app.job_controller.sources == [path]


def test_declined_degraded_recording_is_deleted_without_transcription(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches retaining or transcribing a degraded recording after user decline."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    path = _start_tracked(app, root)
    recorder.result = SimpleNamespace(
        path=path,
        degraded=True,
        warning="Dropped audio blocks.",
        limit_reached=False,
    )
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kwargs: False)

    app._record_stop(force=True)

    assert app.job_controller.sources == []
    assert not path.exists()
    assert path not in app._pending_microphone_files


def test_success_error_and_cancel_events_cleanup_pending_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches raw unscoped unlink calls being left in worker event branches."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    paths = [root / f"event-{index}.wav" for index in range(3)]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"temp")
        app._pending_microphone_files.add(path)
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    app.events.put(("done", (object(), paths[0])))
    app.events.put(("error", (RuntimeError("failed"), paths[1])))
    app.events.put(("job_cancelled", paths[2]))

    app._poll_events()

    assert all(not path.exists() for path in paths)
    assert not app._pending_microphone_files


def test_structured_ambiguous_residue_is_preserved_and_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches blind unlink of recorder-owned identity-ambiguous residue."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    path = _start_tracked(app, root)
    residue = root / ".capture-0.wav.recorder-residue"
    residue.write_bytes(b"preserve")
    cleanup = RecorderCleanupError(
        "recorder preserved ambiguous partial entry", residue_paths=(residue,)
    )
    error = RuntimeError("capture failed")
    error.cleanup_error = cleanup  # type: ignore[attr-defined]
    error.residue_paths = cleanup.residue_paths  # type: ignore[attr-defined]
    recorder.stop_error = error
    recorder.quarantine_path = residue
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox, "showerror", lambda *args, **kwargs: errors.append(args)
    )

    app._record_stop(force=True)

    assert not path.exists()
    assert residue.exists()
    assert residue in app._pending_microphone_files
    assert residue in app._ambiguous_microphone_files
    assert errors and "збереж" in str(errors[0]).lower()


def test_duration_limit_stops_only_current_recording_and_reports_two_hours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches stale after callbacks stopping a later session and missing the two-hour notice."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    stale = root / "stale.wav"
    current = root / "current.wav"
    root.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"wav")
    app._pending_microphone_files.add(current)
    app._active_recording_path = current
    recorder.destination = current
    recorder.recording = False
    recorder.limit_reached = True
    recorder.result = RecordingResult(
        path=current,
        frames_written=1,
        dropped_blocks=0,
        status_messages=(),
        limit_reached=True,
        degraded=True,
        warning="Maximum recording duration reached.",
    )
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)
    prompts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "askyesno",
        lambda *args, **kwargs: prompts.append(kwargs) or False,
    )

    app._poll_recording_limit(stale)
    assert recorder.stop_calls == 0

    app._poll_recording_limit(current)
    assert recorder.stop_calls == 1
    assert prompts and prompts[0]["default"] == app_module.messagebox.NO
    assert not current.exists()
    assert any("2" in value and "год" in value for value in app.status.values)
    assert not any("обробляється" in value for value in app.status.values)


@pytest.mark.parametrize(
    ("offline_only", "validation_error", "consent", "expected_message"),
    [
        (True, None, True, "Offline-only"),
        (False, RuntimeError("upload validation failed"), True, "upload validation failed"),
        (False, None, False, "не розпочато"),
    ],
    ids=("offline", "validation-error", "consent-decline"),
)
def test_owned_microphone_preflight_exit_cleans_without_starting_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    offline_only: bool,
    validation_error: Exception | None,
    consent: bool,
    expected_message: str,
) -> None:
    """Catches synchronous cleanup=True exits retaining audio or claiming a job started."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    path = _start_tracked(app, root)
    app.settings.engine = "openai-cloud"
    app.settings.offline_only = offline_only
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    monkeypatch.setattr(
        app_module.messagebox,
        "askyesno",
        lambda *args, **kwargs: consent,
    )
    if validation_error is not None:
        from voice_studio.engines.openai_cloud import OpenAICloudEngine

        monkeypatch.setattr(
            OpenAICloudEngine,
            "validate_upload",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(validation_error),
        )

    app._process(path, cleanup=True)

    assert not path.exists()
    assert path not in app._pending_microphone_files
    assert app.job_controller.sources == []
    visible = " ".join(str(item) for item in (*errors, *app.status.values))
    assert expected_message.lower() in visible.lower()


def test_original_preflight_exit_keeps_cleanup_false_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches applying microphone ownership cleanup to an original source-file flow."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    original = tmp_path / "user-original.wav"
    original.write_bytes(b"original")
    app.settings.engine = "openai-cloud"
    app.settings.offline_only = True
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: None)

    app._process(original, cleanup=False)

    assert original.read_bytes() == b"original"
    assert app.job_controller.sources == []


def test_close_cancels_recorder_then_deletes_pending_temps_but_not_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches close skipping cancellation/ownership cleanup or deleting a user original."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    pending = [root / f"pending-{index}.wav" for index in range(2)]
    for path in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"temp")
        app._pending_microphone_files.add(path)
    original = tmp_path / "user-original.wav"
    original.write_bytes(b"original")

    app._close()
    app._cleanup_temp(original)

    assert recorder.cancel_calls == 1
    assert all(not path.exists() for path in pending)
    assert original.exists()
    assert app.job_controller.closed
    assert app.destroyed


def test_reporting_does_not_suppress_pending_residue_without_ambiguity_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches close/reporting returning silently when only pending remains."""

    root = _recording_root(monkeypatch, tmp_path)
    app = _app(tmp_path, FakeRecorder(root=root))
    retained = root / "retained-after-close.wav"
    app._pending_microphone_files.add(retained)
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )

    app._report_recording_residues()

    assert errors
    assert "тимчасові файли" in str(errors[0]).lower()


def test_cloud_preflight_stat_failure_cleans_tracked_microphone_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches an unguarded prompt-size stat retaining an owned microphone temp."""

    root = _recording_root(monkeypatch, tmp_path)
    recorder = FakeRecorder(root=root)
    app = _app(tmp_path, recorder)
    path = _start_tracked(app, root)
    app.settings.engine = "openai-cloud"
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    from voice_studio.engines.openai_cloud import OpenAICloudEngine

    monkeypatch.setattr(OpenAICloudEngine, "validate_upload", lambda _source: None)
    real_stat = Path.stat

    def fail_source_stat(candidate: Path, *args: Any, **kwargs: Any) -> Any:
        if candidate == path and kwargs.get("follow_symlinks", True):
            raise OSError("source disappeared during cloud preflight")
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_source_stat)

    assert app._process(path, cleanup=True) is False
    assert not os.path.exists(path)
    assert app.job_controller.sources == []
    assert errors and "source disappeared" in str(errors[0]).lower()


def test_cloud_preflight_stat_failure_never_deletes_original_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches applying microphone cleanup ownership to an original source."""

    root = _recording_root(monkeypatch, tmp_path)
    app = _app(tmp_path, FakeRecorder(root=root))
    original = tmp_path / "user-original.wav"
    original.write_bytes(b"original")
    app.settings.engine = "openai-cloud"
    errors: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append(args),
    )
    from voice_studio.engines.openai_cloud import OpenAICloudEngine

    monkeypatch.setattr(OpenAICloudEngine, "validate_upload", lambda _source: None)
    real_stat = Path.stat

    def fail_source_stat(candidate: Path, *args: Any, **kwargs: Any) -> Any:
        if candidate == original and kwargs.get("follow_symlinks", True):
            raise OSError("source disappeared during cloud preflight")
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_source_stat)

    assert app._process(original, cleanup=False) is False
    assert original.read_bytes() == b"original"
    assert app.job_controller.sources == []
    assert errors and "source disappeared" in str(errors[0]).lower()
