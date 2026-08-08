from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_profile_excludes_training_only_modules() -> None:
    spec = (PROJECT_ROOT / "packaging" / "hermes_voice_studio.spec").read_text(
        encoding="utf-8"
    )

    assert 'collect_submodules("hermes_whisper")' not in spec
    assert 'collect_submodules("hermes_voice_studio")' not in spec
    for module in (
        "hermes_whisper.trainer",
        "hermes_whisper.smoke",
        "hermes_whisper.data",
        "pytest",
        "tensorboard",
        "safetensors",
    ):
        assert f'"{module}"' in spec
    assert '"torch": "py"' in spec
    assert "module_collection_mode=torch_module_collection_mode" in spec
    assert '"torch.distributed.rpc"' not in spec
    assert "runtime_hooks=[]" in spec

def test_frozen_launcher_enables_multiprocessing_support() -> None:
    launcher = (PROJECT_ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")

    assert "multiprocessing.freeze_support()" in launcher
    assert "HVS_RUNTIME_PROBE_OUTPUT" in launcher
    assert "training_only_excluded" in launcher
    assert 'loaded.get("torch")' in launcher


def test_test_rc_build_is_atomic_and_refuses_overwrite() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_test_rc.sh").read_text(
        encoding="utf-8"
    )

    assert 'RELEASE_LABEL="0.3.0-test-rc1"' in build_script
    assert "refusing to overwrite existing Test RC" in build_script
    assert "mktemp -d" in build_script
    assert 'mv "$STAGE_DIRECTORY" "$FINAL_DIRECTORY"' in build_script
    assert "HVS_ACCEPTANCE_RESULT" in build_script
    assert "scripts/patch_frozen_torch.py" in build_script
    assert 'codesign --force --deep --sign - "$FINAL_APP"' in build_script
    assert '"$PYTHON_BIN" -m build' in build_script
    assert '"$PYTHON_BIN" -m pip wheel' not in build_script


def test_frozen_torch_patch_is_exact_and_fail_fast() -> None:
    patcher = (PROJECT_ROOT / "scripts" / "patch_frozen_torch.py").read_text(
        encoding="utf-8"
    )

    assert 'RPC_IMPORT = "import torch.distributed.rpc\\n"' in patcher
    assert "source.count(RPC_IMPORT) != 1" in patcher
    assert "import torch.distributed" in patcher
    assert 'sys.modules["torch.distributed.rpc"]' in patcher
    assert 'FUNCTIONAL_IMPORT = "import torch.nn.functional as F\\n"' in patcher
    assert 'importlib.import_module("torch.nn.functional")' in patcher


def test_windows_build_is_atomic_and_runtime_verified() -> None:
    build_script = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "sys.platform == 'win32'" in build_script
    assert "sys.maxsize > 2**32" in build_script
    assert "quality_gate.ps1" in build_script
    assert "refusing to overwrite existing Windows Test RC" in build_script
    assert "scripts/patch_frozen_torch.py" in build_script
    assert "_internal\\torch\\_jit_internal.py" in build_script
    assert "HVS_RUNTIME_PROBE_OUTPUT" in build_script
    assert "runtime-probe.json" in build_script
    assert "Compress-Archive" in build_script
    assert "Get-FileHash" in build_script
    assert "Move-Item" in build_script
