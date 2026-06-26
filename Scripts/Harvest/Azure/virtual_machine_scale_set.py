"""Harvest Azure virtual machine scale sets."""
from __future__ import annotations

from typing import Any

from ._resource_list import harvest_resource_list

RESOURCE_TYPE = "Microsoft.Compute/virtualMachineScaleSets"


def _has_public_ip_configuration(resource: dict[str, Any]) -> bool:
    props = resource.get("properties") or {}
    vm_profile = props.get("virtualMachineProfile") or {}
    net_profile = vm_profile.get("networkProfile") or {}
    nic_configs = net_profile.get("networkInterfaceConfigurations") or []
    for nic in nic_configs:
        nic_props = nic.get("properties") or {}
        ip_configs = nic_props.get("ipConfigurations") or []
        for ip_cfg in ip_configs:
            ip_props = ip_cfg.get("properties") or {}
            if ip_props.get("publicIPAddressConfiguration"):
                return True
    return False


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    return harvest_resource_list(
        subscription_id,
        RESOURCE_TYPE,
        is_public_fn=_has_public_ip_configuration,
        extra_fn=lambda resource: {
            "instance_count": (((resource.get("sku") or {}).get("capacity"))),
            "orchestration_mode": ((resource.get("properties") or {}).get("orchestrationMode")),
            "upgrade_policy_mode": (((resource.get("properties") or {}).get("upgradePolicy") or {}).get("mode")),
            "has_public_ip_configuration": _has_public_ip_configuration(resource),
        },
    )
