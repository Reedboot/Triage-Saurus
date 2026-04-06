#!/usr/bin/env python3
"""Regression tests for hierarchical Mermaid diagram generation."""

from generate_hierarchical_diagram import HierarchicalDiagramBuilder


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
    monkeypatch.setattr(builder, 'render_compute_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_sql_hierarchy', lambda *args, **kwargs: [])
    monkeypatch.setattr(builder, 'render_node', lambda *args, **kwargs: '')
    monkeypatch.setattr(builder, 'render_styles', lambda *args, **kwargs: [])

    diagram = builder.generate()

    assert diagram.count('internet[') == 1
    assert 'Network Client' not in diagram
