"""Harvest Azure App Service Environments (ASE v2/v3).

All Web Apps and Function Apps running inside an ASE have hostnames like
  <app>.{ase-name}.appserviceenvironment.net
This provider captures the ASE itself so the web UI can show containment.
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Web/hostingEnvironments"


def _subnet_id_from_virtual_network(props: dict[str, Any]) -> str | None:
    virtual_network = props.get("virtualNetwork") or {}
    if not isinstance(virtual_network, dict):
        return None
    subnet = virtual_network.get("subnet") or {}
    if isinstance(subnet, dict):
        subnet_id = safe_str(subnet.get("id"))
        if subnet_id:
            return subnet_id
    return safe_str(virtual_network.get("subnetId") or virtual_network.get("subnet_id"))


def _vnet_name_from_subnet_id(subnet_id: str | None) -> str | None:
    if not subnet_id or "/virtualNetworks/" not in subnet_id:
        return None
    return subnet_id.split("/virtualNetworks/")[-1].split("/")[0] or None


def _resource_group_from_arm_id(resource_id: str | None) -> str | None:
    if not resource_id or "/resourceGroups/" not in resource_id:
        return None
    return resource_id.split("/resourceGroups/")[-1].split("/")[0] or None


def _worker_os_type(ase: dict[str, Any]) -> str | None:
    props = ase.get("properties") or ase
    os_types: list[str] = []
    seen: set[str] = set()
    for pool in props.get("workerPools") or []:
        if not isinstance(pool, dict):
            continue
        candidates = [
            pool.get("osType"),
            pool.get("os_type"),
        ]
        pool_props = pool.get("properties") if isinstance(pool.get("properties"), dict) else {}
        if isinstance(pool_props, dict):
            candidates.extend([pool_props.get("osType"), pool_props.get("os_type")])
        for candidate in candidates:
            os_type = safe_str(candidate)
            if os_type and os_type.lower() not in seen:
                seen.add(os_type.lower())
                os_types.append(os_type)
    if not os_types:
        return None
    return os_types[0] if len(os_types) == 1 else ", ".join(os_types)


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appservice", "ase", "list"], subscription_id)
    results = []

    for ase in raw:
        props = ase.get("properties") or ase

        # Internal ASE DNS suffix: <ase-name>.<region>.appserviceenvironment.net
        dns_suffix = safe_str(props.get("dnsSuffix"))
        subnet_id = _subnet_id_from_virtual_network(props)

        extra = {
            "kind": ase.get("kind"),          # ASEV2 or ASEV3
            "status": props.get("status"),
            "internal_load_balancing_mode": props.get("internalLoadBalancingMode", "None"),
            "is_ilb": props.get("internalLoadBalancingMode", "None") != "None",
            "dns_suffix": dns_suffix,
            "maximum_number_of_machines": props.get("maximumNumberOfMachines"),
            "front_end_scale_factor": props.get("frontEndScaleFactor"),
            "worker_pools": len(props.get("workerPools") or []),
            "virtual_network": (props.get("virtualNetwork") or {}).get("id"),
            "subnet": (props.get("virtualNetwork") or {}).get("subnet"),
            "vnet_name": _vnet_name_from_subnet_id(subnet_id),
            "vnet_resource_group": _resource_group_from_arm_id(subnet_id),
            "subnet_name": subnet_id.split("/subnets/")[-1] if subnet_id and "/subnets/" in subnet_id else None,
            "subnet_id": subnet_id,
            "upgrade_availability": props.get("upgradeAvailability"),
            "hosted_service_families": ["App Service", "Function App"],
            "hosted_resource_types": ["Microsoft.Web/sites"],
            "os_type": _worker_os_type(ase),
        }

        # ILB ASE = no public internet ingress
        is_public = 0 if extra["is_ilb"] else 1

        results.append({
            "id": ase["id"],
            "subscription_id": subscription_id,
            "resource_group": ase.get("resourceGroup"),
            "name": ase.get("name"),
            "type": ase.get("type", RESOURCE_TYPE),
            "location": ase.get("location"),
            "sku": ase.get("kind"),
            "tags": json.dumps(ase.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": dns_suffix,
            "pipeline_tag": None,
            "vnet_name": extra["vnet_name"],
            "vnet_resource_group": extra["vnet_resource_group"],
            "subnet_name": extra["subnet_name"],
            "subnet_id": extra["subnet_id"],
            "network": {
                "vnet": extra["vnet_name"],
                "subnet": extra["subnet_name"],
                "vnet_resource_group": extra["vnet_resource_group"],
                "subnet_id": extra["subnet_id"],
            },
            "raw_json": json.dumps({**ase, "_extra": extra}),
        })

    return results
