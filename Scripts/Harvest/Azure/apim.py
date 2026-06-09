"""Harvest Azure API Management services."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import signal
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

from ._helpers import az, build_endpoints, infer_sku, normalize_route_path, safe_str, _is_msal_lock_error, _AZ_RETRY_MAX, _AZ_RETRY_BACKOFF
from ._staged import BackfillJob, StagedRows

RESOURCE_TYPE = "Microsoft.ApiManagement/service"
_APIM_SERVICE_WORKERS = 4
_APIM_API_WORKERS = 4
_APIM_OPERATION_WORKERS = 4
_BACKFILL_WORKERS = 8
_BACKFILL_EXECUTOR: ThreadPoolExecutor | None = None


def _get_backfill_executor() -> ThreadPoolExecutor:
    global _BACKFILL_EXECUTOR
    if _BACKFILL_EXECUTOR is None:
        _BACKFILL_EXECUTOR = ThreadPoolExecutor(
            max_workers=_BACKFILL_WORKERS,
            thread_name_prefix="apim-backfill",
        )
    return _BACKFILL_EXECUTOR


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)}m{remainder:04.1f}s"


def _format_progress_bar(completed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'#' * width}]"
    filled = min(width, max(0, int((completed / total) * width)))
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _format_progress_line(prefix: str, completed: int, total: int, started_at: float, *, suffix: str = "") -> str:
    percent = int((completed / total) * 100) if total else 100
    elapsed = _format_elapsed(time.perf_counter() - started_at)
    tail = f" | {suffix}" if suffix else ""
    return f"    {prefix} {_format_progress_bar(completed, total)} {completed}/{total} {percent}% | elapsed {elapsed}{tail}"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    started = time.perf_counter()
    raw = az(["apim", "list"], subscription_id)
    if not raw:
        return []

    results: list[dict[str, Any]] = []
    max_workers = min(8, len(raw))
    print(f"    [apim] harvesting {len(raw)} service(s) with up to {max_workers} worker(s)", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_build_service_asset, svc, subscription_id): svc for svc in raw}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            svc = futures[future]
            svc_name = safe_str(svc.get("name")) or safe_str(svc.get("id")) or "unknown"
            try:
                asset = future.result()
            except Exception as exc:
                print(f"    [apim] {svc_name} FAILED ({exc})")
                continue
            results.append(asset)
            print(
                _format_progress_line("[apim] services", completed, len(raw), started, suffix=f"last={svc_name}"),
                flush=True,
            )

    print(f"    [apim] completed in {_format_elapsed(time.perf_counter() - started)}", flush=True)
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


def _build_service_asset(svc: dict[str, Any], subscription_id: str) -> dict[str, Any]:
    props = svc.get("properties") or {}
    gateway_url = props.get("gatewayUrl") or svc.get("gatewayUrl")
    gateway_hosts = _get_gateway_hosts(svc)
    fqdn = gateway_hosts[0] if gateway_hosts else _extract_fqdn(gateway_url)
    exposure_level = _get_apim_exposure_level(svc)
    vnet_type = props.get("virtualNetworkType", "None")

    is_public, is_restricted = _classify_exposure(svc)
    endpoints = build_endpoints([(fqdn, 443, "https")] if fqdn else [])
    auth_methods = json.dumps(["subscription_key", "oauth2", "client_certificate"])

    extra = {
        "gateway_url": gateway_url,
        "portal_url": props.get("portalUrl"),
        "api_count": None,
        "virtual_network_type": vnet_type,
        "gateway_hosts": gateway_hosts,
        "exposure_level": exposure_level,
    }

    return {
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
    }


def _get_api_count(service_name: str | None, subscription_id: str, rg: str | None) -> int:
    if not service_name or not rg:
        return 0
    apis = az(["apim", "api", "list", "--service-name", service_name, "--resource-group", rg], subscription_id)
    return len(apis)


def _az_list_apis(service_name: str, resource_group: str, subscription_id: str) -> list[dict[str, Any]] | None:
    return az([
        "apim", "api", "list",
        "--service-name", service_name,
        "--resource-group", resource_group,
    ], subscription_id)


def _run_az_json(cmd: list[str], timeout: int = 120) -> Any:
    last_stderr = ""
    for attempt in range(_AZ_RETRY_MAX):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise TimeoutError(f"{' '.join(cmd[:6])} timed out after {timeout}s") from exc

        if proc.returncode == 0:
            try:
                return json.loads(stdout or "null")
            except json.JSONDecodeError as exc:
                preview = (stdout or "").replace("\n", " ")[:200]
                raise RuntimeError(f"invalid JSON from {' '.join(cmd[:6])}: {exc.msg}; output={preview!r}") from exc

        last_stderr = stderr.strip()
        if attempt < _AZ_RETRY_MAX - 1 and _is_msal_lock_error(last_stderr):
            time.sleep(_AZ_RETRY_BACKOFF * (attempt + 1))
            continue
        raise RuntimeError(last_stderr[:200])
    raise RuntimeError(last_stderr[:200])


def _az_list_operations(
    service_name: str,
    resource_group: str,
    api_id: str,
    subscription_id: str,
) -> list[dict[str, Any]] | None:
    return az([
        "apim", "api", "operation", "list",
        "--service-name", service_name,
        "--resource-group", resource_group,
        "--api-id", api_id,
    ], subscription_id)


def _az_show_policy(
    resource_kind: str,
    service_name: str,
    resource_group: str,
    subscription_id: str,
    *,
    api_id: str | None = None,
    operation_id: str | None = None,
) -> str | None:
    if resource_kind == "api":
        cmd = ["az", "apim", "api", "policy", "show"]
    elif resource_kind == "operation":
        cmd = ["az", "apim", "api", "operation", "policy", "show"]
    else:
        return None
    cmd += [
        "--service-name", service_name,
        "--resource-group", resource_group,
        "--subscription", subscription_id,
        "--output", "json",
    ]
    if api_id:
        cmd.extend(["--api-id", api_id])
    if operation_id:
        cmd.extend(["--operation-id", operation_id])
    try:
        payload = _run_az_json(cmd)
    except Exception:
        return None

    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, str) and value.strip():
            return value
        props = payload.get("properties") or {}
        value = props.get("value")
        if isinstance(value, str) and value.strip():
            return value
    elif isinstance(payload, str) and payload.strip():
        return payload
    return None


def _ensure_apim_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS apim_api_operations (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            apim_name TEXT NOT NULL,
            api_name TEXT NOT NULL,
            api_display_name TEXT,
            api_path TEXT,
            backend_url TEXT,
            operation_id TEXT NOT NULL,
            display_name TEXT,
            method TEXT,
            url_template TEXT,
            description TEXT,
            requires_subscription INTEGER DEFAULT 1,
            policy_summary TEXT,
            last_synced DATETIME
        );
        CREATE INDEX IF NOT EXISTS idx_apim_ops_sub  ON apim_api_operations(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_apim_ops_apim ON apim_api_operations(apim_name);
        CREATE INDEX IF NOT EXISTS idx_apim_ops_api  ON apim_api_operations(api_name);
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(apim_api_operations)").fetchall()
    }
    if "policy_summary" not in columns:
        conn.execute("ALTER TABLE apim_api_operations ADD COLUMN policy_summary TEXT")
    conn.commit()


def _policy_value(policy_xml: str | None, tag: str) -> list[str]:
    if not policy_xml:
        return []
    pattern = rf"<{tag}\b[^>]*\b(?:base-url|baseUrl)=\"([^\"]+)\""
    return re.findall(pattern, policy_xml, flags=re.IGNORECASE)


def _policy_flags(policy_xml: str | None) -> dict[str, Any]:
    if not policy_xml:
        return {
            "has_policy": False,
            "has_validate_jwt": False,
            "has_set_backend_service": False,
            "has_check_header": False,
            "has_subscription_key_check": False,
            "has_authentication_managed_identity": False,
            "backend_urls": [],
            "backend_ids": [],
        }

    text = policy_xml.lower()
    backend_urls = _policy_value(policy_xml, "set-backend-service")
    backend_ids = []
    backend_ids.extend(re.findall(r'<set-backend-service\b[^>]*\bbackend-id="([^"]+)"', policy_xml, flags=re.IGNORECASE))
    return {
        "has_policy": True,
        "has_validate_jwt": "validate-jwt" in text,
        "has_set_backend_service": "set-backend-service" in text,
        "has_check_header": "check-header" in text,
        "has_subscription_key_check": "subscription-key" in text or "subscription required" in text,
        "has_authentication_managed_identity": "authentication-managed-identity" in text,
        "backend_urls": backend_urls,
        "backend_ids": backend_ids,
    }


def _fetch_api_bundle(
    service: dict[str, Any],
    api: dict[str, Any],
    subscription_id: str,
    now: str,
    *,
    include_operations: bool = True,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    apim_name = safe_str(service.get("name"))
    resource_group = safe_str(service.get("resourceGroup"))
    api_name = safe_str(api.get("name"))
    if not apim_name or not resource_group or not api_name:
        return None, []

    props = api.get("properties") or api
    protocols = [
        protocol.lower()
        for protocol in (props.get("protocols") or api.get("protocols") or [])
        if safe_str(protocol)
    ]
    api_policy = _az_show_policy(
        "api",
        apim_name,
        resource_group,
        subscription_id,
        api_id=api_name,
    )
    api_policy_flags = _policy_flags(api_policy)
    service_url = safe_str(props.get("serviceUrl") or api.get("serviceUrl"))
    api_backend_url = api_policy_flags["backend_urls"][0] if api_policy_flags["backend_urls"] else service_url
    if not api_backend_url:
        return None, []

    api_display = safe_str(props.get("displayName") or api.get("displayName") or api_name)
    api_path = safe_str(props.get("path") or api.get("path"))
    requires_subscription = 1 if props.get("subscriptionRequired", True) else 0
    route = {
        "id": f"{apim_name}::{api_name}",
        "subscription_id": subscription_id,
        "apim_name": apim_name,
        "apim_resource_id": service.get("id"),
        "api_name": api_name,
        "api_display_name": api_display,
        "api_path": api_path,
        "api_protocols": json.dumps(protocols),
        "backend_id": None,
        "backend_url": api_backend_url,
        "service_url": service_url,
        "requires_subscription": requires_subscription,
        "gateway_hosts": json.dumps(_get_gateway_hosts(service)),
        "exposure_level": _get_apim_exposure_level(service),
        "normalized_api_path": normalize_route_path(api_path),
        "api_policy_flags": api_policy_flags,
        "last_synced": now,
    }

    operation_specs: list[dict[str, Any]] = []
    if include_operations:
        ops = _az_list_operations(apim_name, resource_group, api_name, subscription_id)
        for op in ops or []:
            op_id = safe_str(op.get("name") or (op.get("id") or "").split("/")[-1])
            if not op_id:
                continue
            operation_specs.append(
                {
                    "id": f"{apim_name}::{api_name}::{op_id}",
                    "subscription_id": subscription_id,
                    "apim_name": apim_name,
                    "resource_group": resource_group,
                    "api_name": api_name,
                    "api_display_name": api_display,
                    "api_path": api_path,
                    "backend_url": api_backend_url,
                    "operation_id": op_id,
                    "display_name": safe_str(op.get("displayName") or op_id),
                    "method": safe_str(op.get("method") or ""),
                    "url_template": safe_str(op.get("urlTemplate") or op.get("url") or ""),
                    "normalized_url_template": normalize_route_path(op.get("urlTemplate") or op.get("url") or ""),
                    "description": safe_str(op.get("description") or ""),
                    "requires_subscription": requires_subscription,
                    "api_policy_flags": api_policy_flags,
                    "last_synced": now,
                }
            )

    return route, operation_specs


def _materialize_operation_row(op_spec: dict[str, Any]) -> dict[str, Any] | None:
    op_policy = _az_show_policy(
        "operation",
        op_spec["apim_name"],
        op_spec["resource_group"],
        op_spec["subscription_id"],
        api_id=op_spec["api_name"],
        operation_id=op_spec["operation_id"],
    )
    op_flags = _policy_flags(op_policy)
    backend_url = op_flags["backend_urls"][0] if op_flags["backend_urls"] else op_spec["backend_url"]
    merged_flags = {
        "api_policy": op_spec["api_policy_flags"],
        "operation_policy": op_flags,
    }
    return {
        "id": op_spec["id"],
        "subscription_id": op_spec["subscription_id"],
        "apim_name": op_spec["apim_name"],
        "api_name": op_spec["api_name"],
        "api_display_name": op_spec["api_display_name"],
        "api_path": op_spec["api_path"],
        "backend_url": backend_url,
        "operation_id": op_spec["operation_id"],
        "display_name": op_spec["display_name"],
        "method": op_spec["method"],
        "url_template": op_spec["url_template"],
        "description": op_spec["description"],
        "requires_subscription": op_spec["requires_subscription"],
        "policy_summary": json.dumps(merged_flags),
        "last_synced": op_spec["last_synced"],
    }


def _build_operation_specs_for_api(api_spec: dict[str, Any]) -> list[dict[str, Any]]:
    ops = _az_list_operations(
        api_spec["apim_name"],
        api_spec["resource_group"],
        api_spec["api_name"],
        api_spec["subscription_id"],
    )
    operation_specs: list[dict[str, Any]] = []
    for op in ops or []:
        op_id = safe_str(op.get("name") or (op.get("id") or "").split("/")[-1])
        if not op_id:
            continue
        operation_specs.append(
            {
                "id": f"{api_spec['apim_name']}::{api_spec['api_name']}::{op_id}",
                "subscription_id": api_spec["subscription_id"],
                "apim_name": api_spec["apim_name"],
                "resource_group": api_spec["resource_group"],
                "api_name": api_spec["api_name"],
                "api_display_name": api_spec["api_display_name"],
                "api_path": api_spec["api_path"],
                "backend_url": api_spec["backend_url"],
                "operation_id": op_id,
                "display_name": safe_str(op.get("displayName") or op_id),
                "method": safe_str(op.get("method") or ""),
                "url_template": safe_str(op.get("urlTemplate") or op.get("url") or ""),
                "normalized_url_template": normalize_route_path(op.get("urlTemplate") or op.get("url") or ""),
                "description": safe_str(op.get("description") or ""),
                "requires_subscription": api_spec["requires_subscription"],
                "api_policy_flags": api_spec["api_policy_flags"],
                "last_synced": api_spec["last_synced"],
            }
        )
    return operation_specs


def _materialize_api_operations(api_spec: dict[str, Any]) -> list[dict[str, Any]]:
    operation_specs = _build_operation_specs_for_api(api_spec)
    if not operation_specs:
        return []
    return [row for row in (_materialize_operation_row(spec) for spec in operation_specs) if row]


def _harvest_service_route_bundle(
    service: dict[str, Any],
    subscription_id: str,
    now: str,
    *,
    stage_backfill: bool = False,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    bundle_started = time.perf_counter()
    apim_name = safe_str(service.get("name"))
    resource_group = safe_str(service.get("resourceGroup"))
    if not apim_name or not resource_group:
        return None, [], []

    print(f"    [apim-routes] {apim_name}: discovering APIs...", flush=True)
    discover_started = time.perf_counter()
    apis = _az_list_apis(apim_name, resource_group, subscription_id)
    print(
        f"    [apim-routes] {apim_name}: discovered {len(apis) if apis else 0} API(s) in {_format_elapsed(time.perf_counter() - discover_started)}",
        flush=True,
    )
    route_rows: list[dict[str, Any]] = []
    operation_specs: list[dict[str, Any]] = []
    api_backfill_specs: list[dict[str, Any]] = []

    if apis:
        max_workers = min(_APIM_API_WORKERS, len(apis))
        print(
            f"    [apim-routes] {apim_name}: fetching API route bundles in parallel with {max_workers} worker(s)...",
            flush=True,
        )
        api_phase_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _fetch_api_bundle,
                    service,
                    api,
                    subscription_id,
                    now,
                    include_operations=not stage_backfill,
                ): safe_str(api.get("name")) or "<unknown>"
                for api in apis
            }
            completed = 0
            total_apis = len(futures)
            for future in as_completed(futures):
                completed += 1
                api_name = futures[future]
                try:
                    route, api_operation_specs = future.result()
                except Exception as exc:
                    print(f"    [apim-routes] {apim_name}/{api_name} FAILED ({exc})")
                    continue
                if route:
                    route_rows.append(route)
                    if stage_backfill:
                        api_backfill_specs.append(
                            {
                                "subscription_id": subscription_id,
                                "apim_name": apim_name,
                                "resource_group": resource_group,
                                "api_name": route["api_name"],
                                "api_display_name": route["api_display_name"],
                                "api_path": route["api_path"],
                                "backend_url": route["backend_url"],
                                "requires_subscription": route["requires_subscription"],
                                "api_policy_flags": route["api_policy_flags"],
                                "last_synced": now,
                            }
                        )
                if api_operation_specs:
                    operation_specs.extend(api_operation_specs)
                if completed == 1 or completed % 5 == 0 or completed == total_apis:
                    print(
                        _format_progress_line(
                            f"[apim-routes] {apim_name} APIs",
                            completed,
                            total_apis,
                            api_phase_started,
                        ),
                        flush=True,
                    )
        print(
            f"    [apim-routes] {apim_name}: API bundle phase finished in {_format_elapsed(time.perf_counter() - api_phase_started)}",
            flush=True,
        )
    else:
        print(f"    [apim-routes] {apim_name}: 0 APIs found")

    print(f"    [apim-routes] {apim_name}: {len(route_rows)} routes from {len(apis)} APIs")

    if stage_backfill:
        print(
            f"    [apim-routes] {apim_name}: queued {len(api_backfill_specs)} API operation backfill job(s)",
            flush=True,
        )
        print(
            f"    [apim-routes] {apim_name}: bundle complete in {_format_elapsed(time.perf_counter() - bundle_started)} "
            f"({len(route_rows)} route(s), {len(api_backfill_specs)} API backfill spec(s))",
            flush=True,
        )
        return apim_name, route_rows, api_backfill_specs

    operation_rows: list[dict[str, Any]] = []
    if operation_specs:
        print(
            f"    [apim-routes] {apim_name}: fetching {len(operation_specs)} operation policies in parallel with {min(_APIM_OPERATION_WORKERS, len(operation_specs))} worker(s)...",
            flush=True,
        )
        max_workers = min(_APIM_OPERATION_WORKERS, len(operation_specs))
        op_phase_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_materialize_operation_row, spec): spec["id"] for spec in operation_specs}
            completed = 0
            total_ops = len(futures)
            for future in as_completed(futures):
                completed += 1
                op_id = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    print(f"    [apim-routes] {apim_name}/{op_id} FAILED ({exc})")
                    continue
                if row:
                    operation_rows.append(row)
                if completed == 1 or completed % 10 == 0 or completed == total_ops:
                    print(
                        _format_progress_line(
                            f"[apim-routes] {apim_name} ops",
                            completed,
                            total_ops,
                            op_phase_started,
                        ),
                        flush=True,
                    )
        print(
            f"    [apim-routes] {apim_name}: operation policy phase finished in {_format_elapsed(time.perf_counter() - op_phase_started)}",
            flush=True,
        )
    else:
        print(f"    [apim-routes] {apim_name}: 0 operations discovered")

    print(
        f"    [apim-routes] {apim_name}: bundle complete in {_format_elapsed(time.perf_counter() - bundle_started)} "
        f"({len(route_rows)} route(s), {len(operation_rows)} operation row(s))",
        flush=True,
    )
    return apim_name, route_rows, operation_rows


def harvest_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
    *,
    stage_backfill: bool = False,
) -> int | StagedRows:
    """Harvest APIM API→backend route mappings into apim_api_routes."""
    started = time.perf_counter()
    _ensure_apim_schema(conn)

    services = az(["apim", "list"], subscription_id)
    if not services:
        return StagedRows([], []) if stage_backfill else 0

    now = datetime.now(timezone.utc).isoformat()
    total = 0
    max_workers = min(_APIM_SERVICE_WORKERS, len(services))
    service_phase_started = time.perf_counter()
    if stage_backfill:
        core_rows: list[dict[str, Any]] = []
        backfill_jobs: list[BackfillJob] = []
    else:
        core_rows = []
        backfill_jobs = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _harvest_service_route_bundle,
                service,
                subscription_id,
                now,
                stage_backfill=stage_backfill,
            ): safe_str(service.get("name")) or safe_str(service.get("id")) or "<unknown>"
            for service in services
        }
        completed_services = 0
        for future in as_completed(futures):
            service_name = futures[future]
            try:
                apim_name, route_rows, operation_rows = future.result()
            except Exception as exc:
                print(f"    [apim-routes] {service_name} SKIPPED ({exc})")
                completed_services += 1
                print(
                    _format_progress_line(
                        "[apim-routes] services",
                        completed_services,
                        len(services),
                        service_phase_started,
                        suffix=f"last={service_name}",
                    ),
                    flush=True,
                )
                continue

            if not apim_name:
                completed_services += 1
                print(
                    _format_progress_line(
                        "[apim-routes] services",
                        completed_services,
                        len(services),
                        service_phase_started,
                        suffix=f"last={service_name}",
                    ),
                    flush=True,
                )
                continue

            if stage_backfill:
                if route_rows:
                    core_rows.extend(route_rows)
                for spec in operation_rows:
                    backfill_future = _get_backfill_executor().submit(_materialize_api_operations, spec)
                    backfill_jobs.append(
                        BackfillJob(
                            label=f"{apim_name}::{safe_str(spec.get('api_name') or spec.get('id')) or 'api'}",
                            future=backfill_future,
                        )
                    )
                completed_services += 1
                print(
                    _format_progress_line(
                        "[apim-routes] services",
                        completed_services,
                        len(services),
                        service_phase_started,
                        suffix=f"last={apim_name}",
                    ),
                    flush=True,
                )
                continue

            if not dry_run:
                conn.execute(
                    "DELETE FROM apim_api_routes WHERE subscription_id = ? AND apim_name = ?",
                    (subscription_id, apim_name),
                )
                for route in route_rows:
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
                conn.execute(
                    "DELETE FROM apim_api_operations WHERE subscription_id = ? AND apim_name = ?",
                    (subscription_id, apim_name),
                )
                for row in operation_rows:
                    conn.execute(
                        """
                        INSERT INTO apim_api_operations (
                            id, subscription_id, apim_name, api_name, api_display_name,
                            api_path, backend_url, operation_id, display_name,
                            method, url_template, description, requires_subscription,
                            policy_summary, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            api_display_name      = excluded.api_display_name,
                            api_path              = excluded.api_path,
                            backend_url           = excluded.backend_url,
                            display_name          = excluded.display_name,
                            method                = excluded.method,
                            url_template          = excluded.url_template,
                            description           = excluded.description,
                            requires_subscription = excluded.requires_subscription,
                            policy_summary        = excluded.policy_summary,
                            last_synced           = excluded.last_synced
                        """,
                        (
                            row["id"],
                            row["subscription_id"],
                            row["apim_name"],
                            row["api_name"],
                            row["api_display_name"],
                            row["api_path"],
                            row["backend_url"],
                            row["operation_id"],
                            row["display_name"],
                            row["method"].upper() if row["method"] else None,
                            row["url_template"],
                            row["description"],
                            row["requires_subscription"],
                            row["policy_summary"],
                            row["last_synced"],
                        ),
                    )
                conn.commit()

            total += len(route_rows)
            completed_services += 1
            print(
                _format_progress_line(
                "[apim-routes] services",
                completed_services,
                len(services),
                service_phase_started,
                suffix=f"last={apim_name}",
                ),
                flush=True,
            )

    if stage_backfill:
        print(
            f"    [apim-routes] staged {len(core_rows)} route(s) and queued {len(backfill_jobs)} operation backfill job(s)",
            flush=True,
        )
        print(f"    [apim-routes] completed in {_format_elapsed(time.perf_counter() - started)}", flush=True)
        return StagedRows(core_rows, backfill_jobs)

    if total == 0:
        print("  [warn] no APIM API routes were harvested; check CLI access and policy permissions")

    print(f"    [apim-routes] completed in {_format_elapsed(time.perf_counter() - started)}", flush=True)
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
