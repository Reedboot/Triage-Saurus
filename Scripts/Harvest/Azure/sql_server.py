"""Harvest Azure SQL Servers."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Sql/servers"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["sql", "server", "list"], subscription_id)
    results = []

    for server in raw:
        props = server.get("properties") or {}
        fqdn = safe_str(props.get("fullyQualifiedDomainName"))
        is_public, is_restricted, ip_restrictions, firewall_rules = _classify_exposure(server, subscription_id)
        auth_methods = json.dumps(_get_auth_methods(server, subscription_id))

        endpoints = build_endpoints([(fqdn, 1433, "tds/tcp")] if fqdn else [])

        extra = {
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "minimal_tls_version": props.get("minimalTlsVersion"),
            "admin_login": props.get("administratorLogin"),
            "firewall_rule_count": len(firewall_rules),
        }

        results.append({
            "id": server["id"],
            "subscription_id": subscription_id,
            "resource_group": server.get("resourceGroup"),
            "name": server.get("name"),
            "type": server.get("type", RESOURCE_TYPE),
            "location": server.get("location"),
            "sku": None,
            "tags": json.dumps(server.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**server, "_extra": extra}),
        })

        results.extend(_harvest_databases(subscription_id, server, fqdn, is_public, is_restricted, ip_restrictions, auth_methods))

    return results


def _classify_exposure(
    server: dict[str, Any], subscription_id: str
) -> tuple[int, int, list[str], list[dict]]:
    props = server.get("properties") or server

    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0, 0, [], []

    server_name = server.get("name")
    resource_group = server.get("resourceGroup")
    firewall_rules: list[dict] = []
    if server_name and resource_group:
        try:
            firewall_rules = az(
                ["sql", "server", "firewall-rule", "list",
                 "-s", server_name, "-g", resource_group],
                subscription_id,
            )
        except Exception:
            pass

    if not firewall_rules:
        return 1, 0, [], []

    cidrs: list[str] = []
    for rule in firewall_rules:
        start = rule.get("startIpAddress", "")
        end = rule.get("endIpAddress", "")
        if start == "0.0.0.0" and end == "0.0.0.0":
            cidrs.append("0.0.0.0/32 (Allow Azure services)")
        elif start and end:
            cidrs.append(f"{start}-{end}")
    return 0, 1, cidrs, firewall_rules


def _get_auth_methods(server: dict[str, Any], subscription_id: str) -> list[str]:
    props = server.get("properties") or server
    methods = ["sql_auth"]

    # Check for AAD admin configured
    admin = props.get("administrators") or {}
    if admin.get("administratorType") == "ActiveDirectory" or admin.get("login"):
        methods.append("azure_ad")
    else:
        # Try az sql server ad-admin list (may not always be populated inline)
        server_name = server.get("name")
        rg = server.get("resourceGroup")
        if server_name and rg:
            ad_admins = az(
                ["sql", "server", "ad-admin", "list",
                 "--server", server_name, "--resource-group", rg],
                subscription_id,
            )
            if ad_admins:
                methods.append("azure_ad")

    return methods


def _harvest_databases(
    subscription_id: str,
    server: dict[str, Any],
    server_fqdn: str | None,
    server_is_public: int,
    server_is_restricted: int,
    server_ip_restrictions: list[str],
    server_auth_methods: str,
) -> list[dict[str, Any]]:
    server_name = safe_str(server.get("name"))
    resource_group = safe_str(server.get("resourceGroup"))
    server_id = safe_str(server.get("id"))
    if not server_name or not resource_group:
        return []

    databases = az(
        [
            "sql", "db", "list",
            "--server", server_name,
            "--resource-group", resource_group,
        ],
        subscription_id,
    )
    if not databases:
        return []

    rows: list[dict[str, Any]] = []
    for db in databases:
        name = safe_str(db.get("name"))
        if not name or name.lower() == "master":
            continue

        db_id = safe_str(db.get("id")) or (f"{server_id}/databases/{name}" if server_id else None)
        if not db_id:
            continue

        rows.append({
            "id": db_id,
            "subscription_id": subscription_id,
            "resource_group": safe_str(db.get("resourceGroup")) or resource_group,
            "name": name,
            "type": "Microsoft.Sql/servers/databases",
            "location": safe_str(db.get("location")) or server.get("location"),
            "sku": infer_sku(db),
            "tags": json.dumps(db.get("tags") or {}),
            "is_public": int(bool(server_is_public)),
            "is_restricted": int(bool(server_is_restricted)),
            "ip_restrictions": json.dumps(server_ip_restrictions),
            "endpoints": json.dumps([]),
            "auth_methods": server_auth_methods,
            "fqdn": server_fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps(db),
        })

    return rows
