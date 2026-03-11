#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Snapshot:
    mtimes_ns: dict[Path, int]


def iter_markdown_files(findings_dir: Path) -> list[Path]:
    if findings_dir.is_file() and findings_dir.suffix.lower() == ".md":
        return [findings_dir]
    if not findings_dir.exists():
        return []
    return sorted([p for p in findings_dir.rglob("*.md") if p.is_file()])


def take_snapshot(findings_dir: Path) -> Snapshot:
    mtimes: dict[Path, int] = {}
    for p in iter_markdown_files(findings_dir):
        try:
            mtimes[p] = p.stat().st_mtime_ns
        except OSError:
            # File may disappear between listing and stat; treat as change.
            continue
    return Snapshot(mtimes_ns=mtimes)


def diff_snapshots(prev: Snapshot, cur: Snapshot) -> list[str]:
    changes: list[str] = []
    prev_paths = set(prev.mtimes_ns.keys())
    cur_paths = set(cur.mtimes_ns.keys())

    for p in sorted(cur_paths - prev_paths):
        changes.append(f"added: {p}")
    for p in sorted(prev_paths - cur_paths):
        changes.append(f"removed: {p}")
    for p in sorted(prev_paths & cur_paths):
        if prev.mtimes_ns.get(p) != cur.mtimes_ns.get(p):
            changes.append(f"modified: {p}")
    return changes


def run_script(script: Path, args: list[str]) -> None:
    subprocess.run([sys.executable, str(script), *args], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch Output/Findings and regenerate the risk register on changes.")
    ap.add_argument(
        "--findings-dir",
        default="Output/Findings",
        help="Folder to watch (default: Output/Findings)",
    )
    ap.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds (default: 1.0)")
    ap.add_argument("--debounce", type=float, default=0.75, help="Debounce delay before regenerating (default: 0.75)")
    ap.add_argument("--once", action="store_true", help="Run a single regen and exit")
    ap.add_argument(
        "--full",
        action="store_true",
        help="Also run summary/description/score refresh helpers before regen.",
    )
    args = ap.parse_args()

    findings_dir = (ROOT / args.findings_dir).resolve() if not Path(args.findings_dir).is_absolute() else Path(args.findings_dir)
    risk_register = ROOT / "Skills" / "risk_register.py"
    update_summaries = ROOT / "Skills" / "update_validated_summaries.py"
    update_descriptions = ROOT / "Skills" / "update_descriptions.py"
    adjust_scores = ROOT / "Skills" / "adjust_finding_scores.py"

    if args.once:
        if args.full:
            run_script(update_summaries, ["--path", "Output/Findings/Cloud", "--in-place"])
            run_script(update_descriptions, ["--path", "Output/Findings/Cloud", "--refresh-auto", "--in-place"])
            run_script(adjust_scores, ["--path", "Output/Findings/Cloud", "--in-place"])
        run_script(risk_register, [])
        return 0

    print(f"Watching: {findings_dir}")
    print("Regenerates: Output/Summary/Risk Register.xlsx")
    print("Stop: Ctrl+C")

    prev = take_snapshot(findings_dir)
    while True:
        time.sleep(max(0.1, args.interval))
        cur = take_snapshot(findings_dir)
        changes = diff_snapshots(prev, cur)
        if not changes:
            continue

        # Debounce: wait for filesystem to settle.
        time.sleep(max(0.0, args.debounce))
        cur2 = take_snapshot(findings_dir)
        changes2 = diff_snapshots(prev, cur2)
        if not changes2:
            prev = cur2
            continue

        print(f"Detected {len(changes2)} change(s). Regenerating risk register...")
        try:
            if args.full:
                run_script(update_summaries, ["--path", "Output/Findings/Cloud", "--in-place"])
                run_script(update_descriptions, ["--path", "Output/Findings/Cloud", "--refresh-auto", "--in-place"])
                run_script(adjust_scores, ["--path", "Output/Findings/Cloud", "--in-place"])
            run_script(risk_register, [])
        except subprocess.CalledProcessError as e:
            print(f"ERROR: regen failed: {e}", file=sys.stderr)
        prev = take_snapshot(findings_dir)


if __name__ == "__main__":
    raise SystemExit(main())

