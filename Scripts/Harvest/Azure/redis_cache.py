"""Harvest Azure Redis Cache instances."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Cache/Redis"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["redis", "list"], subscription_id)
    results = []

    for r in raw:
        props = r.get("properties") or {}
        host = safe_str(props.get("hostName"))

        extra = {
            "sku_name": (r.get("sku") or {}).get("name"),
            "sku_capacity": (r.get("sku") or {}).get("capacity"),
            "redis_version": props.get("redisVersion"),
            "ssl_port": props.get("sslPort"),
            "non_ssl_port_enabled": props.get("enableNonSslPort", False),
            "minimum_tls_version": props.get("minimumTlsVersion"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "replication_mode": props.get("replicationMode"),
        }

        is_public = 1 if props.get("publicNetworkAccess", "Enabled") == "Enabled" else 0

        results.append({
            "id": r["id"],
            "subscription_id": subscription_id,
            "resource_group": r.get("resourceGroup"),
            "name": r.get("name"),
            "type": r.get("type", RESOURCE_TYPE),
            "location": r.get("location"),
            "sku": (r.get("sku") or {}).get("name"),
            "tags": json.dumps(r.get("tags") or {}),
            "is_public": is_public,
            "fqdn": host,
            "pipeline_tag": (r.get("tags") or {}).get("pipeline") or (r.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**r, "_extra": extra}),
        })

    return results
