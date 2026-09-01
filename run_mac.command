#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

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
# Always run the source tree sitting in this folder, never a stale copy
# installed inside .venv: right after `git pull` this launcher starts
# exactly the pulled code.
export PYTHONPATH="$PWD/src"
.venv/bin/python -c 'import voice_studio; print("VOICE Studio code:", voice_studio.__file__)'
exec .venv/bin/python -m voice_studio gui
