#!/usr/bin/env python3
"""Database helper functions for Triage-Saurus."""

import json
import sqlite3
import sys
import time
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
try:
    import cozo_helpers
    _COZO_HELPERS_AVAILABLE = True
except Exception as e:
    # Attempt to dynamically load the local implementation from Scripts/Enrich/cozo_helpers.py
    import importlib.util
    impl_path = Path(__file__).resolve().parents[2] / "Scripts" / "Enrich" / "cozo_helpers.py"
    if impl_path.exists():
        spec = importlib.util.spec_from_file_location("cozo_helpers", str(impl_path))
        module = importlib.util.module_from_spec(spec)
        loader = spec.loader
        if loader is None:
            raise ImportError("Failed to load cozo_helpers implementation (no loader)")
        try:
            loader.exec_module(module)
            cozo_helpers = module
            sys.modules.setdefault("cozo_helpers", module)
            _COZO_HELPERS_AVAILABLE = True
        except Exception as _load_err:
            # Best-effort: cozo_helpers failed to load (e.g., pycozo not installed).
            # Do not raise here; fall back to non-pycozo mode so DB init can proceed.
            _COZO_HELPERS_AVAILABLE = False
            # Optional: record the failure for diagnostics but avoid noisy failures.
            try:
                print(f"Warning: failed to load cozo_helpers: {_load_err}")
            except Exception:
                pass
    else:
        _COZO_HELPERS_AVAILABLE = False
        # Do not raise here; running without cozo_helpers is supported.
        # raise ImportError("Required module 'cozo_helpers' not found. Install pycozo or provide cozo_helpers.py in PYTHONPATH. Original error: " + str(e))

# Database location
ROOT = Path(__file__).resolve().parents[2]
COZO_DB = ROOT / "Output/Data/cozo.db"
# Prefer Cozo DB for all scripts.
DB_PATH = COZO_DB

# Track which DB paths have had their schema ensured in this process so
# _ensure_schema skips the expensive DDL + lock on every subsequent connection.
_schema_ensured_for: set = set()

ENRICHMENT_QUEUE_STATUSES = {"pending_review", "confirmed", "rejected"}
ENRICHMENT_DECISION_MAP = {
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "reject": "rejected",
    "rejected": "rejected",
}
ENRICHMENT_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def apply_topology_backfills(conn: sqlite3.Connection) -> Dict[str, int]:
    """Backfill additive topology columns for legacy rows (safe + idempotent)."""
    updates: Dict[str, int] = {}

    def _run(label: str, statement: str) -> None:
        cursor = conn.execute(statement)
        updates[label] = max(cursor.rowcount, 0)

    _run(
        "resource_connections_source_repo_id",
        """
        UPDATE resource_connections
        SET source_repo_id = (
            SELECT r.repo_id
            FROM resources r
            WHERE r.id = resource_connections.source_resource_id
        )
        WHERE source_repo_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM resources r
              WHERE r.id = resource_connections.source_resource_id
                AND r.repo_id IS NOT NULL
          )
        """,
    )
    _run(
        "resource_connections_target_repo_id",
        """
        UPDATE resource_connections
        SET target_repo_id = (
            SELECT r.repo_id
            FROM resources r
            WHERE r.id = resource_connections.target_resource_id
        )
        WHERE target_repo_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM resources r
              WHERE r.id = resource_connections.target_resource_id
                AND r.repo_id IS NOT NULL
          )
        """,
    )
    _run(
        "resource_connections_is_cross_repo",
        """
        UPDATE resource_connections
        SET is_cross_repo = CASE WHEN source_repo_id != target_repo_id THEN 1 ELSE 0 END
        WHERE source_repo_id IS NOT NULL
          AND target_repo_id IS NOT NULL
          AND COALESCE(is_cross_repo, -1) != CASE WHEN source_repo_id != target_repo_id THEN 1 ELSE 0 END
        """,
    )
    _run(
        "resource_connections_auth_method",
        """
        UPDATE resource_connections
        SET auth_method = authentication
        WHERE (auth_method IS NULL OR TRIM(auth_method) = '')
          AND (authentication IS NOT NULL AND TRIM(authentication) != '')
        """,
    )
    _run(
        "resource_connections_authentication",
        """
        UPDATE resource_connections
        SET authentication = auth_method
        WHERE (authentication IS NULL OR TRIM(authentication) = '')
          AND (auth_method IS NOT NULL AND TRIM(auth_method) != '')
        """,
    )
    _run(
        "findings_repo_id",
        """
        UPDATE findings
        SET repo_id = (
            SELECT r.repo_id
            FROM resources r
            WHERE r.id = findings.resource_id
        )
        WHERE repo_id IS NULL
          AND resource_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM resources r
              WHERE r.id = findings.resource_id
                AND r.repo_id IS NOT NULL
          )
        """,
    )
    _run(
        "resource_nodes_aliases",
        """
        UPDATE resource_nodes
        SET aliases = '[]'
        WHERE aliases IS NULL OR TRIM(aliases) = ''
        """,
    )
    _run(
        "resource_nodes_confidence",
        """
        UPDATE resource_nodes
        SET confidence = 'extracted'
        WHERE confidence IS NULL
           OR TRIM(confidence) = ''
           OR confidence NOT IN ('extracted', 'inferred', 'user_confirmed')
        """,
    )
    _run(
        "resource_nodes_properties",
        """
        UPDATE resource_nodes
        SET properties = '{}'
        WHERE properties IS NULL OR TRIM(properties) = ''
        """,
    )
    _run(
        "resource_nodes_created_at",
        """
        UPDATE resource_nodes
        SET created_at = CURRENT_TIMESTAMP
        WHERE created_at IS NULL
        """,
    )
    _run(
        "resource_nodes_updated_at",
        """
        UPDATE resource_nodes
        SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP)
        WHERE updated_at IS NULL
        """,
    )
    _run(
        "resource_equivalences_candidate_source_repo",
        """
        UPDATE resource_equivalences
        SET candidate_source_repo = (
            SELECT rn.source_repo
            FROM resource_nodes rn
            WHERE rn.id = resource_equivalences.resource_node_id
        )
        WHERE (candidate_source_repo IS NULL OR TRIM(candidate_source_repo) = '')
          AND EXISTS (
              SELECT 1
              FROM resource_nodes rn
              WHERE rn.id = resource_equivalences.resource_node_id
                AND rn.source_repo IS NOT NULL
                AND TRIM(rn.source_repo) != ''
          )
        """,
    )
    _run(
        "resource_equivalences_equivalence_kind",
        """
        UPDATE resource_equivalences
        SET equivalence_kind = 'cross_repo_alias'
        WHERE (equivalence_kind IS NULL
               OR TRIM(equivalence_kind) = ''
               OR equivalence_kind NOT IN ('cross_repo_alias', 'placeholder_promotion'))
          AND NOT EXISTS (
              SELECT 1
              FROM resource_equivalences dup
              WHERE dup.id != resource_equivalences.id
                AND dup.resource_node_id = resource_equivalences.resource_node_id
                AND dup.candidate_resource_type = resource_equivalences.candidate_resource_type
                AND dup.candidate_terraform_name = resource_equivalences.candidate_terraform_name
                AND dup.candidate_source_repo = resource_equivalences.candidate_source_repo
                AND COALESCE(dup.equivalence_kind, 'cross_repo_alias') = 'cross_repo_alias'
          )
        """,
    )
    _run(
        "resource_equivalences_confidence",
        """
        UPDATE resource_equivalences
        SET confidence = 'medium'
        WHERE confidence IS NULL
           OR TRIM(confidence) = ''
           OR confidence NOT IN ('high', 'medium', 'low')
        """,
    )
    _run(
        "resource_equivalences_evidence_level",
        """
        UPDATE resource_equivalences
        SET evidence_level = 'inferred'
        WHERE evidence_level IS NULL
           OR TRIM(evidence_level) = ''
           OR evidence_level NOT IN ('extracted', 'inferred', 'user_confirmed')
        """,
    )
    _run(
        "resource_equivalences_provenance",
        """
        UPDATE resource_equivalences
        SET provenance = 'legacy_backfill'
        WHERE provenance IS NULL OR TRIM(provenance) = ''
        """,
    )
    _run(
        "resource_equivalences_created_at",
        """
        UPDATE resource_equivalences
        SET created_at = CURRENT_TIMESTAMP
        WHERE created_at IS NULL
        """,
    )
    _run(
        "resource_equivalences_updated_at",
        """
        UPDATE resource_equivalences
        SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP)
        WHERE updated_at IS NULL
        """,
    )
    _run(
        "enrichment_queue_confidence",
        """
        UPDATE enrichment_queue
        SET confidence = 'medium'
        WHERE confidence IS NULL
           OR TRIM(confidence) = ''
           OR confidence NOT IN ('high', 'medium', 'low')
        """,
    )
    _run(
        "enrichment_queue_status",
        """
        UPDATE enrichment_queue
        SET status = 'pending_review'
        WHERE status IS NULL
           OR TRIM(status) = ''
           OR status NOT IN ('pending_review', 'confirmed', 'rejected')
        """,
    )
    _run(
        "enrichment_queue_created_at",
        """
        UPDATE enrichment_queue
        SET created_at = CURRENT_TIMESTAMP
        WHERE created_at IS NULL
        """,
    )

    # ------------------------------------------------------------------
    # Backfill provider IDs and textual provider columns for legacy rows.
    # This ensures older DBs created before provider-detection changes get
    # updated when schema migrations run. The logic is resilient when
    # providers/resource_types tables are missing (e.g., fresh or legacy DBs).
    try:
        import resource_type_db as rtdb
        cur = conn.cursor()

        # Derive provider keys (for potential seeding) but do not rely on providers table existing
        prov_keys = {prov for _, prov in getattr(rtdb, "_PROVIDER_PREFIXES", [])}
        prov_keys.update({"unknown", "terraform"})

        # If providers table exists, ensure keys are present and build prov_map
        prov_map = {}
        has_providers = bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='providers'").fetchone())
        if has_providers:
            for key in sorted(prov_keys):
                cur.execute(
                    "INSERT OR IGNORE INTO providers (key, friendly_name, icon) VALUES (?, ?, ?)",
                    (key, key.title(), ""),
                )
            conn.commit()
            cur.execute("SELECT id, key FROM providers")
            prov_map = {row[1]: row[0] for row in cur.fetchall()}

        # Update resource_types.provider_id using derived provider where missing (if resource_types table exists)
        has_resource_types = bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='resource_types'").fetchone())
        rt_updates = 0
        if has_resource_types:
            rt_rows = cur.execute("SELECT id, terraform_type, provider_id FROM resource_types").fetchall()
            for row in rt_rows:
                rt_id, tf_type, current_pid = row
                pkey = rtdb._derive(tf_type).get("provider", "unknown")
                if not pkey or pkey == "unknown":
                    continue
                pid = prov_map.get(pkey)
                if has_providers and not pid:
                    cur.execute(
                        "INSERT OR IGNORE INTO providers (key, friendly_name, icon) VALUES (?, ?, ?)",
                        (pkey, pkey.title(), ""),
                    )
                    conn.commit()
                    pid = cur.execute("SELECT id FROM providers WHERE key=?", (pkey,)).fetchone()[0]
                    prov_map[pkey] = pid
                if current_pid != pid and pid is not None:
                    cur.execute("UPDATE resource_types SET provider_id = ? WHERE id = ?", (pid, rt_id))
                    rt_updates += 1
            conn.commit()
        updates["resource_types_provider_ids"] = rt_updates

        # Backfill textual provider on resources table for legacy rows (do this regardless of providers table existence)
        has_resources = bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='resources'").fetchone())
        res_updates = 0
        if has_resources:
            res_rows = cur.execute(
                "SELECT id, resource_type FROM resources WHERE provider IS NULL OR TRIM(provider) = '' OR lower(provider) = 'unknown'"
            ).fetchall()
            for rid, rtype in res_rows:
                # Prefer provider from resource_types table if available
                pkey = None
                if has_resource_types:
                    p_row = cur.execute(
                        "SELECT p.key FROM resource_types rt LEFT JOIN providers p ON rt.provider_id = p.id WHERE rt.terraform_type = ?",
                        (rtype,),
                    ).fetchone()
                    if p_row and p_row[0]:
                        pkey = p_row[0]
                if not pkey:
                    pkey = rtdb._derive(rtype).get("provider", "unknown")
                if not pkey or pkey == "unknown":
                    continue
                cur.execute("UPDATE resources SET provider = ? WHERE id = ?", (pkey, rid))
                res_updates += 1
            conn.commit()
        updates["resources_provider_backfill"] = res_updates

        # Backfill resource_nodes.provider as well
        has_resource_nodes = bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='resource_nodes'").fetchone())
        rn_updates = 0
        if has_resource_nodes:
            rn_rows = cur.execute(
                "SELECT id, resource_type FROM resource_nodes WHERE provider IS NULL OR TRIM(provider) = '' OR lower(provider) = 'unknown'"
            ).fetchall()
            for nid, ntype in rn_rows:
                pkey = None
                if has_resource_types:
                    p_row = cur.execute(
                        "SELECT p.key FROM resource_types rt LEFT JOIN providers p ON rt.provider_id = p.id WHERE rt.terraform_type = ?",
                        (ntype,),
                    ).fetchone()
                    if p_row and p_row[0]:
                        pkey = p_row[0]
                if not pkey:
                    pkey = rtdb._derive(ntype).get("provider", "unknown")
                if not pkey or pkey == "unknown":
                    continue
                cur.execute("UPDATE resource_nodes SET provider = ? WHERE id = ?", (pkey, nid))
                rn_updates += 1
            conn.commit()
        updates["resource_nodes_provider_backfill"] = rn_updates

    except Exception:
        # Best-effort backfill: do not fail migrations on errors
        pass

    return updates


