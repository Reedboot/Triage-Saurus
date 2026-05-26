"""Harvest Azure Storage Accounts."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Storage/storageAccounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["storage", "account", "list"], subscription_id)
    results = []

    for acct in raw:
        props = acct.get("properties") or {}
        fqdn = _get_primary_endpoint(props)

        extra = {
            "allow_blob_public_access": props.get("allowBlobPublicAccess", False),
            "minimum_tls_version": props.get("minimumTlsVersion"),
            "https_only": props.get("supportsHttpsTrafficOnly", True),
            "network_default_action": _get_network_default_action(props),
            "kind": acct.get("kind"),
        }

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": infer_sku(acct),
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": _is_public(props),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results


def _get_primary_endpoint(props: dict[str, Any]) -> str | None:
    endpoints = props.get("primaryEndpoints") or {}
    blob = endpoints.get("blob")
    if blob:
        # Strip https:// and trailing slash
        return safe_str(blob.replace("https://", "").replace("http://", "").rstrip("/"))
    return None


def _is_public(props: dict[str, Any]) -> int:
    # Public if blob public access is allowed OR network default action is Allow
    if props.get("allowBlobPublicAccess"):
        return 1
    if _get_network_default_action(props) == "Allow":
        return 1
    return 0


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = props.get("networkAcls") or {}
    return network_acls.get("defaultAction", "Allow")
