#!/usr/bin/env bash
set -euo pipefail
DB=Output/Data/cozo.db
if [ ! -f "$DB" ]; then
echo "Missing $DB"
exit 1
fi
# Check repositories table exists
if ! sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='repositories';" | grep -q "repositories"; then
  echo "repositories table missing in $DB"
  exit 1
fi
echo "cozo.db OK: repositories table exists"