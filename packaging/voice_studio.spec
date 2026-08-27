# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH).parent
sys.path.insert(0, str(project_root / "src"))
hiddenimports = collect_submodules("faster_whisper") + collect_submodules("openai") + [
    "av",
    "ctranslate2",
    "pynput",
    "sounddevice",
    "keyring",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
]
datas = collect_data_files("voice_studio") + collect_data_files("faster_whisper")
development_only_modules = [
    "PIL",
    "_pytest",
    "psutil",
    "pygments",
    "pytest",
]

analysis = Analysis(
    [str(project_root / "packaging" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=development_only_modules,
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VOICE Studio",
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
    name="VOICE Studio",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="VOICE Studio.app",
        bundle_identifier="studio.voice.desktop",
        info_plist={
            "CFBundleDisplayName": "VOICE Studio",
            "CFBundleShortVersionString": "0.3.0rc1",
            "NSMicrophoneUsageDescription": (
                "VOICE Studio records audio only when you explicitly start recording."
            ),
            "NSHighResolutionCapable": True,
        },
    )
