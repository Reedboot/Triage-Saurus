#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Validate"))

from review_generated_diagrams import build_report, summarize_issues  # noqa: E402


def test_summarize_issues_counts_totals_and_value_classes():
    summary = {
        "completed": 1,
        "failed": 0,
        "detection_rules_written": 2,
        "detection_rules_validated": 2,
        "detection_rule_validation_failed": 1,
        "results": [
            {
                "repo_name": "repo-a",
                "status": "completed",
                "orphan_issues": [{}],
                "connection_issues": [{}, {}],
                "parity_issues": [{}],
                "hierarchy_issues": [{}],
                "resource_value_assessments": [
                    {"value_assessment": {"classification": "high_value"}},
                    {"value_assessment": {"classification": "contextual"}},
                    {"value_assessment": {"classification": "low_value"}},
                ],
            }
        ],
    }
    stats = summarize_issues(summary)
    totals = stats["totals"]
    assert totals["orphan_issues"] == 1
    assert totals["connection_issues"] == 2
    assert totals["parity_issues"] == 1
    assert totals["hierarchy_issues"] == 1
    assert totals["high_value_smells"] == 1
    assert totals["contextual_smells"] == 1
    assert totals["low_value_smells"] == 1
    assert totals["detection_rules_written"] == 2
    assert totals["detection_rules_validated"] == 2
    assert totals["detection_rule_validation_failed"] == 1


def test_build_report_contains_before_after_delta_table(tmp_path: Path):
    baseline = {
        "completed": 1,
        "failed": 1,
        "results": [],
        "detection_rules_written": 1,
        "detection_rules_validated": 1,
        "detection_rule_validation_failed": 0,
    }
    after = {
        "completed": 2,
        "failed": 0,
        "results": [],
        "detection_rules_written": 0,
        "detection_rules_validated": 0,
        "detection_rule_validation_failed": 0,
    }
    baseline_pass = {"summary_file": tmp_path / "baseline.json", "cmd": ["python3", "validator.py", "--baseline"]}
    after_pass = {"summary_file": tmp_path / "after.json", "cmd": ["python3", "validator.py", "--after"]}

    report = build_report(
        baseline=baseline,
        after=after,
        baseline_pass=baseline_pass,
        after_pass=after_pass,
        run_root=tmp_path,
    )
    assert "Before / After Metrics" in report
    assert "| `repos_failed` | 1 | 0 | -1 |" in report
    assert "Security Architect Interpretation" in report
