from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.create_release_manifest as manifest_module
from scripts.create_release_manifest import create_manifest
from scripts.generate_sbom import build_sbom

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_release_manifest_rejects_lexical_external_sbom_traversal(tmp_path):
    release = tmp_path / "release"
    (release / "sub").mkdir(parents=True)
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    external_sbom = write_sbom(tmp_path / "outside.json")
    traversal = release / "sub" / ".." / ".." / external_sbom.name

    with pytest.raises(ValueError, match="lexical traversal") as error:
        create_manifest(
            release,
            [artifact],
            traversal,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )
    assert str(tmp_path) not in str(error.value)


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


def test_macos_staging_passes_sbom_to_manifest_and_checksum() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_test_rc.sh").read_text(
        encoding="utf-8"
    )

    assert "--sbom \"$SBOM\"" in build_script
    assert '"voice-studio-sbom.cdx.json"' in build_script
    assert '"$PYTHON_BIN" scripts/generate_sbom.py' in build_script


def test_release_manifest_hashes_exact_validated_sbom_bytes(tmp_path, monkeypatch):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    sbom = write_sbom(release / "voice-studio-sbom.cdx.json")
    replacement = json.dumps(
        build_sbom(
            "beta-package==2.0\n",
            project_name="voice-studio",
            project_version="0.3.0rc1",
        )
    ).encode("utf-8")
    original_read_text = type(sbom).read_text

    def replace_before_reopen(path, *args, **kwargs):
        if path == sbom:
            sbom.write_bytes(replacement)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(sbom), "read_text", replace_before_reopen)
    manifest = create_manifest(
        release,
        [artifact],
        sbom,
        release_label="0.2.0-test-rc1",
        acceptance_result=result,
        repository_root=tmp_path,
    )

    validated_bytes = sbom.read_bytes()
    assert manifest["sbom"]["sha256"] == hashlib.sha256(validated_bytes).hexdigest()
    assert manifest["sbom"]["size"] == len(validated_bytes)


def test_release_manifest_rejects_sbom_path_swap_during_read(tmp_path, monkeypatch):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    sbom = write_sbom(release / "voice-studio-sbom.cdx.json")
    replacement = release / "replacement.json"
    replacement.write_text(
        json.dumps(
            build_sbom(
                "bravo==1\n",
                project_name="voice-studio",
                project_version="0.3.0rc1",
            )
        ),
        encoding="utf-8",
    )
    original_lstat = os.lstat
    target_lstat_calls = 0

    def swap_at_path_boundary(path):
        nonlocal target_lstat_calls
        if Path(path) == sbom:
            target_lstat_calls += 1
            if target_lstat_calls == 2:
                os.replace(replacement, sbom)
        info = original_lstat(path)
        return info

    monkeypatch.setattr(manifest_module.os, "lstat", swap_at_path_boundary)
    with pytest.raises(ValueError, match="changed") as error:
        create_manifest(
            release,
            [artifact],
            sbom,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )
    assert target_lstat_calls == 2
    assert str(tmp_path) not in str(error.value)


def test_release_manifest_rejects_sbom_symlink_without_private_path(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "app.whl"
    artifact.write_bytes(b"wheel")
    result = acceptance(tmp_path / "acceptance.json")
    target = release / "actual-sbom.json"
    write_sbom(target)
    sbom = release / "voice-studio-sbom.cdx.json"
    try:
        sbom.symlink_to(target.name)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(ValueError, match="symlink|reparse") as error:
        create_manifest(
            release,
            [artifact],
            sbom,
            release_label="0.2.0-test-rc1",
            acceptance_result=result,
            repository_root=tmp_path,
        )
    assert str(tmp_path) not in str(error.value)
