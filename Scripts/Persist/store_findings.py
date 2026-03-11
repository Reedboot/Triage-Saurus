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
import db_helpers


def _severity_score(severity: str) -> int:
    return 8 if severity.upper() == "ERROR" else 5


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

    with db_helpers.get_db_connection() as conn:
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

            # insert_finding needs repo_name; resource lookup is internal
            finding_id = db_helpers.insert_finding(
                experiment_id=args.experiment,
                repo_name=args.repo,
                finding_name=rule_id,
                resource_name=None,
                score=sev_score,
                severity=base_sev,
                category=category,
                evidence_location=f"{path}:{start_line}",
                discovered_by="store_findings",
                title=title,
                reason=reason,
                severity_score=sev_score,
                source_file=path,
                source_line_start=start_line,
                source_line_end=end_line,
                code_snippet=code_snippet,
                rule_id=rule_id,
            )

            # Manually link resource_id if we found one (insert_finding can't do it without resource_name)
            if resource_id is not None:
                conn.execute(
                    "UPDATE findings SET resource_id = ? WHERE id = ?",
                    (resource_id, finding_id),
                )

            db_helpers.record_risk_score(finding_id, sev_score, scored_by="script")

            print(f"  [stored] finding {finding_id}: {title} ({base_sev})")
            stored += 1

    print(f"\nStored {stored} new findings, skipped {skipped} duplicates")


if __name__ == "__main__":
    main()
