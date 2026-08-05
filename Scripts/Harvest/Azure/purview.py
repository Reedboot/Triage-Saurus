"""Harvest Microsoft Purview accounts and their public/private exposure."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from ._helpers import az, az_resource_show, build_endpoints, classify_network_access, safe_str

RESOURCE_TYPE = "Microsoft.Purview/accounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []
    for account in raw:
        list_props = account.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props
            for key in ("publicNetworkAccess", "privateEndpointConnections", "endpoints")
        )
        detailed = (
            az_resource_show(account.get("id", ""), subscription_id, runner=az)
            if account.get("id") and needs_detail
            else None
        )
        if detailed:
            account = {**account, **detailed}
        props = account.get("properties") or {}
        endpoints = props.get("endpoints") or {}
        fqdns = [
            safe_str(urlparse(value).hostname or value)
            for value in endpoints.values()
            if isinstance(value, str) and value
        ]
        fqdn = fqdns[0] if fqdns else safe_str(props.get("endpoint"))
        public_access = safe_str(props.get("publicNetworkAccess"))
        private_connections = props.get("privateEndpointConnections") or []
        is_public, is_restricted, ip_restrictions, exposure_class = classify_network_access(
            props,
            endpoint_present=bool(fqdns),
            private_endpoint_connections=private_connections,
        )
        results.append({
            "id": account["id"],
            "subscription_id": subscription_id,
            "resource_group": account.get("resourceGroup"),
            "name": account.get("name"),
            "type": account.get("type", RESOURCE_TYPE),
            "location": account.get("location"),
            "sku": (account.get("sku") or {}).get("name"),
            "tags": json.dumps(account.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
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
                    "exposure_class": exposure_class,
                },
            }),
        })
    return results
