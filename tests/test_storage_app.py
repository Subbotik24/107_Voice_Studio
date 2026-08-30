import builtins
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from voice_studio import storage as storage_module
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


def _recursive_storage_snapshot(root: Path):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        snapshot[path.relative_to(root).as_posix()] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return snapshot


def test_audit_existing_reads_current_wal_from_temp_without_mutating_live_tree(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "data")
    anchor = sqlite3.connect(store.db_path)
    opened_databases = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        opened_databases.append(args[0])
        connection = real_connect(*args, **kwargs)
        return connection

    try:
        assert anchor.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        anchor.execute("PRAGMA wal_autocheckpoint = 0")
        item = transcript()
        item.id = "wal-current"
        anchor.execute(
            """
            INSERT INTO transcripts (
                id, created_at, source_sha256, language, engine, model, status,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.created_at,
                item.source_sha256,
                item.language,
                item.engine,
                item.model,
                item.status,
                json.dumps(item.to_dict()),
            ),
        )
        anchor.commit()
        assert store.db_path.with_name(f"{store.db_path.name}-wal").is_file()
        before = _recursive_storage_snapshot(store.root)
        monkeypatch.setattr(storage_module.sqlite3, "connect", traced_connect)

        result = LocalStore.audit_existing(store.root)

        assert result["records"] == 1
        assert result["status"] == "PASS"
        assert _recursive_storage_snapshot(store.root) == before
        assert opened_databases
        assert all(
            Path(database).resolve() != store.db_path.resolve()
            for database in opened_databases
        )
        assert all(not Path(database).exists() for database in opened_databases)
    finally:
        anchor.close()


@pytest.mark.parametrize("change", ["appearing", "disappearing"])
def test_audit_existing_retries_sidecar_state_churn_without_mutating_live_tree(
    tmp_path, monkeypatch, change
):
    store = LocalStore(tmp_path / "data")
    before = _recursive_storage_snapshot(store.root)
    real_capture = storage_module._capture_audit_database_state
    calls = 0

    def one_sidecar_appearance(db_path):
        nonlocal calls
        calls += 1
        state = real_capture(db_path)
        changed_call = 2 if change == "appearing" else 1
        if calls == changed_call:
            return (state[0], state[1], ("appeared", 1, 1, 1))
        return state

    monkeypatch.setattr(
        storage_module, "_capture_audit_database_state", one_sidecar_appearance
    )

    assert LocalStore.audit_existing(store.root)["status"] == "PASS"

    assert calls == 4
    assert _recursive_storage_snapshot(store.root) == before


def test_audit_existing_refuses_persistent_sidecar_churn_without_mutation(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "data")
    before = _recursive_storage_snapshot(store.root)
    real_capture = storage_module._capture_audit_database_state
    calls = 0

    def persistent_sidecar_churn(db_path):
        nonlocal calls
        calls += 1
        state = real_capture(db_path)
        if calls % 2 == 0:
            return (state[0], state[1], ("appeared", 1, 1, 1))
        return state

    monkeypatch.setattr(
        storage_module, "_capture_audit_database_state", persistent_sidecar_churn
    )

    with pytest.raises(RuntimeError, match="stable storage snapshot"):
        LocalStore.audit_existing(store.root)

    assert calls == storage_module.AUDIT_SNAPSHOT_ATTEMPTS * 2
    assert _recursive_storage_snapshot(store.root) == before


def test_audit_existing_discards_result_when_live_root_changes_after_scan(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "data")
    displaced = tmp_path / "displaced-data"
    real_audit = LocalStore.audit
    audit_calls = 0

    def replace_root_after_scan(snapshot_store):
        nonlocal audit_calls
        audit_calls += 1
        result = real_audit(snapshot_store)
        store.root.replace(displaced)
        store.root.mkdir()
        return result

    monkeypatch.setattr(LocalStore, "audit", replace_root_after_scan)
    try:
        with pytest.raises(RuntimeError, match="stable storage snapshot"):
            LocalStore.audit_existing(store.root)
    finally:
        store.root.rmdir()
        displaced.replace(store.root)

    assert audit_calls == 1
    assert store.db_path.is_file()


def test_audit_existing_refuses_temp_parent_inside_live_root_without_writing(
    tmp_path, monkeypatch
):
    store = LocalStore(tmp_path / "data")
    before = _recursive_storage_snapshot(store.root)
    monkeypatch.setattr(storage_module.tempfile, "gettempdir", lambda: str(store.root))

    with pytest.raises(RuntimeError, match="temporary directory parent"):
        LocalStore.audit_existing(store.root)

    assert _recursive_storage_snapshot(store.root) == before


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


def test_storage_audit_reports_structured_missing_records(tmp_path):
    store = LocalStore(tmp_path / "data")
    missing = []
    for item_id in ("row-a", "row-b"):
        original = tmp_path / f"{item_id}.wav"
        original.write_bytes(item_id.encode())
        managed, digest = store.import_source(original)
        item = transcript()
        item.id = item_id
        item.source_path = str(managed)
        item.source_sha256 = digest
        store.save(item)
        managed.unlink()
        missing.append((item_id, str(managed.resolve())))

    result = store.audit()

    assert result["missing"] == sorted(path for _, path in missing)
    assert result["missing_records"] == [
        {"id": item_id, "path": path}
        for item_id, path in sorted(missing, key=lambda value: value[1])
    ]


def test_storage_audit_reports_model_and_export_drift_without_writing(tmp_path):
    store = LocalStore(tmp_path / "data")
    model_manifest = store.models / "catalog.json"
    model_manifest.write_text(
        '{"version": 1, "models": [{"id": "tiny", "path": "tiny"}]}',
        encoding="utf-8",
    )
    existing_id = "12345678-1234-5678-1234-567812345678"
    stale_id = "87654321-4321-8765-4321-876543218765"
    existing = transcript()
    existing.id = existing_id
    store.save(existing)
    (store.exports / f"{existing_id}.txt").write_bytes(b"existing")
    (store.exports / f"{stale_id}.srt").write_bytes(b"stale")
    (store.exports / "custom-name.md").write_bytes(b"custom")
    (store.exports / "nested").mkdir()
    before = {
        path.relative_to(store.root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [model_manifest, *store.exports.iterdir()]
        if path.is_file()
    }

    result = store.audit()

    assert result["status"] == "PASS"
    assert result["model_catalog"]["missing"] == [
        {
            "id": "tiny",
            "path": str(store.models / "tiny"),
            "reason": "catalogued model is absent",
        }
    ]
    assert result["exports"] == {
        "files": [f"{existing_id}.txt", f"{stale_id}.srt", "custom-name.md"],
        "canonical_stale": [f"{stale_id}.srt"],
        "unmanaged": [f"{existing_id}.txt", "custom-name.md"],
        "blocked": [{"name": "nested", "reason": "export entry is not a regular file"}],
    }
    after = {
        path.relative_to(store.root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [model_manifest, *store.exports.iterdir()]
        if path.is_file()
    }
    assert after == before


def _make_storage_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are unavailable")
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"directory junction creation unavailable: {exc}")


@pytest.mark.parametrize("root_kind", ["missing", "file"])
def test_storage_audit_blocks_export_root_without_scanning(tmp_path, root_kind):
    store = LocalStore(tmp_path / "data")
    store.exports.rmdir()
    if root_kind == "file":
        store.exports.write_bytes(b"not a directory")

    result = store.audit()

    assert result["status"] == "PASS"
    assert result["exports"]["files"] == []
    expected_reason = (
        "export directory is missing"
        if root_kind == "missing"
        else "export directory is not a real directory"
    )
    assert result["exports"]["blocked"] == [{"name": ".", "reason": expected_reason}]
    assert result["exports"]["blocked"][0]["reason"]


def test_storage_audit_blocks_export_root_lstat_error(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    original_lstat = Path.lstat

    def lstat(path):
        if path == store.exports:
            raise OSError("simulated root lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"][0]["name"] == "."
    assert "could not be inspected safely" in result["exports"]["blocked"][0]["reason"]


def test_storage_audit_blocks_export_root_symlink_without_following(tmp_path):
    store = LocalStore(tmp_path / "data")
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_bytes(b"external")
    store.exports.rmdir()
    try:
        store.exports.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise
    before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir()))

    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"][0]["name"] == "."
    assert "symlink" in result["exports"]["blocked"][0]["reason"]
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir())) == before


def test_storage_audit_blocks_export_child_symlink_without_following(tmp_path):
    store = LocalStore(tmp_path / "data")
    target = tmp_path / "outside.txt"
    target.write_bytes(b"external")
    link = store.exports / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise
    before = (target.read_bytes(), target.stat().st_mtime_ns)

    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"] == [
        {"name": "linked.txt", "reason": "export entry is a symlink"}
    ]
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_storage_audit_blocks_export_root_junction_without_following(tmp_path):
    store = LocalStore(tmp_path / "data")
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_bytes(b"external")
    store.exports.rmdir()
    _make_storage_junction(store.exports, target)
    before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir()))

    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"][0]["name"] == "."
    assert "reparse" in result["exports"]["blocked"][0]["reason"]
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir())) == before


def test_storage_audit_blocks_export_child_junction_without_following(tmp_path):
    store = LocalStore(tmp_path / "data")
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_bytes(b"external")
    link = store.exports / "linked"
    _make_storage_junction(link, target)
    before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir()))

    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"][0]["name"] == "linked"
    assert "reparse" in result["exports"]["blocked"][0]["reason"]
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns, sorted(target.iterdir())) == before


def test_storage_audit_reports_export_stat_error_as_blocked(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    original_scandir = storage_module.os.scandir

    class BrokenEntry:
        name = "broken.txt"

        def stat(self, *, follow_symlinks=False):
            raise OSError("simulated stat failure")

    class BrokenScan:
        def __enter__(self):
            return iter([BrokenEntry()])

        def __exit__(self, *_args):
            return False

    def scandir(path):
        if Path(path) == store.exports:
            return BrokenScan()
        return original_scandir(path)

    monkeypatch.setattr(storage_module.os, "scandir", scandir)
    result = store.audit()

    assert result["exports"]["files"] == []
    assert result["exports"]["blocked"] == [
        {"name": "broken.txt", "reason": "export entry could not be inspected safely"}
    ]


def test_storage_audit_normalizes_uppercase_export_uuid_and_suffix(tmp_path):
    store = LocalStore(tmp_path / "data")
    existing_id = "12345678-1234-5678-1234-567812345678"
    stale_id = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    existing = transcript()
    existing.id = existing_id
    store.save(existing)
    existing_name = f"{existing_id.upper()}.TXT"
    stale_name = f"{stale_id.upper()}.SRT"
    (store.exports / existing_name).write_bytes(b"existing")
    (store.exports / stale_name).write_bytes(b"stale")

    result = store.audit()

    assert result["exports"]["files"] == [existing_name, stale_name]
    assert result["exports"]["canonical_stale"] == [stale_name]
    assert result["exports"]["unmanaged"] == [existing_name]


def _stored_missing_source(store: LocalStore, tmp_path: Path, item_id: str = "missing"):
    original = tmp_path / f"{item_id}.wav"
    original.write_bytes(b"original-audio")
    managed, digest = store.import_source(original)
    item = transcript()
    item.id = item_id
    item.source_path = str(managed)
    item.source_sha256 = digest
    store.save(item)
    managed.unlink()
    return item, original, managed


def test_repair_missing_source_requires_confirmation_before_db_access(tmp_path):
    store = LocalStore(tmp_path / "data")
    with pytest.raises(ValueError, match="--yes"):
        store.repair_missing_source("unknown")


def test_repair_missing_source_detaches_only_missing_managed_reference(tmp_path):
    store = LocalStore(tmp_path / "data")
    item, original, managed = _stored_missing_source(store, tmp_path)
    before = item.to_dict()

    result = store.repair_missing_source(
        item.id, confirmed=True, expected_path=str(managed)
    )

    assert result == {
        "repaired": True,
        "id": item.id,
        "path": str(managed),
        "action": "detached_missing_source",
    }
    repaired = store.get(item.id)
    assert repaired is not None
    assert repaired.source_path is None
    assert repaired.audio_retained is False
    after = repaired.to_dict()
    for key in before:
        if key not in {"source_path", "audio_retained"}:
            assert after[key] == before[key]
    assert original.exists()
    assert not managed.exists()


def test_repair_missing_source_second_attempt_is_refused_idempotently(tmp_path):
    store = LocalStore(tmp_path / "data")
    item, _original, managed = _stored_missing_source(store, tmp_path)
    store.repair_missing_source(item.id, confirmed=True, expected_path=str(managed))

    with pytest.raises(ValueError, match="no source"):
        store.repair_missing_source(item.id, confirmed=True)


def test_repair_missing_source_refuses_reappeared_file(tmp_path):
    store = LocalStore(tmp_path / "data")
    item, _original, managed = _stored_missing_source(store, tmp_path)
    managed.write_bytes(b"replacement")

    with pytest.raises(ValueError, match="reappeared"):
        store.repair_missing_source(item.id, confirmed=True)

    assert managed.read_bytes() == b"replacement"
    assert store.get(item.id).source_path == str(managed)


def test_repair_missing_source_refuses_final_symlink(tmp_path):
    store = LocalStore(tmp_path / "data")
    item, _original, managed = _stored_missing_source(store, tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"external")
    try:
        managed.symlink_to(outside)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise

    with pytest.raises(ValueError, match="unsafe"):
        store.repair_missing_source(item.id, confirmed=True)

    assert outside.exists()
    assert store.get(item.id).source_path == str(managed)


def test_repair_missing_source_refuses_unknown_filesystem_error(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    item, _original, managed = _stored_missing_source(store, tmp_path)
    real_lstat = Path.lstat

    def deny_target(path):
        if path == managed:
            raise OSError("simulated access denial")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_target)
    with pytest.raises(ValueError, match="inspected safely"):
        store.repair_missing_source(item.id, confirmed=True)

    assert store.get(item.id).source_path == str(managed)


@pytest.mark.parametrize("invalid_suffix", ["bad\x00.wav", "bad\x00/child.wav"])
def test_repair_missing_source_refuses_invalid_path_without_mutation(tmp_path, invalid_suffix):
    store = LocalStore(tmp_path / "data")
    item = transcript()
    store.save(item)
    payload = item.to_dict()
    payload["source_path"] = str(store.sources / invalid_suffix)
    with store._connect() as db:
        db.execute(
            "UPDATE transcripts SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), item.id),
        )

    with pytest.raises(ValueError, match="could not be inspected safely"):
        store.repair_missing_source(item.id, confirmed=True)

    persisted = store.get(item.id)
    assert persisted.source_path == payload["source_path"]
    assert persisted.audio_retained is True


@pytest.mark.parametrize("case", ["unknown", "no_source", "outside", "mismatch"])
def test_repair_missing_source_refuses_invalid_requests(tmp_path, case):
    store = LocalStore(tmp_path / "data")
    if case == "unknown":
        with pytest.raises(KeyError, match="transcript not found"):
            store.repair_missing_source("unknown", confirmed=True)
        return
    if case == "no_source":
        item = transcript()
        store.save(item)
        with pytest.raises(ValueError, match="no source"):
            store.repair_missing_source(item.id, confirmed=True)
        return
    item, _original, managed = _stored_missing_source(store, tmp_path)
    if case == "outside":
        outside = tmp_path / "outside.wav"
        with store._connect() as db:
            payload = item.to_dict()
            payload["source_path"] = str(outside)
            db.execute(
                "UPDATE transcripts SET payload_json = ? WHERE id = ?",
                (json.dumps(payload), item.id),
            )
        with pytest.raises(ValueError, match="managed sources"):
            store.repair_missing_source(item.id, confirmed=True)
    else:
        with pytest.raises(ValueError, match="expected path"):
            store.repair_missing_source(
                item.id, confirmed=True, expected_path=str(tmp_path / "other.wav")
            )
    expected = managed if case == "mismatch" else tmp_path / "outside.wav"
    assert store.get(item.id).source_path == str(expected)


def test_cleanup_orphans_rechecks_db_reference_inside_write_transaction(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "orphan.wav"
    source.write_bytes(b"referenced-at-cleanup")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "late-reference"
    item.source_path = str(managed)
    item.source_sha256 = digest

    monkeypatch.setattr(
        store,
        "audit",
        lambda: {"orphans": [str(managed.resolve())]},
    )
    store.save(item)

    result = store.cleanup_orphans(confirmed=True)

    assert result == {"removed": [], "count": 0}
    assert managed.exists()


def test_cleanup_orphans_removes_regular_direct_child_and_preserves_nested(tmp_path):
    store = LocalStore(tmp_path / "data")
    regular = store.sources / "regular.wav"
    regular.write_bytes(b"regular")
    nested = store.sources / "nested"
    nested.mkdir()
    (nested / "nested.wav").write_bytes(b"nested")

    result = store.cleanup_orphans(confirmed=True)

    assert result["removed"] == [str(regular)]
    assert not regular.exists()
    assert (nested / "nested.wav").exists()


def test_cleanup_orphans_refuses_symlink_candidate(tmp_path):
    store = LocalStore(tmp_path / "data")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    link = store.sources / "linked.wav"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise

    result = store.cleanup_orphans(confirmed=True)

    assert result == {"removed": [], "count": 0}
    assert link.is_symlink()
    assert outside.exists()


def test_cleanup_orphans_recheck_race_preserves_reference_committed_before_lock(
    tmp_path,
):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "concurrent.wav"
    source.write_bytes(b"concurrent-reference")
    managed, digest = store.import_source(source)
    item = transcript()
    item.id = "concurrent-reference"
    item.source_path = str(managed)
    item.source_sha256 = digest
    audit_ready = threading.Event()
    release_audit = threading.Event()

    def gated_audit():
        audit_ready.set()
        assert release_audit.wait(timeout=5)
        return {"orphans": [str(managed.resolve())]}

    store.audit = gated_audit
    with ThreadPoolExecutor(max_workers=1) as pool:
        cleanup_future = pool.submit(store.cleanup_orphans, confirmed=True)
        assert audit_ready.wait(timeout=5)
        LocalStore(store.root).save(item)
        release_audit.set()
        result = cleanup_future.result(timeout=5)

    assert result == {"removed": [], "count": 0}
    assert managed.exists()


def test_cleanup_orphans_skips_corrupt_nul_reference_row(tmp_path):
    store = LocalStore(tmp_path / "data")
    candidate = store.sources / "orphan.wav"
    candidate.write_bytes(b"must survive")
    item = transcript()
    store.save(item)
    payload = item.to_dict()
    payload["source_path"] = str(store.sources / "bad\x00.wav")
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE transcripts SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), item.id),
        )
    store.audit = lambda: {"orphans": [str(candidate)]}

    result = store.cleanup_orphans(confirmed=True)

    assert result == {"removed": [], "count": 0}
    assert candidate.read_bytes() == b"must survive"


def test_repair_missing_source_refuses_sources_root_junction(tmp_path):
    store = LocalStore(tmp_path / "data")
    item = transcript()
    store.save(item)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    original_sources = store.sources
    original_sources.rmdir()
    _make_storage_junction(original_sources, outside)
    payload = item.to_dict()
    payload["source_path"] = str(original_sources / "missing.wav")
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE transcripts SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), item.id),
        )

    with pytest.raises(ValueError, match="managed sources directory"):
        store.repair_missing_source(item.id, confirmed=True)

    assert sentinel.read_bytes() == b"external"


def test_repair_missing_source_refuses_ancestor_junction(tmp_path):
    store = LocalStore(tmp_path / "data")
    item = transcript()
    store.save(item)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    ancestor = store.sources / "nested"
    _make_storage_junction(ancestor, outside)
    payload = item.to_dict()
    payload["source_path"] = str(ancestor / "missing.wav")
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE transcripts SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), item.id),
        )

    with pytest.raises(ValueError, match="unsafe link"):
        store.repair_missing_source(item.id, confirmed=True)

    assert sentinel.read_bytes() == b"external"


def test_cleanup_orphans_refuses_final_junction_without_touching_target(tmp_path):
    store = LocalStore(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"external")
    candidate = store.sources / "junction"
    _make_storage_junction(candidate, outside)
    store.audit = lambda: {"orphans": [str(candidate)]}

    result = store.cleanup_orphans(confirmed=True)

    assert result == {"removed": [], "count": 0}
    assert sentinel.read_bytes() == b"external"
    assert candidate.exists()
