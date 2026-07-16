#!/usr/bin/env python3
"""Seed CozoDB with a synthetic Azure subscription.

The generated names are intentionally generic and ecommerce-themed, and the
script refuses to reuse any existing subscription resource names, resource
groups, or FQDNs already present in the database.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PERSIST_DIR = REPO_ROOT / "Scripts" / "Persist"
if str(PERSIST_DIR) not in sys.path:
    sys.path.insert(0, str(PERSIST_DIR))

from db_helpers import _ensure_schema  # type: ignore


DEFAULT_BRAND = "marketlane"
DEFAULT_DISPLAY_NAME = "marketlane-demo"
DEFAULT_ENVIRONMENT = "dev"
DEFAULT_STATE = "Enabled"
DEFAULT_TENANT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "triage-saurus:dummy-azure:tenant"))
DEFAULT_SUBSCRIPTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "triage-saurus:dummy-azure:subscription"))
DEFAULT_DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"



@dataclass(frozen=True)
class AssetSpec:
    key: str
    name: str
    resource_group: str
    arm_type: str
    location: str | None = None
    sku: str | None = None
    fqdn: str | None = None
    is_public: int = 0
    is_restricted: int = 0
    ip_restrictions: str | None = None
    endpoints: str | None = None
    auth_methods: str | None = None
    waf_mode: str | None = None
    pipeline_tag: str | None = None
    tags: dict[str, str] | None = None
    raw_json: dict[str, Any] | None = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _arm_id(subscription_id: str, resource_group: str, resource_type: str, suffix: str) -> str:
    return f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/{resource_type}/{suffix}"


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _reject_forbidden(value: str, label: str) -> None:
    lowered = value.lower()



def _load_existing_names(conn: sqlite3.Connection) -> dict[str, set[str]]:
    existing: dict[str, set[str]] = {
        "names": set(),
        "resource_groups": set(),
        "fqdns": set(),
    }
    for row in conn.execute("SELECT COALESCE(display_name, '') FROM subscriptions"):
        if row[0]:
            existing["names"].add(row[0].lower())
    for row in conn.execute("SELECT name, resource_group, COALESCE(fqdn, '') FROM provisioned_assets"):
        if row[0]:
            existing["names"].add(row[0].lower())
        if row[1]:
            existing["resource_groups"].add(row[1].lower())
        if row[2]:
            existing["fqdns"].add(row[2].lower())
    return existing


def _ensure_no_collisions(existing: dict[str, set[str]], subscription_name: str, assets: list[AssetSpec]) -> None:
    if subscription_name.lower() in existing["names"]:
        raise ValueError(f"Subscription name already exists in CozoDB: {subscription_name}")
    for asset in assets:
        _reject_forbidden(asset.name, f"asset name {asset.key}")
        _reject_forbidden(asset.resource_group, f"resource group {asset.key}")
        if asset.fqdn:
            _reject_forbidden(asset.fqdn, f"fqdn {asset.key}")
        if asset.name.lower() in existing["names"]:
            raise ValueError(f"Asset name already exists in CozoDB: {asset.name}")
        if asset.resource_group.lower() in existing["resource_groups"]:
            raise ValueError(f"Resource group already exists in CozoDB: {asset.resource_group}")
        if asset.fqdn and asset.fqdn.lower() in existing["fqdns"]:
            raise ValueError(f"FQDN already exists in CozoDB: {asset.fqdn}")


def _build_assets(brand: str, subscription_id: str | None = None) -> list[AssetSpec]:
    rg = {
        "edge": f"rg-{brand}-edge",
        "network": f"rg-{brand}-network",
        "app": f"rg-{brand}-app",
        "data": f"rg-{brand}-data",
        "ops": f"rg-{brand}-ops",
        "security": f"rg-{brand}-security",
    }

    return [
        AssetSpec(
            key="vnet",
            name=f"vnet-{brand}-core",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={
                "properties": {
                    "addressSpace": {"addressPrefixes": ["10.40.0.0/16"]},
                    "subnets": [
                        {"name": f"snet-{brand}-edge", "properties": {"addressPrefix": "10.40.0.0/24"}},
                        {"name": f"snet-{brand}-ingress", "properties": {"addressPrefix": "10.40.1.0/24"}},
                        {"name": f"snet-{brand}-bastion", "properties": {"addressPrefix": "10.40.2.0/24"}},
                        {"name": f"snet-{brand}-ops", "properties": {"addressPrefix": "10.40.3.0/24"}},
                        {"name": f"snet-{brand}-app", "properties": {"addressPrefix": "10.40.4.0/24"}},
                        {"name": f"snet-{brand}-data", "properties": {"addressPrefix": "10.40.5.0/24"}},
                    ],
                },
            },
        ),
        AssetSpec(
            key="subnet_edge",
            name=f"snet-{brand}-edge",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"addressPrefix": "10.40.0.0/24"}},
        ),
        AssetSpec(
            key="subnet_ingress",
            name=f"snet-{brand}-ingress",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"addressPrefix": "10.40.1.0/24"}},
        ),
        AssetSpec(
            key="subnet_bastion",
            name=f"snet-{brand}-bastion",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"addressPrefix": "10.40.2.0/24"}},
        ),
        AssetSpec(
            key="subnet_ops",
            name=f"snet-{brand}-ops",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"addressPrefix": "10.40.3.0/24"}},
        ),
        AssetSpec(
            key="subnet_app",
            name=f"snet-{brand}-app",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"addressPrefix": "10.40.4.0/24"}},
        ),
        AssetSpec(
            key="subnet_apim",
            name=f"snet-{brand}-apim",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"addressPrefix": "10.40.6.0/24"}},
        ),
        AssetSpec(
            key="subnet_aks",
            name=f"snet-{brand}-aks",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"addressPrefix": "10.40.7.0/24"}},
        ),
        AssetSpec(
            key="subnet_ase",
            name=f"snet-{brand}-ase",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"addressPrefix": "10.40.9.0/24"}},
        ),
        AssetSpec(
            key="subnet_appsvc",
            name=f"snet-{brand}-appsvc",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"addressPrefix": "10.40.8.0/24"}},
        ),
        AssetSpec(
            key="subnet_data",
            name=f"snet-{brand}-data",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/virtualNetworks/subnets",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"addressPrefix": "10.40.5.0/24"}},
        ),
        AssetSpec(
            key="nsg_app",
            name=f"nsg-{brand}-app",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/networkSecurityGroups",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"securityRules": []}},
        ),
        AssetSpec(
            key="nsg_ingress",
            name=f"nsg-{brand}-ingress",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/networkSecurityGroups",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"securityRules": []}},
        ),
        AssetSpec(
            key="route_app",
            name=f"rt-{brand}-app",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/routeTables",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"routes": []}},
        ),
        AssetSpec(
            key="route_ingress",
            name=f"rt-{brand}-ingress",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/routeTables",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"routes": []}},
        ),
        AssetSpec(
            key="pip_edge",
            name=f"pip-{brand}-edge",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/publicIPAddresses",
            location="uksouth",
            sku="Standard",
            fqdn=f"edge.{brand}-retail.com",
            is_public=1,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"publicIPAllocationMethod": "Static", "dnsSettings": {"domainNameLabel": f"{brand}-edge"}}},
        ),
        AssetSpec(
            key="pip_firewall",
            name=f"pip-{brand}-fw",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/publicIPAddresses",
            location="uksouth",
            sku="Standard",
            fqdn=f"fw.{brand}-retail.com",
            is_public=1,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"publicIPAllocationMethod": "Static", "dnsSettings": {"domainNameLabel": f"{brand}-fw"}}},
        ),
        AssetSpec(
            key="pip_bastion",
            name=f"pip-{brand}-bastion",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/publicIPAddresses",
            location="uksouth",
            sku="Standard",
            fqdn=f"bastion.{brand}-retail.com",
            is_public=1,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"publicIPAllocationMethod": "Static", "dnsSettings": {"domainNameLabel": f"{brand}-bastion"}}},
        ),
        AssetSpec(
            key="appgw_edge",
            name=f"agw-{brand}-edge",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways",
            location="uksouth",
            sku="WAF_v2",
            fqdn=f"shop.{brand}-retail.com",
            is_public=1,
            waf_mode="Prevention",
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "frontendIPConfigurations": [
                        {"name": "public", "properties": {"publicIPAddress": {"id": f"pip-{brand}-edge"}}}
                    ],
                    "backendAddressPools": [
                        {"name": "web", "properties": {"backendAddresses": [{"fqdn": f"store.{brand}-retail.azurewebsites.net"}]}}
                    ],
                }
            },
        ),
        AssetSpec(
            key="appgw_listener_web",
            name=f"listener-{brand}-web",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/httpListeners",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"hostName": f"shop.{brand}-retail.com", "protocol": "Https"}},
        ),
        AssetSpec(
            key="appgw_listener_api",
            name=f"listener-{brand}-api",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/httpListeners",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"hostName": f"api.{brand}-retail.com", "protocol": "Https"}},
        ),
        AssetSpec(
            key="appgw_backend_web",
            name=f"bhs-{brand}-web",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/backendHttpSettingsCollection",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"port": 443, "protocol": "Https", "probe": {"id": f"probe-{brand}-web"}}},
        ),
        AssetSpec(
            key="appgw_backend_api",
            name=f"bhs-{brand}-api",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/backendHttpSettingsCollection",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"port": 443, "protocol": "Https", "probe": {"id": f"probe-{brand}-api"}}},
        ),
        AssetSpec(
            key="appgw_probe_web",
            name=f"probe-{brand}-web",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/probes",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"protocol": "Https", "path": "/health"}},
        ),
        AssetSpec(
            key="appgw_probe_api",
            name=f"probe-{brand}-api",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/probes",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"protocol": "Https", "path": "/healthz"}},
        ),
        AssetSpec(
            key="appgw_rule_web",
            name=f"rule-{brand}-web",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/requestRoutingRules",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"ruleType": "Basic", "httpListener": {"id": f"listener-{brand}-web"}, "backendHttpSettings": {"id": f"bhs-{brand}-web"}}},
        ),
        AssetSpec(
            key="appgw_rule_api",
            name=f"rule-{brand}-api",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/applicationGateways/requestRoutingRules",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"ruleType": "Basic", "httpListener": {"id": f"listener-{brand}-api"}, "backendHttpSettings": {"id": f"bhs-{brand}-api"}}},
        ),
        AssetSpec(
            key="waf_policy",
            name=f"waf-{brand}-edge",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"policySettings": {"mode": "Prevention"}, "customRules": []}},
        ),
        AssetSpec(
            key="load_balancer",
            name=f"lb-{brand}-ingress",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/loadBalancers",
            location="uksouth",
            sku="Standard",
            fqdn=f"lb.{brand}-retail.com",
            is_public=1,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "frontendIPConfigurations": [
                        {"name": "public", "properties": {"publicIPAddress": {"id": f"pip-{brand}-edge"}}}
                    ],
                    "backendAddressPools": [
                        {"name": "app", "properties": {"loadBalancerBackendAddresses": [{"fqdn": f"store.{brand}-retail.azurewebsites.net"}]}}
                    ],
                }
            },
        ),
        AssetSpec(
            key="traffic_manager",
            name=f"tm-{brand}-edge",
            resource_group=rg["edge"],
            arm_type="Microsoft.Network/trafficManagerProfiles",
            location="global",
            sku="Standard",
            fqdn=f"tm.{brand}-retail.com",
            is_public=1,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"profileStatus": "Enabled", "trafficRoutingMethod": "Performance"}},
        ),
        AssetSpec(
            key="firewall",
            name=f"fw-{brand}-core",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/azureFirewalls",
            location="uksouth",
            sku="AZFW_VNet",
            fqdn=f"fw.{brand}-retail.local",
            is_public=1,
            is_restricted=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={
                "properties": {
                    "ipConfigurations": [
                        {"name": "cfg1", "properties": {"publicIPAddress": {"id": f"pip-{brand}-fw"}}}
                    ],
                    "threatIntelMode": "Alert",
                }
            },
        ),
        AssetSpec(
            key="bastion",
            name=f"bas-{brand}-core",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/bastionHosts",
            location="uksouth",
            sku="Standard",
            fqdn=f"bas.{brand}-retail.local",
            is_public=1,
            tags={"brand": brand, "tier": "network"},
            raw_json={
                "properties": {
                    "ipConfigurations": [
                        {"name": "bastion", "properties": {"publicIPAddress": {"id": f"pip-{brand}-bastion"}}}
                    ]
                }
            },
        ),
        AssetSpec(
            key="plan_web",
            name=f"asp-{brand}-web",
            resource_group=rg["app"],
            arm_type="Microsoft.Web/serverfarms",
            location="uksouth",
            sku="P1v3",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "properties": {
                    "reserved": True,
                    "hostingEnvironmentProfile": {
                        "id": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-app/providers/Microsoft.Web/hostingEnvironments/ase-{brand}-shared"
                    },
                }
            },
        ),
        AssetSpec(
            key="ase",
            name=f"ase-{brand}-shared",
            resource_group=rg["app"],
            arm_type="Microsoft.Web/hostingEnvironments",
            location="uksouth",
            sku="ASEv3",
            fqdn=f"ase-{brand}-shared.uksouth.appserviceenvironment.net",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "properties": {
                    "internalLoadBalancingMode": "Web",
                    "virtualNetwork": {
                        "id": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core"
                    },
                    "subnet": {
                        "id": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-ase"
                    },
                }
            },
        ),
        AssetSpec(
            key="web_store",
            name=f"store-{brand}",
            resource_group=rg["app"],
            arm_type="Microsoft.Web/sites",
            location="uksouth",
            sku="P1v3",
            fqdn=f"store.{brand}-retail.azurewebsites.net",
            is_public=1,
            auth_methods="managed_identity;openid_connect",
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "kind": "app,linux",
                "properties": {
                    "defaultHostName": f"store.{brand}-retail.azurewebsites.net",
                    "httpsOnly": True,
                    "serverFarmId": f"asp-{brand}-web",
                    "publicNetworkAccess": "Enabled",
                    "virtualNetworkSubnetId": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-appsvc",
                },
            },
        ),
        AssetSpec(
            key="web_slot",
            name=f"store-{brand}-stage",
            resource_group=rg["app"],
            arm_type="Microsoft.Web/sites/slots",
            location="uksouth",
            sku="P1v3",
            fqdn=f"stage.store.{brand}-retail.azurewebsites.net",
            is_public=1,
            auth_methods="managed_identity",
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "properties": {
                    "serverFarmId": f"asp-{brand}-web",
                    "httpsOnly": True,
                    "parentSiteName": f"store-{brand}",
                    "virtualNetworkSubnetId": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-appsvc",
                },
            },
        ),
        AssetSpec(
            key="fn_orders",
            name=f"orders-fn-{brand}",
            resource_group=rg["app"],
            arm_type="Microsoft.Web/sites",
            location="uksouth",
            sku="Y1",
            fqdn=f"orders.{brand}-retail.azurewebsites.net",
            is_public=1,
            auth_methods="managed_identity;function_key",
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "kind": "functionapp,linux",
                "properties": {
                    "defaultHostName": f"orders.{brand}-retail.azurewebsites.net",
                    "httpsOnly": True,
                    "publicNetworkAccess": "Enabled",
                    "serverFarmId": f"asp-{brand}-web",
                    "virtualNetworkSubnetId": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-appsvc",
                },
            },
        ),
        AssetSpec(
            key="storage",
            name=f"st{brand}orders",
            resource_group=rg["data"],
            arm_type="Microsoft.Storage/storageAccounts",
            location="uksouth",
            sku="Standard_LRS",
            fqdn=f"st{brand}orders.blob.core.windows.net",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "data"},
            raw_json={
                "properties": {
                    "primaryEndpoints": {
                        "blob": f"https://st{brand}orders.blob.core.windows.net/",
                    },
                    "networkAcls": {"defaultAction": "Deny"},
                }
            },
        ),
        AssetSpec(
            key="kv",
            name=f"kv-{brand}-secrets",
            resource_group=rg["security"],
            arm_type="Microsoft.KeyVault/vaults",
            location="uksouth",
            sku="standard",
            fqdn=f"kv-{brand}-secrets.vault.azure.net",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "security"},
            raw_json={"properties": {"vaultUri": f"https://kv-{brand}-secrets.vault.azure.net/", "publicNetworkAccess": "Disabled"}},
        ),
        AssetSpec(
            key="sql_server",
            name=f"sql-{brand}-core",
            resource_group=rg["data"],
            arm_type="Microsoft.Sql/servers",
            location="uksouth",
            sku="GP_S_Gen5_2",
            fqdn=f"sql-{brand}-core.database.windows.net",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"fullyQualifiedDomainName": f"sql-{brand}-core.database.windows.net", "publicNetworkAccess": "Disabled"}},
        ),
        AssetSpec(
            key="sql_db",
            name="orders",
            resource_group=rg["data"],
            arm_type="Microsoft.Sql/servers/databases",
            location="uksouth",
            sku="GP_Gen5_2",
            is_public=0,
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"status": "Online", "collation": "SQL_Latin1_General_CP1_CI_AS"}},
        ),
        AssetSpec(
            key="sql_firewall",
            name=f"allow-{brand}-vnet",
            resource_group=rg["data"],
            arm_type="Microsoft.Sql/servers/firewallRules",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"startIpAddress": "10.40.0.0", "endIpAddress": "10.40.255.255"}},
        ),
        AssetSpec(
            key="cosmos",
            name=f"cosmos-{brand}-orders",
            resource_group=rg["data"],
            arm_type="Microsoft.DocumentDB/databaseAccounts",
            location="uksouth",
            sku=None,
            fqdn=f"cosmos-{brand}-orders.documents.azure.com",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "data"},
            raw_json={
                "properties": {
                    "databaseAccountOfferType": "Standard",
                    "publicNetworkAccess": "Disabled",
                    "enableFreeTier": False,
                }
            },
        ),
        AssetSpec(
            key="cosmos_db",
            name="ordersdb",
            resource_group=rg["data"],
            arm_type="Microsoft.DocumentDB/databaseAccounts/sqlDatabases",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"resource": {"id": "ordersdb"}}},
        ),
        AssetSpec(
            key="cosmos_container",
            name="orders",
            resource_group=rg["data"],
            arm_type="Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"resource": {"id": "orders"}}},
        ),
        AssetSpec(
            key="acr",
            name=f"acr{brand}images",
            resource_group=rg["app"],
            arm_type="Microsoft.ContainerRegistry/registries",
            location="uksouth",
            sku="Premium",
            fqdn=f"acr{brand}images.azurecr.io",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"loginServer": f"acr{brand}images.azurecr.io", "publicNetworkAccess": "Disabled"}},
        ),
        AssetSpec(
            key="aks",
            name=f"aks-{brand}-platform",
            resource_group=rg["app"],
            arm_type="Microsoft.ContainerService/managedClusters",
            location="uksouth",
            sku="Standard_D4s_v5",
            fqdn=f"aks-{brand}-platform.hcp.uksouth.azmk8s.io",
            is_public=1,
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "properties": {
                    "dnsPrefix": f"aks-{brand}-platform",
                    "fqdn": f"aks-{brand}-platform.hcp.uksouth.azmk8s.io",
                    "apiServerAccessProfile": {
                        "enablePrivateCluster": False,
                        "subnetId": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-aks",
                    },
                    "agentPoolProfiles": [
                        {
                            "name": "system",
                            "vnetSubnetID": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-aks",
                        }
                    ],
                }
            },
        ),
        AssetSpec(
            key="aks_nodepool",
            name=f"nodepool-{brand}-apps",
            resource_group=rg["app"],
            arm_type="Microsoft.ContainerService/managedClusters/agentPools",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={"properties": {"count": 3, "vmSize": "Standard_D4s_v5"}},
        ),
        AssetSpec(
            key="aks_ingress",
            name=f"ingress-{brand}-api",
            resource_group=rg["app"],
            arm_type="kubernetes_ingress",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "application"},
            raw_json={
                "properties": {
                    "host": f"api.{brand}-retail.com",
                    "service": "catalog-api",
                    "backend": "aks",
                }
            },
        ),
        AssetSpec(
            key="sb",
            name=f"sb-{brand}-events",
            resource_group=rg["data"],
            arm_type="Microsoft.ServiceBus/namespaces",
            location="uksouth",
            sku="Premium",
            fqdn=f"sb-{brand}-events.servicebus.windows.net",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "data"},
            raw_json={"properties": {"serviceBusEndpoint": f"sb-{brand}-events.servicebus.windows.net", "publicNetworkAccess": "Disabled"}},
        ),
        AssetSpec(
            key="appi",
            name=f"appi-{brand}-store",
            resource_group=rg["ops"],
            arm_type="Microsoft.Insights/components",
            location="uksouth",
            sku=None,
            fqdn=f"appi-{brand}-store.applicationinsights.azure.com",
            is_public=0,
            tags={"brand": brand, "tier": "ops"},
            raw_json={"properties": {"ApplicationId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"triage-saurus:{brand}:appi"))}},
        ),
        AssetSpec(
            key="law",
            name=f"law-{brand}-core",
            resource_group=rg["ops"],
            arm_type="Microsoft.OperationalInsights/workspaces",
            location="uksouth",
            sku="PerGB2018",
            is_public=0,
            tags={"brand": brand, "tier": "ops"},
            raw_json={"properties": {"retentionInDays": 30}},
        ),
        AssetSpec(
            key="appcfg",
            name=f"appcfg-{brand}-core",
            resource_group=rg["ops"],
            arm_type="Microsoft.AppConfiguration/configurationStores",
            location="uksouth",
            sku="standard",
            fqdn=f"appcfg-{brand}-core.azconfig.io",
            is_public=0,
            is_restricted=1,
            ip_restrictions="10.40.0.0/16",
            tags={"brand": brand, "tier": "ops"},
            raw_json={"properties": {"endpoint": f"https://appcfg-{brand}-core.azconfig.io", "networkAcls": {"defaultAction": "Deny"}}},
        ),
        AssetSpec(
            key="apim",
            name=f"apim-{brand}-edge",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service",
            location="uksouth",
            sku="Developer_1",
            fqdn=f"apim-{brand}.azure-api.net",
            is_public=1,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "gatewayUrl": f"https://apim-{brand}.azure-api.net",
                    "publisherEmail": f"ops@{brand}-retail.example",
                    "publicNetworkAccess": "Enabled",
                    "virtualNetworkConfiguration": {
                        "subnetResourceId": f"/subscriptions/{subscription_id}/resourceGroups/rg-{brand}-network/providers/Microsoft.Network/virtualNetworks/vnet-{brand}-core/subnets/snet-{brand}-apim"
                    },
                }
            },
        ),
        AssetSpec(
            key="apim_product",
            name=f"product-{brand}-core",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/products",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"displayName": "Core storefront", "subscriptionRequired": True}},
        ),
        AssetSpec(
            key="apim_subscription",
            name=f"sub-{brand}-core",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/subscriptions",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"scope": "product", "state": "active"}},
        ),
        AssetSpec(
            key="apim_backend",
            name=f"backend-{brand}-catalog",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/backends",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"url": f"https://api.{brand}-retail.com", "protocol": "http"}},
        ),
        AssetSpec(
            key="apim_named_value",
            name=f"nv-{brand}-catalog-url",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/namedValues",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={"properties": {"displayName": "catalog-url", "value": f"https://api.{brand}-retail.com"}},
        ),
        AssetSpec(
            key="apim_api",
            name=f"catalog-{brand}",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/apis",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "path": "catalog",
                    "protocols": ["https"],
                    "serviceUrl": f"https://store.{brand}-retail.azurewebsites.net",
                }
            },
        ),
        AssetSpec(
            key="apim_api_orders",
            name=f"orders-{brand}",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/apis",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "path": "orders",
                    "protocols": ["https"],
                    "serviceUrl": f"https://orders.{brand}-retail.azurewebsites.net",
                }
            },
        ),
        AssetSpec(
            key="apim_api_operation_list",
            name="list-items",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/apis/operations",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "method": "GET",
                    "urlTemplate": "/items",
                    "displayName": "List items",
                }
            },
        ),
        AssetSpec(
            key="apim_api_operation_checkout",
            name="create-order",
            resource_group=rg["edge"],
            arm_type="Microsoft.ApiManagement/service/apis/operations",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "edge"},
            raw_json={
                "properties": {
                    "method": "POST",
                    "urlTemplate": "/orders",
                    "displayName": "Create order",
                }
            },
        ),
        AssetSpec(
            key="private_dns_zone",
            name=f"{brand}-retail-local",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/privateDnsZones",
            location="global",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"maxNumberOfRecordSets": 25}},
        ),
        AssetSpec(
            key="private_dns_link",
            name=f"vnetlink-{brand}-core",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/privateDnsZones/virtualNetworkLinks",
            location="global",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"virtualNetwork": {"id": f"vnet-{brand}-core"}, "registrationEnabled": False}},
        ),
        AssetSpec(
            key="pe_sql",
            name=f"pe-{brand}-sql",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/privateEndpoints",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"privateLinkServiceConnections": [{"name": "sql", "properties": {"privateLinkServiceId": f"sql-{brand}-core"}}]}},
        ),
        AssetSpec(
            key="pe_kv",
            name=f"pe-{brand}-kv",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/privateEndpoints",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"privateLinkServiceConnections": [{"name": "kv", "properties": {"privateLinkServiceId": f"kv-{brand}-secrets"}}]}},
        ),
        AssetSpec(
            key="pe_storage",
            name=f"pe-{brand}-storage",
            resource_group=rg["network"],
            arm_type="Microsoft.Network/privateEndpoints",
            location="uksouth",
            is_public=0,
            tags={"brand": brand, "tier": "network"},
            raw_json={"properties": {"privateLinkServiceConnections": [{"name": "storage", "properties": {"privateLinkServiceId": f"st{brand}orders"}}]}},
        ),
    ]


def _build_rows(subscription_id: str, assets: list[AssetSpec], tenant_id: str, display_name: str, state: str, environment: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sub_row = {
        "id": subscription_id,
        "display_name": display_name,
        "tenant_id": tenant_id,
        "environment": environment,
        "state": state,
        "last_synced": _utcnow(),
    }
    asset_rows: list[dict[str, Any]] = []
    for spec in assets:
        raw_json = dict(spec.raw_json or {})
        raw_json.setdefault("id", _arm_id(subscription_id, spec.resource_group, spec.arm_type, spec.name))
        raw_json.setdefault("name", spec.name)
        raw_json.setdefault("resourceGroup", spec.resource_group)
        raw_json.setdefault("location", spec.location)
        raw_json.setdefault("type", spec.arm_type)
        if spec.sku is not None:
            raw_json.setdefault("sku", {"name": spec.sku})
        if spec.tags:
            raw_json.setdefault("tags", spec.tags)

        asset_rows.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"triage-saurus:{subscription_id}:{spec.key}")),
                "subscription_id": subscription_id,
                "resource_group": spec.resource_group,
                "name": spec.name,
                "type": spec.arm_type,
                "location": spec.location,
                "sku": spec.sku,
                "tags": _json_text(spec.tags) if spec.tags else None,
                "is_public": spec.is_public,
                "fqdn": spec.fqdn,
                "pipeline_tag": spec.pipeline_tag,
                "raw_json": json.dumps(raw_json, sort_keys=True),
                "first_detected": _utcnow(),
                "last_synced": _utcnow(),
                "status": "active",
                "is_restricted": spec.is_restricted,
                "ip_restrictions": spec.ip_restrictions,
                "endpoints": spec.endpoints,
                "auth_methods": spec.auth_methods,
                "waf_mode": spec.waf_mode,
            }
        )
    return sub_row, asset_rows


def _upsert_subscription(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO subscriptions (id, display_name, tenant_id, environment, state, last_synced)
        VALUES (:id, :display_name, :tenant_id, :environment, :state, :last_synced)
        ON CONFLICT(id) DO UPDATE SET
            display_name = excluded.display_name,
            tenant_id    = excluded.tenant_id,
            environment  = excluded.environment,
            state        = excluded.state,
            last_synced  = excluded.last_synced
        """,
        row,
    )


