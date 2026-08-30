import os
import queue
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from voice_studio import model_catalog as model_catalog_module
from voice_studio.model_catalog import ModelCatalog


class DownloadQueue:
    def __init__(self, result=None):
        self.events = []
        self.result = result

    def get(self, timeout=None):
        self.events.append(("get", timeout))
        if self.result is None:
            raise queue.Empty
        return self.result

    def cancel_join_thread(self):
        self.events.append("cancel_join_thread")

    def close(self):
        self.events.append("close")


class DownloadProcess:
    def __init__(self, *, alive=False, exitcode=0, start_error=None):
        self.events = []
        self.alive = alive
        self.exitcode = exitcode
        self.start_error = start_error

    def start(self):
        self.events.append("start")
        if self.start_error:
            raise self.start_error

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.events.append("terminate")

    def join(self, timeout=None):
        self.events.append(("join", timeout))

    def kill(self):
        self.events.append("kill")
        self.alive = False


class DownloadContext:
    def __init__(self, process, result=None):
        self.queue = DownloadQueue(result)
        self.process = process

    def Queue(self):
        return self.queue

    def Process(self, *, args, **_kwargs):
        assert args[3] is self.queue
        return self.process


def patch_download_context(monkeypatch, context):
    monkeypatch.setattr(model_catalog_module, "registry_url", lambda: None)
    monkeypatch.setattr(
        model_catalog_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 10**15})(),
    )
    monkeypatch.setattr(
        model_catalog_module.multiprocessing,
        "get_context",
        lambda _name: context,
    )


def local_model(path: Path) -> Path:
    path.mkdir()
    (path / "model.bin").write_bytes(b"fixture model")
    (path / "config.json").write_text('{"model_type":"Whisper"}', encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return path


def snapshot_tree(root: Path) -> tuple[dict[str, tuple[bytes, int]], dict[str, tuple[str, ...]]]:
    files: dict[str, tuple[bytes, int]] = {}
    entries: dict[str, tuple[str, ...]] = {}
    try:
        root.lstat()
    except OSError:
        return files, entries
    if not os.path.isdir(root) or os.path.islink(root):
        return files, entries
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as iterator:
            scanned = list(iterator)
        entries[str(directory.relative_to(root))] = tuple(
            sorted(entry.name for entry in scanned)
        )
        for entry in scanned:
            path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                files[str(path.relative_to(root))] = (path.read_bytes(), entry_stat.st_mtime_ns)
    return files, entries


def assert_inspection_is_read_only(root: Path, expected: dict) -> None:
    before = snapshot_tree(root)
    assert ModelCatalog.inspect(root) == expected
    assert snapshot_tree(root) == before


def test_inspect_absent_root_is_pass_and_does_not_create_it(tmp_path):
    root = tmp_path / "not-created"

    assert_inspection_is_read_only(
        root,
        {
            "status": "PASS",
            "manifest": "absent",
            "missing": [],
            "orphans": [],
            "blocked": [],
            "staging": [],
            "residue": [],
        },
    )
    assert not root.exists()


def test_inspect_reports_catalog_drift_and_staging_without_writing(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "catalog.json").write_text(
        '{"version": 1, "models": [{"id": "tiny", "path": "tiny"}]}',
        encoding="utf-8",
    )
    local_model(root / "small")
    (root / "broken").mkdir()
    (root / "broken" / "config.json").write_text("{}", encoding="utf-8")
    stale_staging = root / ".downloads" / ("tiny-" + "a" * 32)
    fresh_staging = root / ".downloads" / ("small-" + "b" * 32)
    for path in (stale_staging, fresh_staging):
        path.mkdir(parents=True)
        (path / "partial").write_bytes(b"x")
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(stale_staging / "partial", (old, old))
    os.utime(stale_staging, (old, old))
    stale_residue = root / "catalog.json.tmp"
    fresh_residue = root / ("catalog.json." + "c" * 32 + ".tmp")
    stale_residue.write_bytes(b"old")
    fresh_residue.write_bytes(b"fresh")
    os.utime(stale_residue, (old, old))
    monkeypatch.setattr(model_catalog_module.time, "time", lambda: old + 3 * 24 * 60 * 60)

    before = snapshot_tree(root)
    result = ModelCatalog.inspect(root)

    assert result["status"] == "ATTENTION"
    assert result["manifest"] == "valid"
    assert result["missing"] == [
        {"id": "tiny", "path": str(root / "tiny"), "reason": "catalogued model is absent"}
    ]
    assert result["orphans"] == [
        {"id": "broken", "path": str(root / "broken"), "complete": False},
        {"id": "small", "path": str(root / "small"), "complete": True},
    ]
    assert result["staging"] == [
        {"name": fresh_staging.name, "pattern_valid": True, "stale": False, "safe": True},
        {"name": stale_staging.name, "pattern_valid": True, "stale": True, "safe": True},
    ]
    assert result["residue"] == [
        {"name": fresh_residue.name, "stale": False, "safe": True},
        {"name": stale_residue.name, "stale": True, "safe": True},
    ]
    assert snapshot_tree(root) == before


@pytest.mark.parametrize(
    "payload", [b"{not-json", b'{"version":999,"models":[]}', b'{"version":1}']
)
def test_inspect_invalid_manifest_fails_without_writing(tmp_path, payload):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "catalog.json").write_bytes(payload)
    before = snapshot_tree(root)

    result = ModelCatalog.inspect(root)

    assert result == {
        "status": "FAIL",
        "manifest": "invalid",
        "missing": [],
        "orphans": [],
        "blocked": [],
        "staging": [],
        "residue": [],
    }
    assert snapshot_tree(root) == before


