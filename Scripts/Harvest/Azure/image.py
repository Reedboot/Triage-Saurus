"""Harvest Azure managed images."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Compute/images"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "os_type": ((resource.get("properties") or {}).get("storageProfile") or {}).get("osDisk", {}).get("osType"),
            "hyper_v_generation": (resource.get("properties") or {}).get("hyperVGeneration"),
        },
    )
