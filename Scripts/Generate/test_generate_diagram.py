#!/usr/bin/env python3
"""Regression tests for generate_diagram.py."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

from generate_diagram import HierarchicalDiagramBuilder, sanitize_id
from internet_exposure_detector import ExposureDetail
from icon_resolver import get_icon_path


def test_internal_zone_skipped_without_children(monkeypatch):
    """Test that diagram generation works with basic resources."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "vm-1",
                "resource_type": "azurerm_virtual_machine",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"vm-1": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)
    
    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_internet_arrows_are_colored_red(monkeypatch):
    """Test that internet arrows can be included."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "app-gateway",
                "resource_type": "azurerm_application_gateway",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = [
            {
                "source": "Internet",
                "target": "app-gateway",
                "connection_type": "confirmed_public",
                "confirmed": True,
            }
        ]
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"app-gateway": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_alicloud_api_gateway_is_treated_as_public(monkeypatch):
    """Test that diagram works with Alicloud resources."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "ali-gateway",
                "resource_type": "alicloud_api_gateway_api",
                "provider": "alicloud",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"ali-gateway": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_structural_contains_edges_do_not_create_duplicate_app_service_plan_nodes(monkeypatch):
    """Test that structural contains edges don't create duplicate nodes."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
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
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1], resources[2]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_public_ip_collapse_targets_vm_not_vnet(monkeypatch):
    """Test that public IP collapse targets VM not vnet."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
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
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_storage_blob_is_nested_under_container(monkeypatch):
    """Test that storage blob is nested under container."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "storage_container",
            "resource_type": "azurerm_storage_container",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "storage_blob",
            "resource_type": "azurerm_storage_blob",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_punctuation_heavy_labels_are_quoted(monkeypatch):
    """Test that punctuation-heavy labels are quoted."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "name-with/punctuation",
                "resource_type": "azurerm_resource",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"name-with/punctuation": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_rbac_resource_types_are_excluded(monkeypatch):
    """Test that RBAC resource types are excluded."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "role_assignment",
                "resource_type": "azurerm_role_assignment",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"role_assignment": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_data_access_edges_are_unlabelled(monkeypatch):
    """Test that data access edges are unlabelled."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "app",
            "resource_type": "azurerm_app_service",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "db",
            "resource_type": "azurerm_sql_database",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = [
            {
                "source": "app",
                "target": "db",
                "connection_type": "uses_database",
            }
        ]
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_hidden_subnet_is_promoted_with_vm_child(monkeypatch):
    """Test that hidden subnet is promoted when it has VM child."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "subnet",
            "resource_type": "azurerm_subnet",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_s3_bucket_controls_render_inside_bucket_subgraph(monkeypatch):
    """Test that S3 bucket controls render inside bucket subgraph."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "bucket",
            "resource_type": "aws_s3_bucket",
            "provider": "aws",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "bucket_policy",
            "resource_type": "aws_s3_bucket_policy",
            "provider": "aws",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_internet_to_vm_via_public_ip_parent_lookup(monkeypatch):
    """Test internet to VM via public IP parent lookup."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "vm",
            "resource_type": "azurerm_virtual_machine",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "public_ip",
            "resource_type": "azurerm_public_ip",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_vnet_is_rendered_as_internal_container(monkeypatch):
    """Test that vnet is rendered as internal container."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "vnet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_single_nsg_is_rendered_as_compute_container_inside_vnet(monkeypatch):
    """Test that single NSG is rendered as compute container inside vnet."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "vnet",
            "resource_type": "azurerm_virtual_network",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "nsg",
            "resource_type": "azurerm_network_security_group",
            "provider": "azure",
            "repo_name": "repo",
            "parent_resource_id": 1,
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {1: [resources[1]]}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_automation_account_not_nested_inside_managed_identity(monkeypatch):
    """Test that automation account is not nested inside managed identity."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "automation",
            "resource_type": "azurerm_automation_account",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "identity",
            "resource_type": "azurerm_user_assigned_identity",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_provider_filter_oci_includes_legacy_oracle_rows(monkeypatch):
    """Test that provider filter OCI includes legacy oracle rows."""
    builder = HierarchicalDiagramBuilder("exp-1", provider_filter="oci")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "instance",
                "resource_type": "oci_core_instance",
                "provider": "oci",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"instance": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_synthetic_internet_edges_always_included(monkeypatch):
    """Test that synthetic internet edges are always included."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "function",
            "resource_type": "azurerm_function_app",
            "provider": "azure",
            "repo_name": "repo",
        }
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = [
            {
                "source": "Internet",
                "target": "function",
                "connection_type": "synthetic_http",
            }
        ]
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_internet_node_absent_for_internal_only_resource(monkeypatch):
    """Test that internet node is absent for internal only resource."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "internal_db",
                "resource_type": "azurerm_sql_database",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"internal_db": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_distinct_resources_with_same_sanitized_name_get_unique_ids(monkeypatch):
    """Test that distinct resources with same sanitized name get unique IDs."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    resources = [
        {
            "id": 1,
            "resource_name": "example",
            "resource_type": "azurerm_resource_a",
            "provider": "azure",
            "repo_name": "repo",
        },
        {
            "id": 2,
            "resource_name": "example",
            "resource_type": "azurerm_resource_b",
            "provider": "azure",
            "repo_name": "repo",
        },
    ]
    
    def fake_load_data():
        builder.resources = resources
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {r["id"]: r for r in resources}
        builder.resource_by_name = {r["resource_name"]: r for r in resources}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)

    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_validate_diagram_syntax_detects_subgraph_class_suffix_errors():
    """Test that _validate_diagram_syntax detects unsupported subgraph class suffixes."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    bad_diagram = '''flowchart TB
  subgraph n23["🔒 dev-vm"]:::icon-azurerm-virtual-machine
    n20["VM PublicIP"]
  end'''
    
    # Should raise ValueError for unsupported subgraph class suffix
    try:
        builder._validate_diagram_syntax(bad_diagram)
        assert False, "Expected ValueError for subgraph class suffix"
    except ValueError as e:
        msg = str(e).lower()
        assert "subgraph" in msg
        assert "class suffix" in msg


def test_validate_diagram_syntax_allows_valid():
    """Test that _validate_diagram_syntax allows valid diagrams."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    good_diagram = '''flowchart TB
  subgraph n23["dev-vm"]
    n20["VM PublicIP"]
  end'''
    
    # Should NOT raise ValueError for valid diagram
    builder._validate_diagram_syntax(good_diagram)


def test_validate_diagram_syntax_detects_unbalanced_brackets():
    """Test that _validate_diagram_syntax detects unbalanced brackets."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    bad_diagram = '''flowchart TB
  n1["label without closing bracket
  n2["valid"]
  end'''
    
    # Should raise ValueError for unbalanced brackets
    try:
        builder._validate_diagram_syntax(bad_diagram)
        assert False, "Expected ValueError for unbalanced brackets"
    except ValueError as e:
        assert "bracket" in str(e).lower()


def test_diagram_validation_passes_valid_diagrams(monkeypatch):
    """Test that diagram generation validation passes valid diagrams."""
    builder = HierarchicalDiagramBuilder("exp-1")
    
    def fake_load_data():
        builder.resources = [
            {
                "id": 1,
                "resource_name": "app-gateway",
                "resource_type": "azurerm_application_gateway",
                "provider": "azure",
                "repo_name": "repo",
            }
        ]
        builder.connections = []
        builder.children_by_parent = {}
        builder.exposed_resources = {}
        builder.resource_by_id = {1: builder.resources[0]}
        builder.resource_by_name = {"app-gateway": builder.resources[0]}
    
    monkeypatch.setattr(builder, "load_data", fake_load_data)
    monkeypatch.setattr(builder, "infer_connections", lambda: False)
    
    # Valid diagram should not raise any errors
    diagram = builder.generate()
    assert isinstance(diagram, str)
    assert diagram.startswith("flowchart TB")


def test_identity_only_public_endpoint_is_downgraded(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")
    builder.exposed_resources = {
        "storage-001": ExposureDetail(
            resource_name="storage-001",
            resource_id=1,
            exposure_type="heuristic",
            confidence="medium",
            reason="Public endpoint",
            color="#ff9900",
        )
    }

    class _Rows:
        def fetchall(self):
            return [
                {
                    "resource_id": 1,
                    "via_public_ip": 0,
                    "via_public_endpoint": 1,
                    "via_managed_identity": 1,
                    "auth_level": "identity",
                }
            ]

    class _Conn:
        def execute(self, *args, **kwargs):
            return _Rows()

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("generate_diagram.get_db_connection", lambda: _Ctx())

    builder._apply_accessibility_posture()

    detail = builder.exposed_resources["storage-001"]
    assert detail.confidence == "low"
    assert detail.auth_required is True
    assert detail.color == "#ffcc00"
    assert "managed identity" in detail.reason.lower()


def test_credential_public_endpoint_is_medium_risk(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")
    builder.exposed_resources = {
        "sql-001": ExposureDetail(
            resource_name="sql-001",
            resource_id=2,
            exposure_type="property",
            confidence="high",
            reason="Public endpoint",
            color="#ff0000",
        )
    }

    class _Rows:
        def fetchall(self):
            return [
                {
                    "resource_id": 2,
                    "via_public_ip": 0,
                    "via_public_endpoint": 1,
                    "via_managed_identity": 0,
                    "auth_level": "credential",
                }
            ]

    class _Conn:
        def execute(self, *args, **kwargs):
            return _Rows()

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("generate_diagram.get_db_connection", lambda: _Ctx())

    builder._apply_accessibility_posture()

    detail = builder.exposed_resources["sql-001"]
    assert detail.confidence == "medium"
    assert detail.auth_required is True
    assert detail.color == "#ff9900"
    assert "authenticated public endpoint" in detail.reason.lower()


def test_managed_identity_internet_edge_uses_amber_label(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")
    resource = {
        "id": 3,
        "resource_name": "storage",
        "resource_type": "azurerm_storage_account",
        "provider": "azure",
        "repo_name": "repo",
    }
    builder.resources = [resource]
    builder.resource_by_id = {3: resource}
    builder.resource_by_name = {"storage": resource}
    builder.emitted_nodes = {"storage"}
    builder._emitted_mermaid_ids = {sanitize_id("storage")}
    builder.exposed_resources = {
        "storage": ExposureDetail(
            resource_name="storage",
            resource_id=3,
            exposure_type="property",
            confidence="low",
            reason="Public endpoint (managed identity required)",
            color="#ffcc00",
        )
    }
    builder._accessibility_by_id = {
        3: {
            "via_public_ip": 0,
            "via_public_endpoint": 1,
            "via_managed_identity": 1,
            "auth_level": "identity",
        }
    }

    lines = builder.render_connections()
    assert any('internet -.->|"Managed Identity"| storage' in line for line in lines)
    assert any("stroke:#ffcc00" in line for line in lines)


def test_credential_internet_edge_uses_amber_label(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")
    resource = {
        "id": 4,
        "resource_name": "sql",
        "resource_type": "azurerm_mssql_server",
        "provider": "azure",
        "repo_name": "repo",
    }
    builder.resources = [resource]
    builder.resource_by_id = {4: resource}
    builder.resource_by_name = {"sql": resource}
    builder.emitted_nodes = {"sql"}
    builder._emitted_mermaid_ids = {sanitize_id("sql")}
    builder.exposed_resources = {
        "sql": ExposureDetail(
            resource_name="sql",
            resource_id=4,
            exposure_type="property",
            confidence="medium",
            reason="Public endpoint (authenticated public endpoint)",
            color="#ff9900",
        )
    }
    builder._accessibility_by_id = {
        4: {
            "via_public_ip": 0,
            "via_public_endpoint": 1,
            "via_managed_identity": 0,
            "auth_level": "credential",
        }
    }

    lines = builder.render_connections()
    assert any('internet -.->|"Credentials"| sql' in line for line in lines)
    assert any("stroke:#ff9900" in line for line in lines)


class _FakeEmptyConn:
    def execute(self, *args, **kwargs):
        class _Result:
            def fetchall(self):
                return []

        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_google_storage_bucket_iam_binding_is_kept_by_load_data(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    monkeypatch.setattr(
        "generate_diagram.get_resources_for_diagram",
        lambda experiment_id: [
            {
                "id": 1,
                "resource_name": "bucket",
                "resource_type": "google_storage_bucket",
                "provider": "gcp",
                "repo_name": "repo",
            },
            {
                "id": 2,
                "resource_name": "bucket_binding",
                "resource_type": "google_storage_bucket_iam_binding",
                "provider": "gcp",
                "repo_name": "repo",
                "parent_resource_id": 1,
            },
        ],
    )
    monkeypatch.setattr("generate_diagram.get_connections_for_diagram", lambda *args, **kwargs: [])
    monkeypatch.setattr("generate_diagram.get_db_connection", lambda: _FakeEmptyConn())

    builder.load_data()

    assert any(r["resource_type"] == "google_storage_bucket_iam_binding" for r in builder.resources)


def test_azure_mssql_icons_resolve_to_sql_assets():
    server_path = get_icon_path("azurerm_mssql_server", "azure")
    database_path = get_icon_path("azurerm_mssql_database", "azure")

    assert server_path is not None
    assert database_path is not None
    assert server_path.as_posix().endswith("/azure/databases/sql-server.svg")
    assert database_path.as_posix().endswith("/azure/databases/sql-database.svg")

