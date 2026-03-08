#!/usr/bin/env python3
"""List and resolve enrichment assumptions with auditable context answers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from db_helpers import list_enrichment_assumptions, resolve_enrichment_assumption  # noqa: E402


def _print_assumption_rows(rows: list[dict[str, Any]], *, experiment: str, repo: str | None, status: str) -> None:
    repo_scope = repo or "all repos"
    print(
        f"Enrichment assumptions for experiment '{experiment}' "
        f"(repo scope: {repo_scope}, status: {status}): {len(rows)}"
    )
    if not rows:
        print("- None")
        return

    for row in rows:
        assumption_text = row.get("assumption_text") or row.get("context") or "(missing assumption text)"
        print(f"- #{row['id']} [{row.get('gap_type')}/{row.get('confidence')}] {assumption_text}")
        print(f"  status={row.get('status')} question_key={row.get('question_key')}")
        suggested = row.get("suggested_value")
        if suggested:
            print(f"  suggested={suggested}")
        relationship_summary = (row.get("relationship") or {}).get("summary")
        if relationship_summary:
            print(f"  relationship={relationship_summary}")
        print(f"  repo_scope={', '.join(row.get('repo_scope') or [])}")


def _print_resolution_result(result: dict[str, Any]) -> None:
    print(
        f"Resolved assumption #{result['assumption_id']} as {result['status']} "
        f"by {result['resolved_by']}"
    )
    print(f"- context_answer_id: {result['context_answer_id']}")
    print(f"- question_key: {result['question_key']}")
    note = result.get("resolution_note")
    if note:
        print(f"- note: {note}")
    updates = result.get("confidence_updates") or []
    if updates:
        print("- confidence_updates:")
        for update in updates:
            print(f"  - {update}")
    else:
        print("- confidence_updates: none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage user confirmation workflow for enrichment assumptions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List enrichment assumptions")
    list_parser.add_argument("--experiment", required=True, help="Experiment ID")
    list_parser.add_argument("--repo", help="Optional repo name to narrow scope")
    list_parser.add_argument(
        "--status",
        default="pending_review",
        choices=["pending_review", "confirmed", "rejected", "all"],
        help="Filter by queue status",
    )
    list_parser.add_argument("--json", action="store_true", help="Output JSON")

    resolve_parser = subparsers.add_parser("resolve", help="Confirm or reject an enrichment assumption")
    resolve_parser.add_argument("--experiment", required=True, help="Experiment ID")
    resolve_parser.add_argument("--assumption-id", required=True, type=int, help="enrichment_queue.id value")
    resolve_parser.add_argument(
        "--decision",
        required=True,
        choices=["confirm", "confirmed", "reject", "rejected"],
        help="Resolution decision",
    )
    resolve_parser.add_argument("--resolver", required=True, help="Resolver identity (e.g. analyst email)")
    resolve_parser.add_argument("--repo", help="Optional repo scope guard")
    resolve_parser.add_argument(
        "--note",
        help="Decision rationale (required for reject/rejected).",
    )
    resolve_parser.add_argument(
        "--evidence-source",
        default="user_confirmation_cli",
        help="Audit source label for context_answers.evidence_source",
    )
    resolve_parser.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "list":
            rows = list_enrichment_assumptions(
                experiment_id=args.experiment,
                repo_name=args.repo,
                status=args.status,
            )
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                _print_assumption_rows(
                    rows,
                    experiment=args.experiment,
                    repo=args.repo,
                    status=args.status,
                )
            return 0

        if args.command == "resolve":
            if args.decision in {"reject", "rejected"} and not args.note:
                parser.error("--note is required when decision is reject/rejected.")
            result = resolve_enrichment_assumption(
                experiment_id=args.experiment,
                assumption_id=args.assumption_id,
                decision=args.decision,
                resolved_by=args.resolver,
                repo_name=args.repo,
                resolution_note=args.note,
                evidence_source=args.evidence_source,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_resolution_result(result)
            return 0

        parser.error(f"Unsupported command: {args.command}")
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
