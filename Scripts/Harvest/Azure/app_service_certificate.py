"""Harvest Azure App Service certificates."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Web/certificates"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "key_vault_secret_id": ((resource.get("properties") or {}).get("keyVaultId")),
            "subject_name": ((resource.get("properties") or {}).get("subjectName")),
            "thumbprint": ((resource.get("properties") or {}).get("thumbprint")),
        },
    )
