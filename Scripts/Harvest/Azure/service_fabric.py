"""Harvest Azure Service Fabric clusters."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.ServiceFabric/clusters"


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

        results.append({
            "id": cluster["id"],
            "subscription_id": subscription_id,
            "resource_group": cluster.get("resourceGroup"),
            "name": cluster.get("name"),
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