def _ensure_schema(conn: sqlite3.Connection):
    """Ensure tables used by db_helpers exist on the active database.

    To avoid transient sqlite "database is locked" errors during concurrent runs,
    acquire a lightweight filesystem-based migration lock so only one process
    applies schema migrations at a time. This avoids multiple processes attempting
    concurrent ALTER TABLE/CREATE INDEX operations which often lead to "database
    is locked" errors on sqlite.
    """
    # Skip DDL entirely if this process has already ensured the schema for this DB.
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if db_file in _schema_ensured_for:
        return

    # Migration lock file (sibling to DB file)
    lock_path = COZO_DB.with_name(COZO_DB.name + ".schema_lock")
    lock_fd = None
    acquired = False
    # Try to acquire an exclusive lock file with retries
    for attempt in range(6):
        try:
            # O_CREAT | O_EXCL ensures this fails if file already exists
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            # Write PID for diagnostics
            try:
                os.write(lock_fd, str(os.getpid()).encode())
            except Exception:
                pass
            acquired = True
            break
        except FileExistsError:
            # Another process is running migrations; wait and retry
            time.sleep(1 + attempt)
            continue
        except Exception:
            # If something unexpected happens, don't block migrations entirely
            break

    if not acquired:
        # If lock couldn't be acquired, wait for the lock file to disappear and
        # attempt to acquire it again. If the lock appears stale (>5min) remove it.
        wait_start = time.time()
        waited = False
        while lock_path.exists() and (time.time() - wait_start) < 30:
            time.sleep(1)
            waited = True
        if lock_path.exists():
            try:
                mtime = os.path.getmtime(str(lock_path))
                # Remove stale lock older than 5 minutes
                if time.time() - mtime > 300:
                    try:
                        os.unlink(str(lock_path))
                    except Exception:
                        pass
            except Exception:
                pass
        # Try one final time to create the lock before proceeding
        if not acquired:
            try:
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(lock_fd, str(os.getpid()).encode())
                except Exception:
                    pass
                acquired = True
            except Exception:
                # Give up acquiring lock; proceed but migrations may contend
                pass

    # Perform migrations under a try/finally so the lock is removed on exit
    try:
        conn.executescript("""
    CREATE TABLE IF NOT EXISTS repositories (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      repo_url TEXT,
      repo_type TEXT,
      primary_language TEXT,
      files_scanned INTEGER,
      iac_files_count INTEGER,
      code_files_count INTEGER,
      scanned_at TIMESTAMP DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      UNIQUE(experiment_id, repo_name)
    );

    CREATE TABLE IF NOT EXISTS resources (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      repo_id INTEGER NOT NULL,
      resource_name TEXT NOT NULL,
      resource_type TEXT NOT NULL,
      provider TEXT,
      region TEXT,
      discovered_by TEXT,
      discovery_method TEXT,
      source_file TEXT,
      source_line_start INTEGER,
      source_line_end INTEGER,
      parent_resource_id INTEGER,
      status TEXT DEFAULT 'active',
      first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, repo_id, resource_type, resource_name)
    );

    CREATE TABLE IF NOT EXISTS resource_properties (
      id INTEGER PRIMARY KEY,
      resource_id INTEGER NOT NULL,
      property_key TEXT NOT NULL,
      property_value TEXT,
      property_type TEXT,
      is_security_relevant BOOLEAN DEFAULT 0,
      UNIQUE(resource_id, property_key)
    );

    CREATE TABLE IF NOT EXISTS resource_connections (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      source_resource_id INTEGER NOT NULL,
      target_resource_id INTEGER NOT NULL,
      source_repo_id INTEGER,
      target_repo_id INTEGER,
      is_cross_repo BOOLEAN DEFAULT 0,
      connection_type TEXT,
      protocol TEXT,
      port TEXT,
      authentication TEXT,
      authorization TEXT,
      auth_method TEXT,
      is_encrypted BOOLEAN,
      via_component TEXT,
      notes TEXT,
      inferred_internet BOOLEAN DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS trust_boundaries (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      name TEXT NOT NULL,
      boundary_type TEXT,
      provider TEXT,
      region TEXT,
      description TEXT,
      notes TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS trust_boundary_members (
      trust_boundary_id INTEGER NOT NULL,
      resource_id INTEGER NOT NULL,
      PRIMARY KEY (trust_boundary_id, resource_id)
    );

    CREATE TABLE IF NOT EXISTS data_flows (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      name TEXT NOT NULL,
      flow_type TEXT,
      description TEXT,
      notes TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS data_flow_steps (
      id INTEGER PRIMARY KEY,
      flow_id INTEGER NOT NULL,
      step_order INTEGER NOT NULL,
      resource_id INTEGER,
      component_label TEXT,
      protocol TEXT,
      port TEXT,
      auth_method TEXT,
      is_encrypted BOOLEAN,
      notes TEXT
    );

    CREATE TABLE IF NOT EXISTS context_questions (
      id INTEGER PRIMARY KEY,
      question_key TEXT UNIQUE NOT NULL,
      question_text TEXT NOT NULL,
      question_category TEXT
    );

    CREATE TABLE IF NOT EXISTS context_answers (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      question_id INTEGER NOT NULL,
      answer_value TEXT,
      answer_confidence TEXT,
      evidence_source TEXT,
      evidence_type TEXT,
      answered_by TEXT,
      answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Subscription-wide Q&A (applies across repos within an experiment/subscription context)
    CREATE TABLE IF NOT EXISTS subscription_context (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      scope_key TEXT DEFAULT 'global',
      repo_name TEXT,
      question TEXT NOT NULL,
      answer TEXT,
      answered_by TEXT,
      confidence REAL,
      tags TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS skeptic_reviews (
      id INTEGER PRIMARY KEY,
      finding_id INTEGER NOT NULL,
      reviewer_type TEXT NOT NULL,
      score_adjustment REAL,
      adjusted_score REAL,
      confidence REAL,
      reasoning TEXT,
      key_concerns TEXT,
      mitigating_factors TEXT,
      recommendation TEXT DEFAULT 'confirm',
      reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS risk_score_history (
      id INTEGER PRIMARY KEY,
      finding_id INTEGER NOT NULL,
      score REAL NOT NULL,
      scored_by TEXT,
      rationale TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS context_metadata (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      repo_id INTEGER,
      namespace TEXT DEFAULT 'phase2',
      key TEXT NOT NULL,
      value TEXT,
      source TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, repo_id, namespace, key)
    );

    CREATE TABLE IF NOT EXISTS resource_nodes (
      id INTEGER PRIMARY KEY,
      resource_type TEXT NOT NULL,
      terraform_name TEXT NOT NULL,
      canonical_name TEXT,
      friendly_name TEXT,
      display_label TEXT,
      provider TEXT,
      source_repo TEXT,
      aliases TEXT DEFAULT '[]',
      confidence TEXT DEFAULT 'extracted',
      properties TEXT DEFAULT '{}',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(resource_type, terraform_name, source_repo)
    );

    CREATE TABLE IF NOT EXISTS resource_relationships (
      id INTEGER PRIMARY KEY,
      source_id INTEGER NOT NULL,
      target_id INTEGER NOT NULL,
      relationship_type TEXT NOT NULL,
      source_repo TEXT,
      confidence TEXT DEFAULT 'extracted',
      notes TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(source_id, target_id, relationship_type)
    );

    CREATE TABLE IF NOT EXISTS findings (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT,
      repo_id INTEGER,
      title TEXT,
      description TEXT,
      severity TEXT,
      severity_score INTEGER,
      resource_id INTEGER,
      rule_id TEXT,
      source_file TEXT,
      source_line_start INTEGER,
      source_line_end INTEGER,
      code_snippet TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS resource_equivalences (
      id INTEGER PRIMARY KEY,
      resource_node_id INTEGER NOT NULL,
      candidate_resource_type TEXT NOT NULL,
      candidate_terraform_name TEXT NOT NULL,
      candidate_source_repo TEXT NOT NULL,
      equivalence_kind TEXT NOT NULL DEFAULT 'cross_repo_alias',
      confidence TEXT DEFAULT 'medium',
      evidence_level TEXT DEFAULT 'inferred',
      provenance TEXT NOT NULL,
      context TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(
        resource_node_id,
        candidate_resource_type,
        candidate_terraform_name,
        candidate_source_repo,
        equivalence_kind
      )
    );

    CREATE TABLE IF NOT EXISTS enrichment_queue (
      id INTEGER PRIMARY KEY,
      resource_node_id INTEGER,
      relationship_id INTEGER,
      gap_type TEXT NOT NULL,
      context TEXT,
      assumption_text TEXT,
      assumption_basis TEXT,
      confidence TEXT DEFAULT 'medium',
      suggested_value TEXT,
      status TEXT DEFAULT 'pending_review',
      resolved_by TEXT,
      resolved_at TIMESTAMP,
      rejection_reason TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS repo_ai_content (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      section_key TEXT NOT NULL,
      title TEXT NOT NULL,
      content_html TEXT,
      generated_by TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, repo_name, section_key)
    );

    CREATE TABLE IF NOT EXISTS cloud_diagrams (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      repo_name TEXT,
      provider TEXT NOT NULL,
      diagram_title TEXT NOT NULL,
      mermaid_code TEXT NOT NULL,
      display_order INTEGER DEFAULT 0,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, provider, diagram_title)
    );

    CREATE TABLE IF NOT EXISTS exposure_analysis (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      resource_id INTEGER NOT NULL,
      resource_name TEXT NOT NULL,
      resource_type TEXT NOT NULL,
      provider TEXT NOT NULL,
      normalized_role TEXT NOT NULL,
      is_entry_point BOOLEAN DEFAULT 0,
      is_countermeasure BOOLEAN DEFAULT 0,
      is_compute_or_data BOOLEAN DEFAULT 0,
      exposure_level TEXT DEFAULT 'isolated',
      exposure_path TEXT,
      has_internet_path BOOLEAN DEFAULT 0,
      opengrep_violations TEXT DEFAULT '[]',
      base_severity TEXT,
      risk_score REAL DEFAULT 0,
      confidence TEXT DEFAULT 'medium',
      notes TEXT,
      computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, resource_id),
      FOREIGN KEY (experiment_id) REFERENCES experiments(id)
    );

    CREATE TABLE IF NOT EXISTS internet_exposure_paths (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      path_id TEXT NOT NULL,
      source_resource_id INTEGER NOT NULL,
      target_resource_id INTEGER NOT NULL,
      path_length INTEGER DEFAULT 0,
      path_nodes TEXT NOT NULL,
      has_countermeasure BOOLEAN DEFAULT 0,
      countermeasures_in_path TEXT DEFAULT '[]',
      validation_status TEXT DEFAULT 'pending',
      validated_by TEXT,
      validated_at TIMESTAMP,
      notes TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, path_id),
      FOREIGN KEY (experiment_id) REFERENCES experiments(id)
    );

    CREATE TABLE IF NOT EXISTS exposure_risk_scoring (
      id INTEGER PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      resource_id INTEGER NOT NULL,
      opengrep_rule_id TEXT,
      rule_severity TEXT,
      severity_score REAL DEFAULT 0,
      exposure_multiplier REAL DEFAULT 1.0,
      final_risk_score REAL DEFAULT 0,
      exposure_factor TEXT,
      vulnerability_factor TEXT,
      combined_factors TEXT DEFAULT '{}',
      scoring_method TEXT DEFAULT 'exposure_plus_vuln',
      computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(experiment_id, resource_id, opengrep_rule_id),
      FOREIGN KEY (experiment_id) REFERENCES experiments(id)
    );

    CREATE TABLE IF NOT EXISTS shared_resources (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      resource_type TEXT NOT NULL,
      resource_identifier TEXT NOT NULL,
      friendly_name TEXT,
      provider TEXT NOT NULL,
      category TEXT,
      discovered_from_repo TEXT,
      discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      reference_count INTEGER DEFAULT 1,
      variable_name TEXT,
      data_source_name TEXT,
      properties TEXT,
      UNIQUE(provider, resource_type, resource_identifier)
    );

    CREATE TABLE IF NOT EXISTS shared_resource_references (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      shared_resource_id INTEGER NOT NULL,
      repo_name TEXT NOT NULL,
      experiment_id TEXT,
      local_resource_id INTEGER,
      reference_type TEXT,
      discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (shared_resource_id) REFERENCES shared_resources(id),
      FOREIGN KEY (local_resource_id) REFERENCES resources(id),
      UNIQUE(shared_resource_id, repo_name, local_resource_id)
    );
    """)

        # Ensure repo-scoped uniqueness index exists for the newer upsert behavior.
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_diagrams_repo_provider_title ON cloud_diagrams(repo_name, provider, diagram_title)"
            )
        except Exception:
            pass

        # Ensure indexes for shared_resources tables
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shared_resources_lookup ON shared_resources(provider, resource_type, resource_identifier)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shared_resource_references_repo ON shared_resource_references(repo_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shared_resource_references_shared ON shared_resource_references(shared_resource_id)"
            )
        except Exception:
            pass



        # Ensure optional columns exist for backward compatibility.
        resource_columns = {row[1] for row in conn.execute("PRAGMA table_info(resources)").fetchall()}
        if "parent_resource_id" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN parent_resource_id INTEGER")

        connection_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_connections)").fetchall()}
        for col_name, col_type in (
            ("source_repo_id", "INTEGER"),
            ("target_repo_id", "INTEGER"),
            ("is_cross_repo", "BOOLEAN DEFAULT 0"),
            ("connection_type", "TEXT"),
            ("protocol", "TEXT"),
            ("port", "TEXT"),
            ("authentication", "TEXT"),
            ("authorization", "TEXT"),
            ("auth_method", "TEXT"),
            ("is_encrypted", "BOOLEAN"),
            ("via_component", "TEXT"),
            ("notes", "TEXT"),
        ):
            if col_name not in connection_columns:
                conn.execute(f"ALTER TABLE resource_connections ADD COLUMN {col_name} {col_type}")

        flow_columns = {row[1] for row in conn.execute("PRAGMA table_info(data_flows)").fetchall()}
        for col_name, col_type in (
            ("flow_type", "TEXT"),
            ("description", "TEXT"),
            ("notes", "TEXT"),
            ("created_at", "TIMESTAMP"),
        ):
            if col_name not in flow_columns:
                conn.execute(f"ALTER TABLE data_flows ADD COLUMN {col_name} {col_type}")

        flow_step_columns = {row[1] for row in conn.execute("PRAGMA table_info(data_flow_steps)").fetchall()}
        for col_name, col_type in (
            ("resource_id", "INTEGER"),
            ("component_label", "TEXT"),
            ("protocol", "TEXT"),
            ("port", "TEXT"),
            ("auth_method", "TEXT"),
            ("is_encrypted", "BOOLEAN"),
            ("notes", "TEXT"),
        ):
            if col_name not in flow_step_columns:
                conn.execute(f"ALTER TABLE data_flow_steps ADD COLUMN {col_name} {col_type}")

        node_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_nodes)").fetchall()}
        for col_name, col_type in (
            ("canonical_name", "TEXT"),
            ("friendly_name", "TEXT"),
            ("display_label", "TEXT"),
            ("provider", "TEXT"),
            ("source_repo", "TEXT"),
            ("aliases", "TEXT DEFAULT '[]'"),
            ("confidence", "TEXT DEFAULT 'extracted'"),
            ("properties", "TEXT DEFAULT '{}'"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
        ):
            if col_name not in node_columns:
                conn.execute(f"ALTER TABLE resource_nodes ADD COLUMN {col_name} {col_type}")

        relationship_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_relationships)").fetchall()}
        for col_name, col_type in (
            ("source_repo", "TEXT"),
            ("confidence", "TEXT DEFAULT 'extracted'"),
            ("notes", "TEXT"),
            ("created_at", "TIMESTAMP"),
        ):
            if col_name not in relationship_columns:
                conn.execute(f"ALTER TABLE resource_relationships ADD COLUMN {col_name} {col_type}")

        equivalence_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_equivalences)").fetchall()}
        for col_name, col_type in (
            ("resource_node_id", "INTEGER"),
            ("candidate_resource_type", "TEXT"),
            ("candidate_terraform_name", "TEXT"),
            ("candidate_source_repo", "TEXT"),
            ("equivalence_kind", "TEXT DEFAULT 'cross_repo_alias'"),
            ("confidence", "TEXT DEFAULT 'medium'"),
            ("evidence_level", "TEXT DEFAULT 'inferred'"),
            ("provenance", "TEXT"),
            ("context", "TEXT"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
        ):
            if col_name not in equivalence_columns:
                conn.execute(f"ALTER TABLE resource_equivalences ADD COLUMN {col_name} {col_type}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_node "
            "ON resource_equivalences(resource_node_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_candidate "
            "ON resource_equivalences(candidate_source_repo, candidate_resource_type, candidate_terraform_name)"
        )

        queue_columns = {row[1] for row in conn.execute("PRAGMA table_info(enrichment_queue)").fetchall()}
        for col_name, col_type in (
            ("assumption_basis", "TEXT"),
            ("confidence", "TEXT DEFAULT 'medium'"),
            ("suggested_value", "TEXT"),
            ("status", "TEXT DEFAULT 'pending_review'"),
            ("resolved_by", "TEXT"),
            ("resolved_at", "TIMESTAMP"),
            ("rejection_reason", "TEXT"),
            ("created_at", "TIMESTAMP"),
        ):
            if col_name not in queue_columns:
                conn.execute(f"ALTER TABLE enrichment_queue ADD COLUMN {col_name} {col_type}")

        resource_types_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_types'"
        ).fetchone()
        if resource_types_exists:
            resource_type_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_types)").fetchall()}
            for col_name, col_type in (
                ("display_on_architecture_chart", "BOOLEAN DEFAULT 1"),
                ("parent_type", "TEXT"),
            ):
                if col_name not in resource_type_columns:
                    conn.execute(f"ALTER TABLE resource_types ADD COLUMN {col_name} {col_type}")
            conn.execute(
                "UPDATE resource_types SET display_on_architecture_chart = 1 "
                "WHERE display_on_architecture_chart IS NULL"
            )

        findings_columns = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
        if "repo_id" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN repo_id INTEGER")
        if "resource_id" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN resource_id INTEGER")
        if "category" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN category TEXT")
        if "base_severity" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN base_severity TEXT")
        if "evidence_location" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN evidence_location TEXT")
        if "title" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN title TEXT")
        if "description" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN description TEXT")
        if "severity_score" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN severity_score INTEGER")
        if "source_file" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN source_file TEXT")
        if "source_line_start" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN source_line_start INTEGER")
        if "source_line_end" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN source_line_end INTEGER")
        if "code_snippet" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN code_snippet TEXT")
        if "reason" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN reason TEXT")
        if "rule_id" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN rule_id TEXT")
        if "proposed_fix" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN proposed_fix TEXT")
        if "llm_enriched_at" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN llm_enriched_at TIMESTAMP")

        # Human/AI triage feedback (learning signal)
        if "triage_status" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN triage_status TEXT")
            # Default to 'valid' for existing rows
            try:
                conn.execute("UPDATE findings SET triage_status = 'valid' WHERE triage_status IS NULL")
            except Exception:
                pass
        if "triage_reason" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN triage_reason TEXT")
        if "triage_set_by" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN triage_set_by TEXT")
        if "triage_set_at" not in findings_columns:
            conn.execute("ALTER TABLE findings ADD COLUMN triage_set_at TIMESTAMP")

        # Exposure analysis table columns (ensure they exist for backward compatibility)
        exposure_columns = {row[1] for row in conn.execute("PRAGMA table_info(exposure_analysis)").fetchall()}
        for col_name, col_type in (
            ("experiment_id", "TEXT"),
            ("resource_id", "INTEGER"),
            ("resource_name", "TEXT"),
            ("resource_type", "TEXT"),
            ("provider", "TEXT"),
            ("normalized_role", "TEXT"),
            ("is_entry_point", "BOOLEAN"),
            ("is_countermeasure", "BOOLEAN"),
            ("is_compute_or_data", "BOOLEAN"),
            ("exposure_level", "TEXT"),
            ("exposure_path", "TEXT"),
            ("has_internet_path", "BOOLEAN"),
            ("opengrep_violations", "TEXT"),
            ("base_severity", "TEXT"),
            ("risk_score", "REAL"),
            ("confidence", "TEXT"),
            ("notes", "TEXT"),
            ("computed_at", "TIMESTAMP"),
        ):
            if col_name not in exposure_columns:
                conn.execute(f"ALTER TABLE exposure_analysis ADD COLUMN {col_name} {col_type}")
        
        # Create indexes for exposure_analysis queries
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exposure_analysis_experiment_level "
            "ON exposure_analysis(experiment_id, exposure_level)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exposure_analysis_provider "
            "ON exposure_analysis(experiment_id, provider, normalized_role)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exposure_analysis_risk "
            "ON exposure_analysis(experiment_id, risk_score DESC)"
        )

        # Internet exposure paths table columns
        path_columns = {row[1] for row in conn.execute("PRAGMA table_info(internet_exposure_paths)").fetchall()}
        for col_name, col_type in (
            ("experiment_id", "TEXT"),
            ("path_id", "TEXT"),
            ("source_resource_id", "INTEGER"),
            ("target_resource_id", "INTEGER"),
            ("path_length", "INTEGER"),
            ("path_nodes", "TEXT"),
            ("has_countermeasure", "BOOLEAN"),
            ("countermeasures_in_path", "TEXT"),
            ("validation_status", "TEXT"),
            ("validated_by", "TEXT"),
            ("validated_at", "TIMESTAMP"),
            ("notes", "TEXT"),
            ("created_at", "TIMESTAMP"),
        ):
            if col_name not in path_columns:
                conn.execute(f"ALTER TABLE internet_exposure_paths ADD COLUMN {col_name} {col_type}")
        
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_internet_exposure_paths_experiment "
            "ON internet_exposure_paths(experiment_id, source_resource_id)"
        )

        # Exposure risk scoring table columns
        score_columns = {row[1] for row in conn.execute("PRAGMA table_info(exposure_risk_scoring)").fetchall()}
        for col_name, col_type in (
            ("experiment_id", "TEXT"),
            ("resource_id", "INTEGER"),
            ("opengrep_rule_id", "TEXT"),
            ("rule_severity", "TEXT"),
            ("severity_score", "REAL"),
            ("exposure_multiplier", "REAL"),
            ("final_risk_score", "REAL"),
            ("exposure_factor", "TEXT"),
            ("vulnerability_factor", "TEXT"),
            ("combined_factors", "TEXT"),
            ("scoring_method", "TEXT"),
            ("computed_at", "TIMESTAMP"),
        ):
            if col_name not in score_columns:
                conn.execute(f"ALTER TABLE exposure_risk_scoring ADD COLUMN {col_name} {col_type}")
        
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exposure_risk_scoring_resource "
            "ON exposure_risk_scoring(experiment_id, resource_id)"
        )

        apply_topology_backfills(conn)
    finally:
        # Release filesystem migration lock if we acquired one
        try:
            if acquired and lock_fd is not None:
                try:
                    os.close(lock_fd)
                except Exception:
                    pass
                try:
                    os.unlink(str(lock_path))
                except Exception:
                    pass
        except Exception:
            # Best-effort cleanup only; don't fail migrations for cleanup errors
            pass

    # Mark schema as ensured for this DB path so subsequent connections skip DDL.
    _schema_ensured_for.add(db_file)


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    """Context manager for database connections."""
    path = db_path or DB_PATH
    # Ensure parent directory exists so sqlite can create the DB file if needed
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.row_factory = sqlite3.Row  # Access columns by name
    # Ensure schema with retries to avoid concurrent migration lock errors
    for attempt in range(6):
        try:
            _ensure_schema(conn)
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 5:
                time.sleep(1 + attempt)
                continue
            raise
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# REPOSITORY OPERATIONS
# ============================================================================

