"""Harvest Azure user-assigned managed identities."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.ManagedIdentity/userAssignedIdentities"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "principal_id": ((resource.get("properties") or {}).get("principalId")),
            "client_id": ((resource.get("properties") or {}).get("clientId")),
        },
    )
