#!/usr/bin/env python3
"""Query DB-first resource relationships (ingress/egress/parent/related)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import get_resource_query_view  # noqa: E402


def _print_ingress(rows: list[dict]) -> None:
    if not rows:
        print("- None detected")
        return
    for row in rows:
        auth = row.get("auth_method") or "unknown-auth"
        proto = row.get("protocol") or "unknown-proto"
        port = row.get("port") or "-"
        via = row.get("via_component")
        via_text = f", via={via}" if via else ""
        print(
            f"- {row['from_resource']} ({row['from_type']} @ {row['from_repo']}) "
            f"[{row.get('connection_type')}, {proto}:{port}, auth={auth}{via_text}]"
        )


def _print_egress(rows: list[dict]) -> None:
    if not rows:
        print("- None detected")
        return
    for row in rows:
        auth = row.get("auth_method") or "unknown-auth"
        proto = row.get("protocol") or "unknown-proto"
        port = row.get("port") or "-"
        via = row.get("via_component")
        via_text = f", via={via}" if via else ""
        print(
            f"- {row['to_resource']} ({row['to_type']} @ {row['to_repo']}) "
            f"[{row.get('connection_type')}, {proto}:{port}, auth={auth}{via_text}]"
        )


def _print_related(rows: list[dict]) -> None:
    if not rows:
        print("- None detected")
        return
    for row in rows:
        print(
            f"- {row['direction']}: {row['resource']} ({row['resource_type']} @ {row['repo']}) "
            f"[{row.get('connection_type')}]"
        )


def _print_assumptions(rows: list[dict]) -> None:
    if not rows:
        print("- None")
        return
    for row in rows:
        print(
            f"- [{row.get('gap_type')}/{row.get('confidence')}] "
            f"{row.get('assumption_text') or row.get('context')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Query triage DB relationships for a resource.")
    parser.add_argument("--experiment", required=True, help="Experiment ID (e.g. 001)")
    parser.add_argument("--resource", required=True, help="Resource name (Terraform/IaC name)")
    parser.add_argument("--repo", help="Optional repository name filter")
    parser.add_argument(
        "--query",
        choices=["all", "parent", "ingress", "egress", "related", "assumptions"],
        default="all",
        help="Which relationship view to print",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    view = get_resource_query_view(
        experiment_id=args.experiment,
        resource_name=args.resource,
        repo_name=args.repo,
    )
    if view is None:
        print("Resource not found for the requested experiment/repo.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(view, indent=2))
        return 0

    resource = view["resource"]
    print(f"Resource: {resource['name']} ({resource['type']}) @ {resource['repo']}")

    if args.query in ("all", "parent"):
        print("\nParent")
        parent = view.get("parent")
        if parent:
            print(f"- {parent['name']} ({parent['type']})")
        else:
            print("- None")
        if args.query == "parent":
            return 0

    if args.query in ("all", "ingress"):
        print("\nIngress")
        _print_ingress(view.get("ingress", []))
        if args.query == "ingress":
            return 0

    if args.query in ("all", "egress"):
        print("\nEgress")
        _print_egress(view.get("egress", []))
        if args.query == "egress":
            return 0

    if args.query in ("all", "related"):
        print("\nRelated resources")
        _print_related(view.get("related", []))
        if args.query == "related":
            return 0

    if args.query in ("all", "assumptions"):
        print("\nPending assumptions")
        _print_assumptions(view.get("pending_assumptions", []))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
