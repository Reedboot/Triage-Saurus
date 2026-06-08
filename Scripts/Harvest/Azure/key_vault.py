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
        props = kv.get("properties") or kv
        network_acls = _get_network_acls(props)
        vault_uri = props.get("vaultUri")
        fqdn = safe_str(vault_uri.replace("https://", "").rstrip("/")) if vault_uri else None

        is_public, is_restricted, ip_restrictions = _classify_exposure(props)
        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(["azure_ad", "managed_identity"])
        public_network_access = _get_public_network_access(props)
        network_default_action = _get_network_default_action(props)
        network_access_mode = (
            "private"
            if public_network_access.lower() == "disabled"
            else "ip_restricted" if is_restricted else "public"
        )

        extra = {
            "enable_soft_delete": props.get("enableSoftDelete", True),
            "enable_purge_protection": props.get("enablePurgeProtection", False),
            "public_network_access": public_network_access,
            "network_default_action": network_default_action,
            "network_access_mode": network_access_mode,
            "ip_rule_count": len(network_acls.get("ipRules") or []),
            "virtual_network_rule_count": len(network_acls.get("virtualNetworkRules") or []),
            "ip_restriction_count": len(ip_restrictions),
            "sku_family": (props.get("sku") or {}).get("family"),
            "managed_identity_supported": True,
            "managed_identity_required": False,
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
    public_network_access = _get_public_network_access(props)
    if public_network_access.lower() == "disabled":
        return 0, 0, []

    network_acls = _get_network_acls(props)
    default_action = _get_network_default_action(props)

    if default_action.lower() == "deny":
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    ip_rules = network_acls.get("ipRules") or network_acls.get("ip_rules") or []
    vnet_rules = network_acls.get("virtualNetworkRules") or network_acls.get("virtual_network_rules") or []
    if ip_rules or vnet_rules:
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    return 1, 0, []


def _get_network_acls(props: dict[str, Any]) -> dict[str, Any]:
    return props.get("networkAcls") or props.get("network_acls") or {}


def _get_public_network_access(props: dict[str, Any]) -> str:
    return safe_str(props.get("publicNetworkAccess") or props.get("public_network_access") or "Enabled") or "Enabled"


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = _get_network_acls(props)
    return safe_str(network_acls.get("defaultAction") or network_acls.get("default_action") or "Allow") or "Allow"
