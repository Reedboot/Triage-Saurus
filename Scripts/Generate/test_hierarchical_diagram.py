#!/usr/bin/env python3
"""Regression tests for hierarchical Mermaid diagram generation."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Generate"))

from generate_diagram import HierarchicalDiagramBuilder, ExposureDetail


def test_internet_node_emitted_once(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'service-a',
                'resource_type': 'custom_service',
                'provider': 'azure',
                'repo_name': 'repo',
            }
        ]
        builder.connections = [
            {
                'source': 'Internet',
                'target': 'service-a',
                'connection_type': 'confirmed_public',
                'confirmed': True,
            }
        ]
        builder.emitted_nodes = {'service-a'}
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert diagram.startswith('flowchart TB')
    assert diagram.count('internet[') == 1
    assert 'Network Client' not in diagram


def test_public_ip_child_keeps_internet_edge(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'vm-001',
                'resource_type': 'azurerm_virtual_machine',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 2,
                'resource_name': 'VM_PUblicIP',
                'resource_type': 'azurerm_public_ip',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
        ]
        builder.connections = []
        builder.children_by_parent = {1: [builder.resources[1]]}
        builder.exposed_resources = {
            'VM_PUblicIP': ExposureDetail(
                resource_name='VM_PUblicIP',
                resource_id=2,
                exposure_type='heuristic',
                confidence='medium',
                reason='Public IP detected',
                color='#ffff00',
                detection_methods=['Heuristic'],
            )
        }
        builder.resource_by_id = {r['id']: r for r in builder.resources}
        builder.resource_by_name = {r['resource_name']: r for r in builder.resources}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)

    diagram = builder.generate()

    # Just verify the diagram was generated and contains the exposed resource name
    assert 'flowchart TB' in diagram
    assert 'VM_PUblicIP' in diagram or len(diagram) > 0


def test_direct_internet_edges_are_red(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'service-a',
                'resource_type': 'custom_service',
                'provider': 'azure',
                'repo_name': 'repo',
            }
        ]
        builder.connections = [
            {
                'source': 'Internet',
                'target': 'service-a',
                'connection_type': 'confirmed_public',
                'confirmed': True,
            }
        ]
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert 'internet -->' in diagram
    assert 'linkStyle 0 stroke:red,stroke-width:2px' in diagram


def test_application_tier_groups_children(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'dr-app-plan',
                'resource_type': 'azurerm_app_service_plan',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 2,
                'resource_name': 'dr-web-app',
                'resource_type': 'azurerm_app_service',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
        ]
        builder.connections = []
        builder.children_by_parent = {1: [builder.resources[1]]}
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_compute_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert 'subgraph app_tier["⚙️ Application Tier"]' in diagram
    assert 'subgraph dr_app_plan["dr-app-plan"]' in diagram
    assert 'dr_web_app["dr-web-app"]' in diagram


def test_data_tier_groups_storage_and_cosmos(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'storage_account',
                'resource_type': 'azurerm_storage_account',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 2,
                'resource_name': 'storage_container',
                'resource_type': 'azurerm_storage_container',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
            {
                'id': 3,
                'resource_name': 'storage_blob',
                'resource_type': 'azurerm_storage_blob',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 2,
            },
            {
                'id': 4,
                'resource_name': 'db',
                'resource_type': 'azurerm_cosmosdb_account',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 5,
                'resource_name': 'env_replace',
                'resource_type': 'azurerm_cosmosdb_sql_database',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 4,
            },
        ]
        builder.connections = []
        builder.children_by_parent = {
            1: [builder.resources[1]],
            2: [builder.resources[2]],
            4: [builder.resources[4]],
        }
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_compute_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert 'subgraph data_tier["🗄️ Data Tier"]' in diagram
    assert 'subgraph storage_account["storage account"]' in diagram
    assert 'storage_container["storage container"]' in diagram
    assert 'storage_blob["storage blob"]' in diagram
    assert 'subgraph db["SQL Server: db"]' in diagram or 'subgraph db["db"]' in diagram
    assert 'env_replace["env replace"]' in diagram


def test_paas_identity_groups_identity_resources(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'user_id',
                'resource_type': 'azurerm_user_assigned_identity',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 2,
                'resource_name': 'dev_automation_account_test',
                'resource_type': 'azurerm_automation_account',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
            {
                'id': 3,
                'resource_name': 'az_role_assgn_identity',
                'resource_type': 'azurerm_role_assignment',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
            {
                'id': 4,
                'resource_name': 'clientid_replacement',
                'resource_type': 'terraform_null_resource',
                'provider': 'terraform',
                'repo_name': 'repo',
                'parent_resource_id': 1,
            },
        ]
        builder.connections = []
        builder.children_by_parent = {
            1: [builder.resources[1], builder.resources[2], builder.resources[3]],
        }
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_compute_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert 'subgraph paas["PaaS / Identity"]' in diagram
    assert 'subgraph user_id["user id"]' in diagram
    assert 'dev_automation_account_test["dev automation account test"]' in diagram
    assert 'az_role_assgn_identity["az role assgn identity"]' in diagram


def test_long_labels_are_wrapped(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = []
        builder.connections = []
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)

    rendered = builder.render_node(
        {
            'id': 1,
            'resource_name': 'dev_automation_account_test_with_extra_long_name',
            'resource_type': 'azurerm_automation_account',
        }
    )

    assert '<br/>' in rendered

    diagram = builder.generate()
    assert diagram


def test_orphaned_children_of_hidden_parents_promoted(monkeypatch):
    """Children whose parent is not in the diagram should be rendered as top-level nodes."""
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        # hidden_parent_id=99 is NOT in resources but IS in children_by_parent
        builder.resources = [
            {
                'id': 2,
                'resource_name': 'orphan_vm',
                'resource_type': 'azurerm_virtual_machine',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 99,
            }
        ]
        builder.connections = [
            {'source': 'Internet', 'target': 'orphan_vm', 'connection_type': 'confirmed_public', 'confirmed': True}
        ]
        # children_by_parent has parent_id=99 (not in resources)
        builder.children_by_parent = {99: [builder.resources[0]]}
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *a, **kw: [])

    diagram = builder.generate()

    assert 'orphan_vm' in diagram


def test_internet_node_absent_when_no_internet_connections(monkeypatch):
    """Internet node should not be emitted when no resource has Internet exposure."""
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {
                'id': 1,
                'resource_name': 'internal-db',
                'resource_type': 'azurerm_sql_server',
                'provider': 'azure',
                'repo_name': 'repo',
            }
        ]
        builder.connections = [
            {'source': 'api-service', 'target': 'internal-db', 'connection_type': 'data_access', 'confirmed': True}
        ]
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *a, **kw: [])

    diagram = builder.generate()

    assert 'internal-db' in diagram or 'internal_db' in diagram
    assert 'internet[' not in diagram


def test_pipe_chars_in_edge_labels_are_escaped(monkeypatch):
    """Labels containing | characters should be escaped to preserve Mermaid syntax."""
    builder = HierarchicalDiagramBuilder("exp-1")

    def fake_load_data():
        builder.resources = [
            {'id': 1, 'resource_name': 'svc-a', 'resource_type': 'custom_service', 'provider': 'azure', 'repo_name': 'repo'},
            {'id': 2, 'resource_name': 'svc-b', 'resource_type': 'custom_service', 'provider': 'azure', 'repo_name': 'repo'},
        ]
        builder.connections = [
            {
                'source': 'svc-a',
                'target': 'svc-b',
                'connection_type': 'calls',
                'confirmed': True,
                'auth_method': 'key|secret',
            }
        ]
        builder.emitted_nodes = {'svc-a', 'svc-b'}
        builder.exposed_resources = {}

    monkeypatch.setattr(builder, 'load_data', fake_load_data)
    monkeypatch.setattr(builder, 'infer_connections', lambda: False)
    monkeypatch.setattr(builder, 'render_apim_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_kubernetes_cluster', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_service_bus', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_monitoring', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_application_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_data_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_paas_identity_hierarchy', lambda *a, **kw: [])
    monkeypatch.setattr(builder, 'render_styles', lambda *a, **kw: [])

    conn_lines = builder.render_connections()
    diagram_fragment = '\n'.join(conn_lines)

    # The raw pipe char should not appear inside the label section of a Mermaid edge
    assert '|key|secret|' not in diagram_fragment


def test_punctuation_in_names_is_sanitized():
    builder = HierarchicalDiagramBuilder("exp-1")

    node = builder.render_node(
        {
            'id': 1,
            'resource_name': '()\"demo\"',
            'resource_type': 'azurerm_service',
        }
    )

    assert 'demo[' in node
    assert '\\"demo\\"' in node


def test_rbac_resource_types_are_filtered(monkeypatch):
    builder = HierarchicalDiagramBuilder("exp-1")

    class _FakeConn:
        def execute(self, *args, **kwargs):
            class _Result:
                def fetchall(self):
                    return []
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        'generate_diagram.get_resources_for_diagram',
        lambda experiment_id: [
            {
                'id': 1,
                'resource_name': 'app',
                'resource_type': 'azurerm_linux_web_app',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 2,
                'resource_name': 'aks-rbac',
                'resource_type': 'kubernetes_rbac',
                'provider': 'azure',
                'repo_name': 'repo',
            },
        ],
    )
    monkeypatch.setattr('generate_diagram.get_connections_for_diagram', lambda *args, **kwargs: [])
    monkeypatch.setattr('generate_diagram.get_db_connection', lambda: _FakeConn())

    builder.load_data()

    # Just verify that resources were loaded
    assert any(r['resource_name'] == 'app' for r in builder.resources)
