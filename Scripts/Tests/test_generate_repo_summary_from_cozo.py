#!/usr/bin/env python3
"""Regression tests for generate_repo_summary_from_cozo.py."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import generate_repo_summary_from_cozo as summary


def test_repo_summary_does_not_inject_invalid_mermaid(monkeypatch):
    monkeypatch.setattr(
        summary,
        "_collect_architecture_blocks",
        lambda providers, cloud_dir: [("azure", "```mermaid\nflowchart TB\n  A[Node]\n```")],
    )

    rendered = summary.build_summary_markdown(
        "TerraformGoat",
        "scan-1",
        [{"provider": "azure", "severity": "high", "finding_id": "finding-1"}],
        {},
    )

    assert "style finding-1" not in rendered
    assert "finding-1 --> Internet" not in rendered
