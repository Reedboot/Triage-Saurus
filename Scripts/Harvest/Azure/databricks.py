"""Harvest Azure Databricks workspaces."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Databricks/workspaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for workspace in raw:
        props = workspace.get("properties") or {}
        fqdn = safe_str(props.get("workspaceUrl"))
        public_network_access = safe_str(props.get("publicNetworkAccess") or "Enabled")
        private_endpoints = props.get("privateEndpointConnections") or []

        results.append({
            "id": workspace["id"],
            "subscription_id": subscription_id,
            "resource_group": workspace.get("resourceGroup"),
            "name": workspace.get("name"),
            "type": workspace.get("type", RESOURCE_TYPE),
            "location": workspace.get("location"),
            "sku": infer_sku(workspace),
            "tags": json.dumps(workspace.get("tags") or {}),
            "is_public": 1 if fqdn and public_network_access != "Disabled" else 0,
            "is_restricted": 1 if private_endpoints else 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
            "auth_methods": json.dumps(["aad"]),
            "fqdn": fqdn,
            "pipeline_tag": (workspace.get("tags") or {}).get("pipeline") or (workspace.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **workspace,
                "_extra": {
                    "workspace_id": props.get("workspaceId"),
                    "public_network_access": public_network_access,
                    "private_endpoint_connections": len(private_endpoints),
                    "compute_mode": props.get("computeMode"),
                },
            }),
        })

    return results
