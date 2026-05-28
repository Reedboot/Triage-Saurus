"""Harvest Azure App Configuration stores."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.AppConfiguration/configurationStores"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appconfig", "list"], subscription_id)
    results = []

    for store in raw:
        props = store.get("properties") or {}
        endpoint = safe_str(props.get("endpoint", "").replace("https://", "").rstrip("/")) or None

        is_public = 1 if props.get("publicNetworkAccess", "Enabled") == "Enabled" else 0
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
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": endpoint,
            "pipeline_tag": (store.get("tags") or {}).get("pipeline") or (store.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**store, "_extra": extra}),
        })

    return results


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("access_key")
    return methods
