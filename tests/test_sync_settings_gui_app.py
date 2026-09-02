"""Contracts for the Studio-side wiring of the local "Sync folder" feature.

The pure mirroring/validation module is covered end to end in
``tests/test_sync_folder_app.py``. What is asserted here is the app side: the
Settings page section round-trips its three fields into ``Settings`` on Save
and joins the ordinary unsaved-changes guard, an invalid folder is refused
before anything is persisted, the quiet auto-mirror helper is a no-op when
disabled, writes the mirror files when enabled, and swallows any failure onto
the status bar instead of raising; the editor-save and speaker-assignment
hooks call it; and "Sync all now" mirrors every stored transcript on a worker
thread and reports the resulting summary through a ``sync_done`` event.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_studio import app as app_module
from voice_studio.app import VoiceStudioApp
from voice_studio.models import Segment, Settings, Transcript
from voice_studio.storage import LocalStore
from voice_studio.sync_folder import SyncFolderError, SyncSummary


def _transcript(transcript_id: str = "t-1", **overrides: Any) -> Transcript:
    fields: dict[str, Any] = dict(
        id=transcript_id,
        created_at="2026-01-01T00:00:00+00:00",
        source_name="voice.wav",
        source_sha256="a" * 64,
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="raw text",
        corrected_text="corrected text",
    )
    fields.update(overrides)
    return Transcript(**fields)


class FakeEditor:
    """The editor text widget, reduced to what ``_save_edits`` reads."""

    def __init__(self, text: str) -> None:
        self.text = text

    def get(self, _start: str, _end: str) -> str:
        return self.text


# --- Settings page: round trip + dirty guard ---------------------------------


def test_sync_settings_round_trip_into_settings_on_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches Save dropping sync_enabled / sync_folder / sync_include_audio."""

    app = object.__new__(VoiceStudioApp)
    app.settings = Settings()
    app._dictionary_dirty = False
    app._refresh_after_settings_save = lambda _language: None
    saved: list[Settings] = []
    monkeypatch.setattr(app_module, "save_settings", lambda settings: saved.append(settings))

    sync_root = tmp_path / "mirror"
    sync_root.mkdir()
    updated = replace(
        app.settings,
        sync_enabled=True,
        sync_folder=str(sync_root),
        sync_include_audio=True,
    )

    assert VoiceStudioApp._apply_settings_update(app, updated) is True
    assert app.settings.sync_enabled is True
    assert app.settings.sync_folder == str(sync_root)
    assert app.settings.sync_include_audio is True
    assert saved == [app.settings]


def test_the_unsaved_guard_reacts_to_the_sync_fields_like_any_other_setting() -> None:
    """Catches the three sync variables being left out of the dirty guard."""

    app = object.__new__(VoiceStudioApp)
    app._settings_baseline = {
        "sync_enabled": False,
        "sync_folder": "",
        "sync_include_audio": False,
    }
    app._settings_variables = {
        "sync_enabled": SimpleNamespace(get=lambda: False),
        "sync_folder": SimpleNamespace(get=lambda: ""),
        "sync_include_audio": SimpleNamespace(get=lambda: False),
    }

    assert VoiceStudioApp._settings_page_is_dirty(app) is False

    app._settings_variables["sync_folder"] = SimpleNamespace(get=lambda: "/some/folder")

    assert VoiceStudioApp._settings_page_is_dirty(app) is True


# --- Save-time validation -----------------------------------------------------


def test_a_sync_folder_that_is_a_file_is_refused_before_saving(tmp_path: Path) -> None:
    """Catches Save accepting a mirror target that is not a real directory."""

    app = object.__new__(VoiceStudioApp)
    not_a_directory = tmp_path / "mirror.txt"
    not_a_directory.write_text("x", encoding="utf-8")
    invalid = Settings(sync_enabled=True, sync_folder=str(not_a_directory))

    with pytest.raises(SyncFolderError):
        VoiceStudioApp._validate_settings_for_save(app, invalid)


def test_a_sync_folder_inside_the_app_data_folder_is_refused_before_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches Save accepting a mirror target nested inside the private data root."""

    data_root = tmp_path / "data"
    (data_root / "inside").mkdir(parents=True)
    monkeypatch.setattr(app_module, "data_dir", lambda: data_root)
    app = object.__new__(VoiceStudioApp)
    invalid = Settings(sync_enabled=True, sync_folder=str(data_root / "inside"))

    with pytest.raises(SyncFolderError):
        VoiceStudioApp._validate_settings_for_save(app, invalid)


def test_a_disabled_or_valid_sync_configuration_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "data_dir", lambda: tmp_path / "data")
    app = object.__new__(VoiceStudioApp)

    VoiceStudioApp._validate_settings_for_save(app, Settings())  # disabled: no folder check

    valid_root = tmp_path / "mirror"
    valid_root.mkdir()
    VoiceStudioApp._validate_settings_for_save(
        app, Settings(sync_enabled=True, sync_folder=str(valid_root))
    )


def test_save_persists_the_resolved_absolute_sync_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``~/...`` or relative entry must not be stored as typed (audit F-2)."""

    monkeypatch.setattr(app_module, "data_dir", lambda: tmp_path / "data")
    home = tmp_path / "home"
    (home / "Drive").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    app = object.__new__(VoiceStudioApp)

    updated = Settings(sync_enabled=True, sync_folder="~/Drive")
    VoiceStudioApp._validate_settings_for_save(app, updated)

    assert updated.sync_folder == str((home / "Drive").resolve())
    assert "~" not in updated.sync_folder


