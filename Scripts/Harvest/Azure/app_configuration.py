"""Harvest Azure App Configuration stores."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.AppConfiguration/configurationStores"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appconfig", "list"], subscription_id)
    results = []

    for store in raw:
        props = store.get("properties") or {}
        endpoint = safe_str(props.get("endpoint", "").replace("https://", "").rstrip("/")) or None

        is_public, is_restricted, ip_restrictions = _classify_exposure(props)
        endpoints = build_endpoints([(endpoint, 443, "https")] if endpoint else [])
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "sku": (store.get("sku") or {}).get("name"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "disable_local_auth": props.get("disableLocalAuth", False),
            "soft_delete_retention_days": props.get("softDeleteRetentionInDays"),
            "enable_purge_protection": props.get("enablePurgeProtection", False),
            "creation_date": props.get("creationDate"),
        }

        results.append({
            "id": store["id"],
            "subscription_id": subscription_id,
            "resource_group": store.get("resourceGroup"),
            "name": store.get("name"),
            "type": store.get("type", RESOURCE_TYPE),
            "location": store.get("location"),
            "sku": (store.get("sku") or {}).get("name"),
            "tags": json.dumps(store.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": endpoint,
            "pipeline_tag": (store.get("tags") or {}).get("pipeline") or (store.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**store, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_restriction_cidrs)."""
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0, 0, []

    network_acls = props.get("networkAcls") or {}
    cidrs = extract_ip_restrictions(network_acls=network_acls)
    if cidrs:
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("access_key")
    return methods
