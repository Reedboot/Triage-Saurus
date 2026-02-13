#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE_SAMPLE_DIR = ROOT / "Intake" / "Sample"

from output_paths import (
    OUTPUT_AUDIT_DIR,
    OUTPUT_FINDINGS_DIR,
    OUTPUT_KNOWLEDGE_DIR,
    OUTPUT_SUMMARY_DIR,
)

TARGET_GLOBS: tuple[tuple[Path, str], ...] = (
    (OUTPUT_FINDINGS_DIR / "Cloud", "*.md"),
    (OUTPUT_FINDINGS_DIR / "Code", "*.md"),
    (OUTPUT_FINDINGS_DIR / "Repo", "*.md"),
    (OUTPUT_KNOWLEDGE_DIR, "*.md"),
    (OUTPUT_SUMMARY_DIR, "*.md"),
    (OUTPUT_SUMMARY_DIR / "Code", "*.md"),
    (OUTPUT_SUMMARY_DIR / "Cloud", "*.md"),
    (OUTPUT_AUDIT_DIR, "*.md"),
)

EXPLICIT_FILES: tuple[Path, ...] = (
    OUTPUT_SUMMARY_DIR / "Risk Register.xlsx",
)


def iter_targets() -> list[Path]:
    targets: list[Path] = []

    for folder, pattern in TARGET_GLOBS:
        if folder.exists():
            targets.extend(sorted(folder.glob(pattern)))

    for file_path in EXPLICIT_FILES:
        if file_path.exists():
            targets.append(file_path)

    # Only remove sample-staged intake content; never touch user-provided Intake/ files.
    if INTAKE_SAMPLE_DIR.exists():
        targets.append(INTAKE_SAMPLE_DIR)

    # Also remove Output/ content (but keep the folders).
    if (ROOT / "Output").exists():
        for folder in (ROOT / "Output").iterdir():
            if folder.name.startswith("."):
                continue
            if folder.is_dir():
                # Remove files under Output/, but keep the folder structure.
                targets.extend(sorted(p for p in folder.rglob("*") if p.is_file()))

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
            "Clear per-session triage artifacts under Output/ (Findings/Knowledge/Summary/Audit) and sample-staged Intake/Sample/ "
            "without touching templates or user Intake content."
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
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass

    print(f"\nDeleted {deleted} path(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