def _upsert_asset(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO provisioned_assets
            (id, subscription_id, resource_group, name, type, location, sku,
             tags, is_public, fqdn, pipeline_tag, raw_json, first_detected, last_synced,
             status, is_restricted, ip_restrictions, endpoints, auth_methods, waf_mode)
        VALUES
            (:id, :subscription_id, :resource_group, :name, :type, :location, :sku,
             :tags, :is_public, :fqdn, :pipeline_tag, :raw_json, :first_detected, :last_synced,
             :status, :is_restricted, :ip_restrictions, :endpoints, :auth_methods, :waf_mode)
        ON CONFLICT(id) DO UPDATE SET
            subscription_id = excluded.subscription_id,
            resource_group  = excluded.resource_group,
            name            = excluded.name,
            type            = excluded.type,
            location        = excluded.location,
            sku             = excluded.sku,
            tags            = excluded.tags,
            is_public       = excluded.is_public,
            fqdn            = excluded.fqdn,
            pipeline_tag    = excluded.pipeline_tag,
            raw_json        = excluded.raw_json,
            last_synced     = excluded.last_synced,
            status          = excluded.status,
            is_restricted   = excluded.is_restricted,
            ip_restrictions = excluded.ip_restrictions,
            endpoints       = excluded.endpoints,
            auth_methods    = excluded.auth_methods,
            waf_mode        = excluded.waf_mode
        """,
        row,
    )


def _upsert_connection(conn: sqlite3.Connection, experiment_id: str, source_rowid: int, target_rowid: int, connection_type: str, notes: str) -> None:
    conn.execute(
        """
        INSERT INTO resource_connections
            (experiment_id, source_resource_id, target_resource_id, connection_type, notes, inferred_internet)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (experiment_id, source_rowid, target_rowid, connection_type, notes),
    )


