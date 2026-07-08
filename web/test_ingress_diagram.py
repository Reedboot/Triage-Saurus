#!/usr/bin/env python3
"""Regression tests for subscription ingress Mermaid generation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from app import _build_ingress_diagram
from app import _resolve_routing_target_node_id
from app import _routing_lookup_key
from app import _sanitise_node_id
from app import _subscription_access_level


def test_network_subgraphs_split_by_vnet():
    rows = [
        (
            "blue-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
        (
            "green-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            2,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/green-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert mermaid.count('subgraph net_') >= 2
    assert '["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert '["🔒 Network: green-spoke-ukwest"]' in mermaid
    assert "NetworkBoundary" not in mermaid
    assert "blue-spoke-ukwest, green-spoke-ukwest" not in mermaid


def test_non_vnet_entry_points_do_not_create_unnamed_network_group():
    rows = [
        (
            "blue-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
        (
            "pip-public",
            "microsoft.network/publicipaddresses",
            "rg-network",
            "",
            True,
            "",
            2,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"ipAddress":"203.0.113.10"}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert '["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert "Networks / VNet" not in mermaid


def test_vmss_with_only_subnet_id_stays_inside_vnet_subgraph():
    rows = [
        (
            "app-vmss-01",
            "microsoft.compute/virtualmachinescalesets",
            "rg-app",
            "",
            False,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"_extra":{"subnet_id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert 'subgraph net_rg_network__blue_spoke_ukwest["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert 'app-vmss-01' in mermaid
    assert mermaid.index('subgraph net_rg_network__blue_spoke_ukwest["🔒 Network: blue-spoke-ukwest"]') < mermaid.index('app-vmss-01')
    assert mermaid.index('app-vmss-01') < mermaid.index('    end')


def test_subnet_subgraph_contains_network_resources():
    subnet_id = "/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"
    rows = [
        (
            "app",
            "microsoft.network/virtualnetworks/subnets",
            "rg-network",
            "",
            False,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"_extra":{"subnet_id":"%s","subnet_name":"app"}}' % subnet_id,
            None,
            None,
        ),
        (
            "app-vmss-01",
            "microsoft.compute/virtualmachinescalesets",
            "rg-app",
            "",
            False,
            "",
            2,
            False,
            None,
            False,
            None,
            None,
            '{"_extra":{"subnet_id":"%s"}}' % subnet_id,
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert 'subgraph net_rg_network__blue_spoke_ukwest["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert 'Subnet: app' in mermaid
    assert 'style sub_' in mermaid
    assert 'stroke:#94a3b8' in mermaid
    assert mermaid.index('Subnet: app') < mermaid.index('app-vmss-01')


def test_restricted_assets_are_not_classified_as_public():
    asset = {"is_public": True, "is_restricted": True}

    assert _subscription_access_level(asset) == "IP Restricted"


def test_routing_lookup_key_strips_production_suffixes():
    assert _routing_lookup_key("apimanagement-production") == "apimanagement"
    assert _routing_lookup_key("stsapi-production") == "stsapi"


def test_routing_target_resolution_matches_production_hosts():
    node_by_name_normalized = {
        _routing_lookup_key("apimanagement"): "apim-node",
        _routing_lookup_key("stsapi"): "sts-node",
    }

    assert _resolve_routing_target_node_id(
        {"target": "apimanagement-production.azure-api.net", "name": "apimanagement-production"},
        node_by_name_normalized=node_by_name_normalized,
    ) == "apim-node"

    assert _resolve_routing_target_node_id(
        {"target": "stsapi-production.azurewebsites.net", "name": "stsapi-production"},
        node_by_name_normalized=node_by_name_normalized,
    ) == "sts-node"


def test_apim_routing_targets_render_explicit_backend_edge():
    rows = [
        (
            "cop-resource-server-apim",
            "microsoft.apimanagement/service",
            "rg-api",
            "cop-resource-server-apim.azure-api.net",
            True,
            "",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/cop-resource-server-apim",
            False,
            None,
            False,
            None,
            json.dumps([
                {
                    "target": "cbuk-core-prodgreen-api-uksouth.azure-api.net",
                    "name": "cbuk-core-prodgreen-api-uksouth",
                }
            ]),
            json.dumps({"properties": {"publicNetworkAccess": "Enabled"}}),
            None,
            None,
        ),
        (
            "cbuk-core-prodgreen-api-uksouth",
            "microsoft.web/sites",
            "rg-backend",
            "cbuk-core-prodgreen-api-uksouth.azure-api.net",
            False,
            "",
            "/subscriptions/000/resourceGroups/rg-backend/providers/Microsoft.Web/sites/cbuk-core-prodgreen-api-uksouth",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    source_id = _sanitise_node_id("grp_APIM_Public")
    target_id = _sanitise_node_id("rg-backend_cbuk-core-prodgreen-api-uksouth")

    assert source_id in mermaid
    assert target_id in mermaid
    assert f'{source_id} -->|"Routing"| {target_id}' in mermaid
