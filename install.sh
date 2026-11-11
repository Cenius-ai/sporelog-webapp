#!/usr/bin/env bash
# SporeLog project install: verify the interpreter, make the TEST dependency
# available, then exit. SporeLog itself is standard library only, so there is
# nothing to build and nothing to start. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found on PATH; install Python 3.9 or newer" >&2
  exit 1
fi

"$PYTHON" - <<'VERSION_CHECK'
import sys

if sys.version_info < (3, 9):
    sys.exit("error: SporeLog needs Python 3.9 or newer, found " + sys.version.split()[0])
print("python " + sys.version.split()[0] + " ok")
VERSION_CHECK

# Cheap self-check: the file must import cleanly before anything else runs.
"$PYTHON" -c 'import sporelog'
echo "sporelog imports ok"

if "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
  echo "pytest already available, skipping the test dependency install"
else
  echo "installing the test dependency with pip"
  "$PYTHON" -m pip install --upgrade pip setuptools wheel
  "$PYTHON" -m pip install -r requirements.txt
fi

echo "install complete - run: $PYTHON sporelog.py --help"
echo "demo: bash demo.sh   tests: $PYTHON -m pytest"
