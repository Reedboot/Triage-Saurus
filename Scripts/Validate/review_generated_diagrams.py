#!/usr/bin/env python3
"""Diagram Review Skill orchestrator.

Runs diagram validation in two passes (baseline + after) and writes a
security-architect report with before/after deltas, screenshot locations, and
rule-validation outcomes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_SCRIPT = REPO_ROOT / "Scripts" / "Validate" / "web_parallel_scan_validator.py"
DEFAULT_AUDIT_ROOT = REPO_ROOT / "Output" / "Audit"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review generated diagrams with Playwright and produce before/after report."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:9000")
    parser.add_argument("--audit-root", default=str(DEFAULT_AUDIT_ROOT))
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--scan-complete-timeout-sec", type=int, default=600)
    parser.add_argument("--opengrep-timeout-sec", type=int, default=120)
    parser.add_argument("--repo-at-a-time", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--repos", nargs="*", help="Optional repo names to include")
    parser.add_argument("--skip-after-pass", action="store_true", help="Run baseline only (no after pass)")
    return parser.parse_args(argv)


def _find_latest_summary(pass_audit_root: Path) -> Path:
    candidates = sorted(pass_audit_root.glob("WebScanValidation_*/summary.json"))
    if not candidates:
        raise FileNotFoundError(f"No summary.json found under {pass_audit_root}")
    return candidates[-1]


def _build_validator_command(
    *,
    args: argparse.Namespace,
    pass_audit_root: Path,
    write_detection_rules: bool,
    validate_detection_rules: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(VALIDATOR_SCRIPT),
        "--base-url",
        args.base_url,
        "--audit-root",
        str(pass_audit_root),
        "--concurrency",
        str(args.concurrency),
        "--scan-complete-timeout-sec",
        str(args.scan_complete_timeout_sec),
        "--opengrep-timeout-sec",
        str(args.opengrep_timeout_sec),
    ]
    if args.repo_at_a_time:
        cmd.append("--repo-at-a-time")
    if args.headed:
        cmd.append("--headed")
    if args.repos:
        cmd.extend(["--repos", *args.repos])
    if write_detection_rules:
        cmd.append("--write-detection-rules")
    if validate_detection_rules:
        cmd.append("--validate-detection-rules")
    return cmd


def run_validation_pass(
    *,
    pass_name: str,
    args: argparse.Namespace,
    run_root: Path,
    write_detection_rules: bool,
    validate_detection_rules: bool,
) -> dict[str, Any]:
    pass_root = run_root / pass_name
    pass_root.mkdir(parents=True, exist_ok=True)
    cmd = _build_validator_command(
        args=args,
        pass_audit_root=pass_root,
        write_detection_rules=write_detection_rules,
        validate_detection_rules=validate_detection_rules,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    summary_file = _find_latest_summary(pass_root)
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    return {
        "name": pass_name,
        "cmd": cmd,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "summary_file": summary_file,
        "summary": summary,
    }


def summarize_issues(summary: dict[str, Any]) -> dict[str, Any]:
    results = summary.get("results") or []
    totals = {
        "repos_completed": int(summary.get("completed") or 0),
        "repos_failed": int(summary.get("failed") or 0),
        "orphan_issues": 0,
        "connection_issues": 0,
        "parity_issues": 0,
        "hierarchy_issues": 0,
        "high_value_smells": 0,
        "contextual_smells": 0,
        "low_value_smells": 0,
        "detection_rules_written": int(summary.get("detection_rules_written") or 0),
        "detection_rules_validated": int(summary.get("detection_rules_validated") or 0),
        "detection_rule_validation_failed": int(summary.get("detection_rule_validation_failed") or 0),
    }
    repo_rows: list[dict[str, Any]] = []

    for row in results:
        orphan = len(row.get("orphan_issues") or [])
        conn = len(row.get("connection_issues") or [])
        parity = len(row.get("parity_issues") or [])
        hierarchy = len(row.get("hierarchy_issues") or [])
        assessments = row.get("resource_value_assessments") or []
        high = sum(1 for x in assessments if (x.get("value_assessment") or {}).get("classification") == "high_value")
        contextual = sum(
            1 for x in assessments if (x.get("value_assessment") or {}).get("classification") == "contextual"
        )
        low = sum(1 for x in assessments if (x.get("value_assessment") or {}).get("classification") == "low_value")

        totals["orphan_issues"] += orphan
        totals["connection_issues"] += conn
        totals["parity_issues"] += parity
        totals["hierarchy_issues"] += hierarchy
        totals["high_value_smells"] += high
        totals["contextual_smells"] += contextual
        totals["low_value_smells"] += low

        repo_rows.append(
            {
                "repo_name": row.get("repo_name"),
                "status": row.get("status"),
                "orphan_issues": orphan,
                "connection_issues": conn,
                "parity_issues": parity,
                "hierarchy_issues": hierarchy,
                "high_value_smells": high,
            }
        )

    return {"totals": totals, "repos": repo_rows}


def _delta(before: int, after: int) -> str:
    sign = "+" if (after - before) > 0 else ""
    return f"{sign}{after - before}"


def build_report(
    *,
    baseline: dict[str, Any],
    after: dict[str, Any] | None,
    baseline_pass: dict[str, Any],
    after_pass: dict[str, Any] | None,
    run_root: Path,
) -> str:
    baseline_stats = summarize_issues(baseline)
    after_stats = summarize_issues(after) if after else None
    bt = baseline_stats["totals"]
    at = (after_stats or {}).get("totals", {})

    lines = [
        "# Diagram Review Skill Report",
        "",
        f"- **Generated (UTC):** {datetime.now(timezone.utc).isoformat()}",
        f"- **Run root:** `{run_root}`",
        f"- **Baseline summary:** `{baseline_pass['summary_file']}`",
    ]
    if after_pass:
        lines.append(f"- **After summary:** `{after_pass['summary_file']}`")
    lines.extend(
        [
            "",
            "## Security Architect Interpretation",
            "- Unconnected/orphaned elements are treated as strong detection-smell signals.",
            "- Child resources shown flat instead of nested are treated as threat-model quality defects.",
            "- High-value smells are prioritized because they affect ingress, identity, trust boundaries, or data paths.",
            "",
            "## Before / After Metrics",
            "",
            "| Metric | Baseline | After | Delta |",
            "|---|---:|---:|---:|",
        ]
    )
    metrics = [
        "repos_failed",
        "orphan_issues",
        "connection_issues",
        "parity_issues",
        "hierarchy_issues",
        "high_value_smells",
        "contextual_smells",
        "low_value_smells",
        "detection_rules_written",
        "detection_rules_validated",
        "detection_rule_validation_failed",
    ]
    for metric in metrics:
        before = int(bt.get(metric, 0))
        after_val = int(at.get(metric, before if after_stats is None else 0))
        lines.append(f"| `{metric}` | {before} | {after_val} | {_delta(before, after_val)} |")

    lines.extend(
        [
            "",
            "## Baseline Command",
            "```bash",
            " ".join(baseline_pass["cmd"]),
            "```",
        ]
    )
    if after_pass:
        lines.extend(
            [
                "",
                "## After Command",
                "```bash",
                " ".join(after_pass["cmd"]),
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## Repo-level High-Value Smells (Baseline)",
            "",
            "| Repo | Status | Orphans | Missing Connections | Parity Gaps | Hierarchy Smells | High-Value Smells |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in baseline_stats["repos"]:
        lines.append(
            f"| `{row['repo_name']}` | {row['status']} | {row['orphan_issues']} | {row['connection_issues']} | "
            f"{row['parity_issues']} | {row['hierarchy_issues']} | {row['high_value_smells']} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "- Screenshots are stored under each pass folder in `screenshots/`.",
            "- Rule files generated for diagram gaps are written to `Rules/Detection/` during baseline pass.",
            "- Every generated detection rule is validated with `opengrep scan --config <rule-file> <target-repo>` when enabled.",
        ]
    )
    return "\n".join(lines) + "\n"


def print_summary(
    *,
    baseline: dict[str, Any],
    after: dict[str, Any] | None,
    report_file: Path,
) -> None:
    baseline_stats = summarize_issues(baseline)
    after_stats = summarize_issues(after) if after else None
    bt = baseline_stats["totals"]
    at = (after_stats or {}).get("totals", {})

    print("Diagram review summary")
    print(f"- report: {report_file}")
    print(f"- repos failed: {bt['repos_failed']} -> {at.get('repos_failed', bt['repos_failed']) if after_stats else bt['repos_failed']}")
    print(f"- orphan issues: {bt['orphan_issues']} -> {at.get('orphan_issues', bt['orphan_issues']) if after_stats else bt['orphan_issues']}")
    print(f"- parity gaps: {bt['parity_issues']} -> {at.get('parity_issues', bt['parity_issues']) if after_stats else bt['parity_issues']}")
    print(f"- hierarchy smells: {bt['hierarchy_issues']} -> {at.get('hierarchy_issues', bt['hierarchy_issues']) if after_stats else bt['hierarchy_issues']}")
    print(f"- high-value smells: {bt['high_value_smells']} -> {at.get('high_value_smells', bt['high_value_smells']) if after_stats else bt['high_value_smells']}")
    print(f"- rules validated: {bt['detection_rules_validated']} -> {at.get('detection_rules_validated', bt['detection_rules_validated']) if after_stats else bt['detection_rules_validated']}")
    if baseline_stats["repos"]:
        print("- repo breakdown:")
        for row in baseline_stats["repos"]:
            print(
                f"  * {row['repo_name']}: status={row['status']}, orphans={row['orphan_issues']}, "
                f"parity={row['parity_issues']}, hierarchy={row['hierarchy_issues']}, high-value={row['high_value_smells']}"
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = Path(args.audit_root).resolve() / f"DiagramReviewSkill_{timestamp}"
    run_root.mkdir(parents=True, exist_ok=True)

    baseline_pass = run_validation_pass(
        pass_name="baseline",
        args=args,
        run_root=run_root,
        write_detection_rules=True,
        validate_detection_rules=True,
    )
    after_pass: dict[str, Any] | None = None
    if not args.skip_after_pass:
        after_pass = run_validation_pass(
            pass_name="after",
            args=args,
            run_root=run_root,
            write_detection_rules=False,
            validate_detection_rules=False,
        )

    report = build_report(
        baseline=baseline_pass["summary"],
        after=(after_pass or {}).get("summary"),
        baseline_pass=baseline_pass,
        after_pass=after_pass,
        run_root=run_root,
    )
    report_file = run_root / "diagram_review_report.md"
    report_file.write_text(report, encoding="utf-8")
    print_summary(
        baseline=baseline_pass["summary"],
        after=(after_pass or {}).get("summary"),
        report_file=report_file,
    )

    combined_log = [
        "=== BASELINE STDOUT ===",
        baseline_pass["stdout"],
        "",
        "=== BASELINE STDERR ===",
        baseline_pass["stderr"],
        "",
    ]
    if after_pass:
        combined_log.extend(
            [
                "=== AFTER STDOUT ===",
                after_pass["stdout"],
                "",
                "=== AFTER STDERR ===",
                after_pass["stderr"],
                "",
            ]
        )
    (run_root / "orchestration.log").write_text("\n".join(combined_log), encoding="utf-8")

    print(f"Diagram review report: {report_file}")
    print(f"Run artifacts: {run_root}")

    baseline_fail = int(baseline_pass["exit_code"]) != 0
    after_fail = bool(after_pass and int(after_pass["exit_code"]) != 0)
    return 1 if (baseline_fail or after_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
