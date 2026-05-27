"""Harvest Azure Service Bus namespaces."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.ServiceBus/namespaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["servicebus", "namespace", "list"], subscription_id)
    results = []

    for ns in raw:
        props = ns.get("properties") or {}
        fqdn = safe_str(props.get("serviceBusEndpoint", "")
                        .replace("https://", "").replace(":443/", "").rstrip("/")) or None

        sku = (ns.get("sku") or {}).get("name")
        is_public = _is_public(props)

        extra = {
            "sku": sku,
            "tier": (ns.get("sku") or {}).get("tier"),
            "status": props.get("status"),
            "zone_redundant": props.get("zoneRedundant", False),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "minimum_tls_version": props.get("minimumTlsVersion"),
            "local_auth_disabled": props.get("disableLocalAuth", False),
        }

        results.append({
            "id": ns["id"],
            "subscription_id": subscription_id,
            "resource_group": ns.get("resourceGroup"),
            "name": ns.get("name"),
            "type": ns.get("type", RESOURCE_TYPE),
            "location": ns.get("location"),
            "sku": sku,
            "tags": json.dumps(ns.get("tags") or {}),
            "is_public": is_public,
            "fqdn": fqdn,
            "pipeline_tag": (ns.get("tags") or {}).get("pipeline") or (ns.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**ns, "_extra": extra}),
        })

    return results


def _is_public(props: dict[str, Any]) -> int:
    """Check if Service Bus namespace is truly internet-accessible."""
    # If public network access is disabled, not public
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0
    
    # Check for network rules (virtual network or IP rules)
    network_rules = props.get("networkRuleSets") or {}
    virtual_network_rules = network_rules.get("virtualNetworkRules") or []
    ip_rules = network_rules.get("ipRules") or []
    
    # If there are any network rules, access is restricted
    if virtual_network_rules or ip_rules:
        return 0
    
    return 1
