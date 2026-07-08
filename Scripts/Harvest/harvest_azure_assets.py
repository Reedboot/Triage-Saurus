#!/usr/bin/env python3
"""Harvest live Azure cloud assets into Triage-Saurus cozo.db.

Usage:
    # Harvest a single subscription (name or GUID)
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub"

    # Harvest all accessible subscriptions
    python Scripts/Harvest/harvest_azure_assets.py --all

    # Dry-run: print what would be harvested without writing to DB
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub" --dry-run

    # Faster storage inventory: skip blob-object enumeration
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub" --skip-storage-blobs

    # Target a subset of component groups
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub" --components "App Gateways" --components "APIM"

    # Isolate a single component group without correlation steps
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub" --components "App Gateways" --skip-post-harvest

Prerequisites:
    - Python 3.9+
    - Azure CLI (az) installed and in PATH — https://aka.ms/installazurecli
    - Logged in:  az login  (or service principal / managed identity)
"""
from __future__ import annotations

import argparse
import inspect
import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Path setup so this script works from any cwd
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore

from Azure import app_gateway, apim, web_apps, function_apps, aks, storage, key_vault, sql_server
from Azure import cosmos_db, app_service_plan, service_bus, container_registry, virtual_network
from Azure import redis_cache, event_hub, app_configuration, service_fabric, cognitive_services
from Azure import data_factory, app_service_environment, app_insights, private_endpoint, traffic_manager
from Azure import databricks, event_grid, kusto, logic_apps, search_service
from Azure import machine_learning
from Azure import front_door, firewall
from Azure import network_security_group, route_table, public_ip, load_balancer, bastion_host
from Azure import user_assigned_identity, virtual_machine, virtual_machine_scale_set, image
from Azure import log_analytics_workspace, monitor_action_group, activity_log_alert
from Azure import app_service_certificate, app_service_certificate_order
from Azure._staged import BackfillJob, StagedRows
from Azure._helpers import set_probe_enabled
import appgw_routing_map
import apim_routing_map
import private_dns_map

# ---------------------------------------------------------------------------
# Providers registry — order matters: gateways/APIM first for correlation
# ---------------------------------------------------------------------------
PROVIDERS = [
    # ── Ingress / API layer ──────────────────────────────────────────────
    ("App Gateways",            app_gateway.harvest),
    ("APIM",                    apim.harvest),
    ("Traffic Manager",         traffic_manager.harvest),
    ("Front Door",              front_door.harvest),
    # ── Compute ─────────────────────────────────────────────────────────
    ("App Service Environments",app_service_environment.harvest),
    ("App Service Plans",       app_service_plan.harvest),
    ("Web Apps",                web_apps.harvest),
    ("Function Apps",           function_apps.harvest),
    ("AKS",                     aks.harvest),
    ("Virtual Machines",        virtual_machine.harvest),
    ("Virtual Machine Scale Sets", virtual_machine_scale_set.harvest),
    ("Images",                  image.harvest),
    ("Service Fabric",          service_fabric.harvest),
    # ── Data ────────────────────────────────────────────────────────────
    ("Cosmos DB",               cosmos_db.harvest),
    ("SQL Servers",             sql_server.harvest),
    ("Redis Cache",             redis_cache.harvest),
    ("Storage",                 storage.harvest),
    ("Databricks",              databricks.harvest),
    # ── Messaging / Integration ──────────────────────────────────────────
    ("Event Hubs",              event_hub.harvest),
    ("Event Grid",              event_grid.harvest),
    ("Service Bus",             service_bus.harvest),
    ("Logic Apps",              logic_apps.harvest),
    ("Data Factory",            data_factory.harvest),
    # ── AI / ML ─────────────────────────────────────────────────────────
    ("Machine Learning Workspaces", machine_learning.harvest),
    ("Cognitive Services",      cognitive_services.harvest),
    # ── Security / Config ────────────────────────────────────────────────
    ("Key Vaults",              key_vault.harvest),
    ("App Configuration",       app_configuration.harvest),
    ("Container Registries",    container_registry.harvest),
    ("User Assigned Identities", user_assigned_identity.harvest),
    ("Certificates",            app_service_certificate.harvest),
    ("Certificate Orders",      app_service_certificate_order.harvest),
    ("Private Endpoints",       private_endpoint.harvest),
    # ── Observability ────────────────────────────────────────────────────
    ("App Insights",            app_insights.harvest),
    ("Log Analytics Workspaces", log_analytics_workspace.harvest),
    ("Monitor Action Groups",    monitor_action_group.harvest),
    ("Activity Log Alerts",      activity_log_alert.harvest),
    # ── Networking ───────────────────────────────────────────────────────
    ("Virtual Networks",        virtual_network.harvest),
    ("Network Security Groups",  network_security_group.harvest),
    ("Route Tables",            route_table.harvest),
    ("Public IPs",              public_ip.harvest),
    ("Load Balancers",          load_balancer.harvest),
    ("Bastion Hosts",           bastion_host.harvest),
    ("Firewalls",               firewall.harvest),
    # ── Search / Kusto ───────────────────────────────────────────────────
    ("Search Services",         search_service.harvest),
    ("Kusto Clusters",          kusto.harvest),
]

