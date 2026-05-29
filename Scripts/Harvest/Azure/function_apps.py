"""Harvest Azure Function Apps."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, fetch_ase_ilb_map, infer_fqdn, infer_sku, safe_str

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
    props = app.get("properties") or {}

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
