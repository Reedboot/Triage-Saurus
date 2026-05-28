"""Harvest Azure Redis Cache instances."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, safe_str

RESOURCE_TYPE = "Microsoft.Cache/Redis"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["redis", "list"], subscription_id)
    results = []

    for r in raw:
        props = r.get("properties") or {}
        host = safe_str(props.get("hostName"))
        is_public, is_restricted, ip_restrictions = _classify_exposure(r, subscription_id)

        endpoint_entries = _get_endpoint_entries(props, host)
        endpoints = build_endpoints(endpoint_entries)
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "sku_name": (r.get("sku") or {}).get("name"),
            "sku_capacity": (r.get("sku") or {}).get("capacity"),
            "redis_version": props.get("redisVersion"),
            "ssl_port": props.get("sslPort"),
            "non_ssl_port_enabled": props.get("enableNonSslPort", False),
            "minimum_tls_version": props.get("minimumTlsVersion"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "replication_mode": props.get("replicationMode"),
        }

        results.append({
            "id": r["id"],
            "subscription_id": subscription_id,
            "resource_group": r.get("resourceGroup"),
            "name": r.get("name"),
            "type": r.get("type", RESOURCE_TYPE),
            "location": r.get("location"),
            "sku": (r.get("sku") or {}).get("name"),
            "tags": json.dumps(r.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": host,
            "pipeline_tag": (r.get("tags") or {}).get("pipeline") or (r.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**r, "_extra": extra}),
        })

    return results


def _classify_exposure(cache: dict[str, Any], subscription_id: str) -> tuple[int, int, list[str]]:
    props = cache.get("properties") or {}

    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0, 0, []

    cache_name = cache.get("name")
    resource_group = cache.get("resourceGroup")
    firewall_rules: list[dict] = []
    if cache_name and resource_group:
        try:
            firewall_rules = az(
                ["redis", "firewall-rules", "list",
                 "--name", cache_name, "--resource-group", resource_group],
                subscription_id,
            )
        except Exception:
            pass

    if firewall_rules:
        cidrs = [
            f"{r.get('startIP', '')}-{r.get('endIP', '')}"
            for r in firewall_rules
            if r.get("startIP")
        ]
        return 0, 1, cidrs

    return 1, 0, []


def _get_endpoint_entries(props: dict[str, Any], host: str | None) -> list[tuple[str | None, int, str]]:
    entries: list[tuple[str | None, int, str]] = []
    if not host:
        return entries
    ssl_port = props.get("sslPort") or 6380
    entries.append((host, int(ssl_port), "redis+tls"))
    if props.get("enableNonSslPort", False):
        entries.append((host, 6379, "redis"))
    return entries


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    redis_config = props.get("redisConfiguration") or {}
    if redis_config.get("authnotrequired") == "true":
        return ["none"]
    return ["access_key"]
