#!/usr/bin/env bash
set -euo pipefail

DB=Output/Data/cozo.db

if [ ! -f "$DB" ]; then
  echo "❌ Missing $DB"
  echo ""
  echo "  The Cozo DB must be initialised before this check runs."
  echo "  In CI: add a step before this one:"
  echo "    python3 Scripts/Utils/init_cozo_learning.py init Output/Data/cozo.db"
  echo ""
  echo "  Locally:"
  echo "    source .venv/bin/activate"
  echo "    python3 Scripts/Utils/init_cozo_learning.py init Output/Data/cozo.db"
  exit 1
fi

# Check repositories table exists (created by db_helpers._ensure_schema)
if ! sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='repositories';" | grep -q "repositories"; then
  echo "❌ 'repositories' table missing in $DB — schema may be incomplete"
  exit 1
fi

# Check nodes/edges tables exist (created by init_cozo_learning)
for table in nodes edges findings; do
  if ! sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='${table}';" | grep -q "${table}"; then
    echo "❌ '${table}' table missing in $DB — run init_cozo_learning.py to apply full schema"
    exit 1
  fi
done

echo "✅ cozo.db OK: repositories, nodes, edges, findings tables all present"
