#!/usr/bin/env python3
"""Regenerate derived outputs from existing findings.

This script intentionally does NOT create/modify findings; it only refreshes
outputs under Summary/.

Usage:
  python3 Skills/regen_all.py --provider azure

Outputs:
- Summary/Cloud/*.md (per-service summaries)
- Summary/Risk Register.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _infer_provider(root: Path) -> str | None:
    # Best-effort inference; prefer explicit --provider in automation.
    for p in ("azure", "aws", "gcp"):
        if (root / "Knowledge" / f"{p.title()}.md").exists():
            return p
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(description="Regenerate Summary outputs from Findings")
    parser.add_argument("--provider", choices=["azure", "aws", "gcp"], help="Cloud provider")
    parser.add_argument(
        "--no-cloud-summaries",
        action="store_true",
        help="Skip regenerating Summary/Cloud/*.md",
    )
    parser.add_argument(
        "--no-risk-register",
        action="store_true",
        help="Skip regenerating Summary/Risk Register.xlsx",
    )
    args = parser.parse_args()

    provider = args.provider or _infer_provider(root)
    if not provider:
        raise SystemExit("Unable to infer provider; pass --provider azure|aws|gcp")

    ts: str
    try:
        from Skills.generate_findings_from_titles import now_uk, update_service_summaries
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"Unable to load summary generator: {e}")

    ts = now_uk()

    wrote = 0

    if not args.no_cloud_summaries:
        summary_paths = update_service_summaries(provider, ts)
        wrote += len(summary_paths)
        for p in summary_paths:
            print(f"Wrote: {p.relative_to(root)}")

    if not args.no_risk_register:
        try:
            from Skills.risk_register import main as rr_main
        except Exception as e:  # pragma: no cover
            raise SystemExit(f"Unable to load risk register generator: {e}")

        rc = rr_main()
        if rc != 0:
            return rc
        wrote += 1

    if wrote == 0:
        print("Nothing regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
