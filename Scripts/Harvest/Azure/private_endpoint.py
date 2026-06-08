"""Harvest Azure Private Endpoints.

Private Endpoints are security-critical: they show which services are accessed
privately over the VNet rather than over public internet. Capturing them lets
security findings reference whether a service *has* a private endpoint in place.
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Network/privateEndpoints"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "private-endpoint", "list"], subscription_id)
    results = []

    for pe in raw:
        props = pe.get("properties") or pe

        # The linked resource is in privateLinkServiceConnections[0].privateLinkServiceId
        plscs = props.get("privateLinkServiceConnections") or []
        linked_resource_id = None
        group_ids: list[str] = []
        if plscs:
            conn_props = (plscs[0].get("properties") or {})
            linked_resource_id = conn_props.get("privateLinkServiceId")
            group_ids = conn_props.get("groupIds") or []

        # Custom DNS records
        custom_dns = props.get("customDnsConfigs") or []
        fqdns = [safe_str(d.get("fqdn")) for d in custom_dns if d.get("fqdn")]
        fqdn = fqdns[0] if fqdns else None

        nic_ids = [n.get("id") for n in (props.get("networkInterfaces") or [])]

        extra = {
            "linked_resource_id": linked_resource_id,
            "linked_resource_type": linked_resource_id.split("/providers/")[-1].split("/")[0] + "/" +
                                    linked_resource_id.split("/providers/")[-1].split("/")[1]
                                    if linked_resource_id and "/providers/" in linked_resource_id else None,
            "group_ids": group_ids,
            "subnet_id": (props.get("subnet") or {}).get("id"),
            "custom_dns_fqdns": fqdns,
            "nic_ids": nic_ids,
        }

        results.append({
            "id": pe["id"],
            "subscription_id": subscription_id,
            "resource_group": pe.get("resourceGroup"),
            "name": pe.get("name"),
            "type": pe.get("type", RESOURCE_TYPE),
            "location": pe.get("location"),
            "sku": None,
            "tags": json.dumps(pe.get("tags") or {}),
            "is_public": 0,  # Private endpoints are always private by definition
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**pe, "_extra": extra}),
        })

    return results
