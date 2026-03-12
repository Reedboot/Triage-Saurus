#!/usr/bin/env bash
set -euo pipefail

# Simple helper to create/activate a virtualenv, install requirements, and start the web server.
# Usage:
#   ./Scripts/start_web.sh        # create venv, install deps, start server
#   ./Scripts/start_web.sh --no-install  # activate existing venv and start server

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT/.venv"
REQ_FILE="$ROOT/requirements.txt"

echo "Repo root: $ROOT"

if [ "${1-}" = "--no-install" ]; then
  if [ ! -d "$VENV_DIR" ]; then
    echo "Virtualenv not found at $VENV_DIR. Run without --no-install to create and install dependencies."
    exit 1
  fi
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
else
  if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
  fi
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"

  echo "Upgrading pip and installing requirements from $REQ_FILE..."
  python3 -m pip install --upgrade pip
  if [ -f "$REQ_FILE" ]; then
    python3 -m pip install -r "$REQ_FILE"
  else
    echo "requirements.txt not found at $REQ_FILE. Skipping pip install."
  fi
fi

echo "Starting Flask web server (press Ctrl-C to stop). Logs will stream below."

# Run the web app with the active venv so subprocesses inherit the same environment
python3 "$ROOT/web/app.py"
