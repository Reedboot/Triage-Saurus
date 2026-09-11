#!/usr/bin/env python3
"""Initialize Cozo DB schema and seed resource_types.

This script ensures Output/Data/cozo.db exists, applies schema via db_helpers._ensure_schema
and seeds the resource_types table from Scripts/Persist/resource_type_db._FALLBACK.
"""
from pathlib import Path
import importlib.util
import sys
import types

# Provide a lightweight stub for cozo_helpers to avoid import failures during init
# if pycozo is not installed. Real cozo_helpers (pycozo) can be used in normal runs.
sys.modules.setdefault("cozo_helpers", types.SimpleNamespace())

ROOT = Path(__file__).resolve().parents[1]

# Load db_helpers dynamically from Scripts/Persist
db_helpers_path = ROOT / "Scripts" / "Persist" / "db_helpers.py"
spec = importlib.util.spec_from_file_location("db_helpers", str(db_helpers_path))
db_helpers = importlib.util.module_from_spec(spec)
loader = spec.loader
if loader is None:
    raise ImportError("Failed to load db_helpers (no loader)")
loader.exec_module(db_helpers)

# Load resource_type_db to get fallback seed rows
rtdb_path = ROOT / "Scripts" / "Persist" / "resource_type_db.py"
spec2 = importlib.util.spec_from_file_location("resource_type_db", str(rtdb_path))
rtdb = importlib.util.module_from_spec(spec2)
loader2 = spec2.loader
if loader2 is None:
    raise ImportError("Failed to load resource_type_db (no loader)")
loader2.exec_module(rtdb)

print(f"Initializing DB at {db_helpers.DB_PATH}")

# Ensure schema is applied (get_db_connection will call _ensure_schema)
with db_helpers.get_db_connection() as conn:
    # Ensure resource_types table exists (init script is authoritative for seed)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS resource_types (
      id INTEGER PRIMARY KEY,
      resource_type TEXT UNIQUE NOT NULL,
      friendly_name TEXT,
      category TEXT,
      icon TEXT,
      display_on_architecture_chart BOOLEAN DEFAULT 1,
      parent_type TEXT
    );
    """)

# Seed resource_types from _FALLBACK if present
fallback = getattr(rtdb, "_FALLBACK", {})
if fallback:
    inserted = 0
    with db_helpers.get_db_connection() as conn:
        for rtype, meta in fallback.items():
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO resource_types (resource_type, friendly_name, category, icon, display_on_architecture_chart, parent_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (rtype, meta.get("friendly_name"), meta.get("category"), meta.get("icon"), 1 if meta.get("display_on_architecture_chart", False) else 0, meta.get("parent_type"))
                )
                inserted += 1
            except Exception:
                # Non-fatal; continue seeding rest
                pass
    print(f"Seeded {inserted} resource_types")
else:
    print("No fallback seed rows found; nothing to seed.")

print("Initialization complete.")
