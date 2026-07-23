#!/usr/bin/env python3
"""Regression tests for subscription diagram network extraction."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web"))

from subscription_diagram_helpers import (  # type: ignore
    build_subscription_diagrams_by_rg,
    subscription_assets_from_rows,
    subscription_asset_tier,
    subscription_is_allowlist_target,
    subscription_node_id,
)


def _friendly_type(_rtype: str) -> str:
    return "friendly"


def test_extracts_aks_vnet_and_subnet_from_agent_pool_profiles():
    subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/aks-vnet/subnets/aks-subnet"
    rows = [
        (
            "aks-prod",
            "Microsoft.ContainerService/managedClusters",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.ContainerService/managedClusters/aks-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "agentPoolProfiles": [
                        {"vnetSubnetID": subnet_id},
                    ]
                }
            }),
            None,
        )
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    aks = next(asset for asset in assets if asset["name"] == "aks-prod")

    assert aks["subnet_id"] == subnet_id
    assert aks["subnet_name"] == "aks-subnet"
    assert aks["vnet_name"] == "aks-vnet"
    assert aks["parent_vnet_name"] == "aks-vnet"


def test_extracts_app_service_vnet_and_subnet_from_site_config():
    subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/app-vnet/subnets/app-subnet"
    rows = [
        (
            "web-prod",
            "Microsoft.Web/sites",
            "rg-app",
            "web-prod.azurewebsites.net",
            True,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Web/sites/web-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "siteConfig": {
                        "virtualNetworkSubnetId": subnet_id,
                    }
                }
            }),
            None,
        )
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    site = next(asset for asset in assets if asset["name"] == "web-prod")

    assert site["subnet_id"] == subnet_id
    assert site["subnet_name"] == "app-subnet"
    assert site["vnet_name"] == "app-vnet"
    assert site["parent_vnet_name"] == "app-vnet"


def test_extracts_app_service_environment_vnet_and_subnet():
    subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/app-vnet/subnets/app-subnet"
    rows = [
        (
            "production-shared-uksouth",
            "Microsoft.Web/hostingenvironments",
            "rg-net",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Web/hostingenvironments/production-shared-uksouth",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "subnet": {
                        "id": subnet_id,
                    }
                }
            }),
            None,
        )
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    ase = next(asset for asset in assets if asset["name"] == "production-shared-uksouth")

    assert ase["tier"] == "network"
    assert ase["subnet_id"] == subnet_id
    assert ase["subnet_name"] == "app-subnet"
    assert ase["vnet_name"] == "app-vnet"
    assert ase["parent_vnet_name"] == "app-vnet"


def test_extracts_service_fabric_vnet_and_subnet_from_node_types():
    subnet_id = "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Network/virtualNetworks/sf-vnet/subnets/sf-subnet"
    rows = [
        (
            "sf-vnet",
            "Microsoft.Network/virtualNetworks",
            "rg-net",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/sf-vnet",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
        (
            "sf-subnet",
            "Microsoft.Network/virtualNetworks/subnets",
            "rg-net",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/sf-vnet/subnets/sf-subnet",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
        (
            "sf-cluster",
            "Microsoft.ServiceFabric/clusters",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.ServiceFabric/clusters/sf-cluster",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "nodeTypes": [
                        {
                            "subnetId": subnet_id,
                        }
                    ]
                }
            }),
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    sf = next(asset for asset in assets if asset["name"] == "sf-cluster")

    assert sf["subnet_id"] == subnet_id
    assert sf["subnet_name"] == "sf-subnet"
    assert sf["vnet_name"] == "sf-vnet"
    assert sf["parent_vnet_name"] == "sf-vnet"


def test_app_service_environment_renders_inside_vnet_and_subnet():
    subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/app-vnet/subnets/app-subnet"
    rows = [
        (
            "app-vnet",
            "Microsoft.Network/virtualNetworks",
            "rg-net",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/app-vnet",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {},
                "_extra": {
                    "subnets": [
                        {
                            "id": subnet_id,
                            "name": "app-subnet",
                            "properties": {},
                        }
                    ]
                }
            }),
            None,
        ),
        (
            "production-shared-uksouth",
            "Microsoft.Web/hostingenvironments",
            "rg-net",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Web/hostingenvironments/production-shared-uksouth",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "subnet": {
                        "id": subnet_id,
                    }
                }
            }),
            None,
        ),
        (
            "functions_windows",
            "Microsoft.Web/serverfarms",
            "rg-net",
            "functions_windows.production-shared-uksouth.appserviceenvironment.net",
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Web/serverfarms/functions_windows",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {}
            }),
            None,
        ),
    ]

    diagrams = build_subscription_diagrams_by_rg(
        "Test Subscription",
        "production",
        rows,
        sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
        friendly_type=lambda t: t,
        get_icon_path=lambda t: None,
        normalize_attack_paths=lambda *args, **kwargs: [],
    )

    view = next(d["views"]["connectivity"] for d in diagrams if d["rg"] == "rg-net")
    assets = subscription_assets_from_rows(rows, _friendly_type)
    vnet = next(asset for asset in assets if asset["name"] == "app-vnet")
    ase = next(asset for asset in assets if asset["name"] == "production-shared-uksouth")
    plan = next(asset for asset in assets if asset["name"] == "functions_windows")

    vnet_node_id = subscription_node_id(vnet, lambda s: s.replace("/", "_").replace("-", "_"))
    ase_node_id = subscription_node_id(ase, lambda s: s.replace("/", "_").replace("-", "_"))
    plan_node_id = subscription_node_id(plan, lambda s: s.replace("/", "_").replace("-", "_"))
    subnet_node_id = subscription_node_id(
        next(asset for asset in assets if asset["name"] == "app-subnet"),
        lambda s: s.replace("/", "_").replace("-", "_"),
    )

    assert f'{vnet_node_id} -->|"contains"| {subnet_node_id}' in view["mermaid"]
    assert f'{subnet_node_id} -->|"in subnet"| {ase_node_id}' in view["mermaid"]
    assert plan["parent_vnet_name"] == "app-vnet"
    assert plan["subnet_name"] == "app-subnet"
    assert plan["subnet_id"] == subnet_id
    assert f'{subnet_node_id} -->|"in subnet"| {plan_node_id}' in view["mermaid"]


def test_service_fabric_cluster_renders_inside_vnet_and_subnet():
    subnet_id = "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Network/virtualNetworks/sf-vnet/subnets/sf-subnet"
    rows = [
        (
            "sf-vnet",
            "Microsoft.Network/virtualNetworks",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Network/virtualNetworks/sf-vnet",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
        (
            "sf-subnet",
            "Microsoft.Network/virtualNetworks/subnets",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Network/virtualNetworks/sf-vnet/subnets/sf-subnet",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
        (
            "sf-cluster",
            "Microsoft.ServiceFabric/clusters",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.ServiceFabric/clusters/sf-cluster",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "nodeTypes": [
                        {
                            "subnetId": subnet_id,
                        }
                    ]
                }
            }),
            None,
        ),
    ]

    diagrams = build_subscription_diagrams_by_rg(
        "Test Subscription",
        "production",
        rows,
        sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
        friendly_type=lambda t: t,
        get_icon_path=lambda t: None,
        normalize_attack_paths=lambda *args, **kwargs: [],
    )

    view = next(d["views"]["connectivity"] for d in diagrams if d["rg"] == "rg-app")
    assets = subscription_assets_from_rows(rows, _friendly_type)
    vnet = next(asset for asset in assets if asset["name"] == "sf-vnet")
    subnet = next(asset for asset in assets if asset["name"] == "sf-subnet")
    sf = next(asset for asset in assets if asset["name"] == "sf-cluster")

    vnet_node_id = subscription_node_id(vnet, lambda s: s.replace("/", "_").replace("-", "_"))
    subnet_node_id = subscription_node_id(subnet, lambda s: s.replace("/", "_").replace("-", "_"))
    sf_node_id = subscription_node_id(sf, lambda s: s.replace("/", "_").replace("-", "_"))

    assert f'{vnet_node_id} -->|"contains"| {subnet_node_id}' in view["mermaid"]
    assert f'{subnet_node_id} -->|"in subnet"| {sf_node_id}' in view["mermaid"]


def test_machine_learning_workspace_is_backend_and_allowlist_target():
    assert subscription_asset_tier("Microsoft.MachineLearningServices/workspaces", "ml-prod") == "backend"
    assert subscription_is_allowlist_target({
        "arm_type": "Microsoft.MachineLearningServices/workspaces",
    }) is True


def test_extracts_vmss_gateway_and_internal_lb_subnets():
    vmss_subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vmss-vnet/subnets/vmss-subnet"
    appgw_subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/appgw-vnet/subnets/appgw-subnet"
    lb_subnet_id = "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/lb-vnet/subnets/lb-subnet"
    rows = [
        (
            "vmss-prod",
            "Microsoft.Compute/virtualMachineScaleSets",
            "rg-app",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "virtualMachineProfile": {
                        "networkProfile": {
                            "networkInterfaceConfigurations": [
                                {
                                    "ipConfigurations": [
                                        {"subnet": {"id": vmss_subnet_id}},
                                    ]
                                }
                            ]
                        }
                    }
                }
            }),
            None,
        ),
        (
            "appgw-prod",
            "Microsoft.Network/applicationGateways",
            "rg-net",
            None,
            True,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "gatewayIPConfigurations": [
                        {"properties": {"subnet": {"id": appgw_subnet_id}}}
                    ]
                }
            }),
            None,
        ),
        (
            "ilb-prod",
            "Microsoft.Network/loadBalancers",
            "rg-net",
            None,
            True,
            None,
            "/subscriptions/000/resourceGroups/rg-net/providers/Microsoft.Network/loadBalancers/ilb-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "frontendIPConfigurations": [
                        {"properties": {"subnet": {"id": lb_subnet_id}}}
                    ]
                }
            }),
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    vmss = next(asset for asset in assets if asset["name"] == "vmss-prod")
    appgw = next(asset for asset in assets if asset["name"] == "appgw-prod")
    ilb = next(asset for asset in assets if asset["name"] == "ilb-prod")

    assert vmss["subnet_id"] == vmss_subnet_id
    assert vmss["subnet_name"] == "vmss-subnet"
    assert vmss["vnet_name"] == "vmss-vnet"

    assert appgw["subnet_id"] == appgw_subnet_id
    assert appgw["subnet_name"] == "appgw-subnet"
    assert appgw["vnet_name"] == "appgw-vnet"

    assert ilb["subnet_id"] == lb_subnet_id
    assert ilb["subnet_name"] == "lb-subnet"
    assert ilb["vnet_name"] == "lb-vnet"


def test_servicefabric_cluster_links_matching_vmss_nodes():
    rows = [
        (
            "core-sh2",
            "Microsoft.ServiceFabric/clusters",
            "core-sh2-uksouth",
            None,
            False,
            None,
            "/subscriptions/000/resourceGroups/core-sh2-uksouth/providers/Microsoft.ServiceFabric/clusters/core-sh2",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "nodeTypes": [
                    {"name": "svc1"},
                    {"name": "system1"},
                ]
            }),
            None,
        ),
        (
            "svc1",
            "Microsoft.Compute/virtualMachineScaleSets",
            "core-sh2-uksouth",
            None,
            False,
            "Standard_D4lds_v5",
            "/subscriptions/000/resourceGroups/core-sh2-uksouth/providers/Microsoft.Compute/virtualMachineScaleSets/svc1",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
        (
            "system1",
            "Microsoft.Compute/virtualMachineScaleSets",
            "core-sh2-uksouth",
            None,
            False,
            "Standard_D2s_v3",
            "/subscriptions/000/resourceGroups/core-sh2-uksouth/providers/Microsoft.Compute/virtualMachineScaleSets/system1",
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    vmss = next(asset for asset in assets if asset["name"] == "svc1")
    assert vmss["servicefabric_cluster_name"] == "core-sh2"

    diagrams = build_subscription_diagrams_by_rg(
        "pipeline-customer-production",
        "production",
        rows,
        sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
        friendly_type=lambda t: t,
        get_icon_path=lambda t: None,
        normalize_attack_paths=lambda *args, **kwargs: [],
    )

    view = next(d["views"]["connectivity"] for d in diagrams if d["rg"] == "core-sh2-uksouth")
    cluster_node_id = subscription_node_id(
        next(asset for asset in assets if asset["name"] == "core-sh2"),
        lambda s: s.replace("/", "_").replace("-", "_"),
    )
    vmss_node_id = subscription_node_id(vmss, lambda s: s.replace("/", "_").replace("-", "_"))

    assert f'{cluster_node_id} -->|"contains"| {vmss_node_id}' in view["mermaid"]


def test_collapses_apim_public_ip_into_apim_asset():
    public_ip_id = "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.Network/publicIPAddresses/apim-public"
    rows = [
        (
            "apim-prod",
            "Microsoft.ApiManagement/service",
            "rg-api",
            "apim.example.com",
            True,
            "Developer",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "ipConfigurations": [
                        {
                            "properties": {
                                "publicIPAddress": {"id": public_ip_id},
                            }
                        }
                    ]
                }
            }),
            None,
        ),
        (
            "apim-public",
            "Microsoft.Network/publicIPAddresses",
            "rg-api",
            None,
            True,
            "Standard",
            public_ip_id,
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {"ipAddress": "20.30.40.50"}}),
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    apim = next(asset for asset in assets if asset["name"] == "apim-prod")

    assert all(asset["name"] != "apim-public" for asset in assets)
    assert apim["associated_public_ips"] == ["20.30.40.50"]
    assert apim["public_ips"] == ["20.30.40.50"]


def test_apim_named_public_ip_is_not_rendered_as_a_node():
    public_ip_id = "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.Network/publicIPAddresses/apim_public_ip"
    rows = [
        (
            "apim-prod",
            "Microsoft.ApiManagement/service",
            "rg-api",
            "apim.example.com",
            True,
            "Developer",
            "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod",
            False,
            None,
            False,
            None,
            None,
            json.dumps({
                "properties": {
                    "ipConfigurations": [
                        {
                            "properties": {
                                "publicIPAddress": {"id": public_ip_id},
                            }
                        }
                    ]
                }
            }),
            None,
        ),
        (
            "apim_public_ip",
            "Microsoft.Network/publicIPAddresses",
            "rg-api",
            None,
            True,
            "Standard",
            public_ip_id,
            False,
            None,
            False,
            None,
            None,
            json.dumps({"properties": {"ipAddress": "20.30.40.50"}}),
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, _friendly_type)
    assert all(asset["name"] != "apim_public_ip" for asset in assets)
