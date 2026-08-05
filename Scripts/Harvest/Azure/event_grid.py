"""Harvest Azure Event Grid topics."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, az_resource_show, build_endpoints, classify_network_access, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.EventGrid/topics"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for topic in raw:
        list_props = topic.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props for key in ("publicNetworkAccess", "networkAcls", "endpoint")
        )
        detailed = (
            az_resource_show(topic.get("id", ""), subscription_id, runner=az)
            if topic.get("id") and needs_detail
            else None
        )
        if detailed:
            topic = {**topic, **detailed}
        props = topic.get("properties") or {}
        endpoint = safe_str(props.get("endpoint"))
        public_network_access = safe_str(props.get("publicNetworkAccess"))
        disable_local_auth = bool(props.get("disableLocalAuth", False))
        is_public, is_restricted, ip_restrictions, exposure_class = classify_network_access(
            props, endpoint_present=bool(endpoint)
        )

        results.append({
            "id": topic["id"],
            "subscription_id": subscription_id,
            "resource_group": topic.get("resourceGroup"),
            "name": topic.get("name"),
            "type": topic.get("type", RESOURCE_TYPE),
            "location": topic.get("location"),
            "sku": infer_sku(topic),
            "tags": json.dumps(topic.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": build_endpoints([(endpoint, 443, "https")] if endpoint else []),
            "auth_methods": json.dumps([] if disable_local_auth else ["shared_access_key"]),
            "fqdn": endpoint,
            "pipeline_tag": (topic.get("tags") or {}).get("pipeline") or (topic.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **topic,
                "_extra": {
                    "endpoint": endpoint,
                    "public_network_access": public_network_access,
                    "disable_local_auth": disable_local_auth,
                    "exposure_class": exposure_class,
                },
            }),
        })

    return results
