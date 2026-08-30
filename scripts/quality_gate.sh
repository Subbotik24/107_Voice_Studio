#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${PYTHON_BIN:-}" ]; then
  for candidate in python python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "${PYTHON_BIN:-}" ]; then
  echo "error: Python 3.11 or 3.12 was not found" >&2
  exit 2
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 2)' || {
  echo "error: quality gate requires Python 3.11 or 3.12" >&2
  exit 2
}
"$PYTHON_BIN" -c "import pytest, ruff" >/dev/null 2>&1 || {
  echo "error: install development dependencies with: $PYTHON_BIN -m pip install -e '.[dev]'" >&2
  exit 2
}

"$PYTHON_BIN" -m compileall -q src tests scripts packaging
"$PYTHON_BIN" -m ruff check src tests scripts packaging
PYTHONPATH=src "$PYTHON_BIN" scripts/check_help.py
PYTHONPATH=src "$PYTHON_BIN" -m pytest -q
PYTHONPATH=src "$PYTHON_BIN" -m voice_studio.cli --version >/dev/null
