#!/usr/bin/env python3
"""Regression tests for hierarchical Mermaid diagram generation."""

from generate_hierarchical_diagram import HierarchicalDiagramBuilder, ExposureDetail


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

    assert 'internet[/' in diagram
    assert 'internet -.->|Public IP detected| VM_PUblicIP' in diagram


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
                'resource_name': 'db',
                'resource_type': 'azurerm_cosmosdb_account',
                'provider': 'azure',
                'repo_name': 'repo',
            },
            {
                'id': 4,
                'resource_name': 'env_replace',
                'resource_type': 'azurerm_cosmosdb_sql_database',
                'provider': 'azure',
                'repo_name': 'repo',
                'parent_resource_id': 3,
            },
        ]
        builder.connections = []
        builder.children_by_parent = {
            1: [builder.resources[1]],
            3: [builder.resources[3]],
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
    assert 'subgraph storage_account["storage_account"]' in diagram
    assert 'storage_container["storage_container"]' in diagram
    assert 'subgraph db["SQL Server: db"]' in diagram or 'subgraph db["db"]' in diagram
    assert 'env_replace["env_replace"]' in diagram


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

    assert 'subgraph paas["PaaS / Identity"]' in diagram
    assert 'subgraph user_id["user_id"]' in diagram
    assert 'dev_automation_account_test["dev_automation_account_test"]' in diagram
    assert 'az_role_assgn_identity["az_role_assgn_identity"]' in diagram
    assert 'clientid_replacement["clientid_replacement"]' in diagram
