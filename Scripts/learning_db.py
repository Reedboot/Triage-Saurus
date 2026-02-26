#!/usr/bin/env python3
"""SQLite schema and helpers for the Triage-Saurus learning system.

Creates and manages the learning database at Output/Learning/triage.db.

Tables:
- experiments: Metadata for each experiment run
- findings: Per-finding data within experiments
- scan_effectiveness: Metrics per scan type
- question_effectiveness: Metrics per question asked
- path_effectiveness: Metrics per file pattern
- validations: Human feedback on findings

Usage:
    python3 Scripts/learning_db.py init      # Create/reset database
    python3 Scripts/learning_db.py status    # Show current state
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from output_paths import OUTPUT_ROOT

LEARNING_DIR = OUTPUT_ROOT / "Learning"
DB_PATH = LEARNING_DIR / "triage.db"

SCHEMA = """
-- Experiments table
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    model TEXT,  -- Model used (e.g., claude-sonnet-4, claude-haiku-4.5)
    strategy_version TEXT,
    repos TEXT,  -- JSON array of repo names
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_sec INTEGER,
    tokens_used INTEGER,
    findings_count INTEGER,
    high_value_count INTEGER,
    avg_score REAL,
    false_positives INTEGER,
    accuracy_rate REAL,
    human_reviewed INTEGER DEFAULT 0,
    promoted_at TIMESTAMP,  -- When learnings were promoted to production
    promoted_by TEXT,  -- manual, automated, or user identifier
    notes TEXT
);

-- Findings within experiments
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    finding_name TEXT NOT NULL,
    repo TEXT,
    score INTEGER,
    resource_type TEXT,
    discovered_by TEXT,  -- which scan found it
    validation_status TEXT,  -- CONFIRMED, FALSE_POSITIVE, PENDING
    human_feedback TEXT,  -- JSON blob
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- Scan effectiveness metrics
CREATE TABLE IF NOT EXISTS scan_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    scan_type TEXT NOT NULL,  -- iac, sca, sast, secrets
    duration_sec INTEGER,
    findings_count INTEGER,
    high_value_count INTEGER,
    false_positive_count INTEGER,
    files_examined INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- Question effectiveness metrics
CREATE TABLE IF NOT EXISTS question_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT,
    findings_impacted INTEGER,
    avg_score_delta REAL,
    time_to_answer_sec INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- Path pattern effectiveness
CREATE TABLE IF NOT EXISTS path_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    pattern TEXT NOT NULL,
    files_matched INTEGER,
    security_hits INTEGER,
    hit_rate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- Human validations/feedback
CREATE TABLE IF NOT EXISTS validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    finding_name TEXT NOT NULL,
    verdict TEXT NOT NULL,  -- correct, score_too_high, score_too_low, false_positive, false_negative
    reason TEXT,
    correct_score INTEGER,
    evidence_location TEXT,
    learning_action TEXT,
    validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- Learned weights history
CREATE TABLE IF NOT EXISTS weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    weight_key TEXT NOT NULL,
    old_value REAL,
    new_value REAL,
    reason TEXT,
    experiment_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_findings_experiment ON findings(experiment_id);
