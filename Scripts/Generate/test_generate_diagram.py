#!/usr/bin/env python3
"""Regression tests for generate_diagram.py."""

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import generate_diagram
import resource_type_db as _rtdb


class _FakeRow(dict):
    def __getattr__(self, item):
        return self[item]


class _FakeConn:
    def __init__(self, rows_by_sql):
        self.rows_by_sql = rows_by_sql

    def execute(self, sql, params=None):
        normalized_sql = sql.replace(", parent_resource_id", "")
        for marker, rows in self.rows_by_sql.items():
            if marker in sql or marker in normalized_sql:
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


def test_structural_contains_edges_do_not_create_duplicate_app_service_plan_nodes(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "app_service_plan",
            "resource_type": "azurerm_app_service_plan",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "function_app",
            "resource_type": "azurerm_function_app",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
        {
            "id": 3,
            "resource_name": "function_app_front",
            "resource_type": "azurerm_function_app",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    connections = [
        {"source": "app_service_plan", "target": "function_app", "connection_type": "contains"},
        {"source": "app_service_plan", "target": "function_app_front", "connection_type": "contains"},
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: connections)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="app_service_plan", parent_type="azurerm_app_service_plan", child_id=2, child_name="function_app", child_type="azurerm_function_app"),
            _FakeRow(parent_id=1, parent_name="app_service_plan", parent_type="azurerm_app_service_plan", child_id=3, child_name="function_app_front", child_type="azurerm_function_app"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="app_service_plan", resource_type="azurerm_app_service_plan", provider="azure", repo_id=1),
            _FakeRow(id=2, resource_name="function_app", resource_type="azurerm_function_app", provider="azure", repo_id=1),
            _FakeRow(id=3, resource_name="function_app_front", resource_type="azurerm_function_app", provider="azure", repo_id=1),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "App Service Plan", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "App Service Plan")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *args, **kwargs: "Compute")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert 'subgraph app_service_plan["App Service Plan: app_service_plan (2 sub-assets)"]' in diagram
    assert "app_service_plan_sg" not in diagram
    assert "contains" not in diagram
    assert diagram.count("app_service_plan -->") == 0


def test_public_ip_collapse_targets_vm_not_vnet(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "dev_vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "VM_PublicIP",
            "resource_type": "azurerm_public_ip",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
        {
            "id": 3,
            "resource_name": "vNet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    connections = [
        {"source": "VM_PublicIP", "target": "dev_vm", "connection_type": "public_ip"},
        {"source": "vNet", "target": "VM_PublicIP", "connection_type": "contains"},
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: connections)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="dev_vm", parent_type="azurerm_virtual_machine", child_id=2, child_name="VM_PublicIP", child_type="azurerm_public_ip"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="dev_vm", resource_type="azurerm_virtual_machine", provider="azure", repo_id=1),
            _FakeRow(id=2, resource_name="VM_PublicIP", resource_type="azurerm_public_ip", provider="azure", repo_id=1),
            _FakeRow(id=3, resource_name="vNet", resource_type="azurerm_virtual_network", provider="azure", repo_id=1),
        ],
    }))

    def _fake_resource_type(_conn, resource_type):
        rt = (resource_type or "").lower()
        friendly = "Virtual Network" if "virtual_network" in rt else "Virtual Machine"
        return {"display_on_architecture_chart": True, "friendly_name": friendly, "category": "Network" if "virtual_network" in rt else "Compute"}

    def _fake_render_category(_conn, resource_type):
        rt = (resource_type or "").lower()
        if "virtual_network" in rt:
            return "Network"
        if "public_ip" in rt:
            return "Network"
        return "Compute"

    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", _fake_resource_type)
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", _fake_render_category)
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "Virtual Machine")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", _fake_render_category)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "|public IP; public ip| dev_vm" in diagram
    assert "subgraph vNet[\"🔷 VNet: vNet\"]" in diagram
    assert "public IP; contains" not in diagram
    assert "subgraph dev_vm[\"Virtual Machine: dev_vm (1 sub-asset)\"]" in diagram


def test_punctuation_heavy_labels_are_quoted(monkeypatch):
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "node[(())]",
            "resource_type": "azurerm_storage_account",
            "provider": "azure",
            "repo_name": "repo",
        }
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "Storage Account", "category": "Storage"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Storage")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "Storage Account")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *args, **kwargs: "Storage")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert 'node["🪣 node[(())]<br/>Storage Account"]' in diagram
    assert 'node[🪣 node[(())]<br/>Storage Account]' not in diagram


def test_rbac_resource_types_are_excluded(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "app",
            "resource_type": "azurerm_linux_web_app",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "aks-rbac",
            "resource_type": "kubernetes_rbac",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "Web App", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *args, **kwargs: "Web App")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *args, **kwargs: "Compute")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert 'app["🌐 app<br/>Web App"]' in diagram or 'app["app<br/>Web App"]' in diagram
    assert "kubernetes_rbac" not in diagram


