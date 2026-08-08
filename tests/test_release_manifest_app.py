from __future__ import annotations

import json

import pytest

from scripts.create_release_manifest import create_manifest


def acceptance(path, *, tasks=50):
    path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "tasks": tasks,
                "crashes": 0,
                "originals_unchanged": True,
                "storage_audit": {"status": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_release_manifest_has_relative_artifacts_and_acceptance_evidence(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    wheel = release / "app.whl"
    wheel.write_bytes(b"wheel")
    app = release / "App.app"
    app.mkdir()
    (app / "runtime").write_bytes(b"runtime")
    result = acceptance(tmp_path / "acceptance.json")

    manifest = create_manifest(
        release,
        [wheel, app],
        release_label="0.2.0-test-rc1",
        acceptance_result=result,
        repository_root=tmp_path,
    )

    assert manifest["production"] is False
    assert manifest["accuracy_claim"] == "none"
    assert manifest["repository_metadata"] == "absent"
    assert [item["path"] for item in manifest["artifacts"]] == ["app.whl", "App.app"]
    assert manifest["acceptance"]["tasks"] == 50
    assert str(tmp_path) not in json.dumps(manifest)


def test_release_manifest_rejects_short_acceptance(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json", tasks=49)

    with pytest.raises(ValueError, match="50-task"):
        create_manifest(
            release,
            [artifact],
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )
