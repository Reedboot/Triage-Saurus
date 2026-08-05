"""Harvest Azure Kusto clusters."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import (
    az,
    az_resource_show,
    build_endpoints,
    classify_network_access,
    infer_sku,
    safe_str,
)

RESOURCE_TYPE = "Microsoft.Kusto/clusters"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for cluster in raw:
        list_props = cluster.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props
            for key in ("publicNetworkAccess", "publicIPType", "uri", "dataIngestionUri")
        )
        detailed = (
            az_resource_show(cluster.get("id", ""), subscription_id, runner=az)
            if cluster.get("id") and needs_detail
            else None
        )
        if detailed:
            cluster = {**cluster, **detailed}
        props = cluster.get("properties") or {}
        fqdn = safe_str(props.get("uri"))
        ingest_uri = safe_str(props.get("dataIngestionUri"))
        public_network_access = safe_str(props.get("publicNetworkAccess"))
        public_ip_type = safe_str(props.get("publicIPType"))

        endpoint_entries: list[tuple[str | None, int, str]] = []
        if fqdn:
            endpoint_entries.append((fqdn, 443, "https"))
        if ingest_uri:
            endpoint_entries.append((ingest_uri, 443, "https"))
        endpoints = build_endpoints(endpoint_entries)
        is_public, is_restricted, ip_restrictions, exposure_class = classify_network_access(
            props,
            endpoint_present=bool(fqdn),
        )
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
            "auth_methods": json.dumps(["aad"]),
            "fqdn": fqdn,
            "pipeline_tag": (cluster.get("tags") or {}).get("pipeline") or (cluster.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **cluster,
                "_extra": {
                    "public_network_access": public_network_access,
                    "public_ip_type": public_ip_type,
                    "data_ingestion_uri": ingest_uri,
                    "exposure_class": exposure_class,
                },
            }),
        })

    return results
