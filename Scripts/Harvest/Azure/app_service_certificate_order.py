"""Harvest Azure App Service certificate orders."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.CertificateRegistration/certificateOrders"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "domain_count": len((resource.get("properties") or {}).get("certificates") or []),
            "contact": ((resource.get("properties") or {}).get("contact") or {}).get("email"),
            "sku_name": ((resource.get("sku") or {}).get("name")),
        },
    )