HarvestOutput = list[dict[str, Any]] | StagedRows
ProviderFn = Callable[..., HarvestOutput]
ProgressCallback = Callable[[str], None]
_MAX_PROVIDER_WORKERS = 6
_PROGRESS_REFRESH_SECONDS = 10.0
_PROVIDER_WRITE_CHUNK = 250


def _normalize_provider_filters(raw_filters: list[str] | None) -> list[str]:
    filters: list[str] = []
    for raw in raw_filters or []:
        for value in str(raw).split(","):
            cleaned = value.strip()
            if cleaned:
                filters.append(cleaned)
    return filters


def _select_provider_specs(provider_filters: list[str] | None) -> list[tuple[str, ProviderFn]]:
    provider_specs = list(PROVIDERS)
    filters = _normalize_provider_filters(provider_filters)
    if not filters:
        return provider_specs

    available = {label.lower(): (label, fn) for label, fn in provider_specs}
    selected: list[tuple[str, ProviderFn]] = []
    missing: list[str] = []
    for raw in filters:
        match = available.get(raw.lower())
        if not match:
            missing.append(raw)
            continue
        if match not in selected:
            selected.append(match)

    if missing:
        available_labels = ", ".join(label for label, _ in provider_specs)
        raise ValueError(f"Unknown provider label(s): {', '.join(missing)}. Available labels: {available_labels}")

    return selected


@dataclass
class _ProviderState:
    label: str
    state: str = "queued"
    detail: str = ""
    started_at: float | None = None
    finished_at: float | None = None


class HarvestProgress:
    """Render progress for the parallel resource-type harvest queue."""

    def __init__(self, labels: list[str]) -> None:
        self._labels = labels
        self._states = {label: _ProviderState(label=label) for label in labels}
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._last_render = 0.0

    def mark_running(self, label: str, detail: str = "fetching inventory") -> None:
        with self._lock:
            state = self._states[label]
            state.state = "running"
            state.detail = detail
            if state.started_at is None:
                state.started_at = time.monotonic()

    def update(self, label: str, detail: str) -> None:
        with self._lock:
            self._states[label].detail = detail

    def mark_done(self, label: str, detail: str = "") -> None:
        with self._lock:
            state = self._states[label]
            state.state = "done"
            state.detail = detail
            if state.started_at is None:
                state.started_at = time.monotonic()
            state.finished_at = time.monotonic()

    def mark_failed(self, label: str, detail: str) -> None:
        with self._lock:
            state = self._states[label]
            state.state = "failed"
            state.detail = detail
            if state.started_at is None:
                state.started_at = time.monotonic()
            state.finished_at = time.monotonic()

    def render(self, force: bool = False) -> None:
        if not self._labels:
            return

        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_render < 1.0:
                return

            states = [self._states[label] for label in self._labels]
            self._last_render = now

        total = len(states)
        completed = sum(1 for state in states if state.state in {"done", "failed"})
        running = sum(1 for state in states if state.state == "running")
        queued = total - completed - running
        failed = sum(1 for state in states if state.state == "failed")
        percent = int((completed / total) * 100) if total else 100
        bar = _format_progress_bar(completed, total)
        elapsed = _format_duration(now - self._started_at)
        print(
            f"[harvest] provider progress {completed}/{total} {bar} {percent}% "
            f"| running={running} queued={queued} failed={failed} | elapsed {elapsed}",
            flush=True,
        )

        label_width = max(len(state.label) for state in states)
        for state in states:
            print(
                f"  {state.label.ljust(label_width)}  {_format_provider_state(state, now)}",
                flush=True,
            )


