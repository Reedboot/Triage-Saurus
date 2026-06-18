"""Harvest Azure Network Security Groups."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Network/networkSecurityGroups"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "security_rule_count": len((resource.get("properties") or {}).get("securityRules") or []),
            "default_rule_count": len((resource.get("properties") or {}).get("defaultSecurityRules") or []),
            "subnet_count": len((resource.get("properties") or {}).get("subnets") or []),
        },
    )