def insert_repository(
    experiment_id: str,
    repo_path: Path,
    repo_type: str = "Infrastructure"
) -> Tuple[int, str]:
    """Register repository - store only folder name (portable)."""
    
    # Extract just the folder name
    repo_name = repo_path.name
    
    # Try to get git remote URL
    repo_url = None
    try:
        import git
        repo_obj = git.Repo(repo_path)
        if repo_obj.remotes:
            repo_url = repo_obj.remotes.origin.url
    except Exception:
        pass
    
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO repositories
            (experiment_id, repo_name, repo_url, repo_type, scanned_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            RETURNING id
        """, (experiment_id, repo_name, repo_url, repo_type))
        
        row = cursor.fetchone()
        if row:
            return row[0], repo_name
        
        # Already exists, get ID
        existing = conn.execute("""
            SELECT id FROM repositories 
            WHERE experiment_id = ? AND repo_name = ?
        """, (experiment_id, repo_name)).fetchone()
        
        return existing[0], repo_name


def update_repository_stats(
    experiment_id: str,
    repo_name: str,
    files_scanned: int,
    iac_files: int,
    code_files: int
):
    """Update repository scan statistics."""
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE repositories 
            SET files_scanned = ?,
                iac_files_count = ?,
                code_files_count = ?
            WHERE experiment_id = ? AND repo_name = ?
        """, (files_scanned, iac_files, code_files, experiment_id, repo_name))


