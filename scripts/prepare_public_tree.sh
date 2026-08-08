#!/usr/bin/env bash
# Create a clean, independent publishing tree without altering this working copy.
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:?usage: scripts/prepare_public_tree.sh /absolute/path/to/public-copy}"

if [ -e "$TARGET" ]; then
  echo "error: target already exists; refusing to overwrite it" >&2
  exit 2
fi
mkdir -p "$TARGET"
for item in \
  AGENTS.md ARCHITECTURE.md CONTRIBUTING.md LICENSE README.md README.uk.md ROADMAP.md \
  SECURITY.md THIRD_PARTY_NOTICES.md VERIFICATION.md pyproject.toml .gitignore .env.example \
  run_mac.command run_windows.bat build_windows_exe.bat WINDOWS_BUILD_README.md RELEASE_ACCEPTANCE.md \
  src tests scripts packaging config configs docs .github; do
  if [ -e "$SOURCE_ROOT/$item" ]; then
    cp -R -p "$SOURCE_ROOT/$item" "$TARGET/$item"
  fi
done

# Defensive post-copy checks. These names must never be part of a public source tree.
if find "$TARGET" -type f \( -name '*.sqlite*' -o -name '*.hws' -o -name '*.wav' -o \
  -name '*.mp3' -o -name '*.m4a' -o -name '*.mp4' -o -name 'model.bin' \) -print -quit | grep -q .; then
  echo "error: restricted user data or model binary reached publishing tree" >&2
  exit 2
fi
if rg -n --hidden --glob '!.git/**' 'sk-[A-Za-z0-9_-]{20,}' "$TARGET"; then
  echo "error: potential secret found; inspect before publishing" >&2
  exit 2
fi
git -C "$TARGET" init -b main
git -C "$TARGET" add --all
git -C "$TARGET" status --short
echo "Prepared clean publishing tree: $TARGET"
echo "Review it, then add the GitHub remote and create the first commit manually."
