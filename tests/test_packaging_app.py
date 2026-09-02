from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.release_filesystem as release_filesystem

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_profile_collects_runtime_and_excludes_dev_modules() -> None:
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(
        encoding="utf-8"
    )

    assert 'collect_submodules("voice_studio")' not in spec
    assert 'collect_submodules("faster_whisper")' in spec
    assert 'collect_data_files("faster_whisper")' in spec
    assert 'collect_submodules("openai")' in spec
    assert 'collect_submodules("cryptography")' in spec
    for module in ("pytest", "pygments"):
        assert f'"{module}"' in spec
    assert "runtime_hooks=[]" in spec


def test_canonical_help_is_included_in_wheel_and_frozen_distributions() -> None:
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(
        encoding="utf-8"
    )
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    windows_build = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert 'project_root / "docs" / "help"' in spec
    assert '"docs/help"' in spec
    assert '[tool.setuptools.data-files]' in pyproject
    assert '"share/voice-studio/help"' in pyproject
    assert '"docs/help/*.md"' in pyproject
    assert '"docs/help/*.json"' in pyproject
    for language in ("uk", "cs", "en"):
        assert f'"share/voice-studio/help/{language}"' in pyproject
        assert f'"docs/help/{language}/*.md"' in pyproject
    assert 'Join-Path $ProjectRoot "docs/help"' in windows_build
    assert 'Join-Path $WheelSourceDirectory "docs/help"' in windows_build


def test_quality_gate_validates_canonical_help() -> None:
    quality_gate = (PROJECT_ROOT / "scripts" / "quality_gate.ps1").read_text(
        encoding="utf-8"
    )

    assert "scripts/check_help.py" in quality_gate
    for workflow_name in ("ci.yml", "release.yml"):
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / workflow_name
        ).read_text(encoding="utf-8")
        assert "python scripts/check_help.py" in workflow

def test_frozen_launcher_enables_multiprocessing_support() -> None:
    launcher = (PROJECT_ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")

    assert "multiprocessing.freeze_support()" in launcher
    assert "VOICE_STUDIO_RUNTIME_PROBE_OUTPUT" in launcher
    assert "VOICE_STUDIO_TRANSCRIPTION_PROBE_MODEL" in launcher
    assert "frozen_worker_roundtrip" in launcher
    assert '"voice_studio.profiles"' in launcher
    assert '"voice_studio.engines.ollama_audio"' in launcher
    assert '"voice_studio.help_content"' in launcher
    assert "development_only_excluded" in launcher


def test_test_rc_build_is_atomic_and_refuses_overwrite() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_test_rc.sh").read_text(
        encoding="utf-8"
    )

    assert 'RELEASE_LABEL="0.3.0-test-rc1"' in build_script
    assert "refusing to overwrite existing Test RC" in build_script
    assert "mktemp -d" in build_script
    assert "scripts/release_filesystem.py promote" in build_script
    assert "VOICE_STUDIO_ACCEPTANCE_RESULT" in build_script
    assert 'codesign --force --deep --sign - "$FINAL_APP"' in build_script
    assert '"$PYTHON_BIN" -m build' in build_script
    assert '"$PYTHON_BIN" -m pip wheel' not in build_script


