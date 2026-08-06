"""Harvest Azure services whose workload compute is platform-managed or ephemeral."""
from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import urlparse

from ._helpers import az, build_endpoints, infer_sku, safe_str


SERVICE_TYPES = {
    "Container Apps": (
        "Microsoft.App/managedEnvironments",
        "Microsoft.App/containerApps",
    ),
    "Container Instances": ("Microsoft.ContainerInstance/containerGroups",),
    "Synapse": (
        "Microsoft.Synapse/workspaces",
        "Microsoft.Synapse/workspaces/bigDataPools",
        "Microsoft.Synapse/workspaces/sqlPools",
        "Microsoft.Synapse/workspaces/sqlPools/metadata",
    ),
    "Azure Batch": (
        "Microsoft.Batch/batchAccounts",
        "Microsoft.Batch/batchAccounts/pools",
    ),
    "HDInsight": ("Microsoft.HDInsight/clusters",),
    "Azure Spring Apps": (
        "Microsoft.AppPlatform/Spring",
        "Microsoft.AppPlatform/Spring/apps",
    ),
    "Azure VMware Solution": (
        "Microsoft.AVS/privateClouds",
        "Microsoft.AVS/privateClouds/clusters",
    ),
    "Service Fabric Managed Clusters": (
        "Microsoft.ServiceFabric/managedClusters",
        "Microsoft.ServiceFabric/managedClusters/nodeTypes",
    ),
}


def _endpoint_host(value: object) -> str | None:
    text = safe_str(value)
    if not text:
        return None
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = parsed.hostname or parsed.netloc
    if not host or "/" in host or host.lower().endswith(".blob.core.windows.net"):
        return None
    return host


def _find_endpoints(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ("url", "fqdn", "endpoint", "connectivity")):
                if isinstance(child, str):
                    host = _endpoint_host(child)
                    if host and host not in found:
                        found.append(host)
                elif isinstance(child, dict):
                    _find_endpoints(child, found)
            elif isinstance(child, (dict, list)):
                _find_endpoints(child, found)
    elif isinstance(value, list):
        for child in value:
            _find_endpoints(child, found)


def _parent_id(resource_id: str, resource_type: str) -> str | None:
    if "/" not in resource_type:
        return None
    parts = [part for part in resource_id.split("/") if part]
    type_parts = [part for part in resource_type.split("/", 1)[1].split("/") if part]
    if len(type_parts) <= 1:
        return None
    # Resource IDs alternate type/name. Walk back one type/name pair per child level.
    provider_index = next((i for i, part in enumerate(parts) if part.lower() == "providers"), None)
    if provider_index is None:
        return None
    tail = parts[provider_index + 2 :]
    if len(tail) < 2:
        return None
    return "/" + "/".join(parts[: provider_index + 2 + len(tail) - 2])


def _harvest_types(subscription_id: str, resource_types: Iterable[str], service_name: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for resource_type in resource_types:
        for resource in az(["resource", "list", "--resource-type", resource_type], subscription_id):
            props = resource.get("properties") or {}
            endpoints: list[str] = []
            _find_endpoints(props, endpoints)
            public_access = safe_str(
                props.get("publicNetworkAccess")
                or props.get("publicNetworkAccessForIngestion")
                or props.get("publicNetworkAccessForQuery")
                or "Unknown"
            )
            private_endpoints = props.get("privateEndpointConnections") or []
            parent_id = _parent_id(str(resource.get("id") or ""), resource_type)
            raw = {
                **resource,
                "_extra": {
                    "platform_managed": True,
                    "compute_scope": "managed",
                    "managed_service": service_name,
                    "parent_resource_id": parent_id,
                    "public_network_access": public_access,
                    "private_endpoint_connections": len(private_endpoints),
                    "endpoints": endpoints,
                },
            }
            results.append({
                "id": resource["id"],
                "subscription_id": subscription_id,
                "resource_group": resource.get("resourceGroup"),
                "name": resource.get("name"),
                "type": resource.get("type", resource_type),
                "location": resource.get("location"),
                "sku": infer_sku(resource),
                "tags": json.dumps(resource.get("tags") or {}),
                "is_public": 1 if endpoints and public_access.lower() not in {"disabled", "false"} else 0,
                "is_restricted": 1 if private_endpoints else 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": build_endpoints([(host, 443, "https") for host in endpoints]),
                "auth_methods": json.dumps(["azure_ad"]),
                "fqdn": endpoints[0] if endpoints else None,
                "pipeline_tag": (resource.get("tags") or {}).get("pipeline"),
                "raw_json": json.dumps(raw),
            })
    return results


def _provider(name: str):
    def harvest(subscription_id: str) -> list[dict[str, Any]]:
        return _harvest_types(subscription_id, SERVICE_TYPES[name], name)
    return harvest


harvest_container_apps = _provider("Container Apps")
harvest_container_instances = _provider("Container Instances")
harvest_synapse = _provider("Synapse")
harvest_batch = _provider("Azure Batch")
harvest_hdinsight = _provider("HDInsight")
harvest_spring_apps = _provider("Azure Spring Apps")
harvest_avs = _provider("Azure VMware Solution")
harvest_service_fabric_managed = _provider("Service Fabric Managed Clusters")
