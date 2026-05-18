#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Validate"))

from review_generated_diagrams import (  # noqa: E402
    _build_validator_command,
    build_report,
    parse_args,
    run_validation_pass,
    summarize_issues,
)


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


def test_build_report_includes_asset_validation_report(tmp_path: Path):
    baseline = {
        "completed": 1,
        "failed": 0,
        "results": [],
        "detection_rules_written": 0,
        "detection_rules_validated": 0,
        "detection_rule_validation_failed": 0,
    }
    baseline_pass = {"summary_file": tmp_path / "baseline.json", "cmd": ["python3", "validator.py", "--baseline"]}

    report = build_report(
        baseline=baseline,
        after=None,
        baseline_pass=baseline_pass,
        after_pass=None,
        run_root=tmp_path,
    )

    assert "ASSET VALIDATION REPORT" in report
    assert "Asset validation report generation failed" not in report


def test_apply_detection_rules_and_regenerate_times_out(monkeypatch, tmp_path: Path):
    import review_generated_diagrams

    rule_file = tmp_path / "rule-1.yml"
    rule_file.write_text("id: rule-1\n", encoding="utf-8")

    monkeypatch.setattr(review_generated_diagrams, "DETECTION_RULES_DIR", tmp_path)

    seen: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, check=False, timeout=None, **_kwargs):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(review_generated_diagrams.subprocess, "run", fake_run)

    baseline_summary = {
        "results": [
            {
                "repo_name": "repo-a",
                "experiment_id": "exp-1",
                "detection_rules": [{"rule_id": "rule-1"}],
            }
        ]
    }

    result = review_generated_diagrams._apply_detection_rules_and_regenerate(
        baseline_summary=baseline_summary,
        repo_paths={"repo-a": "/repos/repo-a"},
        scan_timeout_sec=123,
    )

    assert result is False
    assert seen["timeout"] == 123


def test_parse_args_only_unscanned_flag():
    args = parse_args(["--only-unscanned"])
    assert args.only_unscanned is True


def test_build_validator_command_forwards_only_unscanned(tmp_path: Path):
    args = parse_args(["--only-unscanned"])
    cmd = _build_validator_command(
        args=args,
        pass_audit_root=tmp_path,
        write_detection_rules=False,
        validate_detection_rules=False,
    )
    assert "--only-unscanned" in cmd


def test_run_validation_pass_surfaces_missing_summary(monkeypatch, tmp_path: Path):
    import review_generated_diagrams

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["python3", "validator.py"],
            returncode=1,
            stdout="validator stdout",
            stderr="validator stderr",
        )

    def fake_find_latest_summary(_pass_audit_root: Path):
        raise FileNotFoundError("missing summary")

    monkeypatch.setattr(review_generated_diagrams.subprocess, "run", fake_run)
    monkeypatch.setattr(review_generated_diagrams, "_find_latest_summary", fake_find_latest_summary)

    args = parse_args([])
    with pytest.raises(RuntimeError, match="did not produce summary.json"):
        run_validation_pass(
            pass_name="baseline",
            args=args,
            run_root=tmp_path,
            write_detection_rules=False,
            validate_detection_rules=False,
        )