def test_inspect_blocks_model_symlink_without_following_or_writing(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    target = local_model(tmp_path / "outside")
    link = root / "linked-model"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise

    before = snapshot_tree(root)
    result = ModelCatalog.inspect(root)

    assert result["status"] == "ATTENTION"
    assert result["blocked"] == [
        {
            "id": "linked-model",
            "path": str(link),
            "reason": "model path is a symlink",
        }
    ]
    assert result["orphans"] == []
    assert snapshot_tree(root) == before
    assert target.joinpath("model.bin").read_bytes() == b"fixture model"


def test_inspect_rejects_nul_in_catalog_path_without_crashing_or_writing(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "catalog.json").write_text(
        '{"version": 1, "models": [{"id": "bad", "path": "bad\\u0000model"}]}',
        encoding="utf-8",
    )
    before = snapshot_tree(root)

    result = ModelCatalog.inspect(root)

    assert result == {
        "status": "ATTENTION",
        "manifest": "valid",
        "missing": [],
        "orphans": [],
        "blocked": [
            {
                "id": "bad",
                "path": str(root / "catalog.json"),
                "reason": "manifest entry has an invalid model path",
            }
        ],
        "staging": [],
        "residue": [],
    }
    assert snapshot_tree(root) == before


def test_inspect_blocks_root_junction_without_following_or_writing(tmp_path):
    outside = local_model(tmp_path / "outside")
    root = tmp_path / "managed"
    make_junction(root, outside)
    before = snapshot_tree(outside)

    result = ModelCatalog.inspect(root)

    assert result == {
        "status": "ATTENTION",
        "manifest": "absent",
        "missing": [],
        "orphans": [],
        "blocked": [
            {
                "id": "<root>",
                "path": str(root),
                "reason": "model root is a reparse point",
            }
        ],
        "staging": [],
        "residue": [],
    }
    assert snapshot_tree(outside) == before
    assert (outside / "model.bin").read_bytes() == b"fixture model"


@pytest.mark.parametrize("ancestor", [False, True])
def test_inspect_blocks_catalogued_junction_without_following_or_writing(
    tmp_path, ancestor
):
    outside = local_model(tmp_path / "outside")
    root = tmp_path / "managed"
    root.mkdir()
    if ancestor:
        junction = root / "ancestor"
        make_junction(junction, outside)
        relative_path = "ancestor/model"
    else:
        junction = root / "linked-model"
        make_junction(junction, outside)
        relative_path = "linked-model"
    (root / "catalog.json").write_text(
        '{"version": 1, "models": [{"id": "tiny", "path": "'
        + relative_path
        + '"}]}',
        encoding="utf-8",
    )
    before = snapshot_tree(outside)

    result = ModelCatalog.inspect(root)

    expected_path = junction
    expected_block = {
        "id": "tiny",
        "path": str(expected_path),
        "reason": "model path is a reparse point",
    }
    assert result["status"] == "ATTENTION"
    assert result["manifest"] == "valid"
    assert expected_block in result["blocked"]
    if not ancestor:
        assert result["blocked"] == [expected_block]
    assert snapshot_tree(outside) == before
    assert (outside / "model.bin").read_bytes() == b"fixture model"


def make_junction(link: Path, target: Path) -> None:
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
        pytest.skip(f"directory junctions are unavailable: {exc}")


def test_local_model_import_verify_resolve_and_remove(tmp_path):
    original = local_model(tmp_path / "original")
    catalog = ModelCatalog(tmp_path / "managed")
    entry = catalog.import_local("tiny-fixture", original)
    managed = catalog.resolve("tiny-fixture")
    assert entry["id"] == "tiny-fixture"
    assert managed != original
    assert catalog.verify("tiny-fixture")["status"] == "PASS"
    with pytest.raises(ValueError, match="--yes"):
        catalog.remove("tiny-fixture")
    assert catalog.remove("tiny-fixture", confirmed=True)["removed"]
    assert original.is_dir()
    assert not managed.exists()


def test_model_integrity_detects_tampering(tmp_path):
    original = local_model(tmp_path / "original")
    catalog = ModelCatalog(tmp_path / "managed")
    catalog.import_local("tiny-fixture", original)
    (catalog.resolve("tiny-fixture") / "model.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        catalog.verify("tiny-fixture")


def test_model_import_excludes_transient_download_metadata(tmp_path):
    original = local_model(tmp_path / "original")
    cache = original / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "model.bin.lock").write_bytes(b"")
    (cache / "model.bin.metadata").write_text("transient", encoding="utf-8")
    (original / ".DS_Store").write_bytes(b"transient")
    (original / "partial.incomplete").write_bytes(b"transient")
    catalog = ModelCatalog(tmp_path / "managed")

    entry = catalog.import_local("tiny-fixture", original)

    assert all(not name.startswith(".cache/") for name in entry["files"])
    assert ".DS_Store" not in entry["files"]
    assert "partial.incomplete" not in entry["files"]
    assert catalog.verify("tiny-fixture")["status"] == "PASS"


def test_offline_mode_blocks_download_without_starting_worker(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    with pytest.raises(RuntimeError, match="offline-only"):
        catalog.install("tiny", offline_only=True)


def test_cancelled_download_bounds_stubborn_process_and_disposes_queue(
    tmp_path, monkeypatch
):
    class RecordingQueue:
        def __init__(self):
            self.events = []

        def cancel_join_thread(self):
            self.events.append("cancel_join_thread")

        def close(self):
            self.events.append("close")

    class StubbornProcess:
        def __init__(self):
            self.events = []
            self.alive = True
            self.exitcode = None

        def start(self):
            self.events.append("start")

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.events.append("terminate")

        def join(self, timeout=None):
            self.events.append(("join", timeout))

        def kill(self):
            self.events.append("kill")
            self.alive = False

    class SpawnContext:
        def __init__(self):
            self.result_queue = RecordingQueue()
            self.process = StubbornProcess()

        def Queue(self):
            return self.result_queue

        def Process(self, *, args, **_kwargs):
            assert args[3] is self.result_queue
            return self.process

    context = SpawnContext()
    catalog = ModelCatalog(tmp_path / "managed")
    monkeypatch.setattr(model_catalog_module, "registry_url", lambda: None)
    monkeypatch.setattr(
        model_catalog_module.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 10**15})(),
    )
    monkeypatch.setattr(
        model_catalog_module.multiprocessing,
        "get_context",
        lambda _name: context,
    )

    with pytest.raises(RuntimeError, match="model download cancelled: tiny"):
        catalog.install("tiny", cancelled=lambda: True)

    assert context.process.events == ["start", "terminate", ("join", 5), "kill", ("join", 2)]
    assert context.result_queue.events == ["cancel_join_thread", "close"]
    assert list(catalog.downloads.iterdir()) == []


