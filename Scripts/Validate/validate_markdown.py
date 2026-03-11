#!/usr/bin/env python3
"""Validate (and optionally fix) Markdown files in Output/.

This complements Scripts/validate_findings.py by checking Mermaid fenced blocks.

Usage:
  python3 Scripts/validate_markdown.py
  python3 Scripts/validate_markdown.py --fix
  python3 Scripts/validate_markdown.py --path Output/Findings/Repo/Repo_foo.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from markdown_validator import validate_markdown_file
from output_paths import OUTPUT_ROOT


def iter_md_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    return sorted(p for p in path.rglob("*.md") if p.is_file() and p.name != ".gitkeep")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Mermaid blocks inside Markdown")
    ap.add_argument("--fix", action="store_true", help="Auto-fix safe Mermaid issues")
    ap.add_argument("--path", action="append", default=[], help="File/folder to validate (repeatable)")
    args = ap.parse_args()

    targets = [Path(p) for p in args.path] if args.path else [OUTPUT_ROOT]

    problems = []
    for t in targets:
        tt = t
        if not tt.is_absolute():
            tt = (ROOT / tt).resolve()
        for f in iter_md_files(tt):
            problems.extend(validate_markdown_file(f, fix=args.fix))

    errs = [p for p in problems if p.level == "ERROR"]
    warns = [p for p in problems if p.level == "WARN"]

    for p in errs + warns:
        rel = p.path
        try:
            rel = p.path.relative_to(ROOT)
        except ValueError:
            pass
        line = f":{p.line}" if p.line else ""
        print(f"{p.level}: {rel}{line} - {p.message}")

    if errs:
        print(f"\nFAILED: {len(errs)} error(s), {len(warns)} warning(s)")
        return 1

    print(f"OK: {len(errs)} error(s), {len(warns)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
