"""Harvest Azure Cognitive Services accounts (OpenAI, Form Recognizer, etc.)."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.CognitiveServices/accounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["cognitiveservices", "account", "list"], subscription_id)
    results = []

    for acct in raw:
        props = acct.get("properties") or {}
        endpoint = safe_str(
            props.get("endpoint", "").replace("https://", "").rstrip("/")
        ) or None

        kind = acct.get("kind", "")
        is_public, is_restricted, ip_restrictions = _classify_exposure(props)

        endpoints = build_endpoints([(endpoint, 443, "https")] if endpoint else [])
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "kind": kind,
            "sku": (acct.get("sku") or {}).get("name"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "network_default_action": (props.get("networkAcls") or {}).get("defaultAction", "Allow"),
            "disable_local_auth": props.get("disableLocalAuth", False),
            "custom_subdomain": props.get("customSubDomainName"),
            "restore": props.get("restore", False),
        }

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": (acct.get("sku") or {}).get("name"),
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": endpoint,
            "pipeline_tag": (acct.get("tags") or {}).get("pipeline") or (acct.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    if props.get("publicNetworkAccess", "Enabled") != "Enabled":
        return 0, 0, []

    network_acls = props.get("networkAcls") or {}
    default_action = network_acls.get("defaultAction", "Allow")

    if default_action == "Deny":
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    ip_rules = network_acls.get("ipRules") or []
    vnet_rules = network_acls.get("virtualNetworkRules") or []
    if ip_rules or vnet_rules:
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("api_key")
    return methods
