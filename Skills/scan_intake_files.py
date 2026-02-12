#!/usr/bin/env python3
"""List intake-style files under a folder.

Purpose
- Reliable filesystem walk (no recursive glob patterns like **)
- Find common intake file types: .txt/.csv/.md (configurable)

Output
- Prints one path per line (relative to repo root by default).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def iter_matching_files(
    root: Path,
    exts: set[str],
    include_hidden: bool,
) -> list[Path]:
    matches: list[Path] = []

    # os.walk is robust across WSL/Windows mounts.
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_hidden:
            # Prune hidden dirs early.
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for name in filenames:
            p = Path(dirpath) / name
            if not include_hidden and _is_hidden(p.relative_to(root)):
                continue
            if p.suffix.lower().lstrip(".") in exts:
                matches.append(p)

    return sorted(matches)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk a folder and list .txt/.csv/.md (or custom) files.",
    )
    parser.add_argument(
        "path",
        help="Folder to scan (or a single file).",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Extension to include (repeatable). Default: txt,csv,md",
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

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        raise SystemExit(f"Path does not exist: {target}")

    exts = {e.lower().lstrip(".") for e in (args.ext or ["txt", "csv", "md"])}

    if target.is_file():
        files = [target] if target.suffix.lower().lstrip(".") in exts else []
    else:
        files = iter_matching_files(target, exts, include_hidden=args.include_hidden)

    repo_root = Path(__file__).resolve().parents[1]

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
