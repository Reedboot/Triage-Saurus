#!/usr/bin/env python3
"""
Infer what resources a repo creates through its module invocations.

Reads cozo.db module_registry to resolve each external module reference found
in the repo's .tf files, then optionally persists the inferred resources into
the main resources table so diagrams and findings see them.

Usage:
  # Print summary only
  python3 Scripts/Context/infer_module_resources.py <repo-path>

  # Persist inferred resources into cozo.db for a specific scan
  python3 Scripts/Context/infer_module_resources.py <repo-path> \\
      --experiment-id <id> --repo-id <id> [--db <path>]
"""

import re
import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from module_registry import lookup_module, record_module_usage

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
try:
    from db_helpers import DB_PATH as DEFAULT_DB
except ImportError:
    DEFAULT_DB = REPO_ROOT / "Output" / "Data" / "cozo.db"


def extract_module_invocations(repo_path: str) -> List[Dict]:
    """Return all external module invocations from .tf files in the repo."""
    invocations = []
    root = Path(repo_path)

    for tf_file in root.glob("**/*.tf"):
        if ".terraform" in tf_file.parts:
            continue

        try:
            content = tf_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            module_re = re.compile(r'module\s+"([^"]+)"\s*\{')
            source_re = re.compile(r'source\s*=\s*["\']([^"\']+)["\']')

            i = 0
            while i < len(lines):
                m = module_re.search(lines[i])
                if m:
                    instance_name = m.group(1)
                    source = None
                    for j in range(i, min(i + 10, len(lines))):
                        s = source_re.search(lines[j])
                        if s:
                            source = s.group(1)
                            break

                    # Only record remote (non-local) sources
                    if source and not source.startswith(("./", "../", "/")):
                        invocations.append({
                            "instance_name": instance_name,
                            "source": source,
                            "source_file": str(tf_file.relative_to(root)),
                            "source_line": i + 1,
                        })
                i += 1

        except Exception as e:
            print(f"Warning: Could not parse {tf_file}: {e}", file=sys.stderr)

    return invocations


def infer_resources_from_modules(
    repo_path: str,
    db_path: Optional[str] = None,
    experiment_id: Optional[str] = None,
    repo_id: Optional[int] = None,
) -> Dict[str, Dict]:
    """Infer resources a repo creates through external module usage.

    If experiment_id and repo_id are supplied, also persists the inferred
    resources into cozo.db (resources table + module_usage table).

    Returns dict: {module_instance_name -> {resource_types, source, ...}}
    """
    inferred: Dict[str, Dict] = {}
    db = db_path or str(DEFAULT_DB)

    for inv in extract_module_invocations(repo_path):
        metadata = lookup_module(inv["source"], db_path=db)
        if not metadata:
            continue

        inferred[inv["instance_name"]] = {
            "resource_types": metadata.resource_types,
            "source": inv["source"],
            "module_name": metadata.module_name,
            "source_file": inv["source_file"],
            "source_line": inv["source_line"],
            "count": len(metadata.resource_types),
        }

        # Persist into cozo.db when running inside a scan
        if experiment_id and repo_id is not None:
            record_module_usage(
                experiment_id=experiment_id,
                repo_id=repo_id,
                module_instance_name=inv["instance_name"],
                module_source=inv["source"],
                source_file=inv["source_file"],
                source_line=inv["source_line"],
                resolved_resource_types=metadata.resource_types,
                db_path=db,
            )

    return inferred


def generate_summary(inferred: Dict) -> str:
    if not inferred:
        return "No external modules found in registry."

    lines = ["📦 Resources inferred from modules:\n"]
    for name, info in sorted(inferred.items()):
        lines.append(f"  {name}  ({info['module_name']})")
        lines.append(f"    Source : {info['source']}")
        lines.append(f"    File   : {info['source_file']}:{info['source_line']}")
        lines.append(f"    Creates: {info['count']} resource type(s)")
        for rtype in info["resource_types"][:5]:
            lines.append(f"      • {rtype}")
        if info["count"] > 5:
            lines.append(f"      … and {info['count'] - 5} more")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Infer module resources for a repo")
    parser.add_argument("repo_path", help="Path to the repo")
    parser.add_argument("--db", default=None, help="Override database path")
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--repo-id",       default=None, type=int)
    args = parser.parse_args()

    result = infer_resources_from_modules(
        args.repo_path,
        db_path=args.db,
        experiment_id=args.experiment_id,
        repo_id=args.repo_id,
    )
    print(generate_summary(result))
