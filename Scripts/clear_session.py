#!/usr/bin/env python3
"""
Delete per-session artifacts under Output/Findings/, Output/Knowledge/, and Output/Summary/.

Resets the working session state so a fresh triage can begin without stale outputs.

Usage:
    python3 Scripts/clear_session.py [--dry-run] [--provider azure|aws|gcp|code|repo]

Options:
    --dry-run       List files that would be deleted without deleting them.
    --provider      Only clear outputs for a specific provider subdirectory.
    --yes           Skip confirmation prompt.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SESSION_DIRS = [
    ROOT / "Output" / "Findings",
    ROOT / "Output" / "Knowledge",
    ROOT / "Output" / "Summary",
]


def collect_targets(provider: str | None) -> list[Path]:
    """Collect files/dirs to delete."""
    targets: list[Path] = []
    for base in SESSION_DIRS:
        if not base.exists():
            continue
        if provider:
            candidate = base / provider.capitalize()
            if candidate.exists():
                targets.append(candidate)
        else:
            for child in sorted(base.iterdir()):
                if child.name != ".gitkeep":
                    targets.append(child)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear per-session output artifacts")
    parser.add_argument("--dry-run", action="store_true", help="List targets without deleting")
    parser.add_argument("--provider", help="Limit to a specific provider subdirectory")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    targets = collect_targets(args.provider)

    if not targets:
        print("No session artifacts found.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Targets to remove:")
    for t in targets:
        label = "DIR " if t.is_dir() else "FILE"
        print(f"  [{label}] {t.relative_to(ROOT)}")

    if args.dry_run:
        return

    if not args.yes:
        confirm = input(f"\nDelete {len(targets)} item(s)? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    for t in targets:
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()

    print(f"✓ Cleared {len(targets)} item(s).")


if __name__ == "__main__":
    main()
