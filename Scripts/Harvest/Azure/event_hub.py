"""Harvest Azure Event Hub namespaces."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.EventHub/namespaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["eventhubs", "namespace", "list"], subscription_id)
    results = []

    for ns in raw:
        props = ns.get("properties") or {}
        # serviceBusEndpoint looks like https://name.servicebus.windows.net:443/
        endpoint_raw = props.get("serviceBusEndpoint", "")
        fqdn = safe_str(
            endpoint_raw.replace("https://", "").replace(":443/", "").rstrip("/")
        ) or None

        sku = (ns.get("sku") or {}).get("name")
        is_public = _is_public(props)

        extra = {
            "sku": sku,
            "tier": (ns.get("sku") or {}).get("tier"),
            "throughput_units": (ns.get("sku") or {}).get("capacity"),
            "auto_inflate_enabled": props.get("isAutoInflateEnabled", False),
            "maximum_throughput_units": props.get("maximumThroughputUnits", 0),
            "kafka_enabled": props.get("kafkaEnabled", False),
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
    """Check if Event Hub namespace is truly internet-accessible."""
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
