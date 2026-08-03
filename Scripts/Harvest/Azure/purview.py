"""Harvest Microsoft Purview accounts and their public/private exposure."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.Purview/accounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []
    for account in raw:
        props = account.get("properties") or {}
        endpoints = props.get("endpoints") or {}
        fqdns = [
            safe_str(urlparse(value).hostname or value)
            for value in endpoints.values()
            if isinstance(value, str) and value
        ]
        fqdn = fqdns[0] if fqdns else safe_str(props.get("endpoint"))
        public_access = safe_str(props.get("publicNetworkAccess") or "Enabled")
        private_connections = props.get("privateEndpointConnections") or []
        results.append({
            "id": account["id"],
            "subscription_id": subscription_id,
            "resource_group": account.get("resourceGroup"),
            "name": account.get("name"),
            "type": account.get("type", RESOURCE_TYPE),
            "location": account.get("location"),
            "sku": (account.get("sku") or {}).get("name"),
            "tags": json.dumps(account.get("tags") or {}),
            "is_public": 1 if fqdn and public_access != "Disabled" else 0,
            "is_restricted": 1 if private_connections or public_access == "Disabled" else 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(value, 443, "https") for value in fqdns]),
            "auth_methods": json.dumps(["azure_ad"]),
            "fqdn": fqdn,
            "pipeline_tag": (account.get("tags") or {}).get("pipeline"),
            "raw_json": json.dumps({
                **account,
                "_extra": {
                    "endpoints": fqdns,
                    "public_network_access": public_access,
                    "private_endpoint_connections": len(private_connections),
                },
            }),
        })
    return results
