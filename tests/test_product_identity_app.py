from __future__ import annotations

import importlib
import subprocess
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_package_and_cli_use_voice_studio_identity() -> None:
    assert importlib.util.find_spec("voice_studio") is not None
    cli = importlib.import_module("voice_studio.cli")

    parser = cli.build_parser()

    assert parser.prog == "voice-studio"
    assert parser.description == "Privacy-first local desktop transcription."


def test_distribution_exposes_only_voice_studio_entry_points() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["name"] == "voice-studio"
    assert project["scripts"] == {"voice-studio": "voice_studio.cli:main"}
    assert project["gui-scripts"] == {"voice-studio-gui": "voice_studio.app:main"}


def test_product_contains_only_supported_transcription_engines() -> None:
    models = importlib.import_module("voice_studio.models")

    assert models.SUPPORTED_ENGINES == ("faster-whisper", "openai-cloud")
    packages = {
        path.parent.name
        for path in (PROJECT_ROOT / "src").glob("*/__init__.py")
    }
    assert packages == {"voice_studio"}


def test_windows_packaging_targets_the_standalone_application() -> None:
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(encoding="utf-8")
    launcher = (PROJECT_ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")
    assert 'name="voice studio"' in spec.lower()
    assert 'collect_submodules("faster_whisper")' in spec
    assert '"faster_whisper"' in launcher


def test_tracked_text_files_do_not_contain_the_retired_product_name() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    retired_identifiers = (
        "her" + "mes",
        ".h" + "ws",
        ".h" + "vs-backup",
        "h" + "vs_",
    )
    violations: list[str] = []
    for relative in tracked:
        path = PROJECT_ROOT / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        normalized = content.casefold()
        if any(identifier in normalized for identifier in retired_identifiers):
            violations.append(relative)
    assert violations == []


def test_release_docs_do_not_reference_deleted_audit_documents() -> None:
    deleted_references = (
        "docs/CONTINUATION_PLAN_0.4.md",
        "docs/PROJECT_AUDIT_STATUS.md",
    )
    for relative in (
        "IMPLEMENTATION_STATUS.md",
        "ROADMAP.md",
        "SECURITY.md",
        "tests/test_gui_contract_app.py",
    ):
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert all(reference not in text for reference in deleted_references)
