"""Harvest Azure API Management services."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ._helpers import az, build_endpoints, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.ApiManagement/service"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["apim", "list"], subscription_id)
    results = []

    for svc in raw:
        props = svc.get("properties") or {}
        gateway_url = props.get("gatewayUrl") or svc.get("gatewayUrl")
        gateway_hosts = _get_gateway_hosts(svc)
        fqdn = gateway_hosts[0] if gateway_hosts else _extract_fqdn(gateway_url)
        exposure_level = _get_apim_exposure_level(svc)
        vnet_type = props.get("virtualNetworkType", "None")

        api_count = _get_api_count(svc.get("name"), subscription_id, svc.get("resourceGroup"))

        is_public, is_restricted = _classify_exposure(svc)
        endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
        auth_methods = json.dumps(["subscription_key", "oauth2", "client_certificate"])

        extra = {
            "gateway_url": gateway_url,
            "portal_url": props.get("portalUrl"),
            "api_count": api_count,
            "virtual_network_type": vnet_type,
            "gateway_hosts": gateway_hosts,
            "exposure_level": exposure_level,
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
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps([]),
            "endpoints": endpoints,
            "auth_methods": auth_methods,
            "fqdn": fqdn,
            "pipeline_tag": None,
            "raw_json": json.dumps({**svc, "_extra": extra}),
        })

    return results


def _extract_fqdn(gateway_url: str | None) -> str | None:
    if not gateway_url:
        return None
    parsed = urlparse(gateway_url)
    return safe_str(parsed.netloc or parsed.path)


def _dedupe_strs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def _get_gateway_hosts(service: dict[str, Any]) -> list[str]:
    props = service.get("properties") or service
    hosts = [
        host
        for host in [
            safe_str(cfg.get("hostName"))
            for cfg in (props.get("hostnameConfigurations") or [])
            if isinstance(cfg, dict)
        ]
        if host
    ]
    fallback = _extract_fqdn(props.get("gatewayUrl") or service.get("gatewayUrl"))
    if fallback:
        hosts.append(fallback)
    return _dedupe_strs(hosts)


def _get_apim_exposure_level(service_or_props: dict[str, Any]) -> str:
    props = service_or_props.get("properties") or service_or_props
    virtual_network_type = (props.get("virtualNetworkType") or service_or_props.get("virtualNetworkType") or "").lower()
    public_network_access = (props.get("publicNetworkAccess") or service_or_props.get("publicNetworkAccess") or "Enabled").lower()
    public_ips = props.get("publicIpAddresses") or service_or_props.get("publicIpAddresses") or []
    private_ips = props.get("privateIPAddresses") or service_or_props.get("privateIPAddresses") or []

    if virtual_network_type == "internal":
        return "Internal"
    if public_network_access == "disabled":
        return "Internal"
    if not public_ips and private_ips:
        return "Internal"
    return "Public"


def _classify_exposure(service_or_props: dict[str, Any]) -> tuple[int, int]:
    """Return (is_public, is_restricted)."""
    return (1, 0) if _get_apim_exposure_level(service_or_props) == "Public" else (0, 0)


def _get_api_count(service_name: str | None, subscription_id: str, rg: str | None) -> int:
    if not service_name or not rg:
        return 0
    apis = az(["apim", "api", "list", "--service-name", service_name, "--resource-group", rg], subscription_id)
    return len(apis)


def _az_list_apis(service_name: str, resource_group: str, subscription_id: str) -> list[dict[str, Any]] | None:
    cmd = [
        "az", "apim", "api", "list",
        "--service-name", service_name,
        "--resource-group", resource_group,
        "--subscription", subscription_id,
        "--output", "json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:
        raise RuntimeError(str(exc)[:200]) from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200])
    return json.loads(result.stdout or "[]") or []


def harvest_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Harvest APIM API→backend route mappings into apim_api_routes."""
    services = az(["apim", "list"], subscription_id)
    if not services:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    total = 0

    for service in services:
        apim_name = safe_str(service.get("name"))
        resource_group = safe_str(service.get("resourceGroup"))
        if not apim_name or not resource_group:
            continue

        print(f"    [apim-routes] {apim_name}...", end=" ", flush=True)
        try:
            apis = _az_list_apis(apim_name, resource_group, subscription_id)
            gateway_hosts = _get_gateway_hosts(service)
            exposure_level = _get_apim_exposure_level(service)
            routes: list[dict[str, Any]] = []

            for api in apis or []:
                props = api.get("properties") or api
                service_url = safe_str(props.get("serviceUrl") or api.get("serviceUrl"))
                api_name = safe_str(api.get("name"))
                if not api_name or not service_url:
                    continue

                protocols = [
                    protocol.lower()
                    for protocol in (props.get("protocols") or api.get("protocols") or [])
                    if safe_str(protocol)
                ]
                routes.append({
                    "id": f"{apim_name}::{api_name}",
                    "subscription_id": subscription_id,
                    "apim_name": apim_name,
                    "apim_resource_id": service.get("id"),
                    "api_name": api_name,
                    "api_display_name": safe_str(props.get("displayName") or api.get("displayName")),
                    "api_path": safe_str(props.get("path") or api.get("path")),
                    "api_protocols": json.dumps(protocols),
                    "backend_id": None,
                    "backend_url": service_url,
                    "service_url": service_url,
                    "requires_subscription": 1 if props.get("subscriptionRequired", True) else 0,
                    "gateway_hosts": json.dumps(gateway_hosts),
                    "exposure_level": exposure_level,
                    "last_synced": now,
                })

            if not dry_run:
                conn.execute(
                    "DELETE FROM apim_api_routes WHERE subscription_id = ? AND apim_name = ?",
                    (subscription_id, apim_name),
                )
                for route in routes:
                    conn.execute(
                        """
                        INSERT INTO apim_api_routes (
                            id, subscription_id, apim_name, apim_resource_id,
                            api_name, api_display_name, api_path, api_protocols,
                            backend_id, backend_url, service_url, requires_subscription,
                            gateway_hosts, exposure_level, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id       = excluded.subscription_id,
                            apim_resource_id      = excluded.apim_resource_id,
                            api_display_name      = excluded.api_display_name,
                            api_path              = excluded.api_path,
                            api_protocols         = excluded.api_protocols,
                            backend_id            = excluded.backend_id,
                            backend_url           = excluded.backend_url,
                            service_url           = excluded.service_url,
                            requires_subscription = excluded.requires_subscription,
                            gateway_hosts         = excluded.gateway_hosts,
                            exposure_level        = excluded.exposure_level,
                            last_synced           = excluded.last_synced
                        """,
                        (
                            route["id"],
                            route["subscription_id"],
                            route["apim_name"],
                            route["apim_resource_id"],
                            route["api_name"],
                            route["api_display_name"],
                            route["api_path"],
                            route["api_protocols"],
                            route["backend_id"],
                            route["backend_url"],
                            route["service_url"],
                            route["requires_subscription"],
                            route["gateway_hosts"],
                            route["exposure_level"],
                            route["last_synced"],
                        ),
                    )
                conn.commit()

            total += len(routes)
            print(f"{len(routes)} routes")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    return total


def get_gateway_fqdns(subscription_id: str) -> dict[str, str]:
    """Build an FQDN→APIM-service-name index for correlation.

    Returns {gateway_fqdn: service_name}.
    """
    raw = az(["apim", "list"], subscription_id)
    index: dict[str, str] = {}
    for svc in raw:
        for host in _get_gateway_hosts(svc):
            index[host.lower()] = svc.get("name", "")
    return index