def ensure_repository_entry(experiment_id: str, repo_name: str) -> int:
    """Ensure a repository record exists for the experiment."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO repositories (experiment_id, repo_name)
            VALUES (?, ?)
        """, (experiment_id, repo_name))
        if cursor.lastrowid:
            return cursor.lastrowid
        row = conn.execute("""
            SELECT id FROM repositories
            WHERE experiment_id = ? AND repo_name = ?
        """, (experiment_id, repo_name)).fetchone()
        return row[0]


def get_repository_id(experiment_id: str, repo_name: str) -> Optional[int]:
    """Return repository ID if registered."""
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT id FROM repositories
            WHERE experiment_id = ? AND repo_name = ?
        """, (experiment_id, repo_name)).fetchone()
        return row[0] if row else None


def upsert_context_metadata(
    experiment_id: str,
    repo_name: str,
    key: str,
    value: str,
    *,
    namespace: str = "phase2",
    source: str = "phase2_context_summary"
):
    """Store structured context metadata for Phase 2 discoveries."""
    repo_id = ensure_repository_entry(experiment_id, repo_name)
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO context_metadata
            (experiment_id, repo_id, namespace, key, value, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id, repo_id, namespace, key) DO UPDATE SET
              value = excluded.value,
              source = excluded.source,
              created_at = CURRENT_TIMESTAMP
        """, (experiment_id, repo_id, namespace, key, value, source))


# ============================================================================
# RESOURCE OPERATIONS
# ============================================================================

def insert_resource(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    resource_type: str,
    provider: str,
    source_file: str,
    source_line: Optional[int] = None,
    source_line_end: Optional[int] = None,
    parent_resource_id: Optional[int] = None,
    properties: Optional[Dict[str, Any]] = None
) -> int:
    """Insert resource with optional line numbers and parent relationship."""
    with get_db_connection() as conn:
        # Get repo_id
        repo_id = conn.execute("""
            SELECT id FROM repositories
            WHERE experiment_id = ? AND repo_name = ?
        """, (experiment_id, repo_name)).fetchone()
        
        if not repo_id:
            raise ValueError(f"Repository {repo_name} not registered in experiment {experiment_id}")
        
        cursor = conn.execute("""
            INSERT OR REPLACE INTO resources 
            (experiment_id, repo_id, resource_name, resource_type, provider, 
             discovered_by, discovery_method, source_file, source_line_start, source_line_end,
             parent_resource_id)
            VALUES (?, ?, ?, ?, ?, 'ContextDiscoveryAgent', 'Terraform', ?, ?, ?, ?)
            RETURNING id
        """, (experiment_id, repo_id[0], resource_name, resource_type, provider, 
              source_file, source_line, source_line_end, parent_resource_id))
        
        resource_id = cursor.fetchone()[0]
        
        # Insert properties
        if properties:
            for key, value in properties.items():
                conn.execute("""
                    INSERT OR REPLACE INTO resource_properties
                    (resource_id, property_key, property_value, property_type, is_security_relevant)
                    VALUES (?, ?, ?, ?, ?)
                """, (resource_id, key, str(value), 
                      _infer_property_type(key), 
                      _is_security_relevant(key)))

    # Record provenance AFTER the main transaction commits to avoid a write-lock
    # deadlock: cozo_helpers._execute_sql opens its own sqlite3 connection, which
    # would block waiting for this connection to release while we'd be waiting for
    # it to return — each call would time out after Python's default 5s connect timeout.
    if _COZO_HELPERS_AVAILABLE:
        try:
            cozo_helpers._insert_relationship_audit(
                from_node=f"resource:{resource_id}",
                to_node=f"resource:{resource_id}",
                rel_type="resource_created",
                action="created",
                actor_type="context_discovery",
                actor_id=experiment_id,
                scan_id=experiment_id,
                evidence_finding_id=None,
                confidence=None,
                details_json=json.dumps({"repo": repo_name, "resource_type": resource_type}),
            )
        except Exception:
            pass

    return resource_id


def ensure_inferred_aks_cluster(experiment_id: str, repo_name: str) -> Optional[int]:
    """Ensure a synthetic AKS cluster exists when k8s workloads exist but no cluster was scanned.

    This happens when a repo deploys to AKS via a remote Terraform module (e.g., terraform-aks)
    and only Skaffold/K8s workloads are visible locally.

    NOTE: We do NOT parent kubernetes_* resources under the cluster in DB because some
    diagram queries filter to root nodes only.
    """
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1",
                (experiment_id, repo_name),
            ).fetchone()
            if not repo_row:
                return None
            repo_id = int(repo_row[0])

            has_k8s = conn.execute(
                "SELECT 1 FROM resources WHERE experiment_id=? AND repo_id=? AND resource_type LIKE 'kubernetes_%' LIMIT 1",
                (experiment_id, repo_id),
            ).fetchone()
            if not has_k8s:
                return None

            existing = conn.execute(
                "SELECT id FROM resources WHERE experiment_id=? AND repo_id=? AND resource_type='azurerm_kubernetes_cluster' LIMIT 1",
                (experiment_id, repo_id),
            ).fetchone()
            if existing:
                return int(existing[0])

            inferred_name = f"__inferred__{repo_name}-aks-cluster"
            inferred_existing = conn.execute(
                "SELECT id FROM resources WHERE experiment_id=? AND repo_id=? AND resource_type='azurerm_kubernetes_cluster' AND resource_name=? LIMIT 1",
                (experiment_id, repo_id, inferred_name),
            ).fetchone()
            if inferred_existing:
                cluster_id = int(inferred_existing[0])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO resources
                      (experiment_id, repo_id, resource_name, resource_type, provider, discovered_by, discovery_method, source_file, source_line_start, status)
                    VALUES
                      (?, ?, ?, 'azurerm_kubernetes_cluster', 'azure', 'Inference', 'k8s_workloads', 'inferred:k8s_workloads', 1, 'active')
                    RETURNING id
                    """,
                    (experiment_id, repo_id, inferred_name),
                )
                row = cur.fetchone()
                cluster_id = int(row[0]) if row else None

                if cluster_id:
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO resource_properties (resource_id, property_key, property_value, property_type, is_security_relevant) VALUES (?,?,?,?,?)",
                            (cluster_id, 'inferred', 'true', 'string', 0),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO resource_properties (resource_id, property_key, property_value, property_type, is_security_relevant) VALUES (?,?,?,?,?)",
                            (cluster_id, 'inference_source', 'k8s_workloads', 'string', 0),
                        )
                    except Exception:
                        pass

            return cluster_id
    except Exception:
        return None


def infer_aks_cluster_link(experiment_id: str, repo_name: str) -> None:
    """Try to link an inferred AKS cluster to a real AKS cluster from a scanned module repo.

    If we can't resolve a single cluster, store an AI-suggested open question in ai_open_questions.
    """
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id=? AND LOWER(repo_name)=LOWER(?) LIMIT 1",
                (experiment_id, repo_name),
            ).fetchone()
            if not repo_row:
                return
            repo_id = int(repo_row[0])

            inferred_row = conn.execute(
                "SELECT id, resource_name FROM resources WHERE experiment_id=? AND repo_id=? AND resource_type='azurerm_kubernetes_cluster' AND resource_name LIKE '__inferred__%' LIMIT 1",
                (experiment_id, repo_id),
            ).fetchone()
            if not inferred_row:
                return
            inferred_cluster_id = int(inferred_row[0])
            inferred_cluster_name = str(inferred_row[1] or '').strip()

            # If we already have a cross-repo link from this inferred cluster, do nothing.
            existing_link = conn.execute(
                "SELECT 1 FROM resource_connections WHERE experiment_id=? AND source_resource_id=? AND is_cross_repo=1 LIMIT 1",
                (experiment_id, inferred_cluster_id),
            ).fetchone()
            if existing_link:
                return

            # Parse terraform module sources from phase2_code metadata.
            mod_rows = conn.execute(
                "SELECT key, value FROM context_metadata WHERE experiment_id=? AND repo_id=? AND namespace='phase2_code' AND key LIKE 'terraform.module.%' ORDER BY id DESC LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall()

            candidate_repos: list[tuple[str, str, int | None]] = []  # (repo_name, file, line)
            for r in mod_rows:
                try:
                    k = str(r['key'] or '')
                    v = str(r['value'] or '')
                    import json as _json
                    j = _json.loads(v) if v.strip().startswith('{') else {}
                    src = str(j.get('source') or v)
                    file = str(j.get('file') or '')
                    line = j.get('line')
                except Exception:
                    src = v
                    file = ''
                    line = None

                m = re.search(r"/_git/([^/]+)", src)
                if m:
                    candidate_repos.append((m.group(1), file, line))
                    continue
                m2 = re.search(r"/([^/]+?)(?:\.git)?(?:(?://|\?)|$)", src)
                if m2 and 'terraform' in m2.group(1).lower():
                    candidate_repos.append((m2.group(1), file, line))

            # Dedup preserving order
            seen = set()
            candidate_repos = [(a, f, l) for (a, f, l) in candidate_repos if not (a.lower() in seen or seen.add(a.lower()))]

            resolved: list[tuple[str, int, str]] = []  # (repo_name, cluster_resource_id, cluster_resource_name)
            for cand_repo, f, l in candidate_repos:
                rr = conn.execute(
                    "SELECT id FROM repositories WHERE experiment_id=? AND LOWER(repo_name)=LOWER(?) LIMIT 1",
                    (experiment_id, cand_repo),
                ).fetchone()
                if not rr:
                    continue
                cand_repo_id = int(rr[0])
                clusters = conn.execute(
                    "SELECT id, resource_name FROM resources WHERE experiment_id=? AND repo_id=? AND resource_type='azurerm_kubernetes_cluster' LIMIT 5",
                    (experiment_id, cand_repo_id),
                ).fetchall()
                if len(clusters) == 1:
                    resolved.append((cand_repo, int(clusters[0][0]), str(clusters[0][1] or '').strip()))

            if len(resolved) == 1:
                cand_repo, target_cluster_id, target_cluster_name = resolved[0]
                # Create a cross-repo connection inferred_cluster -> target_cluster.
                try:
                    insert_connection(
                        experiment_id=experiment_id,
                        source_name=inferred_cluster_name,
                        target_name=target_cluster_name,
                        connection_type='equivalent_to',
                        source_repo=repo_name,
                        target_repo=cand_repo,
                        notes='Inferred: repo uses Terraform module that likely provisions AKS cluster',
                    )
                except Exception:
                    pass
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO resource_properties (resource_id, property_key, property_value, property_type, is_security_relevant) VALUES (?,?,?,?,?)",
                        (inferred_cluster_id, 'linked_cluster_repo', cand_repo, 'string', 0),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO resource_properties (resource_id, property_key, property_value, property_type, is_security_relevant) VALUES (?,?,?,?,?)",
                        (inferred_cluster_id, 'linked_cluster_name', target_cluster_name, 'string', 0),
                    )
                except Exception:
                    pass
                return

            # Otherwise: emit an AI-suggested open question so the Q&A tab can capture it.
            trigger_file = ''
            trigger_line = None
            if candidate_repos:
                trigger_file, trigger_line = candidate_repos[0][1], candidate_repos[0][2]

            question = (
                f"Which AKS cluster do the Kubernetes workloads in repo '{repo_name}' run on (cluster name + owning repo/module)?"
            )
            if candidate_repos:
                cands = ", ".join([cr for cr, _, _ in candidate_repos[:4]])
                question += f" Candidate module repos: {cands}."

            q_obj = {
                "question": question,
                "file": trigger_file or "skaffold.yaml",
                "line": int(trigger_line) if trigger_line else 1,
                "asset": inferred_cluster_name,
            }

            try:
                row = conn.execute(
                    "SELECT value FROM context_metadata WHERE experiment_id=? AND repo_id=? AND namespace='ai_overview' AND key='ai_open_questions' ORDER BY id DESC LIMIT 1",
                    (experiment_id, repo_id),
                ).fetchone()
                existing = []
                if row and row[0]:
                    import json as _json
                    existing = _json.loads(row[0]) if str(row[0]).strip().startswith('[') else []
                if not isinstance(existing, list):
                    existing = []
                if not any(isinstance(x, dict) and str(x.get('question','')).strip().lower() == question.strip().lower() for x in existing):
                    existing.append(q_obj)
                    upsert_context_metadata(
                        experiment_id=experiment_id,
                        repo_name=repo_name,
                        key='ai_open_questions',
                        value=json.dumps(existing[:5]),
                        namespace='ai_overview',
                        source='aks_inference',
                    )
            except Exception:
                pass
    except Exception:
        return


def get_resource_id(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    resource_type: Optional[str] = None
) -> Optional[int]:
    """Get resource ID by name (and optionally type) for parent relationship resolution."""
    with get_db_connection() as conn:
        if resource_type:
            result = conn.execute("""
                SELECT r.id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.experiment_id = ? AND repo.repo_name = ? 
                  AND r.resource_name = ? AND r.resource_type = ?
            """, (experiment_id, repo_name, resource_name, resource_type)).fetchone()
        else:
            result = conn.execute("""
                SELECT r.id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.experiment_id = ? 
                  AND repo.repo_name = ? 
                  AND r.resource_name = ?
            """, (experiment_id, repo_name, resource_name)).fetchone()
        
        return result[0] if result else None


def update_resource_parent(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    parent_resource_id: int
):
    """Update parent_resource_id for a resource (used in second pass after all resources inserted)."""
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE resources 
            SET parent_resource_id = ?
            WHERE id IN (
                SELECT r.id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.experiment_id = ? AND repo.repo_name = ? AND r.resource_name = ?
            )
        """, (parent_resource_id, experiment_id, repo_name, resource_name))


