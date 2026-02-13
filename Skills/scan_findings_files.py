#!/usr/bin/env python3
"""List finding files under Findings/ (stdout only; no writes).

Purpose
- Reliable filesystem walk (no recursive glob patterns like **)
- Determine whether there are any existing findings
- List common finding file types (default: .md)

Usage:
  python3 Skills/scan_findings_files.py
  python3 Skills/scan_findings_files.py Findings/Cloud

Output
- Prints a small summary, then one path per line (relative to repo root if possible).

Exit codes
  0 = success
  2 = Findings/ missing
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def iter_matching_files(root: Path, exts: set[str], include_hidden: bool) -> list[Path]:
    matches: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for name in filenames:
            p = Path(dirpath) / name
            if not include_hidden and _is_hidden(p.relative_to(root)):
                continue
            if p.suffix.lower().lstrip(".") in exts:
                matches.append(p)

    return sorted(matches)


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk Output/Findings and list finding files.")
    parser.add_argument(
        "path",
        nargs="?",
        default="Findings",
        help="Folder to scan (default: Output/Findings).",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Extension to include (repeatable). Default: md",
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

    repo_root = Path(__file__).resolve().parents[1]
    target = Path(args.path).expanduser()
    if not target.is_absolute():
        target = (repo_root / target).resolve()

    from output_paths import OUTPUT_FINDINGS_DIR

    findings_root = OUTPUT_FINDINGS_DIR

    # Back-compat: allow callers to use the old default arg but scan Output/.
    if args.path in ("Findings", "Findings/"):
        target = findings_root

    if args.path in ("Findings", "Findings/") and not findings_root.exists():
        print(f"Findings directory not found: {findings_root}")
        return 2

    if not target.exists():
        raise SystemExit(f"Path does not exist: {target}")

    exts = {e.lower().lstrip(".") for e in (args.ext or ["md"])}
    files = iter_matching_files(target, exts, include_hidden=args.include_hidden)

    print(f"Findings scan path: {target}")
    print(f"Finding files: {len(files)}")

    for f in files:
        if args.absolute:
            print(str(f))
            continue
        try:
            print(f.relative_to(repo_root).as_posix())
        except ValueError:
            print(str(f))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
