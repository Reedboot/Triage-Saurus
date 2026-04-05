#!/usr/bin/env python3
"""Phase 1 pipeline: parse opengrep JSON output and write findings to the DB.

Usage:
    python3 Scripts/store_findings.py <scan_json> --experiment <id> [--repo <name>]
    opengrep scan --config Rules/Misconfigurations /path/to/repo --json --quiet | \
        python3 Scripts/store_findings.py --stdin-json --experiment <id> [--repo <name>]
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
    s = severity.upper()
    if s == "ERROR":
        return "High"
    if s == "INFO":
        return "Low"
    return "Medium"


# File extension → display language name
_EXT_TO_LANG: dict[str, str] = {
    'cs': 'C#/.NET', 'csproj': 'C#/.NET', 'fsproj': 'C#/.NET',
    'vbproj': 'C#/.NET', 'sln': 'C#/.NET',
    'tf': 'Terraform', 'tfvars': 'Terraform',
    'py': 'Python',
    'ts': 'TypeScript', 'tsx': 'TypeScript',
    'js': 'JavaScript', 'jsx': 'JavaScript',
    'go': 'Go',
    'java': 'Java', 'kt': 'Kotlin',
    'rb': 'Ruby',
    'php': 'PHP',
    'rs': 'Rust',
    'swift': 'Swift',
    'scala': 'Scala',
    'sql': 'SQL',
    'ps1': 'PowerShell',
}
# Languages considered infrastructure/config — deprioritised for primary selection
_CONFIG_LANGS = {'Terraform', 'SQL', 'PowerShell'}


def _detect_languages(scanned_paths: list[str]) -> tuple[str, list[str]]:
    """Derive (primary_language, ordered_all_languages) from a list of scanned file paths."""
    counts: dict[str, int] = {}
    for path in scanned_paths:
        filename = path.rsplit('/', 1)[-1]
        if '.' not in filename:
            continue
        ext = filename.rsplit('.', 1)[-1].lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return '', []
    ordered = sorted(counts, key=lambda l: counts[l], reverse=True)
    # Prefer a non-config language as the primary
    primary = next((l for l in ordered if l not in _CONFIG_LANGS), ordered[0])
    return primary, ordered


def _normalize_path(path: str) -> str:
    """Normalize a file path to match format in resources table.
    
    - Removes leading repo/project prefix
    - Handles both absolute and relative paths
    - Returns path relative to repo root
    """
    if not path:
        return ""
    
    # Remove common prefixes
    path = path.replace("\\", "/")  # Normalize separators
    for prefix in ["/home/neil/code/terragoat/", "/home/neil/code/"]:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    
    return path.lstrip("/")


def _find_resource_id(conn, experiment_id: str, path: str, start_line: int):
    """Try to match a resource by source file + line range.
    
    This uses a three-tier strategy:
    1. Exact match: resource's source_line_end is set and contains the finding
    2. Fallback 1: resource's source_line_end is NULL, use closest resource before finding
    3. Fallback 2: Try with normalized path if direct match failed
    """
    # Normalize the path to match what's in the resources table
    norm_path = _normalize_path(path)
    
    # First, try exact match with source_line_end (handles properly parsed resources)
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
    
    # Fallback: If source_line_end is missing, find closest resource at or before this line
    # Try both original and normalized path
    row = conn.execute("""
        SELECT id FROM resources
        WHERE experiment_id = ?
          AND (source_file = ? OR source_file = ?)
          AND source_line_start <= ?
        ORDER BY source_line_start DESC
        LIMIT 1
    """, (experiment_id, path, norm_path, start_line)).fetchone()
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
    parser.add_argument("scan_json", nargs="?", help="Path to opengrep JSON output file")
    parser.add_argument(
        "--stdin-json",
        action="store_true",
        help="Read opengrep JSON payload from stdin instead of a file",
    )
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--repo", default="unknown", help="Repository name")
    args = parser.parse_args()

    if args.stdin_json:
        raw = sys.stdin.read()
        if not raw.strip():
            data = {"results": [], "paths": {"scanned": []}}
        else:
            data = json.loads(raw)
    else:
        if not args.scan_json:
            print("ERROR: provide <scan_json> or use --stdin-json", file=sys.stderr)
            sys.exit(1)

        scan_path = Path(args.scan_json)
        if not scan_path.exists():
            print(f"ERROR: file not found: {scan_path}", file=sys.stderr)
            sys.exit(1)

        data = json.loads(scan_path.read_text())
    results = data.get("results", [])

    # Detect languages from the full list of scanned files
    scanned_paths = data.get("paths", {}).get("scanned", [])
    primary_lang, all_langs = _detect_languages(scanned_paths)

    stored = 0
    skipped = 0
    resource_updates = []  # Track resource_id updates to batch later

    with db_helpers.get_db_connection() as conn:
        # Get or create repo_id once at the start
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
            (args.experiment, args.repo),
        ).fetchone()
        if repo_row:
            repo_id = repo_row[0]
        else:
            # Ensure repository row exists so findings can be joined correctly in the UI
            conn.execute(
                "INSERT INTO repositories (experiment_id, repo_name) VALUES (?, ?)",
                (args.experiment, args.repo),
            )
            repo_id = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
                (args.experiment, args.repo),
            ).fetchone()[0]

        # Prepare all findings for batch insert
        findings_to_insert = []

        for result in results:
            check_id: str = result.get("check_id", "")
            severity: str = result.get("extra", {}).get("severity", "WARNING")
            extra = result.get("extra", {})

            # Skip pure asset-detection context-discovery rules (INFO severity only)
            # All other INFO findings are stored with base_severity=Low and severity_score=2
            if severity.upper() == 'INFO' and (extra.get('metadata') or {}).get('rule_type') == 'context_discovery':
                continue

            path: str = result.get("path", "")
            start_line: int = result.get("start", {}).get("line", 0)
            end_line: int = result.get("end", {}).get("line", start_line)
            message: str = extra.get("message", "")
            reason: str = message.split("\n")[0]
            code_snippet: str = extra.get("lines", "")
            category: str = (extra.get("metadata") or {}).get("category", "IaC")

            rule_id: str = check_id.split(".")[-1]
            title: str = rule_id.replace("-", " ").title()
            sev_score = _severity_score(severity)
            base_sev = _base_severity(severity)
            
            # Generate finding_name: FI_{REPO}_{RULE_ID}
            repo_abbrev = args.repo.replace("-", "_").upper()[:20]  # Limit length
            rule_abbrev = rule_id.replace("-", "_").upper()
            finding_name = f"FI_{repo_abbrev}_{rule_abbrev}"

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
                'finding_name': finding_name,
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

    # Persist language detection results
    if primary_lang or all_langs:
        with db_helpers.get_db_connection() as conn:
            conn.execute(
                """
                UPDATE repositories
                SET primary_language = ?
                WHERE experiment_id = ? AND repo_name = ?
                """,
                (primary_lang, args.experiment, args.repo),
            )
        if all_langs:
            db_helpers.upsert_context_metadata(
                args.experiment, args.repo,
                key="languages_detected",
                value=", ".join(all_langs),
                namespace="scan",
                source="store_findings",
            )
        print(f"  Primary language : {primary_lang or '(none)'}")
        print(f"  Languages detected: {', '.join(all_langs) or '(none)'}")

    # Mark experiment as complete now that findings are stored
    with db_helpers.get_db_connection() as conn:
        conn.execute(
            """
            UPDATE experiments
            SET status = 'complete',
                completed_at = COALESCE(completed_at, datetime('now'))
            WHERE id = ?
            """,
            (args.experiment,),
        )


if __name__ == "__main__":
    main()
