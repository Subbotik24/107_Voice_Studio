#!/bin/bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "error: this Test RC profile requires macOS Apple Silicon" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
"$PYTHON_BIN" -c "import build, PyInstaller, pytest, ruff" >/dev/null 2>&1 || {
  echo "error: install dev/package dependencies with: $PYTHON_BIN -m pip install -e '.[dev,package,benchmark,cloud]'" >&2
  exit 2
}

RELEASE_LABEL="0.3.0-test-rc1"
FINAL_DIRECTORY="$PROJECT_ROOT/dist/$RELEASE_LABEL"
ACCEPTANCE_RESULT="${VOICE_STUDIO_ACCEPTANCE_RESULT:-}"
if [ -z "$ACCEPTANCE_RESULT" ] || [ ! -f "$ACCEPTANCE_RESULT" ]; then
  echo "error: VOICE_STUDIO_ACCEPTANCE_RESULT must point to a passing 50-task acceptance JSON" >&2
  exit 2
fi
if [ -e "$FINAL_DIRECTORY" ]; then
  echo "error: refusing to overwrite existing Test RC: $FINAL_DIRECTORY" >&2
  exit 2
fi

mkdir -p "$PROJECT_ROOT/dist"
STAGE_DIRECTORY="$(mktemp -d "$PROJECT_ROOT/dist/.${RELEASE_LABEL}.XXXXXX")"
cleanup() {
  rm -rf "$STAGE_DIRECTORY"
}
trap cleanup EXIT

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$STAGE_DIRECTORY/pyinstaller-dist" \
  --workpath "$STAGE_DIRECTORY/pyinstaller-work" \
  packaging/voice_studio.spec

BUILT_APP="$STAGE_DIRECTORY/pyinstaller-dist/VOICE Studio.app"
if [ ! -d "$BUILT_APP" ]; then
  echo "error: PyInstaller did not create the application bundle" >&2
  exit 2
fi
FINAL_APP="$STAGE_DIRECTORY/VOICE Studio.app"
mv "$BUILT_APP" "$FINAL_APP"

codesign --force --deep --sign - "$FINAL_APP"

RUNTIME_PROBE="$STAGE_DIRECTORY/runtime-probe.json"
if ! VOICE_STUDIO_RUNTIME_PROBE_OUTPUT="$RUNTIME_PROBE" \
  "$FINAL_APP/Contents/MacOS/VOICE Studio"; then
  if [ -f "$RUNTIME_PROBE" ]; then
    cat "$RUNTIME_PROBE" >&2
  fi
  exit 2
fi

plutil -lint "$FINAL_APP/Contents/Info.plist"
codesign --verify --deep --strict "$FINAL_APP"

DMG_NAME="VOICE-Studio-${RELEASE_LABEL}-macos-arm64-unsigned.dmg"
hdiutil create \
  -volname "VOICE Studio Test RC" \
  -srcfolder "$FINAL_APP" \
  -format UDZO \
  "$STAGE_DIRECTORY/$DMG_NAME"
hdiutil verify "$STAGE_DIRECTORY/$DMG_NAME"

"$PYTHON_BIN" -m build \
  --wheel \
  --no-isolation \
  --outdir "$STAGE_DIRECTORY" \
  "$PROJECT_ROOT"
WHEEL="$STAGE_DIRECTORY/voice_studio-0.3.0rc1-py3-none-any.whl"
if [ ! -f "$WHEEL" ]; then
  echo "error: wheel build did not produce the expected artifact" >&2
  exit 2
fi

cp "$ACCEPTANCE_RESULT" "$STAGE_DIRECTORY/acceptance-result.json"
rm -rf "$STAGE_DIRECTORY/pyinstaller-dist" "$STAGE_DIRECTORY/pyinstaller-work"

"$PYTHON_BIN" scripts/create_release_manifest.py \
  --release-directory "$STAGE_DIRECTORY" \
  --release-label "$RELEASE_LABEL" \
  --acceptance-result "$STAGE_DIRECTORY/acceptance-result.json" \
  --repository-root "$PROJECT_ROOT" \
  --artifact "$FINAL_APP" \
  --artifact "$STAGE_DIRECTORY/$DMG_NAME" \
  --artifact "$WHEEL" \
  --artifact "$RUNTIME_PROBE" \
  --artifact "$STAGE_DIRECTORY/acceptance-result.json" \
  --output "$STAGE_DIRECTORY/release-manifest.json" \
  > "$STAGE_DIRECTORY/release-manifest.stdout.json"

(
  cd "$STAGE_DIRECTORY"
  shasum -a 256 \
    "$DMG_NAME" \
    "voice_studio-0.3.0rc1-py3-none-any.whl" \
    "runtime-probe.json" \
    "acceptance-result.json" \
    "release-manifest.json" \
    > SHA256SUMS.txt
)

mv "$STAGE_DIRECTORY" "$FINAL_DIRECTORY"
trap - EXIT
echo "Created Test RC: $FINAL_DIRECTORY"
