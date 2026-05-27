"""Harvest Azure App Services (Web Apps) and App Service Plans."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_fqdn, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Web/sites"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    """Return normalised provisioned_asset rows for all web apps in a subscription."""
    raw_apps = az(["webapp", "list"], subscription_id)
    results = []

    for app in raw_apps:
        props = app.get("properties") or app  # az webapp list inlines some props at top level
        fqdn = safe_str(app.get("defaultHostName")) or infer_fqdn(app)
        pipeline_tag = _get_pipeline_tag(app, subscription_id)

        results.append({
            "id": app["id"],
            "subscription_id": subscription_id,
            "resource_group": app.get("resourceGroup"),
            "name": app.get("name"),
            "type": app.get("type", RESOURCE_TYPE),
            "location": app.get("location"),
            "sku": infer_sku(app),
            "tags": json.dumps(app.get("tags") or {}),
            "is_public": _is_public(app),
            "fqdn": fqdn,
            "pipeline_tag": pipeline_tag,
            "raw_json": json.dumps(app),
        })

    return results


def _is_public(app: dict[str, Any]) -> int:
    """App Service is truly internet-accessible only if public network access is enabled AND no access restrictions."""
    props = app.get("properties") or {}
    
    # Check public network access setting
    public_network_access = props.get("publicNetworkAccess") or app.get("publicNetworkAccess", "Enabled")
    if public_network_access == "Disabled":
        return 0  # Public network access disabled
    
    # Check for access restrictions (IP ranges, VNet rules)
    site_config = props.get("siteConfig") or {}
    ip_restrictions = site_config.get("ipSecurityRestrictions") or []
    scm_ip_restrictions = site_config.get("scmIpSecurityRestrictions") or []
    
    # If there are ANY access restrictions, it's not truly public
    if ip_restrictions or scm_ip_restrictions:
        return 0
    
    # Check VNet integration
    vnet_name = props.get("virtualNetworkSubnetId") or app.get("virtualNetworkSubnetId")
    if vnet_name:
        return 0  # VNet-integrated = not internet-facing
    
    return 1


def _get_pipeline_tag(app: dict[str, Any], subscription_id: str) -> str | None:
    """Try to read the ADO pipeline deployment source tag for this web app."""
    tags = app.get("tags") or {}
    # Check common tag keys first (fast path, no extra az call)
    for key in ("pipeline", "Pipeline", "ado-pipeline", "build-pipeline", "deploymentPipeline"):
        if key in tags:
            return safe_str(tags[key])
    return None
