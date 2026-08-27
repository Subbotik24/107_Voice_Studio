from pathlib import Path

import pytest

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