def _ensure_dummy_route_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS apim_api_routes (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            apim_name TEXT NOT NULL,
            apim_resource_id TEXT,
            api_name TEXT NOT NULL,
            api_display_name TEXT,
            api_path TEXT,
            api_protocols TEXT,
            backend_id TEXT,
            backend_url TEXT,
            service_url TEXT,
            requires_subscription INTEGER DEFAULT 1,
            gateway_hosts TEXT,
            exposure_level TEXT,
            last_synced DATETIME
        );
        CREATE TABLE IF NOT EXISTS apim_api_operations (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            apim_name TEXT NOT NULL,
            api_name TEXT NOT NULL,
            api_display_name TEXT,
            api_path TEXT,
            backend_url TEXT,
            operation_id TEXT NOT NULL,
            display_name TEXT,
            method TEXT,
            url_template TEXT,
            description TEXT,
            requires_subscription INTEGER DEFAULT 1,
            policy_summary TEXT,
            last_synced DATETIME
        );
        CREATE TABLE IF NOT EXISTS apim_backends (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            apim_name TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            title TEXT,
            description TEXT,
            url TEXT,
            protocol TEXT,
            circuit_breaker TEXT,
            credentials TEXT,
            tls_validate_cert INTEGER DEFAULT 1,
            last_synced DATETIME
        );
        """
    )
    conn.commit()


def _upsert_appgw_route(
    conn: sqlite3.Connection,
    subscription_id: str,
    gateway_name: str,
    gateway_resource_id: str,
    resource_group: str,
    rule_name: str,
    listener_name: str,
    hostname: str,
    protocol: str,
    url_path: str,
    backend_pool_name: str,
    backend_fqdns: list[str],
    http_settings_name: str,
    backend_port: int,
    backend_protocol: str,
    host_override: str | None,
    waf_policy_name: str | None,
    exposure_level: str = "Public",
) -> None:
    conn.execute(
        """
        INSERT INTO appgw_routing_rules
            (id, subscription_id, gateway_name, gateway_resource_id, resource_group,
             rule_name, listener_name, hostname, protocol, url_path, backend_pool_name,
             backend_fqdns, http_settings_name, backend_port, backend_protocol,
             host_override, waf_policy_name, exposure_level, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            hostname          = excluded.hostname,
            protocol          = excluded.protocol,
            backend_pool_name = excluded.backend_pool_name,
            backend_fqdns     = excluded.backend_fqdns,
            backend_port      = excluded.backend_port,
            backend_protocol  = excluded.backend_protocol,
            host_override     = excluded.host_override,
            waf_policy_name   = excluded.waf_policy_name,
            exposure_level    = excluded.exposure_level,
            last_synced       = excluded.last_synced
        """,
        (
            f"{gateway_name}::{rule_name}::{url_path}",
            subscription_id,
            gateway_name,
            gateway_resource_id,
            resource_group,
            rule_name,
            listener_name,
            hostname,
            protocol,
            url_path,
            backend_pool_name,
            json.dumps(backend_fqdns),
            http_settings_name,
            backend_port,
            backend_protocol,
            host_override,
            waf_policy_name,
            exposure_level,
            _utcnow(),
        ),
    )


def _upsert_appgw_waf_policy(
    conn: sqlite3.Connection,
    subscription_id: str,
    name: str,
    resource_group: str,
    mode: str = "Prevention",
    state: str = "Enabled",
    associated_gateways: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO appgw_waf_policies
            (id, subscription_id, name, resource_group, mode, state,
             request_body_check, max_body_kb, managed_rule_sets, custom_rules_count,
             exclusions_count, associated_gateways, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            mode                = excluded.mode,
            state               = excluded.state,
            associated_gateways = excluded.associated_gateways,
            last_synced         = excluded.last_synced
        """,
        (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies/{name}",
            subscription_id,
            name,
            resource_group,
            mode,
            state,
            1,
            128,
            json.dumps([{"type": "OWASP", "version": "3.2"}]),
            0,
            0,
            json.dumps(associated_gateways or []),
            _utcnow(),
        ),
    )


