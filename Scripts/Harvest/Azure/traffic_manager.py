"""Harvest Azure Traffic Manager profiles."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.Network/trafficmanagerprofiles"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "traffic-manager", "profile", "list"], subscription_id)
    results = []

    for profile in raw:
        props = profile.get("properties") or {}
        dns_config = props.get("dnsConfig") or {}
        fqdn = safe_str(dns_config.get("fqdn"))

        # Traffic Manager is DNS-level routing — probe the DNS resolution
        tm_endpoints = build_endpoints([(fqdn, None, "dns")] if fqdn else [])

        raw_endpoints = props.get("endpoints") or []
        extra = {
            "routing_method": props.get("trafficRoutingMethod"),
            "profile_status": props.get("profileStatus"),
            "dns_ttl": dns_config.get("ttl"),
            "fqdn": fqdn,
            "monitor_protocol": (props.get("monitorConfig") or {}).get("protocol"),
            "monitor_port": (props.get("monitorConfig") or {}).get("port"),
            "monitor_path": (props.get("monitorConfig") or {}).get("path"),
            "endpoint_count": len(raw_endpoints),
            "endpoints": [
                {
                    "name": ep.get("name"),
                    "target": (ep.get("properties") or {}).get("target"),
                    "target_resource_id": (ep.get("properties") or {}).get("targetResourceId"),
                    "weight": (ep.get("properties") or {}).get("weight"),
                    "priority": (ep.get("properties") or {}).get("priority"),
                    "endpoint_status": (ep.get("properties") or {}).get("endpointStatus"),
                }
                for ep in raw_endpoints
            ],
        }

        results.append({
            "id": profile["id"],
            "subscription_id": subscription_id,
            "resource_group": profile.get("resourceGroup"),
            "name": profile.get("name"),
            "type": profile.get("type", RESOURCE_TYPE),
            "location": profile.get("location"),
            "sku": None,
            "tags": json.dumps(profile.get("tags") or {}),
            "is_public": 1,  # Traffic Manager profiles are DNS-level, publicly resolvable
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": tm_endpoints,
            "auth_methods": json.dumps([]),
            "fqdn": fqdn,
            "pipeline_tag": (profile.get("tags") or {}).get("pipeline") or (profile.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**profile, "_extra": extra}),
        })

    return results
