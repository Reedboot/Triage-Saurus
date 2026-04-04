#!/usr/bin/env python3
"""Re-link existing findings to resources using improved matching logic.

This script takes findings that have resource_id = NULL and attempts to link them
to resources using the new path normalization and line-range matching strategy.
"""

import sqlite3
import sys
from pathlib import Path


def _normalize_path(path: str) -> str:
    """Normalize a file path to match format in resources table."""
    if not path:
        return ""
    path = path.replace("\\", "/")
    for prefix in ["/home/neil/code/terragoat/", "/home/neil/code/"]:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return path.lstrip("/")


def _find_resource_id(conn, experiment_id: str, path: str, start_line: int):
    """Try to match a resource by source file + line range."""
    norm_path = _normalize_path(path)
    
    # First, try exact match with source_line_end set
    row = conn.execute("""
        SELECT id FROM resources
        WHERE experiment_id = ?
          AND (source_file = ? OR source_file = ?)
          AND source_line_start <= ?
          AND source_line_end IS NOT NULL
          AND source_line_end >= ?
        LIMIT 1
    """, (experiment_id, path, norm_path, start_line, start_line)).fetchone()
    if row:
        return row[0]
    
    # Fallback: find closest resource at/before this line
    row = conn.execute("""
        SELECT id FROM resources
        WHERE experiment_id = ?
          AND (source_file = ? OR source_file = ?)
          AND source_line_start <= ?
        ORDER BY source_line_start DESC
        LIMIT 1
    """, (experiment_id, path, norm_path, start_line)).fetchone()
    return row[0] if row else None


def main():
    db_path = Path("Output/Data/cozo.db")
    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Find all findings with NULL resource_id
    cursor.execute("""
        SELECT id, experiment_id, source_file, source_line_start, rule_id
        FROM findings
        WHERE resource_id IS NULL
        ORDER BY experiment_id, source_file, source_line_start
    """)
    findings = cursor.fetchall()
    
    print(f"Found {len(findings)} findings without resource linkage")
    print()
    
    linked_count = 0
    for finding_id, exp_id, source_file, start_line, rule_id in findings:
        resource_id = _find_resource_id(conn, exp_id, source_file, start_line)
        
        if resource_id:
            # Get resource details for display
            res_row = cursor.execute(
                "SELECT resource_name, resource_type FROM resources WHERE id = ?",
                (resource_id,)
            ).fetchone()
            res_name, res_type = res_row if res_row else ("unknown", "unknown")
            
            # Update the finding
            cursor.execute(
                "UPDATE findings SET resource_id = ? WHERE id = ?",
                (resource_id, finding_id)
            )
            linked_count += 1
            print(f"✓ Linked finding #{finding_id} ({rule_id}) → {res_name} ({res_type})")
    
    conn.commit()
    conn.close()
    
    print()
    print(f"Successfully linked {linked_count} / {len(findings)} findings to resources")
    if linked_count > 0:
        print("Run the analysis again to see findings with proper resource context")


if __name__ == "__main__":
    main()
