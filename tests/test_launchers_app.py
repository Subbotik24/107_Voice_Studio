from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mac_launcher_has_fail_fast_preflight_and_first_run_install() -> None:
    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")

    assert "Python 3.11 or 3.12" in launcher
    assert "import tkinter, _tkinter" in launcher
    assert "first run only" in launcher
    assert "pip install --upgrade" not in launcher


def test_windows_launcher_has_fail_fast_preflight_and_first_run_install() -> None:
    launcher = (PROJECT_ROOT / "run_windows.bat").read_text(encoding="utf-8")

    assert "Python 3.11 or 3.12" in launcher
    assert "import tkinter, _tkinter" in launcher
    assert "first run only" in launcher
    assert "pip install --upgrade" not in launcher
    assert 'cd /d "%~dp0"' in launcher


def test_windows_one_click_builder_uses_isolated_environment() -> None:
    launcher = (PROJECT_ROOT / "build_windows_exe.bat").read_text(encoding="utf-8")

    assert ".venv-windows-build" in launcher
    assert "requirements-windows.lock" in launcher
    assert "--no-build-isolation --no-deps -e ." in launcher
    assert ".[dev,benchmark,package,cloud]" not in launcher
    assert "scripts\\build_windows.ps1" in launcher
    assert "Windows 10 or 11 x64" in launcher