def test_data_access_edges_are_unlabelled(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "app",
            "resource_type": "azurerm_linux_web_app",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "db",
            "resource_type": "azurerm_mssql_server",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    connections = [
        {"source": "app", "target": "db", "connection_type": "data_access", "is_cross_repo": 0},
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: connections)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="app", resource_type="azurerm_linux_web_app", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="db", resource_type="azurerm_mssql_server", provider="azure", repo_id=1, parent_resource_id=None),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "App Service", "category": "Compute"} if "web_app" in (a[1] if len(a) > 1 else "") else {"display_on_architecture_chart": True, "friendly_name": "SQL Server", "category": "Database"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Compute" if "web_app" in (a[1] if len(a) > 1 else "") else "Database")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda _conn, rt: "App Service" if "web_app" in rt else "SQL Server")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda _conn, rt: "Compute" if "web_app" in rt else "Database")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "data access" not in diagram.lower()
    assert " -.-> " in diagram


def test_hidden_subnet_is_promoted_with_vm_child(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "vNet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "dev-subnet",
            "resource_type": "azurerm_subnet",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
        {
            "id": 3,
            "resource_name": "dev-vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 2,
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="vNet", parent_type="azurerm_virtual_network", child_id=2, child_name="dev-subnet", child_type="azurerm_subnet"),
            _FakeRow(parent_id=2, parent_name="dev-subnet", parent_type="azurerm_subnet", child_id=3, child_name="dev-vm", child_type="azurerm_virtual_machine"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="vNet", resource_type="azurerm_virtual_network", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="dev-subnet", resource_type="azurerm_subnet", provider="azure", repo_id=1, parent_resource_id=1),
            _FakeRow(id=3, resource_name="dev-vm", resource_type="azurerm_virtual_machine", provider="azure", repo_id=1, parent_resource_id=2),
        ],
    }))

    def _fake_rt(_c, rt):
        rt_l = (rt or "").lower()
        if "virtual_network" in rt_l:
            return {"display_on_architecture_chart": True, "friendly_name": "Virtual Network", "category": "Network"}
        if "subnet" in rt_l:
            return {"display_on_architecture_chart": False, "friendly_name": "Subnet", "category": "Network"}
        return {"display_on_architecture_chart": True, "friendly_name": "Virtual Machine", "category": "Compute"}

    def _fake_cat(_c, rt):
        rt_l = (rt or "").lower()
        if "virtual_network" in rt_l or "subnet" in rt_l:
            return "Network"
        return "Compute"

    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", _fake_rt)
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", _fake_cat)
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", _fake_cat)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "subgraph vNet[\"🔷 VNet: vNet\"]" in diagram
    assert "subgraph dev_subnet[\"Subnet: dev-subnet (1 sub-asset)\"]" in diagram
    assert "dev_vm" in diagram
    assert diagram.index("subgraph dev_subnet") < diagram.index("dev_vm")


def test_s3_bucket_controls_render_inside_bucket_subgraph(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "bucket_upload",
            "resource_type": "aws_s3_bucket",
            "provider": "aws",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "bucket_upload_acl",
            "resource_type": "aws_s3_bucket_acl",
            "provider": "aws",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
        {
            "id": 3,
            "resource_name": "bucket_upload_ownership_controls",
            "resource_type": "aws_s3_bucket_ownership_controls",
            "provider": "aws",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="bucket_upload", parent_type="aws_s3_bucket", child_id=2, child_name="bucket_upload_acl", child_type="aws_s3_bucket_acl"),
            _FakeRow(parent_id=1, parent_name="bucket_upload", parent_type="aws_s3_bucket", child_id=3, child_name="bucket_upload_ownership_controls", child_type="aws_s3_bucket_ownership_controls"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="bucket_upload", resource_type="aws_s3_bucket", provider="aws", repo_id=1),
            _FakeRow(id=2, resource_name="bucket_upload_acl", resource_type="aws_s3_bucket_acl", provider="aws", repo_id=1),
            _FakeRow(id=3, resource_name="bucket_upload_ownership_controls", resource_type="aws_s3_bucket_ownership_controls", provider="aws", repo_id=1),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)

    assert _rtdb.get_resource_type(None, "aws_s3_bucket_acl")["parent_type"] == "aws_s3_bucket"
    assert _rtdb.get_resource_type(None, "aws_s3_bucket_ownership_controls")["parent_type"] == "aws_s3_bucket"

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "subgraph zone_data[\"🗄️ Data Tier\"]" in diagram
    assert 'subgraph bucket_upload["🗄️ S3 Bucket: bucket_upload (2 sub-assets)"]' in diagram
    assert "bucket_upload_acl[" in diagram
    assert "bucket_upload_ownership_controls[" in diagram


def test_internet_to_vm_via_public_ip_parent_lookup(monkeypatch):
    """When Internet→PublicIP is the only connection, collapse to Internet→VM via parent_resource_id."""
    resources = [
        {
            "id": 1,
            "resource_name": "dev-vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "VM_PublicIP",
            "resource_type": "azurerm_public_ip",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    # Only Internet→VM_PublicIP, no VM_PublicIP→dev-vm edge
    connections = [
        {"source": "Internet", "target": "VM_PublicIP", "connection_type": "internet_access", "is_cross_repo": 0},
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "_add_internet_connections", lambda conns, *a, **kw: connections)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="dev-vm", parent_type="azurerm_virtual_machine", child_id=2, child_name="VM_PublicIP", child_type="azurerm_public_ip"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="dev-vm", resource_type="azurerm_virtual_machine", provider="azure", repo_id=1),
            _FakeRow(id=2, resource_name="VM_PublicIP", resource_type="azurerm_public_ip", provider="azure", repo_id=1),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "Virtual Machine", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "internet -->" in diagram
    assert "dev_vm" in diagram
    assert "public IP" in diagram
    assert "VM_PublicIP -->" not in diagram  # public IP should not appear as an arrow source


def test_vnet_is_rendered_as_internal_container(monkeypatch):
    """A single VNet should be rendered as an internal container, not suppressed."""
    resources = [
        {
            "id": 1,
            "resource_name": "dev-vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "vNet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id": [
            _FakeRow(id=1, resource_name="dev-vm", resource_type="azurerm_virtual_machine", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="vNet", resource_type="azurerm_virtual_network", provider="azure", repo_id=1, parent_resource_id=None),
        ],
    }))

    def _fake_rt(_c, rt):
        if "virtual_network" in (rt or ""):
            return {"display_on_architecture_chart": True, "friendly_name": "Virtual Network", "category": "Network"}
        return {"display_on_architecture_chart": True, "friendly_name": "Virtual Machine", "category": "Compute"}

    def _fake_cat(_c, rt):
        return "Network" if "virtual_network" in (rt or "") else "Compute"

    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", _fake_rt)
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", _fake_cat)
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", _fake_cat)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "subgraph vNet[\"🔷 VNet: vNet\"]" in diagram
    assert "dev_vm" in diagram
    assert "style vNet" in diagram
    assert "fill:" not in diagram


def test_single_nsg_is_rendered_as_compute_container_inside_vnet(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "dev-vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "vNet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 3,
            "resource_name": "dev-nsg",
            "resource_type": "azurerm_network_security_group",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id": [
            _FakeRow(id=1, resource_name="dev-vm", resource_type="azurerm_virtual_machine", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="vNet", resource_type="azurerm_virtual_network", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=3, resource_name="dev-nsg", resource_type="azurerm_network_security_group", provider="azure", repo_id=1, parent_resource_id=None),
        ],
    }))

    def _fake_rt(_c, rt):
        rt_l = (rt or "").lower()
        if "virtual_network" in rt_l:
            return {"display_on_architecture_chart": True, "friendly_name": "Virtual Network", "category": "Network"}
        if "network_security_group" in rt_l:
            return {"display_on_architecture_chart": True, "friendly_name": "Network Security Group", "category": "Network"}
        return {"display_on_architecture_chart": True, "friendly_name": "Virtual Machine", "category": "Compute"}

    def _fake_cat(_c, rt):
        rt_l = (rt or "").lower()
        if "virtual_network" in rt_l or "network_security_group" in rt_l:
            return "Network"
        return "Compute"

    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", _fake_rt)
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", _fake_cat)
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", _fake_cat)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "subgraph vNet[\"🔷 VNet: vNet\"]" in diagram
    assert "subgraph dev_nsg[\"🛡️ NSG: dev-nsg\"]" in diagram
    assert "subgraph compute_tier[\"🖥️ Compute Tier\"]" in diagram
    assert "dev_vm" in diagram


def test_automation_account_not_nested_inside_managed_identity(monkeypatch):
    """Automation account that USES a managed identity should not be nested inside it."""
    resources = [
        {
            "id": 1,
            "resource_name": "user_id",
            "resource_type": "azurerm_user_assigned_identity",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "automation_account",
            "resource_type": "azurerm_automation_account",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    connections = [
        {"source": "automation_account", "target": "user_id", "connection_type": "authenticates_via", "is_cross_repo": 0},
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: connections)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [
            _FakeRow(parent_id=1, parent_name="user_id", parent_type="azurerm_user_assigned_identity", child_id=2, child_name="automation_account", child_type="azurerm_automation_account"),
        ],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="user_id", resource_type="azurerm_user_assigned_identity", provider="azure", repo_id=1),
            _FakeRow(id=2, resource_name="automation_account", resource_type="azurerm_automation_account", provider="azure", repo_id=1),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "Identity", "category": "Identity"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Identity")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *a, **kw: "Identity")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    # automation_account should appear as a separate node, not nested inside user_id
    assert "subgraph user_id" not in diagram
    # the authenticates_via arrow should still be present
    assert "automation_account" in diagram
    assert "user_id" in diagram


def test_provider_filter_oci_includes_legacy_oracle_rows(monkeypatch):
    resources = [
        {
            "id": 1,
            "resource_name": "legacy-oci",
            "resource_type": "oci_core_instance",
            "provider": "oracle",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "new-oci",
            "resource_type": "oci_core_instance",
            "provider": "oci",
            "repo_name": "repo",
        },
    ]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="legacy-oci", resource_type="oci_core_instance", provider="oracle", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="new-oci", resource_type="oci_core_instance", provider="oci", repo_id=1, parent_resource_id=None),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "Compute", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *a, **kw: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *a, **kw: "Compute")

    diagram = generate_diagram.generate_architecture_diagram("exp-1", provider="oci")

    assert "legacy-oci" in diagram
    assert "new-oci" in diagram


