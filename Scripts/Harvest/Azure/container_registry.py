"""Harvest Azure Container Registries."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.ContainerRegistry/registries"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["acr", "list"], subscription_id)
    results = []

    for reg in raw:
        props = reg.get("properties") or {}
        login_server = safe_str(props.get("loginServer"))
        is_public, is_restricted, ip_restrictions = _classify_exposure(props)

        endpoints = build_endpoints([(login_server, 443, "https")] if login_server else [])
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "sku": (reg.get("sku") or {}).get("name"),
            "admin_user_enabled": props.get("adminUserEnabled", False),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "zone_redundancy": props.get("zoneRedundancy", "Disabled"),
            "anonymous_pull_enabled": props.get("anonymousPullEnabled", False),
            "network_rule_bypass": props.get("networkRuleBypassOptions", "AzureServices"),
            "network_default_action": (props.get("networkRuleSet") or {}).get("defaultAction", "Allow"),
        }

        results.append({
            "id": reg["id"],
            "subscription_id": subscription_id,
            "resource_group": reg.get("resourceGroup"),
            "name": reg.get("name"),
            "type": reg.get("type", RESOURCE_TYPE),
            "location": reg.get("location"),
            "sku": infer_sku(reg),
            "tags": json.dumps(reg.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": login_server,
            "pipeline_tag": (reg.get("tags") or {}).get("pipeline") or (reg.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**reg, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    if props.get("publicNetworkAccess", "Enabled") != "Enabled":
        return 0, 0, []

    network_rule_set = props.get("networkRuleSet") or {}
    default_action = network_rule_set.get("defaultAction", "Allow")

    if default_action == "Deny":
        cidrs = extract_ip_restrictions(network_acls=network_rule_set)
        return 0, 1, cidrs

    ip_rules = network_rule_set.get("ipRules") or []
    vnet_rules = network_rule_set.get("virtualNetworkRules") or []
    if ip_rules or vnet_rules:
        cidrs = extract_ip_restrictions(network_acls=network_rule_set)
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods = ["azure_ad_token"]
    if props.get("adminUserEnabled", False):
        methods.append("admin_password")
    if props.get("anonymousPullEnabled", False):
        methods.append("anonymous_pull")
    return methods
