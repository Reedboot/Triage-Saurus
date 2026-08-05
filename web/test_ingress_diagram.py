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


def test_load_balancers_render_as_distinct_named_nodes():
    rows = [
        (
            name,
            "microsoft.network/loadbalancers",
            rg,
            "",
            False,
            "",
            f"/subscriptions/000/resourceGroups/{rg}/providers/Microsoft.Network/loadBalancers/{name}",
            False,
            None,
            False,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        )
        for name, rg in [
            ("shared", "rg-sf"),
            ("management", "rg-sf"),
            ("management_internal", "rg-sf"),
            ("management", "rg-sfha"),
            ("management_internal", "rg-sfha"),
            ("kubernetes-internal", "rg-aks"),
        ]
    ]

    mermaid = _build_ingress_diagram(rows)["mermaid"]

    for name, rg in [
        ("shared", "rg-sf"),
        ("management", "rg-sf"),
        ("management_internal", "rg-sf"),
        ("management", "rg-sfha"),
        ("management_internal", "rg-sfha"),
        ("kubernetes-internal", "rg-aks"),
    ]:
        node_id = _sanitise_node_id(f"{rg}_{name}")
        assert f"{node_id}[" in mermaid
        assert name in mermaid

    assert "Load_Balancer_ep_group" not in mermaid
    assert "blue-spoke-ukwest, green-spoke-ukwest" not in mermaid