def test_synthetic_internet_edges_always_included(monkeypatch):
    """Test that synthetic Internet connections are always included in diagrams."""
    resources = [
        {
            "id": 1,
            "resource_name": "app-1",
            "resource_type": "azurerm_linux_web_app",
            "provider": "azure",
            "repo_name": "repo",
        }
    ]

    called = {"add_internet": False}

    def _fake_add_internet(connections, *args, **kwargs):
        called["add_internet"] = True
        return connections + [{"source": "Internet", "target": "app-1", "connection_type": "internet_access"}]

    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: resources)
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "_add_internet_connections", _fake_add_internet)
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "SELECT COUNT(1) as c FROM repositories": [_FakeRow(c=1)],
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id FROM resources": [
            _FakeRow(id=1, resource_name="app-1", resource_type="azurerm_linux_web_app", provider="azure", repo_id=1, parent_resource_id=None),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "App Service", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)
    monkeypatch.setattr(generate_diagram._rtdb, "get_friendly_name", lambda *a, **kw: "App Service")
    monkeypatch.setattr(generate_diagram._rtdb, "get_category", lambda *a, **kw: "Compute")

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    # Synthetic Internet connections are always included
    assert called["add_internet"] is True
    assert diagram
    assert "Internet" in diagram


def test_internet_node_absent_for_internal_only_resource(monkeypatch):
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "internal-db",
            "resource_type": "azurerm_sql_server",
            "provider": "azure",
            "repo_name": "repo",
        }
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id": [],
    }))
    monkeypatch.setattr(
        generate_diagram._rtdb,
        "get_resource_type",
        lambda *args, **kwargs: {"display_on_architecture_chart": True, "friendly_name": "SQL Server", "category": "Database"},
    )
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *args, **kwargs: "Database")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *args, **kwargs: False)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "internal" in diagram.lower()
    assert 'internet["' not in diagram


