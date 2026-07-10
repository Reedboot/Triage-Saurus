"""Harvest Azure virtual machine scale sets."""
from __future__ import annotations

import json
from typing import Any

from ._resource_list import harvest_resource_list
from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Compute/virtualMachineScaleSets"


def _subnet_refs(resource: dict[str, Any]) -> list[str]:
    props = resource.get("properties") or {}
    vm_profile = props.get("virtualMachineProfile") or {}
    net_profile = vm_profile.get("networkProfile") or {}
    subnet_ids: list[str] = []

    for nic_cfg in net_profile.get("networkInterfaceConfigurations") or []:
        nic_props = nic_cfg.get("properties") or {}
        for ip_cfg in nic_props.get("ipConfigurations") or nic_cfg.get("ipConfigurations") or []:
            ip_props = ip_cfg.get("properties") or {}
            for candidate in (
                (ip_props.get("subnet") or {}).get("id") if isinstance(ip_props.get("subnet"), dict) else None,
                (ip_cfg.get("subnet") or {}).get("id") if isinstance(ip_cfg.get("subnet"), dict) else None,
            ):
                subnet_id = safe_str(candidate)
                if subnet_id and subnet_id not in subnet_ids:
                    subnet_ids.append(subnet_id)
    return subnet_ids


def _os_type(resource: dict[str, Any]) -> str | None:
    props = resource.get("properties") or {}
    vm_profile = props.get("virtualMachineProfile") or {}
    storage_profile = vm_profile.get("storageProfile") or props.get("storageProfile") or {}
    os_disk = storage_profile.get("osDisk") or {}
    os_type = os_disk.get("osType")
    return safe_str(os_type) or None


def _vnet_name_from_subnet_id(subnet_id: str) -> str | None:
    if not subnet_id or "/virtualNetworks/" not in subnet_id:
        return None
    return subnet_id.split("/virtualNetworks/")[-1].split("/")[0] or None


def _resource_group_from_arm_id(resource_id: str) -> str | None:
    if not resource_id or "/resourceGroups/" not in resource_id:
        return None
    return resource_id.split("/resourceGroups/")[-1].split("/")[0] or None


def _show_vmss(subscription_id: str, resource_id: str) -> dict[str, Any]:
    shown = az(["resource", "show", "--ids", resource_id], subscription_id)
    return shown if isinstance(shown, dict) else {}


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    rows = harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        extra_fn=lambda resource: {
            "instance_count": (((resource.get("sku") or {}).get("capacity"))),
            "orchestration_mode": ((resource.get("properties") or {}).get("orchestrationMode")),
            "upgrade_policy_mode": (((resource.get("properties") or {}).get("upgradePolicy") or {}).get("mode")),
        },
    )

    results: list[dict[str, Any]] = []
    for row in rows:
        details = _show_vmss(subscription_id, row["id"])
        if details:
            subnet_ids = _subnet_refs(details)
            subnet_id = subnet_ids[0] if subnet_ids else None
            os_type = _os_type(details)
            extra = dict(details.get("_extra") or {})
            if subnet_id:
                extra.update(
                    {
                        "subnet_id": subnet_id,
                        "subnet_name": subnet_id.split("/subnets/")[-1] if "/subnets/" in subnet_id else None,
                        "vnet_name": _vnet_name_from_subnet_id(subnet_id),
                        "vnet_resource_group": _resource_group_from_arm_id(subnet_id),
                        "subnet_ids": subnet_ids,
                    }
                )
            if os_type:
                extra["os_type"] = os_type
            row["raw_json"] = json.dumps({**details, "_extra": extra})
            # Keep the original row shape but store the richer VMSS payload so the
            # architecture view can infer the VNet boundary from nested NIC config.
        results.append(row)

    return results
