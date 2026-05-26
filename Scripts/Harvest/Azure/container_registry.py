"""Harvest Azure Container Registries."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str, infer_sku

RESOURCE_TYPE = "Microsoft.ContainerRegistry/registries"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["acr", "list"], subscription_id)
    results = []

    for reg in raw:
        props = reg.get("properties") or {}
        login_server = safe_str(props.get("loginServer"))

        extra = {
            "sku": (reg.get("sku") or {}).get("name"),
            "admin_user_enabled": props.get("adminUserEnabled", False),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "zone_redundancy": props.get("zoneRedundancy", "Disabled"),
            "anonymous_pull_enabled": props.get("anonymousPullEnabled", False),
            "network_rule_bypass": props.get("networkRuleBypassOptions", "AzureServices"),
            "network_default_action": (props.get("networkRuleSet") or {}).get("defaultAction", "Allow"),
        }

        # Public if public network access is Enabled and no restrictions
        network_default = (props.get("networkRuleSet") or {}).get("defaultAction", "Allow")
        is_public = (
            1 if props.get("publicNetworkAccess", "Enabled") == "Enabled"
            and network_default == "Allow"
            else 0
        )

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
            "fqdn": login_server,
            "pipeline_tag": (reg.get("tags") or {}).get("pipeline") or (reg.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**reg, "_extra": extra}),
        })

    return results
