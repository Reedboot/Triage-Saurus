"""Harvest Azure Service Bus namespaces."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.ServiceBus/namespaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["servicebus", "namespace", "list"], subscription_id)
    results = []

    for ns in raw:
        props = ns.get("properties") or ns
        fqdn = safe_str(props.get("serviceBusEndpoint", "")
                        .replace("https://", "").replace(":443/", "").rstrip("/")) or None

        sku = (ns.get("sku") or {}).get("name")
        is_public, is_restricted, ip_restrictions = _classify_exposure(props)

        endpoints = build_endpoints([
            (fqdn, 5671, "amqp+tls"),
            (fqdn, 443, "https"),
        ] if fqdn else [])
        auth_methods = json.dumps(_get_auth_methods(props))

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
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": (ns.get("tags") or {}).get("pipeline") or (ns.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**ns, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0, 0, []

    network_rules = props.get("networkRuleSets") or {}
    vnet_rules = network_rules.get("virtualNetworkRules") or []
    ip_rules = network_rules.get("ipRules") or []

    if vnet_rules or ip_rules:
        cidrs = extract_ip_restrictions(ip_rules=ip_rules, vnet_rules=vnet_rules)
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("sas_key")
    return methods
