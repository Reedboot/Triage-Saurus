"""Harvest Azure Private Link Services and their load-balancer targets."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Network/privateLinkServices"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "private-link-service", "list"], subscription_id)
    results: list[dict[str, Any]] = []

    for service in raw:
        props = service.get("properties") or {}
        frontend_ids: list[str] = []
        for config in props.get("loadBalancerFrontendIpConfigurations") or []:
            config_id = config.get("id") if isinstance(config, dict) else config
            if config_id:
                frontend_ids.append(str(config_id))
        load_balancer_ids = sorted({
            value.split("/frontendIPConfigurations/", 1)[0]
            for value in frontend_ids
            if "/frontendIPConfigurations/" in value
        })
        connections = props.get("privateEndpointConnections") or []
        extra = {
            "load_balancer_frontend_ids": frontend_ids,
            "load_balancer_ids": load_balancer_ids,
            "private_endpoint_connection_count": len(connections),
            "visibility": props.get("visibility"),
            "auto_approval": props.get("autoApproval"),
        }
        results.append({
            "id": service["id"],
            "subscription_id": subscription_id,
            "resource_group": service.get("resourceGroup"),
            "name": service.get("name"),
            "type": service.get("type", RESOURCE_TYPE),
            "location": service.get("location"),
            "sku": None,
            "tags": json.dumps(service.get("tags") or {}),
            "is_public": 0,
            "is_restricted": 1 if connections else 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": None,
            "pipeline_tag": None,
            "raw_json": json.dumps({**service, "_extra": extra}),
        })
    return results