def test_the_project_version_is_identical_in_every_release_surface() -> None:
    """pyproject, package, PyInstaller bundle, manifest script and build scripts agree."""

    import tomllib

    import voice_studio

    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    assert voice_studio.__version__ == version
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(encoding="utf-8")
    assert f'"CFBundleShortVersionString": "{version}"' in spec
    manifest = (PROJECT_ROOT / "scripts" / "create_release_manifest.py").read_text(
        encoding="utf-8"
    )
    assert f'_PROJECT_VERSION = "{version}"' in manifest
    for script_name in ("build_windows.ps1", "build_test_rc.sh"):
        script = (PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert f'--project-version "{version}"' in script


@pytest.mark.parametrize(
    "script_name",
    ["build_windows.ps1", "build_test_rc.sh"],
)
def test_release_staging_generates_sbom_from_repository_lock(script_name: str) -> None:
    build_script = (PROJECT_ROOT / "scripts" / script_name).read_text(
        encoding="utf-8"
    )

    assert "scripts/generate_sbom.py" in build_script
    assert "requirements-windows.lock" in build_script
    assert "voice-studio" in build_script
    assert "0.3.0rc1" in build_script
    assert "voice-studio-sbom.cdx.json" in build_script
    assert "--project-name" in build_script
    assert "--project-version" in build_script
    assert "pip freeze" not in build_script
    assert "pip list" not in build_script
    assert "https://" not in build_script
    assert "http://" not in build_script

    if script_name == "build_windows.ps1":
        assert '$SBOM = Join-Path $StageDirectory "voice-studio-sbom.cdx.json"' in build_script
        assert re.search(r"--output[ \t]+\$SBOM(?:\r?\n|$)", build_script)
    else:
        assert 'SBOM="$STAGE_DIRECTORY/voice-studio-sbom.cdx.json"' in build_script
        assert '--output "$SBOM"' in build_script


def test_windows_release_checksums_the_staged_sbom() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "$ChecksumTargets" in build_script
    assert "$SBOM" in build_script
    assert "$ChecksumTargets = @($ArchivePath, $RuntimeProbe, $ReadmePath, $SBOM)" in build_script


def test_release_promotion_helper_moves_exact_directory_without_clobber(tmp_path) -> None:
    stage = tmp_path / "stage"
    final = tmp_path / "final"
    stage.mkdir()
    (stage / "artifact.txt").write_text("staged", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release_filesystem.py",
            "promote",
            "--source",
            str(stage),
            "--destination",
            str(final),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not stage.exists()
    assert (final / "artifact.txt").read_text(encoding="utf-8") == "staged"


def test_release_promotion_helper_refuses_existing_destination_without_nesting(
    tmp_path,
) -> None:
    stage = tmp_path / "stage"
    final = tmp_path / "final"
    stage.mkdir()
    final.mkdir()
    (stage / "staged.txt").write_text("staged", encoding="utf-8")
    (final / "existing.txt").write_text("existing", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release_filesystem.py",
            "promote",
            "--source",
            str(stage),
            "--destination",
            str(final),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "destination already exists" in result.stderr
    assert (stage / "staged.txt").read_text(encoding="utf-8") == "staged"
    assert (final / "existing.txt").read_text(encoding="utf-8") == "existing"
    assert not (final / stage.name).exists()
    assert str(tmp_path) not in result.stderr


def test_release_promotion_primitive_refuses_destination_created_after_precheck(
    tmp_path, monkeypatch
) -> None:
    stage = tmp_path / "stage"
    final = tmp_path / "final"
    stage.mkdir()
    (stage / "staged.txt").write_text("staged", encoding="utf-8")
    original_lstat = release_filesystem.os.lstat
    raced = False

    def create_destination_during_precheck(path):
        nonlocal raced
        if Path(path) == final and not raced:
            raced = True
            final.mkdir()
            (final / "existing.txt").write_text("existing", encoding="utf-8")
            raise FileNotFoundError
        return original_lstat(path)

    monkeypatch.setattr(release_filesystem.os, "lstat", create_destination_during_precheck)
    with pytest.raises(FileExistsError, match="destination already exists") as error:
        release_filesystem.promote_directory_no_replace(stage, final)

    assert raced
    assert (stage / "staged.txt").read_text(encoding="utf-8") == "staged"
    assert (final / "existing.txt").read_text(encoding="utf-8") == "existing"
    assert str(tmp_path) not in str(error.value)


def test_release_promotion_refuses_unsupported_platform_without_moving(
    tmp_path, monkeypatch
) -> None:
    stage = tmp_path / "stage"
    final = tmp_path / "final"
    stage.mkdir()
    (stage / "staged.txt").write_text("staged", encoding="utf-8")
    monkeypatch.setattr(release_filesystem.sys, "platform", "unsupported-os")

    with pytest.raises(ValueError, match="platform is unsupported") as error:
        release_filesystem.promote_directory_no_replace(stage, final)

    assert (stage / "staged.txt").read_text(encoding="utf-8") == "staged"
    assert not final.exists()
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    "script_name, expected_call",
    [
        (
            "build_windows.ps1",
            "scripts/release_filesystem.py promote --source $StageDirectory "
            "--destination $FinalDirectory",
        ),
        (
            "build_test_rc.sh",
            "scripts/release_filesystem.py promote \\\n"
            '  --source "$STAGE_DIRECTORY" \\\n'
            '  --destination "$FINAL_DIRECTORY"',
        ),
    ],
)
def test_release_builders_use_atomic_no_replace_promotion(
    script_name: str, expected_call: str
) -> None:
    build_script = (PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert expected_call in build_script
    assert 'mv "$STAGE_DIRECTORY" "$FINAL_DIRECTORY"' not in build_script
    assert "Move-Item -Path $StageDirectory -Destination $FinalDirectory" not in build_script


def test_windows_build_is_atomic_and_runtime_verified() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "sys.platform == 'win32'" in build_script
    assert "sys.maxsize > 2**32" in build_script
    assert "quality_gate.ps1" in build_script
    assert "refusing to overwrite existing Windows Test RC" in build_script
    assert "VOICE_STUDIO_RUNTIME_PROBE_OUTPUT" in build_script
    assert "runtime-probe.json" in build_script
    assert "Compress-Archive" in build_script
    assert "Security.Cryptography.SHA256" in build_script
    assert "Get-FileHash" not in build_script
    assert "scripts/release_filesystem.py promote" in build_script
    assert '".wheel-source"' in build_script
    assert "all(n.split('/', 1)[0].startswith('voice_studio')" in build_script
    assert "RequiredWheelSuffixes" in build_script
    assert "voice_studio/profiles.py" in build_script
    assert "voice_studio/engines/ollama_audio.py" in build_script
    assert "RequiredFrozenHelpPaths" in build_script
    for language in ("uk", "cs", "en"):
        assert f'"{language}\\quick-start.md"' in build_script


def test_windows_quality_gate_accepts_an_explicit_python_executable() -> None:
    quality_gate = (PROJECT_ROOT / "scripts" / "quality_gate.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string]$FilePath' in quality_gate
    assert '[string[]]$Arguments' in quality_gate
    assert "& $FilePath @Arguments" in quality_gate


def test_windows_batch_uses_a_clean_python_312_locked_environment() -> None:
    batch = (PROJECT_ROOT / "build_windows_exe.bat").read_text(encoding="utf-8")
    lock = (PROJECT_ROOT / "requirements-windows.lock").read_text(encoding="utf-8")

    assert "py -3.12" in batch
    assert "py -3.11" not in batch
    assert '.venv\\Scripts\\python.exe' in batch
    assert "-m venv --clear .venv-windows-build" in batch
    assert "--only-binary=:all: --requirement requirements-windows.lock" in batch
    assert "--no-build-isolation --no-deps -e ." in batch
    assert "-m pip check" in batch
    requirements = [
        line.strip() for line in lock.splitlines() if line.strip() and not line.startswith("#")
    ]
    assert requirements
    assert all("==" in requirement for requirement in requirements)
    assert any(requirement.casefold().startswith("pyinstaller==") for requirement in requirements)
    assert any(requirement.casefold().startswith("build==") for requirement in requirements)
