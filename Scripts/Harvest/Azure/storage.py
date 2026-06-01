"""Harvest Azure Storage Accounts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Storage/storageAccounts"
_SKIP_BLOB_CHILD_PREFIXES = ("bootdiagnostics-",)


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["storage", "account", "list"], subscription_id)
    if not raw:
        return []

    if len(raw) == 1:
        return _harvest_storage_account(subscription_id, raw[0])

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(raw))) as pool:
        for rows in pool.map(lambda acct: _harvest_storage_account(subscription_id, acct), raw):
            results.extend(rows)
    return results


def _harvest_storage_account(subscription_id: str, acct: dict[str, Any]) -> list[dict[str, Any]]:
    props = acct.get("properties") or {}
    fqdn = _get_primary_endpoint(props)
    is_public, is_restricted, ip_restrictions = _classify_exposure(props)
    allow_shared_key = props.get("allowSharedKeyAccess", True)

    endpoint_entries = _get_all_endpoint_entries(props)
    endpoints = build_endpoints(endpoint_entries)
    auth_methods = json.dumps(_get_auth_methods(props))

    extra = {
        "allow_blob_public_access": props.get("allowBlobPublicAccess", False),
        "minimum_tls_version": props.get("minimumTlsVersion"),
        "https_only": props.get("supportsHttpsTrafficOnly", True),
        "network_default_action": _get_network_default_action(props),
        "kind": acct.get("kind"),
        "shared_key_auth_enabled": allow_shared_key,
        "managed_identity_required": not allow_shared_key,
    }

    results = [{
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
    }]

    results.extend(
        _harvest_blob_containers(
            subscription_id,
            acct,
            fqdn,
            is_public,
            is_restricted,
            ip_restrictions,
            auth_methods,
        )
    )
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
    if not props.get("allowSharedKeyAccess", True):
        methods.append("managed_identity")
    # Shared key access (account key + SAS)
    if props.get("allowSharedKeyAccess", True):
        methods.append("account_key")
        methods.append("sas_token")
    return methods


def _get_network_default_action(props: dict[str, Any]) -> str:
    network_acls = props.get("networkAcls") or {}
    return network_acls.get("defaultAction", "Allow")


def _harvest_blob_containers(
    subscription_id: str,
    account: dict[str, Any],
    account_fqdn: str | None,
    account_is_public: int,
    account_is_restricted: int,
    account_ip_restrictions: list[str],
    account_auth_methods: str,
) -> list[dict[str, Any]]:
    account_name = safe_str(account.get("name"))
    account_id = safe_str(account.get("id"))
    resource_group = safe_str(account.get("resourceGroup"))
    if not account_name or not account_id or not resource_group:
        return []

    containers = az(
        [
            "storage", "container", "list",
            "--account-name", account_name,
            "--auth-mode", "login",
        ],
        subscription_id,
    )
    if not containers:
        return []

    rows: list[dict[str, Any]] = []
    blob_tasks: list[tuple[
        str,
        dict[str, Any],
        str | None,
        int,
        int,
        list[str],
        str,
        str,
        str,
        str | None,
    ]] = []
    for container in containers:
        name = safe_str(container.get("name"))
        if not name:
            continue

        props = container.get("properties") or {}
        public_access = safe_str(
            container.get("publicAccess")
            or props.get("publicAccess")
            or props.get("blobPublicAccess")
        )
        child_id = f"{account_id}/blobServices/default/containers/{name}"
        rows.append({
            "id": child_id,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "name": name,
            "type": "Microsoft.Storage/storageAccounts/blobServices/containers",
            "location": account.get("location"),
            "sku": public_access,
            "tags": json.dumps(container.get("metadata") or {}),
            "is_public": 1 if account_is_public and (public_access or "").lower() in {"blob", "container"} else 0,
            "is_restricted": account_is_restricted,
            "ip_restrictions": json.dumps(account_ip_restrictions),
            "endpoints": json.dumps([]),
            "auth_methods": account_auth_methods,
            "fqdn": f"{account_fqdn}/{name}" if account_fqdn else None,
            "pipeline_tag": None,
            "raw_json": json.dumps(container),
        })

        # Boot diagnostics containers can explode in number on VM-heavy
        # subscriptions and usually do not provide useful drill-down value.
        # Keep the container asset, but skip blob fan-out for those containers.
        if name.lower().startswith(_SKIP_BLOB_CHILD_PREFIXES):
            continue

        blob_tasks.append(
            (
                subscription_id,
                account,
                account_fqdn,
                account_is_public,
                account_is_restricted,
                account_ip_restrictions,
                account_auth_methods,
                resource_group,
                child_id,
                name,
                public_access,
            )
        )

    if not blob_tasks:
        return rows

    if len(blob_tasks) == 1:
        rows.extend(_harvest_blob_objects(*blob_tasks[0]))
        return rows

    with ThreadPoolExecutor(max_workers=min(8, len(blob_tasks))) as pool:
        for blob_rows in pool.map(_harvest_blob_objects_from_task, blob_tasks):
            rows.extend(blob_rows)

    return rows


def _harvest_blob_objects_from_task(
    task: tuple[
        str,
        dict[str, Any],
        str | None,
        int,
        int,
        list[str],
        str,
        str,
        str,
        str | None,
    ]
) -> list[dict[str, Any]]:
    return _harvest_blob_objects(*task)


def _harvest_blob_objects(
    subscription_id: str,
    account: dict[str, Any],
    account_fqdn: str | None,
    account_is_public: int,
    account_is_restricted: int,
    account_ip_restrictions: list[str],
    account_auth_methods: str,
    resource_group: str,
    container_id: str,
    container_name: str,
    container_public_access: str | None,
) -> list[dict[str, Any]]:
    account_name = safe_str(account.get("name"))
    if not account_name:
        return []

    blobs = az(
        [
            "storage", "blob", "list",
            "--account-name", account_name,
            "--container-name", container_name,
            "--auth-mode", "login",
        ],
        subscription_id,
    )
    if not blobs:
        return []

    rows: list[dict[str, Any]] = []
    for blob in blobs:
        name = safe_str(blob.get("name"))
        if not name:
            continue

        props = blob.get("properties") or {}
        access_tier = safe_str(props.get("accessTier") or blob.get("accessTier"))
        blob_type = safe_str(props.get("blobType") or blob.get("blobType"))
        child_id = f"{container_id}/blobs/{name}"
        rows.append({
            "id": child_id,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "name": name,
            "type": "Microsoft.Storage/storageAccounts/blobServices/containers/blobs",
            "location": account.get("location"),
            "sku": access_tier or blob_type,
            "tags": json.dumps(blob.get("metadata") or {}),
            "is_public": 1 if account_is_public and (container_public_access or "").lower() in {"blob", "container"} else 0,
            "is_restricted": account_is_restricted,
            "ip_restrictions": json.dumps(account_ip_restrictions),
            "endpoints": json.dumps([]),
            "auth_methods": account_auth_methods,
            "fqdn": f"{account_fqdn}/{container_name}/{name}" if account_fqdn else None,
            "pipeline_tag": None,
            "raw_json": json.dumps(blob),
        })

    return rows
