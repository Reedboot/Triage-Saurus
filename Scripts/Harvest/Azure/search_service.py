"""Harvest Azure Search services."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Search/searchServices"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for service in raw:
        props = service.get("properties") or {}
        endpoint = safe_str(props.get("endpoint"))
        public_network_access = safe_str(props.get("publicNetworkAccess") or "Enabled")
        disable_local_auth = bool(props.get("disableLocalAuth", False))

        results.append({
            "id": service["id"],
            "subscription_id": subscription_id,
            "resource_group": service.get("resourceGroup"),
            "name": service.get("name"),
            "type": service.get("type", RESOURCE_TYPE),
            "location": service.get("location"),
            "sku": infer_sku(service),
            "tags": json.dumps(service.get("tags") or {}),
            "is_public": 1 if endpoint and public_network_access != "Disabled" else 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(endpoint, 443, "https")] if endpoint else []),
            "auth_methods": json.dumps([] if disable_local_auth else ["api_key"]),
            "fqdn": endpoint,
            "pipeline_tag": (service.get("tags") or {}).get("pipeline") or (service.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **service,
                "_extra": {
                    "endpoint": endpoint,
                    "public_network_access": public_network_access,
                    "disable_local_auth": disable_local_auth,
                },
            }),
        })

    return results