def _format_progress_bar(completed: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return f"[{'#' * width}]"
    filled = min(width, max(0, int((completed / total) * width)))
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_provider_state(state: _ProviderState, now: float) -> str:
    bar = _format_provider_progress_bar(state, now)
    detail = f" — {state.detail}" if state.detail else ""
    if state.state == "queued":
        return f"{bar} queued"

    started_at = state.started_at or now
    if state.state == "running":
        return f"{bar} running {_format_duration(now - started_at)}{detail}"

    finished_at = state.finished_at or now
    elapsed = _format_duration(finished_at - started_at)
    status = "done" if state.state == "done" else "FAILED"
    return f"{bar} {status} {elapsed}{detail}"


def _format_provider_progress_bar(state: _ProviderState, now: float, width: int = 24) -> str:
    if width <= 0:
        return "[]"

    if state.state == "done":
        return _format_progress_bar(1, 1, width=width)

    if state.state == "failed":
        return f"[{'x' * width}]"

    if state.state == "running":
        started_at = state.started_at or now
        span = max(1, width - 4)
        tick = int((now - started_at) * 4)
        offset = sum(ord(ch) for ch in state.label)
        start = (tick + offset) % span
        segment = min(5, width)
        bar = ["-"] * width
        for idx in range(segment):
            pos = start + idx
            if pos >= width:
                break
            bar[pos] = "="
        end = min(width - 1, start + segment - 1)
        bar[end] = ">"
        return f"[{''.join(bar)}]"

    return _format_progress_bar(0, 1, width=width)


def _supports_progress(provider_fn: ProviderFn) -> bool:
    try:
        signature = inspect.signature(provider_fn)
    except (TypeError, ValueError):
        return False
    if "progress" in signature.parameters:
        return True
    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())


def _supports_stage_backfill(provider_fn: ProviderFn) -> bool:
    try:
        signature = inspect.signature(provider_fn)
    except (TypeError, ValueError):
        return False
    if "stage_backfill" in signature.parameters:
        return True
    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())


def _invoke_provider(
    provider_fn: ProviderFn,
    sub_id: str,
    progress: ProgressCallback | None = None,
    *,
    stage_backfill: bool = False,
) -> HarvestOutput:
    kwargs: dict[str, Any] = {}
    if progress and _supports_progress(provider_fn):
        kwargs["progress"] = progress
    if stage_backfill and _supports_stage_backfill(provider_fn):
        kwargs["stage_backfill"] = True
    if kwargs:
        return provider_fn(sub_id, **kwargs)
    return provider_fn(sub_id)


def _run_provider_task(
    label: str,
    provider_fn: ProviderFn,
    sub_id: str,
    progress: HarvestProgress,
) -> HarvestOutput:
    progress.mark_running(label)
    progress_cb: ProgressCallback | None = (
        (lambda detail, _label=label: progress.update(_label, detail))
        if label == "Storage"
        else None
    )
    return _invoke_provider(
        provider_fn,
        sub_id,
        progress_cb,
        stage_backfill=(label == "Storage"),
    )