def test_download_start_failure_disposes_queue_and_owned_staging(tmp_path, monkeypatch):
    process = DownloadProcess(start_error=RuntimeError("spawn failed"))
    context = DownloadContext(process)
    patch_download_context(monkeypatch, context)
    catalog = ModelCatalog(tmp_path / "managed")

    with pytest.raises(RuntimeError, match="spawn failed"):
        catalog.install("tiny")

    assert context.queue.events == ["cancel_join_thread", "close"]
    assert list(catalog.downloads.iterdir()) == []


def test_timed_out_download_kills_stubborn_process_and_disposes_queue(
    tmp_path, monkeypatch
):
    process = DownloadProcess(alive=True)
    context = DownloadContext(process)
    patch_download_context(monkeypatch, context)
    catalog = ModelCatalog(tmp_path / "managed")

    with pytest.raises(TimeoutError, match="model download timed out after -1 seconds: tiny"):
        catalog.install("tiny", timeout_seconds=-1)

    assert process.events == ["start", "terminate", ("join", 5), "kill", ("join", 2)]
    assert context.queue.events == ["cancel_join_thread", "close"]
    assert list(catalog.downloads.iterdir()) == []


def test_download_worker_error_disposes_queue_and_owned_staging(tmp_path, monkeypatch):
    process = DownloadProcess(alive=False, exitcode=1)
    context = DownloadContext(process, {"ok": False, "error": "network failed"})
    patch_download_context(monkeypatch, context)
    catalog = ModelCatalog(tmp_path / "managed")

    with pytest.raises(RuntimeError, match="cannot install model tiny: network failed"):
        catalog.install("tiny")

    assert process.events == ["start"]
    assert context.queue.events == [("get", 2), "cancel_join_thread", "close"]
    assert list(catalog.downloads.iterdir()) == []


