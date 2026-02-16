#!/usr/bin/env python3
"""Stage repo sample findings into Intake/ for processing.

This script copies from:
- Sample Findings/Cloud -> Intake/Sample/Cloud
- Sample Findings/Code  -> Intake/Sample/Code

It never deletes user-provided Intake content; it only overwrites the specific
Intake/Sample/<Type> destination.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stage(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        raise SystemExit(f"Source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main() -> int:
    p = argparse.ArgumentParser(description="Copy repo sample findings into Intake/")
    p.add_argument("--type", choices=["cloud", "code", "all"], default="all")
    args = p.parse_args()

    if args.type in {"cloud", "all"}:
        stage(ROOT / "Sample Findings" / "Cloud", ROOT / "Intake" / "Sample" / "Cloud")
        print("Staged: Intake/Sample/Cloud")

    if args.type in {"code", "all"}:
        stage(ROOT / "Sample Findings" / "Code", ROOT / "Intake" / "Sample" / "Code")
        print("Staged: Intake/Sample/Code")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
