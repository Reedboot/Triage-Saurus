"""Harvest Azure Front Door Standard/Premium CDN profiles and endpoints."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import (
    az,
    az_resource_show,
    build_endpoints,
    enrich_resource_if_missing,
    infer_sku,
    safe_str,
)

PROFILE_TYPE = "Microsoft.Cdn/profiles"
ENDPOINT_TYPE = "Microsoft.Cdn/profiles/endpoints"
ENDPOINT_TYPE_AFD = "Microsoft.Cdn/profiles/afdEndpoints"


def _props(resource: dict[str, Any]) -> dict[str, Any]:
    value = resource.get("properties")
    return value if isinstance(value, dict) else resource


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = safe_str(value)
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def _parent_profile_id(resource_id: str | None) -> str | None:
    value = safe_str(resource_id)
    if not value:
        return None
    lower = value.lower()
    marker = "/profiles/"
    if marker not in lower:
        return None
    return value[: lower.index(marker) + len(marker)] + value[lower.index(marker) + len(marker):].split("/", 1)[0]


def _hostnames(resource: dict[str, Any]) -> list[str]:
    props = _props(resource)
    values: list[str] = []
    for key in (
        "hostName",
        "hostname",
        "frontDoorEndpointHostName",
        "defaultDomain",
        "domainName",
    ):
        value = props.get(key) or resource.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, (str, int)))
    for key in ("hostNames", "customDomains"):
        value = props.get(key) or resource.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, dict):
                    item_props = _props(item)
                    values.extend(
                        str(item_props.get(candidate))
                        for candidate in ("hostName", "hostname", "domainName")
                        if item_props.get(candidate)
                    )
    return _dedupe(values)


def _origin_hosts(resource: dict[str, Any]) -> list[str]:
    props = _props(resource)
    values: list[str] = []
    for key in ("hostName", "hostname", "originHostHeader", "address"):
        value = props.get(key) or resource.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("origins", "origin", "loadBalancingSettings"):
        value = props.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    values.extend(_origin_hosts(item))
    return _dedupe(values)


def _enabled_state(resource: dict[str, Any]) -> str:
    props = _props(resource)
    for key in ("enabledState", "enabled", "deploymentStatus", "provisioningState"):
        value = props.get(key)
        if value is None:
            value = resource.get(key)
        if isinstance(value, bool):
            return "Enabled" if value else "Disabled"
        text = safe_str(value)
        if text:
            return text
    return "Unknown"


def _resource_list(subscription_id: str, resource_type: str) -> list[dict[str, Any]]:
    return [
        item for item in az(["resource", "list", "--resource-type", resource_type], subscription_id)
        if isinstance(item, dict)
    ]


def _detail(resource: dict[str, Any], subscription_id: str, required: tuple[str, ...]) -> dict[str, Any]:
    return enrich_resource_if_missing(resource, subscription_id, required, runner=az)


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    profiles = _resource_list(subscription_id, PROFILE_TYPE)
    endpoints = _resource_list(subscription_id, ENDPOINT_TYPE)
    endpoints.extend(_resource_list(subscription_id, ENDPOINT_TYPE_AFD))
    routes = _resource_list(subscription_id, f"{ENDPOINT_TYPE}/routes")
    routes.extend(_resource_list(subscription_id, f"{ENDPOINT_TYPE_AFD}/routes"))
    origin_groups = _resource_list(subscription_id, f"{ENDPOINT_TYPE}/originGroups")
    origin_groups.extend(_resource_list(subscription_id, f"{ENDPOINT_TYPE_AFD}/originGroups"))
    origins = _resource_list(subscription_id, f"{ENDPOINT_TYPE}/originGroups/origins")
    origins.extend(_resource_list(subscription_id, f"{ENDPOINT_TYPE_AFD}/originGroups/origins"))

    endpoint_map: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        endpoint_id = safe_str(endpoint.get("id"))
        if endpoint_id:
            endpoint_map.setdefault(endpoint_id.lower(), endpoint)
    endpoints = list(endpoint_map.values())
    endpoint_by_id = {
        str(item.get("id") or "").lower(): item for item in endpoints if item.get("id")
    }
    profile_endpoints: dict[str, list[dict[str, Any]]] = {}
    for endpoint in endpoints:
        parent_id = _parent_profile_id(endpoint.get("id"))
        if parent_id:
            profile_endpoints.setdefault(parent_id.lower(), []).append(endpoint)

    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for child in [*routes, *origin_groups, *origins]:
        parent = _parent_profile_id(child.get("id"))
        if parent:
            children_by_parent.setdefault(parent.lower(), []).append(child)
        child_id = str(child.get("id") or "").lower()
        if child_id:
            endpoint_id = child_id.split("/routes/", 1)[0].split("/origingroups/", 1)[0]
            endpoint_id = endpoint_id.split("/origins/", 1)[0]
            children_by_parent.setdefault(endpoint_id, []).append(child)

    results: list[dict[str, Any]] = []
    for profile in profiles:
        profile = _detail(profile, subscription_id, ("originGroups", "frontDoorEndpoints", "endpoints"))
        profile_id = safe_str(profile.get("id"))
        if not profile_id:
            continue
        profile_key = profile_id.lower()
        endpoint_items = list(profile_endpoints.get(profile_key, []))
        if not endpoint_items:
            endpoint_items = [
                endpoint for endpoint in endpoint_by_id.values()
                if (
                    _parent_profile_id(endpoint.get("id") or "")
                    and _parent_profile_id(endpoint.get("id") or "").lower() == profile_key
                )
            ]

        endpoint_records: list[dict[str, Any]] = []
        all_hosts: list[str] = []
        for endpoint in endpoint_items:
            endpoint_id = safe_str(endpoint.get("id"))
            endpoint = _detail(endpoint, subscription_id, ("origins", "routes", "enabledState"))
            endpoint_props = _props(endpoint)
            endpoint_hosts = _hostnames(endpoint)
            all_hosts.extend(endpoint_hosts)
            endpoint_children = children_by_parent.get((endpoint_id or "").lower(), [])
            endpoint_origins = list(endpoint_props.get("origins") or [])
            endpoint_routes = list(endpoint_props.get("routes") or [])
            endpoint_origins.extend(
                child for child in endpoint_children
                if "/origins/" in str(child.get("id") or "").lower()
                or "/origingroups/" in str(child.get("id") or "").lower()
            )
            endpoint_routes.extend(
                child for child in endpoint_children
                if "/routes/" in str(child.get("id") or "").lower()
            )
            route_map: dict[str, dict[str, Any]] = {}
            for route in endpoint_routes:
                route_key = safe_str(route.get("id")) or safe_str(route.get("name"))
                if route_key:
                    route_map.setdefault(route_key.lower(), route)
            endpoint_routes = list(route_map.values())
            origin_hosts = _dedupe([
                host
                for origin in endpoint_origins
                for host in _origin_hosts(origin if isinstance(origin, dict) else {})
            ])
            route_metadata = [
                {
                    "name": safe_str(route.get("name")),
                    "id": safe_str(route.get("id")),
                    "patterns": _props(route).get("patternsToMatch") or [],
                    "origin_group": safe_str((_props(route).get("originGroup") or {}).get("id")),
                    "enabled_state": _enabled_state(route),
                }
                for route in endpoint_routes
                if isinstance(route, dict)
            ]
            endpoint_extra = {
                "exposure_class": "public_edge",
                "direction": "inbound",
                "purpose": "public CDN edge",
                "profile_id": profile_id,
                "endpoint_hostname": endpoint_hosts[0] if endpoint_hosts else None,
                "endpoint_hostnames": endpoint_hosts,
                "public_hostname": endpoint_hosts[0] if endpoint_hosts else None,
                "origin_hosts": origin_hosts,
                "origin_hostnames": origin_hosts,
                "enabled_state": _enabled_state(endpoint),
                "enabled": _enabled_state(endpoint).lower() not in {"disabled", "false"},
                "routing": route_metadata,
                "routes": route_metadata,
                "route_names": [item["name"] for item in route_metadata if item["name"]],
            }
            endpoint_records.append({
                "resource": endpoint,
                "hosts": endpoint_hosts,
                "origins": origin_hosts,
                "routes": route_metadata,
                "extra": endpoint_extra,
            })

        all_hosts = _dedupe(all_hosts)
        profile_children = children_by_parent.get(profile_key, [])
        profile_origins = _dedupe([
            host for child in profile_children for host in _origin_hosts(child)
        ])
        fqdn = all_hosts[0] if all_hosts else None
        profile_extra = {
            "exposure_class": "public_edge",
            "direction": "inbound",
            "purpose": "public CDN edge",
            "enabled_state": _enabled_state(profile),
            "enabled": _enabled_state(profile).lower() not in {"disabled", "false"},
            "endpoint_ids": [
                safe_str(record["resource"].get("id"))
                for record in endpoint_records
                if record["resource"].get("id")
            ],
            "endpoint_hostnames": all_hosts,
            "public_hostnames": all_hosts,
            "public_hostname": fqdn if all_hosts else None,
            "origin_hostnames": _dedupe([*profile_origins, *[
                host for record in endpoint_records for host in record["origins"]
            ]]),
            "endpoints": [
                {
                    "id": safe_str(record["resource"].get("id")),
                    "name": safe_str(record["resource"].get("name")),
                    "hostnames": record["hosts"],
                    "origin_hostnames": record["origins"],
                    "routes": record["routes"],
                    "enabled_state": record["extra"]["enabled_state"],
                }
                for record in endpoint_records
            ],
        }
        profile_extra["origin_hosts"] = profile_extra["origin_hostnames"]
        active_endpoint_hosts = [
            host
            for record in endpoint_records
            if record["extra"]["enabled_state"].lower() not in {"disabled", "false"}
            for host in record["hosts"]
        ]
        results.append({
            "id": profile_id,
            "subscription_id": subscription_id,
            "resource_group": profile.get("resourceGroup"),
            "name": profile.get("name"),
            "type": profile.get("type", PROFILE_TYPE),
            "location": profile.get("location"),
            "sku": infer_sku(profile),
            "tags": json.dumps(profile.get("tags") or {}),
            "is_public": 1 if active_endpoint_hosts else 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(host, 443, "https") for host in all_hosts]),
            "auth_methods": json.dumps([]),
            "fqdn": fqdn,
            "pipeline_tag": (profile.get("tags") or {}).get("pipeline") or (profile.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**profile, "_extra": profile_extra}),
        })

        for record in endpoint_records:
            endpoint = record["resource"]
            endpoint_id = safe_str(endpoint.get("id"))
            if not endpoint_id:
                continue
            results.append({
                "id": endpoint_id,
                "subscription_id": subscription_id,
                "resource_group": endpoint.get("resourceGroup") or profile.get("resourceGroup"),
                "name": endpoint.get("name"),
                "type": ENDPOINT_TYPE,
                "location": endpoint.get("location") or profile.get("location"),
                "sku": infer_sku(profile),
                "tags": json.dumps(endpoint.get("tags") or profile.get("tags") or {}),
                "is_public": 1 if (
                    record["hosts"]
                    and record["extra"]["enabled_state"].lower() not in {"disabled", "false"}
                ) else 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": build_endpoints([(host, 443, "https") for host in record["hosts"]]),
                "auth_methods": json.dumps([]),
                "fqdn": record["hosts"][0] if record["hosts"] else None,
                "pipeline_tag": None,
                "raw_json": json.dumps({**endpoint, "_extra": record["extra"]}),
            })

    return results
