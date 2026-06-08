"""Harvest Azure Function Apps."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from ._helpers import az, build_endpoints, fetch_ase_ilb_map, infer_fqdn, infer_sku, safe_str, _is_msal_lock_error, _AZ_RETRY_MAX, _AZ_RETRY_BACKOFF

RESOURCE_TYPE = "Microsoft.Web/sites"  # function apps share the same ARM type

_DEFAULT_ALLOW_ALL_PRIORITY = 65000


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["functionapp", "list"], subscription_id)
    ase_ilb_map = fetch_ase_ilb_map(subscription_id)
    results = []

    for app in raw:
        fqdn = safe_str(app.get("defaultHostName")) or infer_fqdn(app)
        tags = app.get("tags") or {}
        pipeline_tag = None
        for key in ("pipeline", "Pipeline", "ado-pipeline", "build-pipeline"):
            if key in tags:
                pipeline_tag = safe_str(tags[key])
                break

        is_public, is_restricted, ip_restrictions = _classify_exposure(app, ase_ilb_map)
        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(_get_auth_methods(app))

        results.append({
            "id": app["id"],
            "subscription_id": subscription_id,
            "resource_group": app.get("resourceGroup"),
            "name": app.get("name"),
            "type": app.get("type", RESOURCE_TYPE),
            "location": app.get("location"),
            "sku": infer_sku(app),
            "tags": json.dumps(tags),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": pipeline_tag,
            "raw_json": json.dumps(app),
        })

    return results


def _classify_exposure(app: dict[str, Any], ase_ilb_map: dict[str, bool] | None = None) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_restriction_cidrs)."""
    props = app.get("properties") or app

    # ILB App Service Environment: web endpoint is VNet-internal only.
    # Only short-circuit when the ASE is confirmed in the map — if the lookup
    # failed (empty map) we fall through rather than silently mis-classifying.
    ase_profile = props.get("hostingEnvironmentProfile") or app.get("hostingEnvironmentProfile")
    if ase_profile and isinstance(ase_profile, dict) and ase_ilb_map:
        ase_id = safe_str(ase_profile.get("id") or "")
        if ase_id and ase_ilb_map.get(ase_id.lower(), False):
            return 0, 0, []

    public_network_access = props.get("publicNetworkAccess") or app.get("publicNetworkAccess", "Enabled")
    if public_network_access == "Disabled":
        return 0, 0, []

    # NOTE: virtualNetworkSubnetId = outbound VNet integration only.
    # It does NOT restrict inbound public access — do not use as a private indicator.

    site_config = props.get("siteConfig") or {}
    raw_rules = (site_config.get("ipSecurityRestrictions") or []) + (
        site_config.get("scmIpSecurityRestrictions") or []
    )
    meaningful_rules = [
        r for r in raw_rules
        if r.get("priority") != _DEFAULT_ALLOW_ALL_PRIORITY
        and r.get("action", "").lower() == "allow"
    ]

    if meaningful_rules:
        cidrs = [
            r.get("ipAddress") or r.get("vnetSubnetResourceId") or ""
            for r in meaningful_rules
            if r.get("ipAddress") or r.get("vnetSubnetResourceId")
        ]
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(app: dict[str, Any]) -> list[str]:
    props = app.get("properties") or {}
    methods: list[str] = []

    auth_settings = props.get("siteAuthSettings") or {}
    if auth_settings.get("enabled"):
        methods.append("azure_ad")
    else:
        methods.append("azure_ad_optional")

    # Function apps also support function-level API keys
    methods.append("function_key")

    return methods


def _extract_function_name(function_item: dict[str, Any]) -> str | None:
    name = safe_str(function_item.get("name"))
    if not name:
        return None
    return name.split("/")[-1]


