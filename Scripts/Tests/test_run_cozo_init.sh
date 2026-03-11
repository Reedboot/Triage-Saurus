#!/usr/bin/env bash
set -euo pipefail

# Lightweight CI test: ensure venv, install deps, run cozo scan, verify DB
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[test] Python: $(python3 --version 2>&1)"

echo "[test] Creating venv .venv-cozo"
python3 -m venv .venv-cozo
. .venv-cozo/bin/activate

echo "[test] Installing requirements"
pip install -r requirements.txt

# Ensure Intake list and a scanable repo exist under the parent directory
mkdir -p "$ROOT_DIR/Intake"
REPOS_FILE="$ROOT_DIR/Intake/ReposToScan.txt"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
# Prefer using the cloned terragoat repo in TestsCorpus. Ensure a repo named 'terragoat' exists under the parent directory
if [ ! -d "$PARENT_DIR/terragoat" ]; then
  if [ -d "$ROOT_DIR/TestsCorpus/terragoat" ]; then
    echo "[test] Creating symlink to TestsCorpus/terragoat under parent directory"
    ln -s "$ROOT_DIR/TestsCorpus/terragoat" "$PARENT_DIR/terragoat"
  else
    echo "[test] Cloning terragoat into parent directory for scan"
    git clone --depth 1 https://github.com/bridgecrewio/terragoat "$PARENT_DIR/terragoat"
  fi
fi
# Point the repos file at terragoat
echo "terragoat" > "$REPOS_FILE"

echo "[test] Ensuring opengrep is available"
if ! command -v opengrep >/dev/null 2>&1; then
  echo "[test] Installing opengrep to /usr/local/bin/opengrep"
  sudo curl -sSL https://github.com/opengrep/opengrep/releases/latest/download/opengrep_manylinux_x86 -o /usr/local/bin/opengrep
  sudo chmod +x /usr/local/bin/opengrep
fi

echo "[test] Running Scripts/run_cozo_repos.sh --force"
bash Scripts/run_cozo_repos.sh --force

echo "[test] Verifying Output/Data/cozo.db contents"
python3 - <<'PY'
import sqlite3, sys, os
db='Output/Data/cozo.db'
if not os.path.exists(db):
    print('DB not found:', db)
    sys.exit(2)
con=sqlite3.connect(db)
cur=con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables=cur.fetchall()
print('Tables:', tables)
if not tables:
    print('No tables present in DB')
    sys.exit(3)
# Optionally count findings table rows if it exists
for t in ('repos','resources','findings'):
    try:
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"{t}:", cur.fetchone()[0])
    except Exception:
        pass
print('DB verification OK')
PY

echo "[test] Completed successfully"