# ============================================================================
# CONNECTION OPERATIONS
# ============================================================================

def insert_connection(
    experiment_id: str,
    source_name: str,
    target_name: str,
    connection_type: str,
    protocol: Optional[str] = None,
    port: Optional[str] = None,
    authentication: Optional[str] = None,
    source_repo: Optional[str] = None,
    target_repo: Optional[str] = None,
    authorization: Optional[str] = None,
    auth_method: Optional[str] = None,
    is_encrypted: Optional[bool] = None,
    via_component: Optional[str] = None,
    notes: Optional[str] = None,
):
    """Insert or update a resource connection with cross-repo detection."""
    with get_db_connection() as conn:
        # Get source resource
        if source_repo:
            source_result = conn.execute("""
                SELECT r.id, r.repo_id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.resource_name = ? AND r.experiment_id = ? AND repo.repo_name = ?
            """, (source_name, experiment_id, source_repo)).fetchone()
        else:
            source_result = conn.execute("""
                SELECT id, repo_id FROM resources
                WHERE resource_name = ? AND experiment_id = ?
            """, (source_name, experiment_id)).fetchone()
        
        # Get target resource
        if target_repo:
            target_result = conn.execute("""
                SELECT r.id, r.repo_id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.resource_name = ? AND r.experiment_id = ? AND repo.repo_name = ?
            """, (target_name, experiment_id, target_repo)).fetchone()
        else:
            target_result = conn.execute("""
                SELECT id, repo_id FROM resources
                WHERE resource_name = ? AND experiment_id = ?
            """, (target_name, experiment_id)).fetchone()
        
        if source_result and target_result:
            is_cross_repo = source_result[1] != target_result[1]

            source_repo_id = source_result[1]
            target_repo_id = target_result[1]
            effective_auth_method = auth_method or authentication
            effective_authentication = authentication or auth_method

            existing = conn.execute(
                """
                SELECT id FROM resource_connections
                WHERE experiment_id = ?
                  AND source_resource_id = ?
                  AND target_resource_id = ?
                  AND COALESCE(connection_type, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (experiment_id, source_result[0], target_result[0], connection_type),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE resource_connections
                    SET source_repo_id = ?,
                        target_repo_id = ?,
                        is_cross_repo = ?,
                        protocol = COALESCE(?, protocol),
                        port = COALESCE(?, port),
                        authentication = COALESCE(?, authentication),
                        authorization = COALESCE(?, authorization),
                        auth_method = COALESCE(?, auth_method),
                        is_encrypted = COALESCE(?, is_encrypted),
                        via_component = COALESCE(?, via_component),
                        notes = COALESCE(?, notes)
                    WHERE id = ?
                    """,
                    (
                        source_repo_id,
                        target_repo_id,
                        is_cross_repo,
                        protocol,
                        port,
                        effective_authentication,
                        authorization,
                        effective_auth_method,
                        is_encrypted,
                        via_component,
                        notes,
                        existing[0],
                    ),
                )
                _audit = ("updated", source_result[0], target_result[0])
                _return_id = existing[0]
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO resource_connections
                    (experiment_id, source_resource_id, target_resource_id, source_repo_id, target_repo_id,
                     is_cross_repo, connection_type, protocol, port, authentication, authorization,
                     auth_method, is_encrypted, via_component, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        experiment_id,
                        source_result[0],
                        target_result[0],
                        source_repo_id,
                        target_repo_id,
                        is_cross_repo,
                        connection_type,
                        protocol,
                        port,
                        effective_authentication,
                        authorization,
                        effective_auth_method,
                        is_encrypted,
                        via_component,
                        notes,
                    ),
                )
                row = cursor.fetchone()
                new_id = row[0] if row else None
                _audit = ("created", source_result[0], target_result[0]) if new_id else None
                _return_id = new_id
        else:
            _audit = None
            _return_id = None

    # Fire provenance audit AFTER the transaction commits to avoid a write-lock
    # deadlock (same issue as insert_resource — cozo_helpers opens its own connection).
    if _audit and _COZO_HELPERS_AVAILABLE:
        _action, _src_id, _tgt_id = _audit
        audit_details = json.dumps({
            "protocol": protocol, "port": port,
            "authentication": authentication, "authorization": authorization,
            "auth_method": auth_method, "is_encrypted": is_encrypted,
            "via_component": via_component, "notes": notes,
        })
        try:
            cozo_helpers._insert_relationship_audit(
                from_node=f"resource:{_src_id}",
                to_node=f"resource:{_tgt_id}",
                rel_type=connection_type or 'connection',
                action=_action,
                actor_type="context_discovery",
                actor_id=experiment_id,
                scan_id=experiment_id,
                evidence_finding_id=None,
                confidence=None,
                details_json=audit_details,
            )
        except Exception:
            pass
    return _return_id


# ============================================================================
# FINDING OPERATIONS
# ============================================================================

def insert_finding(
    experiment_id: str,
    repo_name: str,
    finding_name: str,
    resource_name: Optional[str],
    score: int,
    severity: str,
    category: str,
    evidence_location: str,
    discovered_by: str = "SecurityAgent",
    title: Optional[str] = None,
    description: Optional[str] = None,
    severity_score: Optional[int] = None,
    source_file: Optional[str] = None,
    source_line_start: Optional[int] = None,
    source_line_end: Optional[int] = None,
    code_snippet: Optional[str] = None,
    reason: Optional[str] = None,
    rule_id: Optional[str] = None,
    proposed_fix: Optional[str] = None,
) -> int:
    """Insert finding and return finding_id.

    Backward-compatible: old callers pass finding_name/score; new callers can
    also supply the enriched columns.  title falls back to finding_name;
    severity_score falls back to score.
    """
    effective_title = title if title is not None else finding_name
    effective_severity_score = severity_score if severity_score is not None else score

    with get_db_connection() as conn:
        # Resolve resource — warn but don't raise if not found
        resource_id = None
        repo_id = None
        if resource_name:
            resource_result = conn.execute("""
                SELECT r.id, r.repo_id FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE r.resource_name = ? AND r.experiment_id = ? AND repo.repo_name = ?
            """, (resource_name, experiment_id, repo_name)).fetchone()
            if resource_result:
                resource_id = resource_result[0]
                repo_id = resource_result[1]
            else:
                import warnings
                warnings.warn(
                    f"Resource '{resource_name}' not found in repo '{repo_name}' "
                    f"experiment '{experiment_id}' — inserting finding without resource link."
                )

        # Fall back to repo_id via repo name if still None
        if repo_id is None:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
                (experiment_id, repo_name),
            ).fetchone()
            if repo_row:
                repo_id = repo_row[0]

        # Attempt insert with retries on SQLITE_BUSY/locked errors.
        cursor = None
        for attempt in range(6):
            try:
                cursor = conn.execute("""
                    INSERT INTO findings
                    (experiment_id, repo_id, resource_id, title, description, category,
                     severity_score, base_severity, evidence_location, source_file, source_line_start,
                     source_line_end, rule_id, proposed_fix, code_snippet, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    experiment_id, repo_id, resource_id, effective_title, description, category,
                    effective_severity_score, severity, evidence_location, source_file, source_line_start,
                    source_line_end, rule_id, proposed_fix, code_snippet, reason,
                ))
                break
            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and attempt < 5:
                    # Backoff before retrying
                    time.sleep(1 + attempt)
                    continue
                raise

        if cursor is None:
            raise RuntimeError('Failed to insert finding after retries')

        return cursor.fetchone()[0]


def batch_insert_findings(
    conn,
    findings_data: list,
) -> list:
    """
    Batch insert multiple findings in a single transaction.
    
    Args:
        conn: Database connection (must be managed by caller)
        findings_data: List of dicts with keys:
            - experiment_id, repo_id, resource_id, title, description, category,
              severity_score, base_severity, evidence_location, source_file,
              source_line_start, source_line_end, rule_id, proposed_fix,
              code_snippet, reason
    
    Returns:
        List of inserted finding IDs
    """
    if not findings_data:
        return []
    
    finding_ids = []
    
    for attempt in range(6):
        try:
            for finding in findings_data:
                cursor = conn.execute("""
                    INSERT INTO findings
                    (experiment_id, repo_id, resource_id, title, description, category,
                     severity_score, base_severity, evidence_location, source_file, source_line_start,
                     source_line_end, rule_id, proposed_fix, code_snippet, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    finding['experiment_id'],
                    finding['repo_id'],
                    finding.get('resource_id'),
                    finding['title'],
                    finding.get('description'),
                    finding['category'],
                    finding['severity_score'],
                    finding['base_severity'],
                    finding['evidence_location'],
                    finding.get('source_file'),
                    finding.get('source_line_start'),
                    finding.get('source_line_end'),
                    finding.get('rule_id'),
                    finding.get('proposed_fix'),
                    finding.get('code_snippet'),
                    finding.get('reason'),
                ))
                finding_ids.append(cursor.fetchone()[0])
            break
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 5:
                time.sleep(1 + attempt)
                finding_ids.clear()
                continue
            raise
    
    return finding_ids


def store_skeptic_review(
    finding_id: int,
    reviewer_type: str,
    score_adjustment: float,
    adjusted_score: float,
    confidence: float,
    reasoning: str,
    key_concerns: str = None,
    mitigating_factors: str = None,
    recommendation: str = 'confirm',
) -> int:
    """Insert or update a skeptic review for a finding. Returns review id."""
    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM skeptic_reviews WHERE finding_id = ? AND reviewer_type = ?",
            (finding_id, reviewer_type),
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE skeptic_reviews
                SET score_adjustment = ?, adjusted_score = ?, confidence = ?,
                    reasoning = ?, key_concerns = ?, mitigating_factors = ?,
                    recommendation = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (score_adjustment, adjusted_score, confidence, reasoning,
                  key_concerns, mitigating_factors, recommendation, existing[0]))
            return existing[0]
        else:
            cursor = conn.execute("""
                INSERT INTO skeptic_reviews
                (finding_id, reviewer_type, score_adjustment, adjusted_score,
                 confidence, reasoning, key_concerns, mitigating_factors, recommendation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (finding_id, reviewer_type, score_adjustment, adjusted_score,
                  confidence, reasoning, key_concerns, mitigating_factors, recommendation))
            return cursor.fetchone()[0]


def record_risk_score(
    finding_id: int,
    score: float,
    scored_by: str,
    rationale: str = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Append a risk score snapshot to risk_score_history. Returns history row id.

    If `conn` is provided, use it (this allows callers to include the write in an
    existing transaction to avoid sqlite write-lock contention). Otherwise, open
    a new connection for a standalone insert.
    """
    if conn is None:
        with get_db_connection() as conn_local:
            cursor = conn_local.execute("""
                INSERT INTO risk_score_history (finding_id, score, scored_by, rationale)
                VALUES (?, ?, ?, ?)
                RETURNING id
            """, (finding_id, score, scored_by, rationale))
            return cursor.fetchone()[0]
    else:
        cursor = conn.execute("""
            INSERT INTO risk_score_history (finding_id, score, scored_by, rationale)
            VALUES (?, ?, ?, ?)
            RETURNING id
        """, (finding_id, score, scored_by, rationale))
        return cursor.fetchone()[0]


def store_remediation(
    finding_id: int,
    title: str,
    description: str = None,
    remediation_type: str = 'config',
    effort: str = 'medium',
    priority: int = 2,
    code_fix: str = None,
    reference_url: str = None,
) -> int:
    """Insert or update a remediation for a finding. Returns remediation id."""
    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM remediations WHERE finding_id = ? AND title = ?",
            (finding_id, title),
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE remediations
                SET description = ?, remediation_type = ?, effort = ?, priority = ?,
                    code_fix = ?, reference_url = ?
                WHERE id = ?
            """, (description, remediation_type, effort, priority,
                  code_fix, reference_url, existing[0]))
            return existing[0]
        else:
            cursor = conn.execute("""
                INSERT INTO remediations
                (finding_id, title, description, remediation_type, effort, priority,
                 code_fix, reference_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (finding_id, title, description, remediation_type, effort, priority,
                  code_fix, reference_url))
            return cursor.fetchone()[0]


def insert_trust_boundary(
    experiment_id: str,
    name: str,
    boundary_type: str,
    provider: str = None,
    region: str = None,
    description: str = None,
) -> int:
    """Insert or return existing trust boundary id."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO trust_boundaries
            (experiment_id, name, boundary_type, provider, region, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (experiment_id, name, boundary_type, provider, region, description))
        row = conn.execute(
            "SELECT id FROM trust_boundaries WHERE experiment_id = ? AND name = ?",
            (experiment_id, name),
        ).fetchone()
        return row[0]


def add_resource_to_trust_boundary(trust_boundary_id: int, resource_id: int):
    """Add a resource to a trust boundary (idempotent)."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO trust_boundary_members (trust_boundary_id, resource_id) VALUES (?, ?)",
            (trust_boundary_id, resource_id),
        )


