"""Harvest Azure Kusto clusters."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Kusto/clusters"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for cluster in raw:
        props = cluster.get("properties") or {}
        fqdn = safe_str(props.get("uri"))
        ingest_uri = safe_str(props.get("dataIngestionUri"))
        public_network_access = safe_str(props.get("publicNetworkAccess") or "Enabled")
        public_ip_type = safe_str(props.get("publicIPType"))

        endpoint_entries: list[tuple[str | None, int, str]] = []
        if fqdn:
            endpoint_entries.append((fqdn, 443, "https"))
        if ingest_uri:
            endpoint_entries.append((ingest_uri, 443, "https"))
        endpoints = build_endpoints(endpoint_entries)
        results.append({
            "id": cluster["id"],
            "subscription_id": subscription_id,
            "resource_group": cluster.get("resourceGroup"),
            "name": cluster.get("name"),
            "type": cluster.get("type", RESOURCE_TYPE),
            "location": cluster.get("location"),
            "sku": infer_sku(cluster),
            "tags": json.dumps(cluster.get("tags") or {}),
            "is_public": 1 if fqdn and public_network_access != "Disabled" else 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": endpoints,
            "auth_methods": json.dumps(["aad"]),
            "fqdn": fqdn,
            "pipeline_tag": (cluster.get("tags") or {}).get("pipeline") or (cluster.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **cluster,
                "_extra": {
                    "public_network_access": public_network_access,
                    "public_ip_type": public_ip_type,
                    "data_ingestion_uri": ingest_uri,
                },
            }),
        })

    return results
