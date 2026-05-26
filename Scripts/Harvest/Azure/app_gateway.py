"""Harvest Azure Application Gateways and WAF policies."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Network/applicationGateways"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "application-gateway", "list"], subscription_id)
    results = []

    for gw in raw:
        props = gw.get("properties") or {}
        fqdn = _get_frontend_fqdn(props)
        waf_mode = _get_waf_mode(gw, subscription_id)

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
            "is_public": _has_public_frontend(props),
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
