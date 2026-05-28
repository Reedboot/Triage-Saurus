"""Harvest Azure Application Gateways and WAF policies."""
from __future__ import annotations

import json
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
