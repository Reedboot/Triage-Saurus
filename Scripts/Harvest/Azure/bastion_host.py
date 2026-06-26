"""Harvest Azure Bastion hosts."""
from __future__ import annotations

from typing import Any

from ._helpers import safe_str
from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Network/bastionHosts"


def _fqdn(resource: dict[str, Any]) -> str | None:
    props = resource.get("properties") or {}
    dns = props.get("dnsSettings") or {}
    return safe_str(dns.get("fqdn") or props.get("dnsName"))


def _extract_public_ip_ids(resource: dict[str, Any]) -> list[str]:
    props = resource.get("properties") or {}
    found: list[str] = []
    seen: set[str] = set()
    for config in props.get("ipConfigurations") or []:
        cfg_props = config.get("properties") or {}
        refs = [
            (cfg_props.get("publicIPAddress") or {}).get("id"),
            cfg_props.get("publicIPAddressId"),
        ]
        for ref in refs:
            value = safe_str(ref)
            if value and value.lower() not in seen:
                seen.add(value.lower())
                found.append(value)
    return found


def _is_public(resource: dict[str, Any]) -> bool:
    return bool(_extract_public_ip_ids(resource))


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        fqdn_fn=_fqdn,
        is_public_fn=_is_public,
        extra_fn=lambda resource: {
            "ip_configuration_count": len((resource.get("properties") or {}).get("ipConfigurations") or []),
            "scale_units": (resource.get("properties") or {}).get("scaleUnits"),
            "sku_tier": safe_str((resource.get("sku") or {}).get("tier")),
            "public_ip_resource_ids": _extract_public_ip_ids(resource),
        },
    )
