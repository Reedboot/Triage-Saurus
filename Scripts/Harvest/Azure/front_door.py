"""Harvest Azure Front Door (classic + Standard/Premium AFD)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE_CLASSIC = "Microsoft.Network/frontdoors"
RESOURCE_TYPE_AFD = "Microsoft.Cdn/profiles"


def _tail(resource_id: str | None) -> str | None:
    value = safe_str(resource_id)
    if not value:
        return None
    return value.rstrip("/").split("/")[-1]


def _dedupe_strs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def _is_afd_profile(profile: dict[str, Any]) -> bool:
    sku = infer_sku(profile) or ""
    return sku.startswith("Standard_AzureFrontDoor") or sku.startswith("Premium_AzureFrontDoor")


def _extract_classic_frontend_map(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = profile.get("properties") or profile
    frontend_map: dict[str, dict[str, Any]] = {}
    for endpoint in props.get("frontendEndpoints") or []:
        endpoint_props = endpoint.get("properties") or endpoint
        endpoint_id = safe_str(endpoint.get("id") or endpoint_props.get("id"))
        endpoint_name = safe_str(endpoint.get("name") or _tail(endpoint_id))
        hostname = safe_str(endpoint_props.get("hostName") or endpoint.get("hostName"))
        if endpoint_name:
            frontend_map[endpoint_name] = {
                "name": endpoint_name,
                "hostname": hostname,
                "waf_policy": _tail(((endpoint_props.get("webApplicationFirewallPolicyLink") or {}).get("id"))),
            }
        if endpoint_id:
            frontend_map[endpoint_id] = frontend_map.get(endpoint_name or endpoint_id, {
                "name": endpoint_name,
                "hostname": hostname,
                "waf_policy": _tail(((endpoint_props.get("webApplicationFirewallPolicyLink") or {}).get("id"))),
            })
    return frontend_map


def _extract_classic_backend_map(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = profile.get("properties") or profile
    backend_map: dict[str, dict[str, Any]] = {}
    for pool in props.get("backendPools") or []:
        pool_props = pool.get("properties") or pool
        pool_id = safe_str(pool.get("id") or pool_props.get("id"))
        pool_name = safe_str(pool.get("name") or _tail(pool_id))
        payload = {
            "name": pool_name,
            "origins": _dedupe_strs([
                address
                for backend in (pool_props.get("backends") or [])
                for address in [safe_str((backend.get("properties") or backend).get("address") or backend.get("address"))]
                if address
            ]),
        }
        if pool_name:
            backend_map[pool_name] = payload
        if pool_id:
            backend_map[pool_id] = payload
    return backend_map


def _extract_classic_routes(profile: dict[str, Any]) -> list[dict[str, Any]]:
    props = profile.get("properties") or profile
    profile_name = safe_str(profile.get("name")) or "unknown-frontdoor"
    frontend_map = _extract_classic_frontend_map(profile)
    backend_map = _extract_classic_backend_map(profile)
    rows: list[dict[str, Any]] = []

    for rule in props.get("routingRules") or []:
        rule_props = rule.get("properties") or rule
        route_name = safe_str(rule.get("name"))
        if not route_name:
            continue
        patterns = rule_props.get("patternsToMatch") or ["/*"]
        backend_pool_ref = rule_props.get("backendPool") or {}
        backend_key = safe_str(backend_pool_ref.get("id")) or safe_str(backend_pool_ref.get("name"))
        backend = backend_map.get(backend_key or "", {})
        frontend_refs = rule_props.get("frontendEndpoints") or []
        if not frontend_refs:
            frontend_refs = [{"id": key} for key, value in frontend_map.items() if key.startswith("/") and value.get("hostname")]

        for frontend_ref in frontend_refs:
            frontend_key = safe_str((frontend_ref or {}).get("id")) or safe_str((frontend_ref or {}).get("name"))
            frontend = frontend_map.get(frontend_key or "")
            if not frontend:
                frontend = frontend_map.get(_tail(frontend_key) or "")
            if not frontend or not frontend.get("hostname"):
                continue
            rows.append({
                "profile_name": profile_name,
                "profile_tier": "Classic",
                "endpoint_name": frontend.get("name"),
                "hostname": frontend.get("hostname"),
                "route_name": route_name,
                "patterns": patterns,
                "origin_group": backend.get("name"),
                "origins": backend.get("origins") or [],
                "waf_policy": frontend.get("waf_policy"),
                "https_redirect": 1 if rule_props.get("httpsRedirect") else 0,
                "exposure_level": "Public",
            })
    return rows


def _extract_afd_route(
    route: dict[str, Any],
    hostname: str | None,
    profile_name: str,
    profile_tier: str,
    endpoint_name: str,
    origins: list[str] | None = None,
    waf_policy: str | None = None,
) -> dict[str, Any] | None:
    route_props = route.get("properties") or route
    route_name = safe_str(route.get("name"))
    if not route_name:
        return None
    return {
        "profile_name": profile_name,
        "profile_tier": profile_tier,
        "endpoint_name": endpoint_name,
        "hostname": hostname,
        "route_name": route_name,
        "patterns": route_props.get("patternsToMatch") or ["/*"],
        "origin_group": _tail(((route_props.get("originGroup") or {}).get("id"))),
        "origins": origins or [],
        "waf_policy": waf_policy,
        "https_redirect": 1 if (route_props.get("httpsRedirect") in (True, "Enabled")) else 0,
        "exposure_level": "Public",
    }


def _get_classic_hosts(profile: dict[str, Any]) -> list[str]:
    frontend_map = _extract_classic_frontend_map(profile)
    return _dedupe_strs([
        frontend.get("hostname")
        for key, frontend in frontend_map.items()
        if not key.startswith("/") and frontend.get("hostname")
    ])


def _list_afd_endpoints(profile_name: str, resource_group: str, subscription_id: str) -> list[dict[str, Any]]:
    return az(["afd", "endpoint", "list", "--profile-name", profile_name, "--resource-group", resource_group], subscription_id)


def _list_afd_routes(endpoint_name: str, profile_name: str, resource_group: str, subscription_id: str) -> list[dict[str, Any]]:
    return az([
        "afd", "route", "list",
        "--endpoint-name", endpoint_name,
        "--profile-name", profile_name,
        "--resource-group", resource_group,
    ], subscription_id)


def _list_afd_origins(origin_group: str, profile_name: str, resource_group: str, subscription_id: str) -> list[str]:
    origins = az([
        "afd", "origin", "list",
        "--origin-group-name", origin_group,
        "--profile-name", profile_name,
        "--resource-group", resource_group,
    ], subscription_id)
    return _dedupe_strs([
        host
        for origin in origins
        for host in [safe_str((origin.get("properties") or origin).get("hostName") or origin.get("hostName"))]
        if host
    ])


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for profile in az(["network", "front-door", "list"], subscription_id):
        hosts = _get_classic_hosts(profile)
        fqdn = hosts[0] if hosts else None
        results.append({
            "id": profile["id"],
            "subscription_id": subscription_id,
            "resource_group": profile.get("resourceGroup"),
            "name": profile.get("name"),
            "type": profile.get("type", RESOURCE_TYPE_CLASSIC),
            "location": profile.get("location"),
            "sku": "Classic",
            "tags": json.dumps(profile.get("tags") or {}),
            "is_public": 1,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
            "auth_methods": json.dumps([]),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps(profile),
        })

    for profile in az(["afd", "profile", "list"], subscription_id):
        if not _is_afd_profile(profile):
            continue
        profile_name = safe_str(profile.get("name"))
        resource_group = safe_str(profile.get("resourceGroup"))
        if not profile_name or not resource_group:
            continue
        try:
            endpoints = _list_afd_endpoints(profile_name, resource_group, subscription_id)
            hosts = _dedupe_strs([
                host
                for endpoint in endpoints
                for host in [safe_str((endpoint.get("properties") or endpoint).get("hostName") or endpoint.get("hostName"))]
                if host
            ])
            fqdn = hosts[0] if hosts else None
            results.append({
                "id": profile["id"],
                "subscription_id": subscription_id,
                "resource_group": profile.get("resourceGroup"),
                "name": profile.get("name"),
                "type": profile.get("type", RESOURCE_TYPE_AFD),
                "location": profile.get("location"),
                "sku": infer_sku(profile),
                "tags": json.dumps(profile.get("tags") or {}),
                "is_public": 1,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
                "auth_methods": json.dumps([]),
                "fqdn": fqdn,
                "pipeline_tag": None,
                "raw_json": json.dumps({**profile, "_extra": {"endpoint_hosts": hosts}}),
            })
        except Exception as exc:
            print(f"    [front-door] {profile_name} SKIPPED ({exc})")

    return results


def harvest_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Harvest Front Door routing rules into front_door_routes."""
    now = datetime.now(timezone.utc).isoformat()
    total = 0

    classic_profiles = az(["network", "front-door", "list"], subscription_id)
    for profile in classic_profiles:
        profile_name = safe_str(profile.get("name"))
        if not profile_name:
            continue
        print(f"    [front-door-routes] {profile_name}...", end=" ", flush=True)
        try:
            rows = _extract_classic_routes(profile)
            if not dry_run:
                conn.execute(
                    "DELETE FROM front_door_routes WHERE subscription_id = ? AND profile_name = ?",
                    (subscription_id, profile_name),
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO front_door_routes (
                            id, subscription_id, profile_name, profile_tier, endpoint_name,
                            hostname, route_name, patterns, origin_group, origins,
                            waf_policy, https_redirect, exposure_level, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id = excluded.subscription_id,
                            profile_tier    = excluded.profile_tier,
                            hostname        = excluded.hostname,
                            patterns        = excluded.patterns,
                            origin_group    = excluded.origin_group,
                            origins         = excluded.origins,
                            waf_policy      = excluded.waf_policy,
                            https_redirect  = excluded.https_redirect,
                            exposure_level  = excluded.exposure_level,
                            last_synced     = excluded.last_synced
                        """,
                        (
                            f"{row['profile_name']}::{row['endpoint_name']}::{row['route_name']}",
                            subscription_id,
                            row["profile_name"],
                            row["profile_tier"],
                            row["endpoint_name"],
                            row["hostname"],
                            row["route_name"],
                            json.dumps(row["patterns"]),
                            row["origin_group"],
                            json.dumps(row["origins"]),
                            row["waf_policy"],
                            row["https_redirect"],
                            row["exposure_level"],
                            now,
                        ),
                    )
                conn.commit()
            total += len(rows)
            print(f"{len(rows)} routes")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    afd_profiles = [profile for profile in az(["afd", "profile", "list"], subscription_id) if _is_afd_profile(profile)]
    for profile in afd_profiles:
        profile_name = safe_str(profile.get("name"))
        resource_group = safe_str(profile.get("resourceGroup"))
        if not profile_name or not resource_group:
            continue
        print(f"    [front-door-routes] {profile_name}...", end=" ", flush=True)
        try:
            profile_tier = infer_sku(profile) or "Standard_AzureFrontDoor"
            endpoints = _list_afd_endpoints(profile_name, resource_group, subscription_id)
            rows: list[dict[str, Any]] = []
            for endpoint in endpoints:
                endpoint_name = safe_str(endpoint.get("name"))
                hostname = safe_str((endpoint.get("properties") or endpoint).get("hostName") or endpoint.get("hostName"))
                if not endpoint_name:
                    continue
                for route in _list_afd_routes(endpoint_name, profile_name, resource_group, subscription_id):
                    route_row = _extract_afd_route(route, hostname, profile_name, profile_tier, endpoint_name)
                    if not route_row:
                        continue
                    origin_group = route_row.get("origin_group")
                    if origin_group:
                        route_row["origins"] = _list_afd_origins(origin_group, profile_name, resource_group, subscription_id)
                    rows.append(route_row)

            if not dry_run:
                conn.execute(
                    "DELETE FROM front_door_routes WHERE subscription_id = ? AND profile_name = ?",
                    (subscription_id, profile_name),
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO front_door_routes (
                            id, subscription_id, profile_name, profile_tier, endpoint_name,
                            hostname, route_name, patterns, origin_group, origins,
                            waf_policy, https_redirect, exposure_level, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id = excluded.subscription_id,
                            profile_tier    = excluded.profile_tier,
                            hostname        = excluded.hostname,
                            patterns        = excluded.patterns,
                            origin_group    = excluded.origin_group,
                            origins         = excluded.origins,
                            waf_policy      = excluded.waf_policy,
                            https_redirect  = excluded.https_redirect,
                            exposure_level  = excluded.exposure_level,
                            last_synced     = excluded.last_synced
                        """,
                        (
                            f"{row['profile_name']}::{row['endpoint_name']}::{row['route_name']}",
                            subscription_id,
                            row["profile_name"],
                            row["profile_tier"],
                            row["endpoint_name"],
                            row["hostname"],
                            row["route_name"],
                            json.dumps(row["patterns"]),
                            row["origin_group"],
                            json.dumps(row["origins"]),
                            row["waf_policy"],
                            row["https_redirect"],
                            row["exposure_level"],
                            now,
                        ),
                    )
                conn.commit()
            total += len(rows)
            print(f"{len(rows)} routes")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    return total