def _normalize_rows(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, StagedRows):
        return result.core_rows
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [row for row in result if row is not None]
    raise TypeError(f"unexpected harvest result type: {type(result)!r}")


def _persist_rows(
    conn: sqlite3.Connection,
    dry_run: bool,
    rows: list[dict[str, Any]],
    seen_ids: set[str],
) -> int:
    count = 0
    for asset in rows:
        asset_id = asset["id"]
        if asset_id in seen_ids:
            continue
        seen_ids.add(asset_id)
        count += 1
        if not dry_run:
            upsert_asset(conn, asset)
    return count


def _drain_ready_backfill_jobs(
    jobs: list[BackfillJob],
    conn: sqlite3.Connection,
    dry_run: bool,
    seen_ids: set[str],
) -> int:
    processed = 0
    remaining: list[BackfillJob] = []
    for job in jobs:
        if not job.future.done():
            remaining.append(job)
            continue
        try:
            result = job.future.result()
        except Exception as exc:
            print(f"  [backfill] {job.label} FAILED ({exc})")
            continue
        processed += _persist_rows(conn, dry_run, _normalize_rows(result), seen_ids)
    jobs[:] = remaining
    return processed


def _flush_backfill_jobs(
    jobs: list[BackfillJob],
    conn: sqlite3.Connection,
    dry_run: bool,
    seen_ids: set[str],
) -> int:
    processed = 0
    pending = {job.future: job for job in jobs}
    while pending:
        done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
        for future in done:
            job = pending.pop(future)
            try:
                result = future.result()
            except Exception as exc:
                print(f"  [backfill] {job.label} FAILED ({exc})")
                continue
            processed += _persist_rows(conn, dry_run, _normalize_rows(result), seen_ids)
    jobs.clear()
    return processed


# ---------------------------------------------------------------------------
# Prerequisites check
# ---------------------------------------------------------------------------

