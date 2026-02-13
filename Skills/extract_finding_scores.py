#!/usr/bin/env python3
"""Extract finding titles, overall scores, and descriptions.

Usage:
  python3 Skills/extract_finding_scores.py [Findings/Cloud]

Outputs a Markdown table to stdout.
"""

from __future__ import annotations

import glob
import os
import sys
from typing import Optional


def _first_matching(lines: list[str], prefix: str) -> Optional[str]:
    for line in lines:
        if line.startswith(prefix):
            return line
    return None


def main() -> int:
    from output_paths import OUTPUT_FINDINGS_DIR

    findings_dir = sys.argv[1] if len(sys.argv) > 1 else str(OUTPUT_FINDINGS_DIR / "Cloud")
    pattern = os.path.join(findings_dir, "*.md")

    rows: list[tuple[str, str, str, str]] = []
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f]

        title = lines[0].lstrip("# ").strip() if lines else os.path.basename(path)
        overall = _first_matching(lines, "- **Overall Score:**") or "- **Overall Score:** (missing)"
        desc = _first_matching(lines, "- **Description:**") or "- **Description:** (missing)"

        overall_val = overall.split("**Overall Score:**", 1)[-1].strip()
        desc_val = desc.split("**Description:**", 1)[-1].strip()

        rel = path.replace("\\", "/")
        rows.append((rel, title, overall_val, desc_val))

    print("| Finding | Overall Score | Risk / Description |")
    print("|---|---:|---|")
    for rel, title, overall_val, desc_val in rows:
        # keep table readable
        desc_val = desc_val.replace("|", "\\|")
        print(f"| [{title}]({rel}) | {overall_val} | {desc_val} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
