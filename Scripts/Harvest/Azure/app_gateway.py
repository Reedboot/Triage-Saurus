"""Harvest Azure Application Gateways and WAF policies."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Network/applicationGateways"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "application-gateway", "list"], subscription_id)
    results = []

    for gw in raw:
        props = gw.get("properties") or {}
        fqdn = _get_frontend_fqdn(props)
        waf_mode = _get_waf_mode(gw, subscription_id)
        is_public = _has_public_frontend(props)

        endpoint_entries = _get_endpoint_entries(props)
        endpoints = build_endpoints(endpoint_entries)
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "listener_count": len(props.get("httpListeners") or []),
            "backend_pool_count": len(props.get("backendAddressPools") or []),
            "waf_mode": waf_mode,
        }

        results.append({
            "id": gw["id"],
            "subscription_id": subscription_id,
            "resource_group": gw.get("resourceGroup"),
            "name": gw.get("name"),
            "type": gw.get("type", RESOURCE_TYPE),
            "location": gw.get("location"),
            "sku": infer_sku(gw),
            "tags": json.dumps(gw.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": 0,  # App Gateways use NSGs/WAF policies externally; no inline IP restriction
            "ip_restrictions": json.dumps([]),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**gw, "_extra": extra}),
        })

    return results


def _get_frontend_fqdn(props: dict[str, Any]) -> str | None:
    """Extract the first public frontend IP DNS name from App Gateway properties."""
    for fip in props.get("frontendIPConfigurations") or []:
        fip_props = fip.get("properties") or {}
        pip = fip_props.get("publicIPAddress")
        if pip:
            pip_props = pip.get("properties") or {}
            dns = pip_props.get("dnsSettings") or {}
            fqdn = dns.get("fqdn")
            if fqdn:
                return safe_str(fqdn)
    return None


def _has_public_frontend(props: dict[str, Any]) -> int:
    for fip in props.get("frontendIPConfigurations") or []:
        fip_props = fip.get("properties") or {}
        if fip_props.get("publicIPAddress"):
            return 1
    return 0


def _get_endpoint_entries(props: dict[str, Any]) -> list[tuple[str | None, int, str]]:
    """Build endpoint entries from frontend IP + listener port/protocol combinations."""
    # Collect public frontend IP FQDNs and IPs
    frontend_addresses: list[str] = []
    for fip in props.get("frontendIPConfigurations") or []:
        fip_props = fip.get("properties") or {}
        pip = fip_props.get("publicIPAddress")
        if pip:
            pip_props = pip.get("properties") or {}
            dns = pip_props.get("dnsSettings") or {}
            fqdn = dns.get("fqdn") or pip_props.get("ipAddress")
            if fqdn:
                frontend_addresses.append(fqdn)

    if not frontend_addresses:
        return []

    entries: list[tuple[str | None, int, str]] = []
    for listener in props.get("httpListeners") or []:
        l_props = listener.get("properties") or {}
        protocol = (l_props.get("protocol") or "Http").lower()
        # Resolve port from frontend port reference
        fp_ref = l_props.get("frontendPort") or {}
        fp_id = fp_ref.get("id") or ""
        port = _resolve_frontend_port(props, fp_id)
        for addr in frontend_addresses:
            entries.append((addr, port, protocol))

    # Fallback: if no listeners parsed, add default ports
    if not entries:
        for addr in frontend_addresses:
            entries.append((addr, 443, "https"))

    return entries


def _resolve_frontend_port(props: dict[str, Any], port_id: str) -> int:
    for fp in props.get("frontendPorts") or []:
        if fp.get("id") == port_id or fp.get("id", "").endswith(f"/{port_id.split('/')[-1]}"):
            fp_props = fp.get("properties") or {}
            port = fp_props.get("port")
            if port:
                return int(port)
    return 443  # default


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    for listener in props.get("httpListeners") or []:
        l_props = listener.get("properties") or {}
        protocol = (l_props.get("protocol") or "").lower()
        if protocol == "https":
            methods.append("tls_termination")
        if l_props.get("requireServerNameIndication"):
            if "sni" not in methods:
                methods.append("sni")
        if l_props.get("sslCertificate"):
            pass  # TLS cert present (client → gateway), already captured
        # Mutual TLS: check for clientAuthConfiguration
        if l_props.get("clientAuthConfiguration"):
            if "mutual_tls" not in methods:
                methods.append("mutual_tls")
    return list(dict.fromkeys(methods)) or ["none"]


def _get_waf_mode(gw: dict[str, Any], subscription_id: str) -> str | None:
    """Read WAF mode from the gateway SKU properties (v2 inline) or WAF policy."""
    props = gw.get("properties") or {}
    sku = gw.get("sku") or {}
    sku_name = (sku.get("name") or "").upper()

    if "WAF" in sku_name:
        waf_config = props.get("webApplicationFirewallConfiguration") or {}
        mode = waf_config.get("firewallMode")
        if mode:
            return mode

        # WAF v2 uses a policy reference
        policy_ref = props.get("firewallPolicy")
        if policy_ref:
            return "PolicyAttached"

    return None


def get_backend_fqdns(subscription_id: str) -> dict[str, str]:
    """Build an FQDN→gateway-name index for correlation.

    Returns {backend_fqdn: gateway_name} for all app gateways in the subscription.
    Used by correlate_assets.py to mark downstream resources as gateway-fronted.
    """
    raw = az(["network", "application-gateway", "list"], subscription_id)
    index: dict[str, str] = {}
    for gw in raw:
        props = gw.get("properties") or {}
        gw_name = gw.get("name", "")
        for pool in props.get("backendAddressPools") or []:
            pool_props = pool.get("properties") or {}
            for addr in pool_props.get("backendAddresses") or []:
                fqdn = addr.get("fqdn") or addr.get("ipAddress")
                if fqdn:
                    index[fqdn.lower()] = gw_name
    return index


# ---------------------------------------------------------------------------
# Routing + WAF harvest (runs as part of the main harvest pipeline)
# ---------------------------------------------------------------------------

def _az_show(args: list[str], subscription_id: str, timeout: int = 120) -> dict | None:
    """Run an az show command and return parsed JSON, or None on failure."""
    cmd = ["az"] + args + ["--subscription", subscription_id, "--output", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "null")
    except Exception:
        return None


def _id_tail(resource_id: str | None) -> str:
    """Return the last path segment of an ARM resource ID."""
    return (resource_id or "").rstrip("/").split("/")[-1]


def _build_lookup(items: list[dict], key: str = "name") -> dict[str, dict]:
    return {item[key]: item for item in items if item.get(key)}


def extract_routes(gw: dict) -> list[dict]:
    """Return a flat list of route dicts, one per (listener × path_rule).

    Handles both nested ARM REST shape (properties wrapper) and the flat
    az CLI show shape.
    """
    props = gw.get("properties") or gw

    listeners_lkp  = _build_lookup(props.get("httpListeners") or [])
    pools_lkp      = _build_lookup(props.get("backendAddressPools") or [])
    http_cfg_lkp   = _build_lookup(props.get("backendHttpSettingsCollection") or [])
    url_maps_lkp   = _build_lookup(props.get("urlPathMaps") or [])
    frontend_ports = {
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
        rule_name     = rule.get("name", "")
        listener_name = _id_tail((rp.get("httpListener") or {}).get("id"))
        listener      = _listener_detail(listener_name)
        hostnames     = listener["hostnames"] or ["*"]
        direct_pool   = _id_tail((rp.get("backendAddressPool") or {}).get("id"))
        direct_cfg    = _id_tail((rp.get("backendHttpSettings") or {}).get("id"))
        url_map_name  = _id_tail((rp.get("urlPathMap") or {}).get("id"))
        rule_waf      = _id_tail((rp.get("firewallPolicy") or {}).get("id")) or listener["waf_policy"]

        if direct_pool:
            cfg = _http_cfg_detail(direct_cfg)
            for hostname in hostnames:
                routes.append({
                    "rule_name": rule_name, "listener_name": listener_name,
                    "hostname": hostname, "protocol": listener["protocol"],
                    "url_path": "/*", "backend_pool_name": direct_pool,
                    "backend_fqdns": _pool_fqdns(direct_pool),
                    "http_settings_name": direct_cfg,
                    "backend_port": cfg["port"], "backend_protocol": cfg["protocol"],
                    "host_override": cfg["host_override"], "waf_policy_name": rule_waf or None,
                })
        elif url_map_name:
            url_map = url_maps_lkp.get(url_map_name) or {}
            mp = url_map.get("properties") or {}
            default_pool = _id_tail((mp.get("defaultBackendAddressPool") or {}).get("id"))
            default_cfg  = _id_tail((mp.get("defaultBackendHttpSettings") or {}).get("id"))
            if default_pool:
                cfg = _http_cfg_detail(default_cfg)
                for hostname in hostnames:
                    routes.append({
                        "rule_name": rule_name, "listener_name": listener_name,
                        "hostname": hostname, "protocol": listener["protocol"],
                        "url_path": "/*", "backend_pool_name": default_pool,
                        "backend_fqdns": _pool_fqdns(default_pool),
                        "http_settings_name": default_cfg,
                        "backend_port": cfg["port"], "backend_protocol": cfg["protocol"],
                        "host_override": cfg["host_override"], "waf_policy_name": rule_waf or None,
                    })
            for path_rule in (mp.get("pathRules") or []):
                prp = path_rule.get("properties") or {}
                pool_name  = _id_tail((prp.get("backendAddressPool") or {}).get("id"))
                cfg_name   = _id_tail((prp.get("backendHttpSettings") or {}).get("id"))
                path_waf   = _id_tail((prp.get("firewallPolicy") or {}).get("id")) or rule_waf
                paths      = prp.get("paths") or ["/*"]
                cfg        = _http_cfg_detail(cfg_name)
                for hostname in hostnames:
                    for path in paths:
                        routes.append({
                            "rule_name": f"{rule_name}::{path_rule.get('name', '')}",
                            "listener_name": listener_name,
                            "hostname": hostname, "protocol": listener["protocol"],
                            "url_path": path, "backend_pool_name": pool_name,
                            "backend_fqdns": _pool_fqdns(pool_name),
                            "http_settings_name": cfg_name,
                            "backend_port": cfg["port"], "backend_protocol": cfg["protocol"],
                            "host_override": cfg["host_override"], "waf_policy_name": path_waf or None,
                        })

    return routes


def harvest_routing(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Harvest App Gateway listener→backend routing rules and WAF policies.

    Called automatically by the main harvest script after provisioned_assets
    are written so the fqdn_to_asset lookup is up-to-date.

    Returns (rules_upserted, waf_policies_upserted).
    """
    now = datetime.now(timezone.utc).isoformat()

    gateways = az(["network", "application-gateway", "list"], subscription_id)
    if not gateways:
        return 0, 0

    # Build FQDN → asset_id map from already-written provisioned_assets
    fqdn_to_asset: dict[str, str] = {}
    for row in conn.execute(
        "SELECT id, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
        (subscription_id,),
    ).fetchall():
        fqdn_to_asset[row[1].lower().rstrip(".")] = row[0]

    total_rules = 0
    total_waf   = 0

    for gw_stub in gateways:
        name = gw_stub["name"]
        rg   = gw_stub.get("resourceGroup", "")
        print(f"    [appgw-routing] {name}...", end=" ", flush=True)

        gw = _az_show(
            ["network", "application-gateway", "show", "--name", name, "--resource-group", rg],
            subscription_id,
        )
        if not gw:
            print("FAILED (show returned nothing)")
            continue

        routes = extract_routes(gw)
        print(f"{len(routes)} routes", end="")

        if not dry_run and routes:
            # Remove stale rows for this gateway before repopulating
            conn.execute(
                "DELETE FROM appgw_routing_rules WHERE subscription_id = ? AND gateway_name = ?",
                (subscription_id, name),
            )
            gw_resource_id = gw.get("id") or gw_stub.get("id")
            for route in routes:
                rule_id = f"{name}::{route['rule_name']}::{route['url_path']}"
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
            conn.commit()
            total_rules += len(routes)
        elif dry_run:
            total_rules += len(routes)

        print()

    # WAF policies
    waf_policies = az(["network", "application-gateway", "waf-policy", "list"], subscription_id)
    gw_waf_map: dict[str, list[str]] = {}
    for gw in gateways:
        gw_name = gw["name"]
        props = gw.get("properties") or gw

        # Gateway-level WAF policy association
        pol_id = (props.get("firewallPolicy") or {}).get("id", "")
        pol_name = pol_id.split("/")[-1] if pol_id else None
        if pol_name:
            gw_waf_map.setdefault(pol_name, []).append(gw_name)

        # Per-listener WAF policy associations (per-listener policies are NOT linked
        # at the gateway level, so they would otherwise have associated_gateways=[]).
        for listener in props.get("httpListeners") or []:
            lp = listener.get("properties") or {}
            l_pol_id = (lp.get("firewallPolicy") or {}).get("id", "")
            l_pol_name = l_pol_id.split("/")[-1] if l_pol_id else None
            if l_pol_name and l_pol_name != pol_name:
                gw_entry = gw_waf_map.setdefault(l_pol_name, [])
                if gw_name not in gw_entry:
                    gw_entry.append(gw_name)

    for pol_stub in waf_policies:
        pol_name = pol_stub["name"]
        pol_rg   = pol_stub.get("resourceGroup", "")
        pol_id   = pol_stub.get("id", "")

        pol = _az_show(
            ["network", "application-gateway", "waf-policy", "show",
             "--name", pol_name, "--resource-group", pol_rg],
            subscription_id,
        ) or pol_stub
        pp  = pol.get("properties") or {}
        # If the show command fell back to pol_stub (from the list command), try its
        # properties too — the list output usually includes policySettings.mode/state.
        if not pp.get("policySettings"):
            pp = pol_stub.get("properties") or pp
        ps  = pp.get("policySettings") or {}
        managed      = pp.get("managedRules") or {}
        rule_sets    = managed.get("managedRuleSets") or []
        exclusions   = managed.get("exclusions") or []
        custom_rules = pp.get("customRules") or []
        associated   = gw_waf_map.get(pol_name, [])

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
                    ps.get("mode"), ps.get("state"),
                    1 if ps.get("requestBodyCheck") else 0,
                    ps.get("maxRequestBodySizeInKb"),
                    json.dumps([{"type": rs.get("ruleSetType"), "version": rs.get("ruleSetVersion")}
                                for rs in rule_sets]),
                    len(custom_rules), len(exclusions),
                    json.dumps(associated),
                    now,
                ),
            )
        total_waf += 1

    if not dry_run and total_waf:
        conn.commit()

    return total_rules, total_waf
