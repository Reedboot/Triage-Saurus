#!/usr/bin/env python3
"""Regression tests for report_generation architecture Mermaid output."""

from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils", "Validate"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import report_generation


def test_simple_architecture_diagram_uses_top_down_layout():
    diagram = report_generation._build_simple_architecture_diagram("repo", {})

    assert diagram.startswith("flowchart TB")


def test_service_only_architecture_diagram_uses_top_down_layout(monkeypatch):
    monkeypatch.setattr(
        report_generation,
        "_load_repo_topology_connections",
        lambda repo_name: ["dummy"],
    )
    monkeypatch.setattr(
        report_generation,
        "_collect_db_topology_edges",
        lambda repo_name, db_connections, exclude_connection_types=None: (
            [("Internet", "Service", "calls")],
            [],
        ),
    )

    diagram = report_generation._build_service_only_architecture_diagram(
        "repo",
        SimpleNamespace(resources=[]),
        repo_path=None,
    )

    assert diagram.startswith("flowchart TB")
