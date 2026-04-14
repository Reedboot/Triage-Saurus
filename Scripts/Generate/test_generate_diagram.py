#!/usr/bin/env python3
"""Regression tests for generate_diagram.py."""

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import generate_diagram


class _FakeRow(dict):
    def __getattr__(self, item):
        return self[item]


class _FakeConn:
    def __init__(self, rows_by_sql):
        self.rows_by_sql = rows_by_sql

    def execute(self, sql, params=None):
        for marker, rows in self.rows_by_sql.items():
            if marker in sql:
                return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_internal_zone_skipped_without_children(monkeypatch):
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "vm-1",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        }
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
    }))
    monkeypatch.setattr(
        generate_diagram._rtdb,
        "get_resource_type",
        lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "Virtual Machine", "category": "Compute"},
    )
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert diagram.startswith("flowchart TB")
    assert "subgraph zone_internal" not in diagram
    assert "vm-1" in diagram


def test_internet_arrows_are_colored_red(monkeypatch):
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "app-gateway",
            "resource_type": "azurerm_application_gateway",
            "provider": "azure",
            "repo_name": "repo",
        }
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT DISTINCT r.resource_name, r.resource_type": [
            _FakeRow(resource_name="app-gateway", resource_type="azurerm_application_gateway"),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "App Service", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "App Service")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *args, **kwargs: "Compute")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert diagram.startswith("flowchart TB")
    assert "internet -->" in diagram
    assert "linkStyle 0 stroke:red,stroke-width:2px" in diagram
    assert "subgraph zone_internet" not in diagram
    assert "style zone_internet" not in diagram
    assert "Internet[" not in diagram


def test_alicloud_api_gateway_is_treated_as_public(monkeypatch):
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "ali-gateway",
            "resource_type": "alicloud_api_gateway_api",
            "provider": "alicloud",
            "repo_name": "repo",
        }
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT DISTINCT r.resource_name, r.resource_type": [
            _FakeRow(resource_name="ali-gateway", resource_type="alicloud_api_gateway_api"),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "API Gateway", "category": "API"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Other")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "API Gateway")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *args, **kwargs: "API")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert diagram.startswith("flowchart TB")
    assert "internet -->" in diagram
