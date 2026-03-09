#!/usr/bin/env python3
"""CozoDB schema and helpers for the Triage-Saurus learning system.

Creates and manages the learning database at Output/Learning/triage.cozo
using the RocksDB storage engine via pycozo.

Relations:
- experiments: Metadata for each experiment run
- findings: Per-finding data within experiments
- scan_effectiveness: Metrics per scan type
- question_effectiveness: Metrics per question asked
- path_effectiveness: Metrics per file pattern
- validations: Human feedback on findings
- weight_history: Learned weights evolution

Usage:
    python3 Scripts/learning_db.py init      # Create/reset database
    python3 Scripts/learning_db.py status    # Show current state
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pycozo.client import Client

from output_paths import OUTPUT_ROOT

LEARNING_DIR = OUTPUT_ROOT / "Learning"
DB_PATH = LEARNING_DIR / "triage.cozo"

# ── Relation schemas ──────────────────────────────────────────────────────────

_RELATIONS: dict[str, str] = {
    "ts_seqs": ":create ts_seqs { name: String => value: Int }",
    "experiments": """
        :create experiments {
            id: String =>
            name: String,
            status: String,
            strategy_version: String?,
            repos: String?,
            started_at: String?,
            completed_at: String?,
            duration_sec: Int?,
            tokens_used: Int?,
            findings_count: Int?,
            high_value_count: Int?,
            avg_score: Float?,
            false_positives: Int?,
            accuracy_rate: Float?,
            human_reviewed: Int,
            promoted_at: String?,
            promoted_by: String?,
            notes: String?
        }
    """,
    "findings_learning": """
        :create findings_learning {
            id: Int =>
            experiment_id: String,
            finding_name: String,
            repo: String?,
            score: Int?,
            resource_type: String?,
            discovered_by: String?,
            validation_status: String?,
            human_feedback: String?
        }
    """,
    "scan_effectiveness": """
        :create scan_effectiveness {
            id: Int =>
            experiment_id: String,
            scan_type: String,
            duration_sec: Int?,
            findings_count: Int?,
            high_value_count: Int?,
            false_positive_count: Int?,
            files_examined: Int?,
            created_at: String?
        }
    """,
    "question_effectiveness": """
        :create question_effectiveness {
            id: Int =>
            experiment_id: String,
            question_key: String,
            question_text: String?,
            findings_impacted: Int?,
            avg_score_delta: Float?,
            time_to_answer_sec: Int?,
            created_at: String?
        }
    """,
    "path_effectiveness": """
        :create path_effectiveness {
            id: Int =>
            experiment_id: String,
            pattern: String,
            files_matched: Int?,
            security_hits: Int?,
            hit_rate: Float?,
            created_at: String?
        }
    """,
    "validations": """
        :create validations {
            id: Int =>
            experiment_id: String,
            finding_name: String,
            verdict: String,
            reason: String?,
            correct_score: Int?,
            evidence_location: String?,
            learning_action: String?,
            validated_at: String?
        }
    """,
    "weight_history": """
        :create weight_history {
            id: Int =>
            weight_key: String,
            old_value: Float?,
            new_value: Float?,
            reason: String?,
            experiment_id: String?,
            created_at: String?
        }
    """,
}

# ── Internal helpers ──────────────────────────────────────────────────────────

_db_cache: dict[str, Client] = {}


def _get_db(path: Path = DB_PATH) -> Client:
    """Return (or lazily open) the CozoDB client for *path*."""
    key = str(path)
    if key not in _db_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        _db_cache[key] = Client("rocksdb", str(path), dataframe=False)
    return _db_cache[key]


def _rows_to_dicts(result: dict) -> list[dict]:
    """Convert a CozoDB query result to a list of dicts."""
    headers = result["headers"]
    return [dict(zip(headers, row)) for row in result["rows"]]


def _next_id(db: Client, seq_name: str) -> int:
    """Return the next auto-increment ID for *seq_name*."""
    result = db.run("?[v] := *ts_seqs[$name, v]", {"name": seq_name})
    current = result["rows"][0][0] if result["rows"] else 0
    new_id = current + 1
    db.put("ts_seqs", [{"name": seq_name, "value": new_id}])
    return new_id


def _now() -> str:
    return datetime.now().isoformat()


# ── Public API ────────────────────────────────────────────────────────────────


def init_db() -> None:
    """Create or upgrade the learning database."""
    db = _get_db()
    existing = {row[0] for row in db.relations()["rows"]}
    for name, schema in _RELATIONS.items():
        if name not in existing:
            db.run(schema)
        # Seed zero-value counters for each sequence-based relation
    seq_relations = {
        "findings_learning",
        "scan_effectiveness",
        "question_effectiveness",
        "path_effectiveness",
        "validations",
        "weight_history",
    }
    for rel in seq_relations:
        existing_seq = db.run("?[v] := *ts_seqs[$n, v]", {"n": rel})
        if not existing_seq["rows"]:
            db.put("ts_seqs", [{"name": rel, "value": 0}])
    print(f"Database initialized at {DB_PATH}")


def get_connection() -> Client:
    """Return the CozoDB client, initialising if needed."""
    if not DB_PATH.exists():
        init_db()
    return _get_db()


def get_status() -> dict:
    """Return current learning status."""
    db = get_connection()

    # Count experiments by status
    exp_rows = _rows_to_dicts(
        db.run("?[status, count(id)] := *experiments[id, _, status, _, _, _, _, _, _, _, _, _, _, _, _, _, _]")
    )

    # Latest experiment
    latest_rows = _rows_to_dicts(
        db.run(
            "?[id, name, status, findings_count, accuracy_rate, started_at] := "
            "*experiments[id, name, status, _, _, _, started_at, _, _, _, findings_count, _, _, accuracy_rate, _, _, _] "
            ":order -started_at :limit 1"
        )
    )

    # Aggregate metrics
    totals_rows = _rows_to_dicts(
        db.run(
            "?[total_experiments, total_findings, avg_accuracy, avg_duration] := "
            "total_experiments = count(id), "
            "total_findings = sum(coalesce(fc, 0)), "
            "avg_accuracy = mean(ar), "
            "avg_duration = mean(coalesce(ds, 0)) "
            ":- *experiments[id, _, status, _, _, _, _, _, ds, _, fc, _, _, ar, _, _, _], "
            "status != 'pending'"
        )
    )

    # Top false positive patterns
    fp_rows = _rows_to_dicts(
        db.run(
            "?[finding_name, fp_count] := "
            "fp_count = count(id), "
            ":- *validations[id, _, finding_name, verdict, _, _, _, _, _], "
            "verdict = 'false_positive' "
            ":order -fp_count :limit 5"
        )
    )

    return {
        "experiments_by_status": {row["status"]: row["count(id)"] for row in exp_rows},
        "latest_experiment": latest_rows[0] if latest_rows else None,
        "totals": totals_rows[0] if totals_rows else {},
        "top_false_positive_patterns": fp_rows,
    }


def print_status() -> None:
    """Print current learning status to stdout."""
    db = get_connection()

    print("== Learning Database Status ==")
    print(f"Database: {DB_PATH}")
    print()

    exp_rows = _rows_to_dicts(
        db.run(
            "?[status, n] := n = count(id) :- *experiments[id, _, status, _, _, _, _, _, _, _, _, _, _, _, _, _, _]"
        )
    )
    print("Experiments by status:")
    for row in exp_rows:
        print(f"  {row['status']}: {row['n']}")
    print()

    latest_rows = _rows_to_dicts(
        db.run(
            "?[id, name, status, findings_count, accuracy_rate] := "
            "*experiments[id, name, status, _, _, _, started_at, _, _, _, findings_count, _, _, accuracy_rate, _, _, _] "
            ":order -started_at :limit 1"
        )
    )
    if latest_rows:
        exp = latest_rows[0]
        print(f"Latest experiment: {exp.get('id')} ({exp.get('status')})")
        if exp.get("findings_count"):
            print(f"  Findings: {exp['findings_count']}")
        if exp.get("accuracy_rate"):
            print(f"  Accuracy: {exp['accuracy_rate']:.1%}")
    print()

    total_rows = _rows_to_dicts(
        db.run(
            "?[n] := n = count(id) :- *experiments[id, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _]"
        )
    )
    if total_rows and total_rows[0].get("n", 0):
        print(f"Aggregate metrics:")
        print(f"  Total experiments: {total_rows[0]['n']}")


def create_experiment(
    exp_id: str,
    name: str,
    repos: list[str],
    strategy: str = "default",
) -> None:
    """Create a new experiment record."""
    db = get_connection()
    db.put(
        "experiments",
        [
            {
                "id": exp_id,
                "name": name,
                "status": "pending",
                "strategy_version": strategy,
                "repos": json.dumps(repos),
                "started_at": _now(),
                "completed_at": None,
                "duration_sec": None,
                "tokens_used": None,
                "findings_count": None,
                "high_value_count": None,
                "avg_score": None,
                "false_positives": None,
                "accuracy_rate": None,
                "human_reviewed": 0,
                "promoted_at": None,
                "promoted_by": None,
                "notes": None,
            }
        ],
    )


def update_experiment(exp_id: str, **kwargs) -> None:
    """Update experiment fields."""
    db = get_connection()
    db.update("experiments", [{"id": exp_id, **kwargs}])


def record_validation(
    experiment_id: str,
    finding_name: str,
    verdict: str,
    reason: Optional[str] = None,
    correct_score: Optional[int] = None,
    evidence_location: Optional[str] = None,
    learning_action: Optional[str] = None,
) -> None:
    """Record human validation feedback."""
    db = get_connection()
    new_id = _next_id(db, "validations")
    db.put(
        "validations",
        [
            {
                "id": new_id,
                "experiment_id": experiment_id,
                "finding_name": finding_name,
                "verdict": verdict,
                "reason": reason,
                "correct_score": correct_score,
                "evidence_location": evidence_location,
                "learning_action": learning_action,
                "validated_at": _now(),
            }
        ],
    )


def get_scan_effectiveness_summary() -> list[dict]:
    """Get aggregated scan effectiveness across all experiments."""
    db = get_connection()
    rows = _rows_to_dicts(
        db.run(
            "?[scan_type, run_count, avg_findings, avg_high_value, avg_fp_rate, avg_duration] := "
            "run_count = count(id), "
            "avg_findings = mean(coalesce(findings_count, 0)), "
            "avg_high_value = mean(coalesce(high_value_count, 0)), "
            "avg_fp_rate = mean(coalesce(false_positive_count, 0)), "
            "avg_duration = mean(coalesce(duration_sec, 0)) "
            ":- *scan_effectiveness[id, eid, scan_type, duration_sec, findings_count, "
            "high_value_count, false_positive_count, _, _] "
            ":order -avg_high_value"
        )
    )
    return rows


def get_question_effectiveness_summary() -> list[dict]:
    """Get aggregated question effectiveness across all experiments."""
    db = get_connection()
    rows = _rows_to_dicts(
        db.run(
            "?[question_key, times_asked, total_findings_impacted, overall_avg_delta, avg_answer_time] := "
            "times_asked = count(id), "
            "total_findings_impacted = sum(coalesce(findings_impacted, 0)), "
            "overall_avg_delta = mean(coalesce(avg_score_delta, 0.0)), "
            "avg_answer_time = mean(coalesce(time_to_answer_sec, 0)) "
            ":- *question_effectiveness[id, _, question_key, _, findings_impacted, avg_score_delta, time_to_answer_sec, _] "
            ":order -total_findings_impacted"
        )
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage the Triage-Saurus learning database."
    )
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
