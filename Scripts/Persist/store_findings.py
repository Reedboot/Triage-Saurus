#!/usr/bin/env python3
"""Phase 1 pipeline: parse opengrep JSON output and write findings to the DB.

Usage:
    python3 Scripts/store_findings.py <scan_json> --experiment <id> [--repo <name>]
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Utils"))
import db_helpers
from shared_utils import _severity_score


def _base_severity(severity: str) -> str:
    return "High" if severity.upper() == "ERROR" else "Medium"


def _find_resource_id(conn, experiment_id: str, path: str, start_line: int):
    """Try to match a resource by source file + line range."""
    row = conn.execute("""
        SELECT id FROM resources
        WHERE experiment_id = ?
          AND source_file = ?
          AND source_line_start <= ?
          AND source_line_end >= ?
        LIMIT 1
    """, (experiment_id, path, start_line, start_line)).fetchone()
    return row[0] if row else None


def _already_exists(conn, experiment_id: str, rule_id: str, source_file: str, source_line_start: int) -> bool:
    row = conn.execute("""
        SELECT id FROM findings
        WHERE experiment_id = ?
          AND rule_id = ?
          AND source_file = ?
          AND source_line_start = ?
        LIMIT 1
    """, (experiment_id, rule_id, source_file, source_line_start)).fetchone()
    return row is not None


def main():
    parser = argparse.ArgumentParser(description="Store opengrep findings to DB")
    parser.add_argument("scan_json", help="Path to opengrep JSON output file")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--repo", default="unknown", help="Repository name")
    args = parser.parse_args()

    scan_path = Path(args.scan_json)
    if not scan_path.exists():
        print(f"ERROR: file not found: {scan_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(scan_path.read_text())
    results = data.get("results", [])

    stored = 0
    skipped = 0
    resource_updates = []  # Track resource_id updates to batch later

    with db_helpers.get_db_connection() as conn:
        # Get repo_id once at the start
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
            (args.experiment, args.repo),
        ).fetchone()
        repo_id = repo_row[0] if repo_row else None

        # Prepare all findings for batch insert
        findings_to_insert = []

        for result in results:
            check_id: str = result.get("check_id", "")
            severity: str = result.get("extra", {}).get("severity", "WARNING")

            # Skip INFO and Context rules
            if severity.upper() == "INFO":
                continue
            if "Context" in check_id:
                continue

            path: str = result.get("path", "")
            start_line: int = result.get("start", {}).get("line", 0)
            end_line: int = result.get("end", {}).get("line", start_line)
            extra = result.get("extra", {})
            message: str = extra.get("message", "")
            reason: str = message.split("\n")[0]
            code_snippet: str = extra.get("lines", "")
            category: str = (extra.get("metadata") or {}).get("category", "IaC")

            rule_id: str = check_id.split(".")[-1]
            title: str = rule_id.replace("-", " ").title()
            sev_score = _severity_score(severity)
            base_sev = _base_severity(severity)

            # Duplicate check
            if _already_exists(conn, args.experiment, rule_id, path, start_line):
                print(f"  [skip] duplicate: {rule_id} {path}:{start_line}")
                skipped += 1
                continue

            resource_id = _find_resource_id(conn, args.experiment, path, start_line)

            findings_to_insert.append({
                'experiment_id': args.experiment,
                'repo_id': repo_id,
                'resource_id': resource_id,
                'title': title,
                'description': None,
                'category': category,
                'severity_score': sev_score,
                'base_severity': base_sev,
                'evidence_location': f"{path}:{start_line}",
                'source_file': path,
                'source_line_start': start_line,
                'source_line_end': end_line,
                'rule_id': rule_id,
                'proposed_fix': None,
                'code_snippet': code_snippet,
                'reason': reason,
            })

        # Batch insert all findings
        if findings_to_insert:
            finding_ids = db_helpers.batch_insert_findings(conn, findings_to_insert)

            # Record risk scores for all findings
            for finding_id, finding_data in zip(finding_ids, findings_to_insert):
                db_helpers.record_risk_score(finding_id, finding_data['severity_score'], scored_by="script", conn=conn)
                print(f"  [stored] finding {finding_id}: {finding_data['title']} ({finding_data['base_severity']})")
                stored += 1

    print(f"\nStored {stored} new findings, skipped {skipped} duplicates")


if __name__ == "__main__":
    main()
