"""Harvest Azure public IP addresses."""
from __future__ import annotations

from typing import Any

from ._helpers import safe_str
from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Network/publicIPAddresses"


def _fqdn(resource: dict[str, Any]) -> str | None:
    props = resource.get("properties") or {}
    dns = props.get("dnsSettings") or {}
    return safe_str(dns.get("fqdn") or props.get("fqdn"))


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        fqdn_fn=_fqdn,
        is_public_fn=lambda resource: True,
        extra_fn=lambda resource: {
            "ip_address": (resource.get("properties") or {}).get("ipAddress"),
            "allocation_method": ((resource.get("properties") or {}).get("publicIPAllocationMethod")),
            "idle_timeout_minutes": (resource.get("properties") or {}).get("idleTimeoutInMinutes"),
        },
    )
