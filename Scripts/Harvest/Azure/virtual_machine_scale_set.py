"""Harvest Azure virtual machine scale sets."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Compute/virtualMachineScaleSets"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "instance_count": (((resource.get("sku") or {}).get("capacity"))),
            "orchestration_mode": ((resource.get("properties") or {}).get("orchestrationMode")),
            "upgrade_policy_mode": (((resource.get("properties") or {}).get("upgradePolicy") or {}).get("mode")),
        },
    )
