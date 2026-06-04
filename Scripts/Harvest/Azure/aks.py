"""Harvest Azure Kubernetes Service clusters."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ._helpers import (
    _az_rest,
    az,
    build_endpoints,
    classify_host_alias_exposure,
    infer_sku,
    normalize_route_path,
    safe_str,
)

RESOURCE_TYPE = "Microsoft.ContainerService/managedClusters"

# AKS AAD server app ID — used as the --resource scope when calling the K8s API via az rest
_AKS_SCOPE = "6dae42f8-4368-4678-94ff-3960e28e3630"

# Kubernetes API resource paths
_K8S_RESOURCE_PATHS: dict[str, str] = {
    "ingresses": "/apis/networking.k8s.io/v1/ingresses",
    "services": "/api/v1/services",
    "deployments": "/apis/apps/v1/deployments",
}
_AKS_CLUSTER_WORKERS = 4


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


def _get_ingress_route_exposure_level(host_aliases: list[str] | None) -> str:
    return classify_host_alias_exposure(host_aliases)


# ---------------------------------------------------------------------------
# AKS route model — Kubernetes ingress → service → deployment mapping
# ---------------------------------------------------------------------------

def _get_cluster_portal_fqdn(cluster_id: str) -> str:
    """Fetch azurePortalFQDN for a cluster via the ARM management API."""
    body = _az_rest(f"https://management.azure.com{cluster_id}?api-version=2024-09-01")
    fqdn = (body.get("properties") or {}).get("azurePortalFQDN")
    if not fqdn:
        raise RuntimeError(f"azurePortalFQDN missing for cluster {cluster_id}")
    return fqdn


def _get_kubernetes_resources(portal_fqdn: str, resource_type: str) -> list[dict]:
    """Fetch all K8s resources of the given type, handling pagination."""
    path = _K8S_RESOURCE_PATHS[resource_type]
    items: list[dict] = []
    url: str | None = f"https://{portal_fqdn}{path}"

    while url:
        body = _az_rest(url, resource=_AKS_SCOPE)
        items.extend(body.get("items") or [])
        next_token = (body.get("metadata") or {}).get("continue")
        url = f"https://{portal_fqdn}{path}?continue={next_token}" if next_token else None

    return items


def _get_service_port_value(port: dict | None) -> str | int | None:
    """Return the port number or name from an ingress backend servicePort spec."""
    if port is None:
        return None
    if "number" in port:
        return port["number"]
    if "name" in port:
        return port["name"]
    return None


def _get_ingress_status_addresses(ingress: dict) -> list[str]:
    """Extract load-balancer IP addresses and hostnames from ingress status."""
    addresses: list[str] = []
    lb_ingresses = (
        ((ingress.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []
    )
    for entry in lb_ingresses:
        for key in ("ip", "hostname"):
            val = entry.get(key)
            if val and val.strip():
                addresses.append(val.strip())
    # De-duplicate while preserving order
    seen: set[str] = set()
    return [a for a in addresses if not (a in seen or seen.add(a))]  # type: ignore[func-returns-value]


def _get_ingress_backend_references(ingresses: list[dict]) -> list[dict]:
    """Flatten ingress rules into backend service references."""
    refs: list[dict] = []
    for ingress in ingresses:
        meta = ingress.get("metadata") or {}
        namespace = meta.get("namespace")
        ingress_name = meta.get("name")
        host_aliases = _get_ingress_status_addresses(ingress)
        spec = ingress.get("spec") or {}

        # Default backend
        default_backend = spec.get("defaultBackend")
        if default_backend and default_backend.get("service"):
            svc = default_backend["service"]
            refs.append({
                "namespace": namespace,
                "ingress_name": ingress_name,
                "host": None,
                "path": None,
                "service_name": svc.get("name"),
                "service_port": _get_service_port_value(svc.get("port")),
                "is_default_backend": True,
                "host_aliases": host_aliases,
            })

        # Routing rules
        for rule in spec.get("rules") or []:
            http = rule.get("http") or {}
            for path_entry in http.get("paths") or []:
                backend = path_entry.get("backend")
                if not backend or not backend.get("service"):
                    continue
                svc = backend["service"]
                refs.append({
                    "namespace": namespace,
                    "ingress_name": ingress_name,
                    "host": rule.get("host"),
                    "path": path_entry.get("path"),
                    "service_name": svc.get("name"),
                    "service_port": _get_service_port_value(svc.get("port")),
                    "is_default_backend": False,
                    "host_aliases": host_aliases,
                })

    return refs


def _test_selector_matches_labels(selector: dict | None, labels: dict | None) -> bool:
    """Return True only when selector is non-empty and every key=value pair exists in labels."""
    if not selector:
        return False
    if not labels:
        return False
    for key, value in selector.items():
        if labels.get(key) != value:
            return False
    return True


def _get_matching_deployments_for_service(service: dict, deployments: list[dict]) -> list[dict]:
    """Find deployments in the same namespace whose pod template labels satisfy the service selector."""
    service_ns = (service.get("metadata") or {}).get("namespace")
    selector = (service.get("spec") or {}).get("selector")
    matches = []
    for deploy in deployments:
        if (deploy.get("metadata") or {}).get("namespace") != service_ns:
            continue
        pod_labels = ((deploy.get("spec") or {}).get("template") or {}).get("metadata", {}).get("labels")
        if _test_selector_matches_labels(selector, pod_labels):
            matches.append(deploy)
    return matches


def _get_deployment_label(deployment: dict, label_name: str) -> str | None:
    """Get a label value, checking pod template labels first then deployment metadata labels."""
    pod_labels = (
        ((deployment.get("spec") or {}).get("template") or {})
        .get("metadata", {})
        .get("labels") or {}
    )
    value = pod_labels.get(label_name)
    if value and str(value).strip():
        return str(value).strip()
    meta_labels = (deployment.get("metadata") or {}).get("labels") or {}
    value = meta_labels.get(label_name)
    return str(value).strip() if value and str(value).strip() else None


def _build_route_model(
    cluster_meta: dict[str, Any],
    ingresses: list[dict],
    services: list[dict],
    deployments: list[dict],
) -> list[dict[str, Any]]:
    """Join ingress backend refs → services → deployments into a flat route model.

    Only routes where both ``git_repository`` and ``team`` deployment labels
    are present are included — matching the AksExposure.ps1 BuildAksRouteModel logic.
    """
    routes: list[dict[str, Any]] = []
    backend_refs = _get_ingress_backend_references(ingresses)

    # Build a fast lookup: (namespace, service_name) → service
    service_lookup: dict[tuple[str, str], dict] = {}
    for svc in services:
        ns = (svc.get("metadata") or {}).get("namespace", "")
        name = (svc.get("metadata") or {}).get("name", "")
        service_lookup[(ns, name)] = svc

    for ref in backend_refs:
        ns = ref["namespace"] or ""
        svc = service_lookup.get((ns, ref["service_name"] or ""))
        if svc is None:
            continue

        matching_deployments = _get_matching_deployments_for_service(svc, deployments)
        for deploy in matching_deployments:
            git_repo = _get_deployment_label(deploy, "git_repository")
            team = _get_deployment_label(deploy, "team")

            if not git_repo or not team:
                continue

            routes.append({
                "cluster_name": cluster_meta.get("name"),
                "cluster_resource_id": cluster_meta.get("id"),
                "resource_group": cluster_meta.get("resourceGroup"),
                "namespace": ns,
                "ingress_name": ref["ingress_name"],
                "host": ref["host"],
                "host_aliases": ref["host_aliases"],
                "path": ref["path"],
                "is_default_backend": 1 if ref["is_default_backend"] else 0,
                "service_name": (svc.get("metadata") or {}).get("name"),
                "service_port": ref["service_port"],
                "service_ports": list(svc.get("spec", {}).get("ports") or []),
                "deployment_name": (deploy.get("metadata") or {}).get("name"),
                "deployment_namespace": (deploy.get("metadata") or {}).get("namespace"),
                "pod_template_labels": (
                    ((deploy.get("spec") or {}).get("template") or {})
                    .get("metadata", {})
                    .get("labels") or {}
                ),
                "git_repository": git_repo,
                "team": team,
                "exposure_level": _get_ingress_route_exposure_level(ref["host_aliases"]),
            })

    return routes


def _harvest_cluster_route_bundle(cluster: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    cluster_id = cluster.get("id", "")
    cluster_name = cluster.get("name", "")
    if not cluster_id or not cluster_name:
        return None, []

    print(f"    [aks-routes] {cluster_name}...", end=" ", flush=True)
    portal_fqdn = _get_cluster_portal_fqdn(cluster_id)
    ingresses = _get_kubernetes_resources(portal_fqdn, "ingresses")
    services = _get_kubernetes_resources(portal_fqdn, "services")
    deployments = _get_kubernetes_resources(portal_fqdn, "deployments")
    routes = _build_route_model(cluster, ingresses, services, deployments)
    print(f"{len(routes)} routes")
    return cluster_name, routes


def _make_route_id(
    cluster_name: str,
    namespace: str,
    ingress_name: str,
    host: str | None,
    path: str | None,
    service_name: str | None,
    service_port: Any,
    deployment_name: str | None,
    is_default_backend: int,
) -> str:
    """Build a deterministic unique ID for an AKS route row."""
    h = host or "*"
    p = normalize_route_path(path) or "/"
    svc = service_name or ""
    port = str(service_port) if service_port is not None else ""
    deploy = deployment_name or ""
    default = "default" if is_default_backend else "rule"
    return f"{cluster_name}::{namespace}::{ingress_name}::{h}::{p}::{svc}::{port}::{deploy}::{default}"


def harvest_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Harvest K8s ingress→service→deployment route model for every AKS cluster.

    Mirrors the BuildAksRouteModel logic from AksExposure.ps1 in Phoenix.
    Tokens are acquired once per subscription; clusters are processed individually
    so a single unreachable cluster does not abort the rest.

    Returns the total number of routes harvested across all clusters.
    """
    clusters = az(["aks", "list"], subscription_id)
    if not clusters:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    total = 0
    max_workers = min(_AKS_CLUSTER_WORKERS, len(clusters))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_harvest_cluster_route_bundle, cluster): cluster.get("name") or cluster.get("id") or "<unknown>"
            for cluster in clusters
        }
        for future in as_completed(futures):
            cluster_name = futures[future]
            try:
                returned_cluster_name, routes = future.result()
            except Exception as exc:
                print(f"    [aks-routes] {cluster_name} SKIPPED ({exc})")
                continue

            if not returned_cluster_name:
                continue

            if not dry_run and routes:
                # Replace all existing routes for this cluster before inserting
                conn.execute(
                    "DELETE FROM aks_routes WHERE subscription_id = ? AND cluster_name = ?",
                    (subscription_id, returned_cluster_name),
                )
                for r in routes:
                    route_id = _make_route_id(
                        returned_cluster_name,
                        r["namespace"] or "",
                        r["ingress_name"] or "",
                        r["host"],
                        r["path"],
                        r["service_name"],
                        r["service_port"],
                        r["deployment_name"],
                        r["is_default_backend"],
                    )
                    conn.execute(
                        """
                        INSERT INTO aks_routes (
                            id, subscription_id, cluster_name, cluster_resource_id,
                            resource_group, namespace, ingress_name, host, host_aliases,
                            path, is_default_backend, service_name, service_port,
                            service_ports, deployment_name, deployment_namespace,
                            pod_template_labels, git_repository, team, exposure_level, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id      = excluded.subscription_id,
                            cluster_resource_id  = excluded.cluster_resource_id,
                            resource_group       = excluded.resource_group,
                            host_aliases         = excluded.host_aliases,
                            service_port         = excluded.service_port,
                            service_ports        = excluded.service_ports,
                            deployment_namespace = excluded.deployment_namespace,
                            pod_template_labels  = excluded.pod_template_labels,
                            git_repository       = excluded.git_repository,
                            team                 = excluded.team,
                            exposure_level       = excluded.exposure_level,
                            last_synced          = excluded.last_synced
                        """,
                        (
                            route_id,
                            subscription_id,
                            returned_cluster_name,
                            r["cluster_resource_id"],
                            r["resource_group"],
                            r["namespace"],
                            r["ingress_name"],
                            r["host"],
                            json.dumps(r["host_aliases"]),
                            r["path"],
                            r["is_default_backend"],
                            r["service_name"],
                            str(r["service_port"]) if r["service_port"] is not None else None,
                            json.dumps(r["service_ports"]),
                            r["deployment_name"],
                            r["deployment_namespace"],
                            json.dumps(r["pod_template_labels"]),
                            r["git_repository"],
                            r["team"],
                            r["exposure_level"],
                            now,
                        ),
                    )
                conn.commit()

            total += len(routes)

    return total