def insert_data_flow(
    experiment_id: str,
    name: str,
    flow_type: str,
    description: str = None,
) -> int:
    """Insert a data flow and return its id."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO data_flows (experiment_id, name, flow_type, description)
            VALUES (?, ?, ?, ?)
            RETURNING id
        """, (experiment_id, name, flow_type, description))
        return cursor.fetchone()[0]


def add_data_flow_step(
    flow_id: int,
    step_order: int,
    component_label: str,
    resource_id: int = None,
    protocol: str = None,
    port: str = None,
    auth_method: str = None,
    is_encrypted: bool = None,
    notes: str = None,
) -> int:
    """Add a step to a data flow. Returns step id."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO data_flow_steps
            (flow_id, step_order, component_label, resource_id, protocol, port,
             auth_method, is_encrypted, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, (flow_id, step_order, component_label, resource_id, protocol, port,
              auth_method, is_encrypted, notes))
        return cursor.fetchone()[0]


# ============================================================================
# CONTEXT OPERATIONS
# ============================================================================

def insert_context_answer(
    experiment_id: str,
    question_key: str,
    answer_value: str,
    evidence_source: str,
    confidence: str = 'confirmed',
    answered_by: str = 'ContextDiscoveryAgent',
    question_text: Optional[str] = None,
    question_category: str = 'General',
    evidence_type: str = 'code',
) -> int:
    """Record context answer and return inserted context_answers.id."""
    with get_db_connection() as conn:
        return _insert_context_answer_with_conn(
            conn,
            experiment_id=experiment_id,
            question_key=question_key,
            answer_value=answer_value,
            evidence_source=evidence_source,
            confidence=confidence,
            answered_by=answered_by,
            question_text=question_text,
            question_category=question_category,
            evidence_type=evidence_type,
        )


def _upsert_context_question(
    conn: sqlite3.Connection,
    *,
    question_key: str,
    question_text: Optional[str] = None,
    question_category: str = 'General',
) -> int:
    """Return context_questions.id, creating the question if it doesn't exist."""
    existing = conn.execute(
        "SELECT id FROM context_questions WHERE question_key = ?",
        (question_key,),
    ).fetchone()
    if existing:
        return int(existing[0])

    resolved_question_text = question_text or question_key.replace('_', ' ').title()
    cursor = conn.execute(
        """
        INSERT INTO context_questions
        (question_key, question_text, question_category)
        VALUES (?, ?, ?)
        """,
        (question_key, resolved_question_text, question_category),
    )
    return int(cursor.lastrowid)


def _insert_context_answer_with_conn(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    question_key: str,
    answer_value: str,
    evidence_source: str,
    confidence: str = 'confirmed',
    answered_by: str = 'ContextDiscoveryAgent',
    question_text: Optional[str] = None,
    question_category: str = 'General',
    evidence_type: str = 'code',
) -> int:
    """Insert a context answer using an existing connection."""
    question_id = _upsert_context_question(
        conn,
        question_key=question_key,
        question_text=question_text,
        question_category=question_category,
    )

    cursor = conn.execute(
        """
        INSERT INTO context_answers
        (experiment_id, question_id, answer_value, answer_confidence,
         evidence_source, evidence_type, answered_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            question_id,
            answer_value,
            confidence,
            evidence_source,
            evidence_type,
            answered_by,
        ),
    )
    return int(cursor.lastrowid)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _infer_property_type(key: str) -> str:
    """Infer property type from key name."""
    security_keywords = ['public', 'firewall', 'encryption', 'tls', 'auth', 'access', 'rbac']
    network_keywords = ['subnet', 'vnet', 'ip', 'port', 'protocol']
    identity_keywords = ['identity', 'principal', 'role', 'permission']
    
    key_lower = key.lower()
    
    if any(k in key_lower for k in security_keywords):
        return 'security'
    elif any(k in key_lower for k in network_keywords):
        return 'network'
    elif any(k in key_lower for k in identity_keywords):
        return 'identity'
    else:
        return 'configuration'


def _is_security_relevant(key: str) -> bool:
    """Determine if property is security-relevant."""
    security_keywords = [
        'public', 'firewall', 'encryption', 'tls', 'ssl', 'auth', 'access',
        'rbac', 'identity', 'role', 'permission', 'security', 'audit',
        'logging', 'monitoring', 'vulnerability', 'exposed', 'open'
    ]
    
    key_lower = key.lower()
    return any(k in key_lower for k in security_keywords)


def format_source_location(source_file: str, start_line: Optional[int], end_line: Optional[int]) -> str:
    """Format source location for display."""
    if start_line:
        if end_line and end_line != start_line:
            return f"{source_file}:{start_line}-{end_line}"
        else:
            return f"{source_file}:{start_line}"
    else:
        return source_file


# ============================================================================
# QUERY HELPERS
# ============================================================================

def get_resources_for_diagram(experiment_id: str) -> List[Dict]:
    """Get all resources with properties merged into a canonical dict for diagram/summaries."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT r.id, r.resource_name, r.resource_type, r.provider, repo.repo_name,
                   COALESCE(MAX(f.severity_score), 0) as max_finding_score
            FROM resources r
            JOIN repositories repo ON r.repo_id = repo.id
            LEFT JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
            GROUP BY r.id
            ORDER BY r.resource_type, r.resource_name
        """, [experiment_id])
        rows = cursor.fetchall()
        resources = []
        for row in rows:
            r = dict(row)
            props = conn.execute("SELECT property_key, property_value FROM resource_properties WHERE resource_id = ?", [r['id']]).fetchall()
            prop_dict = {p['property_key']: _maybe_parse_json(p['property_value']) for p in props}
            # Normalize common fields
            canon = {
                'id': r['id'],
                'resource_name': r['resource_name'],
                'resource_type': r['resource_type'],
                'provider': r['provider'],
                'repo_name': r['repo_name'],
                'max_finding_score': r['max_finding_score'],
                'properties': prop_dict,
                'public': _prop_bool(prop_dict.get('public') or prop_dict.get('public_access') or prop_dict.get('public', False)),
                'public_reason': prop_dict.get('public_reason') or prop_dict.get('notes') or '',
                'network_acls': _maybe_parse_json(prop_dict.get('network_acls')),
                'firewall_rules': _maybe_parse_json(prop_dict.get('firewall_rules')) or [],
            }
            resources.append(canon)
        return resources


def _maybe_parse_json(val: Optional[str]):
    if val is None:
        return None
    try:
        return json.loads(val)
    except Exception:
        return val


def _prop_bool(val):
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).lower()
    return s in ('1','true','yes','y','t')


def get_connections_for_diagram(experiment_id: str, repo_name: Optional[str] = None) -> List[Dict]:
    """Get connections for diagram generation, optionally scoped to a repository."""
    with get_db_connection() as conn:
        query = """
            SELECT 
              r_src.resource_name as source,
              r_src.resource_type as source_type,
              r_tgt.resource_name as target,
              r_tgt.resource_type as target_type,
              rc.connection_type,
              rc.protocol,
              rc.port,
              COALESCE(rc.auth_method, rc.authentication) as auth_method,
              rc.is_encrypted,
              rc.via_component,
              rc.notes,
              rc.is_cross_repo,
              repo_src.repo_name as source_repo,
              repo_tgt.repo_name as target_repo
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
              JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
              JOIN repositories repo_src ON r_src.repo_id = repo_src.id
              JOIN repositories repo_tgt ON r_tgt.repo_id = repo_tgt.id
              WHERE rc.experiment_id = ?
        """
        params: list[Any] = [experiment_id]
        if repo_name:
            query += " AND (repo_src.repo_name = ? OR repo_tgt.repo_name = ?)"
            params.extend([repo_name, repo_name])
        query += " ORDER BY rc.id"

        cursor = conn.execute(query, params)
        
        return [dict(row) for row in cursor.fetchall()]


def get_resources_by_architectural_concern(
    experiment_id: str,
    concern: str,  # 'ingress', 'routing', 'backend', 'network'
    provider: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> List[Dict]:
    """Get resources filtered by architectural concern (ingress, routing, backend, network).
    
    Returns only root resources (parent_resource_id IS NULL) for use as diagram nodes.
    Child resources are shown via hierarchies, not as root nodes.
    """
    with get_db_connection() as conn:
        concern_lower = (concern or "").lower().strip()
        
        if concern_lower == "ingress":
            # Internet-facing gateways, load balancers, WAF, public IPs
            query = """
                SELECT DISTINCT r.id, r.resource_name, r.resource_type, r.provider, repo.repo_name,
                       COALESCE(MAX(f.severity_score), 0) as max_finding_score
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN findings f ON r.id = f.resource_id
                WHERE r.experiment_id = ?
                  AND r.parent_resource_id IS NULL
                  AND (
                    r.resource_type LIKE '%application_gateway%'
                    OR r.resource_type LIKE '%load_balancer%'
                    OR r.resource_type LIKE '%lb%'
                    OR r.resource_type LIKE '%firewall%'
                    OR r.resource_type LIKE '%waf%'
                    OR r.resource_type LIKE '%nat_gateway%'
                    OR r.resource_type LIKE '%public_ip%'
                  )
            """
            params = [experiment_id]
            
        elif concern_lower == "routing":
            # APIM, Service Bus, Event Hub, API operations
            query = """
                SELECT DISTINCT r.id, r.resource_name, r.resource_type, r.provider, repo.repo_name,
                       COALESCE(MAX(f.severity_score), 0) as max_finding_score
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN findings f ON r.id = f.resource_id
                WHERE r.experiment_id = ?
                  AND r.parent_resource_id IS NULL
                  AND (
                    r.resource_type LIKE '%api_management%'
                    OR r.resource_type LIKE '%servicebus_namespace%'
                    OR r.resource_type LIKE '%eventhub_namespace%'
                    OR r.resource_type LIKE '%eventgrid%'
                    OR r.resource_type LIKE '%api_gateway%'
                  )
            """
            params = [experiment_id]
            
        elif concern_lower == "backend":
            # Compute, databases, storage, app services
            query = """
                SELECT DISTINCT r.id, r.resource_name, r.resource_type, r.provider, repo.repo_name,
                       COALESCE(MAX(f.severity_score), 0) as max_finding_score
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN findings f ON r.id = f.resource_id
                WHERE r.experiment_id = ?
                  AND r.parent_resource_id IS NULL
                  AND (
                    r.resource_type LIKE '%virtual_machine%'
                    OR r.resource_type LIKE '%kubernetes%'
                    OR r.resource_type LIKE '%aks%'
                    OR r.resource_type LIKE '%app_service%'
                    OR r.resource_type LIKE '%function_app%'
                    OR r.resource_type LIKE '%sql_server%'
                    OR r.resource_type LIKE '%mysql%'
                    OR r.resource_type LIKE '%postgresql%'
                    OR r.resource_type LIKE '%cosmos%'
                    OR r.resource_type LIKE '%storage_account%'
                    OR r.resource_type LIKE '%container_registry%'
                    OR r.resource_type LIKE '%container_instance%'
                    OR r.resource_type LIKE '%redis%'
                    OR r.resource_type LIKE '%cache%'
                  )
                  AND r.id NOT IN (
                    SELECT r2.id FROM resources r2
                    WHERE r2.experiment_id = ?
                      AND r2.parent_resource_id IS NULL
                      AND (
                        r2.resource_type LIKE '%application_gateway%'
                        OR r2.resource_type LIKE '%load_balancer%'
                        OR r2.resource_type LIKE '%api_management%'
                        OR r2.resource_type LIKE '%servicebus_namespace%'
                      )
                  )
            """
            params = [experiment_id, experiment_id]
            
        elif concern_lower == "network":
            # VNets, subnets, NSGs, private endpoints
            query = """
                SELECT DISTINCT r.id, r.resource_name, r.resource_type, r.provider, repo.repo_name,
                       COALESCE(MAX(f.severity_score), 0) as max_finding_score
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN findings f ON r.id = f.resource_id
                WHERE r.experiment_id = ?
                  AND r.parent_resource_id IS NULL
                  AND (
                    r.resource_type LIKE '%virtual_network%'
                    OR r.resource_type LIKE '%vnet%'
                    OR r.resource_type LIKE '%network_security_group%'
                    OR r.resource_type LIKE '%nsg%'
                    OR r.resource_type LIKE '%vpn_gateway%'
                    OR r.resource_type LIKE '%private_endpoint%'
                    OR r.resource_type LIKE '%expressroute%'
                  )
            """
            params = [experiment_id]
        else:
            return []
        
        # Apply optional filters
        if provider:
            query += " AND LOWER(r.provider) = LOWER(?)"
            params.append(provider)
        
        if repo_name:
            query += " AND repo.repo_name = ?"
            params.append(repo_name)
        
        query += " GROUP BY r.id ORDER BY r.resource_type, r.resource_name"
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        # Enrich with properties like the main get_resources_for_diagram does
        resources = []
        for row in rows:
            r = dict(row)
            props = conn.execute("SELECT property_key, property_value FROM resource_properties WHERE resource_id = ?", [r['id']]).fetchall()
            prop_dict = {p['property_key']: _maybe_parse_json(p['property_value']) for p in props}
            r['properties'] = prop_dict
            r['public'] = _prop_bool(prop_dict.get('public') or prop_dict.get('public_access') or False)
            resources.append(r)
        
        return resources


def get_hierarchy_for_resource(resource_id: int) -> List[Dict]:
    """Get all descendants of a resource (children, grandchildren, etc.) recursively."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            WITH RECURSIVE hierarchy AS (
              SELECT id, resource_name, resource_type, parent_resource_id, 0 as depth
              FROM resources
              WHERE id = ?
              
              UNION ALL
              
              SELECT r.id, r.resource_name, r.resource_type, r.parent_resource_id, h.depth + 1
              FROM resources r
              JOIN hierarchy h ON r.parent_resource_id = h.id
              WHERE h.depth < 5
            )
            SELECT id, resource_name, resource_type, parent_resource_id, depth
            FROM hierarchy
            WHERE depth > 0
            ORDER BY depth, resource_name
        """, [resource_id])
        
        return [dict(row) for row in cursor.fetchall()]


def get_internet_exposed_resources(
    experiment_id: str,
    min_severity: int = 7,
    provider: Optional[str] = None,
) -> List[Dict]:
    """Get resources with internet exposure findings.
    
    Useful for identifying entry points in the ingress diagram.
    """
    with get_db_connection() as conn:
        query = """
            SELECT DISTINCT r.id, r.resource_name, r.resource_type, r.provider,
                   MAX(f.severity_score) as max_severity,
                   GROUP_CONCAT(DISTINCT f.rule_id) as rule_ids
            FROM resources r
            JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
              AND f.severity_score >= ?
              AND (
                f.title LIKE '%internet%'
                OR f.title LIKE '%public%'
                OR f.title LIKE '%external%'
                OR f.description LIKE '%internet%'
                OR f.description LIKE '%public%'
                OR f.description LIKE '%exposed%'
              )
        """
        params = [experiment_id, min_severity]
        
        if provider:
            query += " AND LOWER(r.provider) = LOWER(?)"
            params.append(provider)
        
        query += " GROUP BY r.id ORDER BY max_severity DESC, r.resource_name"
        
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_resource_query_view(
    experiment_id: str,
    resource_name: str,
    repo_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return parent/child/ingress/egress/related view for a resource."""
    with get_db_connection() as conn:
        base_query = """
            SELECT r.id, r.resource_name, r.resource_type, repo.repo_name,
                   p.resource_name AS parent_name, p.resource_type AS parent_type
            FROM resources r
            JOIN repositories repo ON r.repo_id = repo.id
            LEFT JOIN resources p ON r.parent_resource_id = p.id
            WHERE r.experiment_id = ? AND r.resource_name = ?
        """
        params: list[Any] = [experiment_id, resource_name]
        if repo_name:
            base_query += " AND repo.repo_name = ?"
            params.append(repo_name)
        base_query += " ORDER BY r.id LIMIT 1"

        resource = conn.execute(base_query, params).fetchone()
        if not resource:
            return None

        resource_id = resource["id"]
        owning_repo = resource["repo_name"]

        children = conn.execute(
            """
            SELECT c.resource_name, c.resource_type
            FROM resources c
            WHERE c.parent_resource_id = ?
            ORDER BY c.resource_name
            """,
            (resource_id,),
        ).fetchall()

        ingress = conn.execute(
            """
            SELECT src.resource_name AS from_resource,
                   src.resource_type AS from_type,
                   repo_src.repo_name AS from_repo,
                   rc.connection_type,
                   rc.protocol,
                   rc.port,
                   COALESCE(rc.auth_method, rc.authentication) AS auth_method,
                   rc.is_encrypted,
                   rc.via_component,
                   rc.notes
            FROM resource_connections rc
            JOIN resources src ON rc.source_resource_id = src.id
            JOIN repositories repo_src ON src.repo_id = repo_src.id
            WHERE rc.experiment_id = ? AND rc.target_resource_id = ?
            ORDER BY src.resource_name
            """,
            (experiment_id, resource_id),
        ).fetchall()

        egress = conn.execute(
            """
            SELECT dst.resource_name AS to_resource,
                   dst.resource_type AS to_type,
                   repo_dst.repo_name AS to_repo,
                   rc.connection_type,
                   rc.protocol,
                   rc.port,
                   COALESCE(rc.auth_method, rc.authentication) AS auth_method,
                   rc.is_encrypted,
                   rc.via_component,
                   rc.notes
            FROM resource_connections rc
            JOIN resources dst ON rc.target_resource_id = dst.id
            JOIN repositories repo_dst ON dst.repo_id = repo_dst.id
            WHERE rc.experiment_id = ? AND rc.source_resource_id = ?
            ORDER BY dst.resource_name
            """,
            (experiment_id, resource_id),
        ).fetchall()

        assumptions = conn.execute(
            """
            SELECT eq.id, eq.gap_type, eq.context, eq.assumption_text,
                   eq.confidence, eq.suggested_value
            FROM enrichment_queue eq
            JOIN resource_nodes rn ON rn.id = eq.resource_node_id
            WHERE eq.status = 'pending_review'
              AND rn.terraform_name IN (?, ?)
              AND (rn.source_repo = ? OR rn.aliases LIKE ?)
            ORDER BY eq.confidence DESC, eq.created_at ASC
            """,
            (
                resource_name,
                f"__inferred__{resource_name.lower()}",
                owning_repo,
                f'%"{owning_repo}"%',
            ),
        ).fetchall()

        related: list[dict[str, Any]] = []
        for row in ingress:
            related.append(
                {
                    "resource": row["from_resource"],
                    "resource_type": row["from_type"],
                    "repo": row["from_repo"],
                    "direction": "ingress",
                    "connection_type": row["connection_type"],
                }
            )
        for row in egress:
            related.append(
                {
                    "resource": row["to_resource"],
                    "resource_type": row["to_type"],
                    "repo": row["to_repo"],
                    "direction": "egress",
                    "connection_type": row["connection_type"],
                }
            )

        return {
            "resource": {
                "name": resource["resource_name"],
                "type": resource["resource_type"],
                "repo": owning_repo,
            },
            "parent": (
                {
                    "name": resource["parent_name"],
                    "type": resource["parent_type"],
                }
                if resource["parent_name"]
                else None
            ),
            "children": [dict(row) for row in children],
            "ingress": [dict(row) for row in ingress],
            "egress": [dict(row) for row in egress],
            "related": related,
            "pending_assumptions": [dict(row) for row in assumptions],
        }