def test_successful_download_disposes_queue_without_terminating_exited_worker(
    tmp_path, monkeypatch
):
    process = DownloadProcess(alive=False)
    context = DownloadContext(process, {"ok": True})
    patch_download_context(monkeypatch, context)
    catalog = ModelCatalog(tmp_path / "managed")
    promoted = {}

    def promote(model_id, temporary, *, source, revision):
        promoted.update(
            model_id=model_id,
            temporary=temporary,
            source=source,
            revision=revision,
        )
        return {"id": model_id}

    monkeypatch.setattr(catalog, "_promote", promote)

    assert catalog.install("tiny", revision="rev-1") == {"id": "tiny"}

    assert process.events == ["start"]
    assert context.queue.events == [("get", 2), "cancel_join_thread", "close"]
    assert not promoted["temporary"].exists()
    assert list(catalog.downloads.iterdir()) == []


def test_uninstalled_model_has_actionable_error(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    with pytest.raises(FileNotFoundError, match="models install tiny"):
        catalog.resolve("tiny")


def test_reconcile_adopts_complete_orphan_and_preserves_provenance(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    orphan = local_model(catalog.root / "tiny-orphan")
    result = catalog.reconcile()
    entry = catalog.get("tiny-orphan")
    assert result["status"] == "PASS"
    assert result["action"] == "repaired"
    assert result["adopted"] == ["tiny-orphan"]
    assert entry is not None
    assert entry["source"] == "reconciled"
    assert entry["revision"] is None
    assert entry["reconciled"] is True
    assert catalog.verify("tiny-orphan")["status"] == "PASS"
    assert catalog.resolve("tiny-orphan") == orphan


def test_reconcile_drops_only_provably_absent_manifest_entry(tmp_path):
    source = local_model(tmp_path / "source")
    catalog = ModelCatalog(tmp_path / "managed")
    catalog.import_local("missing", source)
    shutil.rmtree(catalog.root / "missing")
    result = catalog.reconcile()
    assert result["dropped"] == ["missing"]
    assert catalog.list() == []


def test_reconcile_blocks_incomplete_orphan_without_mutating_it(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    incomplete = catalog.root / "broken"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}", encoding="utf-8")
    result = catalog.reconcile()
    blocked = result["blocked"]
    assert result["action"] == "attention"
    assert result["adopted"] == []
    assert blocked == [
        {
            "id": "broken",
            "path": str(incomplete),
            "reason": "model directory is incomplete; model.bin and config.json are required",
        }
    ]
    assert set(blocked[0]) == {"id", "path", "reason"}
    assert incomplete.is_dir()


def test_reconcile_blocks_orphan_model_symlink(tmp_path):
    source = local_model(tmp_path / "source")
    catalog = ModelCatalog(tmp_path / "managed")
    symlink = catalog.root / "linked-model"
    try:
        symlink.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlink creation denied: {exc}")
        raise

    result = catalog.reconcile()

    assert result["action"] == "attention"
    assert result["adopted"] == []
    assert result["blocked"] == [
        {
            "id": "linked-model",
            "path": str(symlink),
            "reason": "model path is a symlink",
        }
    ]
    assert set(result["blocked"][0]) == {"id", "path", "reason"}
    assert symlink.is_symlink()


def test_reconcile_blocks_windows_junction_without_touching_target(tmp_path):
    target = local_model(tmp_path / "outside")
    catalog = ModelCatalog(tmp_path / "managed")
    junction = catalog.root / "linked-model"
    make_junction(junction, target)

    result = catalog.reconcile()

    assert result["blocked"] == [
        {
            "id": "linked-model",
            "path": str(junction),
            "reason": "model path is a reparse point",
        }
    ]
    assert junction.is_dir()
    assert target.is_dir()
    assert (target / "model.bin").read_bytes() == b"fixture model"
    assert catalog.list() == []


@pytest.mark.parametrize("managed", [False, True])
def test_remove_rejects_windows_junction_without_touching_target(tmp_path, managed):
    catalog = ModelCatalog(tmp_path / "managed")
    target = local_model(catalog.root / "outside")
    junction = catalog.root / "linked-model"
    make_junction(junction, target)
    if managed:
        catalog._save(
            {"version": 1, "models": [{"id": "linked-model", "path": "linked-model"}]}
        )

    with pytest.raises(ValueError, match="reparse"):
        catalog.remove("linked-model", confirmed=True)
    assert junction.is_dir()
    assert target.is_dir()
    assert (target / "model.bin").read_bytes() == b"fixture model"


def test_reconcile_keeps_staging_with_descendant_junction(tmp_path, monkeypatch):
    target = local_model(tmp_path / "outside")
    catalog = ModelCatalog(tmp_path / "managed")
    staging = catalog.downloads / ("tiny-" + "c" * 32)
    staging.mkdir()
    (staging / "partial").write_bytes(b"x")
    make_junction(staging / "payload", target)
    now = time.time() + 3 * 24 * 60 * 60
    monkeypatch.setattr(model_catalog_module.time, "time", lambda: now)

    result = catalog.reconcile()

    assert staging.name in result["staging_kept"]
    assert staging.exists()
    assert target.is_dir()
    assert (target / "model.bin").read_bytes() == b"fixture model"


def test_reconcile_keeps_old_staging_root_with_fresh_nested_file(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    staging = catalog.downloads / ("tiny-" + "d" * 32)
    staging.mkdir()
    fresh_file = staging / "partial"
    fresh_file.write_bytes(b"x")
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(staging, (old, old))

    result = catalog.reconcile()

    assert staging.name in result["staging_kept"]
    assert staging.exists()
    assert fresh_file.exists()


def test_unmanaged_remove_preserves_existing_manifest_bytes_and_mtime(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    catalog._save({"version": 1, "models": []})
    before_bytes = catalog.catalog_path.read_bytes()
    before_mtime = catalog.catalog_path.stat().st_mtime_ns
    local_model(catalog.root / "stray")

    result = catalog.remove("stray", confirmed=True)

    assert result == {"removed": True, "id": "stray", "unmanaged": True}
    assert catalog.catalog_path.read_bytes() == before_bytes
    assert catalog.catalog_path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("payload", [b"{not-json", b'{"version":999,"models":[]}'])
def test_reconcile_quarantines_bad_manifest_and_rebuilds(tmp_path, payload):
    catalog = ModelCatalog(tmp_path / "managed")
    local_model(catalog.root / "recoverable")
    catalog.catalog_path.write_bytes(payload)
    result = catalog.reconcile()
    quarantine = Path(result["catalog_quarantined"])
    assert quarantine.read_bytes() == payload
    assert catalog.verify("recoverable")["status"] == "PASS"


def test_reconcile_is_idempotent_and_does_not_rehash_catalogued_models(
    tmp_path, monkeypatch
):
    source = local_model(tmp_path / "source")
    catalog = ModelCatalog(tmp_path / "managed")
    catalog.import_local("stable", source)
    before = catalog.catalog_path.stat().st_mtime_ns
    calls = 0
    original = model_catalog_module.sha256_file

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(model_catalog_module, "sha256_file", counted)
    result = catalog.reconcile()
    assert result["action"] == "none"
    assert calls == 0
    assert catalog.catalog_path.stat().st_mtime_ns == before


def test_reconcile_clean_profile_does_not_write_manifest(tmp_path, monkeypatch):
    catalog = ModelCatalog(tmp_path / "managed")
    monkeypatch.setattr(
        catalog,
        "_save",
        lambda _payload: pytest.fail("healthy reconciliation must not write"),
    )
    result = catalog.reconcile()
    assert result == {
        "status": "PASS",
        "action": "none",
        "adopted": [],
        "dropped": [],
        "blocked": [],
        "staging_removed": [],
        "staging_kept": [],
        "residue_removed": [],
        "catalog_quarantined": None,
    }


def test_reconcile_removes_only_stale_well_formed_staging(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    stale = catalog.downloads / ("tiny-" + "a" * 32)
    fresh = catalog.downloads / ("small-" + "b" * 32)
    malformed = catalog.downloads / "keep-me"
    for path in (stale, fresh, malformed):
        path.mkdir()
        (path / "partial").write_bytes(b"x")
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(stale / "partial", (old, old))
    os.utime(stale, (old, old))

    result = catalog.reconcile()

    assert result["staging_removed"] == [stale.name]
    assert set(result["staging_kept"]) == {fresh.name, malformed.name}
    assert not stale.exists()
    assert fresh.exists() and malformed.exists()


def test_reconcile_handles_manifest_residue_by_age(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    catalog._save({"version": 1, "models": []})
    old = catalog.root / "catalog.json.tmp"
    fresh = catalog.root / ("catalog.json." + "a" * 32 + ".tmp")
    old.write_text("old", encoding="utf-8")
    fresh.write_text("fresh", encoding="utf-8")
    timestamp = time.time() - 301
    os.utime(old, (timestamp, timestamp))

    before = catalog.catalog_path.read_bytes()
    result = catalog.reconcile()

    assert result["residue_removed"] == [old.name]
    assert fresh.exists()
    assert catalog.catalog_path.read_bytes() == before


def test_remove_accepts_unmanaged_directory_only_with_confirmation(tmp_path):
    catalog = ModelCatalog(tmp_path / "managed")
    target = local_model(catalog.root / "stray")

    with pytest.raises(ValueError, match="--yes"):
        catalog.remove("stray")

    assert catalog.remove("stray", confirmed=True) == {
        "removed": True,
        "id": "stray",
        "unmanaged": True,
    }
    assert not target.exists()


def test_atomic_catalog_save_removes_failed_temporary_file(tmp_path, monkeypatch):
    catalog = ModelCatalog(tmp_path / "managed")
    original = Path.replace

    def fail_tmp(path, target):
        if path.name.startswith("catalog.json.") and path.name.endswith(".tmp"):
            raise OSError("replace failed")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_tmp)
    with pytest.raises(OSError, match="replace failed"):
        catalog._save({"version": 1, "models": []})
    assert list(catalog.root.glob("catalog.json*.tmp")) == []
