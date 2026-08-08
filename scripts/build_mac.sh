#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: macOS packaging must run on macOS" >&2
  exit 2
fi
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
"$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1 || {
  echo "error: install packaging dependencies with: $PYTHON_BIN -m pip install -e '.[package,hermes]'" >&2
  exit 2
}

"$PYTHON_BIN" -m PyInstaller --noconfirm --clean packaging/hermes_voice_studio.spec
APP="dist/Hermes Voice Studio.app"
if [ ! -d "$APP" ]; then
  echo "error: PyInstaller did not create $APP" >&2
  exit 2
fi
DMG="dist/Hermes-Voice-Studio-0.2.0-unsigned.dmg"
rm -f "$DMG"
hdiutil create -volname "Hermes Voice Studio" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "Created unsigned artifact: $DMG"