def _upsert_apim_route(
    conn: sqlite3.Connection,
    subscription_id: str,
    apim_name: str,
    apim_resource_id: str,
    api_name: str,
    api_display_name: str,
    api_path: str,
    api_protocols: list[str],
    backend_id: str | None,
    backend_url: str,
    service_url: str,
    requires_subscription: int = 1,
    exposure_level: str = "Public",
) -> None:
    conn.execute(
        """
        INSERT INTO apim_api_routes
            (id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
             api_path, api_protocols, backend_id, backend_url, service_url,
             requires_subscription, gateway_hosts, exposure_level, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            backend_id            = excluded.backend_id,
            backend_url           = excluded.backend_url,
            service_url           = excluded.service_url,
            requires_subscription = excluded.requires_subscription,
            exposure_level        = excluded.exposure_level,
            last_synced           = excluded.last_synced
        """,
        (
            f"{apim_name}::{api_name}",
            subscription_id,
            apim_name,
            apim_resource_id,
            api_name,
            api_display_name,
            api_path,
            json.dumps(api_protocols),
            backend_id,
            backend_url,
            service_url,
            requires_subscription,
            json.dumps([f"{apim_name}.azure-api.net"]),
            exposure_level,
            _utcnow(),
        ),
    )


def _upsert_apim_backend(
    conn: sqlite3.Connection,
    subscription_id: str,
    apim_name: str,
    backend_id: str,
    title: str,
    url: str,
    protocol: str = "http",
) -> None:
    conn.execute(
        """
        INSERT INTO apim_backends
            (id, subscription_id, apim_name, backend_id, title, description,
             url, protocol, circuit_breaker, credentials, tls_validate_cert, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title       = excluded.title,
            url         = excluded.url,
            protocol    = excluded.protocol,
            last_synced = excluded.last_synced
        """,
        (
            f"{apim_name}::{backend_id}",
            subscription_id,
            apim_name,
            backend_id,
            title,
            None,
            url,
            protocol,
            json.dumps({"count": 5, "interval": "00:00:30"}),
            json.dumps({}),
            1,
            _utcnow(),
        ),
    )


