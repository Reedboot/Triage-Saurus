"""Harvest Azure Bastion hosts."""
from __future__ import annotations

from typing import Any

import json

from ._helpers import az, build_endpoints, infer_fqdn, infer_sku, safe_str

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


def _bastion_show(subscription_id: str, resource_group: str, name: str) -> dict[str, Any] | None:
    details = az(
        ["network", "bastion", "show", "--resource-group", resource_group, "--name", name],
        subscription_id,
    )
    if isinstance(details, dict) and details:
        return details
    resource_id = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Network/bastionHosts/{name}"
    details = az(["resource", "show", "--ids", resource_id], subscription_id)
    if isinstance(details, dict) and details:
        return details
    return None


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for resource in raw:
        resource_group = safe_str(resource.get("resourceGroup"))
        name = safe_str(resource.get("name"))
        detailed = resource
        if resource_group and name:
            fetched = _bastion_show(subscription_id, resource_group, name)
            if isinstance(fetched, dict):
                detailed = fetched

        fqdn = _fqdn(detailed) or infer_fqdn(detailed)
        public_ip_ids = _extract_public_ip_ids(detailed)
        props = detailed.get("properties") or {}
        extra = {
            "ip_configuration_count": len(props.get("ipConfigurations") or []),
            "scale_units": props.get("scaleUnits"),
            "sku_tier": safe_str((detailed.get("sku") or {}).get("tier")),
            "public_ip_resource_ids": public_ip_ids,
        }

        results.append(
            {
                "id": detailed.get("id") or resource.get("id"),
                "subscription_id": subscription_id,
                "resource_group": resource_group or resource.get("resourceGroup"),
                "name": name or resource.get("name"),
                "type": detailed.get("type", resource.get("type", RESOURCE_TYPE)),
                "location": detailed.get("location", resource.get("location")),
                "sku": infer_sku(detailed),
                "tags": json.dumps(detailed.get("tags") or resource.get("tags") or {}),
                "is_public": 1 if _is_public(detailed) else 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
                "auth_methods": json.dumps([]),
                "fqdn": fqdn,
                "pipeline_tag": None,
                "raw_json": json.dumps({**detailed, "_extra": extra}),
            }
        )

    return results
