"""Harvest Azure API Management services."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.ApiManagement/service"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["apim", "list"], subscription_id)
    results = []

    for svc in raw:
        props = svc.get("properties") or {}
        gateway_url = props.get("gatewayUrl") or svc.get("gatewayUrl")
        fqdn = _extract_fqdn(gateway_url)

        api_count = _get_api_count(svc.get("name"), subscription_id, svc.get("resourceGroup"))

        extra = {
            "gateway_url": gateway_url,
            "portal_url": props.get("portalUrl"),
            "api_count": api_count,
            "virtual_network_type": props.get("virtualNetworkType", "None"),
        }

        results.append({
            "id": svc["id"],
            "subscription_id": subscription_id,
            "resource_group": svc.get("resourceGroup"),
            "name": svc.get("name"),
            "type": svc.get("type", RESOURCE_TYPE),
            "location": svc.get("location"),
            "sku": infer_sku(svc),
            "tags": json.dumps(svc.get("tags") or {}),
            "is_public": _is_public(props),
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**svc, "_extra": extra}),
        })

    return results


def _extract_fqdn(gateway_url: str | None) -> str | None:
    if not gateway_url:
        return None
    # Strip https:// prefix
    fqdn = gateway_url.replace("https://", "").replace("http://", "").rstrip("/")
    return safe_str(fqdn)


def _is_public(props: dict[str, Any]) -> int:
    vnet_type = props.get("virtualNetworkType", "None")
    # Internal = private APIM; None = public; External = public with VNet integration
    return 0 if vnet_type == "Internal" else 1


def _get_api_count(service_name: str | None, subscription_id: str, rg: str | None) -> int:
    if not service_name or not rg:
        return 0
    apis = az(["apim", "api", "list", "--service-name", service_name, "--resource-group", rg], subscription_id)
    return len(apis)


def get_gateway_fqdns(subscription_id: str) -> dict[str, str]:
    """Build an FQDN→APIM-service-name index for correlation.

    Returns {gateway_fqdn: service_name}.
    """
    raw = az(["apim", "list"], subscription_id)
    index: dict[str, str] = {}
    for svc in raw:
        props = svc.get("properties") or {}
        gateway_url = props.get("gatewayUrl") or svc.get("gatewayUrl")
        fqdn = _extract_fqdn(gateway_url)
        if fqdn:
            index[fqdn.lower()] = svc.get("name", "")
    return index