def test_distinct_resources_with_same_sanitized_name_get_unique_ids(monkeypatch):
    """Two different resources that sanitize to the same Mermaid ID must not collapse into one node."""
    monkeypatch.setattr(generate_diagram, "get_resources_for_diagram", lambda experiment_id: [
        {
            "id": 1,
            "resource_name": "app-service",
            "resource_type": "azurerm_app_service",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "app_service",
            "resource_type": "azurerm_linux_web_app",
            "provider": "azure",
            "repo_name": "repo",
        },
    ])
    monkeypatch.setattr(generate_diagram, "get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr(generate_diagram, "get_db_connection", lambda: _FakeConn({
        "JOIN resources child ON child.parent_resource_id = parent.id": [],
        "SELECT id, resource_name, resource_type, provider, repo_id": [
            _FakeRow(id=1, resource_name="app-service", resource_type="azurerm_app_service", provider="azure", repo_id=1, parent_resource_id=None),
            _FakeRow(id=2, resource_name="app_service", resource_type="azurerm_linux_web_app", provider="azure", repo_id=1, parent_resource_id=None),
        ],
    }))
    monkeypatch.setattr(generate_diagram._rtdb, "get_resource_type", lambda *a, **kw: {"display_on_architecture_chart": True, "friendly_name": "App Service", "category": "Compute"})
    monkeypatch.setattr(generate_diagram._rtdb, "get_render_category", lambda *a, **kw: "Compute")
    monkeypatch.setattr(generate_diagram._rtdb, "is_physical_network_device", lambda *a, **kw: False)

    diagram = generate_diagram.generate_architecture_diagram("exp-1")

    assert "app-service" in diagram or "app service" in diagram
    assert "app_service" in diagram or "app service" in diagram
    assert diagram.count('app_service') >= 1
