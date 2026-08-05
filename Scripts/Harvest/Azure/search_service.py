"""Harvest Azure Search services."""
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

RESOURCE_TYPE = "Microsoft.Search/searchServices"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", RESOURCE_TYPE], subscription_id)
    results: list[dict[str, Any]] = []

    for service in raw:
        list_props = service.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props
            for key in ("publicNetworkAccess", "networkRuleSet", "privateEndpointConnections", "endpoint")
        )
        detailed = (
            az_resource_show(service.get("id", ""), subscription_id, runner=az)
            if service.get("id") and needs_detail
            else None
        )
        if detailed:
            service = {**service, **detailed}
        props = service.get("properties") or {}
        endpoint = safe_str(props.get("endpoint"))
        public_network_access = safe_str(props.get("publicNetworkAccess"))
        disable_local_auth = bool(props.get("disableLocalAuth", False))
        network_rules = props.get("networkRuleSet") or props.get("networkAcls") or {}
        is_public, is_restricted, ip_restrictions, exposure_class = classify_network_access(
            props,
            endpoint_present=bool(endpoint),
            network_acls=network_rules,
            private_endpoint_connections=props.get("privateEndpointConnections") or [],
        )

        results.append({
            "id": service["id"],
            "subscription_id": subscription_id,
            "resource_group": service.get("resourceGroup"),
            "name": service.get("name"),
            "type": service.get("type", RESOURCE_TYPE),
            "location": service.get("location"),
            "sku": infer_sku(service),
            "tags": json.dumps(service.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": build_endpoints([(endpoint, 443, "https")] if endpoint else []),
            "auth_methods": json.dumps([] if disable_local_auth else ["api_key"]),
            "fqdn": endpoint,
            "pipeline_tag": (service.get("tags") or {}).get("pipeline") or (service.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({
                **service,
                "_extra": {
                    "endpoint": endpoint,
                    "public_network_access": public_network_access,
                    "disable_local_auth": disable_local_auth,
                    "exposure_class": exposure_class,
                },
            }),
        })

    return results
