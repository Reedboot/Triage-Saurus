"""Harvest Azure virtual machines."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Compute/virtualMachines"


def _fqdn(resource: dict[str, Any]) -> str | None:
    props = resource.get("properties") or {}
    return safe_str(
        resource.get("fqdn")
        or resource.get("dnsName")
        or props.get("fqdn")
        or props.get("dnsName")
    )


def _is_public(resource: dict[str, Any]) -> bool:
    return bool(safe_str(resource.get("publicIps") or resource.get("publicIpAddress")))


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    rows = az(["vm", "list", "-d"], subscription_id)
    results: list[dict[str, Any]] = []

    for vm in rows:
        fqdn = _fqdn(vm)
        is_public = 1 if _is_public(vm) else 0
        private_ips = safe_str(vm.get("privateIps"))
        public_ips = safe_str(vm.get("publicIps"))
        os_type = safe_str(vm.get("osType"))
        power_state = safe_str(vm.get("powerState"))

        results.append({
            "id": vm["id"],
            "subscription_id": subscription_id,
            "resource_group": vm.get("resourceGroup"),
            "name": vm.get("name"),
            "type": vm.get("type", RESOURCE_TYPE),
            "location": vm.get("location"),
            "sku": safe_str((vm.get("hardwareProfile") or {}).get("vmSize")) or safe_str(vm.get("vmSize")),
            "tags": json.dumps(vm.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": 1 if is_public else 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps(["ssh", "rdp"]),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({
                **vm,
                "_extra": {
                    "public_ips": public_ips,
                    "private_ips": private_ips,
                    "os_type": os_type,
                    "power_state": power_state,
                },
            }),
        })

    return results
