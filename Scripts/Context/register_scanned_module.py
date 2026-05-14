#!/usr/bin/env python3
"""
Register a scanned module in the cozo.db module registry.

Called automatically by the scan pipeline after each external module is scanned,
so subsequent repo scans can infer the resources that module creates.

Usage:
  python3 Scripts/Context/register_scanned_module.py <module-path> <module-source-url>

  --db   Override the database path (default: Output/Data/cozo.db)

Example:
  python3 Scripts/Context/register_scanned_module.py \\
    /mnt/c/Repos/terraform-aks \\
    "git::https://example.com/org/terraform-aks//modules/azure"
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from module_registry import analyze_module, register_module, capture_module_findings


def main():
    parser = argparse.ArgumentParser(description="Register a scanned module in cozo.db")
    parser.add_argument("module_path",   help="Path to the module repository on disk")
    parser.add_argument("module_source", help='Module source URL (e.g. "git::https://...")')
    parser.add_argument("--db",          default=None,
                        help="Override database path (default: Output/Data/cozo.db)")
    parser.add_argument("--experiment-id", default=None,
                        help="Experiment ID from the module scan — used to capture findings")
    args = parser.parse_args()

    print(f"🔍 Analyzing module: {args.module_path}")
    metadata = analyze_module(args.module_path)
    metadata.module_source = args.module_source

    register_module(metadata, db_path=args.db)

    if args.experiment_id:
        capture_module_findings(
            module_source=args.module_source,
            module_experiment_id=args.experiment_id,
            db_path=args.db,
        )


if __name__ == "__main__":
    main()
