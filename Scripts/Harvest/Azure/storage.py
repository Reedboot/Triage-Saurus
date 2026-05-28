"""Harvest Azure Storage Accounts."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Storage/storageAccounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["storage", "account", "list"], subscription_id)
    results = []

    for acct in raw:
        props = acct.get("properties") or {}
        fqdn = _get_primary_endpoint(props)
        is_public, is_restricted, ip_restrictions = _classify_exposure(props)

        endpoint_entries = _get_all_endpoint_entries(props)
        endpoints = build_endpoints(endpoint_entries)
        auth_methods = json.dumps(_get_auth_methods(props))

        extra = {
            "allow_blob_public_access": props.get("allowBlobPublicAccess", False),
            "minimum_tls_version": props.get("minimumTlsVersion"),
            "https_only": props.get("supportsHttpsTrafficOnly", True),
            "network_default_action": _get_network_default_action(props),
            "kind": acct.get("kind"),
        }

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": infer_sku(acct),
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results


def _get_primary_endpoint(props: dict[str, Any]) -> str | None:
    endpoints = props.get("primaryEndpoints") or {}
    blob = endpoints.get("blob")
    if blob:
        return safe_str(blob.replace("https://", "").replace("http://", "").rstrip("/"))
    return None


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_cidrs)."""
    network_acls = props.get("networkAcls") or {}
    default_action = network_acls.get("defaultAction", "Allow")

    # If default action is Deny → allowlist mode (restricted)
    if default_action == "Deny":
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    # Check for specific rules even when default is Allow
    ip_rules = network_acls.get("ipRules") or []
    vnet_rules = network_acls.get("virtualNetworkRules") or []
    if ip_rules or vnet_rules:
        cidrs = extract_ip_restrictions(network_acls=network_acls)
        return 0, 1, cidrs

    return 1, 0, []


def _get_all_endpoint_entries(props: dict[str, Any]) -> list[tuple[str | None, int, str]]:
    """Build endpoint list from all primary service endpoints."""
    primary = props.get("primaryEndpoints") or {}
    https_only = props.get("supportsHttpsTrafficOnly", True)
    entries: list[tuple[str | None, int, str]] = []

    protocol = "https" if https_only else "http"

    for svc, raw_url in primary.items():
        if not raw_url or svc in ("microsoftEndpoints", "internetEndpoints"):
            continue
        addr = safe_str(
            raw_url.replace("https://", "").replace("http://", "").rstrip("/")
        )
        entries.append((addr, 443, protocol))

    return entries


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    # Shared key access (account key + SAS)
    if props.get("allowSharedKeyAccess", True):
        methods.append("account_key")
        methods.append("sas_token")
    return methods


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = props.get("networkAcls") or {}
    return network_acls.get("defaultAction", "Allow")
