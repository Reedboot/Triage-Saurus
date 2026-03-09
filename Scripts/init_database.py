#!/usr/bin/env python3
"""Initialize the SQLite schema for Triage-Saurus learning database."""

from pathlib import Path
import sys
from db_helpers import apply_topology_backfills

# Database location
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage.db"


def init_schema(conn: sqlite3.Connection):
    """Create all tables with proper schema."""
    
    # ============================================================================
    # EXPERIMENTS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
          id TEXT PRIMARY KEY,
          name TEXT,
          parent_experiment_id TEXT,
          
          agent_versions TEXT,
          script_versions TEXT,
          strategy_version TEXT,
          model TEXT,
          
          changes_description TEXT,
          changes_files TEXT,
          hypothesis TEXT,
          
          repos TEXT,
          
          status TEXT DEFAULT 'running',
          started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          completed_at TIMESTAMP,
          duration_sec INTEGER,
          
          tokens_used INTEGER,
          findings_count INTEGER,
          high_value_count INTEGER,
          avg_score REAL,
          
          false_positives INTEGER,
          false_negatives INTEGER,
          accuracy_rate REAL,
          precision REAL,
          recall REAL,
          
          human_reviewed BOOLEAN DEFAULT 0,
          human_quality_rating INTEGER,
          notes TEXT,
          
          created_by TEXT,
          tags TEXT,
          
          FOREIGN KEY(parent_experiment_id) REFERENCES experiments(id)
        )
    """)
    
    # ============================================================================
    # REPOSITORIES
    # ============================================================================
    conn.execute("""
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
          
          scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          UNIQUE(experiment_id, repo_name)
        )
    """)
    
    # ============================================================================
    # RESOURCES (Asset Inventory)
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resources (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          repo_id INTEGER NOT NULL,
          
          resource_name TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          provider TEXT,
          region TEXT,
          parent_resource_id INTEGER,
          
          discovered_by TEXT,
          discovery_method TEXT,
          source_file TEXT,
          source_line_start INTEGER,
          source_line_end INTEGER,
          
          status TEXT DEFAULT 'active',
          first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(repo_id) REFERENCES repositories(id),
          FOREIGN KEY(parent_resource_id) REFERENCES resources(id),
          UNIQUE(experiment_id, repo_id, resource_name)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_resources_experiment 
        ON resources(experiment_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_resources_type 
        ON resources(resource_type)
    """)
    
    # ============================================================================
    # RESOURCE PROPERTIES
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_properties (
          id INTEGER PRIMARY KEY,
          resource_id INTEGER NOT NULL,
          
          property_key TEXT NOT NULL,
          property_value TEXT,
          property_type TEXT,
          is_security_relevant BOOLEAN DEFAULT 0,
          
          FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
          UNIQUE(resource_id, property_key)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_properties_security 
        ON resource_properties(is_security_relevant) 
        WHERE is_security_relevant = 1
    """)
    
    # ============================================================================
    # RESOURCE CONTEXT
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_context (
          resource_id INTEGER PRIMARY KEY,
          
          business_criticality TEXT,
          data_classification TEXT,
          environment TEXT,
          purpose TEXT,
          owner_team TEXT,
          cost_per_month REAL,
          
          usage_pattern TEXT,
          user_count INTEGER,
          uptime_requirement TEXT,
          
          compliance_scope TEXT,
          
          last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
        )
    """)
    
    # ============================================================================
    # RESOURCE CONNECTIONS
    # ============================================================================
    conn.execute("""
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
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(source_resource_id) REFERENCES resources(id) ON DELETE CASCADE,
          FOREIGN KEY(target_resource_id) REFERENCES resources(id) ON DELETE CASCADE
        )
    """)
    
    # ============================================================================
    # FINDINGS (create if not exists, then update if needed)
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS findings (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          repo_id INTEGER,
          resource_id INTEGER,
          
          title TEXT NOT NULL,
          description TEXT,
          category TEXT,
          
          severity_score INTEGER,
          base_severity TEXT,
          overall_score TEXT,
          
          evidence_location TEXT,
          source_file TEXT,
          source_line_start INTEGER,
          source_line_end INTEGER,
          
          finding_path TEXT,
          
          detected_by TEXT,
          detection_method TEXT,
          
          status TEXT DEFAULT 'open',
          
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(repo_id) REFERENCES repositories(id),
          FOREIGN KEY(resource_id) REFERENCES resources(id)
        )
    """)
    
    # Check if findings table needs additional columns (for backward compatibility)
    cursor = conn.execute("PRAGMA table_info(findings)")
    columns = {row[1] for row in cursor.fetchall()}
    
    if 'repo_id' not in columns:
        # Need to add repo_id column
        conn.execute("ALTER TABLE findings ADD COLUMN repo_id INTEGER")
    if 'resource_id' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN resource_id INTEGER")
    if 'category' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN category TEXT")
    if 'evidence_location' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN evidence_location TEXT")
    if 'base_severity' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN base_severity TEXT")
    if 'title' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN title TEXT")
    if 'description' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN description TEXT")
    if 'severity_score' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN severity_score INTEGER")
    if 'source_file' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN source_file TEXT")
    if 'source_line_start' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN source_line_start INTEGER")
    if 'source_line_end' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN source_line_end INTEGER")
    if 'code_snippet' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN code_snippet TEXT")
    if 'reason' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN reason TEXT")
    if 'llm_enriched_at' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN llm_enriched_at TIMESTAMP")
    if 'rule_id' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN rule_id TEXT")
    if 'proposed_fix' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN proposed_fix TEXT")

    # resources table — add display_label if missing
    cursor = conn.execute("PRAGMA table_info(resources)")
    res_cols = {row[1] for row in cursor.fetchall()}
    if 'display_label' not in res_cols:
        conn.execute("ALTER TABLE resources ADD COLUMN display_label TEXT")
    if 'tags' not in res_cols:
        conn.execute("ALTER TABLE resources ADD COLUMN tags TEXT")

    # resource_connections table — keep canonical topology columns additive/idempotent
    cursor = conn.execute("PRAGMA table_info(resource_connections)")
    rc_cols = {row[1] for row in cursor.fetchall()}
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
        if col_name not in rc_cols:
            conn.execute(f"ALTER TABLE resource_connections ADD COLUMN {col_name} {col_type}")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_connections_cross_repo
        ON resource_connections(experiment_id, is_cross_repo)
        WHERE is_cross_repo = 1
    """)

    # ============================================================================
    # TRUST BOUNDARIES
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trust_boundaries (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          name TEXT NOT NULL,
          boundary_type TEXT,        -- 'vnet','subnet','paas','internet','aks_namespace'
          provider TEXT,
          region TEXT,
          description TEXT,
          notes TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(experiment_id) REFERENCES experiments(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trust_boundary_members (
          trust_boundary_id INTEGER NOT NULL,
          resource_id INTEGER NOT NULL,
          PRIMARY KEY (trust_boundary_id, resource_id),
          FOREIGN KEY(trust_boundary_id) REFERENCES trust_boundaries(id),
          FOREIGN KEY(resource_id) REFERENCES resources(id)
        )
    """)

    # ============================================================================
    # DATA FLOWS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_flows (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          name TEXT NOT NULL,
          flow_type TEXT,            -- 'ingress','egress','internal','auth'
          description TEXT,
          notes TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(experiment_id) REFERENCES experiments(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_flow_steps (
          id INTEGER PRIMARY KEY,
          flow_id INTEGER NOT NULL,
          step_order INTEGER NOT NULL,
          resource_id INTEGER,
          component_label TEXT,      -- e.g. 'Internet','WAF','App Gateway'
          protocol TEXT,
          port TEXT,
          auth_method TEXT,
          is_encrypted BOOLEAN,
          notes TEXT,
          FOREIGN KEY(flow_id) REFERENCES data_flows(id),
          FOREIGN KEY(resource_id) REFERENCES resources(id)
        )
    """)
    cursor = conn.execute("PRAGMA table_info(data_flows)")
    df_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in (
        ("flow_type", "TEXT"),
        ("description", "TEXT"),
        ("notes", "TEXT"),
        ("created_at", "TIMESTAMP"),
    ):
        if col_name not in df_cols:
            conn.execute(f"ALTER TABLE data_flows ADD COLUMN {col_name} {col_type}")

    cursor = conn.execute("PRAGMA table_info(data_flow_steps)")
    dfs_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in (
        ("resource_id", "INTEGER"),
        ("component_label", "TEXT"),
        ("protocol", "TEXT"),
        ("port", "TEXT"),
        ("auth_method", "TEXT"),
        ("is_encrypted", "BOOLEAN"),
        ("notes", "TEXT"),
    ):
        if col_name not in dfs_cols:
            conn.execute(f"ALTER TABLE data_flow_steps ADD COLUMN {col_name} {col_type}")

    # ============================================================================
    # RISK SCORE HISTORY
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_score_history (
          id INTEGER PRIMARY KEY,
          finding_id INTEGER NOT NULL,
          score REAL NOT NULL,
          scored_by TEXT,            -- 'script','llm','security_skeptic','human'
          rationale TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(finding_id) REFERENCES findings(id)
        )
    """)

    # ============================================================================
    # REMEDIATIONS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS remediations (
          id INTEGER PRIMARY KEY,
          finding_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          description TEXT,
          remediation_type TEXT,     -- 'config','code','architecture','process'
          effort TEXT,               -- 'low','medium','high'
          priority INTEGER,
          code_fix TEXT,
          reference_url TEXT,
          status TEXT DEFAULT 'proposed',
          verified_by TEXT,
          verified_at TIMESTAMP,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(finding_id) REFERENCES findings(id)
        )
    """)

    # ============================================================================
    # SKEPTIC REVIEWS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skeptic_reviews (
          id INTEGER PRIMARY KEY,
          finding_id INTEGER NOT NULL,
          reviewer_type TEXT NOT NULL,   -- 'security','dev','platform'
          score_adjustment REAL,         -- delta applied to base score
          adjusted_score REAL,
          confidence REAL,               -- 0.0–1.0
          reasoning TEXT,
          key_concerns TEXT,
          mitigating_factors TEXT,
          recommendation TEXT,           -- 'confirm','downgrade','dismiss','escalate'
          reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(finding_id) REFERENCES findings(id)
        )
    """)

    # ============================================================================
    # COUNTERMEASURES
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS countermeasures (
          id INTEGER PRIMARY KEY,
          resource_id INTEGER NOT NULL,
          finding_id INTEGER,
          
          control_type TEXT,
          control_name TEXT,
          control_category TEXT,
          
          effectiveness REAL,
          status TEXT,
          
          evidence_location TEXT,
          configuration_details TEXT,
          
          notes TEXT,
          last_verified TIMESTAMP,
          
          FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
          FOREIGN KEY(finding_id) REFERENCES findings(id) ON DELETE SET NULL
        )
    """)
    
    # ============================================================================
    # COMPOUND RISKS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS compound_risks (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          resource_id INTEGER NOT NULL,
          
          finding_ids TEXT,
          finding_count INTEGER,
          base_compound_score INTEGER,
          
          risk_multiplier REAL,
          context_multiplier REAL,
          exposure_multiplier REAL,
          
          active_countermeasures TEXT,
          countermeasure_discount REAL,
          
          adjusted_score INTEGER,
          risk_category TEXT,
          
          blast_radius_resources TEXT,
          blast_radius_severity TEXT,
          
          attack_chain TEXT,
          remediation_priority INTEGER,
          
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
        )
    """)
    
    # ============================================================================
    # CONTEXT QUESTIONS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_questions (
          id INTEGER PRIMARY KEY,
          question_key TEXT UNIQUE NOT NULL,
          question_text TEXT NOT NULL,
          question_category TEXT,
          
          applies_to_resource_types TEXT,
          applies_to_findings TEXT,
          
          impacts_risk_score BOOLEAN DEFAULT 0,
          score_adjustment_range TEXT,
          
          priority INTEGER,
          is_blocking BOOLEAN DEFAULT 0,
          
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # ============================================================================
    # CONTEXT ANSWERS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_answers (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          question_id INTEGER NOT NULL,
          
          answer_value TEXT,
          answer_confidence TEXT,
          
          evidence_source TEXT,
          evidence_type TEXT,
          evidence_details TEXT,
          
          findings_affected TEXT,
          score_adjustments TEXT,
          
          answered_by TEXT,
          answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          validated BOOLEAN DEFAULT 0,
          validation_notes TEXT,
          validated_at TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(question_id) REFERENCES context_questions(id)
        )
    """)
    
    # ============================================================================
    # KNOWLEDGE FACTS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_facts (
          id INTEGER PRIMARY KEY,
          fact_key TEXT UNIQUE NOT NULL,
          fact_category TEXT,
          
          fact_value TEXT,
          fact_type TEXT,
          
          applies_to_environment TEXT,
          applies_to_provider TEXT,
          applies_to_resources TEXT,
          
          confidence_level TEXT,
          source TEXT,
          
          first_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          last_verified TIMESTAMP,
          expires_at TIMESTAMP,
          
          times_used INTEGER DEFAULT 0,
          experiments_used TEXT
        )
    """)
    
    # ============================================================================
    # GENERATED DIAGRAMS
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_diagrams (
          id INTEGER PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          diagram_type TEXT NOT NULL,
          resource_filter TEXT,
          
          mermaid_code TEXT,
          
          node_count INTEGER,
          edge_count INTEGER,
          generation_time_ms INTEGER,
          
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id)
        )
    """)
    
    # ============================================================================
    # LOOKUP TABLES — providers + resource_types
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS providers (
          id      INTEGER PRIMARY KEY,
          key     TEXT UNIQUE NOT NULL,
          friendly_name TEXT NOT NULL,
          icon    TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_types (
          id                         INTEGER PRIMARY KEY,
          provider_id                INTEGER,
          terraform_type             TEXT UNIQUE NOT NULL,
          friendly_name              TEXT NOT NULL,
          category                   TEXT,
          icon                       TEXT,
          is_data_store              BOOLEAN DEFAULT 0,
          is_internet_facing_capable BOOLEAN DEFAULT 0,
          display_on_architecture_chart BOOLEAN DEFAULT 1,
          parent_type                TEXT,
          FOREIGN KEY(provider_id) REFERENCES providers(id)
        )
    """)
    cursor = conn.execute("PRAGMA table_info(resource_types)")
    rt_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in (
        ("display_on_architecture_chart", "BOOLEAN DEFAULT 1"),
        ("parent_type", "TEXT"),
    ):
        if col_name not in rt_cols:
            conn.execute(f"ALTER TABLE resource_types ADD COLUMN {col_name} {col_type}")
    conn.execute(
        "UPDATE resource_types SET display_on_architecture_chart = 1 "
        "WHERE display_on_architecture_chart IS NULL"
    )

    # ============================================================================
    # KNOWLEDGE GRAPH — cross-repo resource nodes + typed relationships
    # ============================================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_nodes (
          id               INTEGER PRIMARY KEY,
          resource_type    TEXT NOT NULL,
          terraform_name   TEXT NOT NULL,
          canonical_name   TEXT,
          friendly_name    TEXT,
          display_label    TEXT,
          provider         TEXT,
          source_repo      TEXT,
          aliases          TEXT DEFAULT '[]',
          confidence       TEXT DEFAULT 'extracted'
                             CHECK(confidence IN ('extracted','inferred','user_confirmed')),
          properties       TEXT DEFAULT '{}',
          created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(resource_type, terraform_name, source_repo)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_relationships (
          id                INTEGER PRIMARY KEY,
          source_id         INTEGER NOT NULL,
          target_id         INTEGER NOT NULL,
          relationship_type TEXT NOT NULL
                              CHECK(relationship_type IN (
                                'contains',
                                'grants_access_to',
                                'routes_ingress_to',
                                'depends_on',
                                'encrypts',
                                'restricts_access',
                                'monitors',
                                'authenticates_via'
                              )),
          source_repo       TEXT,
          confidence        TEXT DEFAULT 'extracted'
                              CHECK(confidence IN ('extracted','inferred','user_confirmed')),
          notes             TEXT,
          created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(source_id, target_id, relationship_type),
          FOREIGN KEY(source_id) REFERENCES resource_nodes(id) ON DELETE CASCADE,
          FOREIGN KEY(target_id) REFERENCES resource_nodes(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_equivalences (
          id                       INTEGER PRIMARY KEY,
          resource_node_id         INTEGER NOT NULL,
          candidate_resource_type  TEXT NOT NULL,
          candidate_terraform_name TEXT NOT NULL,
          candidate_source_repo    TEXT NOT NULL,
          equivalence_kind         TEXT NOT NULL DEFAULT 'cross_repo_alias'
                                     CHECK(equivalence_kind IN ('cross_repo_alias','placeholder_promotion')),
          confidence               TEXT DEFAULT 'medium'
                                     CHECK(confidence IN ('high','medium','low')),
          evidence_level           TEXT DEFAULT 'inferred'
                                     CHECK(evidence_level IN ('extracted','inferred','user_confirmed')),
          provenance               TEXT NOT NULL,
          context                  TEXT,
          created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(
            resource_node_id,
            candidate_resource_type,
            candidate_terraform_name,
            candidate_source_repo,
            equivalence_kind
          ),
          FOREIGN KEY(resource_node_id) REFERENCES resource_nodes(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_node "
        "ON resource_equivalences(resource_node_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_candidate "
        "ON resource_equivalences(candidate_source_repo, candidate_resource_type, candidate_terraform_name)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS enrichment_queue (
          id                  INTEGER PRIMARY KEY,
          resource_node_id    INTEGER,
          relationship_id     INTEGER,
          gap_type            TEXT NOT NULL
                                CHECK(gap_type IN (
                                  'unknown_name',
                                  'ambiguous_ref',
                                  'cross_repo_link',
                                  'missing_target',
                                  'assumption'
                                )),
          context             TEXT,
          assumption_text     TEXT,
          assumption_basis    TEXT,
          confidence          TEXT DEFAULT 'medium'
                                CHECK(confidence IN ('high','medium','low')),
          suggested_value     TEXT,
          status              TEXT DEFAULT 'pending_review'
                                CHECK(status IN ('pending_review','confirmed','rejected')),
          resolved_by         TEXT,
          resolved_at         TIMESTAMP,
          rejection_reason    TEXT,
          created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(resource_node_id) REFERENCES resource_nodes(id) ON DELETE CASCADE,
          FOREIGN KEY(relationship_id)  REFERENCES resource_relationships(id) ON DELETE CASCADE
        )
    """)

    cursor = conn.execute("PRAGMA table_info(resource_nodes)")
    node_cols = {row[1] for row in cursor.fetchall()}
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
        if col_name not in node_cols:
            conn.execute(f"ALTER TABLE resource_nodes ADD COLUMN {col_name} {col_type}")

    cursor = conn.execute("PRAGMA table_info(resource_relationships)")
    rel_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in (
        ("source_repo", "TEXT"),
        ("confidence", "TEXT DEFAULT 'extracted'"),
        ("notes", "TEXT"),
        ("created_at", "TIMESTAMP"),
    ):
        if col_name not in rel_cols:
            conn.execute(f"ALTER TABLE resource_relationships ADD COLUMN {col_name} {col_type}")

    cursor = conn.execute("PRAGMA table_info(resource_equivalences)")
    equiv_cols = {row[1] for row in cursor.fetchall()}
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
        if col_name not in equiv_cols:
            conn.execute(f"ALTER TABLE resource_equivalences ADD COLUMN {col_name} {col_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_node "
        "ON resource_equivalences(resource_node_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_equivalences_candidate "
        "ON resource_equivalences(candidate_source_repo, candidate_resource_type, candidate_terraform_name)"
    )

    cursor = conn.execute("PRAGMA table_info(enrichment_queue)")
    eq_cols = {row[1] for row in cursor.fetchall()}
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
        if col_name not in eq_cols:
            conn.execute(f"ALTER TABLE enrichment_queue ADD COLUMN {col_name} {col_type}")

    backfill_stats = apply_topology_backfills(conn)
    if any(backfill_stats.values()):
        summary = ", ".join(
            f"{name}={count}" for name, count in sorted(backfill_stats.items()) if count
        )
        print(f"ℹ️ Applied legacy topology backfills: {summary}")

    # Seed providers
    conn.executemany(
        "INSERT OR IGNORE INTO providers (key, friendly_name, icon) VALUES (?, ?, ?)",
        [
            ("azure",    "Microsoft Azure",         "☁️"),
            ("aws",      "Amazon Web Services",     "🟠"),
            ("gcp",      "Google Cloud Platform",   "🔵"),
            ("alicloud", "Alibaba Cloud",            "🟡"),
            ("oracle",   "Oracle Cloud",             "🔴"),
        ],
    )

    # Helper to get provider_id by key
    def _pid(key: str) -> int | None:
        row = conn.execute("SELECT id FROM providers WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # Seed resource_types  (terraform_type, friendly_name, category, icon, provider_key,
    #                        is_data_store, is_internet_facing_capable)
    _SEED: list[tuple] = [
        # Azure — Identity
        ("azurerm_key_vault",                          "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_key_vault_key",                      "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_key_vault_secret",                   "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_user_assigned_identity",             "Managed Identity",           "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_role_definition",                    "Role Definition",            "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_role_assignment",                    "Role Assignment",            "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_policy_definition",                  "Policy Definition",          "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_policy_assignment",                  "Policy Assignment",          "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_policy_set_definition",              "Policy Set",                 "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_client_config",                      "Client Config",              "Identity",   "🧭",  "azure", 0, 0),
        # Azure — Identity (Azure AD)
        ("azuread_application",                        "Azure AD Application",                 "Identity",   "👤",  "azure", 0, 0),
        ("azuread_application_password",               "Azure AD Application Password",        "Identity",   "🔐",  "azure", 0, 0),
        ("azuread_directory_role",                     "Azure AD Directory Role",              "Identity",   "👤",  "azure", 0, 0),
        ("azuread_directory_role_assignment",          "Azure AD Directory Role Assignment",   "Identity",   "👤",  "azure", 0, 0),
        ("azuread_domains",                            "Azure AD Domain",                      "Identity",   "👤",  "azure", 0, 0),
        ("azuread_group",                              "Azure AD Group",                       "Identity",   "👥",  "azure", 0, 0),
        ("azuread_group_member",                       "Azure AD Group Member",                "Identity",   "👥",  "azure", 0, 0),
        ("azuread_service_principal",                  "Azure AD Service Principal",           "Identity",   "👤",  "azure", 0, 0),
        ("azuread_service_principal_password",         "Azure AD Service Principal Password",  "Identity",   "🔐",  "azure", 0, 0),
        ("azuread_user",                               "Azure AD User",                        "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_ssh_public_key",                     "SSH Public Key",                       "Identity",   "🔑",  "azure", 0, 0),
        # Azure — Database
        ("azurerm_mssql_server",                       "SQL Server",                 "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_sql_server",                         "SQL Server",                 "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_mssql_database",                     "SQL Database",               "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_mssql_server_security_alert_policy", "SQL Alert Policy",           "Security",   "🚨", "azure", 0, 0),
        ("azurerm_mysql_server",                       "MySQL Server",               "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_postgresql_server",                  "PostgreSQL Server",          "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_postgresql_configuration",           "PostgreSQL Server",          "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_cosmosdb_account",                   "Cosmos DB",                  "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_cosmosdb_sql_database",              "Cosmos DB SQL Database",     "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_cosmosdb_sql_container",             "Cosmos DB SQL Container",    "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_mssql_firewall_rule",                "SQL Firewall Rule",          "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_sql_firewall_rule",                  "SQL Firewall Rule",          "Security",   "🛡️", "azure", 0, 0),
        # Azure — Storage
        ("azurerm_storage_account",                    "Storage Account",            "Storage",    "🗄️", "azure", 1, 1),
        ("azurerm_storage_account_network_rules",      "Storage Account",            "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_storage_container",                  "Storage Container",          "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_storage_blob",                       "Storage Blob",               "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_managed_disk",                       "Managed Disk",               "Storage",    "💾", "azure", 1, 0),
        # Auth/Credentials — Identity layer, excluded from diagram nodes
        ("azurerm_storage_account_sas",                "Storage Account SAS",        "Identity",   "🔑", "azure", 0, 0),
        # Database governance config — excluded from diagram via routing filter
        ("azurerm_mssql_database_extended_auditing_policy",        "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_extended_auditing_policy",          "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_microsoft_support_auditing_policy", "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_transparent_data_encryption",       "SQL Transparent Encryption", "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_virtual_network_rule",                     "SQL VNet Rule",              "Security", "🛡️", "azure", 0, 0),
        # VM extensions — agents installed on VMs, excluded from diagram
        ("azurerm_virtual_machine_extension",         "VM Extension", "", "🔧", "azure", 0, 0),
        ("azurerm_linux_virtual_machine_extension",   "VM Extension", "", "🔧", "azure", 0, 0),
        ("azurerm_windows_virtual_machine_extension", "VM Extension", "", "🔧", "azure", 0, 0),
        # Azure — Compute
        ("azurerm_linux_virtual_machine",              "Linux VM",                   "Compute",    "🖥️", "azure", 0, 1),
        ("azurerm_windows_virtual_machine",            "Windows VM",                 "Compute",    "🖥️", "azure", 0, 1),
        ("azurerm_app_service",                        "App Service",                "Compute",    "🌐", "azure", 0, 1),
        ("azurerm_linux_function_app",                 "Function App",               "Compute",    "⚡", "azure", 0, 1),
        ("azurerm_windows_function_app",               "Function App",               "Compute",    "⚡", "azure", 0, 1),
        ("azurerm_linux_web_app",                      "Linux Web App",               "Compute",    "🌐", "azure", 0, 1),
        ("azurerm_service_plan",                       "Service Plan",                "Compute",    "⚙️", "azure", 0, 1),
        # Azure — Container
        ("azurerm_kubernetes_cluster",                 "AKS Cluster",                "Container",  "☸️", "azure", 0, 1),
        ("azurerm_container_registry",                 "Container Registry",         "Container",  "📦", "azure", 0, 0),
        ("azurerm_container_group",                    "Container Instance",         "Container",  "📦", "azure", 0, 1),
        # Azure — Network
        ("azurerm_application_gateway",                "Application Gateway",        "Network",    "🌐", "azure", 0, 1),
        ("azurerm_lb",                                 "Load Balancer",              "Network",    "🌐", "azure", 0, 1),
        ("azurerm_virtual_network",                    "Virtual Network",            "Network",    "🔷", "azure", 0, 0),
        ("azurerm_subnet",                             "Subnet",                     "Network",    "🔷", "azure", 0, 0),
        ("azurerm_network_interface",                  "Network Interface",          "Network",    "🔷", "azure", 0, 0),
        ("azurerm_public_ip",                          "Public IP",                  "Network",    "🌍", "azure", 0, 1),
        ("azurerm_private_endpoint",                   "Private Endpoint",           "Network",    "🔒", "azure", 0, 0),
        ("azurerm_network_interface_security_group_association", "NIC Security Group Association", "Security", "🔗", "azure", 0, 0),
        ("azurerm_network_security_rule",              "Network Security Rule",      "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_network_watcher",                    "Network Watcher",            "Monitoring", "📡", "azure", 0, 0),
        ("azurerm_network_watcher_flow_log",           "Network Watcher Flow Log",   "Monitoring", "📡", "azure", 0, 0),
        ("azurerm_resource_group",                     "Resource Group",             "Network",    "📦", "azure", 0, 0),
        ("azurerm_resources",                          "Resources",                  "Other",      "📦", "azure", 0, 0),
        # Azure — Security
        ("azurerm_network_security_group",             "Network Security Group",     "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_firewall",                           "Azure Firewall",             "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_web_application_firewall_policy",    "WAF Policy",                 "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_security_center_contact",             "Security Center Contact",    "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_security_center_subscription_pricing","Security Center Pricing",    "Security",   "🛡️", "azure", 0, 0),
        # Azure — Monitoring
        ("azurerm_monitor_diagnostic_setting",         "Diagnostic Settings",        "Monitoring", "📊", "azure", 0, 0),
        ("azurerm_monitor_log_profile",                "Log Profile",                "Monitoring", "📊", "azure", 0, 0),
        ("azurerm_log_analytics_workspace",            "Log Analytics Workspace",    "Monitoring", "📊", "azure", 0, 0),
        # AWS — Storage
        ("aws_s3_bucket",                              "S3 Bucket",                  "Storage",    "🗄️", "aws",   1, 1),
        ("aws_s3_bucket_object",                       "S3 Bucket",                  "Storage",    "🗄️", "aws",   1, 0),
        ("aws_s3_bucket_policy",                       "S3 Bucket Policy",           "Storage",    "📜", "aws",   0, 0),
        ("aws_s3_bucket_public_access_block",          "Public Access Block",        "Storage",    "🔒", "aws",   0, 0),
        ("aws_ebs_volume",                             "EBS Volume",                 "Storage",    "💾", "aws",   1, 0),
        ("aws_ecr_repository",                         "ECR Repository",             "Storage",    "🗄️", "aws",   0, 0),
        ("aws_volume_attachment",                      "Volume Attachment",          "Storage",    "🔗", "aws",   0, 0),
        # AWS — Database
        ("aws_rds_cluster",                            "RDS Cluster",                "Database",   "🗃️", "aws",   1, 0),
        ("aws_db_instance",                            "RDS Instance",               "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster",                        "Neptune Cluster",            "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster_instance",               "Neptune Instance",           "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster_snapshot",               "Neptune Snapshot",           "Database",   "🗃️", "aws",   1, 0),
        ("aws_elasticsearch_domain",                   "OpenSearch Domain",          "Database",   "🔍", "aws",   1, 1),
        ("aws_elasticsearch_domain_policy",            "OpenSearch Domain",          "Database",   "🔍", "aws",   1, 0),
        ("aws_dynamodb_table",                         "DynamoDB Table",             "Database",   "🗃️", "aws",   1, 0),
        # AWS — Compute
        ("aws_ami",                                    "AMI",                        "Compute",    "🖥️", "aws",   0, 0),
        ("aws_instance",                               "EC2 Instance",               "Compute",    "🖥️", "aws",   0, 1),
        ("aws_lambda_function",                        "Lambda Function",            "Compute",    "⚡", "aws",   0, 0),
        # AWS — Container
        ("aws_ecs_cluster",                            "ECS Cluster",                "Container",  "☸️", "aws",   0, 0),
        ("aws_ecs_service",                            "ECS Service",                "Container",  "☸️", "aws",   0, 0),
        # AWS — Network
        ("aws_elb",                                    "Load Balancer",              "Network",    "🌐", "aws",   0, 1),
        ("aws_alb",                                    "App Load Balancer",          "Network",    "🌐", "aws",   0, 1),
        ("aws_lb",                                     "Network Load Balancer",      "Network",    "🌐", "aws",   0, 1),
        ("aws_lb_listener",                            "Load Balancer Listener",     "Network",    "🎧", "aws",   0, 0),
        ("aws_alb_listener",                           "Load Balancer Listener",     "Network",    "🎧", "aws",   0, 0),
        ("aws_lb_target_group",                        "Target Group",               "Network",    "🎯", "aws",   0, 0),
        ("aws_alb_target_group",                       "Target Group",               "Network",    "🎯", "aws",   0, 0),
        ("aws_lb_target_group_attachment",             "Target Attachment",          "Network",    "🔗", "aws",   0, 0),
        ("aws_eip",                                    "Elastic IP",                 "Network",    "🌍", "aws",   0, 1),
        ("aws_route",                                  "Route",                      "Network",    "🛣️", "aws",   0, 0),
        ("aws_route_table",                            "Route Table",                "Network",    "🛣️", "aws",   0, 0),
        ("aws_route_table_association",                "Route Table Association",    "Network",    "🔗", "aws",   0, 0),
        ("aws_vpc",                                    "VPC",                        "Network",    "🔷", "aws",   0, 0),
        ("aws_subnet",                                 "Subnet",                     "Network",    "🔷", "aws",   0, 0),
        ("aws_internet_gateway",                       "Internet Gateway",           "Network",    "🌍", "aws",   0, 0),
        # AWS — Security
        ("aws_security_group",                         "Security Group",             "Security",   "🛡️", "aws",   0, 0),
        ("aws_security_group_rule",                    "Security Group Rule",        "Security",   "🛡️", "aws",   0, 0),
        # AWS — Identity
        ("aws_iam_role",                               "IAM Role",                   "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_policy",                             "IAM Policy",                 "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_policy_document",                    "IAM Policy Document",        "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_role_policy",                        "IAM Role Policy",            "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_role_policy_attachment",             "Iam Role Policy Attachment", "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_user",                               "IAM User",                   "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_user_policy",                        "Iam User Policy",            "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_access_key",                         "Iam Access Key",             "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_instance_profile",                   "IAM Instance Profile",       "Identity",   "👤", "aws",   0, 0),
        ("aws_kms_key",                                "KMS Key",                    "Identity",   "🔑", "aws",   0, 0),
        ("aws_kms_alias",                              "KMS Key Alias",              "Identity",   "🔑", "aws",   0, 0),
        ("aws_key_pair",                               "Key Pair",                   "Identity",   "🔑", "aws",   0, 0),
        ("aws_ssm_parameter",                          "SSM Parameter",              "Identity",   "🔐", "aws",   0, 0),
        # GCP — Storage
        ("google_storage_bucket",                      "GCS Bucket",                 "Storage",    "🗄️", "gcp",   1, 1),
        ("google_storage_bucket_iam_binding",          "GCS Bucket",                 "Storage",    "🗄️", "gcp",   1, 0),
        # GCP — Database
        ("google_sql_database_instance",               "Cloud SQL Instance",         "Database",   "🗃️", "gcp",   1, 1),
        ("google_bigquery_dataset",                    "BigQuery Dataset",           "Database",   "🗃️", "gcp",   1, 1),
        ("google_bigtable_instance",                   "Bigtable Instance",          "Database",   "🗃️", "gcp",   1, 0),
        # GCP — Compute
        ("google_compute_instance",                    "Compute Instance",           "Compute",    "🖥️", "gcp",   0, 1),
        ("google_cloudfunctions_function",             "Cloud Function",             "Compute",    "⚡", "gcp",   0, 0),
        # GCP — Container
        ("google_container_cluster",                   "GKE Cluster",                "Container",  "☸️", "gcp",   0, 0),
        ("google_container_node_pool",                 "GKE Node Pool",              "Container",  "☸️", "gcp",   0, 0),
        # GCP — Network
        ("google_compute_network",                     "VPC Network",                "Network",    "🔷", "gcp",   0, 0),
        ("google_compute_subnetwork",                  "Subnetwork",                 "Network",    "🔷", "gcp",   0, 0),
        # GCP — Security
        ("google_compute_firewall",                    "Firewall Rule",              "Security",   "🛡️", "gcp",   0, 0),
        # GCP — Identity
        ("google_project_iam_binding",                 "IAM Binding",                "Identity",   "👤", "gcp",   0, 0),
        ("google_kms_crypto_key",                      "KMS Crypto Key",             "Identity",   "🔑", "gcp",   0, 0),
        ("google_service_account",                     "Service Account",            "Identity",   "👤", "gcp",   0, 0),
        # Alibaba Cloud
        ("alicloud_actiontrail_trail",                 "Actiontrail Trail",          "Monitoring", "📜", "alicloud", 0, 0),
        ("alicloud_ram_role",                          "RAM Role",                   "Identity",   "👤",  "alicloud", 0, 0),
    ]

    display_overrides = {
        # IAM / RBAC / policy controls are context-only (not architecture nodes)
        "azurerm_role_definition": 0,
        "azurerm_role_assignment": 0,
        "azurerm_policy_definition": 0,
        "azurerm_policy_assignment": 0,
        "azurerm_policy_set_definition": 0,
        "azuread_application": 0,
        "azuread_application_password": 0,
        "azuread_directory_role": 0,
        "azuread_directory_role_assignment": 0,
        "azuread_domains": 0,
        "azuread_group": 0,
        "azuread_group_member": 0,
        "azuread_service_principal": 0,
        "azuread_service_principal_password": 0,
        "azuread_user": 0,
        "azurerm_ssh_public_key": 0,
        "azurerm_security_center_contact": 0,
        "azurerm_security_center_subscription_pricing": 0,
        "aws_iam_role": 0,
        "aws_iam_policy": 0,
        "aws_iam_policy_document": 0,
        "aws_iam_user": 0,
        "aws_iam_instance_profile": 0,
        "aws_iam_role_policy": 0,
        "aws_iam_role_policy_attachment": 0,
        "aws_iam_user_policy": 0,
        "aws_iam_access_key": 0,
        "aws_kms_key": 0,
        "aws_kms_alias": 0,
        "aws_key_pair": 0,
        "aws_ssm_parameter": 0,
        "aws_elasticsearch_domain_policy": 0,
        "aws_lb_listener": 0,
        "aws_alb_listener": 0,
        "aws_lb_target_group": 0,
        "aws_alb_target_group": 0,
        "google_project_iam_binding": 0,
        "google_storage_bucket_iam_binding": 0,
        # Child components only render when vulnerable (nested under parent)
        "aws_s3_bucket_policy": 0,
        "aws_s3_bucket_public_access_block": 0,
    }
    parent_type_overrides = {
        "aws_lb_listener": "aws_lb",
        "aws_alb_listener": "aws_alb",
        "aws_lb_target_group": "aws_lb",
        "aws_alb_target_group": "aws_alb",
        "aws_lb_target_group_attachment": "aws_lb_target_group",
        "aws_s3_bucket_policy": "aws_s3_bucket",
        "aws_s3_bucket_public_access_block": "aws_s3_bucket",
        "google_storage_bucket_iam_binding": "google_storage_bucket",
        "azurerm_lb_backend_address_pool": "azurerm_lb",
        "azurerm_lb_rule": "azurerm_lb",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
    }

    for (tf_type, fname, cat, icon, pkey, is_ds, is_if) in _SEED:
        display_on_architecture_chart = display_overrides.get(tf_type, 1)
        parent_type = parent_type_overrides.get(tf_type)
        conn.execute(
            """
            INSERT INTO resource_types
            (
              provider_id,
              terraform_type,
              friendly_name,
              category,
              icon,
              is_data_store,
              is_internet_facing_capable,
              display_on_architecture_chart,
              parent_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(terraform_type) DO UPDATE SET
              provider_id=excluded.provider_id,
              friendly_name=excluded.friendly_name,
              category=excluded.category,
              icon=excluded.icon,
              is_data_store=excluded.is_data_store,
              is_internet_facing_capable=excluded.is_internet_facing_capable,
              display_on_architecture_chart=excluded.display_on_architecture_chart,
              parent_type=COALESCE(excluded.parent_type, resource_types.parent_type)
            """,
            (
                _pid(pkey),
                tf_type,
                fname,
                cat,
                icon,
                is_ds,
                is_if,
                display_on_architecture_chart,
                parent_type,
            ),
        )

    print("✅ Schema initialized successfully")


def main():
    """Initialize or upgrade the database schema."""
    
    # Create Output/Learning directory if needed
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect and initialize
    conn = sqlite3.connect(DB_PATH)
    
    try:
        init_schema(conn)
        conn.commit()
        print(f"✅ Database ready: {DB_PATH}")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error initializing database: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