def test_mirror_quietly_revalidates_the_root_against_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings pointing inside the data root (hand-edited/restored) never mirror (F-1)."""

    data_root = tmp_path / "data"
    sources = data_root / "sources"
    sources.mkdir(parents=True)
    monkeypatch.setattr(app_module, "data_dir", lambda: data_root)
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=True, sync_folder=str(sources))
    app.store = SimpleNamespace(sources=sources)
    messages: list[str] = []
    app.status = SimpleNamespace(set=messages.append)
    app._t = lambda key, **kw: f"{key}:{kw.get('error', '')}"
    item = Transcript(
        id="abcdef1234567890",
        created_at="2026-09-02T10:00:00",
        source_name="rec.wav",
        source_sha256="0" * 64,
        language="uk",
        engine="ollama",
        model="m",
        raw_text="raw",
        corrected_text="corr",
    )

    VoiceStudioApp._mirror_transcript_quietly(app, item)

    assert list(sources.iterdir()) == []
    assert messages and messages[-1].startswith("sync_failed:")


# --- _mirror_transcript_quietly ----------------------------------------------


def test_mirror_quietly_is_a_noop_when_sync_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=False, sync_folder="")
    calls: list[object] = []
    monkeypatch.setattr(app_module, "mirror_transcript", lambda *a, **k: calls.append((a, k)))

    VoiceStudioApp._mirror_transcript_quietly(app, _transcript())

    assert calls == []


def test_mirror_quietly_is_a_noop_when_enabled_without_a_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=True, sync_folder="   ")
    calls: list[object] = []
    monkeypatch.setattr(app_module, "mirror_transcript", lambda *a, **k: calls.append((a, k)))

    VoiceStudioApp._mirror_transcript_quietly(app, _transcript())

    assert calls == []


def test_mirror_quietly_writes_the_mirror_files_when_enabled(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "data")
    sync_root = tmp_path / "mirror"
    sync_root.mkdir()
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(
        sync_enabled=True, sync_folder=str(sync_root), sync_include_audio=False
    )
    app.store = store
    app.status = SimpleNamespace(set=lambda _message: None)
    app._t = lambda key, **_values: key

    VoiceStudioApp._mirror_transcript_quietly(app, _transcript())

    written = [path for path in sync_root.rglob("*") if path.is_file()]
    assert {path.suffix for path in written} == {".md", ".json"}


def test_mirror_quietly_swallows_a_failure_onto_the_status_bar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Catches a mirror failure raising into the caller instead of being reported."""

    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=True, sync_folder=str(tmp_path / "mirror"))
    app.store = SimpleNamespace(sources=tmp_path / "sources")
    statuses: list[str] = []
    app.status = SimpleNamespace(set=statuses.append)
    app._t = lambda key, **_values: key
    monkeypatch.setattr(
        app_module,
        "mirror_transcript",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    VoiceStudioApp._mirror_transcript_quietly(app, _transcript())  # must not raise

    assert statuses == ["sync_failed"]


# --- hooks: editor save and speaker assignment -------------------------------


def test_saving_an_edit_triggers_the_mirror_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript = _transcript()

    class Store:
        def __init__(self) -> None:
            self.sources = tmp_path / "sources"

        def update_editor_state(
            self, transcript_id: str, text: str, formatting: dict[str, object]
        ) -> Transcript:
            assert transcript_id == transcript.id
            transcript.corrected_text = text
            return transcript

    app = object.__new__(VoiceStudioApp)
    app.current = transcript
    app.editor = FakeEditor("edited text")
    app.store = Store()
    app.settings = Settings(sync_enabled=True, sync_folder=str(tmp_path / "mirror"))
    app.status = SimpleNamespace(set=lambda _message: None)
    app._t = lambda key, **_values: key
    app._editor_formatting = lambda: {}
    app._editor_baseline = None
    calls: list[Transcript] = []
    def record_mirror(transcript_arg: Transcript, *_a: Any, **_k: Any) -> None:
        calls.append(transcript_arg)

    monkeypatch.setattr(app_module, "mirror_transcript", record_mirror)

    assert VoiceStudioApp._save_edits(app) is True

    assert calls == [transcript]


def test_saving_an_edit_does_not_mirror_when_sync_is_disabled(tmp_path: Path) -> None:
    transcript = _transcript()

    class Store:
        def update_editor_state(
            self, transcript_id: str, text: str, formatting: dict[str, object]
        ) -> Transcript:
            transcript.corrected_text = text
            return transcript

    app = object.__new__(VoiceStudioApp)
    app.current = transcript
    app.editor = FakeEditor("edited text")
    app.store = Store()
    app.settings = Settings(sync_enabled=False, sync_folder="")
    app.status = SimpleNamespace(set=lambda _message: None)
    app._t = lambda key, **_values: key
    app._editor_formatting = lambda: {}
    app._editor_baseline = None

    assert VoiceStudioApp._save_edits(app) is True  # store.sources is never touched


def test_assigning_a_speaker_triggers_the_mirror_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript = _transcript(segments=[Segment(start=0.0, end=1.0, text="hi")])

    class Store:
        def __init__(self) -> None:
            self.sources = tmp_path / "sources"

        def update_speaker_labels(self, transcript_id: str, labels: dict[int, str]) -> Transcript:
            assert transcript_id == transcript.id
            transcript.metadata = {"speaker_labels": {str(k): v for k, v in labels.items()}}
            return transcript

    app = object.__new__(VoiceStudioApp)
    app.current = transcript
    app.store = Store()
    app.settings = Settings(sync_enabled=True, sync_folder=str(tmp_path / "mirror"))
    app.status = SimpleNamespace(set=lambda _message: None)
    app._t = lambda key, **_values: key
    app.smart_speaker_list = SimpleNamespace(curselection=lambda: (0,))
    app._refresh_smart_text = lambda: None
    monkeypatch.setattr(app_module.simpledialog, "askstring", lambda *_a, **_k: "Оля")
    calls: list[Transcript] = []
    def record_mirror(transcript_arg: Transcript, *_a: Any, **_k: Any) -> None:
        calls.append(transcript_arg)

    monkeypatch.setattr(app_module, "mirror_transcript", record_mirror)

    VoiceStudioApp._assign_smart_speaker(app)

    assert calls == [transcript]


# --- Sync all now --------------------------------------------------------


def test_sync_all_now_refuses_when_disabled_and_never_starts_a_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=False, sync_folder="")
    app._t = lambda key, **_values: key
    errors: list[object] = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app._start_worker = lambda *_a, **_k: pytest.fail("must not start a worker")

    VoiceStudioApp._sync_all_now(app)

    assert errors


