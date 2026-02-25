#!/usr/bin/env python3
"""Initialize the SQLite schema for Triage-Saurus learning database."""

import sqlite3
from pathlib import Path
import sys

# Database location
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/triage.db"


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
          
          is_cross_repo BOOLEAN DEFAULT 0,
          
          connection_type TEXT,
          protocol TEXT,
          port TEXT,
          
          authentication TEXT,
          authorization TEXT,
          is_encrypted BOOLEAN,
          
          via_component TEXT,
          
          FOREIGN KEY(experiment_id) REFERENCES experiments(id),
          FOREIGN KEY(source_resource_id) REFERENCES resources(id) ON DELETE CASCADE,
          FOREIGN KEY(target_resource_id) REFERENCES resources(id) ON DELETE CASCADE
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_connections_cross_repo 
        ON resource_connections(experiment_id, is_cross_repo) 
        WHERE is_cross_repo = 1
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
