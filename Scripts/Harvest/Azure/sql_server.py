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
        is_public = _is_public(server, subscription_id)

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
            "is_public": is_public,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**server, "_extra": extra}),
        })

    return results


def _is_public(server: dict[str, Any], subscription_id: str) -> int:
    """Check if SQL Server is truly internet-accessible."""
    props = server.get("properties") or {}
    
    # If public network access is disabled, it's not public
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0
    
    # Check firewall rules
    server_name = server.get("name")
    resource_group = server.get("resourceGroup")
    if server_name and resource_group:
        try:
            firewall_rules = az(["sql", "server", "firewall-rule", "list", 
                                "--name", server_name, "--resource-group", resource_group], 
                               subscription_id)
            # If there are firewall rules, access is restricted
            if firewall_rules:
                return 0
        except Exception:
            pass  # If we can't fetch rules, assume public
    
    return 1
