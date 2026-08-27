from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_profile_collects_runtime_and_excludes_dev_modules() -> None:
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(
        encoding="utf-8"
    )

    assert 'collect_submodules("voice_studio")' not in spec
    assert 'collect_submodules("faster_whisper")' in spec
    assert 'collect_data_files("faster_whisper")' in spec
    assert 'collect_submodules("openai")' in spec
    for module in ("pytest", "pygments"):
        assert f'"{module}"' in spec
    assert "runtime_hooks=[]" in spec

def test_frozen_launcher_enables_multiprocessing_support() -> None:
    launcher = (PROJECT_ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")

    assert "multiprocessing.freeze_support()" in launcher
    assert "VOICE_STUDIO_RUNTIME_PROBE_OUTPUT" in launcher
    assert "VOICE_STUDIO_TRANSCRIPTION_PROBE_MODEL" in launcher
    assert "frozen_worker_roundtrip" in launcher
    assert "development_only_excluded" in launcher


def test_test_rc_build_is_atomic_and_refuses_overwrite() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_test_rc.sh").read_text(
        encoding="utf-8"
    )

    assert 'RELEASE_LABEL="0.3.0-test-rc1"' in build_script
    assert "refusing to overwrite existing Test RC" in build_script
    assert "mktemp -d" in build_script
    assert 'mv "$STAGE_DIRECTORY" "$FINAL_DIRECTORY"' in build_script
    assert "VOICE_STUDIO_ACCEPTANCE_RESULT" in build_script
    assert 'codesign --force --deep --sign - "$FINAL_APP"' in build_script
    assert '"$PYTHON_BIN" -m build' in build_script
    assert '"$PYTHON_BIN" -m pip wheel' not in build_script


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
    assert "Move-Item" in build_script
    assert '".wheel-source"' in build_script
    assert "all(n.split('/', 1)[0].startswith('voice_studio')" in build_script


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
