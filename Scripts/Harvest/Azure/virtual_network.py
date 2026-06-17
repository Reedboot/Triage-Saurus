"""Harvest Azure Virtual Networks."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az

RESOURCE_TYPE = "Microsoft.Network/virtualNetworks"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "vnet", "list"], subscription_id)
    results = []

    for vnet in raw:
        props = vnet.get("properties") or vnet
        subnets = props.get("subnets") or []

        extra = {
            "address_space": props.get("addressSpace", {}).get("addressPrefixes", []),
            "dns_servers": (props.get("dhcpOptions") or {}).get("dnsServers", []),
            "subnet_count": len(subnets),
            "subnets": [
                {
                    "name": s.get("name"),
                    "prefix": (s.get("properties") or {}).get("addressPrefix"),
                    "nsg": bool((s.get("properties") or {}).get("networkSecurityGroup")),
                    "route_table": bool((s.get("properties") or {}).get("routeTable")),
                    "delegations": [(d.get("properties") or {}).get("serviceName")
                                    for d in (s.get("properties") or {}).get("delegations") or []],
                }
                for s in subnets
            ],
            "peerings_count": len(props.get("virtualNetworkPeerings") or []),
        }

        results.append({
            "id": vnet["id"],
            "subscription_id": subscription_id,
            "resource_group": vnet.get("resourceGroup"),
            "name": vnet.get("name"),
            "type": vnet.get("type", RESOURCE_TYPE),
            "location": vnet.get("location"),
            "sku": None,
            "tags": json.dumps(vnet.get("tags") or {}),
            "is_public": 0,  # VNets themselves aren't public-facing
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": None,
            "pipeline_tag": None,
            "raw_json": json.dumps({**vnet, "_extra": extra}),
        })

        for subnet in subnets:
            subnet_props = subnet.get("properties") or {}
            subnet_name = subnet.get("name")
            subnet_id = subnet.get("id") or (
                f"{vnet['id']}/subnets/{subnet_name}" if subnet_name else None
            )
            if not subnet_name or not subnet_id:
                continue

            subnet_extra = {
                "parent_vnet_id": vnet["id"],
                "parent_vnet_name": vnet.get("name"),
                "parent_vnet_resource_group": vnet.get("resourceGroup"),
                "address_prefix": subnet_props.get("addressPrefix"),
                "address_prefixes": subnet_props.get("addressPrefixes") or [],
                "network_security_group_id": (subnet_props.get("networkSecurityGroup") or {}).get("id"),
                "network_security_group_name": (subnet_props.get("networkSecurityGroup") or {}).get("name"),
                "route_table_id": (subnet_props.get("routeTable") or {}).get("id"),
                "route_table_name": (subnet_props.get("routeTable") or {}).get("name"),
                "delegations": [
                    (d.get("properties") or {}).get("serviceName")
                    for d in subnet_props.get("delegations") or []
                    if (d.get("properties") or {}).get("serviceName")
                ],
            }

            results.append({
                "id": subnet_id,
                "subscription_id": subscription_id,
                "resource_group": vnet.get("resourceGroup"),
                "name": subnet_name,
                "type": "Microsoft.Network/virtualNetworks/subnets",
                "location": vnet.get("location"),
                "sku": None,
                "tags": json.dumps({}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": json.dumps([]),
                "fqdn": None,
                "pipeline_tag": None,
                "raw_json": json.dumps({**subnet, "_extra": subnet_extra}),
            })

    return results