CREATE INDEX IF NOT EXISTS idx_scan_experiment ON scan_effectiveness(experiment_id);
CREATE INDEX IF NOT EXISTS idx_validations_experiment ON validations(experiment_id);
CREATE INDEX IF NOT EXISTS idx_validations_verdict ON validations(verdict);
"""


def init_db() -> None:
    """Create or reset the learning database."""
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    
    print(f"Database initialized at {DB_PATH}")


def get_connection() -> sqlite3.Connection:
    """Get a connection to the learning database."""
    if not DB_PATH.exists():
        init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_status() -> dict:
    """Get current learning status."""
    conn = get_connection()
    
    # Count experiments by status
    experiments = conn.execute(
        "SELECT status, COUNT(*) as count FROM experiments GROUP BY status"
    ).fetchall()
    
    # Get latest experiment
    latest = conn.execute(
        "SELECT * FROM experiments ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    
    # Get aggregate metrics
    totals = conn.execute("""
        SELECT 
            COUNT(*) as total_experiments,
            SUM(findings_count) as total_findings,
            AVG(accuracy_rate) as avg_accuracy,
            AVG(duration_sec) as avg_duration
        FROM experiments
        WHERE status != 'pending'
    """).fetchone()
    
    # Get false positive patterns (if validations table exists)
    try:
        fp_patterns = conn.execute("""
            SELECT finding_name, COUNT(*) as fp_count
            FROM validations
            WHERE verdict = 'false_positive'
            GROUP BY finding_name
            ORDER BY fp_count DESC
            LIMIT 5
        """).fetchall()
    except sqlite3.OperationalError:
        # validations table doesn't exist yet
        fp_patterns = []
    
    conn.close()
    
    return {
        "experiments_by_status": {row["status"]: row["count"] for row in experiments},
        "latest_experiment": dict(latest) if latest else None,
        "totals": dict(totals) if totals else {},
        "top_false_positive_patterns": [dict(row) for row in fp_patterns],
    }


def print_status() -> None:
    """Print current learning status to stdout."""
    status = get_status()
    
    print("== Learning Database Status ==")
    print(f"Database: {DB_PATH}")
    print()
    
    print("Experiments by status:")
    for s, count in status.get("experiments_by_status", {}).items():
        print(f"  {s}: {count}")
    print()
    
    if status.get("latest_experiment"):
        exp = status["latest_experiment"]
        print(f"Latest experiment: {exp.get('id')} ({exp.get('status')})")
        if exp.get("findings_count"):
            print(f"  Findings: {exp['findings_count']}")
        if exp.get("accuracy_rate"):
            print(f"  Accuracy: {exp['accuracy_rate']:.1%}")
    print()
    
    totals = status.get("totals", {})
    if totals.get("total_experiments"):
        print("Aggregate metrics:")
        print(f"  Total experiments: {totals['total_experiments']}")
        print(f"  Total findings: {totals['total_findings'] or 0}")
        if totals.get("avg_accuracy"):
            print(f"  Average accuracy: {totals['avg_accuracy']:.1%}")
        if totals.get("avg_duration"):
            print(f"  Average duration: {totals['avg_duration']:.0f}s")
    print()
    
    if status.get("top_false_positive_patterns"):
        print("Top false positive patterns:")
        for row in status["top_false_positive_patterns"]:
            print(f"  {row['finding_name']}: {row['fp_count']} times")


def create_experiment(exp_id: str, name: str, repos: list[str], strategy: str = "default", model: str = None) -> None:
    """Create a new experiment record."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO experiments (id, name, status, model, strategy_version, repos, started_at)
        VALUES (?, ?, 'pending', ?, ?, ?, ?)
        """,
        (exp_id, name, model, strategy, json.dumps(repos), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def update_experiment(exp_id: str, **kwargs) -> None:
    """Update experiment fields."""
    conn = get_connection()
    
    # Build SET clause dynamically
    set_parts = []
    values = []
    for key, value in kwargs.items():
        set_parts.append(f"{key} = ?")
        values.append(value)
    
    values.append(exp_id)
    
    conn.execute(
        f"UPDATE experiments SET {', '.join(set_parts)} WHERE id = ?",
        values
    )
    conn.commit()
    conn.close()


def record_validation(
    experiment_id: str,
    finding_name: str,
    verdict: str,
    reason: str = None,
    correct_score: int = None,
    evidence_location: str = None,
    learning_action: str = None,
) -> None:
    """Record human validation feedback."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO validations 
        (experiment_id, finding_name, verdict, reason, correct_score, evidence_location, learning_action)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (experiment_id, finding_name, verdict, reason, correct_score, evidence_location, learning_action)
    )
    conn.commit()
    conn.close()


def get_scan_effectiveness_summary() -> list[dict]:
    """Get aggregated scan effectiveness across all experiments."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT 
            scan_type,
            COUNT(*) as run_count,
            AVG(findings_count) as avg_findings,
            AVG(high_value_count) as avg_high_value,
            AVG(CAST(false_positive_count AS REAL) / NULLIF(findings_count, 0)) as avg_fp_rate,
            AVG(duration_sec) as avg_duration
        FROM scan_effectiveness
        GROUP BY scan_type
        ORDER BY avg_high_value DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_question_effectiveness_summary() -> list[dict]:
    """Get aggregated question effectiveness across all experiments."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT 
            question_key,
            COUNT(*) as times_asked,
            SUM(findings_impacted) as total_findings_impacted,
            AVG(avg_score_delta) as overall_avg_delta,
            AVG(time_to_answer_sec) as avg_answer_time
        FROM question_effectiveness
        GROUP BY question_key
        ORDER BY total_findings_impacted DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Triage-Saurus learning database.")
    parser.add_argument(
        "command",
        choices=["init", "status"],
        help="Command to run",
    )
    args = parser.parse_args()
    
    if args.command == "init":
        init_db()
    elif args.command == "status":
        print_status()
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
