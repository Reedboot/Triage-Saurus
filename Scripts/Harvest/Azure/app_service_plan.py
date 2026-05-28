"""Harvest Azure App Service Plans."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_sku

RESOURCE_TYPE = "Microsoft.Web/serverfarms"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appservice", "plan", "list"], subscription_id)
    results = []

    for plan in raw:
        props = plan.get("properties") or {}
        sku_info = plan.get("sku") or {}

        extra = {
            "os_type": "Windows" if not props.get("reserved") else "Linux",
            "sku_tier": sku_info.get("tier"),
            "sku_size": sku_info.get("size"),
            "current_number_of_workers": props.get("currentNumberOfWorkers", 0),
            "maximum_number_of_workers": props.get("maximumNumberOfWorkers", 0),
            "is_spot": props.get("isSpot", False),
            "per_site_scaling": props.get("perSiteScaling", False),
            "number_of_sites": props.get("numberOfSites", 0),
            "zone_redundant": props.get("zoneRedundant", False),
        }

        results.append({
            "id": plan["id"],
            "subscription_id": subscription_id,
            "resource_group": plan.get("resourceGroup"),
            "name": plan.get("name"),
            "type": plan.get("type", RESOURCE_TYPE),
            "location": plan.get("location"),
            "sku": infer_sku(plan),
            "tags": json.dumps(plan.get("tags") or {}),
            "is_public": 0,  # App Service Plans are control-plane resources, not directly public
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": None,
            "pipeline_tag": (plan.get("tags") or {}).get("pipeline") or (plan.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**plan, "_extra": extra}),
        })

    return results
