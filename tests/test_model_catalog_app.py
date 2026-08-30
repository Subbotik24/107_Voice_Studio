import shutil
from pathlib import Path

import pytest

from voice_studio import model_catalog as model_catalog_module
from voice_studio.model_catalog import ModelCatalog


def local_model(path: Path) -> Path:
    path.mkdir()
    (path / "model.bin").write_bytes(b"fixture model")
    (path / "config.json").write_text('{"model_type":"Whisper"}', encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return path


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
    assert result["action"] == "attention"
    assert result["adopted"] == []
    assert result["blocked"][0]["id"] == "broken"
    assert "model.bin" in result["blocked"][0]["reason"]
    assert incomplete.is_dir()


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
