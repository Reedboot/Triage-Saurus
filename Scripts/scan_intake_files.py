#!/usr/bin/env python3
"""Helper to enumerate intake files or repos list.

Usage:
  # List intake files under a path (defaults to Intake/)
  python3 Scripts/scan_intake_files.py --intake Intake/Cloud

  # Print repos listed in Intake/ReposToScan.txt (one per line, ignore comments/empty)
  python3 Scripts/scan_intake_files.py --repos

This is a lightweight wrapper around Scripts/Scan/scan_files.py for compatibility
with existing docs and agent instructions.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def print_repos_list(repos_file: Path) -> int:
    if not repos_file.exists():
        print(f"Repos file not found: {repos_file}")
        return 2

    try:
        text = repos_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"Unable to read repos file: {e}")
        return 3

    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)

    print(f"Repos listed: {len(lines)}")
    for r in lines:
        print(r)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="List intake files or repos listed in Intake/ReposToScan.txt")
    parser.add_argument("--repos", action="store_true", help="Print repos listed in Intake/ReposToScan.txt")
    parser.add_argument("--intake", default="Intake", help="Intake folder to scan (default: Intake)")
    parser.add_argument("--ext", action="append", default=None, help="Extension filter (repeatable)")
    parser.add_argument("--absolute", action="store_true", help="Print absolute paths")
    parser.add_argument("--include-hidden", action="store_true", help="Include hidden files")

    args = parser.parse_args()

    if args.repos:
        repos_file = Path(args.intake)
        if not repos_file.is_absolute():
            repos_file = (ROOT / repos_file).resolve()
        # If a folder was passed, look for ReposToScan.txt inside it
        if repos_file.is_dir():
            repos_file = repos_file / "ReposToScan.txt"
        return print_repos_list(repos_file)

    # Otherwise delegate to the consolidated scan_files script
    cmd = [sys.executable, str(ROOT / "Scripts" / "Scan" / "scan_files.py"), "intake"]
    if args.ext:
        for e in args.ext:
            cmd.extend(["--ext", e])
    if args.absolute:
        cmd.append("--absolute")
    if args.include_hidden:
        cmd.append("--include-hidden")
    # Allow passing a custom intake path
    if args.intake and args.intake != "Intake":
        cmd.append(args.intake)

    try:
        proc = subprocess.run(cmd, check=False)
        return proc.returncode
    except Exception as e:
        print(f"Failed to run scanner: {e}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
