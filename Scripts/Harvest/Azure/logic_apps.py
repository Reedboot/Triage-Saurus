"""Harvest Azure Logic App workflows."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Logic/workflows"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for workflow in raw:
        props = workflow.get("properties") or {}
        endpoint = safe_str(props.get("accessEndpoint"))
        state = safe_str(props.get("state"))

        results.append({
            "id": workflow["id"],
            "subscription_id": subscription_id,
            "resource_group": workflow.get("resourceGroup"),
            "name": workflow.get("name"),
            "type": workflow.get("type", RESOURCE_TYPE),
            "location": workflow.get("location"),
            "sku": infer_sku(workflow),
            "tags": json.dumps(workflow.get("tags") or {}),
            "is_public": 1 if endpoint else 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(endpoint, 443, "https")] if endpoint else []),
            "auth_methods": json.dumps([]),
            "fqdn": endpoint,
            "pipeline_tag": (workflow.get("tags") or {}).get("pipeline") or (workflow.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **workflow,
                "_extra": {
                    "access_endpoint": endpoint,
                    "state": state,
                    "version": props.get("version"),
                },
            }),
        })

    return results
