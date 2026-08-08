import hashlib
import json
import zipfile

from hermes_whisper.bundle import create_model_bundle, extract_model_bundle, verify_model_bundle


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_runtime_bundle_does_not_require_trainer_state(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(
        json.dumps({"model": {"languages": ["uk", "cs"]}}), encoding="utf-8"
    )
    (checkpoint / "tokenizer.json").write_text("{}", encoding="utf-8")
    (checkpoint / "model.pt").write_bytes(b"model-state")
    (checkpoint / "trainer.pt").write_bytes(b"trainer-state")
    files = {
        name: sha(checkpoint / name)
        for name in ("config.json", "tokenizer.json", "model.pt", "trainer.pt")
    }
    (checkpoint / "metadata.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "step": 17,
                "model_name": "unit-hermes",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "unit.hws"
    create_model_bundle(checkpoint, bundle)
    verified = verify_model_bundle(bundle)
    assert verified["model_name"] == "unit-hermes"
    with zipfile.ZipFile(bundle) as archive:
        assert "trainer.pt" not in archive.namelist()
        metadata = json.loads(archive.read("metadata.json"))
        assert "trainer.pt" not in metadata["files"]
        assert "trainer.pt" in metadata["checkpoint_files"]
    extracted = extract_model_bundle(bundle, tmp_path / "cache")
    assert not (extracted / "trainer.pt").exists()
    assert (extracted / "model.pt").read_bytes() == b"model-state"
    (extracted / "model.pt").write_bytes(b"tampered-cache")
    repaired = extract_model_bundle(bundle, tmp_path / "cache")
    assert repaired == extracted
    assert (repaired / "model.pt").read_bytes() == b"model-state"
