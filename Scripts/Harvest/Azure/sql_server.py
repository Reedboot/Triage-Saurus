"""Harvest Azure SQL Servers."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Sql/servers"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["sql", "server", "list"], subscription_id)
    results = []

    for server in raw:
        props = server.get("properties") or {}
        fqdn = safe_str(props.get("fullyQualifiedDomainName"))

        extra = {
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "minimal_tls_version": props.get("minimalTlsVersion"),
            "admin_login": props.get("administratorLogin"),
        }

        results.append({
            "id": server["id"],
            "subscription_id": subscription_id,
            "resource_group": server.get("resourceGroup"),
            "name": server.get("name"),
            "type": server.get("type", RESOURCE_TYPE),
            "location": server.get("location"),
            "sku": None,
            "tags": json.dumps(server.get("tags") or {}),
            "is_public": 1 if props.get("publicNetworkAccess", "Enabled") == "Enabled" else 0,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**server, "_extra": extra}),
        })

    return results
