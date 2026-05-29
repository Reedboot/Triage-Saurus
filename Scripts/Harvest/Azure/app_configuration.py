"""Harvest Azure App Configuration stores."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, build_endpoints, extract_ip_restrictions, safe_str

RESOURCE_TYPE = "Microsoft.AppConfiguration/configurationStores"

# Role definition names that represent RBAC-controlled App Config access.
# If neither is assigned, apps are almost certainly using access keys.
_APPCONFIG_DATA_ROLES = {
    "App Configuration Data Owner",
    "App Configuration Data Reader",
}


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appconfig", "list"], subscription_id)
    results = []

    for store in raw:
        props = store.get("properties") or {}
        resource_id = store.get("id", "")
        endpoint = safe_str(props.get("endpoint", "").replace("https://", "").rstrip("/")) or None

        is_public, is_restricted, ip_restrictions = _classify_exposure(props)
        endpoints = build_endpoints([(endpoint, 443, "https")] if endpoint else [])
        auth_methods = json.dumps(_get_auth_methods(props))
        rbac_check = _check_rbac(resource_id, subscription_id, props, store)

        extra = {
            "sku": (store.get("sku") or {}).get("name"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "disable_local_auth": props.get("disableLocalAuth", False),
            "soft_delete_retention_days": props.get("softDeleteRetentionInDays"),
            "enable_purge_protection": props.get("enablePurgeProtection", False),
            "creation_date": props.get("creationDate"),
            "rbac_check": rbac_check,
        }

        results.append({
            "id": resource_id,
            "subscription_id": subscription_id,
            "resource_group": store.get("resourceGroup"),
            "name": store.get("name"),
            "type": store.get("type", RESOURCE_TYPE),
            "location": store.get("location"),
            "sku": (store.get("sku") or {}).get("name"),
            "tags": json.dumps(store.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps(ip_restrictions),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": endpoint,
            "pipeline_tag": (store.get("tags") or {}).get("pipeline") or (store.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**store, "_extra": extra}),
        })

    return results


def _classify_exposure(props: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (is_public, is_restricted, ip_restriction_cidrs)."""
    if props.get("publicNetworkAccess", "Enabled") == "Disabled":
        return 0, 0, []

    network_acls = props.get("networkAcls") or {}
    cidrs = extract_ip_restrictions(network_acls=network_acls)
    if cidrs:
        return 0, 1, cidrs

    return 1, 0, []


def _get_auth_methods(props: dict[str, Any]) -> list[str]:
    methods: list[str] = ["azure_ad"]
    if not props.get("disableLocalAuth", False):
        methods.append("access_key")
    return methods


def _check_rbac(
    resource_id: str,
    subscription_id: str,
    props: dict[str, Any],
    store: dict[str, Any],
) -> dict[str, Any]:
    """Assess RBAC posture for an App Configuration store.

    Checks:
    - disableLocalAuth  — when False, access keys are still enabled (not RBAC-only)
    - Role assignments  — presence of App Configuration Data Owner/Reader roles
                         indicates RBAC is actively used; absence suggests key auth
    - Managed identity  — system/user-assigned identity enables keyless MSI auth
    - Private endpoints — no private endpoint + public access = internet-exposed
    """
    local_auth_disabled = bool(props.get("disableLocalAuth", False))

    # Managed identity on the store itself (not the consuming app)
    identity = store.get("identity") or {}
    has_system_identity = identity.get("type", "").lower() in {"systemassigned", "systemassigned,userassigned"}
    user_identities = list((identity.get("userAssignedIdentities") or {}).keys())
    has_identity = has_system_identity or bool(user_identities)

    # Private endpoint connections
    pe_conns = props.get("privateEndpointConnections") or []
    has_private_endpoint = bool(pe_conns)
    approved_pe_count = sum(
        1 for c in pe_conns
        if (c.get("properties") or {}).get("privateLinkServiceConnectionState", {}).get("status") == "Approved"
    )

    # Role assignments scoped to this resource
    assignments = az(
        ["role", "assignment", "list", "--scope", resource_id, "--include-inherited"],
        subscription_id,
    )
    data_role_assignments = [
        a for a in assignments
        if a.get("roleDefinitionName") in _APPCONFIG_DATA_ROLES
    ]
    # Flag assignments granted at subscription scope (overly broad)
    broad_data_assignments = [
        a for a in data_role_assignments
        if "/resourceGroups/" not in a.get("scope", "")
    ]

    # Derive findings
    findings: list[str] = []
    if not local_auth_disabled:
        findings.append("access_keys_enabled")
    if not data_role_assignments:
        findings.append("no_data_rbac_roles_found")
    if not has_identity:
        findings.append("no_managed_identity")
    if not has_private_endpoint and props.get("publicNetworkAccess", "Enabled") != "Disabled":
        findings.append("no_private_endpoint")
    if broad_data_assignments:
        findings.append("data_role_granted_at_subscription_scope")

    return {
        "local_auth_disabled": local_auth_disabled,
        "rbac_only": local_auth_disabled,
        "has_managed_identity": has_identity,
        "has_system_identity": has_system_identity,
        "user_identity_count": len(user_identities),
        "has_private_endpoint": has_private_endpoint,
        "approved_private_endpoint_count": approved_pe_count,
        "data_role_assignment_count": len(data_role_assignments),
        "broad_data_assignment_count": len(broad_data_assignments),
        "findings": findings,
        "risk": _rbac_risk_level(findings),
    }


def _rbac_risk_level(findings: list[str]) -> str:
    """Derive a simple risk label from the findings list."""
    if "access_keys_enabled" in findings and "no_private_endpoint" in findings:
        return "HIGH"
    if "access_keys_enabled" in findings or "no_private_endpoint" in findings:
        return "MEDIUM"
    if findings:
        return "LOW"
    return "OK"
