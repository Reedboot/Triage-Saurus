"""Harvest Azure Data Factory instances."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az

RESOURCE_TYPE = "Microsoft.DataFactory/factories"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    # az datafactory list requires the datafactory extension
    raw = az(["datafactory", "list"], subscription_id)
    if not raw:
        # Fallback: use generic resource list
        raw = az(
            ["resource", "list", "--resource-type", RESOURCE_TYPE],
            subscription_id,
        )
    results = []

    for factory in raw:
        props = factory.get("properties") or {}
        is_public = _is_public(props)

        extra = {
            "provisioning_state": props.get("provisioningState"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "global_parameters_count": len(props.get("globalParameters") or {}),
            "managed_virtual_network_enabled": bool(
                (props.get("managedVirtualNetwork") or {}).get("type")
            ),
            "git_config_type": (props.get("repoConfiguration") or {}).get("type"),
        }

        results.append({
            "id": factory["id"],
            "subscription_id": subscription_id,
            "resource_group": factory.get("resourceGroup"),
            "name": factory.get("name"),
            "type": factory.get("type", RESOURCE_TYPE),
            "location": factory.get("location"),
            "sku": None,
            "tags": json.dumps(factory.get("tags") or {}),
            "is_public": is_public,
            "fqdn": None,
            "pipeline_tag": (factory.get("tags") or {}).get("pipeline") or (factory.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**factory, "_extra": extra}),
        })

    return results


def _is_public(props: dict[str, Any]) -> int:
    """Check if Data Factory is truly internet-accessible."""
    # If public network access is disabled, not public
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0
    
    # If managed VNet is enabled, it's not internet-facing
    if (props.get("managedVirtualNetwork") or {}).get("type"):
        return 0
    
    return 1
