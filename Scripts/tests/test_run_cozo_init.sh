#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COZO_DB="$REPO_ROOT/Output/Data/cozo.db"
LOG="/tmp/test_run_cozo_init.log"

# Start clean
rm -f "$COZO_DB" "$LOG"

# Run the init script directly (avoid virtualenv/python differences)
python3 "$REPO_ROOT/Scripts/init_cozo_learning.py" init "$COZO_DB" >"$LOG" 2>&1 || true

if [ ! -f "$COZO_DB" ]; then
  echo "FAILED: cozo.db not created"
  tail -n 200 "$LOG" || true
  exit 2
fi

# Required tables to validate
tables=(providers resource_types findings repositories resources)
for t in "${tables[@]}"; do
  if ! sqlite3 "$COZO_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='$t';" | grep -q "^$t$"; then
    echo "FAILED: Missing table $t"
    echo "--- DB tables ---"
    sqlite3 "$COZO_DB" "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;"
    exit 3
  fi
done

echo "PASS: cozo.db initialized with required tables"
exit 0
