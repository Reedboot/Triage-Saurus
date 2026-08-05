"""Harvest Azure service resources that are easy to miss in provider-specific CLIs."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPES = (
    "Microsoft.Web/connections",
    "Microsoft.EventGrid/systemTopics",
    "Microsoft.Automation/automationAccounts",
    "Microsoft.Network/natGateways",
    "Microsoft.Network/networkInterfaces",
    "Microsoft.DatabaseWatcher/watchers",
    "Microsoft.Network/privateDnsZones",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
    "Microsoft.Fabric/capacities",
)


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for resource_type in RESOURCE_TYPES:
        for resource in az(["resource", "list", "--resource-type", resource_type], subscription_id):
            props = resource.get("properties") or {}
            if resource_type == "Microsoft.Network/networkInterfaces":
                ip_configs = props.get("ipConfigurations") or []
                has_public_ip = any(
                    (item.get("properties") or {}).get("publicIPAddress")
                    for item in ip_configs
                    if isinstance(item, dict)
                )
                has_lb_backend = any(
                    (item.get("properties") or {}).get("loadBalancerBackendAddressPools")
                    for item in ip_configs
                    if isinstance(item, dict)
                )
                if not has_public_ip and not has_lb_backend:
                    continue
                nic_extra = {
                    "public_ip_resource_ids": [
                        (item.get("properties") or {}).get("publicIPAddress", {}).get("id")
                        for item in ip_configs
                        if isinstance((item.get("properties") or {}).get("publicIPAddress"), dict)
                        and (item.get("properties") or {}).get("publicIPAddress", {}).get("id")
                    ],
                    "load_balancer_backend_pool_ids": [
                        pool.get("id")
                        for item in ip_configs
                        for pool in ((item.get("properties") or {}).get("loadBalancerBackendAddressPools") or [])
                        if isinstance(pool, dict) and pool.get("id")
                    ],
                    "subnet_ids": [
                        (item.get("properties") or {}).get("subnet", {}).get("id")
                        for item in ip_configs
                        if isinstance((item.get("properties") or {}).get("subnet"), dict)
                        and (item.get("properties") or {}).get("subnet", {}).get("id")
                    ],
                    "network_security_group_id": (
                        (props.get("networkSecurityGroup") or {}).get("id")
                        if isinstance(props.get("networkSecurityGroup"), dict)
                        else None
                    ),
                    "exposure_class": "requires_nsg_review" if has_public_ip else "private",
                    "direction": "inbound" if has_public_ip else "internal",
                    "purpose": "public IP association requires NSG review" if has_public_ip else "private NIC",
                }
            elif resource_type == "Microsoft.Network/natGateways":
                nic_extra = {
                    "exposure_class": "egress_only",
                    "direction": "egress",
                    "purpose": "NAT Gateway egress-only public IP translation",
                }
            else:
                nic_extra = {}
            public_ip_resource_ids = [
                item.get("id")
                for item in (props.get("publicIpAddresses") or [])
                if isinstance(item, dict) and item.get("id")
            ]
            endpoints = [
                safe_str(value)
                for key, value in props.items()
                if isinstance(value, str) and ("endpoint" in key.lower() or "url" in key.lower())
            ]
            public_access = safe_str(props.get("publicNetworkAccess"))
            results.append({
                "id": resource["id"],
                "subscription_id": subscription_id,
                "resource_group": resource.get("resourceGroup"),
                "name": resource.get("name"),
                "type": resource.get("type", resource_type),
                "location": resource.get("location"),
                "sku": infer_sku(resource),
                "tags": json.dumps(resource.get("tags") or {}),
                "is_public": 1 if endpoints and public_access == "Enabled" else 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": build_endpoints([(value, 443, "https") for value in endpoints]),
                "auth_methods": json.dumps(["azure_ad"]),
                "fqdn": endpoints[0] if endpoints else None,
                "pipeline_tag": (resource.get("tags") or {}).get("pipeline"),
                "raw_json": json.dumps({
                    **resource,
                    "_extra": {
                        "public_network_access": public_access,
                        "endpoints": endpoints,
                        "parent_resource_id": (
                            resource["id"].split("/virtualNetworkLinks/", 1)[0]
                            if "/virtualNetworkLinks/" in resource["id"]
                            else (props.get("virtualMachine") or {}).get("id")
                            if resource_type == "Microsoft.Network/networkInterfaces"
                            else None
                        ),
                        "vnet_id": (
                            (props.get("virtualNetwork") or {}).get("id")
                            if resource_type == "Microsoft.Network/privateDnsZones/virtualNetworkLinks"
                            else None
                        ),
                        **nic_extra,
                        "public_ip_resource_ids": public_ip_resource_ids,
                    },
                }),
            })
    return results
