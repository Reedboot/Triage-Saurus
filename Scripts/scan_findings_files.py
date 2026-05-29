#!/usr/bin/env python3
"""
Scan Output/Findings/ for all finding .md files and return a structured list.

Used by agents to discover what findings exist for the current session.

Usage:
    python3 Scripts/scan_findings_files.py [--json] [--provider azure|aws|gcp|code]

Output (default): one file path per line
Output (--json):  JSON array of {path, provider, filename} objects
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINDINGS_DIR = ROOT / "Output" / "Findings"


def scan_findings(provider: str | None = None) -> list[dict]:
    """Return all finding .md files under Output/Findings/, optionally filtered by provider."""
    results = []

    if not FINDINGS_DIR.exists():
        return results

    for md_file in sorted(FINDINGS_DIR.rglob("*.md")):
        rel = md_file.relative_to(ROOT)
        parts = rel.parts  # e.g. ('Output', 'Findings', 'Cloud', 'finding.md')
        inferred_provider = parts[2].lower() if len(parts) > 3 else "unknown"

        if provider and inferred_provider != provider.lower():
            continue

        results.append({
            "path": str(rel),
            "provider": inferred_provider,
            "filename": md_file.name,
        })

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="List finding .md files in Output/Findings/")
    parser.add_argument("--json", action="store_true", help="Output as JSON array")
    parser.add_argument("--provider", help="Filter by provider (azure, aws, gcp, code, repo)")
    args = parser.parse_args()

    findings = scan_findings(provider=args.provider)

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            print(f["path"])

    if not findings:
        sys.exit(0)


if __name__ == "__main__":
    main()
