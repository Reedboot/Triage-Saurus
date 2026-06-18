"""Harvest Azure Log Analytics workspaces."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.OperationalInsights/workspaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "retention_in_days": ((resource.get("properties") or {}).get("retentionInDays")),
            "sku_tier": ((resource.get("sku") or {}).get("tier")),
            "workspace_casing": (resource.get("properties") or {}).get("workspaceCasing"),
        },
    )
