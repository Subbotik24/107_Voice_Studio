#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Optional self-update of a developer checkout: only when the user sets
# VOICE_STUDIO_AUTO_UPDATE=1 the launcher contacts origin and syncs to the
# latest main before the launch. It is off by default because the product is
# local/private by default and a launch must not make a network call on its
# own. Offline or without git the launcher just starts what is here, and a
# checkout with local edits is never overwritten.
# --- self-update: begin ---
update_moved_head=0
if [ "${VOICE_STUDIO_AUTO_UPDATE:-0}" = "1" ] && [ -d .git ] && command -v git >/dev/null 2>&1; then
  echo "Updating VOICE Studio to the latest version…"
  if [ -n "$(git status --porcelain)" ]; then
    echo "Local changes in this folder are kept - the update is skipped."
  else
    before_rev="$(git rev-parse HEAD 2>/dev/null || true)"
    export GIT_TERMINAL_PROMPT=0
    if git fetch origin; then
      git checkout -f -B main origin/main || echo "Update could not be applied - starting the current version."
    else
      echo "Offline or fetch failed — starting the current version."
    fi
    after_rev="$(git rev-parse HEAD 2>/dev/null || true)"
    if [ -n "$before_rev" ] && [ -n "$after_rev" ] && [ "$before_rev" != "$after_rev" ]; then
      update_moved_head=1
    fi
  fi
fi
# --- self-update: end ---

if [ -z "${PYTHON_BIN:-}" ]; then
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "${PYTHON_BIN:-}" ]; then
  echo "VOICE Studio requires Python 3.11 or 3.12."
  exit 2
fi
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 2)' || {
  echo "VOICE Studio requires Python 3.11 or 3.12, not $("$PYTHON_BIN" --version 2>&1)."
  exit 2
}
"$PYTHON_BIN" -c 'import tkinter, _tkinter' >/dev/null 2>&1 || {
  echo "Tk is missing. Homebrew users: brew install python-tk@3.11"
  exit 2
}
if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
  echo "The existing .venv is incomplete: .venv/bin/python is missing."
  echo "Move the broken .venv aside, then run this launcher again to recreate it."
  exit 2
fi
if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -c 'import voice_studio' >/dev/null 2>&1 || {
  echo "Installing VOICE Studio into .venv (first run only)…"
  .venv/bin/python -m pip install -e ".[cloud]"
}
if [ "$update_moved_head" = "1" ]; then
  echo "Update changed the code — reinstalling dependencies…"
  .venv/bin/python -m pip install -e ".[cloud]"
fi
# Always run the source tree sitting in this folder, never a stale copy
# installed inside .venv: right after `git pull` this launcher starts
# exactly the pulled code.
export PYTHONPATH="$PWD/src"
.venv/bin/python -c 'import voice_studio; print("VOICE Studio code:", voice_studio.__file__)'
exec .venv/bin/python -m voice_studio gui
