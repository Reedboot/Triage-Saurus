"""Harvest Azure Key Vaults."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.KeyVault/vaults"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["keyvault", "list"], subscription_id)
    results = []

    for kv in raw:
        props = kv.get("properties") or {}
        vault_uri = props.get("vaultUri")
        fqdn = safe_str(vault_uri.replace("https://", "").rstrip("/")) if vault_uri else None

        is_public, is_restricted, ip_restrictions = _classify_exposure(props)
        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(["azure_ad"])  # Key Vault always requires AAD

        extra = {
            "enable_soft_delete": props.get("enableSoftDelete", True),
            "enable_purge_protection": props.get("enablePurgeProtection", False),
            "network_default_action": _get_network_default_action(props),
            "sku_family": (props.get("sku") or {}).get("family"),
        }

        results.append({
            "id": kv["id"],
            "subscription_id": subscription_id,
            "resource_group": kv.get("resourceGroup"),
            "name": kv.get("name"),
            "type": kv.get("type", RESOURCE_TYPE),
            "location": kv.get("location"),
            "sku": safe_str((props.get("sku") or {}).get("name")),
            "tags": json.dumps(kv.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**kv, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_cidrs)."""
    if props.get("publicNetworkAccess") == "Disabled":
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


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = props.get("networkAcls") or {}
    return network_acls.get("defaultAction", "Allow")
