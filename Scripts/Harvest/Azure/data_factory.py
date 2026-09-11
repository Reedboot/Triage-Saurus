"""Harvest Azure Data Factory instances."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, az_resource_show, classify_network_access

RESOURCE_TYPE = "Microsoft.DataFactory/factories"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    # az datafactory list requires the datafactory extension
    raw = az(["datafactory", "list"], subscription_id)
    if not raw:
        # Fallback: use generic resource list
        raw = az(
            ["resource", "list", "--resource-type", RESOURCE_TYPE],
            subscription_id,
        )
    results = []

    for factory in raw:
        list_props = factory.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props
            for key in ("publicNetworkAccess", "privateEndpointConnections", "networkAcls")
        )
        detailed = (
            az_resource_show(factory.get("id", ""), subscription_id, runner=az)
            if factory.get("id") and needs_detail
            else None
        )
        if detailed:
            factory = {**factory, **detailed}
        props = factory.get("properties") or factory
        is_public, is_restricted, ip_restrictions, exposure_class = _classify_exposure(props)
        managed_private_endpoints = _harvest_managed_private_endpoints(
            subscription_id,
            factory.get("resourceGroup"),
            factory.get("name"),
        )

        extra = {
            "provisioning_state": props.get("provisioningState"),
            "public_network_access": props.get("publicNetworkAccess"),
            "exposure_class": exposure_class,
            "global_parameters_count": len(props.get("globalParameters") or {}),
            "managed_virtual_network_enabled": bool(
                (props.get("managedVirtualNetwork") or {}).get("type")
            ),
            "git_config_type": (props.get("repoConfiguration") or {}).get("type"),
            "managed_private_endpoints": managed_private_endpoints,
        }

        results.append({
            "id": factory["id"],
            "subscription_id": subscription_id,
            "resource_group": factory.get("resourceGroup"),
            "name": factory.get("name"),
            "type": factory.get("type", RESOURCE_TYPE),
            "location": factory.get("location"),
            "sku": None,
            "tags": json.dumps(factory.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps(["azure_ad"]),
            "fqdn": None,
            "pipeline_tag": (factory.get("tags") or {}).get("pipeline") or (factory.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**factory, "_extra": extra}),
        })

    return results


def _harvest_managed_private_endpoints(
    subscription_id: str,
    resource_group: str | None,
    factory_name: str | None,
) -> list[dict[str, Any]]:
    """Harvest Data Factory managed-VNet private endpoints and their targets."""
    if not resource_group or not factory_name:
        return []

    managed_vnets = az(
        [
            "datafactory",
            "managed-virtual-network",
            "list",
            "--factory-name",
            factory_name,
            "--resource-group",
            resource_group,
        ],
        subscription_id,
    )
    endpoints: list[dict[str, Any]] = []
    for managed_vnet in managed_vnets:
        managed_vnet_name = managed_vnet.get("name")
        if not managed_vnet_name:
            continue
        managed_endpoints = az(
            [
                "datafactory",
                "managed-private-endpoint",
                "list",
                "--factory-name",
                factory_name,
                "--managed-virtual-network-name",
                managed_vnet_name,
                "--resource-group",
                resource_group,
            ],
            subscription_id,
        )
        for endpoint in managed_endpoints:
            endpoint_props = endpoint.get("properties") or {}
            state = endpoint_props.get("connectionState") or {}
            endpoints.append({
                "name": endpoint.get("name"),
                "managed_virtual_network": managed_vnet_name,
                "group_id": endpoint_props.get("groupId"),
                "target_resource_id": endpoint_props.get("privateLinkResourceId"),
                "endpoint_resource_id": endpoint_props.get("resourceId") or endpoint.get("id"),
                "connection_state": state.get("status"),
                "connection_actions_required": state.get("actionsRequired"),
                "provisioning_state": endpoint_props.get("provisioningState"),
                "fqdns": endpoint_props.get("fqdns") or [],
            })
    return endpoints


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str], str]:
    """Classify the management-plane endpoint, not managed-VNet egress."""
    return classify_network_access(props, endpoint_present=True)


def _is_public(props: dict[str, Any]) -> int:
    """Backward-compatible boolean helper used by older callers."""
    return _classify_exposure(props)[0]
