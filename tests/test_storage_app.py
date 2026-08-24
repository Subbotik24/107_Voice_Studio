import builtins
import hashlib
import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_voice_studio.models import Transcript
from hermes_voice_studio.storage import LocalStore


def transcript() -> Transcript:
    return Transcript(
        id="1",
        created_at="2026-07-26T00:00:00+00:00",
        source_name="note.wav",
        source_sha256="b" * 64,
        language="cs",
        engine="faster-whisper",
        model="small",
        raw_text="technická poznámka",
        corrected_text="technická poznámka",
    )


def test_history_search(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)
    assert store.list("technická") == [item]
    assert store.list("missing") == []


def test_store_connection_context_releases_sqlite_handle(tmp_path):
    """The store must release SQLite files before backup restore swaps directories."""

    store = LocalStore(tmp_path)
    with store._connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_legacy_payload_defaults_engine():
    data = transcript().to_dict()
    data.pop("engine")
    restored = Transcript.from_dict(data)
    assert restored.engine == "faster-whisper"


def test_shared_source_is_not_deleted_while_another_record_references_it(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    managed, digest = store.import_source(source)
    first = transcript()
    first.source_sha256 = digest
    first.source_path = str(managed)
    second = Transcript.from_dict(first.to_dict())
    second.id = "2"
    store.save(first)
    store.save(second)
    store.delete_audio(first)
    assert managed.exists()
    store.delete_audio(second)
    assert not managed.exists()


def test_import_source_hashes_and_copies_source_in_one_pass(tmp_path, monkeypatch):
    import shutil

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"one-pass-content")
    source_reads = 0
    real_io_open = io.open
    real_builtin_open = builtins.open
    monkeypatch.setattr(
        shutil,
        "copy2",
        lambda *_args, **_kwargs: pytest.fail("managed import must not copy source twice"),
    )

    def counted_io_open(file, *args, **kwargs):
        nonlocal source_reads
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(file).resolve() == source.resolve() and "r" in mode:
            source_reads += 1
        return real_io_open(file, *args, **kwargs)

    def counted_builtin_open(file, *args, **kwargs):
        nonlocal source_reads
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(file).resolve() == source.resolve() and "r" in mode:
            source_reads += 1
        return real_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", counted_io_open)
    monkeypatch.setattr(builtins, "open", counted_builtin_open)

    managed, digest = store.import_source(source)

    assert source_reads == 1
    assert managed.read_bytes() == source.read_bytes()
    assert digest == hashlib.sha256(managed.read_bytes()).hexdigest()
    assert source.exists()


