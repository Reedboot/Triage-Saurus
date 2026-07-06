#!/usr/bin/env python3
"""Regression tests for subscription diagram network extraction."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web"))

from subscription_diagram_helpers import subscription_assets_from_rows  # type: ignore


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