def _extract_http_triggers(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for function_item in functions:
        props = function_item.get("properties") or {}
        config = props.get("config") or function_item.get("config") or {}
        bindings = config.get("bindings") or []
        function_name = _extract_function_name(function_item)
        if not function_name or not bindings:
            continue

        http_binding = next(
            (
                binding for binding in bindings
                if isinstance(binding, dict) and (binding.get("type") or "").lower() == "httptrigger"
            ),
            None,
        )
        if not http_binding:
            continue

        route = safe_str(http_binding.get("route")) or function_name
        auth_level = (safe_str(http_binding.get("authLevel")) or "function").lower()
        methods = [
            method.upper()
            for method in (http_binding.get("methods") or [])
            if safe_str(method)
        ]
        triggers.append({
            "function_name": function_name,
            "route": route,
            "auth_level": auth_level,
            "methods": methods,
        })
    return triggers


def _az_list_functions(app_name: str, resource_group: str, subscription_id: str) -> list[dict[str, Any]]:
    cmd = [
        "az", "functionapp", "function", "list",
        "--name", app_name,
        "--resource-group", resource_group,
        "--subscription", subscription_id,
        "--output", "json",
    ]
    last_stderr = ""
    for attempt in range(_AZ_RETRY_MAX):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as exc:
            raise RuntimeError(str(exc)[:200]) from exc
        if result.returncode == 0:
            return json.loads(result.stdout or "[]") or []
        last_stderr = result.stderr.strip()
        if attempt < _AZ_RETRY_MAX - 1 and _is_msal_lock_error(last_stderr):
            time.sleep(_AZ_RETRY_BACKOFF * (attempt + 1))
            continue
        # Extract the last meaningful line for a clean error message
        last_line = next((ln.strip() for ln in reversed(last_stderr.splitlines()) if ln.strip()), last_stderr)
        raise RuntimeError(last_line[:200])
    last_line = next((ln.strip() for ln in reversed(last_stderr.splitlines()) if ln.strip()), last_stderr)
    raise RuntimeError(last_line[:200])


def harvest_http_triggers(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Harvest HTTP trigger routes from Function Apps."""
    apps = az(["functionapp", "list"], subscription_id)
    if not apps:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    total = 0

    for app in apps:
        function_app_id = safe_str(app.get("id"))
        function_app_name = safe_str(app.get("name"))
        resource_group = safe_str(app.get("resourceGroup"))
        if not function_app_id or not function_app_name or not resource_group:
            continue

        print(f"    [function-triggers] {function_app_name}...", end=" ", flush=True)
        try:
            functions = _az_list_functions(function_app_name, resource_group, subscription_id)
            triggers = _extract_http_triggers(functions)
            fqdn = safe_str(app.get("defaultHostName")) or infer_fqdn(app)
            props = app.get("properties") or {}
            app_is_public = 1 if (props.get("publicNetworkAccess") or app.get("publicNetworkAccess") or "Enabled") != "Disabled" else 0

            rows = []
            for trigger in triggers:
                route_path = (trigger["route"] or trigger["function_name"]).lstrip("/")
                full_url = f"https://{fqdn}/api/{route_path}" if fqdn else None
                rows.append({
                    "id": f"{function_app_id}::{trigger['function_name']}",
                    "subscription_id": subscription_id,
                    "function_app_id": function_app_id,
                    "function_app_name": function_app_name,
                    "resource_group": resource_group,
                    "function_name": trigger["function_name"],
                    "route": trigger["route"],
                    "auth_level": trigger["auth_level"],
                    "methods": json.dumps(trigger["methods"]),
                    "fqdn": fqdn,
                    "full_url": full_url,
                    "is_public": 1 if app_is_public and trigger["auth_level"] == "anonymous" else 0,
                    "last_synced": now,
                })

            if not dry_run:
                conn.execute(
                    "DELETE FROM function_app_http_triggers WHERE subscription_id = ? AND function_app_id = ?",
                    (subscription_id, function_app_id),
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT INTO function_app_http_triggers (
                            id, subscription_id, function_app_id, function_app_name,
                            resource_group, function_name, route, auth_level,
                            methods, fqdn, full_url, is_public, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id   = excluded.subscription_id,
                            function_app_name = excluded.function_app_name,
                            resource_group    = excluded.resource_group,
                            route             = excluded.route,
                            auth_level        = excluded.auth_level,
                            methods           = excluded.methods,
                            fqdn              = excluded.fqdn,
                            full_url          = excluded.full_url,
                            is_public         = excluded.is_public,
                            last_synced       = excluded.last_synced
                        """,
                        (
                            row["id"],
                            row["subscription_id"],
                            row["function_app_id"],
                            row["function_app_name"],
                            row["resource_group"],
                            row["function_name"],
                            row["route"],
                            row["auth_level"],
                            row["methods"],
                            row["fqdn"],
                            row["full_url"],
                            row["is_public"],
                            row["last_synced"],
                        ),
                    )
                conn.commit()

            total += len(rows)
            print(f"{len(rows)} triggers")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    return total
