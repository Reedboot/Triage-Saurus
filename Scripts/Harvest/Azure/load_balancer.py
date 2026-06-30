"""Harvest Azure load balancers."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_fqdn, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Network/loadBalancers"


def _extract_public_ip_ids(resource: dict[str, Any]) -> list[str]:
    props = resource.get("properties") or {}
    found: list[str] = []
    seen: set[str] = set()
    for config in props.get("frontendIPConfigurations") or []:
        cfg_props = config.get("properties") or config
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


def _extract_routing_targets(resource: dict[str, Any]) -> list[dict[str, str]]:
    props = resource.get("properties") or {}
    targets: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add_target(value: str, name: str = "", target_type: str = "") -> None:
        target = safe_str(value)
        if not target:
            return
        key = target.lower()
        if key in seen:
            return
        seen.add(key)
        entry = {"target": target}
        if name:
            entry["name"] = name
        if target_type:
            entry["type"] = target_type
        targets.append(entry)

    def _name_from_id(resource_id: str, segment: str) -> str:
        parts = [p for p in str(resource_id or "").split("/") if p]
        lowered = [p.lower() for p in parts]
        if segment not in lowered:
            return ""
        idx = lowered.index(segment)
        if idx + 1 >= len(parts):
            return ""
        return str(parts[idx + 1]).strip()

    for pool in props.get("backendAddressPools") or []:
        if not isinstance(pool, dict):
            continue
        pool_name = safe_str(pool.get("name")) or ""
        pool_props = pool.get("properties") if isinstance(pool.get("properties"), dict) else pool

        for cfg in pool_props.get("backendIPConfigurations") or []:
            cfg_id = safe_str((cfg or {}).get("id") if isinstance(cfg, dict) else cfg)
            if not cfg_id:
                continue
            cfg_l = cfg_id.lower()
            if "/virtualmachinescalesets/" in cfg_l:
                vmss_name = _name_from_id(cfg_id, "virtualmachinescalesets")
                _add_target(vmss_name or cfg_id, vmss_name or pool_name or "VM Scale Set", "Microsoft.Compute/virtualMachineScaleSets")
            elif "/virtualmachines/" in cfg_l:
                vm_name = _name_from_id(cfg_id, "virtualmachines")
                _add_target(vm_name or cfg_id, vm_name or pool_name or "Virtual Machine", "Microsoft.Compute/virtualMachines")
            elif "/networkinterfaces/" in cfg_l:
                nic_name = _name_from_id(cfg_id, "networkinterfaces")
                _add_target(nic_name or cfg_id, nic_name or pool_name or "Network Interface", "Microsoft.Network/networkInterfaces")
            else:
                _add_target(cfg_id, pool_name)

        for backend in pool_props.get("loadBalancerBackendAddresses") or []:
            if not isinstance(backend, dict):
                continue
            backend_props = backend.get("properties") if isinstance(backend.get("properties"), dict) else backend
            candidate = (
                safe_str(backend_props.get("ipAddress"))
                or safe_str(backend_props.get("fqdn"))
                or safe_str(backend.get("name"))
            )
            _add_target(candidate or "", safe_str(backend.get("name")) or pool_name)

    return targets


def _load_balancer_show(subscription_id: str, resource_group: str, name: str) -> dict[str, Any] | None:
    details = az(
        ["network", "lb", "show", "--resource-group", resource_group, "--name", name],
        subscription_id,
    )
    if isinstance(details, dict):
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
            fetched = _load_balancer_show(subscription_id, resource_group, name)
            if isinstance(fetched, dict):
                detailed = fetched

        fqdn = infer_fqdn(detailed)
        public_ip_ids = _extract_public_ip_ids(detailed)
        props = detailed.get("properties") or {}
        extra = {
            "frontend_ip_configuration_count": len(props.get("frontendIPConfigurations") or []),
            "backend_pool_count": len(props.get("backendAddressPools") or []),
            "probe_count": len(props.get("probes") or []),
            "sku_tier": safe_str((detailed.get("sku") or {}).get("tier")),
            "public_ip_resource_ids": public_ip_ids,
            "routing_targets": _extract_routing_targets(detailed),
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
                "is_public": 1 if public_ip_ids else 0,
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
