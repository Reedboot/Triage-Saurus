#!/usr/bin/env python3
"""Build a full App Gateway → backend and rewrite routing map and persist it to cozo.db.

NOTE: This is now run automatically as part of the main harvest pipeline
(Scripts/Harvest/harvest_azure_assets.py). You only need to run this script
manually if you want to refresh routing/rewrite/WAF data without re-harvesting all assets,
or if you need to use the --dry-run flag to inspect what would be written.

For each Application Gateway in the subscription this script:
  1. Calls `az network application-gateway show` per gateway to get full nested properties
  2. Builds the chain: public hostname (listener) → routing rule → URL path map → backend pool → backend FQDNs
  3. Captures rewrite rule sets associated with routing rules and path maps
  4. Captures WAF policy references and mode/state for each listener/path rule
  5. Cross-references backend pool addresses with provisioned_assets.fqdn
     to create resource_connections rows (type='appgw_routing')
  6. Stores the routing map in appgw_routing_rules table
  7. Stores rewrite rule sets in appgw_rewrite_rule_sets table
  8. Stores WAF policy summary in appgw_waf_policies table

Usage:
    python Scripts/Harvest/appgw_routing_map.py --subscription "subscription-production"
    python Scripts/Harvest/appgw_routing_map.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore
from Azure._helpers import normalize_host_key, normalize_route_path, route_path_matches  # type: ignore

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_APPGW_DDL = """
CREATE TABLE IF NOT EXISTS appgw_routing_rules (
    id                  TEXT PRIMARY KEY,   -- {gw_name}::{rule_name}::{path}
    subscription_id     TEXT NOT NULL,
    gateway_name        TEXT NOT NULL,
    gateway_resource_id TEXT,
    resource_group      TEXT,
    rule_name           TEXT NOT NULL,
    listener_name       TEXT,
    hostname            TEXT,               -- public hostname from listener
    protocol            TEXT,               -- HTTP / HTTPS
    url_path            TEXT DEFAULT '/*',  -- path pattern (/* = catch-all)
    backend_pool_name   TEXT,
    backend_fqdns       TEXT,               -- JSON array of resolved FQDNs/IPs
    http_settings_name  TEXT,
    backend_port        INTEGER,
    backend_protocol    TEXT,
    host_override       TEXT,               -- backend host header override
    waf_policy_name     TEXT,               -- per-rule/per-listener WAF policy
    exposure_level      TEXT DEFAULT 'Public',
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_sub     ON appgw_routing_rules(subscription_id);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_gateway ON appgw_routing_rules(gateway_name);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_host    ON appgw_routing_rules(hostname);

CREATE TABLE IF NOT EXISTS appgw_rewrite_rule_sets (
    id                  TEXT PRIMARY KEY,   -- {gw_name}::{set_name}
    subscription_id     TEXT NOT NULL,
    gateway_name        TEXT NOT NULL,
    gateway_resource_id TEXT,
    resource_group      TEXT,
    set_name            TEXT NOT NULL,
    attached_routes     TEXT,               -- JSON array of routing-rule/path-rule references
    attached_route_count INTEGER DEFAULT 0,
    rewrite_rules       TEXT,               -- JSON array of rewrite rules
    rewrite_rule_count  INTEGER DEFAULT 0,
    provisioning_state  TEXT,
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_appgw_rewrite_sets_sub     ON appgw_rewrite_rule_sets(subscription_id);
CREATE INDEX IF NOT EXISTS idx_appgw_rewrite_sets_gateway ON appgw_rewrite_rule_sets(gateway_name);

CREATE TABLE IF NOT EXISTS appgw_waf_policies (
    id                  TEXT PRIMARY KEY,   -- resource id
    subscription_id     TEXT NOT NULL,
    name                TEXT NOT NULL,
    resource_group      TEXT,
    mode                TEXT,               -- Prevention / Detection / NULL (unconfigured)
    state               TEXT,               -- Enabled / Disabled / NULL
    request_body_check  INTEGER DEFAULT 0,
    max_body_kb         INTEGER,
    managed_rule_sets   TEXT,               -- JSON array of {type, version}
    custom_rules_count  INTEGER DEFAULT 0,
    exclusions_count    INTEGER DEFAULT 0,
    associated_gateways TEXT,               -- JSON array of gateway names using this policy
    last_synced         DATETIME
);
"""
_APPGW_FETCH_WORKERS = 4


def _ensure_appgw_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_APPGW_DDL)
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(appgw_routing_rules)").fetchall()
    }
    if "exposure_level" not in existing_columns:
        conn.execute("ALTER TABLE appgw_routing_rules ADD COLUMN exposure_level TEXT DEFAULT 'Public'")
    conn.commit()


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------

def _az(*args: str, subscription_id: str, timeout: int = 120) -> Any:
    cmd = ["az", *args, "--subscription", subscription_id, "--output", "json"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        print(f"    [warn] az {' '.join(args[:4])} timed out after {timeout}s; skipping")
        return None
    if proc.returncode != 0:
        print(f"    [warn] az {' '.join(args[:4])} failed: {stderr.strip()[:120]}")
        return None
    try:
        return json.loads(stdout or "null")
    except json.JSONDecodeError:
        return None


def list_appgw(subscription_id: str) -> list[dict]:
    return _az("network", "application-gateway", "list", subscription_id=subscription_id) or []


def show_appgw(name: str, rg: str, subscription_id: str) -> dict | None:
    return _az(
        "network", "application-gateway", "show",
        "--name", name, "-g", rg,
        subscription_id=subscription_id,
    )


def show_url_path_map(name: str, rg: str, map_name: str, subscription_id: str) -> dict | None:
    return _az(
        "network", "application-gateway", "url-path-map", "show",
        "--gateway-name", name,
        "--name", map_name,
        "--resource-group", rg,
        subscription_id=subscription_id,
    )


def list_waf_policies(subscription_id: str) -> list[dict]:
    return _az("network", "application-gateway", "waf-policy", "list",
               subscription_id=subscription_id) or []


def show_waf_policy(name: str, rg: str, subscription_id: str) -> dict | None:
    return _az(
        "network", "application-gateway", "waf-policy", "show",
        "--name", name, "-g", rg,
        subscription_id=subscription_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id_tail(resource_id: str | None) -> str:
    return (resource_id or "").split("/")[-1]


def _url_to_fqdn(url: str | None) -> str | None:
    if not url:
        return None
    return url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0] or None


def _build_lookup(items: list[dict], key: str = "name") -> dict[str, dict]:
    return {_id_tail(item.get("id", "")) or item.get(key, ""): item for item in items}


def _dedupe_route_paths(paths: list[str] | None) -> list[str]:
    """Normalize and de-duplicate path patterns while preserving order."""
    unique_paths: list[str] = []
    for raw_path in paths or []:
        path = normalize_route_path(raw_path)
        if not path:
            continue

        duplicate = any(
            route_path_matches(existing, path) and route_path_matches(path, existing)
            for existing in unique_paths
        )
        if duplicate:
            continue

        unique_paths.append(path)
    return unique_paths


# ---------------------------------------------------------------------------
# Core: extract routing chains from a fully-hydrated AppGW
# ---------------------------------------------------------------------------

def extract_routes(gw: dict, subscription_id: str, routing_rules: list[dict] | None = None) -> list[dict]:
    """
    Return a flat list of route dicts, one per (listener × path_rule).
    Each dict has: rule_name, listener_name, hostname, protocol, url_path,
    backend_pool_name, backend_fqdns, http_settings_name, backend_port,
    backend_protocol, host_override, waf_policy_name.
    """
    props = gw.get("properties") or gw

    # Build lookups by resource name (last segment of id)
    listeners_lkp   = _build_lookup(props.get("httpListeners") or gw.get("httpListeners") or [])
    pools_lkp       = _build_lookup(props.get("backendAddressPools") or gw.get("backendAddressPools") or [])
    http_cfg_lkp    = _build_lookup(props.get("backendHttpSettingsCollection") or gw.get("backendHttpSettingsCollection") or [])
    url_maps_lkp    = _build_lookup(props.get("urlPathMaps") or gw.get("urlPathMaps") or [])
    frontend_ports  = {
        _id_tail(fp.get("id")): ((fp.get("properties") or fp).get("port"))
        for fp in (props.get("frontendPorts") or gw.get("frontendPorts") or [])
    }
    frontend_exposure_lookup: dict[str, str] = {}
    for fip in (props.get("frontendIPConfigurations") or gw.get("frontendIPConfigurations") or []):
        fip_props = fip.get("properties") or fip
        exposure_level = "Public" if fip_props.get("publicIPAddress") else "Internal"
        fip_id = _id_tail(fip.get("id"))
        fip_name = fip.get("name") or fip_id
        if fip_id:
            frontend_exposure_lookup[fip_id] = exposure_level
        if fip_name:
            frontend_exposure_lookup[fip_name] = exposure_level

    def _pool_fqdns(pool_name: str) -> list[str]:
        pool = pools_lkp.get(pool_name) or {}
        pp = pool.get("properties") or pool
        return [
            (a.get("fqdn") or a.get("ipAddress") or "?")
            for a in (pp.get("backendAddresses") or [])
        ]

    def _http_cfg_detail(cfg_name: str) -> dict:
        cfg = http_cfg_lkp.get(cfg_name) or {}
        cp = cfg.get("properties") or cfg
        return {
            "port": cp.get("port"),
            "protocol": cp.get("protocol"),
            "host_override": cp.get("hostName") or (
                "(pick-from-backend)" if cp.get("pickHostNameFromBackendAddress") else None
            ),
        }

    def _listener_detail(listener_name: str) -> dict:
        l = listeners_lkp.get(listener_name) or {}
        lp = l.get("properties") or l
        port_id = _id_tail((lp.get("frontendPort") or {}).get("id"))
        hosts = lp.get("hostNames") or ([lp["hostName"]] if lp.get("hostName") else [])
        listener_frontend_id = _id_tail((lp.get("frontendIPConfiguration") or {}).get("id"))
        return {
            "protocol": lp.get("protocol"),
            "port": frontend_ports.get(port_id),
            "hostnames": hosts,
            "waf_policy": _id_tail((lp.get("firewallPolicy") or {}).get("id")),
            "exposure_level": frontend_exposure_lookup.get(listener_frontend_id, "Public"),
        }

    routes: list[dict] = []
    if routing_rules is None:
        route_source = (gw.get("requestRoutingRules") or props.get("requestRoutingRules") or []) + (gw.get("routingRules") or props.get("routingRules") or [])
    else:
        route_source = routing_rules

    seen_rule_names: set[str] = set()
    for rule in route_source:
        rule_name = rule.get("name") or _id_tail(rule.get("id"))
        if not rule_name or rule_name in seen_rule_names:
            continue
        seen_rule_names.add(rule_name)

        rp = rule.get("properties") or rule
        listener_name = _id_tail((rp.get("httpListener") or {}).get("id"))
        listener     = _listener_detail(listener_name)
        hostnames    = listener["hostnames"] or ["*"]

        # Direct pool (Basic routing)
        direct_pool = _id_tail((rp.get("backendAddressPool") or {}).get("id"))
        direct_cfg  = _id_tail((rp.get("backendHttpSettings") or {}).get("id"))
        url_map_name = _id_tail((rp.get("urlPathMap") or {}).get("id"))
        rule_waf    = _id_tail((rp.get("firewallPolicy") or {}).get("id")) or listener["waf_policy"]

        if direct_pool:
            cfg_detail = _http_cfg_detail(direct_cfg)
            for hostname in hostnames:
                routes.append({
                    "rule_name": rule_name,
                    "listener_name": listener_name,
                    "hostname": hostname,
                    "protocol": listener["protocol"],
                    "url_path": normalize_route_path("/*") or "/*",
                    "backend_pool_name": direct_pool,
                    "backend_fqdns": _pool_fqdns(direct_pool),
                    "http_settings_name": direct_cfg,
                    "backend_port": cfg_detail["port"],
                    "backend_protocol": cfg_detail["protocol"],
                    "host_override": cfg_detail["host_override"],
                    "waf_policy_name": rule_waf or None,
                    "exposure_level": listener["exposure_level"],
                })
        elif url_map_name:
            url_map = url_maps_lkp.get(url_map_name) or {}
            if not url_map:
                gw_name = gw.get("name") or props.get("name") or ""
                rg = gw.get("resourceGroup") or props.get("resourceGroup") or ""
                url_map = show_url_path_map(gw_name, rg, url_map_name, subscription_id) or {}
            mp = url_map.get("properties") or url_map

            # Default path
            default_pool = _id_tail((mp.get("defaultBackendAddressPool") or {}).get("id"))
            default_cfg  = _id_tail((mp.get("defaultBackendHttpSettings") or {}).get("id"))
            if default_pool:
                cfg_detail = _http_cfg_detail(default_cfg)
                for hostname in hostnames:
                    routes.append({
                        "rule_name": rule_name,
                        "listener_name": listener_name,
                        "hostname": hostname,
                        "protocol": listener["protocol"],
                        "url_path": normalize_route_path("/*") or "/*",
                        "backend_pool_name": default_pool,
                        "backend_fqdns": _pool_fqdns(default_pool),
                        "http_settings_name": default_cfg,
                        "backend_port": cfg_detail["port"],
                        "backend_protocol": cfg_detail["protocol"],
                        "host_override": cfg_detail["host_override"],
                        "waf_policy_name": rule_waf or None,
                        "exposure_level": listener["exposure_level"],
                    })

            # Path-specific rules
            for path_rule in (mp.get("pathRules") or []):
                prp = path_rule.get("properties") or path_rule
                pool_name = _id_tail((prp.get("backendAddressPool") or {}).get("id"))
                cfg_name  = _id_tail((prp.get("backendHttpSettings") or {}).get("id"))
                path_waf  = _id_tail((prp.get("firewallPolicy") or {}).get("id")) or rule_waf
                paths     = _dedupe_route_paths(prp.get("paths") or ["/*"])
                cfg_detail = _http_cfg_detail(cfg_name)
                for hostname in hostnames:
                    for path in paths:
                        routes.append({
                            "rule_name": f"{rule_name}::{path_rule.get('name','')}",
                            "listener_name": listener_name,
                            "hostname": hostname,
                            "protocol": listener["protocol"],
                            "url_path": path,
                            "backend_pool_name": pool_name,
                            "backend_fqdns": _pool_fqdns(pool_name),
                            "http_settings_name": cfg_name,
                            "backend_port": cfg_detail["port"],
                            "backend_protocol": cfg_detail["protocol"],
                            "host_override": cfg_detail["host_override"],
                            "waf_policy_name": path_waf or None,
                            "exposure_level": listener["exposure_level"],
                        })

    return routes


def _collect_rewrite_rule_links(props: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Collect routing-rule and path-rule references for each rewrite set."""
    links: dict[str, list[dict[str, Any]]] = {}
    path_map_contexts: dict[str, dict[str, Any]] = {}

    for rule in props.get("requestRoutingRules") or []:
        rp = rule.get("properties") or rule
        rule_name = rule.get("name") or _id_tail(rule.get("id"))
        if not rule_name:
            continue

        listener_name = _id_tail((rp.get("httpListener") or {}).get("id")) or None
        url_map_name = _id_tail((rp.get("urlPathMap") or {}).get("id")) or None
        context = {
            "routing_rule_name": rule_name,
            "listener_name": listener_name,
            "rule_type": rp.get("ruleType"),
        }
        if url_map_name:
            path_map_contexts[url_map_name] = context

        set_name = _id_tail((rp.get("rewriteRuleSet") or {}).get("id"))
        if set_name:
            links.setdefault(set_name, []).append({
                "kind": "requestRoutingRule",
                "url_path_map": url_map_name,
                **context,
            })

    for path_map in props.get("urlPathMaps") or []:
        mp = path_map.get("properties") or path_map
        map_name = path_map.get("name") or _id_tail(path_map.get("id"))
        base_context = (path_map_contexts.get(map_name) or {}).copy()

        default_set = _id_tail((mp.get("defaultRewriteRuleSet") or {}).get("id"))
        if default_set:
            links.setdefault(default_set, []).append({
                "kind": "urlPathMapDefault",
                "url_path_map": map_name,
                **base_context,
            })

        for path_rule in (mp.get("pathRules") or []):
            prp = path_rule.get("properties") or path_rule
            set_name = _id_tail((prp.get("rewriteRuleSet") or {}).get("id"))
            if not set_name:
                continue
            route_context = base_context.copy()
            route_context.update({
                "kind": "pathRule",
                "url_path_map": map_name,
                "path_rule_name": path_rule.get("name") or _id_tail(path_rule.get("id")),
                "paths": prp.get("paths") or ["/*"],
            })
            links.setdefault(set_name, []).append(route_context)

    return links


def extract_rewrite_rule_sets(gw: dict) -> list[dict[str, Any]]:
    """Return one row per rewrite rule set with attached routing references."""
    props = gw.get("properties") or gw
    rewrite_sets = props.get("rewriteRuleSets") or gw.get("rewriteRuleSets") or []
    links = _collect_rewrite_rule_links(props)

    rows: list[dict[str, Any]] = []
    for rewrite_set in rewrite_sets:
        set_name = rewrite_set.get("name") or _id_tail(rewrite_set.get("id"))
        if not set_name:
            continue

        set_props = rewrite_set.get("properties") or rewrite_set
        rewrite_rules = []
        for rule in set_props.get("rewriteRules") or []:
            action_set = rule.get("actionSet") or {}
            rewrite_rules.append({
                "name": rule.get("name"),
                "rule_sequence": rule.get("ruleSequence"),
                "conditions": rule.get("conditions") or [],
                "request_header_configurations": action_set.get("requestHeaderConfigurations") or [],
                "response_header_configurations": action_set.get("responseHeaderConfigurations") or [],
                "url_configuration": action_set.get("urlConfiguration") or {},
            })

        rows.append({
            "set_name": set_name,
            "set_id": rewrite_set.get("id"),
            "attached_routes": links.get(set_name, []),
            "attached_route_count": len(links.get(set_name, [])),
            "rewrite_rules": rewrite_rules,
            "rewrite_rule_count": len(rewrite_rules),
            "provisioning_state": set_props.get("provisioningState"),
        })

    return rows


# ---------------------------------------------------------------------------
# Process one gateway
# ---------------------------------------------------------------------------

def process_gateway(
    gw_stub: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    fqdn_to_asset: dict[str, tuple[str, str, str | None]],
    dry_run: bool,
    now: str,
    gw: dict | None = None,
) -> tuple[int, int]:
    name = gw_stub["name"]
    rg   = gw_stub["resourceGroup"]
    print(f"\n  [appgw] {name} (rg={rg})")

    if gw is None:
        print(f"    fetching full config...", end=" ", flush=True)
        gw = show_appgw(name, rg, subscription_id)
        if not gw:
            print("FAILED")
            return 0, 0
    else:
        print(f"    using cached full config...", end=" ", flush=True)

    routes = extract_routes(gw, subscription_id, routing_rules=None)
    print(f"{len(routes)} route entries")

    experiment_id = f"harvest-{subscription_id}"
    gw_resource_id = gw.get("id") or gw_stub.get("id")
    gw_asset_rows  = conn.execute(
        "SELECT type FROM provisioned_assets WHERE subscription_id = ? AND name = ?",
        (subscription_id, name),
    ).fetchall()
    gw_asset_type = gw_asset_rows[0][0] if gw_asset_rows else gw.get("type") or gw_stub.get("type") or "Microsoft.Network/applicationGateways"

    def _lookup_resource_id(resource_name: str, resource_type: str | None = None) -> int | None:
        if not resource_name:
            return None
        if resource_type:
            row = conn.execute(
                """
                SELECT rowid FROM provisioned_assets
                WHERE subscription_id = ? AND name = ? AND type = ?
                LIMIT 1
                """,
                (subscription_id, resource_name, resource_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT rowid FROM provisioned_assets
                WHERE subscription_id = ? AND name = ?
                LIMIT 1
                """,
                (subscription_id, resource_name),
            ).fetchone()
        return row[0] if row else None

    source_resource_id = _lookup_resource_id(name, gw_asset_type)

    rules_upserted = 0
    connections_created = 0

    for route in routes:
        route_path = normalize_route_path(route["url_path"]) or route["url_path"]
        rule_id = f"{name}::{route['rule_name']}::{route_path}"

        if not dry_run:
            conn.execute(
                """
                INSERT INTO appgw_routing_rules
                    (id, subscription_id, gateway_name, gateway_resource_id, resource_group,
                     rule_name, listener_name, hostname, protocol, url_path,
                     backend_pool_name, backend_fqdns, http_settings_name,
                     backend_port, backend_protocol, host_override,
                     waf_policy_name, exposure_level, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    hostname          = excluded.hostname,
                    protocol          = excluded.protocol,
                    backend_pool_name = excluded.backend_pool_name,
                    backend_fqdns     = excluded.backend_fqdns,
                    backend_port      = excluded.backend_port,
                    backend_protocol  = excluded.backend_protocol,
                    host_override     = excluded.host_override,
                    waf_policy_name   = excluded.waf_policy_name,
                    exposure_level    = excluded.exposure_level,
                    last_synced       = excluded.last_synced
                """,
                (
                    rule_id, subscription_id, name, gw_resource_id, rg,
                    route["rule_name"], route["listener_name"],
                    route["hostname"], route["protocol"], route["url_path"],
                    route["backend_pool_name"],
                    json.dumps(route["backend_fqdns"]),
                    route["http_settings_name"],
                    route["backend_port"], route["backend_protocol"],
                    route["host_override"], route["waf_policy_name"],
                    route["exposure_level"],
                    now,
                ),
            )
            rules_upserted += 1

            # resource_connections: AppGW → backend asset
            if source_resource_id is not None:
                for fqdn in route["backend_fqdns"]:
                    target_asset_name: str | None = None
                    target_asset_type: str | None = None
                    backend_key = normalize_host_key(fqdn) or fqdn.lower().rstrip(".")
                    for known_fqdn, (_asset_id, asset_name, asset_type) in fqdn_to_asset.items():
                        if (
                            known_fqdn == backend_key or
                            backend_key.endswith(f".{known_fqdn}") or
                            known_fqdn.endswith(f".{backend_key}")
                        ):
                            target_asset_name = asset_name
                            target_asset_type = asset_type
                            break

                    target_resource_id = _lookup_resource_id(target_asset_name or "", target_asset_type) if target_asset_name else None

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO resource_connections
                            (experiment_id, source_resource_id, target_resource_id, connection_type,
                             target_external, connection_metadata)
                        VALUES (?, ?, ?, 'appgw_routing', ?, ?)
                        """,
                        (
                            experiment_id,
                            source_resource_id,
                            target_resource_id,
                            None if target_resource_id else fqdn,
                            json.dumps({
                                "hostname": route["hostname"],
                                "url_path": route["url_path"],
                                "normalized_url_path": route_path,
                                "backend_pool": route["backend_pool_name"],
                                "backend_fqdn": fqdn,
                                "waf_policy": route["waf_policy_name"],
                                "exposure_level": route["exposure_level"],
                            }),
                        ),
                    )
                    connections_created += 1

    if not dry_run:
        conn.commit()

    print(f"    → {rules_upserted} rules upserted, {connections_created} connections created")
    return rules_upserted, connections_created


def process_rewrite_rule_sets(
    gw_stub: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool,
    now: str,
    gw: dict | None = None,
) -> tuple[int, int]:
    name = gw_stub["name"]
    rg   = gw_stub["resourceGroup"]
    print(f"\n  [appgw-rewrites] {name} (rg={rg})")

    if gw is None:
        print(f"    fetching full config...", end=" ", flush=True)
        gw = show_appgw(name, rg, subscription_id)
        if not gw:
            print("FAILED")
            return 0, 0
    else:
        print(f"    using cached full config...", end=" ", flush=True)

    rewrite_sets = extract_rewrite_rule_sets(gw)
    print(f"{len(rewrite_sets)} rewrite rule set entries")

    gw_resource_id = gw.get("id") or gw_stub.get("id")
    set_count = 0
    rule_count = 0

    if not dry_run:
        conn.execute(
            "DELETE FROM appgw_rewrite_rule_sets WHERE subscription_id = ? AND gateway_name = ?",
            (subscription_id, name),
        )
        for rewrite_set in rewrite_sets:
            set_id = f"{name}::{rewrite_set['set_name']}"
            conn.execute(
                """
                INSERT INTO appgw_rewrite_rule_sets
                    (id, subscription_id, gateway_name, gateway_resource_id, resource_group,
                     set_name, attached_routes, attached_route_count,
                     rewrite_rules, rewrite_rule_count, provisioning_state, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    gateway_resource_id = excluded.gateway_resource_id,
                    resource_group       = excluded.resource_group,
                    attached_routes      = excluded.attached_routes,
                    attached_route_count = excluded.attached_route_count,
                    rewrite_rules        = excluded.rewrite_rules,
                    rewrite_rule_count   = excluded.rewrite_rule_count,
                    provisioning_state   = excluded.provisioning_state,
                    last_synced          = excluded.last_synced
                """,
                (
                    set_id, subscription_id, name, gw_resource_id, rg,
                    rewrite_set["set_name"],
                    json.dumps(rewrite_set["attached_routes"]),
                    rewrite_set["attached_route_count"],
                    json.dumps(rewrite_set["rewrite_rules"]),
                    rewrite_set["rewrite_rule_count"],
                    rewrite_set["provisioning_state"],
                    now,
                ),
            )
            set_count += 1
            rule_count += rewrite_set["rewrite_rule_count"]
        conn.commit()
    else:
        set_count = len(rewrite_sets)
        rule_count = sum(rewrite_set["rewrite_rule_count"] for rewrite_set in rewrite_sets)

    print(f"    → {set_count} rewrite rule sets upserted, {rule_count} rewrite rules recorded")
    return set_count, rule_count


# ---------------------------------------------------------------------------
# Process WAF policies
# ---------------------------------------------------------------------------

def process_waf_policies(
    subscription_id: str,
    gateways: list[dict],
    conn: sqlite3.Connection,
    dry_run: bool,
    now: str,
) -> int:
    print(f"\n  [waf] Harvesting WAF policies...")

    # Build gateway_name → waf_policy_name map
    gw_waf_map: dict[str, list[str]] = {}
    for gw in gateways:
        props = gw.get("properties") or gw
        waf_pol_id = (props.get("firewallPolicy") or {}).get("id", "")
        pol_name = waf_pol_id.split("/")[-1] if waf_pol_id else None
        if pol_name:
            gw_waf_map.setdefault(pol_name, []).append(gw["name"])

    policies = list_waf_policies(subscription_id)
    print(f"    found {len(policies)} WAF policies", end=" ", flush=True)

    count = 0
    unconfigured = []
    policy_results: list[tuple[dict, dict]] = []
    if policies:
        max_workers = min(_APPGW_FETCH_WORKERS, len(policies))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(show_waf_policy, pol_stub["name"], pol_stub["resourceGroup"], subscription_id): pol_stub
                for pol_stub in policies
            }
            for future in as_completed(futures):
                pol_stub = futures[future]
                try:
                    pol = future.result() or pol_stub
                except Exception:
                    pol = pol_stub
                policy_results.append((pol_stub, pol))

    for pol_stub, pol in policy_results:
        pol_name = pol_stub["name"]
        pol_rg   = pol_stub["resourceGroup"]
        pol_id   = pol_stub.get("id", "")
        pp  = pol.get("properties") or {}
        ps  = pp.get("policySettings") or {}
        managed = pp.get("managedRules") or {}
        rule_sets = managed.get("managedRuleSets") or []
        exclusions = managed.get("exclusions") or []
        custom_rules = pp.get("customRules") or []

        mode  = ps.get("mode")
        state = ps.get("state")

        if not rule_sets or mode is None:
            unconfigured.append(pol_name)

        associated = gw_waf_map.get(pol_name, [])

        if not dry_run:
            conn.execute(
                """
                INSERT INTO appgw_waf_policies
                    (id, subscription_id, name, resource_group, mode, state,
                     request_body_check, max_body_kb, managed_rule_sets,
                     custom_rules_count, exclusions_count,
                     associated_gateways, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    mode                = excluded.mode,
                    state               = excluded.state,
                    request_body_check  = excluded.request_body_check,
                    max_body_kb         = excluded.max_body_kb,
                    managed_rule_sets   = excluded.managed_rule_sets,
                    custom_rules_count  = excluded.custom_rules_count,
                    exclusions_count    = excluded.exclusions_count,
                    associated_gateways = excluded.associated_gateways,
                    last_synced         = excluded.last_synced
                """,
                (
                    pol_id, subscription_id, pol_name, pol_rg,
                    mode, state,
                    1 if ps.get("requestBodyCheck") else 0,
                    ps.get("maxRequestBodySizeInKb"),
                    json.dumps([{"type": rs.get("ruleSetType"), "version": rs.get("ruleSetVersion")}
                                for rs in rule_sets]),
                    len(custom_rules), len(exclusions),
                    json.dumps(associated),
                    now,
                ),
            )
            count += 1

    if not dry_run:
        conn.commit()

    print(f"— {count} persisted")
    if unconfigured:
        print(f"\n  ⚠  {len(unconfigured)} WAF policy/policies have no managed rule sets or mode configured:")
        for n in unconfigured:
            associated = gw_waf_map.get(n, [])
            assoc_str = f" (used by: {', '.join(associated)})" if associated else ""
            print(f"    - {n}{assoc_str}")
        print("  → These WAF policies are NOT enforcing any rules. Recommend setting mode=Prevention and adding OWASP rule set.")

    return count


# ---------------------------------------------------------------------------
# Public harvest entry point
# ---------------------------------------------------------------------------

def harvest_routing(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> tuple[int, int, int, int]:
    """Harvest App Gateway listener→backend routing rules, rewrites, and WAF policies.

    Returns (routing_rules, rewrite_rule_sets, rewrite_rules, waf_policies).
    """
    _ensure_appgw_schema(conn)

    gateways = list_appgw(subscription_id)
    if not gateways:
        print("  No Application Gateways found — skipping")
        return 0, 0, 0, 0

    total_rules = 0
    total_rewrite_sets = 0
    total_rewrite_rules = 0
    total_waf = 0
    now = datetime.now(timezone.utc).isoformat()

    # Build FQDN → asset map for cross-referencing backend pools.
    fqdn_to_asset: dict[str, tuple[str, str, str | None]] = {
        (normalize_host_key(row[3]) or row[3].lower().rstrip(".")): (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT id, name, type, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
            (subscription_id,),
        ).fetchall()
    }

    full_gateways = []
    gateway_results: list[tuple[dict, dict]] = []
    max_workers = min(_APPGW_FETCH_WORKERS, len(gateways))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for gw_stub in gateways:
            name = gw_stub["name"]
            rg = gw_stub.get("resourceGroup", "")
            print(f"    [appgw-routing] {name}...", end=" ", flush=True)
            futures[pool.submit(show_appgw, name, rg, subscription_id)] = gw_stub

        for future in as_completed(futures):
            gw_stub = futures[future]
            try:
                gw = future.result()
            except Exception:
                gw = None
            if not gw:
                print("FAILED (show returned nothing)")
                continue
            gateway_results.append((gw_stub, gw))

    for gw_stub, gw in gateway_results:
        full_gateways.append(gw)
        rules, _connections = process_gateway(
            gw_stub, subscription_id, conn, fqdn_to_asset, dry_run, now, gw=gw
        )
        total_rules += rules
        rewrite_sets, rewrite_rules = process_rewrite_rule_sets(
            gw_stub, subscription_id, conn, dry_run, now, gw=gw
        )
        total_rewrite_sets += rewrite_sets
        total_rewrite_rules += rewrite_rules

    if total_rules == 0:
        print("  [warn] no App Gateway routing rows were harvested; check routingRules/requestRoutingRules coverage")

    total_waf = process_waf_policies(subscription_id, full_gateways, conn, dry_run, now)
    return total_rules, total_rewrite_sets, total_rewrite_rules, total_waf


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build App Gateway routing, rewrite, and WAF maps from a live Azure subscription"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID")
    group.add_argument("--all", action="store_true", dest="all_subs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    subs_raw = subprocess.run(
        ["az", "account", "list", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )
    all_subs: list[dict] = json.loads(subs_raw.stdout or "[]")

    if args.all_subs:
        target_subs = all_subs
    else:
        needle = args.subscription.lower()
        target_subs = [
            s for s in all_subs
            if s.get("id", "").lower() == needle or s.get("name", "").lower() == needle
        ]
        if not target_subs:
            names = [s.get("name") for s in all_subs]
            print(f"[error] Subscription '{args.subscription}' not found. Available: {names}", file=sys.stderr)
            sys.exit(1)

    db_path = REPO_ROOT / "Output" / "Data" / "cozo.db"
    if not db_path.exists():
        print(f"[error] cozo.db not found at {db_path}. Run harvest_azure_assets.py first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)
    _ensure_appgw_schema(conn)

    total_rules = 0
    total_rewrite_sets = 0
    total_rewrite_rules = 0
    total_waf = 0

    for sub in target_subs:
        sub_id   = sub["id"]
        sub_name = sub.get("name") or sub_id
        print(f"\n[subscription] {sub_name}")
        rules, rewrite_sets, rewrite_rules, waf = harvest_routing(sub_id, conn, dry_run=args.dry_run)
        total_rules += rules
        total_rewrite_sets += rewrite_sets
        total_rewrite_rules += rewrite_rules
        total_waf += waf

    conn.close()
    print(
        f"\n[appgw-routing] Done. {total_rules} routing rules, "
        f"{total_rewrite_sets} rewrite rule sets ({total_rewrite_rules} rewrite rules), "
        f"{total_waf} WAF policies across {len(target_subs)} subscription(s)."
    )


if __name__ == "__main__":
    main()
