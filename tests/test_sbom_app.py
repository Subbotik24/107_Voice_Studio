import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.generate_sbom import (
    build_sbom,
    parse_locked_components,
    validate_sbom_document,
)


def test_sbom_is_deterministic_across_line_endings(tmp_path):
    lf = "# lock\nAlpha_Pkg==2.0\nzeta.pkg==1.0\n"
    first = build_sbom(lf, project_name="voice-studio", project_version="0.3.0rc1")
    second = build_sbom(
        lf.replace("\n", "\r\n"),
        project_name="voice-studio",
        project_version="0.3.0rc1",
    )
    assert first == second
    assert [item["name"] for item in first["components"]] == ["alpha-pkg", "zeta-pkg"]
    assert str(tmp_path) not in json.dumps(first)


def test_parser_rejects_duplicates_after_normalization():
    with pytest.raises(ValueError, match="duplicate"):
        parse_locked_components("Alpha_Pkg==1\nalpha-pkg==2\n")


@pytest.mark.parametrize(
    "row",
    [
        "alpha>=1",
        "alpha==1; python_version >= '3.11'",
        "alpha[extra]==1",
        "-r other.txt",
        "alpha @ https://example.test/alpha.whl",
        "-e .",
        "alpha==",
        "alpha==1 # inline comment",
        "../alpha==1",
    ],
)
def test_parser_rejects_non_exact_requirement_rows(row):
    with pytest.raises(ValueError):
        parse_locked_components(row + "\n")


def test_parser_returns_sorted_frozen_components():
    components = parse_locked_components("Zeta_pkg==1\nalpha.pkg==2\n# comment\n")
    assert [(item.name, item.version) for item in components] == [
        ("alpha-pkg", "2"),
        ("zeta-pkg", "1"),
    ]
    with pytest.raises(AttributeError):
        components[0].name = "changed"


def test_sbom_has_exact_profile_and_lock_digest():
    lock = "zeta.pkg==1.0\nAlpha_Pkg==2.0\n"
    document = build_sbom(lock, project_name="voice-studio", project_version="0.3.0rc1")
    canonical = b"alpha-pkg==2.0\nzeta-pkg==1.0\n"
    assert document["$schema"] == "https://cyclonedx.org/schema/bom-1.6.schema.json"
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.6"
    assert document["version"] == 1
    assert document["metadata"] == {
        "component": {
            "name": "voice-studio",
            "type": "application",
            "version": "0.3.0rc1",
        }
    }
    assert document["properties"] == [
        {"name": "voice-studio:lock-sha256", "value": hashlib.sha256(canonical).hexdigest()},
        {"name": "voice-studio:sbom-scope", "value": "windows-x64-release-environment"},
        {"name": "voice-studio:source-lock", "value": "requirements-windows.lock"},
    ]
    assert document["components"] == [
        {
            "bom-ref": "pkg:pypi/alpha-pkg@2.0",
            "name": "alpha-pkg",
            "purl": "pkg:pypi/alpha-pkg@2.0",
            "type": "library",
            "version": "2.0",
        },
        {
            "bom-ref": "pkg:pypi/zeta-pkg@1.0",
            "name": "zeta-pkg",
            "purl": "pkg:pypi/zeta-pkg@1.0",
            "type": "library",
            "version": "1.0",
        },
    ]
    validate_sbom_document(document)


def test_validator_rejects_extra_keys_unsorted_and_paths():
    document = build_sbom(
        "alpha==1\nbeta==1\n", project_name="voice-studio", project_version="0.3.0rc1"
    )
    extra = dict(document)
    extra["unexpected"] = True
    with pytest.raises(ValueError):
        validate_sbom_document(extra)

    unsorted = dict(document)
    unsorted["components"] = list(reversed(document["components"]))
    with pytest.raises(ValueError):
        validate_sbom_document(unsorted)

    pathful = dict(document)
    pathful["metadata"] = {
        "component": {"name": "C:\\secret", "type": "application", "version": "1"}
    }
    with pytest.raises(ValueError):
        validate_sbom_document(pathful)


def test_cli_preserves_existing_output_when_input_is_invalid(tmp_path):
    lock = tmp_path / "bad.lock"
    output = tmp_path / "sbom.json"
    lock.write_text("alpha>=1\n", encoding="utf-8")
    output.write_text("keep me\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_sbom.py",
            "--lock",
            str(lock),
            "--project-name",
            "voice-studio",
            "--project-version",
            "0.3.0rc1",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == "keep me\n"


def test_repository_lock_contains_exact_58_components():
    lock_path = Path(__file__).parents[1] / "requirements-windows.lock"
    components = parse_locked_components(lock_path.read_text(encoding="utf-8"))
    assert len(components) == 58
    assert any(item.name == "faster-whisper" and item.version == "1.2.1" for item in components)