def _normalize_queue_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "all":
        return normalized
    if normalized not in ENRICHMENT_QUEUE_STATUSES:
        valid = ", ".join(sorted(ENRICHMENT_QUEUE_STATUSES | {"all"}))
        raise ValueError(f"Invalid enrichment_queue status '{status}'. Expected one of: {valid}")
    return normalized


def _normalize_enrichment_decision(decision: str) -> str:
    normalized = (decision or "").strip().lower()
    resolved = ENRICHMENT_DECISION_MAP.get(normalized)
    if not resolved:
        valid = ", ".join(sorted(ENRICHMENT_DECISION_MAP))
        raise ValueError(f"Invalid decision '{decision}'. Expected one of: {valid}")
    return resolved


def _load_repo_aliases(raw_aliases: Optional[str], *, field_name: str) -> list[str]:
    if not raw_aliases:
        return []
    try:
        parsed = json.loads(raw_aliases)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid aliases JSON in {field_name}: {raw_aliases}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"Expected aliases list in {field_name}, got: {type(parsed).__name__}")
    aliases: list[str] = []
    for alias in parsed:
        if alias is None:
            continue
        alias_text = str(alias).strip()
        if alias_text:
            aliases.append(alias_text)
    return aliases


def _list_experiment_repos(conn: sqlite3.Connection, experiment_id: str) -> list[str]:
    repo_rows = conn.execute(
        "SELECT repo_name FROM repositories WHERE experiment_id = ? ORDER BY repo_name",
        (experiment_id,),
    ).fetchall()
    repo_names = [str(row["repo_name"]) for row in repo_rows if row["repo_name"]]
    if not repo_names:
        raise ValueError(f"No repositories found for experiment '{experiment_id}'.")
    return repo_names


