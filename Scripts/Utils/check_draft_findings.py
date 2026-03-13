#!/usr/bin/env python3
"""
Check for draft findings that need triage.

This script scans all findings and identifies which ones are still drafts
(generic boilerplate without validation). Use this during session kickoff
to prompt the user to complete draft findings.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from output_paths import OUTPUT_FINDINGS_DIR
from shared_utils import is_draft_finding as _is_draft_shared


def is_draft_finding(file_path: Path) -> bool:
    """Check if a finding is a draft."""
    try:
        return _is_draft_shared(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return False


def main() -> int:
    """Main entry point."""
    if not OUTPUT_FINDINGS_DIR.exists():
        print("No findings directory found.")
        return 1
    
    # Scan all finding files
    draft_findings = []
    validated_findings = []
    
    for finding_file in OUTPUT_FINDINGS_DIR.rglob("*.md"):
        if is_draft_finding(finding_file):
            draft_findings.append(finding_file)
        else:
            validated_findings.append(finding_file)
    
    total = len(draft_findings) + len(validated_findings)
    
    if total == 0:
        print("No findings found.")
        return 1
    
    draft_pct = (len(draft_findings) / total) * 100
    validated_pct = (len(validated_findings) / total) * 100
    
    print(f"📊 Finding Validation Status:\n")
    print(f"  Total findings: {total}")
    print(f"  ✅ Validated: {len(validated_findings)} ({validated_pct:.1f}%)")
    print(f"  ⚠️  Draft (needs triage): {len(draft_findings)} ({draft_pct:.1f}%)")
    
    if draft_findings:
        print(f"\n⚠️  WARNING: {len(draft_findings)} draft findings need validation!\n")
        print("These findings have generic boilerplate and need:")
        print("  • Applicability confirmation (Yes/No/Don't know)")
        print("  • Specific evidence (resource IDs, query output)")
        print("  • Environment context (production/non-prod, internet-facing)")
        print("  • Accurate risk scoring based on actual exposure\n")
        
        # Show a few examples
        print("Sample draft findings:")
        for finding in sorted(draft_findings)[:10]:
            rel_path = finding.relative_to(OUTPUT_FINDINGS_DIR)
            print(f"  • {rel_path}")
        
        if len(draft_findings) > 10:
            print(f"  ... and {len(draft_findings) - 10} more")
        
        return 2  # Exit code 2 = action needed
    else:
        print("\n✅ All findings have been validated!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
