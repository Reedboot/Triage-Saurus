"""Harvest Azure activity log alerts."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Insights/activityLogAlerts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "scope_count": len((resource.get("properties") or {}).get("scopes") or []),
            "enabled": bool((resource.get("properties") or {}).get("enabled", True)),
            "condition_type": (((resource.get("properties") or {}).get("condition") or {}).get("allOf", [{}])[0].get("field")),
        },
    )
