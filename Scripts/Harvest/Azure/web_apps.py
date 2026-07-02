"""Harvest Azure App Services (Web Apps) and App Service Plans."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, fetch_ase_ilb_map, infer_fqdn, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Web/sites"

# App Service default "Allow all" catch-all rule — not a real restriction
_DEFAULT_ALLOW_ALL_PRIORITY = 65000


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    """Return normalised provisioned_asset rows for all web apps in a subscription."""
    raw_apps = az(["webapp", "list"], subscription_id)
    ase_ilb_map = fetch_ase_ilb_map(subscription_id)
    results = []

    for app in raw_apps:
        fqdn = safe_str(app.get("defaultHostName")) or infer_fqdn(app)
        pipeline_tag = _get_pipeline_tag(app, subscription_id)
        is_public, is_restricted, ip_restrictions = _classify_exposure(app, ase_ilb_map)
        kind = safe_str(app.get("kind"))

        endpoints = build_endpoints(_get_endpoint_entries(app, fqdn))
        auth_methods = json.dumps(_get_auth_methods(app))

        results.append({
            "id": app["id"],
            "subscription_id": subscription_id,
            "resource_group": app.get("resourceGroup"),
            "name": app.get("name"),
            "type": app.get("type", RESOURCE_TYPE),
            "location": app.get("location"),
            "sku": infer_sku(app),
            "tags": json.dumps(app.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": pipeline_tag,
            "raw_json": json.dumps({
                **app,
                "_extra": {
                    "os_type": _os_type_from_kind(kind),
                },
            }),
        })

        results.extend(_harvest_slots(app, subscription_id, ase_ilb_map))

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

    # Public network access disabled → private
    public_network_access = props.get("publicNetworkAccess") or app.get("publicNetworkAccess", "Enabled")
    if public_network_access == "Disabled":
        return 0, 0, []

    # NOTE: virtualNetworkSubnetId = outbound VNet integration only.
    # It does NOT restrict inbound public access — do not use as a private indicator.

    # Collect IP restrictions (ignore the default "Allow all" catch-all rule)
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
        return 0, 1, cidrs  # partially exposed

    return 1, 0, []  # fully public


def _get_endpoint_entries(app: dict[str, Any], primary_fqdn: str | None) -> list[tuple[str | None, int, str]]:
    entries: list[tuple[str | None, int, str]] = []
    if primary_fqdn:
        entries.append((primary_fqdn, 443, "https"))
    # Additional custom hostnames
    props = app.get("properties") or {}
    for hostname in (props.get("hostNames") or app.get("hostNames") or []):
        host = safe_str(hostname)
        if host and host != primary_fqdn:
            entries.append((host, 443, "https"))
    return entries


def _get_auth_methods(app: dict[str, Any]) -> list[str]:
    """Infer auth methods from app configuration (best-effort)."""
    methods: list[str] = []
    props = app.get("properties") or {}
    site_config = props.get("siteConfig") or {}

    # App Service supports AAD/OAuth2 authentication if auth is configured
    # We check for the authSettings hint in kind or config
    auth_settings = props.get("siteAuthSettings") or {}
    if auth_settings.get("enabled"):
        methods.append("azure_ad")
    else:
        methods.append("azure_ad_optional")

    # Function keys / basic auth
    if site_config.get("ftpsState") not in ("Disabled", "FtpsOnly"):
        pass  # FTPS state doesn't directly indicate function keys

    # Check if basic auth is enabled (publishingCredentials)
    basic_auth_enabled = (props.get("basicPublishingCredentialsPolicies") or {}).get("allow", True)
    if basic_auth_enabled:
        methods.append("basic_publishing_credentials")

    return methods


def _get_pipeline_tag(app: dict[str, Any], subscription_id: str) -> str | None:
    """Try to read the ADO pipeline deployment source tag for this web app."""
    tags = app.get("tags") or {}
    for key in ("pipeline", "Pipeline", "ado-pipeline", "build-pipeline", "deploymentPipeline"):
        if key in tags:
            return safe_str(tags[key])
    return None


def _os_type_from_kind(kind: str | None) -> str | None:
    kind_l = safe_str(kind).lower()
    if "linux" in kind_l:
        return "Linux"
    if "windows" in kind_l:
        return "Windows"
    return None


def _harvest_slots(
    app: dict[str, Any],
    subscription_id: str,
    ase_ilb_map: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    app_name = safe_str(app.get("name"))
    resource_group = safe_str(app.get("resourceGroup"))
    if not app_name or not resource_group:
        return []

    try:
        slots = az(
            ["webapp", "deployment", "slot", "list", "--name", app_name, "--resource-group", resource_group],
            subscription_id,
        )
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for slot in slots:
        fqdn = safe_str(slot.get("defaultHostName")) or infer_fqdn(slot)
        is_public, is_restricted, ip_restrictions = _classify_exposure(slot, ase_ilb_map)
        kind = safe_str(slot.get("kind"))
        rows.append({
            "id": slot["id"],
            "subscription_id": subscription_id,
            "resource_group": slot.get("resourceGroup") or resource_group,
            "name": slot.get("name"),
            "type": slot.get("type", "Microsoft.Web/sites/slots"),
            "location": slot.get("location"),
            "sku": infer_sku(slot),
            "tags": json.dumps(slot.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": build_endpoints(_get_endpoint_entries(slot, fqdn)),
            "auth_methods": json.dumps(_get_auth_methods(slot)),
            "fqdn": fqdn,
            "pipeline_tag": _get_pipeline_tag(slot, subscription_id),
            "raw_json": json.dumps({
                **slot,
                "_extra": {
                    "os_type": _os_type_from_kind(kind),
                    "slot_parent": app_name,
                    "slot_name": safe_str(slot.get("name")),
                },
            }),
        })
    return rows
