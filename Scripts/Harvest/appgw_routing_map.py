#!/usr/bin/env python3
"""Build a full App Gateway → backend routing map and persist it to cozo.db.

For each Application Gateway in the subscription this script:
  1. Calls `az network application-gateway show` per gateway to get full nested properties
  2. Builds the chain: public hostname (listener) → routing rule → URL path map → backend pool → backend FQDNs
  3. Captures WAF policy references and mode/state for each listener/path rule
  4. Cross-references backend pool addresses with provisioned_assets.fqdn
     to create resource_connections rows (type='appgw_routing')
  5. Stores the routing map in appgw_routing_rules table
  6. Stores WAF policy summary in appgw_waf_policies table

Usage:
    python Scripts/Harvest/appgw_routing_map.py --subscription "pipeline-customer-production"
    python Scripts/Harvest/appgw_routing_map.py --all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore

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
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_sub     ON appgw_routing_rules(subscription_id);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_gateway ON appgw_routing_rules(gateway_name);
CREATE INDEX IF NOT EXISTS idx_appgw_rules_host    ON appgw_routing_rules(hostname);

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


def _ensure_appgw_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_APPGW_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------

def _az(*args: str, subscription_id: str, timeout: int = 120) -> Any:
    cmd = ["az", *args, "--subscription", subscription_id, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        print(f"    [warn] az {' '.join(args[:4])} failed: {result.stderr.strip()[:120]}")
        return None
    try:
        return json.loads(result.stdout or "null")
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


# ---------------------------------------------------------------------------
# Core: extract routing chains from a fully-hydrated AppGW
# ---------------------------------------------------------------------------

def extract_routes(gw: dict) -> list[dict]:
    """
    Return a flat list of route dicts, one per (listener × path_rule).
    Each dict has: rule_name, listener_name, hostname, protocol, url_path,
    backend_pool_name, backend_fqdns, http_settings_name, backend_port,
    backend_protocol, host_override, waf_policy_name.
    """
    props = gw.get("properties") or gw

    # Build lookups by resource name (last segment of id)
    listeners_lkp   = _build_lookup(props.get("httpListeners") or [])
    pools_lkp       = _build_lookup(props.get("backendAddressPools") or [])
    http_cfg_lkp    = _build_lookup(props.get("backendHttpSettingsCollection") or [])
    url_maps_lkp    = _build_lookup(props.get("urlPathMaps") or [])
    frontend_ports  = {
        _id_tail(fp.get("id")): (fp.get("properties") or {}).get("port")
        for fp in (props.get("frontendPorts") or [])
    }

    def _pool_fqdns(pool_name: str) -> list[str]:
        pool = pools_lkp.get(pool_name) or {}
        pp = pool.get("properties") or {}
        return [
            (a.get("fqdn") or a.get("ipAddress") or "?")
            for a in (pp.get("backendAddresses") or [])
        ]

    def _http_cfg_detail(cfg_name: str) -> dict:
        cfg = http_cfg_lkp.get(cfg_name) or {}
        cp = cfg.get("properties") or {}
        return {
            "port": cp.get("port"),
            "protocol": cp.get("protocol"),
            "host_override": cp.get("hostName") or (
                "(pick-from-backend)" if cp.get("pickHostNameFromBackendAddress") else None
            ),
        }

    def _listener_detail(listener_name: str) -> dict:
        l = listeners_lkp.get(listener_name) or {}
        lp = l.get("properties") or {}
        port_id = _id_tail((lp.get("frontendPort") or {}).get("id"))
        hosts = lp.get("hostNames") or ([lp["hostName"]] if lp.get("hostName") else [])
        return {
            "protocol": lp.get("protocol"),
            "port": frontend_ports.get(port_id),
            "hostnames": hosts,
            "waf_policy": _id_tail((lp.get("firewallPolicy") or {}).get("id")),
        }

    routes: list[dict] = []

    for rule in (props.get("requestRoutingRules") or []):
        rp = rule.get("properties") or {}
        rule_name    = rule.get("name", "")
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
                    "url_path": "/*",
                    "backend_pool_name": direct_pool,
                    "backend_fqdns": _pool_fqdns(direct_pool),
                    "http_settings_name": direct_cfg,
                    "backend_port": cfg_detail["port"],
                    "backend_protocol": cfg_detail["protocol"],
                    "host_override": cfg_detail["host_override"],
                    "waf_policy_name": rule_waf or None,
                })
        elif url_map_name:
            url_map = url_maps_lkp.get(url_map_name) or {}
            mp = url_map.get("properties") or {}

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
                        "url_path": "/*",
                        "backend_pool_name": default_pool,
                        "backend_fqdns": _pool_fqdns(default_pool),
                        "http_settings_name": default_cfg,
                        "backend_port": cfg_detail["port"],
                        "backend_protocol": cfg_detail["protocol"],
                        "host_override": cfg_detail["host_override"],
                        "waf_policy_name": rule_waf or None,
                    })

            # Path-specific rules
            for path_rule in (mp.get("pathRules") or []):
                prp = path_rule.get("properties") or {}
                pool_name = _id_tail((prp.get("backendAddressPool") or {}).get("id"))
                cfg_name  = _id_tail((prp.get("backendHttpSettings") or {}).get("id"))
                path_waf  = _id_tail((prp.get("firewallPolicy") or {}).get("id")) or rule_waf
                paths     = prp.get("paths") or ["/*"]
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
                        })

    return routes


# ---------------------------------------------------------------------------
# Process one gateway
# ---------------------------------------------------------------------------

def process_gateway(
    gw_stub: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    fqdn_to_asset: dict[str, tuple[str, str]],
    dry_run: bool,
    now: str,
) -> tuple[int, int]:
    name = gw_stub["name"]
    rg   = gw_stub["resourceGroup"]
    print(f"\n  [appgw] {name} (rg={rg})")

    print(f"    fetching full config...", end=" ", flush=True)
    gw = show_appgw(name, rg, subscription_id)
    if not gw:
        print("FAILED")
        return 0, 0

    routes = extract_routes(gw)
    print(f"{len(routes)} route entries")

    gw_resource_id = gw.get("id") or gw_stub.get("id")
    gw_asset_rows  = conn.execute(
        "SELECT id FROM provisioned_assets WHERE subscription_id = ? AND name = ?",
        (subscription_id, name),
    ).fetchall()
    gw_asset_id = gw_asset_rows[0][0] if gw_asset_rows else gw_resource_id

    rules_upserted = 0
    connections_created = 0

    for route in routes:
        rule_id = f"{name}::{route['rule_name']}::{route['url_path']}"

        if not dry_run:
            conn.execute(
                """
                INSERT INTO appgw_routing_rules
                    (id, subscription_id, gateway_name, gateway_resource_id, resource_group,
                     rule_name, listener_name, hostname, protocol, url_path,
                     backend_pool_name, backend_fqdns, http_settings_name,
                     backend_port, backend_protocol, host_override,
                     waf_policy_name, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    hostname          = excluded.hostname,
                    protocol          = excluded.protocol,
                    backend_pool_name = excluded.backend_pool_name,
                    backend_fqdns     = excluded.backend_fqdns,
                    backend_port      = excluded.backend_port,
                    backend_protocol  = excluded.backend_protocol,
                    host_override     = excluded.host_override,
                    waf_policy_name   = excluded.waf_policy_name,
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
                    now,
                ),
            )
            rules_upserted += 1

            # resource_connections: AppGW → backend asset
            for fqdn in route["backend_fqdns"]:
                target_asset_id = None
                for known_fqdn, (asset_id, _) in fqdn_to_asset.items():
                    if known_fqdn == fqdn or fqdn.endswith(f".{known_fqdn}") or known_fqdn.endswith(f".{fqdn}"):
                        target_asset_id = asset_id
                        break

                conn.execute(
                    """
                    INSERT OR REPLACE INTO resource_connections
                        (source_id, target_id, connection_type, metadata)
                    VALUES (?, ?, 'appgw_routing', ?)
                    """,
                    (
                        gw_asset_id,
                        target_asset_id or f"fqdn:{fqdn}",
                        json.dumps({
                            "hostname": route["hostname"],
                            "url_path": route["url_path"],
                            "backend_pool": route["backend_pool_name"],
                            "backend_fqdn": fqdn,
                            "waf_policy": route["waf_policy_name"],
                        }),
                    ),
                )
                connections_created += 1

    if not dry_run:
        conn.commit()

    print(f"    → {rules_upserted} rules upserted, {connections_created} connections created")
    return rules_upserted, connections_created


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
    for pol_stub in policies:
        pol_name = pol_stub["name"]
        pol_rg   = pol_stub["resourceGroup"]
        pol_id   = pol_stub.get("id", "")

        # Get full policy details
        pol = show_waf_policy(pol_name, pol_rg, subscription_id) or pol_stub
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build App Gateway + WAF routing map from live Azure subscription"
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
    total_connections = 0
    now = datetime.now(timezone.utc).isoformat()

    for sub in target_subs:
        sub_id   = sub["id"]
        sub_name = sub.get("name") or sub_id
        print(f"\n[subscription] {sub_name}")

        gateways = list_appgw(sub_id)
        if not gateways:
            print("  No Application Gateways found — skipping")
            continue

        # Load FQDN → asset map for cross-referencing
        fqdn_to_asset: dict[str, tuple[str, str]] = {
            row[2]: (row[0], row[1])
            for row in conn.execute(
                "SELECT id, name, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
                (sub_id,),
            ).fetchall()
        }

        # Fetch full gateway configs and process routing
        full_gateways = []
        for gw_stub in gateways:
            full_gw = show_appgw(gw_stub["name"], gw_stub["resourceGroup"], sub_id)
            if full_gw:
                full_gateways.append(full_gw)
                rules, conns = process_gateway(
                    gw_stub, sub_id, conn, fqdn_to_asset, args.dry_run, now
                )
                total_rules += rules
                total_connections += conns

        # Process WAF policies
        process_waf_policies(sub_id, full_gateways, conn, args.dry_run, now)

    conn.close()
    print(f"\n[appgw-routing] Done. {total_rules} routing rules, {total_connections} connections across {len(target_subs)} subscription(s).")


if __name__ == "__main__":
    main()
