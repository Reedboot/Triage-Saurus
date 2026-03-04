#!/usr/bin/env python3
"""Database helper functions for Triage-Saurus."""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

# Database location
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage.db"


def _ensure_schema(conn: sqlite3.Connection):
    """Ensure tables used by db_helpers exist on the active database."""
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
      scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
      authentication TEXT
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
    """)

    # Ensure optional columns exist for backward compatibility.
    resource_columns = {row[1] for row in conn.execute("PRAGMA table_info(resources)").fetchall()}
    if "parent_resource_id" not in resource_columns:
        conn.execute("ALTER TABLE resources ADD COLUMN parent_resource_id INTEGER")

    connection_columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_connections)").fetchall()}
    if "source_repo_id" not in connection_columns:
        conn.execute("ALTER TABLE resource_connections ADD COLUMN source_repo_id INTEGER")
    if "target_repo_id" not in connection_columns:
        conn.execute("ALTER TABLE resource_connections ADD COLUMN target_repo_id INTEGER")

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


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    """Context manager for database connections."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # Access columns by name
    _ensure_schema(conn)
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
            (experiment_id, repo_name, repo_url, repo_type)
            VALUES (?, ?, ?, ?)
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
        
        return resource_id


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
    target_repo: Optional[str] = None
):
    """Insert resource connection with cross-repo detection."""
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
            
            conn.execute("""
                INSERT INTO resource_connections
                (experiment_id, source_resource_id, target_resource_id, 
                 is_cross_repo, connection_type, protocol, port, authentication)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (experiment_id, source_result[0], target_result[0], 
                  is_cross_repo, connection_type, protocol, port, authentication))


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

        cursor = conn.execute("""
            INSERT INTO findings
            (experiment_id, repo_id, finding_name, resource_id, score, base_severity,
             category, discovered_by, evidence_location, validation_status,
             title, description, severity_score, source_file, source_line_start,
             source_line_end, code_snippet, reason, rule_id, proposed_fix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Draft',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, (
            experiment_id, repo_id, finding_name, resource_id, score, severity,
            category, discovered_by, evidence_location,
            effective_title, description, effective_severity_score,
            source_file, source_line_start, source_line_end,
            code_snippet, reason, rule_id, proposed_fix,
        ))

        return cursor.fetchone()[0]


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
) -> int:
    """Append a risk score snapshot to risk_score_history. Returns history row id."""
    with get_db_connection() as conn:
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
    answered_by: str = 'ContextDiscoveryAgent'
):
    """Record context answer."""
    with get_db_connection() as conn:
        # Get or create question
        question_id = conn.execute("""
            SELECT id FROM context_questions WHERE question_key = ?
        """, (question_key,)).fetchone()
        
        if not question_id:
            # Question doesn't exist, create it
            cursor = conn.execute("""
                INSERT INTO context_questions 
                (question_key, question_text, question_category)
                VALUES (?, ?, 'General')
                RETURNING id
            """, (question_key, question_key.replace('_', ' ').title()))
            question_id = cursor.fetchone()[0]
        else:
            question_id = question_id[0]
        
        conn.execute("""
            INSERT INTO context_answers
            (experiment_id, question_id, answer_value, answer_confidence,
             evidence_source, evidence_type, answered_by)
            VALUES (?, ?, ?, ?, ?, 'code', ?)
        """, (experiment_id, question_id, answer_value, confidence, 
              evidence_source, answered_by))


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
                   COALESCE(MAX(f.severity_score), MAX(f.score), 0) as max_finding_score
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


def get_connections_for_diagram(experiment_id: str) -> List[Dict]:
    """Get all connections for diagram generation."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT 
              r_src.resource_name as source,
              r_tgt.resource_name as target,
              rc.protocol,
              rc.is_cross_repo,
              repo_src.repo_name as source_repo,
              repo_tgt.repo_name as target_repo
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            JOIN repositories repo_src ON r_src.repo_id = repo_src.id
            JOIN repositories repo_tgt ON r_tgt.repo_id = repo_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id])
        
        return [dict(row) for row in cursor.fetchall()]


if __name__ == "__main__":
    # Test basic operations
    print(f"Database path: {DB_PATH}")
    print(f"Database exists: {DB_PATH.exists()}")
