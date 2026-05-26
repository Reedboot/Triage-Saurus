"""Harvest Azure Kubernetes Service clusters."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.ContainerService/managedClusters"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["aks", "list"], subscription_id)
    results = []

    for cluster in raw:
        props = cluster.get("properties") or {}
        fqdn = safe_str(props.get("fqdn")) or safe_str(props.get("privateFqdn"))
        is_public = _is_public(props)

        extra = {
            "kubernetes_version": props.get("kubernetesVersion"),
            "node_count": _total_node_count(props),
            "private_cluster": props.get("apiServerAccessProfile", {}).get("enablePrivateCluster", False),
        }

        results.append({
            "id": cluster["id"],
            "subscription_id": subscription_id,
            "resource_group": cluster.get("resourceGroup"),
            "name": cluster.get("name"),
            "type": cluster.get("type", RESOURCE_TYPE),
            "location": cluster.get("location"),
            "sku": infer_sku(cluster),
            "tags": json.dumps(cluster.get("tags") or {}),
            "is_public": is_public,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**cluster, "_extra": extra}),
        })

    return results


def _is_public(props: dict[str, Any]) -> int:
    api_access = props.get("apiServerAccessProfile") or {}
    if api_access.get("enablePrivateCluster"):
        return 0
    return 1


def _total_node_count(props: dict[str, Any]) -> int:
    total = 0
    for pool in props.get("agentPoolProfiles") or []:
        total += pool.get("count") or 0
    return total
