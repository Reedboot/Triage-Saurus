#!/usr/bin/env python3
"""Compare intake titles to existing findings titles (stdout-only; no writes).

Purpose
- Support idempotent bulk processing across multiple days.
- Detect intake items that already exist as findings (by normalised title key).

Inputs
- --intake: file or folder containing title-only inputs (.txt/.csv/.md)
- --findings: folder containing findings Markdown (.md). Defaults to Findings/Cloud.

Output
- Summary counts, then lists of duplicates and new items.

Exit codes
- 0 = success
- 2 = intake path missing
- 3 = findings path missing
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

from output_paths import OUTPUT_FINDINGS_DIR


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _normalise_title(line: str) -> str:
    # Accept both raw lines and Markdown headings.
    return line.strip().lstrip("\ufeff").lstrip("# ").strip()


def _dedupe_key(title: str) -> str:
    # Keep consistent with generate_findings_from_titles.py
    s = _normalise_title(title).lower()
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    s = re.sub(r"(/etc/(?:shadow|gshadow|passwd|group))\-(?=\s)", r"\1", s)
    return s


def _titles_from_text_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    titles: list[str] = []

    if path.suffix.lower() in {".txt", ".csv"}:
        for l in text.splitlines():
            t = _normalise_title(l)
            if t:
                titles.append(t)
        return titles

    # .md and other: first non-empty line = title
    for l in text.splitlines():
        t = _normalise_title(l)
        if t:
            return [t]
    return []


def _iter_files(root: Path, *, exts: set[str], include_hidden: bool) -> list[Path]:
    matches: list[Path] = []
    if root.is_file():
        if root.suffix.lower().lstrip(".") in exts:
            return [root]
        return []

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


def _extract_intake_titles(intake: Path, *, include_hidden: bool) -> list[str]:
    exts = {"txt", "csv", "md"}
    paths = _iter_files(intake, exts=exts, include_hidden=include_hidden)
    titles: list[str] = []
    for p in paths:
        if p.name.startswith("."):
            continue
        if p.name in {"README.md", ".gitignore", ".gitkeep"}:
            continue
        titles.extend(_titles_from_text_file(p))
    return titles


def _extract_finding_titles(findings: Path, *, include_hidden: bool) -> list[str]:
    exts = {"md"}
    paths = _iter_files(findings, exts=exts, include_hidden=include_hidden)
    titles: list[str] = []

    for p in paths:
        try:
            first = p.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        except OSError:
            continue
        if not first:
            continue

        # First line is typically: "# 🟣 <title>".
        line = _normalise_title(first[0])
        if line.startswith("🟣"):
            line = line.lstrip("🟣 ").strip()
        if line:
            titles.append(line)

    return titles


def _print_rel(path: Path, *, absolute: bool) -> str:
    if absolute:
        return str(path)
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare intake titles to existing findings by normalised title.")
    parser.add_argument("--intake", required=True, help="Intake file or folder (e.g., Intake/Cloud)")
    parser.add_argument(
        "--findings",
        default=str(OUTPUT_FINDINGS_DIR / "Cloud"),
        help="Findings folder to compare against (default: Output/Findings/Cloud)",
    )
    parser.add_argument("--absolute", action="store_true", help="Print absolute paths")
    parser.add_argument("--include-hidden", action="store_true", help="Include hidden files/dirs")

    args = parser.parse_args()

    intake = Path(args.intake).expanduser()
    if not intake.is_absolute():
        intake = (ROOT / intake).resolve()
    if not intake.exists():
        print(f"Intake path not found: {intake}")
        return 2

    findings = Path(args.findings).expanduser()
    if not findings.is_absolute():
        findings = (ROOT / findings).resolve()
    if not findings.exists():
        print(f"Findings path not found: {findings}")
        return 3

    intake_titles = _extract_intake_titles(intake, include_hidden=args.include_hidden)
    finding_titles = _extract_finding_titles(findings, include_hidden=args.include_hidden)

    finding_keys = {_dedupe_key(t) for t in finding_titles}

    dupes: list[str] = []
    new: list[str] = []

    seen_intake: set[str] = set()
    for t in intake_titles:
        k = _dedupe_key(t)
        if k in seen_intake:
            continue
        seen_intake.add(k)

        if k in finding_keys:
            dupes.append(t)
        else:
            new.append(t)

    print("== Intake vs Findings (title dedupe) ==")
    print(f"Intake path:   {_print_rel(intake, absolute=args.absolute)}")
    print(f"Findings path: {_print_rel(findings, absolute=args.absolute)}")
    print(f"Intake titles (unique): {len(seen_intake)}")
    print(f"Already processed:      {len(dupes)}")
    print(f"New to process:         {len(new)}")

    if dupes:
        print("\n== Already processed (duplicates) ==")
        for t in dupes:
            print(f"- {t}")

    if new:
        print("\n== New items ==")
        for t in new:
            print(f"- {t}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
