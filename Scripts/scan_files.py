#!/usr/bin/env python3
"""Universal file scanner - consolidates scan_findings_files.py, scan_intake_files.py, and scan_knowledge_refinement.py.

Purpose:
- Single script for scanning different directories
- Finds files by extension or detects refinement questions in Knowledge/
- Reduces code duplication across 4 separate scanning scripts

Usage:
  # Scan findings
  python3 Scripts/scan_files.py findings

  # Scan intake with custom extensions
  python3 Scripts/scan_files.py intake --ext txt --ext csv

  # Scan knowledge for refinement questions
  python3 Scripts/scan_files.py knowledge --mode refinement

  # Scan custom path
  python3 Scripts/scan_files.py /path/to/folder --ext md

Exit codes:
  0 = success
  2 = target directory not found
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path


# -----------------------------------------------------------------------------
# File walking
# -----------------------------------------------------------------------------

def _is_hidden(path: Path, root: Path) -> bool:
    """Check if path or any parent (relative to root) is hidden."""
    try:
        rel = path.relative_to(root)
        return any(part.startswith(".") for part in rel.parts)
    except ValueError:
        return False


def iter_files(root: Path, exts: set[str], include_hidden: bool) -> list[Path]:
    """Walk directory tree and collect files matching extensions."""
    matches: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for name in filenames:
            p = Path(dirpath) / name
            if not include_hidden and _is_hidden(p, root):
                continue
            if not exts or p.suffix.lower().lstrip(".") in exts:
                matches.append(p)

    return sorted(matches)


# -----------------------------------------------------------------------------
# Knowledge refinement detection
# -----------------------------------------------------------------------------

HEADING_RE = re.compile(r"^##\s+(Unknowns|❓\s*Open\s+Questions)\s*$", re.IGNORECASE)
NEXT_SECTION_RE = re.compile(r"^#{1,2}\s+")


@dataclass(frozen=True)
class RefinementFinding:
    path: Path
    section: str
    line: int
    excerpt: list[str]


def _meaningful_lines(lines: list[str]) -> list[str]:
    """Filter out empty lines and comments."""
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        out.append(line.rstrip("\n"))
    return out


def scan_knowledge_file(path: Path) -> list[RefinementFinding]:
    """Scan markdown file for refinement sections (Unknowns, Open Questions)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    findings: list[RefinementFinding] = []
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
            findings.append(RefinementFinding(
                path=path,
                section=section,
                line=i + 1,
                excerpt=content[:12],
            ))
        i = j

    return findings


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Universal file scanner for findings/intake/knowledge directories.",
    )
    parser.add_argument(
        "target",
        help="Preset (findings/intake/knowledge) or custom path to scan.",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Extension to include (repeatable). Defaults depend on target.",
    )
    parser.add_argument(
        "--mode",
        choices=["list", "refinement"],
        default="list",
        help="Output mode: list files (default) or find refinement questions (knowledge only).",
    )
    parser.add_argument(
        "--absolute",
        action="store_true",
        help="Print absolute paths (default: relative to repo root).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files/directories.",
    )

    args = parser.parse_args()

    # Resolve repo root
    repo_root = Path(__file__).resolve().parents[1]

    # Resolve target path
    from output_paths import OUTPUT_FINDINGS_DIR, OUTPUT_KNOWLEDGE_DIR

    presets = {
        "findings": OUTPUT_FINDINGS_DIR,
        "intake": repo_root / "Intake",
        "knowledge": OUTPUT_KNOWLEDGE_DIR,
    }

    if args.target.lower() in presets:
        target = presets[args.target.lower()]
    else:
        target = Path(args.target).expanduser()
        if not target.is_absolute():
            target = (repo_root / target).resolve()

    if not target.exists():
        print(f"Error: Target not found: {target}", flush=True)
        return 2

    # Determine extensions
    default_exts = {
        "findings": {"md"},
        "intake": {"txt", "csv", "md"},
        "knowledge": {"md"},
    }
    
    if args.ext:
        exts = {e.lower().lstrip(".") for e in args.ext}
    else:
        preset_name = args.target.lower() if args.target.lower() in presets else "findings"
        exts = default_exts.get(preset_name, {"md"})

    # Handle refinement mode (knowledge only)
    if args.mode == "refinement":
        if args.target.lower() != "knowledge":
            print("Warning: Refinement mode only works with 'knowledge' target", flush=True)
        
        files = iter_files(target, {"md"}, args.include_hidden)
        print(f"Knowledge markdown files: {len(files)}", flush=True)
        
        all_findings: list[RefinementFinding] = []
        for f in files:
            all_findings.extend(scan_knowledge_file(f))
        
        print(f"\nOutstanding refinement sections: {len(all_findings)}", flush=True)
        for item in all_findings:
            try:
                rel = item.path.relative_to(repo_root)
            except ValueError:
                rel = item.path
            print(f"\n=== {rel}:{item.line} ({item.section}) ===", flush=True)
            for line in item.excerpt:
                print(line, flush=True)
        
        return 0

    # List mode (default)
    files = iter_files(target, exts, args.include_hidden)
    
    print(f"Scan path: {target}", flush=True)
    print(f"Files found: {len(files)}", flush=True)
    print("", flush=True)

    for f in files:
        if args.absolute:
            print(str(f), flush=True)
        else:
            try:
                print(f.relative_to(repo_root).as_posix(), flush=True)
            except ValueError:
                print(str(f), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
