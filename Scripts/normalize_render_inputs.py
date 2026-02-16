#!/usr/bin/env python3
"""Normalize JSON render inputs to avoid boilerplate phrases.

This is a mechanical helper: it does not invent evidence or change scores.
It should not be used to mark findings "validated". Use explicit
`meta.validation_status` for draft/validated status.

Usage:
  python3 Scripts/normalize_render_inputs.py --path Output/Audit/RenderInputs --in-place
  python3 Scripts/normalize_render_inputs.py --path Output/Audit/RenderInputs --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# Older pipelines used phrase matching to infer draft status.
# We keep these here only to reduce boilerplate repetition, not to change status.
BOILERPLATE_PHRASES = [
    "draft finding generated from a title-only input",
    "this is a draft finding",
    "validate the affected resources/scope",
    "title-only input; needs validation",
]


def _clean_text(s: str) -> str:
    out = s
    for ind in BOILERPLATE_PHRASES:
        out = re.sub(re.escape(ind), "sample input only; requires confirmation in the target environment", out, flags=re.IGNORECASE)

    # Remove generator-only placeholders that shouldn't persist.
    out = out.replace("<add evidence here, e.g., resource IDs, query output, screenshots, or IaC paths>", "TODO: add evidence (resource IDs / queries / IaC paths).")
    out = out.replace("<fill in>", "TODO")
    out = out.replace("<note>", "TODO")
    return out


def _walk(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    if isinstance(obj, str):
        return _clean_text(obj)
    return obj


def iter_json_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def main() -> int:
    p = argparse.ArgumentParser(description="Normalize JSON render inputs (remove draft marker boilerplate).")
    p.add_argument("--path", default="Output/Audit/RenderInputs", help="Folder containing render input JSON files")
    p.add_argument("--in-place", action="store_true", help="Write changes back to disk")
    p.add_argument("--dry-run", action="store_true", help="Report changes only")
    args = p.parse_args()

    target = Path(args.path).expanduser()
    if not target.is_absolute():
        target = (ROOT / target).resolve()

    files = iter_json_files(target)
    if not files:
        print(f"No JSON files found under {target}")
        return 0

    changed = 0
    for fp in files:
        before = fp.read_text(encoding="utf-8", errors="replace")
        model = json.loads(before)
        model2 = _walk(model)
        after = json.dumps(model2, indent=2, sort_keys=True) + "\n"
        if after != before:
            changed += 1
            print(f"CHANGED: {fp.relative_to(ROOT)}")
            if args.in_place:
                fp.write_text(after, encoding="utf-8")

    if args.dry_run and args.in_place:
        raise SystemExit("Use either --dry-run or --in-place (not both).")

    print(f"Files changed: {changed}/{len(files)}")
    if changed and not args.in_place:
        print("Dry-run only. Re-run with --in-place to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
