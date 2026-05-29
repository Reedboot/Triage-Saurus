"""Harvest Azure Cosmos DB accounts."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.DocumentDB/databaseAccounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["cosmosdb", "list"], subscription_id)
    results = []

    for acct in raw:
        props = acct.get("properties") or {}
        doc_endpoint = props.get("documentEndpoint", "")
        fqdn = safe_str(doc_endpoint.replace("https://", "").rstrip("/")) or None

        is_public, is_restricted, ip_restrictions = _classify_exposure(props)
        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "kind": acct.get("kind"),
            "api": _infer_api(acct),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "enable_free_tier": props.get("enableFreeTier", False),
            "enable_multiple_write_locations": props.get("enableMultipleWriteLocations", False),
            "backup_policy": (props.get("backupPolicy") or {}).get("type"),
            "locations": [loc.get("locationName") for loc in (props.get("readLocations") or [])],
            "ip_rules_count": len(props.get("ipRules") or []),
        }

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": None,
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": (acct.get("tags") or {}).get("pipeline") or (acct.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    if props.get("publicNetworkAccess", "Enabled") != "Enabled":
        return 0, 0, []

    ip_rules = props.get("ipRules") or []

    # virtualNetworkRules are only enforced when isVirtualNetworkFilterEnabled is True.
    # If the flag is absent or False the rules are stored but have no effect, so the
    # account is still publicly reachable and must not be mis-classified as restricted.
    vnet_filter_enabled = props.get("isVirtualNetworkFilterEnabled", False)
    vnet_rules = (props.get("virtualNetworkRules") or []) if vnet_filter_enabled else []

    if ip_rules or vnet_rules:
        cidrs = extract_ip_restrictions(ip_rules=ip_rules, vnet_rules=vnet_rules,
                                        rule_value_key="ipAddressOrRange")
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    # Azure AD (RBAC) is always available; local auth (primary key) can be disabled
    disable_local = props.get("disableLocalAuth", False)
    if not disable_local:
        methods.append("primary_key")
    methods.append("azure_ad")
    return methods


def _infer_api(acct: dict[str, Any]) -> str:
    """Return a human-readable Cosmos DB API name."""
    caps = {c.get("name") for c in (acct.get("properties", {}).get("capabilities") or [])}
    kind = acct.get("kind", "")
    if "EnableMongo" in caps or kind == "MongoDB":
        return "MongoDB"
    if "EnableCassandra" in caps:
        return "Cassandra"
    if "EnableTable" in caps:
        return "Table"
    if "EnableGremlin" in caps:
        return "Gremlin"
    return "NoSQL"
