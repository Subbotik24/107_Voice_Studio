# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH).parent
sys.path.insert(0, str(project_root / "src"))
hiddenimports = collect_submodules("faster_whisper") + [
    "av",
    "ctranslate2",
    "pynput",
    "sounddevice",
    "keyring",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
]
datas = collect_data_files("hermes_voice_studio")
training_only_modules = [
    "hermes_whisper.__main__",
    "hermes_whisper.cli",
    "hermes_whisper.data",
    "hermes_whisper.evaluation",
    "hermes_whisper.losses",
    "hermes_whisper.manifest",
    "hermes_whisper.metrics",
    "hermes_whisper.smoke",
    "hermes_whisper.trainer",
    "PIL",
    "_pytest",
    "psutil",
    "pygments",
    "pytest",
    "safetensors",
    "tensorboard",
    "torch.utils.benchmark",
    "torch.utils.tensorboard",
]
torch_module_collection_mode = {
    # Current PyTorch configuration and JIT modules inspect their source at
    # runtime. A source-only layout also avoids duplicate package imports in
    # the frozen loader while retaining the original file-loader semantics.
    "torch": "py",
}

analysis = Analysis(
    [str(project_root / "packaging" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=training_only_modules,
    noarchive=False,
    module_collection_mode=torch_module_collection_mode,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Hermes Voice Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Hermes Voice Studio",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="Hermes Voice Studio.app",
        bundle_identifier="org.hermesvoice.studio",
        info_plist={
            "CFBundleDisplayName": "Hermes Voice Studio",
            "CFBundleShortVersionString": "0.3.0rc1",
            "NSMicrophoneUsageDescription": (
                "Hermes Voice Studio records audio only when you explicitly start recording."
            ),
            "NSHighResolutionCapable": True,
        },
    )
