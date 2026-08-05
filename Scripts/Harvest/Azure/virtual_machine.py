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


def _is_databricks_worker(resource: dict[str, Any]) -> bool:
    tags = resource.get("tags") or {}
    text = " ".join(
        [
            safe_str(resource.get("resourceGroup")),
            safe_str(resource.get("name")),
            json.dumps(tags),
        ]
    ).lower()
    return "databricks" in text or "dbr-" in text


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    rows = az(["vm", "list", "-d"], subscription_id)
    results: list[dict[str, Any]] = []

    for vm in rows:
        fqdn = _fqdn(vm)
        has_public_ip = _is_public(vm)
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
            # A public IP association is not proof of an inbound service.
            "is_public": 0,
            # A public IP association still requires NIC/subnet NSG review;
            # it is neither direct public ingress nor an IP-restricted service.
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps(["ssh", "rdp"]),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({
                **vm,
                "_extra": {
                    "public_ips": public_ips,
                    "exposure_class": (
                        "egress_only"
                        if _is_databricks_worker(vm) and has_public_ip
                        else "requires_nsg_review" if has_public_ip else "private"
                    ),
                    "inbound_exposure_requires_nsg_review": has_public_ip,
                    "public_ip_direction": "egress" if _is_databricks_worker(vm) and has_public_ip else "unknown",
                    "public_ip_purpose": (
                        "Databricks worker public egress/management address"
                        if _is_databricks_worker(vm) and has_public_ip
                        else None
                    ),
                    "private_ips": private_ips,
                    "os_type": os_type,
                    "power_state": power_state,
                },
            }),
        })

    return results
