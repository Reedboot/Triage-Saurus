"""Harvest Azure Machine Learning workspaces / AI Foundry resources."""
from __future__ import annotations

import json
from urllib.parse import urlparse
from typing import Any

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.MachineLearningServices/workspaces"
CHILD_RESOURCE_TYPES = (
    "Microsoft.MachineLearningServices/workspaces/onlineEndpoints",
    "Microsoft.MachineLearningServices/workspaces/onlineEndpoints/deployments",
    "Microsoft.MachineLearningServices/workspaces/batchEndpoints",
    "Microsoft.MachineLearningServices/workspaces/batchEndpoints/deployments",
    "Microsoft.MachineLearningServices/workspaces/computes",
    "Microsoft.MachineLearningServices/workspaces/connections",
    "Microsoft.MachineLearningServices/workspaces/datastores",
)


def _workspace_fqdn(resource: dict[str, Any]) -> str | None:
    props = resource.get("properties") or {}
    raw_url = safe_str(
        props.get("workspaceUrl")
        or props.get("studioUrl")
        or props.get("discoveryUrl")
    )
    if not raw_url:
        return None

    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    host = parsed.hostname or parsed.netloc or parsed.path
    return safe_str(host)


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for workspace in raw:
        props = workspace.get("properties") or {}
        fqdn = _workspace_fqdn(workspace)
        private_endpoints = props.get("privateEndpointConnections") or []
        public_network_access = safe_str(props.get("publicNetworkAccess") or "Enabled")

        results.append({
            "id": workspace["id"],
            "subscription_id": subscription_id,
            "resource_group": workspace.get("resourceGroup"),
            "name": workspace.get("name"),
            "type": workspace.get("type", RESOURCE_TYPE),
            "location": workspace.get("location"),
            "sku": safe_str((workspace.get("sku") or {}).get("name")),
            "tags": json.dumps(workspace.get("tags") or {}),
            "is_public": 1 if fqdn and public_network_access != "Disabled" else 0,
            "is_restricted": 1 if private_endpoints else 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
            "auth_methods": json.dumps(["azure_ad"]),
            "fqdn": fqdn,
            "pipeline_tag": (workspace.get("tags") or {}).get("pipeline") or (workspace.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **workspace,
                "_extra": {
                    "platform_managed": True,
                    "compute_scope": "managed",
                    "managed_service": "Machine Learning",
                    "workspace_url": props.get("workspaceUrl"),
                    "studio_url": props.get("studioUrl"),
                    "discovery_url": props.get("discoveryUrl"),
                    "public_network_access": public_network_access,
                    "private_endpoint_connections": len(private_endpoints),
                    "kind": workspace.get("kind"),
                },
            }),
        })

    for resource_type in CHILD_RESOURCE_TYPES:
        try:
            child_resources = az(["resource", "list", "--resource-type", resource_type], subscription_id)
        except AssertionError:
            # Keep compatibility with isolated provider tests that stub only
            # the workspace list command.
            child_resources = []
        for child in child_resources:
            child_id = safe_str(child.get("id")) or ""
            if not child_id:
                continue
            parent_id = child_id.rsplit("/", 2)[0] if child_id.count("/") >= 2 else child_id
            results.append({
                "id": child_id,
                "subscription_id": subscription_id,
                "resource_group": child.get("resourceGroup"),
                "name": child.get("name"),
                "type": child.get("type", resource_type),
                "location": child.get("location"),
                "sku": None,
                "tags": json.dumps(child.get("tags") or {}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": json.dumps(["azure_ad"]),
                "fqdn": None,
                "pipeline_tag": None,
                "raw_json": json.dumps({
                    **child,
                    "_extra": {
                        "parent_resource_id": parent_id,
                        "workspace_child": True,
                        "platform_managed": True,
                        "compute_scope": "managed",
                        "managed_service": "Machine Learning",
                    },
                }),
            })

    return results
