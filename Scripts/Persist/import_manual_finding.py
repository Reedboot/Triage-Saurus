#!/usr/bin/env python3
"""Import manually-created findings into the database.

Usage:
    python3 Scripts/Persist/import_manual_finding.py \\
        --experiment 001 \\
        --file Findings/Code/FI_AVP_001_Unsecured_Header_Auth.md \\
        --rule-id manual-avp-001 \\
        --severity 10

Or with metadata parsing from markdown frontmatter (if present):
    python3 Scripts/Persist/import_manual_finding.py \\
        --experiment 001 \\
        --file Findings/Code/FI_AVP_001_Unsecured_Header_Auth.md
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Utils"))
import db_helpers


def parse_markdown_frontmatter(content: str) -> dict:
    """Extract metadata from YAML frontmatter if present."""
    metadata = {}
    
    # Try to extract title from first heading
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if title_match:
        metadata['title'] = title_match.group(1).strip()
    
    # Try to extract severity from markdown (e.g., "**Severity:** 10/10" or "🔴 CRITICAL")
    severity_match = re.search(r'\*\*Severity:?\*\*\s*[:\-]?\s*(\d+)', content, re.IGNORECASE)
    if severity_match:
        metadata['severity_score'] = int(severity_match.group(1))
    elif '🔴' in content or 'CRITICAL' in content.upper():
        metadata['severity_score'] = 10
        metadata['base_severity'] = 'Critical'
    elif '🟠' in content or 'HIGH' in content.upper():
        metadata['severity_score'] = 7
        metadata['base_severity'] = 'High'
    elif '🟡' in content or 'MEDIUM' in content.upper():
        metadata['severity_score'] = 5
        metadata['base_severity'] = 'Medium'
    
    # Try to extract file location
    location_match = re.search(r'\*\*Location:?\*\*\s*[:\-]?\s*`?([^`\n]+)`?', content, re.IGNORECASE)
    if location_match:
        location = location_match.group(1).strip()
        # Extract file and line if format is "file.cs:123"
        if ':' in location:
            file_part, line_part = location.rsplit(':', 1)
            metadata['source_file'] = file_part
            try:
                metadata['source_line_start'] = int(line_part)
            except ValueError:
                pass
        else:
            metadata['source_file'] = location
    
    # Try to extract CWE
    cwe_match = re.search(r'CWE-(\d+)', content)
    if cwe_match:
        metadata['cwe'] = f"CWE-{cwe_match.group(1)}"
    
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Import manual finding into database")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--file", required=True, help="Path to finding markdown file")
    parser.add_argument("--rule-id", help="Rule ID (auto-generated if not provided)")
    parser.add_argument("--severity", type=int, help="Severity score 1-10 (parsed from file if not provided)")
    parser.add_argument("--repo", help="Repository name (for repo_id lookup)")
    parser.add_argument("--category", default="Code", help="Finding category (default: Code)")
    args = parser.parse_args()

    finding_file = Path(args.file)
    if not finding_file.exists():
        print(f"ERROR: File not found: {finding_file}", file=sys.stderr)
        sys.exit(1)

    # Read finding content
    content = finding_file.read_text(encoding='utf-8')
    
    # Parse metadata from markdown
    metadata = parse_markdown_frontmatter(content)
    
    # Auto-generate finding_name from filename if not in metadata
    finding_name = finding_file.stem  # e.g., FI_AVP_001_Unsecured_Header_Auth
    
    # Auto-generate rule_id if not provided
    rule_id = args.rule_id
    if not rule_id:
        # Extract from finding_name: FI_AVP_001_... -> manual-avp-001
        match = re.search(r'FI_([A-Z]+)_(\d+)', finding_name)
        if match:
            rule_id = f"manual-{match.group(1).lower()}-{match.group(2)}"
        else:
            rule_id = f"manual-{finding_name.lower()}"
    
    # Build finding data
    severity_score = args.severity or metadata.get('severity_score', 5)
    base_severity = metadata.get('base_severity')
    if not base_severity:
        if severity_score >= 9:
            base_severity = 'Critical'
        elif severity_score >= 7:
            base_severity = 'High'
        elif severity_score >= 4:
            base_severity = 'Medium'
        else:
            base_severity = 'Low'
    
    title = metadata.get('title', finding_name.replace('_', ' '))
    source_file = metadata.get('source_file', '')
    source_line_start = metadata.get('source_line_start', 0)
    
    # Get repo_id if repo name provided
    repo_id = None
    if args.repo:
        with db_helpers.get_db_connection() as conn:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
                (args.experiment, args.repo)
            ).fetchone()
            repo_id = repo_row[0] if repo_row else None
    
    # Check if already exists
    with db_helpers.get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM findings WHERE experiment_id = ? AND rule_id = ?",
            (args.experiment, rule_id)
        ).fetchone()
        
        if existing:
            print(f"Finding already exists with ID {existing[0]}: {title}")
            sys.exit(0)
    
    # Insert finding
    now = datetime.utcnow().isoformat()
    
    with db_helpers.get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO findings (
                experiment_id, repo_id, rule_id, finding_name, title, 
                category, source_file, source_line_start, 
                severity_score, base_severity, finding_path, 
                status, llm_enriched_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            args.experiment, repo_id, rule_id, finding_name, title,
            args.category, source_file, source_line_start,
            severity_score, base_severity, str(finding_file),
            'enriched', now, now, now
        ))
        
        finding_id = cursor.lastrowid
        conn.commit()
        
        # Record initial risk score
        db_helpers.record_risk_score(finding_id, severity_score, scored_by="manual", conn=conn)
        
        print(f"✓ Imported finding ID {finding_id}: {title} ({base_severity} - {severity_score}/10)")
        print(f"  Rule ID: {rule_id}")
        print(f"  Finding Name: {finding_name}")
        print(f"  File: {finding_file}")


if __name__ == "__main__":
    main()
