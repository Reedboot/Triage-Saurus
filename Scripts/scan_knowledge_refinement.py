#!/usr/bin/env python3
"""Scan Knowledge/ for outstanding refinement questions (stdout only; no writes).

Purpose:
- List all Markdown files under Knowledge/ (including top-level provider files).
- Detect non-empty sections under:
  - "## Unknowns"
  - "## ❓ Open Questions"
- Print excerpts so a human (or agent) can ask the user to resume refinement.

Why this exists:
- Some CLIs/environments lack common shell utilities (e.g., ripgrep).
- Repo instructions discourage relying on recursive glob patterns; this script uses
  filesystem walking.

Usage:
  python3 Scripts/scan_knowledge_refinement.py

Exit codes:
  0 = success (even if no questions found)
  2 = Knowledge/ missing
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from output_paths import OUTPUT_KNOWLEDGE_DIR

KNOWLEDGE_DIR = OUTPUT_KNOWLEDGE_DIR

HEADING_RE = re.compile(r"^##\s+(Unknowns|❓\s*Open\s+Questions)\s*$", re.IGNORECASE)
NEXT_SECTION_RE = re.compile(r"^#{1,2}\s+")


@dataclass(frozen=True)
class Finding:
    path: Path
    section: str
    line: int
    excerpt: list[str]


def _iter_markdown_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        for fn in filenames:
            if fn.lower().endswith(".md"):
                files.append(Path(dirpath) / fn)
    return sorted(files)


def _meaningful_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for l in lines:
        s = l.strip()
        if not s:
            continue
        if s.startswith("<!--"):
            continue
        out.append(l.rstrip("\n"))
    return out


def scan_file(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[Finding] = []

    i = 0
    while i < len(text):
        m = HEADING_RE.match(text[i].strip())
        if not m:
            i += 1
            continue

        section = m.group(1)
        start = i + 1
        j = start
        while j < len(text) and not NEXT_SECTION_RE.match(text[j]):
            j += 1

        content = _meaningful_lines(text[start:j])
        if content:
            excerpt = content[:12]
            findings.append(Finding(path=path, section=section, line=i + 1, excerpt=excerpt))

        i = j

    return findings


def main() -> int:
    if not KNOWLEDGE_DIR.exists():
        print(f"Knowledge directory not found: {KNOWLEDGE_DIR}")
        return 2

    files = _iter_markdown_files(KNOWLEDGE_DIR)
    print(f"Knowledge markdown files: {len(files)}")
    for f in files:
        print(f"- {f.relative_to(ROOT)}")

    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(scan_file(f))

    print(f"\nOutstanding refinement sections: {len(all_findings)}")
    for item in all_findings:
        rel = item.path.relative_to(ROOT)
        print(f"\n=== {rel}:{item.line} ({item.section}) ===")
        for l in item.excerpt:
            print(l)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