def _upsert_apim_operation(
    conn: sqlite3.Connection,
    subscription_id: str,
    apim_name: str,
    api_name: str,
    api_display_name: str,
    api_path: str,
    backend_url: str,
    operation_id: str,
    display_name: str,
    method: str,
    url_template: str,
    description: str = "",
    requires_subscription: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO apim_api_operations
            (id, subscription_id, apim_name, api_name, api_display_name, api_path,
             backend_url, operation_id, display_name, method, url_template,
             description, requires_subscription, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            backend_url           = excluded.backend_url,
            display_name          = excluded.display_name,
            method                = excluded.method,
            url_template          = excluded.url_template,
            description           = excluded.description,
            requires_subscription = excluded.requires_subscription,
            last_synced           = excluded.last_synced
        """,
        (
            f"{apim_name}::{api_name}::{operation_id}",
            subscription_id,
            apim_name,
            api_name,
            api_display_name,
            api_path,
            backend_url,
            operation_id,
            display_name,
            method,
            url_template,
            description,
            requires_subscription,
            _utcnow(),
        ),
    )


def _upsert_aks_route(
    conn: sqlite3.Connection,
    subscription_id: str,
    cluster_name: str,
    resource_group: str,
    namespace: str,
    ingress_name: str,
    host: str,
    path: str,
    service_name: str,
    service_port: str,
    deployment_name: str,
    git_repository: str,
    *,
    exposure_level: str = "Public",
    service_ports: list[str] | None = None,
    pod_template_labels: dict[str, str] | None = None,
) -> None:
    route_id = f"{cluster_name}::{namespace}::{ingress_name}::{host or '*'}::{path or '*'}::{service_name}::{service_port}::{deployment_name}"
    cluster_resource_id = _arm_id(subscription_id, resource_group, "Microsoft.ContainerService/managedClusters", cluster_name)
    conn.execute(
        """
        INSERT INTO aks_routes
            (id, subscription_id, cluster_name, cluster_resource_id, resource_group, namespace,
             ingress_name, host, host_aliases, path, is_default_backend, service_name,
             service_port, service_ports, deployment_name, deployment_namespace,
             pod_template_labels, git_repository, team, exposure_level, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            host                 = excluded.host,
            path                 = excluded.path,
            service_name         = excluded.service_name,
            service_port         = excluded.service_port,
            service_ports        = excluded.service_ports,
            deployment_name      = excluded.deployment_name,
            deployment_namespace = excluded.deployment_namespace,
            pod_template_labels   = excluded.pod_template_labels,
            git_repository       = excluded.git_repository,
            team                 = excluded.team,
            exposure_level       = excluded.exposure_level,
            last_synced          = excluded.last_synced
        """,
        (
            route_id,
            subscription_id,
            cluster_name,
            cluster_resource_id,
            resource_group,
            namespace,
            ingress_name,
            host,
            json.dumps([host]) if host else None,
            path,
            0,
            service_name,
            service_port,
            json.dumps(service_ports or ([service_port] if service_port else [])),
            deployment_name,
            namespace,
            json.dumps(pod_template_labels or {}),
            git_repository,
            "marketplace",
            exposure_level,
            _utcnow(),
        ),
    )


def _upsert_firewall_policy(
    conn: sqlite3.Connection,
    subscription_id: str,
    name: str,
    resource_group: str,
    associated_firewalls: list[str],
    *,
    mode: str = "Alert",
    threat_intelligence_mode: str = "Alert",
    rule_collection_groups: list[dict[str, Any]] | None = None,
    nat_rule_count: int = 0,
    app_rule_count: int = 0,
) -> None:
    policy_id = _arm_id(subscription_id, resource_group, "Microsoft.Network/firewallPolicies", name)
    conn.execute(
        """
        INSERT INTO firewall_policies
            (id, subscription_id, name, resource_group, associated_firewalls, mode,
             threat_intelligence_mode, dns_proxy_enabled, rule_collection_groups,
             nat_rule_count, app_rule_count, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            associated_firewalls     = excluded.associated_firewalls,
            mode                    = excluded.mode,
            threat_intelligence_mode = excluded.threat_intelligence_mode,
            dns_proxy_enabled       = excluded.dns_proxy_enabled,
            rule_collection_groups  = excluded.rule_collection_groups,
            nat_rule_count          = excluded.nat_rule_count,
            app_rule_count          = excluded.app_rule_count,
            last_synced             = excluded.last_synced
        """,
        (
            policy_id,
            subscription_id,
            name,
            resource_group,
            json.dumps(associated_firewalls),
            mode,
            threat_intelligence_mode,
            0,
            json.dumps(rule_collection_groups or []),
            nat_rule_count,
            app_rule_count,
            _utcnow(),
        ),
    )


def _upsert_firewall_nat_rule(
    conn: sqlite3.Connection,
    subscription_id: str,
    firewall_name: str,
    resource_group: str,
    collection_name: str,
    rule_name: str,
    entry_hosts: list[str],
    translated_address: str,
    translated_fqdn: str,
    translated_port: str,
    protocols: list[str],
    exposure_level: str = "Public",
) -> None:
    rule_id = f"{firewall_name}::{collection_name}::{rule_name}::{translated_fqdn or translated_address}"
    conn.execute(
        """
        INSERT INTO firewall_nat_rules
            (id, subscription_id, firewall_name, resource_group, collection_name, rule_name,
             entry_hosts, translated_address, translated_fqdn, translated_port, protocols,
             exposure_level, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            entry_hosts        = excluded.entry_hosts,
            translated_address = excluded.translated_address,
            translated_fqdn    = excluded.translated_fqdn,
            translated_port    = excluded.translated_port,
            protocols          = excluded.protocols,
            exposure_level     = excluded.exposure_level,
            last_synced        = excluded.last_synced
        """,
        (
            rule_id,
            subscription_id,
            firewall_name,
            resource_group,
            collection_name,
            rule_name,
            json.dumps(entry_hosts),
            translated_address,
            translated_fqdn,
            translated_port,
            json.dumps(protocols),
            exposure_level,
            _utcnow(),
        ),
    )


def _upsert_firewall_app_rule(
    conn: sqlite3.Connection,
    subscription_id: str,
    firewall_name: str,
    resource_group: str,
    collection_name: str,
    rule_name: str,
    source_addresses: list[str],
    target_fqdns: list[str],
    protocols: list[str],
) -> None:
    rule_id = f"{firewall_name}::{collection_name}::{rule_name}"
    conn.execute(
        """
        INSERT INTO firewall_app_rules
            (id, subscription_id, firewall_name, resource_group, collection_name, rule_name,
             source_addresses, target_fqdns, protocols, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_addresses = excluded.source_addresses,
            target_fqdns     = excluded.target_fqdns,
            protocols        = excluded.protocols,
            last_synced      = excluded.last_synced
        """,
        (
            rule_id,
            subscription_id,
            firewall_name,
            resource_group,
            collection_name,
            rule_name,
            json.dumps(source_addresses),
            json.dumps(target_fqdns),
            json.dumps(protocols),
            _utcnow(),
        ),
    )


def seed_dummy_subscription(db_path: Path, subscription_id: str, display_name: str, tenant_id: str, environment: str, state: str, brand: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_schema(conn)
        _ensure_dummy_route_schema(conn)

        assets = _build_assets(brand, subscription_id=subscription_id)
        experiment_id = f"dummy-{subscription_id}"

        conn.execute("DELETE FROM resource_connections WHERE experiment_id = ?", (experiment_id,))
        conn.execute("DELETE FROM subscription_context WHERE experiment_id = ? AND scope_key = 'subscription'", (experiment_id,))
        conn.execute("DELETE FROM provisioned_assets WHERE subscription_id = ?", (subscription_id,))
        conn.execute("DELETE FROM aks_routes WHERE subscription_id = ?", (subscription_id,))
        conn.execute("DELETE FROM firewall_policies WHERE subscription_id = ?", (subscription_id,))
        conn.execute("DELETE FROM firewall_nat_rules WHERE subscription_id = ?", (subscription_id,))
        conn.execute("DELETE FROM firewall_app_rules WHERE subscription_id = ?", (subscription_id,))
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))

        existing = _load_existing_names(conn)
        _ensure_no_collisions(existing, display_name, assets)

        sub_row, asset_rows = _build_rows(subscription_id, assets, tenant_id, display_name, state, environment)

        _upsert_subscription(conn, sub_row)
        for row in asset_rows:
            _upsert_asset(conn, row)

        asset_id_by_key = {
            spec.key: conn.execute(
                "SELECT rowid FROM provisioned_assets WHERE id = ?",
                (row["id"],),
            ).fetchone()[0]
            for spec, row in zip(assets, asset_rows, strict=True)
        }

        gateway_id = conn.execute("SELECT id FROM provisioned_assets WHERE name = ?", (f"agw-{brand}-edge",)).fetchone()[0]
        apim_id = conn.execute("SELECT id FROM provisioned_assets WHERE name = ?", (f"apim-{brand}-edge",)).fetchone()[0]
        apim_api_catalog_id = conn.execute("SELECT id FROM provisioned_assets WHERE name = ?", (f"catalog-{brand}",)).fetchone()[0]
        apim_api_orders_id = conn.execute("SELECT id FROM provisioned_assets WHERE name = ?", (f"orders-{brand}",)).fetchone()[0]
        appgw_rg = f"rg-{brand}-edge"
        apim_rg = f"rg-{brand}-edge"
        app_rg = f"rg-{brand}-app"
        network_rg = f"rg-{brand}-network"

        _upsert_appgw_route(
            conn,
            subscription_id,
            f"agw-{brand}-edge",
            gateway_id,
            appgw_rg,
            f"rule-{brand}-web",
            f"listener-{brand}-web",
            f"shop.{brand}-retail.com",
            "Https",
            "/*",
            f"bhs-{brand}-web",
            [f"store.{brand}-retail.azurewebsites.net"],
            f"bhs-{brand}-web",
            443,
            "Https",
            None,
            "waf-v2",
        )
        _upsert_appgw_route(
            conn,
            subscription_id,
            f"agw-{brand}-edge",
            gateway_id,
            appgw_rg,
            f"rule-{brand}-api",
            f"listener-{brand}-api",
            f"api.{brand}-retail.com",
            "Https",
            "/*",
            f"bhs-{brand}-api",
            [f"aks-{brand}-platform.hcp.uksouth.azmk8s.io"],
            f"bhs-{brand}-api",
            443,
            "Https",
            None,
            "waf-v2",
        )

        _upsert_apim_backend(conn, subscription_id, f"apim-{brand}-edge", "store-backend", "store-backend", f"https://store.{brand}-retail.azurewebsites.net", "http")
        _upsert_apim_backend(conn, subscription_id, f"apim-{brand}-edge", "aks-marketlane-platform-orders-orders-api-8080", "aks-marketlane-platform-orders-orders-api-8080", f"https://orders.{brand}-retail.internal/", "http")

        _upsert_apim_route(
            conn,
            subscription_id,
            f"apim-{brand}-edge",
            apim_id,
            f"catalog-{brand}",
            "Catalog API",
            "catalog",
            ["https"],
            "store-backend",
            f"https://store.{brand}-retail.azurewebsites.net",
            f"https://store.{brand}-retail.azurewebsites.net",
        )
        _upsert_apim_route(
            conn,
            subscription_id,
            f"apim-{brand}-edge",
            apim_id,
            f"orders-{brand}",
            "Orders API",
            "orders",
            ["https"],
            "aks-marketlane-platform-orders-orders-api-8080",
            f"https://orders.{brand}-retail.internal/",
            f"https://orders.{brand}-retail.internal/",
        )

        _upsert_apim_operation(conn, subscription_id, f"apim-{brand}-edge", f"catalog-{brand}", "Catalog API", "catalog", f"https://store.{brand}-retail.azurewebsites.net", "list-items", "List items", "GET", "/items")
        _upsert_apim_operation(conn, subscription_id, f"apim-{brand}-edge", f"catalog-{brand}", "Catalog API", "catalog", f"https://store.{brand}-retail.azurewebsites.net", "get-item", "Get item", "GET", "/items/{itemId}")
        _upsert_apim_operation(conn, subscription_id, f"apim-{brand}-edge", f"orders-{brand}", "Orders API", "orders", f"https://orders.{brand}-retail.internal/", "create-order", "Create order", "POST", "/orders")
        _upsert_apim_operation(conn, subscription_id, f"apim-{brand}-edge", f"orders-{brand}", "Orders API", "orders", f"https://orders.{brand}-retail.internal/", "cancel-order", "Cancel order", "POST", "/orders/{orderId}/cancel")

        _upsert_aks_route(
            conn,
            subscription_id,
            f"aks-{brand}-platform",
            app_rg,
            "storefront",
            "storefront-ingress",
            f"store.{brand}-retail.com",
            "/*",
            "store-web",
            "80",
            "store-web",
            f"https://github.com/{brand}/storefront.git",
            exposure_level="Public",
            service_ports=["80", "443"],
            pod_template_labels={
                "app.kubernetes.io/name": "store-web",
                "app.kubernetes.io/component": "frontend",
            },
        )
        _upsert_aks_route(
            conn,
            subscription_id,
            f"aks-{brand}-platform",
            app_rg,
            "orders",
            "orders-ingress",
            f"orders.{brand}-retail.internal",
            "/api/orders/*",
            "orders-api",
            "8080",
            "orders-api",
            f"https://github.com/{brand}/orders-api.git",
            exposure_level="Internal",
            service_ports=["8080"],
            pod_template_labels={
                "app.kubernetes.io/name": "orders-api",
                "app.kubernetes.io/component": "api",
            },
        )

        _upsert_firewall_policy(
            conn,
            subscription_id,
            f"fw-{brand}-policy",
            network_rg,
            [f"fw-{brand}-core"],
            rule_collection_groups=[
                {
                    "name": f"egress-{brand}",
                    "priority": 100,
                    "collection_count": 2,
                    "rule_count": 3,
                }
            ],
            nat_rule_count=1,
            app_rule_count=2,
        )
        _upsert_firewall_nat_rule(
            conn,
            subscription_id,
            f"fw-{brand}-core",
            network_rg,
            "web-collection",
            "shop-public",
            ["52.160.10.10"],
            "10.40.4.10",
            f"shop.{brand}-retail.com",
            "443",
            ["HTTPS:443"],
            exposure_level="Public",
        )
        _upsert_firewall_app_rule(
            conn,
            subscription_id,
            f"fw-{brand}-core",
            network_rg,
            "egress",
            "storefront-egress",
            ["10.40.4.0/24"],
            [f"store.{brand}-retail.azurewebsites.net", f"orders.{brand}-retail.azurewebsites.net"],
            ["Https:443"],
        )
        _upsert_firewall_app_rule(
            conn,
            subscription_id,
            f"fw-{brand}-core",
            network_rg,
            "egress",
            "platform-egress",
            ["10.40.7.0/24"],
            [f"*.{brand}-retail.internal", "mcr.microsoft.com"],
            ["Https:443"],
        )

        notes = "Seeded by Scripts/Harvest/seed_dummy_azure_subscription.py"
        connection_pairs = [
            ("pip_edge", "appgw_edge", "fronts"),
            ("appgw_edge", "waf_policy", "secured_by"),
            ("appgw_edge", "appgw_listener_web", "contains"),
            ("appgw_edge", "appgw_listener_api", "contains"),
            ("appgw_edge", "appgw_backend_web", "contains"),
            ("appgw_edge", "appgw_backend_api", "contains"),
            ("appgw_edge", "appgw_probe_web", "contains"),
            ("appgw_edge", "appgw_probe_api", "contains"),
            ("appgw_edge", "appgw_rule_web", "contains"),
            ("appgw_edge", "appgw_rule_api", "contains"),
            ("appgw_rule_web", "appgw_listener_web", "uses"),
            ("appgw_rule_web", "appgw_backend_web", "uses"),
            ("appgw_rule_api", "appgw_listener_api", "uses"),
            ("appgw_rule_api", "appgw_backend_api", "uses"),
            ("pip_edge", "load_balancer", "fronts"),
            ("pip_firewall", "firewall", "assigns_ip"),
            ("pip_bastion", "bastion", "assigns_ip"),
            ("appgw_edge", "web_store", "routes_to"),
            ("appgw_edge", "aks_ingress", "routes_to"),
            ("load_balancer", "web_store", "routes_to"),
            ("traffic_manager", "appgw_edge", "routes_to"),
            ("apim", "apim_api", "contains"),
            ("apim", "apim_api_orders", "contains"),
            ("apim", "apim_product", "contains"),
            ("apim", "apim_subscription", "contains"),
            ("apim", "apim_backend", "contains"),
            ("apim", "apim_named_value", "contains"),
            ("apim_api", "apim_api_operation_list", "contains"),
            ("apim_api", "apim_api_operation_checkout", "contains"),
            ("apim_api_orders", "apim_api_operation_list", "contains"),
            ("apim_api_orders", "apim_api_operation_checkout", "contains"),
            ("apim_product", "apim_api", "contains"),
            ("apim_subscription", "apim_product", "contains"),
            ("apim_backend", "apim_api", "routes_to"),
            ("apim_backend", "apim_api_orders", "routes_to"),
            ("apim_named_value", "apim_backend", "configures"),
            ("apim", "web_store", "routes_to"),
            ("apim", "fn_orders", "routes_to"),
            ("apim", "aks_ingress", "routes_to"),
            ("firewall", "appgw_edge", "inspects"),
            ("bastion", "vnet", "manages"),
            ("plan_web", "web_store", "hosts"),
            ("plan_web", "fn_orders", "hosts"),
            ("web_store", "sql_server", "reads_writes"),
            ("web_store", "storage", "stores_to"),
            ("web_store", "cosmos", "stores_to"),
            ("sql_server", "sql_firewall", "contains"),
            ("web_slot", "web_store", "contains"),
            ("fn_orders", "subnet_app", "deploys_to"),
            ("web_store", "subnet_app", "deploys_to"),
            ("fn_orders", "sb", "publishes_to"),
            ("aks", "acr", "pulls_from"),
            ("aks", "subnet_aks", "runs_in"),
            ("aks", "aks_nodepool", "contains"),
            ("aks_ingress", "aks", "routes_to"),
            ("aks_ingress", "appgw_edge", "fronted_by"),
            ("apim", "subnet_apim", "runs_in"),
            ("plan_web", "ase", "hosts"),
            ("ase", "subnet_ase", "resides_in"),
            ("firewall", "subnet_edge", "protects"),
            ("bastion", "subnet_bastion", "resides_in"),
            ("load_balancer", "subnet_ingress", "resides_in"),
            ("appgw_edge", "subnet_ingress", "resides_in"),
            ("route_ingress", "subnet_ingress", "routes"),
            ("route_app", "subnet_app", "routes"),
            ("nsg_ingress", "subnet_ingress", "secures"),
            ("nsg_app", "subnet_app", "secures"),
            ("vnet", "private_dns_zone", "contains"),
            ("private_dns_zone", "private_dns_link", "links"),
            ("pe_sql", "sql_server", "private_link"),
            ("pe_kv", "kv", "private_link"),
            ("pe_storage", "storage", "private_link"),
            ("pe_storage", "cosmos", "private_link"),
            ("vnet", "subnet_edge", "contains"),
            ("vnet", "subnet_ingress", "contains"),
            ("vnet", "subnet_bastion", "contains"),
            ("vnet", "subnet_ops", "contains"),
            ("vnet", "subnet_app", "contains"),
            ("vnet", "subnet_apim", "contains"),
            ("vnet", "subnet_aks", "contains"),
            ("vnet", "subnet_ase", "contains"),
            ("vnet", "subnet_appsvc", "contains"),
            ("vnet", "subnet_data", "contains"),
            ("appcfg", "web_store", "configures"),
            ("appi", "web_store", "observes"),
            ("law", "appi", "collects"),
            ("sql_server", "sql_db", "hosts"),
            ("sql_server", "cosmos", "pairs_with"),
            ("cosmos", "cosmos_db", "contains"),
            ("cosmos_db", "cosmos_container", "contains"),
            ("aks_ingress", "aks_nodepool", "routes_to"),
            ("kv", "appcfg", "stores_config_for"),
        ]
        for src_key, dst_key, rel in connection_pairs:
            _upsert_connection(
                conn,
                experiment_id,
                asset_id_by_key[src_key],
                asset_id_by_key[dst_key],
                rel,
                notes,
            )

        conn.execute(
            """
            INSERT INTO subscription_context
                (experiment_id, scope_key, repo_name, question, answer, answered_by, confidence, tags, created_at, updated_at)
            VALUES (?, 'subscription', NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                "What is this subscription?",
                f"Synthetic ecommerce demo subscription seeded by {brand}.",
                "seed_script",
                1.0,
                "synthetic,dummy,ecommerce",
                _utcnow(),
                _utcnow(),
            ),
        )

        conn.commit()
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate CozoDB with a synthetic Azure subscription")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite/Cozo DB path")
    parser.add_argument("--subscription-id", default=DEFAULT_SUBSCRIPTION_ID, help="Synthetic subscription id")
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME, help="Synthetic subscription display name")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="Synthetic tenant id")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT, help="Subscription environment label")
    parser.add_argument("--state", default=DEFAULT_STATE, help="Subscription state label")
    parser.add_argument("--brand", default=DEFAULT_BRAND, help="Generic company prefix used in asset names")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seed_dummy_subscription(
        db_path=args.db_path,
        subscription_id=args.subscription_id,
        display_name=args.display_name,
        tenant_id=args.tenant_id,
        environment=args.environment,
        state=args.state,
        brand=args.brand,
    )
    print(f"Seeded dummy Azure subscription '{args.display_name}' into {args.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