def test_aks_node_resource_group_load_balancer_routes_to_service():
    cluster_name = "aks-external"
    cluster_rg = "rg-aks-external"
    lb_rg = "rg-aks-nodes-external"
    rows = [
        (
            cluster_name,
            "microsoft.containerservice/managedclusters",
            cluster_rg,
            "",
            False,
            "",
            f"/subscriptions/000/resourceGroups/{cluster_rg}/providers/Microsoft.ContainerService/managedClusters/{cluster_name}",
            False,
            None,
            False,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
        (
            "kubernetes-internal",
            "microsoft.network/loadbalancers",
            lb_rg,
            "",
            False,
            "",
            f"/subscriptions/000/resourceGroups/{lb_rg}/providers/Microsoft.Network/loadBalancers/kubernetes-internal",
            False,
            None,
            False,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
    ]
    route_rows = [
        (
            cluster_name,
            "prodyellow-ford",
            "car-ingress",
            "car.example.test",
            "/*",
            "private",
            "car-ford-image-cb-prdgreen-service",
            4000,
            "car-ford-image",
            None,
            cluster_rg,
            None,
        )
    ]

    mermaid = _build_ingress_diagram(rows, aks_route_rows=route_rows)["mermaid"]
    lb_node = _sanitise_node_id(f"{lb_rg}_kubernetes-internal")
    service_node = _sanitise_node_id(
        f"{cluster_rg}_aks_service_{cluster_name}_prodyellow-ford_car-ford-image-cb-prdgreen-service_4000"
    )
    assert any(
        line.startswith(f"    {lb_node} --> ")
        and "aks_ingress_" in line
        for line in mermaid.splitlines()
    ), mermaid
    assert any(
        line.endswith(f" --> {service_node}")
        and "aks_ingress_" in line
        for line in mermaid.splitlines()
    ), mermaid


def test_service_fabric_management_load_balancer_routes_to_each_sf_load_balancer():
    rg = "rg-sf"
    rows = [
        (
            "sf-cluster",
            "microsoft.servicefabric/clusters",
            rg,
            "",
            False,
            "",
            f"/subscriptions/000/resourceGroups/{rg}/providers/Microsoft.ServiceFabric/clusters/sf-cluster",
            False,
            None,
            False,
            None,
            None,
            '{"_extra":{"vnet_name":"sf-vnet","vnet_resource_group":"rg-sf","subnet_name":"service-fabric","subnet_id":"/subscriptions/000/resourceGroups/rg-sf/providers/Microsoft.Network/virtualNetworks/sf-vnet/subnets/service-fabric"},"properties":{}}',
            None,
            None,
        )
    ]
    for name in ("management", "shared", "stock", "fpscpu"):
        rows.append(
            (
                name,
                "microsoft.network/loadbalancers",
                rg,
                "",
                False,
                "",
                f"/subscriptions/000/resourceGroups/{rg}/providers/Microsoft.Network/loadBalancers/{name}",
                False,
                None,
                False,
                None,
                None,
                '{"properties":{}}',
                None,
                None,
            )
        )

    mermaid = _build_ingress_diagram(rows)["mermaid"]
    management_node = _sanitise_node_id(f"{rg}_management")
    for name in ("shared", "stock", "fpscpu"):
        target_node = _sanitise_node_id(f"{rg}_{name}")
        assert f'{management_node} -->|"Load balancing"| {target_node}' in mermaid
    subnet_start = mermaid.index('["Subnet: service-fabric"]')
    subnet_end = mermaid.index("        end", subnet_start)
    assert management_node in mermaid[subnet_start:subnet_end]


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
                    "target": "production-api-uksouth.azure-api.net",
                    "name": "production-api-uksouth",
                }
            ]),
            json.dumps({"properties": {"publicNetworkAccess": "Enabled"}}),
            None,
            None,
        ),
        (
            "production-api-uksouth",
            "microsoft.web/sites",
            "rg-backend",
            "production-api-uksouth.azure-api.net",
            False,
            "",
            "/subscriptions/000/resourceGroups/rg-backend/providers/Microsoft.Web/sites/production-api-uksouth",
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
    target_id = _sanitise_node_id("rg-backend_production-api-uksouth")

    assert source_id in mermaid
    assert target_id in mermaid
    assert f'{source_id} -->|"Routing"| {target_id}' in mermaid


def test_apim_service_fabric_backend_routes_to_cluster_load_balancer():
    rows = [
        (
            "main-yellow-api-uksouth",
            "microsoft.apimanagement/service",
            "rg-api",
            "main-yellow-api-uksouth.azure-api.net",
            True,
            "",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/main-yellow-api-uksouth",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
        (
            "stock",
            "microsoft.network/loadbalancers",
            "main-yellow-sfha-uksouth",
            "",
            False,
            "",
            "/subscriptions/000/resourceGroups/main-yellow-sfha-uksouth/providers/Microsoft.Network/loadBalancers/stock",
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

    diagram = _build_ingress_diagram(
        rows,
        apim_route_map={
            "main-yellow-api-uksouth": [
                "https://main-yellow-sfha-uksouth.cbinnovation.uk:19080",
            ],
        },
        apim_backend_rows=[
            {
                "apim_name": "main-yellow-api-uksouth",
                "backend_id": "main-yellow-sfha",
                "title": "main-yellow-sfha",
                "description": "Service Fabric backend",
                "url": "https://main-yellow-sfha-uksouth.cbinnovation.uk:19080",
                "protocol": "http",
            },
        ],
    )
    mermaid = diagram["mermaid"]

    backend_id = _sanitise_node_id(
        "main-yellow-api-uksouth::main-yellow-sfha"
    )
    load_balancer_id = _sanitise_node_id(
        "main-yellow-sfha-uksouth_stock"
    )
    assert f"{backend_id} --> {load_balancer_id}" in mermaid


def test_appgw_routes_keep_edges_for_multiple_apim_backend_pools():
    rows = [
        (
            "main-production-appgatewaycop-uksouth",
            "microsoft.network/applicationgateways",
            "rg-gateway",
            "",
            True,
            "",
            "/subscriptions/000/resourceGroups/rg-gateway/providers/Microsoft.Network/applicationGateways/gateway",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
        (
            "main-production-api-uksouth",
            "microsoft.apimanagement/service",
            "rg-api",
            "main-production-api-uksouth.azure-api.net",
            False,
            "Developer",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/main-production-api-uksouth",
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
    appgw_routes = [
        (
            "main-production-appgatewaycop-uksouth",
            "cop2new.clearbank.co.uk",
            json.dumps(["main-production-api-uksouth.azure-api.net"]),
            "cop-resource-server-apim",
            "resource-server-listener",
            "/*",
            "Https",
            None,
        ),
        (
            "main-production-appgatewaycop-uksouth",
            "payuknew.clearbank.co.uk",
            json.dumps(["main-production-api-uksouth.azure-api.net"]),
            "cop-auth-server-apim",
            "auth-server-listener",
            "/*",
            "Https",
            None,
        ),
    ]

    diagram = _build_ingress_diagram(
        rows,
        appgw_routes=appgw_routes,
        public_appgw_names={"main-production-appgatewaycop-uksouth"},
    )
    mermaid = diagram["mermaid"]

    target_id = "grp_APIM_Private"
    assert (
        f"{_sanitise_node_id('agpool_rg-gateway_main-production-appgatewaycop-uksouth_cop-resource-server-apim')} "
        f"--> {target_id}"
    ) in mermaid
    assert (
        f"{_sanitise_node_id('agpool_rg-gateway_main-production-appgatewaycop-uksouth_cop-auth-server-apim')} "
        f"--> {target_id}"
    ) in mermaid


def test_appgw_routes_target_explicit_apim_api_nodes():
    rows = [
        (
            "gateway",
            "microsoft.network/applicationgateways",
            "rg-gateway",
            "",
            True,
            "",
            "/subscriptions/000/resourceGroups/rg-gateway/providers/Microsoft.Network/applicationGateways/gateway",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
        (
            "apim",
            "microsoft.apimanagement/service",
            "rg-api",
            "apim.azure-api.net",
            False,
            "",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim",
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
    diagram = _build_ingress_diagram(
        rows,
        appgw_routes=[
            ("gateway", "api.example.test", json.dumps(["apim.azure-api.net"]), "orders", "listener", "/*", "Https", None),
        ],
        apim_api_rows=[{
            "apim_name": "apim",
            "api_name": "orders",
            "api_display_name": "Orders",
            "api_path": "/orders",
            "apim_resource_id": "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim",
            "backend_id": "orders-backend",
            "backend_url": "https://orders.example.test",
            "service_url": "https://apim.azure-api.net/orders",
        }],
    )
    mermaid = diagram["mermaid"]
    pool_id = _sanitise_node_id("agpool_rg-gateway_gateway_orders")
    api_id = _sanitise_node_id("rg-api_apim::orders")
    assert f"{pool_id} --> {api_id}" in mermaid
    assert f"{api_id} --> { _sanitise_node_id('grp_APIM_Private')}" in mermaid
