from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mac_launcher_has_fail_fast_preflight_and_first_run_install() -> None:
    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")

    assert "Python 3.11 or 3.12" in launcher
    assert "import tkinter, _tkinter" in launcher
    assert "first run only" in launcher
    assert "pip install --upgrade" not in launcher


def test_mac_launcher_always_runs_the_checked_out_source_tree() -> None:
    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")

    assert 'export PYTHONPATH="$PWD/src"' in launcher
    assert "python -m voice_studio gui" in launcher
    assert "voice-studio gui" not in launcher.replace("-m voice_studio gui", "")


def test_windows_launcher_has_fail_fast_preflight_and_first_run_install() -> None:
    launcher = (PROJECT_ROOT / "run_windows.bat").read_text(encoding="utf-8")

    assert "Python 3.11 or 3.12" in launcher
    assert "import tkinter, _tkinter" in launcher
    assert "first run only" in launcher
    assert "pip install --upgrade" not in launcher
    assert 'cd /d "%~dp0"' in launcher


def test_launchers_self_update_from_origin_main_and_survive_offline() -> None:
    for name in ("run_windows.bat", "run_mac.command"):
        launcher = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "git fetch origin" in launcher
        assert "git checkout -f -B main origin/main" in launcher
        assert "starting the current version" in launcher.lower()


def test_launchers_never_hang_on_a_git_credential_prompt() -> None:
    for name in ("run_windows.bat", "run_mac.command"):
        launcher = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "GIT_TERMINAL_PROMPT=0" in launcher
        fetch_index = launcher.index("git fetch origin")
        prompt_index = launcher.index("GIT_TERMINAL_PROMPT=0")
        assert prompt_index < fetch_index


def test_launchers_survive_a_failed_checkout_and_still_launch() -> None:
    for name in ("run_windows.bat", "run_mac.command"):
        launcher = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "Update could not be applied - starting the current version." in launcher
        checkout_index = launcher.index("git checkout -f -B main origin/main")
        fallback_index = launcher.index("Update could not be applied")
        assert fallback_index > checkout_index


def test_launchers_self_update_is_opt_in_and_off_by_default() -> None:
    """A launch must not contact the network unless the user asked for it."""

    mac = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")
    windows = (PROJECT_ROOT / "run_windows.bat").read_text(encoding="utf-8")
    assert '[ "${VOICE_STUDIO_AUTO_UPDATE:-0}" = "1" ]' in mac
    assert 'if "%VOICE_STUDIO_AUTO_UPDATE%"=="1" if exist ".git"' in windows
    for launcher in (mac, windows):
        assert launcher.index("VOICE_STUDIO_AUTO_UPDATE") < launcher.index("git fetch origin")


def test_launchers_warn_before_discarding_local_edits() -> None:
    for name in ("run_windows.bat", "run_mac.command"):
        launcher = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "git status --porcelain" in launcher
        assert "Local changes in this folder are kept - the update is skipped." in launcher
        assert "discarded by the update" not in launcher


def test_launchers_reinstall_dependencies_when_the_update_moved_head() -> None:
    for name in ("run_windows.bat", "run_mac.command"):
        launcher = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "git rev-parse HEAD" in launcher
        assert launcher.count('pip install -e ".[cloud]"') >= 2


def _write_git_repo_with_master_only(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "-b", "master", str(remote)], check=True
    )
    subprocess.run(["git", "init", "--quiet", "-b", "master", str(repo)], check=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "--quiet",
            "-m",
            "init",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "--quiet", "origin", "master"], check=True
    )
    return repo


# ``bash`` on a Windows runner resolves to the WSL stub, which cannot run the
# macOS launcher; the block is a POSIX shell script by definition.
_POSIX_SHELL_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="the macOS launcher update block needs a POSIX bash"
)


@_POSIX_SHELL_ONLY
def test_mac_launcher_update_block_survives_a_missing_origin_main(tmp_path: Path) -> None:
    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")
    begin = launcher.index("# --- self-update: begin ---")
    end = launcher.index("# --- self-update: end ---") + len("# --- self-update: end ---")
    update_block = launcher[begin:end]

    repo = _write_git_repo_with_master_only(tmp_path)
    script = tmp_path / "update_block.sh"
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n" + update_block + "\necho LAUNCH_REACHED\n",
        encoding="utf-8",
    )

    environment = {**os.environ, "VOICE_STUDIO_AUTO_UPDATE": "1"}
    result = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LAUNCH_REACHED" in result.stdout
    assert "Update could not be applied" in result.stdout

    # Without the opt-in the block is inert: no fetch attempt, no message.
    quiet = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env={key: value for key, value in os.environ.items() if key != "VOICE_STUDIO_AUTO_UPDATE"},
    )
    assert quiet.returncode == 0, quiet.stdout + quiet.stderr
    assert "LAUNCH_REACHED" in quiet.stdout
    assert "Updating VOICE Studio" not in quiet.stdout


@_POSIX_SHELL_ONLY
def test_mac_launcher_update_block_keeps_local_edits(tmp_path: Path) -> None:
    """A dirty checkout is never overwritten: the update is skipped instead."""

    launcher = (PROJECT_ROOT / "run_mac.command").read_text(encoding="utf-8")
    begin = launcher.index("# --- self-update: begin ---")
    end = launcher.index("# --- self-update: end ---") + len("# --- self-update: end ---")
    update_block = launcher[begin:end]

    repo = _write_git_repo_with_master_only(tmp_path)
    (repo / "file.txt").write_text("edited locally\n", encoding="utf-8")
    script = tmp_path / "update_block.sh"
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n" + update_block + "\necho LAUNCH_REACHED\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "VOICE_STUDIO_AUTO_UPDATE": "1"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the update is skipped" in result.stdout
    assert "LAUNCH_REACHED" in result.stdout
    assert (repo / "file.txt").read_text(encoding="utf-8") == "edited locally\n"


def test_windows_launcher_always_runs_the_checked_out_source_tree() -> None:
    launcher = (PROJECT_ROOT / "run_windows.bat").read_text(encoding="utf-8")

    assert 'set "PYTHONPATH=%~dp0src"' in launcher
    assert "-m voice_studio gui" in launcher
    assert "voice-studio.exe gui" not in launcher


def test_windows_one_click_builder_uses_isolated_environment() -> None:
    launcher = (PROJECT_ROOT / "build_windows_exe.bat").read_text(encoding="utf-8")

    assert ".venv-windows-build" in launcher
    assert "requirements-windows.lock" in launcher
    assert "--no-build-isolation --no-deps -e ." in launcher
    assert ".[dev,benchmark,package,cloud]" not in launcher
    assert "scripts\\build_windows.ps1" in launcher
    assert "Windows 10 or 11 x64" in launcher
