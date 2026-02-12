#!/usr/bin/env python3
"""Consolidated workspace scan (stdout-only; no writes).

Purpose
- One command to scan:
  - Knowledge/ refinement questions (## Unknowns / ## ❓ Open Questions)
  - Findings/ presence
  - One or more Intake/ (or other) folders for triageable files

This is a lightweight wrapper around the existing helper scripts:
- scan_knowledge_refinement.py
- scan_findings_files.py
- scan_intake_files.py

Usage:
  python3 Skills/scan_workspace.py
  python3 Skills/scan_workspace.py --intake Intake/Cloud

Exit codes:
  0 = success (even if folders are missing/empty)
"""

from __future__ import annotations

import argparse
from pathlib import Path

# When run as `python3 Skills/scan_workspace.py`, the Skills/ dir is on sys.path,
# so these imports resolve to sibling scripts.
import scan_findings_files as sff
import scan_intake_files as sif
import scan_knowledge_refinement as skr


ROOT = Path(__file__).resolve().parents[1]


def _print_rel(path: Path, *, absolute: bool) -> str:
    if absolute:
        return str(path)
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def scan_knowledge() -> None:
    print("== Knowledge refinement ==")

    if not skr.KNOWLEDGE_DIR.exists():
        print(f"Knowledge directory not found: {skr.KNOWLEDGE_DIR}")
        print()
        return

    files = skr._iter_markdown_files(skr.KNOWLEDGE_DIR)
    print(f"Knowledge markdown files: {len(files)}")
    for f in files:
        print(f"- {f.relative_to(ROOT)}")

    all_findings: list[skr.Finding] = []
    for f in files:
        all_findings.extend(skr.scan_file(f))

    print(f"\nOutstanding refinement sections: {len(all_findings)}")
    for item in all_findings:
        rel = item.path.relative_to(ROOT)
        print(f"\n=== {rel}:{item.line} ({item.section}) ===")
        for l in item.excerpt:
            print(l)

    print()


def scan_findings(
    path: str,
    *,
    exts: set[str],
    include_hidden: bool,
    absolute: bool,
) -> None:
    print("== Findings scan ==")

    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (ROOT / target).resolve()

    if not target.exists():
        print(f"Findings path does not exist: {target}")
        print()
        return

    files = sff.iter_matching_files(target, exts, include_hidden=include_hidden)

    print(f"Findings scan path: {target}")
    print(f"Finding files: {len(files)}")
    for f in files:
        print(_print_rel(f, absolute=absolute))

    print()


def scan_intake_paths(
    paths: list[str],
    *,
    exts: set[str],
    include_hidden: bool,
    absolute: bool,
) -> None:
    print("== Intake scan ==")

    if not paths:
        print("No intake paths provided.")
        print()
        return

    for raw in paths:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = (ROOT / target).resolve()

        if not target.exists():
            print(f"Path does not exist: {target}")
            continue

        if target.is_file():
            files = [target] if target.suffix.lower().lstrip(".") in exts else []
        else:
            files = sif.iter_matching_files(target, exts, include_hidden=include_hidden)

        print(f"\nIntake scan path: {target}")
        print(f"Intake files: {len(files)}")
        for f in files:
            print(_print_rel(f, absolute=absolute))

    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidated scan of Knowledge/, Findings/, and Intake/.")
    parser.add_argument(
        "--skip-knowledge",
        action="store_true",
        help="Skip Knowledge/ refinement scan.",
    )
    parser.add_argument(
        "--skip-findings",
        action="store_true",
        help="Skip Findings/ scan.",
    )
    parser.add_argument(
        "--skip-intake",
        action="store_true",
        help="Skip Intake/ scan.",
    )
    parser.add_argument(
        "--findings-path",
        default="Findings",
        help="Folder to scan for findings (default: Findings).",
    )
    parser.add_argument(
        "--intake",
        action="append",
        default=None,
        help="Intake folder/file to scan (repeatable). Default: scans common paths.",
    )
    parser.add_argument(
        "--findings-ext",
        action="append",
        default=None,
        help="Findings extension to include (repeatable). Default: md",
    )
    parser.add_argument(
        "--intake-ext",
        action="append",
        default=None,
        help="Intake extensions to include (repeatable). Default: txt,csv,md",
    )
    parser.add_argument(
        "--absolute",
        action="store_true",
        help="Print absolute paths (default: paths relative to repo root if possible).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files/directories (default: excluded).",
    )

    args = parser.parse_args()

    findings_exts = {e.lower().lstrip(".") for e in (args.findings_ext or ["md"])}
    intake_exts = {e.lower().lstrip(".") for e in (args.intake_ext or ["txt", "csv", "md"])}

    intake_paths = args.intake
    if intake_paths is None:
        intake_paths = [
            "Intake/Cloud",
            "Intake/Code",
            "Intake/Sample/Cloud",
            "Intake/Sample/Code",
            "Sample Findings/Cloud",
            "Sample Findings/Code",
        ]

    if not args.skip_knowledge:
        scan_knowledge()

    if not args.skip_findings:
        scan_findings(
            args.findings_path,
            exts=findings_exts,
            include_hidden=args.include_hidden,
            absolute=args.absolute,
        )

    if not args.skip_intake:
        scan_intake_paths(
            intake_paths,
            exts=intake_exts,
            include_hidden=args.include_hidden,
            absolute=args.absolute,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
