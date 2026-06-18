"""Harvest Azure route tables."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Network/routeTables"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "route_count": len((resource.get("properties") or {}).get("routes") or []),
            "subnet_count": len((resource.get("properties") or {}).get("subnets") or []),
            "disable_bgp_route_propagation": bool((resource.get("properties") or {}).get("disableBgpRoutePropagation")),
        },
    )