def test_import_source_enforces_growing_source_cap_and_cleans_partial(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "large.wav"
    source.write_bytes(b"four")

    with pytest.raises(ValueError, match="max_bytes"):
        store.import_source(source, max_bytes=3)

    assert source.exists()
    assert source.read_bytes() == b"four"
    assert list(store.sources.iterdir()) == []


def test_simultaneous_imports_have_distinct_owned_partial_paths(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    replace_paths = []
    lock = threading.Lock()
    opened = threading.Barrier(2, timeout=5)
    real_replace = Path.replace
    real_open = Path.open

    def synchronized_open(self, mode="r", *args, **kwargs):
        if self.resolve() == source.resolve() and mode == "rb":
            opened.wait()
        return real_open(self, mode, *args, **kwargs)

    def record_replace(self, target):
        if self.parent.resolve() == store.sources.resolve():
            with lock:
                replace_paths.append(self)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", record_replace)
    monkeypatch.setattr(Path, "open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: store.import_source(source), range(2))

    assert first[0] != second[0]
    assert first[1] in first[0].name
    assert second[1] in second[0].name
    assert len(replace_paths) == 2
    assert len({path.name for path in replace_paths}) == 2
    assert source.exists()


def test_import_source_never_replaces_an_existing_final_target(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    first, _digest = store.import_source(source)
    real_replace = Path.replace

    def reject_existing(self, target):
        assert not target.exists(), "final target replacement is unsafe on Windows"
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", reject_existing)
    second, _ = store.import_source(source)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_import_source_cancellation_cleans_owned_partial_only(tmp_path):
    from hermes_voice_studio.jobs import JobCancelled
    from hermes_voice_studio.operation import OperationBudget

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "cancel.wav"
    source.write_bytes(b"cancel-me")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")

    with pytest.raises(JobCancelled, match="import"):
        store.import_source(source, budget=OperationBudget(10, cancelled=lambda: True))

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_timeout_cleans_owned_partial_only(tmp_path):
    from hermes_voice_studio.operation import OperationBudget

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "timeout.wav"
    source.write_bytes(b"timeout-me")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")

    with pytest.raises(TimeoutError, match="import"):
        store.import_source(source, budget=OperationBudget(0))

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_timeout_after_a_chunk_cleans_owned_partial_only(tmp_path, monkeypatch):
    from hermes_voice_studio import operation
    from hermes_voice_studio.operation import OperationBudget

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "timeout-after-chunk.wav"
    source.write_bytes(b"timeout-after-chunk")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")
    calls = 0

    def monotonic():
        nonlocal calls
        calls += 1
        return 20.0 if calls == 5 else float(calls)

    monkeypatch.setattr(operation.time, "monotonic", monotonic)
    with pytest.raises(TimeoutError, match="import"):
        store.import_source(source, budget=OperationBudget(10))

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_cancellation_after_a_chunk_cleans_owned_partial_only(tmp_path):
    from hermes_voice_studio.jobs import JobCancelled
    from hermes_voice_studio.operation import OperationBudget

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "cancel-after-chunk.wav"
    source.write_bytes(b"cancel-after-chunk")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(JobCancelled, match="import"):
        store.import_source(source, budget=OperationBudget(10, cancelled=cancelled))

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_read_failure_cleans_owned_partial_only(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "read-failure.wav"
    source.write_bytes(b"read-failure")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")
    real_open = Path.open

    class FailingReader:
        def __init__(self, stream):
            self.stream = stream
            self.reads = 0

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *exc):
            return self.stream.__exit__(*exc)

        def read(self, size=-1):
            self.reads += 1
            if self.reads > 1:
                raise OSError("read failed")
            return self.stream.read(size)

    def patched_open(self, mode="r", *args, **kwargs):
        stream = real_open(self, mode, *args, **kwargs)
        if self.resolve() == source.resolve() and mode == "rb":
            return FailingReader(stream)
        return stream

    monkeypatch.setattr(Path, "open", patched_open)
    with pytest.raises(OSError, match="read failed"):
        store.import_source(source)

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_write_failure_cleans_owned_partial_only(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "write-failure.wav"
    source.write_bytes(b"write-failure")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")
    real_open = Path.open

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *exc):
            return self.stream.__exit__(*exc)

        def write(self, _block):
            raise OSError("write failed")

    def patched_open(self, mode="r", *args, **kwargs):
        stream = real_open(self, mode, *args, **kwargs)
        if self.parent.resolve() == store.sources.resolve() and mode == "xb":
            return FailingWriter(stream)
        return stream

    monkeypatch.setattr(Path, "open", patched_open)
    with pytest.raises(OSError, match="write failed"):
        store.import_source(source)

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_promotion_failure_cleans_owned_partial_only(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "promotion.wav"
    source.write_bytes(b"promotion-failure")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")

    def fail_replace(self, _target):
        raise OSError("promotion failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="promotion failed"):
        store.import_source(source)

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_surfaces_partial_cleanup_failure_with_residue_and_cause(
    tmp_path, monkeypatch
):
    import importlib

    operation = importlib.import_module("hermes_voice_studio.operation")
    error_type = getattr(operation, "OwnedPartialCleanupError", None)
    assert error_type is not None, "structured partial cleanup error is required"
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "cleanup-failure.wav"
    source.write_bytes(b"cleanup-failure")
    real_unlink = Path.unlink

    def fail_replace(self, _target):
        raise OSError("promotion failed")

    def fail_partial_unlink(self, missing_ok=False):
        if self.name.endswith(".partial"):
            raise OSError("cleanup denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_partial_unlink)
    with pytest.raises(error_type) as raised:
        store.import_source(source)

    error = raised.value
    assert error.residue_path.exists()
    assert str(error.residue_path) in str(error)
    assert isinstance(error.__cause__, OSError)
    assert str(error.__cause__) == "promotion failed"
    assert source.exists()


def test_cleanup_rechecks_before_unlink_when_a_reference_commits_between_checks(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "race.wav"
    source.write_bytes(b"race-content")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "race-reference"
    item.source_sha256 = digest
    item.source_path = str(managed)
    calls = 0
    real_check = getattr(store, "_source_is_referenced", None)
    assert real_check is not None, "source reference check must be centralized"

    def racing_check(target, source_hash):
        nonlocal calls
        calls += 1
        referenced = real_check(target, source_hash)
        if calls == 1:
            store.save(item)
        return referenced

    monkeypatch.setattr(store, "_source_is_referenced", racing_check)
    store.remove_unreferenced_source(managed, digest)

    assert calls >= 2
    assert managed.exists()
    assert store.get(item.id).source_path == str(managed)


def test_raw_text_is_immutable_and_corrected_text_has_explicit_update(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)
    item.raw_text = "mutated"
    with pytest.raises(ValueError, match="raw_text"):
        store.save(item)
    updated = store.update_corrected_text("1", "corrected")
    assert updated.raw_text == "technická poznámka"
    assert updated.corrected_text == "corrected"


def test_source_name_can_be_renamed_without_changing_transcript_content(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)

    renamed = store.rename_source_name("1", "зустріч_з_командою.wav")

    assert renamed.source_name == "зустріч_з_командою.wav"
    assert renamed.raw_text == "technická poznámka"
    assert renamed.corrected_text == item.corrected_text
    assert store.get("1").source_name == "зустріч_з_командою.wav"


def test_editor_formatting_is_saved_without_changing_raw_or_corrected_text(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)

    updated = store.update_editor_formatting(
        "1", {"bold": [("1.0", "1.4")], "italic": [("1.5", "1.8")]}
    )

    assert updated.raw_text == item.raw_text
    assert updated.corrected_text == item.corrected_text
    assert updated.metadata["editor_formatting"]["bold"] == [["1.0", "1.4"]]


def test_editor_state_update_persists_text_and_formatting_atomically(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)

    updated = store.update_editor_state(
        "1", "corrected together", {"bold": [("1.0", "1.4")], "italic": []}
    )

    assert updated.corrected_text == "corrected together"
    assert updated.raw_text == item.raw_text
    persisted = store.get("1")
    assert persisted.corrected_text == "corrected together"
    assert persisted.metadata["editor_formatting"]["bold"] == [["1.0", "1.4"]]


def test_editor_state_update_rolls_back_when_database_write_fails(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)
    with store._connect() as db:
        db.execute(
            """
            CREATE TRIGGER fail_editor_state_update
            BEFORE UPDATE ON transcripts
            BEGIN
                SELECT RAISE(ABORT, 'editor state write failed');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="editor state write failed"):
        store.update_editor_state("1", "must not persist", {"bold": []})

    persisted = store.get("1")
    assert persisted.corrected_text == item.corrected_text
    assert "editor_formatting" not in persisted.metadata


def test_schema_version_and_legacy_engine_migration(tmp_path):
    db_path = tmp_path / "history.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE transcripts (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                language TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
    LocalStore(tmp_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        columns = {row[1] for row in db.execute("PRAGMA table_info(transcripts)")}
    assert "engine" in columns


def test_storage_audit_and_explicit_orphan_cleanup(tmp_path):
    store = LocalStore(tmp_path / "data")
    orphan = store.sources / "orphan.wav"
    orphan.write_bytes(b"managed orphan")
    audit = store.audit()
    assert audit["status"] == "PASS"
    assert audit["orphans"] == [str(orphan.resolve())]
    with pytest.raises(ValueError, match="--yes"):
        store.cleanup_orphans()
    result = store.cleanup_orphans(confirmed=True)
    assert result["count"] == 1
    assert not orphan.exists()
