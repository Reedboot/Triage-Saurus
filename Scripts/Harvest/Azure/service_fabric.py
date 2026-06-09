"""Harvest Azure Service Fabric clusters."""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from ._helpers import (
    az,
    build_endpoints,
    safe_str,
    _is_msal_lock_error,
    _AZ_RETRY_MAX,
    _AZ_RETRY_BACKOFF,
)

RESOURCE_TYPE = "Microsoft.ServiceFabric/clusters"
APPLICATION_RESOURCE_TYPE = "Microsoft.ServiceFabric/clusters/applications"
SERVICE_RESOURCE_TYPE = "Microsoft.ServiceFabric/clusters/services"


def _az_sf_json(args: list[str], subscription_id: str) -> list[dict[str, Any]]:
    """Run az sf ... and return parsed JSON list, tolerating noisy output."""
    cmd = ["az", "sf"] + args + ["--subscription", subscription_id, "--output", "json"]
    last_stderr = ""
    for attempt in range(_AZ_RETRY_MAX):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except Exception:
            return []

        stdout = (result.stdout or "").strip()
        if result.returncode == 0:
            if not stdout:
                return []
            try:
                parsed = json.loads(stdout)
            except Exception:
                return []
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                value = parsed.get("value")
                if isinstance(value, list):
                    return value
            return []

        last_stderr = (result.stderr or "").strip()
        if attempt < _AZ_RETRY_MAX - 1 and _is_msal_lock_error(last_stderr):
            time.sleep(_AZ_RETRY_BACKOFF * (attempt + 1))
            continue
        return []
    return []


def _sanitize_sf_name(value: str | None) -> str:
    raw = safe_str(value) or ""
    return raw.replace("fabric:/", "").replace("fabric:", "").replace("/", "_")


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["sf", "cluster", "list"], subscription_id)
    results = []

    for cluster in raw:
        props = cluster.get("properties") or cluster
        # managementEndpoint looks like https://hostname:19080
        mgmt = props.get("managementEndpoint", "")
        fqdn = safe_str(
            mgmt.replace("https://", "").replace("http://", "").split(":")[0]
        ) or None

        # Service Fabric management port 19080 (HTTP/REST) and 19000 (TCP client)
        endpoints = build_endpoints([
            (fqdn, 19080, "https"),
            (fqdn, 19000, "tcp"),
        ] if fqdn else [])

        node_types = props.get("nodeTypes") or []
        extra = {
            "cluster_state": props.get("clusterState"),
            "cluster_code_version": props.get("clusterCodeVersion"),
            "management_endpoint": mgmt,
            "node_type_count": len(node_types),
            "node_types": [
                {
                    "name": nt.get("name"),
                    "vm_instance_count": nt.get("vmInstanceCount"),
                    "is_primary": nt.get("isPrimary"),
                    "durability_level": nt.get("durabilityLevel"),
                }
                for nt in node_types
            ],
            "reliability_level": props.get("reliabilityLevel"),
            "upgrade_mode": props.get("upgradeMode"),
            "add_on_features": props.get("addOnFeatures") or [],
        }

        cluster_id = cluster.get("id")
        cluster_rg = cluster.get("resourceGroup")
        cluster_name = cluster.get("name")

        applications = _az_sf_json(
            ["application", "list", "--resource-group", cluster_rg, "--cluster-name", cluster_name],
            subscription_id,
        ) if cluster_rg and cluster_name else []
        extra["application_count"] = len(applications)
        extra["service_count"] = 0

        for app in applications:
            app_name = safe_str(app.get("name"))
            if not app_name:
                continue
            app_id = safe_str(app.get("id")) or f"{cluster_id}/applications/{_sanitize_sf_name(app_name)}"
            app_props = app.get("properties") or {}

            results.append({
                "id": app_id,
                "subscription_id": subscription_id,
                "resource_group": cluster_rg,
                "name": app_name,
                "type": APPLICATION_RESOURCE_TYPE,
                "location": cluster.get("location"),
                "sku": safe_str(app_props.get("typeName")) or None,
                "tags": json.dumps(cluster.get("tags") or {}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": json.dumps(["azure_ad", "client_certificate"]),
                "fqdn": fqdn,
                "pipeline_tag": (cluster.get("tags") or {}).get("pipeline") or (cluster.get("tags") or {}).get("ado-pipeline"),
                "raw_json": json.dumps({
                    **app,
                    "_extra": {
                        "cluster_id": cluster_id,
                        "cluster_name": cluster_name,
                        "application_name": app_name,
                    },
                }),
            })

            services = _az_sf_json(
                [
                    "service",
                    "list",
                    "--resource-group",
                    cluster_rg,
                    "--cluster-name",
                    cluster_name,
                    "--application-name",
                    app_name,
                ],
                subscription_id,
            ) if cluster_rg and cluster_name else []
            extra["service_count"] += len(services)

            for svc in services:
                svc_name = safe_str(svc.get("name"))
                if not svc_name:
                    continue
                svc_id = safe_str(svc.get("id")) or f"{cluster_id}/services/{_sanitize_sf_name(app_name)}::{_sanitize_sf_name(svc_name)}"
                svc_props = svc.get("properties") or {}
                results.append({
                    "id": svc_id,
                    "subscription_id": subscription_id,
                    "resource_group": cluster_rg,
                    "name": svc_name,
                    "type": SERVICE_RESOURCE_TYPE,
                    "location": cluster.get("location"),
                    "sku": safe_str(svc_props.get("serviceTypeName")) or safe_str(svc_props.get("serviceKind")) or None,
                    "tags": json.dumps(cluster.get("tags") or {}),
                    "is_public": 0,
                    "is_restricted": 0,
                    "ip_restrictions": json.dumps([]),
                    "endpoints": json.dumps([]),
                    "auth_methods": json.dumps(["azure_ad", "client_certificate"]),
                    "fqdn": fqdn,
                    "pipeline_tag": (cluster.get("tags") or {}).get("pipeline") or (cluster.get("tags") or {}).get("ado-pipeline"),
                    "raw_json": json.dumps({
                        **svc,
                        "_extra": {
                            "cluster_id": cluster_id,
                            "cluster_name": cluster_name,
                            "application_name": app_name,
                            "application_id": app_id,
                            "service_status": safe_str(svc_props.get("serviceStatus")) or safe_str(svc.get("serviceStatus")),
                            "health_state": safe_str(svc_props.get("healthState")) or safe_str(svc.get("healthState")),
                        },
                    }),
                })

        results.append({
            "id": cluster_id,
            "subscription_id": subscription_id,
            "resource_group": cluster_rg,
            "name": cluster_name,
            "type": cluster.get("type", RESOURCE_TYPE),
            "location": cluster.get("location"),
            "sku": None,
            "tags": json.dumps(cluster.get("tags") or {}),
            "is_public": 0,  # SF management endpoints should be locked down
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": endpoints,
            "auth_methods": json.dumps(["azure_ad", "client_certificate"]),
            "fqdn": fqdn,
            "pipeline_tag": (cluster.get("tags") or {}).get("pipeline") or (cluster.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**cluster, "_extra": extra}),
        })

    return results
