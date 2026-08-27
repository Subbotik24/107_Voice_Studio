import builtins
import hashlib
import io
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from voice_studio.models import Transcript
from voice_studio.storage import LocalStore, _compact_uuid


def process_import_source(root: str, source: str) -> str:
    return str(LocalStore(Path(root)).import_source(Path(source))[0])


def compact_uuid(value: str) -> str:
    return _compact_uuid(uuid.UUID(value))


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
    import voice_studio.storage as storage

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    link_paths = []
    lock = threading.Lock()
    opened = threading.Barrier(2, timeout=5)
    real_link = os.link
    real_open = Path.open

    def synchronized_open(self, mode="r", *args, **kwargs):
        if self.resolve() == source.resolve() and mode == "rb":
            opened.wait()
        return real_open(self, mode, *args, **kwargs)

    def record_link(source_path, target_path, *args, **kwargs):
        if Path(target_path).parent.resolve() == store.sources.resolve():
            with lock:
                link_paths.append(Path(target_path))
        return real_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", record_link)
    monkeypatch.setattr(Path, "open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: store.import_source(source), range(2))

    assert first[0] != second[0]
    assert first[1] in first[0].name
    assert second[1] in second[0].name
    assert len(link_paths) == 2
    assert len({path.name for path in link_paths}) == 2
    assert source.exists()


def test_import_source_never_replaces_an_existing_final_target(tmp_path, monkeypatch):
    import voice_studio.storage as storage

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    first, _digest = store.import_source(source)
    real_link = os.link

    def reject_existing(source_path, target_path, *args, **kwargs):
        assert not Path(target_path).exists(), "final target replacement is unsafe on Windows"
        return real_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", reject_existing)
    second, _ = store.import_source(source)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_concurrent_process_imports_never_overwrite_each_other(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")

    with ProcessPoolExecutor(max_workers=2) as pool:
        paths = list(
            pool.map(
                process_import_source,
                [str(store.root)] * 2,
                [str(source)] * 2,
            )
        )

    assert len(set(paths)) == 2
    assert all(Path(path).read_bytes() == source.read_bytes() for path in paths)
    assert source.exists()


def test_import_retries_full_uuid_collision_without_touching_existing_target(
    tmp_path, monkeypatch
):
    import voice_studio.storage as storage

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "collision.wav"
    source.write_bytes(b"collision-content")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    collision_uuid = "11111111111111111111111111111111"
    winner_uuid = "22222222222222222222222222222222"
    existing = store.sources / f"{source_hash}{compact_uuid(collision_uuid)}.wav"
    existing.write_bytes(b"existing-peer")
    values = iter(
        [
            uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            uuid.UUID(collision_uuid),
            uuid.UUID(winner_uuid),
        ]
    )
    monkeypatch.setattr(storage.uuid, "uuid4", lambda: next(values))

    linked = []
    real_link = os.link

    def record_link(source_path, target_path, *args, **kwargs):
        linked.append(Path(target_path))
        return real_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", record_link)
    with existing.open("rb") as peer:
        managed, _ = store.import_source(source)
        assert peer.read() == b"existing-peer"

    assert managed.name.endswith(f"{compact_uuid(winner_uuid)}.wav")
    assert existing.read_bytes() == b"existing-peer"
    assert linked[0] == existing
    assert managed.read_bytes() == source.read_bytes()


def test_repeated_uuid_collision_has_bounded_allocation_and_cleans_partial(
    tmp_path, monkeypatch
):
    import voice_studio.operation as operation
    import voice_studio.storage as storage

    limit = getattr(storage, "MAX_MANAGED_TARGET_ATTEMPTS", None)
    assert limit is not None, "managed target allocation must be bounded"
    error_type = getattr(operation, "ManagedTargetAllocationError", None)
    assert error_type is not None, "allocation exhaustion needs a concrete error"
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "repeated-collision.wav"
    source.write_bytes(b"repeated-collision")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    collision_uuid = uuid.UUID("33333333333333333333333333333333")
    existing = store.sources / f"{source_hash}{compact_uuid(collision_uuid.hex)}.wav"
    existing.write_bytes(b"existing-peer")
    partial_uuid = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    uuid_calls = 0

    def repeat_uuid():
        nonlocal uuid_calls
        uuid_calls += 1
        return partial_uuid if uuid_calls == 1 else collision_uuid

    monkeypatch.setattr(storage.uuid, "uuid4", repeat_uuid)
    real_link = os.link
    link_calls = 0

    def collision_guard(source_path, target_path, *args, **kwargs):
        nonlocal link_calls
        link_calls += 1
        if link_calls > limit:
            raise AssertionError("unbounded managed target allocation")
        return real_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", collision_guard)
    with pytest.raises(error_type, match=f"after {limit} attempts") as raised:
        store.import_source(source)

    assert raised.value.attempts == limit
    assert uuid_calls == limit + 1
    assert link_calls == limit
    assert existing.read_bytes() == b"existing-peer"
    assert list(store.sources.glob("*.partial")) == []
    assert source.exists()


def test_link_failure_cleanup_failure_surfaces_owned_residue(tmp_path, monkeypatch):
    import voice_studio.storage as storage

    operation = __import__("voice_studio.operation", fromlist=["OwnedPartialCleanupError"])
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "link-failure.wav"
    source.write_bytes(b"link-failure")

    def fail_link(*_args, **_kwargs):
        raise OSError("link failed")

    monkeypatch.setattr(storage.os, "link", fail_link)
    real_unlink = Path.unlink

    def fail_partial_unlink(self, missing_ok=False):
        if self.name.endswith(".partial"):
            raise OSError("cleanup denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_partial_unlink)
    with pytest.raises(operation.OwnedPartialCleanupError) as raised:
        store.import_source(source)

    assert raised.value.residue_path.exists()
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "link failed"


def test_link_success_partial_unlink_failure_surfaces_residue(tmp_path, monkeypatch):
    import voice_studio.storage as storage

    operation = __import__("voice_studio.operation", fromlist=["OwnedPartialCleanupError"])
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "unlink-failure.wav"
    source.write_bytes(b"unlink-failure")
    real_link = os.link
    real_unlink = Path.unlink

    monkeypatch.setattr(storage.os, "link", real_link)

    def fail_partial_unlink(self, missing_ok=False):
        if self.name.endswith(".partial"):
            raise OSError("partial unlink denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_partial_unlink)
    with pytest.raises(operation.OwnedPartialCleanupError) as raised:
        store.import_source(source)

    assert raised.value.residue_path.exists()
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "partial unlink denied"


def test_import_source_cancellation_cleans_owned_partial_only(tmp_path):
    from voice_studio.jobs import JobCancelled
    from voice_studio.operation import OperationBudget

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
    from voice_studio.operation import OperationBudget

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
    from voice_studio import operation
    from voice_studio.operation import OperationBudget

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

    monkeypatch.setattr(operation, "_monotonic", monotonic)
    with pytest.raises(TimeoutError, match="import"):
        store.import_source(source, budget=OperationBudget(10))

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_cancellation_after_a_chunk_cleans_owned_partial_only(tmp_path):
    from voice_studio.jobs import JobCancelled
    from voice_studio.operation import OperationBudget

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
    import voice_studio.storage as storage

    store = LocalStore(tmp_path / "data")
    source = tmp_path / "promotion.wav"
    source.write_bytes(b"promotion-failure")
    unrelated = store.sources / "unrelated.wav"
    unrelated.write_bytes(b"keep")

    def fail_link(*_args, **_kwargs):
        raise OSError("promotion failed")

    monkeypatch.setattr(storage.os, "link", fail_link)
    with pytest.raises(OSError, match="promotion failed"):
        store.import_source(source)

    assert source.exists()
    assert unrelated.exists()
    assert list(store.sources.iterdir()) == [unrelated]


def test_import_source_surfaces_partial_cleanup_failure_with_residue_and_cause(
    tmp_path, monkeypatch
):
    import importlib

    import voice_studio.storage as storage

    operation = importlib.import_module("voice_studio.operation")
    error_type = getattr(operation, "OwnedPartialCleanupError", None)
    assert error_type is not None, "structured partial cleanup error is required"
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "cleanup-failure.wav"
    source.write_bytes(b"cleanup-failure")
    real_unlink = Path.unlink

    def fail_link(*_args, **_kwargs):
        raise OSError("promotion failed")

    def fail_partial_unlink(self, missing_ok=False):
        if self.name.endswith(".partial"):
            raise OSError("cleanup denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(storage.os, "link", fail_link)
    monkeypatch.setattr(Path, "unlink", fail_partial_unlink)
    with pytest.raises(error_type) as raised:
        store.import_source(source)

    error = raised.value
    assert error.residue_path.exists()
    assert str(error.residue_path) in str(error)
    assert isinstance(error.__cause__, OSError)
    assert str(error.__cause__) == "promotion failed"
    assert source.exists()


def test_cleanup_rechecks_before_unlink_when_a_reference_commits_between_checks(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "race.wav"
    source.write_bytes(b"race-content")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "race-reference"
    item.source_sha256 = digest
    item.source_path = str(managed)
    store.remove_unreferenced_source(managed, digest)

    assert not managed.exists()
    assert store.get(item.id) is None


def test_cleanup_first_rejects_save_after_final_check_and_unlink(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "cleanup-first.wav"
    source.write_bytes(b"cleanup-first")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "cleanup-first-reference"
    item.source_sha256 = digest
    item.source_path = str(managed)
    ready = threading.Event()
    release = threading.Event()
    real_unlink = Path.unlink

    def gated_unlink(self, missing_ok=False):
        if self.resolve() == managed.resolve():
            ready.set()
            assert release.wait(timeout=5)
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", gated_unlink)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup_future = pool.submit(store.remove_unreferenced_source, managed, digest)
        assert ready.wait(timeout=5)
        save_started = threading.Event()

        def save_from_peer():
            save_started.set()
            LocalStore(store.root).save(item)

        save_future = pool.submit(save_from_peer)
        assert save_started.wait(timeout=5)
        release.set()
        cleanup_future.result(timeout=5)
        with pytest.raises(FileNotFoundError, match="managed source"):
            save_future.result(timeout=5)

    assert not managed.exists()


def test_save_first_commits_and_cleanup_preserves_managed_source(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "save-first.wav"
    source.write_bytes(b"save-first")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "save-first-reference"
    item.source_sha256 = digest
    item.source_path = str(managed)

    LocalStore(store.root).save(item)
    store.remove_unreferenced_source(managed, digest)

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
