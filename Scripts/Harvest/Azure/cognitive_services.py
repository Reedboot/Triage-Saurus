"""Harvest Azure Cognitive Services accounts (OpenAI, Form Recognizer, etc.)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ._helpers import (
    az,
    az_resource_show,
    build_endpoints,
    classify_network_access,
    safe_str,
)

RESOURCE_TYPE = "Microsoft.CognitiveServices/accounts"
_MAX_DETAIL_WORKERS = 8


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["cognitiveservices", "account", "list"], subscription_id)
    detail_by_id: dict[str, dict[str, Any] | None] = {}

    def fetch_detail(acct: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        resource_id = acct.get("id", "")
        list_props = acct.get("properties") or {}
        needs_detail = not isinstance(list_props, dict) or any(
            key not in list_props
            for key in ("publicNetworkAccess", "networkAcls", "privateEndpointConnections")
        )
        if not resource_id or not needs_detail:
            return resource_id, None
        return resource_id, az_resource_show(resource_id, subscription_id, runner=az)

    detail_targets = [acct for acct in raw if acct.get("id")]
    if detail_targets:
        with ThreadPoolExecutor(max_workers=min(_MAX_DETAIL_WORKERS, len(detail_targets))) as pool:
            for resource_id, detailed in pool.map(fetch_detail, detail_targets):
                detail_by_id[resource_id] = detailed

    results = []

    for acct in raw:
        detailed = detail_by_id.get(acct.get("id", ""))
        if detailed:
            acct = {**acct, **detailed}
        props = acct.get("properties") or {}
        endpoint = safe_str(
            props.get("endpoint", "").replace("https://", "").rstrip("/")
        ) or None

        kind = acct.get("kind", "")
        is_public, is_restricted, ip_restrictions, exposure_class = _classify_exposure(props)

        endpoints = build_endpoints([(endpoint, 443, "https")] if endpoint else [])
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "kind": kind,
            "sku": (acct.get("sku") or {}).get("name"),
            "public_network_access": props.get("publicNetworkAccess"),
            "network_default_action": (props.get("networkAcls") or {}).get("defaultAction"),
            "exposure_class": exposure_class,
            "disable_local_auth": props.get("disableLocalAuth", False),
            "custom_subdomain": props.get("customSubDomainName"),
            "restore": props.get("restore", False),
        }

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": (acct.get("sku") or {}).get("name"),
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": endpoint,
            "pipeline_tag": (acct.get("tags") or {}).get("pipeline") or (acct.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    # AI Foundry projects, project connections, and model deployments are child
    # ARM resources. They are useful attack-surface inventory even though they
    # do not have their own network endpoint.
    for resource_type in (
        "Microsoft.CognitiveServices/accounts/projects",
        "Microsoft.CognitiveServices/accounts/projects/connections",
        "Microsoft.CognitiveServices/accounts/deployments",
    ):
        for child in az(["resource", "list", "--resource-type", resource_type], subscription_id):
            child_id = safe_str(child.get("id")) or ""
            if not child_id:
                continue
            parent_id = child_id.rsplit("/", 2)[0] if child_id.count("/") >= 2 else child_id
            results.append({
                "id": child_id,
                "subscription_id": subscription_id,
                "resource_group": child.get("resourceGroup"),
                "name": child.get("name"),
                "type": child.get("type", resource_type),
                "location": child.get("location"),
                "sku": None,
                "tags": json.dumps(child.get("tags") or {}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": json.dumps(["azure_ad"]),
                "fqdn": None,
                "pipeline_tag": None,
                "raw_json": json.dumps({
                    **child,
                    "_extra": {
                        "parent_resource_id": parent_id,
                        "foundry_child": True,
                    },
                }),
            })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str], str]:
    endpoint = safe_str(props.get("endpoint"))
    return classify_network_access(
        props,
        endpoint_present=bool(endpoint),
        private_endpoint_connections=props.get("privateEndpointConnections") or [],
    )


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("api_key")
    return methods
