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
        # az CLI returns a flat structure — no nested "properties" wrapper
        public_fqdn = safe_str(cluster.get("fqdn"))
        private_fqdn = safe_str(cluster.get("privateFqdn"))
        # Prefer public FQDN for endpoint probing; fall back to private FQDN for display
        fqdn = public_fqdn or private_fqdn
        is_public, is_restricted, ip_restrictions = _classify_exposure(cluster)

        endpoints = build_endpoints([(public_fqdn, 443, "https")] if public_fqdn else [])
        auth_methods = json.dumps(_get_auth_methods(cluster))

        api_access = cluster.get("apiServerAccessProfile") or {}
        extra = {
            "kubernetes_version": cluster.get("kubernetesVersion"),
            "node_count": _total_node_count(cluster),
            "private_cluster": api_access.get("enablePrivateCluster", False),
            "public_fqdn": public_fqdn,
            "private_fqdn": private_fqdn,
            "public_network_access": cluster.get("publicNetworkAccess"),
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


def _classify_exposure(cluster: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_cidrs)."""
    api_access = cluster.get("apiServerAccessProfile") or {}

    if api_access.get("enablePrivateCluster"):
        return 0, 0, []

    authorized_ip_ranges = api_access.get("authorizedIpRanges") or []
    if authorized_ip_ranges:
        return 0, 1, list(authorized_ip_ranges)

    # A public FQDN with no IP restrictions = publicly accessible API server
    if cluster.get("fqdn"):
        return 1, 0, []

    return 0, 0, []


def _get_auth_methods(cluster: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    aad_profile = cluster.get("aadProfile") or {}
    if aad_profile:
        methods.append("azure_ad")

    oidc = cluster.get("oidcIssuerProfile") or {}
    if oidc.get("enabled"):
        methods.append("oidc_workload_identity")

    disable_local = cluster.get("disableLocalAccounts", False)
    if not disable_local:
        methods.append("local_kubeconfig")

    return methods or ["local_kubeconfig"]


def _total_node_count(cluster: dict[str, Any]) -> int:
    total = 0
    for pool in cluster.get("agentPoolProfiles") or []:
        total += pool.get("count") or 0
    return total
