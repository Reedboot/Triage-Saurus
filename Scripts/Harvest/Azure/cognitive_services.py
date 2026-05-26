"""Harvest Azure Cognitive Services accounts (OpenAI, Form Recognizer, etc.)."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

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

        extra = {
            "kind": kind,
            "sku": (acct.get("sku") or {}).get("name"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "network_default_action": (props.get("networkAcls") or {}).get("defaultAction", "Allow"),
            "disable_local_auth": props.get("disableLocalAuth", False),
            "custom_subdomain": props.get("customSubDomainName"),
            "restore": props.get("restore", False),
        }

        network_default = (props.get("networkAcls") or {}).get("defaultAction", "Allow")
        is_public = (
            1 if props.get("publicNetworkAccess", "Enabled") == "Enabled"
            and network_default == "Allow"
            else 0
        )

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
            "fqdn": endpoint,
            "pipeline_tag": (acct.get("tags") or {}).get("pipeline") or (acct.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results