def _fetch_enrichment_rows(
    conn: sqlite3.Connection,
    *,
    status: str = "pending_review",
    assumption_id: Optional[int] = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT eq.id,
               eq.resource_node_id,
               eq.relationship_id,
               eq.gap_type,
               eq.context,
               eq.assumption_text,
               eq.assumption_basis,
               eq.confidence,
               eq.suggested_value,
               eq.status,
               eq.resolved_by,
               eq.resolved_at,
               eq.rejection_reason,
               eq.created_at,
               rn.resource_type AS node_resource_type,
               rn.terraform_name AS node_terraform_name,
               rn.source_repo AS node_source_repo,
               rn.aliases AS node_aliases,
               rn.confidence AS node_confidence,
               rr.relationship_type AS relationship_type,
               rr.confidence AS relationship_confidence,
               src.resource_type AS rel_source_resource_type,
               src.terraform_name AS rel_source_terraform_name,
               src.source_repo AS rel_source_repo,
               src.aliases AS rel_source_aliases,
               tgt.resource_type AS rel_target_resource_type,
               tgt.terraform_name AS rel_target_terraform_name,
               tgt.source_repo AS rel_target_repo,
               tgt.aliases AS rel_target_aliases
        FROM enrichment_queue eq
        LEFT JOIN resource_nodes rn ON rn.id = eq.resource_node_id
        LEFT JOIN resource_relationships rr ON rr.id = eq.relationship_id
        LEFT JOIN resource_nodes src ON src.id = rr.source_id
        LEFT JOIN resource_nodes tgt ON tgt.id = rr.target_id
    """
    clauses: list[str] = []
    params: list[Any] = []

    normalized_status = _normalize_queue_status(status)
    if normalized_status != "all":
        clauses.append("eq.status = ?")
        params.append(normalized_status)
    if assumption_id is not None:
        clauses.append("eq.id = ?")
        params.append(assumption_id)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += (
        " ORDER BY CASE eq.confidence "
        "WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, "
        "eq.created_at ASC, eq.id ASC"
    )
    return conn.execute(query, params).fetchall()


def _enrichment_assumption_question_key(assumption_id: int) -> str:
    return f"enrichment_queue_assumption_{assumption_id}_decision"


def _assumption_repo_scope(row: sqlite3.Row) -> set[str]:
    repos: set[str] = set()
    for key in ("node_source_repo", "rel_source_repo", "rel_target_repo"):
        value = row[key]
        if value:
            repos.add(str(value))

    for key in ("node_aliases", "rel_source_aliases", "rel_target_aliases"):
        repos.update(_load_repo_aliases(row[key], field_name=key))
    return repos


def _serialize_assumption_row(row: sqlite3.Row, repo_scope: set[str]) -> Dict[str, Any]:
    relationship_summary: Optional[str] = None
    if row["relationship_type"] and row["rel_source_resource_type"] and row["rel_target_resource_type"]:
        relationship_summary = (
            f"{row['rel_source_resource_type']}.{row['rel_source_terraform_name']} "
            f"--[{row['relationship_type']}]--> "
            f"{row['rel_target_resource_type']}.{row['rel_target_terraform_name']}"
        )

    return {
        "id": row["id"],
        "resource_node_id": row["resource_node_id"],
        "relationship_id": row["relationship_id"],
        "gap_type": row["gap_type"],
        "context": row["context"],
        "assumption_text": row["assumption_text"],
        "assumption_basis": row["assumption_basis"],
        "confidence": row["confidence"],
        "suggested_value": row["suggested_value"],
        "status": row["status"],
        "resolved_by": row["resolved_by"],
        "resolved_at": row["resolved_at"],
        "rejection_reason": row["rejection_reason"],
        "created_at": row["created_at"],
        "node": {
            "resource_type": row["node_resource_type"],
            "terraform_name": row["node_terraform_name"],
            "source_repo": row["node_source_repo"],
            "confidence": row["node_confidence"],
        },
        "relationship": {
            "type": row["relationship_type"],
            "confidence": row["relationship_confidence"],
            "source": {
                "resource_type": row["rel_source_resource_type"],
                "terraform_name": row["rel_source_terraform_name"],
                "source_repo": row["rel_source_repo"],
            },
            "target": {
                "resource_type": row["rel_target_resource_type"],
                "terraform_name": row["rel_target_terraform_name"],
                "source_repo": row["rel_target_repo"],
            },
            "summary": relationship_summary,
        },
        "repo_scope": sorted(repo_scope),
        "question_key": _enrichment_assumption_question_key(int(row["id"])),
    }


def list_enrichment_assumptions(
    experiment_id: str,
    repo_name: Optional[str] = None,
    status: str = "pending_review",
) -> List[Dict[str, Any]]:
    """
    List enrichment queue assumptions scoped to an experiment and optional repo.

    Scope is derived from repositories registered to the experiment and matched
    against node source_repo + aliases.
    """
    provider_norm = (provider or "unknown").strip().lower()

    with get_db_connection() as conn:
        experiment_repos = _list_experiment_repos(conn, experiment_id)
        repo_scope = set(experiment_repos)
        if repo_name and repo_name not in repo_scope:
            raise ValueError(
                f"Repository '{repo_name}' is not registered under experiment '{experiment_id}'."
            )

        rows = _fetch_enrichment_rows(conn, status=status)
        records: list[Dict[str, Any]] = []
        for row in rows:
            assumption_scope = _assumption_repo_scope(row)
            if not assumption_scope.intersection(repo_scope):
                continue
            if repo_name and repo_name not in assumption_scope:
                continue
            records.append(_serialize_assumption_row(row, assumption_scope))
        return records


def _apply_confirmation_confidence_updates(conn: sqlite3.Connection, row: sqlite3.Row) -> list[str]:
    updates: list[str] = []
    relationship_id = row["relationship_id"]
    resource_node_id = row["resource_node_id"]
    gap_type = (row["gap_type"] or "").strip().lower()

    if relationship_id:
        rel_cursor = conn.execute(
            "UPDATE resource_relationships "
            "SET confidence='user_confirmed' "
            "WHERE id=? AND confidence!='user_confirmed'",
            (relationship_id,),
        )
        if rel_cursor.rowcount:
            updates.append(f"resource_relationships[{relationship_id}] confidence=user_confirmed")

    if resource_node_id and gap_type in {"cross_repo_link", "unknown_name"}:
        node_cursor = conn.execute(
            "UPDATE resource_nodes "
            "SET confidence='user_confirmed', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND confidence!='user_confirmed'",
            (resource_node_id,),
        )
        if node_cursor.rowcount:
            updates.append(f"resource_nodes[{resource_node_id}] confidence=user_confirmed")

        equiv_cursor = conn.execute(
            "UPDATE resource_equivalences "
            "SET evidence_level='user_confirmed', updated_at=CURRENT_TIMESTAMP "
            "WHERE resource_node_id=? AND evidence_level!='user_confirmed'",
            (resource_node_id,),
        )
        if equiv_cursor.rowcount:
            updates.append(
                f"resource_equivalences[resource_node_id={resource_node_id}] "
                f"evidence_level=user_confirmed ({equiv_cursor.rowcount} rows)"
            )

    return updates


def resolve_enrichment_assumption(
    experiment_id: str,
    assumption_id: int,
    decision: str,
    resolved_by: str,
    *,
    repo_name: Optional[str] = None,
    resolution_note: Optional[str] = None,
    evidence_source: str = "user_confirmation_cli",
) -> Dict[str, Any]:
    """
    Resolve a pending enrichment assumption and persist an auditable context answer.

    Confirmation upgrades graph confidence where an explicit rule exists.
    Rejections preserve existing graph confidence and record rejection reason.
    """
    normalized_decision = _normalize_enrichment_decision(decision)
    resolver = (resolved_by or "").strip()
    if not resolver:
        raise ValueError("resolved_by must be provided.")
    note = (resolution_note or "").strip()
    if normalized_decision == "rejected" and not note:
        raise ValueError("A rejection requires --note explaining why the assumption was rejected.")
    if not evidence_source.strip():
        raise ValueError("evidence_source must be provided.")

    with get_db_connection() as conn:
        experiment_repos = _list_experiment_repos(conn, experiment_id)
        experiment_scope = set(experiment_repos)
        if repo_name and repo_name not in experiment_scope:
            raise ValueError(
                f"Repository '{repo_name}' is not registered under experiment '{experiment_id}'."
            )

        rows = _fetch_enrichment_rows(conn, status="all", assumption_id=assumption_id)
        if not rows:
            raise ValueError(f"Assumption id {assumption_id} was not found in enrichment_queue.")
        row = rows[0]

        assumption_scope = _assumption_repo_scope(row)
        if not assumption_scope.intersection(experiment_scope):
            raise ValueError(
                f"Assumption id {assumption_id} is not associated with experiment '{experiment_id}'."
            )
        if repo_name and repo_name not in assumption_scope:
            raise ValueError(
                f"Assumption id {assumption_id} is outside repository scope '{repo_name}'."
            )

        if row["status"] != "pending_review":
            raise ValueError(
                f"Assumption id {assumption_id} is already resolved with status '{row['status']}'."
            )

        question_key = _enrichment_assumption_question_key(assumption_id)
        assumption_text = row["assumption_text"] or row["context"] or f"Assumption #{assumption_id}"
        answer_payload = json.dumps(
            {
                "assumption_id": assumption_id,
                "decision": normalized_decision,
                "note": note or None,
                "assumption_text": assumption_text,
                "resolver": resolver,
            },
            sort_keys=True,
        )
        answer_id = _insert_context_answer_with_conn(
            conn,
            experiment_id=experiment_id,
            question_key=question_key,
            question_text=f"Resolve enrichment assumption #{assumption_id}: {assumption_text}",
            question_category="EnrichmentQueue",
            answer_value=answer_payload,
            evidence_source=evidence_source,
            evidence_type="user_confirmation",
            confidence="confirmed",
            answered_by=resolver,
        )

        rejection_reason = note if normalized_decision == "rejected" else None
        queue_cursor = conn.execute(
            """
            UPDATE enrichment_queue
            SET status = ?,
                resolved_by = ?,
                resolved_at = CURRENT_TIMESTAMP,
                rejection_reason = ?
            WHERE id = ? AND status = 'pending_review'
            """,
            (
                normalized_decision,
                resolver,
                rejection_reason,
                assumption_id,
            ),
        )
        if queue_cursor.rowcount != 1:
            raise RuntimeError(
                f"Failed to resolve assumption id {assumption_id}; status changed during update."
            )

        confidence_updates: list[str] = []
        if normalized_decision == "confirmed":
            confidence_updates = _apply_confirmation_confidence_updates(conn, row)

        return {
            "assumption_id": assumption_id,
            "experiment_id": experiment_id,
            "repo_name": repo_name,
            "status": normalized_decision,
            "resolved_by": resolver,
            "resolution_note": note or None,
            "question_key": question_key,
            "context_answer_id": answer_id,
            "confidence_updates": confidence_updates,
        }


if __name__ == "__main__":
    # Test basic operations
    pass


# ── repo_ai_content helpers ───────────────────────────────────────────────────

def upsert_ai_section(
    experiment_id: str,
    repo_name: str,
    section_key: str,
    title: str,
    content_html: str,
    generated_by: str = "system",
) -> None:
    """Insert or update an AI-generated HTML section for a repo/experiment."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO repo_ai_content
                (experiment_id, repo_name, section_key, title, content_html, generated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(experiment_id, repo_name, section_key) DO UPDATE SET
                title        = excluded.title,
                content_html = excluded.content_html,
                generated_by = excluded.generated_by,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (experiment_id, repo_name, section_key, title, content_html, generated_by),
        )
        conn.commit()


def get_ai_sections(experiment_id: str, repo_name: str) -> list[dict]:
    """Return all AI content sections for a repo/experiment, ordered by section_key."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT section_key, title, content_html, generated_by, updated_at
            FROM repo_ai_content
            WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
            ORDER BY section_key
            """,
            (experiment_id, repo_name),
        ).fetchall()
    return [dict(r) for r in rows]


# ── cloud_diagrams helpers ────────────────────────────────────────────────────

def upsert_cloud_diagram(
    experiment_id: str,
    provider: str,
    diagram_title: str,
    mermaid_code: str,
    display_order: int = 0,
) -> None:
    """Insert or update a Mermaid architecture diagram for a provider/experiment.

    Uses the repository name associated with the experiment as the canonical
    owner for the diagram and enforces uniqueness per (repo_name, provider,
    diagram_title) to prevent duplicate provider tabs across multiple experiment
    runs. If a repo_name cannot be determined, falls back to the existing
    experiment-scoped uniqueness.
    """
    # Skip meta-providers that shouldn't have architecture diagrams
    provider_norm = (provider or "unknown").strip().lower()
    if provider_norm in ('terraform', 'kubernetes', 'unknown', ''):
        return  # Don't create architecture diagrams for these
    
    with get_db_connection() as conn:
        # Resolve a primary repo name for this experiment (if any)
        repo_row = conn.execute(
            "SELECT repo_name FROM repositories WHERE experiment_id = ? LIMIT 1",
            (experiment_id,),
        ).fetchone()
        primary_repo = repo_row[0] if repo_row and repo_row[0] else None

        provider_norm = (provider or "unknown").strip().lower()

        # Backfill repo_name on existing rows for this experiment so historical
        # rows become associated with a repo (best-effort); ignore failures.
        if primary_repo:
            try:
                conn.execute(
                    "UPDATE cloud_diagrams SET repo_name = ? WHERE experiment_id = ? AND (repo_name IS NULL OR repo_name = '')",
                    (primary_repo, experiment_id),
                )
            except Exception:
                pass

            # Ensure a unique index exists on (repo_name, provider, diagram_title)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_diagrams_repo_provider_title ON cloud_diagrams(repo_name, provider, diagram_title)"
                )
            except Exception:
                pass

            # Delete stale diagrams in other experiments for the same repo+provider+title
            # Use case-insensitive comparison on provider to catch case-variants.
            try:
                conn.execute(
                    "DELETE FROM cloud_diagrams WHERE repo_name = ? AND lower(provider) = ? AND diagram_title = ? AND experiment_id != ?",
                    (primary_repo, provider_norm, diagram_title, experiment_id),
                )
            except Exception:
                pass

            # Now upsert using repo-scoped uniqueness
            conn.execute(
                """
                INSERT INTO cloud_diagrams
                    (experiment_id, repo_name, provider, diagram_title, mermaid_code, display_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(repo_name, provider, diagram_title) DO UPDATE SET
                    mermaid_code  = excluded.mermaid_code,
                    display_order = excluded.display_order,
                    experiment_id = excluded.experiment_id,
                    updated_at    = CURRENT_TIMESTAMP
                """,
                (experiment_id, primary_repo, provider_norm, diagram_title, mermaid_code, display_order),
            )
        else:
            # Fallback to experiment-scoped uniqueness if no repo_name is available
            conn.execute(
                """
                INSERT INTO cloud_diagrams
                    (experiment_id, provider, diagram_title, mermaid_code, display_order, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(experiment_id, provider, diagram_title) DO UPDATE SET
                    mermaid_code  = excluded.mermaid_code,
                    display_order = excluded.display_order,
                    updated_at    = CURRENT_TIMESTAMP
                """,
                (experiment_id, provider_norm, diagram_title, mermaid_code, display_order),
            )

        conn.commit()


def get_cloud_diagrams(experiment_id: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return cloud diagrams for an experiment (optionally repo-scoped), deduplicated case-insensitively and ordered by display_order then provider.

    Groups diagrams by lowercase(provider)+lower(diagram_title) and keeps the most
    recently updated row for each logical diagram. Provider is returned in Title
    case for display.
    """
    with get_db_connection() as conn:
        if repo_name:
            rows = conn.execute(
                """
                SELECT id, repo_name, provider, diagram_title, mermaid_code, display_order, updated_at
                FROM cloud_diagrams
                WHERE experiment_id = ?
                  AND LOWER(COALESCE(repo_name, '')) = LOWER(?)
                """,
                (experiment_id, repo_name),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, repo_name, provider, diagram_title, mermaid_code, display_order, updated_at
                FROM cloud_diagrams
                WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchall()

    # Deduplicate case-insensitively by (provider, diagram_title) keeping the latest updated_at
    grouped: dict[tuple[str, str], dict] = {}
    for r in rows:
        prov = (r["provider"] or "").strip()
        title = (r["diagram_title"] or "").strip()
        key = (prov.lower(), title.lower())
        existing = grouped.get(key)
        if not existing or (r.get("updated_at") or "") > (existing.get("updated_at") or ""):
            grouped[key] = dict(r)

    # Sort and return
    items = sorted(grouped.values(), key=lambda x: (x.get("display_order") or 0, (x.get("provider") or "").lower()))
    result: list[dict] = []
    for row in items:
        provider_display = (row.get("provider") or "").capitalize()
        result.append({
            "provider": provider_display,
            "diagram_title": row.get("diagram_title"),
            "mermaid_code": row.get("mermaid_code"),
            "display_order": row.get("display_order") or 0,
        })
    return result

