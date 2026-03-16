#!/usr/bin/env bash
set -euo pipefail

# Simple helper to create/activate a virtualenv, install requirements, and start the web server.
# Usage:
#   ./Scripts/start_web.sh        # create venv, install deps, start server
#   ./Scripts/start_web.sh --no-install  # activate existing venv and start server

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT/.venv"
REQ_FILE="$ROOT/requirements.txt"

# ── ANSI colours ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

_setup_box() {
  echo ""
  echo -e "${RED}╔══════════════════════════════════════════════════════╗${RESET}"
  echo -e "${RED}║        ⛔  Python virtual environment missing        ║${RESET}"
  echo -e "${RED}╚══════════════════════════════════════════════════════╝${RESET}"
  echo ""
  echo -e "  ${BOLD}The .venv directory was not found or could not be created.${RESET}"
  echo ""
  echo -e "  ${CYAN}To set up the environment, run:${RESET}"
  echo ""
  echo -e "    ${YELLOW}python3 -m venv .venv${RESET}"
  echo -e "    ${YELLOW}source .venv/bin/activate${RESET}"
  echo -e "    ${YELLOW}pip install -r requirements.txt${RESET}"
  echo ""
  echo -e "  Then re-run:  ${BOLD}./Scripts/start_web.sh${RESET}"
  echo ""
}

# ── Ensure or activate virtualenv first ─────────────────────────────────────

# Print repo root early for visibility
echo "Repo root: $ROOT"

if [ "${1-}" = "--no-install" ]; then
  if [ ! -d "$VENV_DIR" ]; then
    _setup_box
    exit 1
  fi
  # Activate existing venv
  # shellcheck source=/dev/null
  if ! source "$VENV_DIR/bin/activate" 2>/dev/null; then
    echo -e "${RED}Failed to activate .venv — it may be corrupt.${RESET}"
    _setup_box
    exit 1
  fi
else
  if [ -d "$VENV_DIR" ]; then
    echo -e "${CYAN}Using existing virtualenv at $VENV_DIR${RESET}"
    # shellcheck source=/dev/null
    if ! source "$VENV_DIR/bin/activate" 2>/dev/null; then
      echo -e "${RED}Failed to activate .venv — it may be corrupt.${RESET}"
      _setup_box
      exit 1
    fi
  else
    # Need system python3 to create the venv
    if ! command -v python3 &>/dev/null; then
      _setup_box
      echo -e "  ${RED}python3 was not found in PATH. Install Python 3.11+ first.${RESET}"
      echo ""
      exit 1
    fi

    echo -e "${CYAN}Creating virtualenv at $VENV_DIR...${RESET}"
    if ! python3 -m venv "$VENV_DIR"; then
      _setup_box
      echo -e "  ${RED}'python3 -m venv' failed. Ensure the 'venv' module is available:${RESET}"
      echo -e "    ${YELLOW}# Ubuntu/Debian: sudo apt install python3-venv${RESET}"
      echo ""
      exit 1
    fi

    # shellcheck source=/dev/null
    if ! source "$VENV_DIR/bin/activate" 2>/dev/null; then
      echo -e "${RED}Failed to activate .venv after creation — something went wrong.${RESET}"
      _setup_box
      exit 1
    fi
  fi

  echo -e "${CYAN}Upgrading pip and installing requirements...${RESET}"
  # Prefer using the venv python executable directly to avoid system python ambiguity
  if [ -x "$VENV_DIR/bin/python" ]; then
    PIP_PY="$VENV_DIR/bin/python"
  else
    PIP_PY=$(command -v python3 || command -v python)
  fi
  "$PIP_PY" -m pip install --upgrade pip -q
  if [ -f "$REQ_FILE" ]; then
    "$PIP_PY" -m pip install -r "$REQ_FILE" -q
    echo -e "${GREEN}✅ Dependencies installed.${RESET}"
  else
    echo -e "${YELLOW}⚠ requirements.txt not found at $REQ_FILE — skipping pip install.${RESET}"
  fi

  # Install project in editable mode if it appears to be a Python package
  if [ -f "$ROOT/pyproject.toml" ] || [ -f "$ROOT/setup.py" ]; then
    echo -e "${CYAN}Installing repository into venv (editable mode)...${RESET}"
    # Use -q to keep output quiet; don't fail startup if install errors
    "$PIP_PY" -m pip install -e "$ROOT" -q || \
      echo -e "${YELLOW}⚠ Editable install failed; continuing without it.${RESET}"
  else
    echo -e "${YELLOW}ℹ No pyproject.toml or setup.py found — skipping editable install.${RESET}"
  fi
fi

echo ""
echo -e "${GREEN}🦕 Starting Triage-Saurus web server${RESET}"
echo -e "   ${CYAN}URL: http://localhost:5000${RESET}"
echo -e "   ${CYAN}Press Ctrl-C to stop.${RESET}"
echo ""

# Run the web app with the active venv so subprocesses inherit the same environment
if [ -x "$VENV_DIR/bin/python" ]; then
  PY_EXEC="$VENV_DIR/bin/python"
else
  PY_EXEC=$(command -v python3 || command -v python)
fi
echo -e "  ${CYAN}Launching web app with: ${BOLD}${PY_EXEC}${RESET}"
exec "$PY_EXEC" "$ROOT/web/app.py"
