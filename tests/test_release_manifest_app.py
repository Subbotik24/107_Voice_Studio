from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from scripts.create_release_manifest import create_manifest
from scripts.generate_sbom import build_sbom


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


def write_sbom(path, *, project_version="0.3.0rc1"):
    path.write_text(
        json.dumps(
            build_sbom(
                "alpha==1\n",
                project_name="voice-studio",
                project_version=project_version,
            )
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
    sbom = write_sbom(release / "voice-studio-sbom.cdx.json")

    manifest = create_manifest(
        release,
        [wheel, app],
        sbom,
        release_label="0.2.0-test-rc1",
        acceptance_result=result,
        repository_root=tmp_path,
    )

    assert manifest["production"] is False
    assert manifest["accuracy_claim"] == "none"
    assert manifest["repository_metadata"] == "absent"
    assert [item["path"] for item in manifest["artifacts"]] == ["app.whl", "App.app"]
    assert manifest["sbom"] == {
        "format": "CycloneDX",
        "spec_version": "1.6",
        "path": "voice-studio-sbom.cdx.json",
        "sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
        "size": sbom.stat().st_size,
    }
    assert manifest["acceptance"]["tasks"] == 50
    assert str(tmp_path) not in json.dumps(manifest)


def test_release_manifest_rejects_short_acceptance(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json", tasks=49)
    sbom = write_sbom(release / "voice-studio-sbom.cdx.json")

    with pytest.raises(ValueError, match="50-task"):
        create_manifest(
            release,
            [artifact],
            sbom,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )


def test_release_manifest_rejects_external_sbom(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    external_sbom = write_sbom(tmp_path / "voice-studio-sbom.cdx.json")

    with pytest.raises(ValueError, match="inside release directory"):
        create_manifest(
            release,
            [artifact],
            external_sbom,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "case, expected_exception, message",
    [
        ("malformed", json.JSONDecodeError, None),
        ("invalid", ValueError, "SBOM"),
        ("wrong-version", ValueError, "application version"),
        ("directory", ValueError, "regular file"),
        ("missing", FileNotFoundError, None),
    ],
)
def test_release_manifest_rejects_invalid_sbom_inputs(
    tmp_path, case, expected_exception, message
):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    sbom = release / "voice-studio-sbom.cdx.json"
    if case == "malformed":
        sbom.write_text("{not-json", encoding="utf-8")
    elif case == "invalid":
        sbom.write_text(json.dumps({}), encoding="utf-8")
    elif case == "wrong-version":
        write_sbom(sbom, project_version="0.3.0")
    elif case == "directory":
        sbom.mkdir()
    else:
        sbom = release / "missing-sbom.json"

    with pytest.raises(expected_exception, match=message):
        create_manifest(
            release,
            [artifact],
            sbom,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )


def test_release_manifest_cli_exposes_required_sbom_option():
    result = subprocess.run(
        [sys.executable, "scripts/create_release_manifest.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--sbom SBOM" in result.stdout
