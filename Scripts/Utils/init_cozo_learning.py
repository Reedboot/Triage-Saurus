#!/usr/bin/env python3
"""Initialize learning schema inside the Cozo SQLite DB.

This script delegates canonical topology table creation to db_helpers._ensure_schema
(to remain compatible with existing writes) and creates the lookup tables
providers and resource_types (seeded from resource_type_db._FALLBACK).
"""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path
import sys

# Ensure Scripts is on path
ROOT = Path(__file__).resolve().parents[2]
SYS_SCRIPTS = str(ROOT / 'Scripts')
if SYS_SCRIPTS not in sys.path:
    sys.path.insert(0, SYS_SCRIPTS)
# Also add Scripts/Persist where db_helpers lives after the reorg
SYS_PERSIST = str(ROOT / 'Scripts' / 'Persist')
if SYS_PERSIST not in sys.path:
    sys.path.insert(0, SYS_PERSIST)

import db_helpers
import resource_type_db as rtdb

DEFAULT_COZO_DB = ROOT / "Output/Data/cozo.db"


def init(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    conn.execute("PRAGMA busy_timeout = 30000;")
    try:
        cur = conn.cursor()
        # Create providers table if missing (create minimal expected tables BEFORE calling db_helpers._ensure_schema
        # because db_helpers may attempt ALTER TABLE on findings/resources)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                friendly_name TEXT,
                icon TEXT
            )
            """
        )
        # Create resource_types table if missing
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER,
                terraform_type TEXT UNIQUE,
                friendly_name TEXT,
                category TEXT,
                icon TEXT,
                is_data_store INTEGER DEFAULT 0,
                is_internet_facing_capable INTEGER DEFAULT 0,
                display_on_architecture_chart INTEGER DEFAULT 1,
                parent_type TEXT,
                FOREIGN KEY(provider_id) REFERENCES providers(id)
            )
            """
        )

        # Create findings table expected by db_helpers and legacy workflows
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT,
                repo_id INTEGER,
                resource_id INTEGER,
                rule_id TEXT,
                title TEXT,
                description TEXT,
                reason TEXT,
                category TEXT,
                code_snippet TEXT,
                source_file TEXT,
                source_line_start INTEGER,
                source_line_end INTEGER,
                severity_score INTEGER DEFAULT 4,
                base_severity TEXT,
                status TEXT DEFAULT 'raw',
                finding_path TEXT,
                llm_enriched_at TIMESTAMP,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        conn.commit()

        # Now delegate canonical learning schema creation/patching to db_helpers
        # (db_helpers._ensure_schema may run ALTER TABLE statements that expect findings/resources exist)
        db_helpers._ensure_schema(conn)
        conn.commit()

        # Ensure legacy resource columns exist for compatibility with db_helpers
        resource_cols = {row[1] for row in conn.execute("PRAGMA table_info(resources)").fetchall()}
        for col, coltype in (("resource_type", "TEXT"), ("provider", "TEXT"), ("discovered_by", "TEXT"), ("discovery_method", "TEXT")):
            if col not in resource_cols:
                conn.execute(f"ALTER TABLE resources ADD COLUMN {col} {coltype}")
        conn.commit()

        # Seed providers
        prov_keys = {prov for _, prov in getattr(rtdb, "_PROVIDER_PREFIXES", [])}
        prov_keys.update({"unknown", "terraform"})
        for key in sorted(prov_keys):
            cur.execute("INSERT OR IGNORE INTO providers (key, friendly_name, icon) VALUES (?, ?, ?)", (key, key.title(), ""))
        conn.commit()

        # Map provider keys to ids
        cur.execute("SELECT id, key FROM providers")
        prov_map = {row[1]: row[0] for row in cur.fetchall()}

        # Seed resource_types from fallback
        for tf_type, entry in getattr(rtdb, "_FALLBACK", {}).items():
            provider = rtdb._derive(tf_type).get("provider", "unknown")
            pid = prov_map.get(provider)
            cur.execute(
                "INSERT OR IGNORE INTO resource_types (provider_id, terraform_type, friendly_name, category, icon, display_on_architecture_chart, parent_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, tf_type, entry.get("friendly_name"), entry.get("category"), entry.get("icon"), 1 if entry.get("display_on_architecture_chart", True) else 0, entry.get("parent_type")),
            )
        conn.commit()

        # Create helpful indexes for fast lookups
        cur.execute("CREATE INDEX IF NOT EXISTS idx_providers_key ON providers(key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resource_types_terraform_type ON resource_types(terraform_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resource_types_provider_id ON resource_types(provider_id)")

        # Also create lightweight node/edge graph tables used by older enrichment code
        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                resource_type TEXT,
                terraform_name TEXT,
                source_repo TEXT,
                aliases TEXT,
                canonical_name TEXT,
                context TEXT,
                provenance TEXT,
                evidence_level TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id TEXT,
                to_id TEXT,
                type TEXT,
                confidence TEXT,
                evidence_level TEXT,
                equivalence_kind TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        # provenance: an append-only audit trail for relationship/node changes
        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS relationship_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_node TEXT,
                to_node TEXT,
                rel_type TEXT,
                action TEXT,
                actor_type TEXT,
                actor_id TEXT,
                scan_id TEXT,
                evidence_finding_id TEXT,
                confidence REAL,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        conn.commit()

        # Ensure provenance columns on edges for fast lookup (add if missing)
        edge_cols = {row[1] for row in conn.execute("PRAGMA table_info(edges)").fetchall()}
        if 'source_scan_id' not in edge_cols:
            conn.execute("ALTER TABLE edges ADD COLUMN source_scan_id TEXT")
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize learning schema inside Cozo DB")
    parser.add_argument("init", nargs='?', help="init command", default=None)
    parser.add_argument("db_path", nargs="?", type=Path, default=DEFAULT_COZO_DB)
    args = parser.parse_args()
    if args.init is None:
        print("Usage: init_cozo_learning.py init [<cozo_db_path>]")
        raise SystemExit(1)
    init(args.db_path)
    print(f"Initialized learning schema at {args.db_path}")
