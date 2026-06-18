"""Harvest Azure load balancers."""
from __future__ import annotations

from typing import Any

from ._helpers import safe_str
from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Network/loadBalancers"


def _is_public(resource: dict[str, Any]) -> bool:
    props = resource.get("properties") or {}
    for config in props.get("frontendIPConfigurations") or []:
        cfg_props = config.get("properties") or config
        if (cfg_props.get("publicIPAddress") or {}).get("id"):
            return True
        if cfg_props.get("publicIPAddressId"):
            return True
    return False


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        is_public_fn=_is_public,
        extra_fn=lambda resource: {
            "frontend_ip_configuration_count": len((resource.get("properties") or {}).get("frontendIPConfigurations") or []),
            "backend_pool_count": len((resource.get("properties") or {}).get("backendAddressPools") or []),
            "probe_count": len((resource.get("properties") or {}).get("probes") or []),
            "sku_tier": safe_str((resource.get("sku") or {}).get("tier")),
        },
    )
