"""Harvest Azure Monitor action groups."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Insights/actionGroups"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "receiver_count": len((resource.get("properties") or {}).get("emailReceivers") or [])
            + len((resource.get("properties") or {}).get("smsReceivers") or [])
            + len((resource.get("properties") or {}).get("webhookReceivers") or [])
            + len((resource.get("properties") or {}).get("voiceReceivers") or [])
            + len((resource.get("properties") or {}).get("armRoleReceivers") or []),
            "short_name": ((resource.get("properties") or {}).get("groupShortName")),
        },
    )