def test_sync_all_now_refuses_an_invalid_folder_and_never_starts_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_directory = tmp_path / "mirror.txt"
    not_a_directory.write_text("x", encoding="utf-8")
    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(sync_enabled=True, sync_folder=str(not_a_directory))
    app._t = lambda key, **_values: key
    errors: list[object] = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app._start_worker = lambda *_a, **_k: pytest.fail("must not start a worker")

    VoiceStudioApp._sync_all_now(app)

    assert errors


def test_sync_all_now_mirrors_every_transcript_and_posts_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches "Sync all now" skipping stored transcripts or losing the summary."""

    data_root = tmp_path / "data"
    monkeypatch.setattr(app_module, "data_dir", lambda: data_root / "unrelated")
    store = LocalStore(data_root)
    store.save(_transcript("t-1"))
    store.save(_transcript("t-2"))
    sync_root = tmp_path / "mirror"
    sync_root.mkdir()

    app = object.__new__(VoiceStudioApp)
    app.settings = Settings(
        sync_enabled=True, sync_folder=str(sync_root), sync_include_audio=False
    )
    app.store = store
    app.status = SimpleNamespace(set=lambda _message: None)
    app._t = lambda key, **_values: key
    app.events = queue.Queue()
    busy_calls: list[bool] = []
    app._set_busy = busy_calls.append

    def run_synchronously(_role: str, target: Any, **_kwargs: Any) -> None:
        target()

    app._start_worker = run_synchronously

    VoiceStudioApp._sync_all_now(app)

    assert busy_calls == [True]
    event, value = app.events.get_nowait()
    assert event == "sync_done"
    assert isinstance(value, SyncSummary)
    assert value.written == 2
    assert value.failed == ()


def test_the_sync_done_event_reports_success_on_the_status_bar_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object.__new__(VoiceStudioApp)
    app.events = queue.Queue()
    app._shutdown_event = threading.Event()
    statuses: list[str] = []
    app.status = SimpleNamespace(set=statuses.append)
    app._t = lambda key, **_values: key
    app._set_busy = lambda _value: None
    app.after = lambda *_a, **_k: None
    errors: list[object] = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app.events.put(("sync_done", SyncSummary(written=3, audio=1, failed=())))

    VoiceStudioApp._poll_events(app)

    assert statuses == ["sync_done"]
    assert errors == []


def test_the_sync_done_event_reports_a_failure_with_a_modal_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object.__new__(VoiceStudioApp)
    app.events = queue.Queue()
    app._shutdown_event = threading.Event()
    statuses: list[str] = []
    app.status = SimpleNamespace(set=statuses.append)
    app._t = lambda key, **_values: key
    app._set_busy = lambda _value: None
    app.after = lambda *_a, **_k: None
    errors: list[object] = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app.events.put(("sync_done", RuntimeError("disk full")))

    VoiceStudioApp._poll_events(app)

    assert statuses == ["sync_failed"]
    assert errors
