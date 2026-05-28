"""Harvest Azure Kubernetes Service clusters."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.ContainerService/managedClusters"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["aks", "list"], subscription_id)
    results = []

    for cluster in raw:
        props = cluster.get("properties") or {}
        fqdn = safe_str(props.get("fqdn")) or safe_str(props.get("privateFqdn"))
        is_public, is_restricted, ip_restrictions = _classify_exposure(props)

        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(_get_auth_methods(props))

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
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**cluster, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_cidrs)."""
    api_access = props.get("apiServerAccessProfile") or {}

    if api_access.get("enablePrivateCluster"):
        return 0, 0, []

    authorized_ip_ranges = api_access.get("authorizedIPRanges") or []
    if authorized_ip_ranges:
        return 0, 1, list(authorized_ip_ranges)

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    aad_profile = props.get("aadProfile") or {}
    if aad_profile:
        methods.append("azure_ad")

    oidc = props.get("oidcIssuerProfile") or {}
    if oidc.get("enabled"):
        methods.append("oidc_workload_identity")

    # Local accounts (basic kubeconfig)
    disable_local = props.get("disableLocalAccounts", False)
    if not disable_local:
        methods.append("local_kubeconfig")

    return methods or ["local_kubeconfig"]


def _total_node_count(props: dict[str, Any]) -> int:
    total = 0
    for pool in props.get("agentPoolProfiles") or []:
        total += pool.get("count") or 0
    return total
