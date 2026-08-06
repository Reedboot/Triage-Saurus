"""Harvest Azure Databricks workspaces."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, az_resource_show, build_endpoints, classify_network_access, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Databricks/workspaces"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for workspace in raw:
        list_props = workspace.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props for key in ("publicNetworkAccess", "privateEndpointConnections", "workspaceUrl")
        )
        detailed = (
            az_resource_show(workspace.get("id", ""), subscription_id, runner=az)
            if workspace.get("id") and needs_detail
            else None
        )
        if detailed:
            workspace = {**workspace, **detailed}
        props = workspace.get("properties") or {}
        fqdn = safe_str(props.get("workspaceUrl"))
        public_network_access = safe_str(props.get("publicNetworkAccess"))
        private_endpoints = props.get("privateEndpointConnections") or []
        is_public, is_restricted, ip_restrictions, exposure_class = classify_network_access(
            props, endpoint_present=bool(fqdn), private_endpoint_connections=private_endpoints
        )

        results.append({
            "id": workspace["id"],
            "subscription_id": subscription_id,
            "resource_group": workspace.get("resourceGroup"),
            "name": workspace.get("name"),
            "type": workspace.get("type", RESOURCE_TYPE),
            "location": workspace.get("location"),
            "sku": infer_sku(workspace),
            "tags": json.dumps(workspace.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
            "auth_methods": json.dumps(["aad"]),
            "fqdn": fqdn,
            "pipeline_tag": (workspace.get("tags") or {}).get("pipeline") or (workspace.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **workspace,
                "_extra": {
                    "platform_managed": True,
                    "compute_scope": "managed",
                    "managed_service": "Databricks",
                    "workspace_id": props.get("workspaceId"),
                    "public_network_access": public_network_access,
                    "private_endpoint_connections": len(private_endpoints),
                    "compute_mode": props.get("computeMode"),
                    "exposure_class": exposure_class,
                },
            }),
        })

    return results