def check_prerequisites() -> bool:
    """Verify az CLI is installed and the user is logged in. Returns True if OK."""
    ok = True

    # 1. Python version
    if sys.version_info < (3, 9):
        print(f"[prereq] ✗ Python 3.9+ required (running {sys.version.split()[0]})", file=sys.stderr)
        ok = False
    else:
        print(f"[prereq] ✓ Python {sys.version.split()[0]}")

    # 2. Azure CLI installed
    az_path = shutil.which("az")
    if not az_path:
        print("[prereq] ✗ Azure CLI not found in PATH.", file=sys.stderr)
        print("         Install from: https://aka.ms/installazurecli", file=sys.stderr)
        ok = False
    else:
        try:
            ver = subprocess.run(
                ["az", "version", "--output", "json"],
                capture_output=True, text=True, timeout=15,
            )
            az_ver = json.loads(ver.stdout or "{}").get("azure-cli", "unknown")
            print(f"[prereq] ✓ Azure CLI {az_ver} ({az_path})")
        except Exception:
            print(f"[prereq] ✓ Azure CLI found ({az_path})")

    if not ok:
        return False

    # 3. Logged in to Azure
    login_check = subprocess.run(
        ["az", "account", "show", "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if login_check.returncode != 0:
        print("[prereq] ✗ Not logged in to Azure.", file=sys.stderr)
        print("         Run: az login", file=sys.stderr)
        print("         Or for service principal: az login --service-principal -u <appId> -p <password> --tenant <tenant>", file=sys.stderr)
        return False

    try:
        account = json.loads(login_check.stdout)
        print(f"[prereq] ✓ Logged in as: {account.get('user', {}).get('name', 'unknown')} "
              f"(tenant: {account.get('tenantId', '?')})")
    except Exception:
        print("[prereq] ✓ Logged in to Azure")

    return True


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

def list_subscriptions() -> list[dict[str, Any]]:
    """Return all accessible subscriptions via az account list."""
    result = subprocess.run(
        ["az", "account", "list", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"[error] az account list failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    return json.loads(result.stdout or "[]") or []


def resolve_subscription(name_or_id: str, all_subs: list[dict]) -> dict | None:
    """Find a subscription by display name or GUID (case-insensitive)."""
    needle = name_or_id.lower()
    for sub in all_subs:
        if sub.get("id", "").lower() == needle:
            return sub
        if sub.get("name", "").lower() == needle:
            return sub
    return None


def infer_environment(display_name: str) -> str:
    name = display_name.lower()
    if any(k in name for k in ("prod", "production", "live")):
        return "prod"
    if any(k in name for k in ("staging", "stage", "sim", "uat", "preprod")):
        return "staging"
    if any(k in name for k in ("dev", "develop", "sandbox", "test")):
        return "dev"
    if any(k in name for k in ("shared", "platform", "infra", "core", "hub")):
        return "shared"
    return "unknown"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def upsert_subscription(conn: sqlite3.Connection, sub: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO subscriptions (id, display_name, tenant_id, environment, state, last_synced)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name = excluded.display_name,
            tenant_id    = excluded.tenant_id,
            environment  = excluded.environment,
            state        = excluded.state,
            last_synced  = excluded.last_synced
        """,
        (
            sub["id"],
            sub.get("name") or sub.get("displayName"),
            sub.get("tenantId"),
            infer_environment(sub.get("name") or sub.get("displayName") or ""),
            sub.get("state", "Enabled"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def upsert_asset(conn: sqlite3.Connection, asset: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO provisioned_assets
            (id, subscription_id, resource_group, name, type, location, sku,
             tags, is_public, fqdn, pipeline_tag, raw_json, first_detected, last_synced, status,
             is_restricted, ip_restrictions, endpoints, auth_methods, waf_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            subscription_id = excluded.subscription_id,
            resource_group  = excluded.resource_group,
            name            = excluded.name,
            type            = excluded.type,
            location        = excluded.location,
            sku             = excluded.sku,
            tags            = excluded.tags,
            is_public       = excluded.is_public,
            fqdn            = excluded.fqdn,
            pipeline_tag    = excluded.pipeline_tag,
            raw_json        = excluded.raw_json,
            last_synced     = excluded.last_synced,
            status          = 'active',
            first_detected  = COALESCE(provisioned_assets.first_detected, excluded.first_detected),
            is_restricted   = excluded.is_restricted,
            ip_restrictions = excluded.ip_restrictions,
            endpoints       = excluded.endpoints,
            auth_methods    = excluded.auth_methods,
            waf_mode        = excluded.waf_mode
        """,
        (
            asset["id"],
            asset.get("subscription_id"),
            asset.get("resource_group"),
            asset.get("name"),
            asset.get("type"),
            asset.get("location"),
            asset.get("sku"),
            asset.get("tags"),
            asset.get("is_public", 0),
            asset.get("fqdn"),
            asset.get("pipeline_tag"),
            asset.get("raw_json"),
            now,  # first_detected — preserved by COALESCE on conflict
            now,  # last_synced
            asset.get("is_restricted", 0),
            asset.get("ip_restrictions"),
            asset.get("endpoints"),
            asset.get("auth_methods"),
            asset.get("waf_mode"),
        ),
    )


# ---------------------------------------------------------------------------
# Stale asset detection
# ---------------------------------------------------------------------------

def mark_stale_assets(
    conn: sqlite3.Connection,
    sub_id: str,
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    """Mark assets not seen in this harvest run as potentially_removed.

    Returns the list of newly-stale assets for reporting.
    """
    # Find active assets for this subscription that were NOT in this harvest
    rows = conn.execute(
        """
        SELECT id, name, type, resource_group, first_detected, last_synced
        FROM provisioned_assets
        WHERE subscription_id = ? AND status = 'active'
        """,
        (sub_id,),
    ).fetchall()

    stale = []
    for row in rows:
        if row[0] not in seen_ids:
            conn.execute(
                "UPDATE provisioned_assets SET status = 'potentially_removed' WHERE id = ?",
                (row[0],),
            )
            stale.append({
                "id": row[0], "name": row[1], "type": row[2],
                "resource_group": row[3], "first_detected": row[4], "last_synced": row[5],
            })

    return stale


# ---------------------------------------------------------------------------
# Harvest one subscription
# ---------------------------------------------------------------------------

def harvest_subscription(
    sub: dict[str, Any],
    conn: sqlite3.Connection,
    dry_run: bool = False,
    provider_filters: list[str] | None = None,
    skip_post_harvest: bool = False,
) -> int:
    sub_id = sub["id"]
    sub_name = sub.get("name") or sub.get("displayName") or sub_id
    print(f"\n[subscription] {sub_name} ({sub_id})")

    if not dry_run:
        upsert_subscription(conn, sub)

    seen_ids: set[str] = set()
    pending_backfill_jobs: list[BackfillJob] = []
    total = 0
    provider_specs = _select_provider_specs(provider_filters)

    def _store_result(result: HarvestOutput) -> tuple[int, int]:
        nonlocal total
        if isinstance(result, StagedRows):
            core_count = _persist_rows(conn, dry_run, result.core_rows, seen_ids)
            total += core_count
            pending_backfill_jobs.extend(result.backfill_jobs)
            return core_count, len(result.backfill_jobs)

        rows = _normalize_rows(result)
        row_count = _persist_rows(conn, dry_run, rows, seen_ids)
        total += row_count
        return row_count, 0

    if provider_specs:
        print(
            f"  [providers] harvesting {len(provider_specs)} resource types "
            f"in parallel ({min(_MAX_PROVIDER_WORKERS, len(provider_specs))} workers)...",
            flush=True,
        )
        progress = HarvestProgress([label for label, _ in provider_specs])
        future_map: dict[Any, str] = {}

        with ThreadPoolExecutor(max_workers=min(_MAX_PROVIDER_WORKERS, len(provider_specs))) as pool:
            for label, provider_fn in provider_specs:
                future = pool.submit(_run_provider_task, label, provider_fn, sub_id, progress)
                future_map[future] = label

            progress.render(force=True)
            pending = set(future_map)
            while pending:
                done, pending = wait(pending, timeout=_PROGRESS_REFRESH_SECONDS, return_when=FIRST_COMPLETED)
                if not done:
                    processed_backfills = _drain_ready_backfill_jobs(pending_backfill_jobs, conn, dry_run, seen_ids)
                    total += processed_backfills
                    if processed_backfills and not dry_run:
                        conn.commit()
                    progress.render(force=True)
                    continue

                for future in done:
                    label = future_map[future]
                    try:
                        assets = future.result()
                    except Exception as exc:
                        progress.mark_failed(label, str(exc))
                        progress.render(force=True)
                        continue

                    asset_count, backfill_count = _store_result(assets)

                    processed_backfills = _drain_ready_backfill_jobs(pending_backfill_jobs, conn, dry_run, seen_ids)
                    total += processed_backfills
                    if processed_backfills and not dry_run:
                        conn.commit()

                    if dry_run:
                        if backfill_count:
                            progress.mark_done(label, f"{asset_count} core assets, {backfill_count} backfills (dry-run)")
                        else:
                            progress.mark_done(label, f"{asset_count} assets (dry-run)")
                        progress.render(force=True)
                        continue

                    if asset_count:
                        progress.update(label, f"writing {asset_count} assets")
                        progress.render(force=True)
                        conn.commit()

                    if backfill_count:
                        progress.mark_done(label, f"{asset_count} core assets, {backfill_count} backfills")
                    else:
                        progress.mark_done(label, f"{asset_count} assets")
                    progress.render(force=True)
    else:
        print("  [providers] no parallel harvesters configured")

    if skip_post_harvest:
        print("  [post-harvest] skipped by request")
    else:
        # App Gateway routing + rewrites + WAF — runs after assets so fqdn_to_asset lookup is populated
        print(f"  [App Gateway Routing] harvesting listeners, routing rules, rewrite rules & WAF policies...", flush=True)
        try:
            rules, rewrite_sets, rewrite_rules, waf = appgw_routing_map.harvest_routing(sub_id, conn, dry_run=dry_run)
            action = "would write" if dry_run else "written"
            print(
                f"  [App Gateway Routing] {rules} routing rules, "
                f"{rewrite_sets} rewrite rule sets ({rewrite_rules} rewrite rules), "
                f"{waf} WAF policies {action}"
            )
        except Exception as exc:
            print(f"  [App Gateway Routing] FAILED ({exc})")

        # Private DNS zones/records for internal host resolution coverage
        print(f"  [Private DNS] harvesting zones, records, and VNet links...", flush=True)
        try:
            dns_summary = private_dns_map.harvest_private_dns(sub_id, conn, dry_run=dry_run)
            action = "would harvest" if dry_run else "written"
            print(
                f"  [Private DNS] {dns_summary.get('zones', 0)} zones, "
                f"{dns_summary.get('records', 0)} records {action}"
            )
        except Exception as exc:
            print(f"  [Private DNS] FAILED ({exc})")

        # AKS ingress → service → deployment route model
        print(f"  [AKS Routes] harvesting ingress→service→deployment mappings...", flush=True)
        try:
            route_count = aks.harvest_routes(sub_id, conn, dry_run=dry_run)
            action = "would harvest" if dry_run else "written"
            print(f"  [AKS Routes] {route_count} routes {action}")
        except Exception as exc:
            print(f"  [AKS Routes] FAILED ({exc})")

        # APIM API → backend routes (staged backfill: operations enrich asynchronously)
        print(f"  [APIM Routes] harvesting API→backend mappings...", flush=True)
        try:
            phase_started = time.perf_counter()
            result = apim.harvest_routes(sub_id, conn, dry_run=dry_run, stage_backfill=True)
            asset_count, backfill_count = _store_result(result)
            action = "would write" if dry_run else "written"
            print(f"  [APIM Routes] {asset_count} routes {action}, {backfill_count} operation(s) queued for backfill in {time.perf_counter() - phase_started:.2f}s")
        except Exception as exc:
            print(f"  [APIM Routes] FAILED ({exc})")

        # APIM backend inventory + API-to-backend links
        print(f"  [APIM Backend Links] harvesting backend inventory and route links...", flush=True)
        try:
            phase_started = time.perf_counter()
            backend_count, link_count = apim_routing_map.harvest_backends(sub_id, conn, dry_run=dry_run)
            action = "would write" if dry_run else "written"
            print(f"  [APIM Backend Links] {backend_count} backends, {link_count} links {action} in {time.perf_counter() - phase_started:.2f}s")
        except Exception as exc:
            print(f"  [APIM Backend Links] FAILED ({exc})")

        # Function App HTTP triggers
        print(f"  [Function App Triggers] harvesting HTTP trigger routes...", flush=True)
        try:
            trigger_count = function_apps.harvest_http_triggers(sub_id, conn, dry_run=dry_run)
            action = "would write" if dry_run else "written"
            print(f"  [Function App Triggers] {trigger_count} triggers {action}")
        except Exception as exc:
            print(f"  [Function App Triggers] FAILED ({exc})")

    # Front Door routing rules
    print(f"  [Front Door Routes] harvesting routing rules...", flush=True)
    try:
        fd_count = front_door.harvest_routes(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Front Door Routes] {fd_count} routes {action}")
    except Exception as exc:
        print(f"  [Front Door Routes] FAILED ({exc})")

    # Azure Firewall NAT + app rules
    print(f"  [Firewall Rules] harvesting NAT and application rules...", flush=True)
    try:
        nat_count, app_count = firewall.harvest_rules(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Firewall Rules] {nat_count} NAT rules, {app_count} app rules {action}")
    except Exception as exc:
        print(f"  [Firewall Rules] FAILED ({exc})")

    print(f"  [Firewall Policies] harvesting policy summaries...", flush=True)
    try:
        policy_count = firewall.harvest_policies(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Firewall Policies] {policy_count} policies {action}")
    except Exception as exc:
        print(f"  [Firewall Policies] FAILED ({exc})")

    processed_backfills = _flush_backfill_jobs(pending_backfill_jobs, conn, dry_run, seen_ids)
    total += processed_backfills
    if processed_backfills and not dry_run:
        conn.commit()

    if not dry_run and seen_ids:
        stale = mark_stale_assets(conn, sub_id, seen_ids)
        conn.commit()
        if stale:
            print(f"\n  ⚠ {len(stale)} asset(s) not seen this run — marked as 'potentially_removed':")
            for a in stale:
                print(f"    - {a['type']}/{a['name']} (rg: {a['resource_group']}, last seen: {a['last_synced']})")
            print("  To confirm removal:  UPDATE provisioned_assets SET status='removed' WHERE id='<id>';")
            print("  To restore (false positive):  UPDATE provisioned_assets SET status='active' WHERE id='<id>';")

    print(f"  [total] {total} assets for {sub_name}")
    return total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest live Azure cloud assets into Triage-Saurus cozo.db"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID", help="Subscription name or GUID to harvest")
    group.add_argument("--all", action="store_true", dest="all_subs", help="Harvest all accessible subscriptions")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be harvested without writing to DB")
    parser.add_argument("--skip-prereq-check", action="store_true", help="Skip prerequisites check")
    parser.add_argument("--skip-probes", action="store_true",
                        help="Skip active connectivity probes (faster, no network connections made)")
    parser.add_argument(
        "--skip-storage-blobs",
        action="store_true",
        help="Skip blob-object enumeration under storage containers (faster on large subscriptions)",
    )
    parser.add_argument(
        "--components",
        "--providers",
        dest="provider_filters",
        action="append",
        metavar="LABEL",
        help="Harvest only selected provider groups. Repeat the flag or pass a comma-separated list.",
    )
    parser.add_argument(
        "--skip-post-harvest",
        action="store_true",
        help="Skip post-harvest correlation steps such as routing, DNS, AKS, APIM, and trigger enrichment",
    )
    args = parser.parse_args()

    set_probe_enabled(not args.skip_probes)
    storage.set_include_blob_children(not args.skip_storage_blobs)

    if not args.skip_prereq_check:
        print("[prereq] Checking prerequisites...")
        if not check_prerequisites():
            sys.exit(1)
        print()

    print("[harvest] Listing accessible Azure subscriptions...")
    all_subs = list_subscriptions()
    if not all_subs:
        print("[error] No subscriptions found. Make sure you are logged in: az login", file=sys.stderr)
        sys.exit(1)

    if args.all_subs:
        target_subs = all_subs
    else:
        sub = resolve_subscription(args.subscription, all_subs)
        if not sub:
            names = [s.get("name") for s in all_subs]
            print(f"[error] Subscription '{args.subscription}' not found. Available: {names}", file=sys.stderr)
            sys.exit(1)
        target_subs = [sub]

    db_path = REPO_ROOT / "Output" / "Data" / "cozo.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)

    grand_total = 0
    provider_filters = _normalize_provider_filters(args.provider_filters)
    for sub in target_subs:
        try:
            grand_total += harvest_subscription(
                sub,
                conn,
                dry_run=args.dry_run,
                provider_filters=provider_filters,
                skip_post_harvest=args.skip_post_harvest,
            )
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

    conn.close()
    print(f"\n[harvest] Done. {grand_total} assets across {len(target_subs)} subscription(s).")
    if not args.dry_run:
        print(f"[harvest] Stored in: {db_path}")


if __name__ == "__main__":
    main()
