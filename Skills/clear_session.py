#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGET_GLOBS: tuple[tuple[Path, str], ...] = (
    (ROOT / "Findings" / "Cloud", "*.md"),
    (ROOT / "Findings" / "Code", "*.md"),
    (ROOT / "Knowledge", "*.md"),
    (ROOT / "Summary", "*.md"),
    (ROOT / "Summary" / "Code", "*.md"),
    (ROOT / "Summary" / "Cloud", "*.md"),
)

EXPLICIT_FILES: tuple[Path, ...] = (
    ROOT / "Summary" / "Risk Register.xlsx",
)


def iter_targets() -> list[Path]:
    targets: list[Path] = []

    for folder, pattern in TARGET_GLOBS:
        if folder.exists():
            targets.extend(sorted(folder.glob(pattern)))

    for file_path in EXPLICIT_FILES:
        if file_path.exists():
            targets.append(file_path)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in targets:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clear per-session triage artifacts (Findings/Knowledge/Summary) without touching templates or .gitkeep files."
        )
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete files. Without this flag, performs a dry-run.",
    )
    args = parser.parse_args()

    targets = iter_targets()
    if not targets:
        print("No session artifacts found to delete.")
        return 0

    print("Targets:")
    for path in targets:
        print(f"- {path.relative_to(ROOT)}")

    if not args.yes:
        print("\nDry-run only. Re-run with --yes to delete.")
        return 0

    deleted = 0
    for path in targets:
        try:
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass

    print(f"\nDeleted {deleted} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
