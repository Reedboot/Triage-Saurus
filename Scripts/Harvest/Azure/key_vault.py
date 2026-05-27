"""Harvest Azure Key Vaults."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.KeyVault/vaults"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["keyvault", "list"], subscription_id)
    results = []

    for kv in raw:
        props = kv.get("properties") or {}
        vault_uri = props.get("vaultUri")
        fqdn = safe_str(vault_uri.replace("https://", "").rstrip("/")) if vault_uri else None

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
            "is_public": _is_public(props),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**kv, "_extra": extra}),
        })

    return results


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = props.get("networkAcls") or {}
    return network_acls.get("defaultAction", "Allow")


def _is_public(props: dict[str, Any]) -> int:
    # Public only if network access is enabled AND not IP-restricted
    if props.get("publicNetworkAccess") == "Disabled":
        return 0
    
    network_acls = props.get("networkAcls") or {}
    default_action = network_acls.get("defaultAction", "Allow")
    
    # If default action is "Deny", it's IP-restricted
    if default_action == "Deny":
        return 0
    
    # Check for VNet or IP rules restricting access
    virtual_network_rules = network_acls.get("virtualNetworkRules") or []
    ip_rules = network_acls.get("ipRules") or []
    
    if virtual_network_rules or ip_rules:
        return 0  # IP-restricted
    
    return 1
