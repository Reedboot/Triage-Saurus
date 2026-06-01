"""Harvest Azure Traffic Manager profiles."""
from __future__ import annotations

import json
import socket
import time
from typing import Any

from ._helpers import az, build_endpoints, safe_str, _PROBES_ENABLED

RESOURCE_TYPE = "Microsoft.Network/trafficmanagerprofiles"

# Per-endpoint probe timeout (seconds). Kept short to avoid slowing harvests.
_EP_PROBE_TIMEOUT = 4


def _probe_tm_endpoint(target: str, probe_cache: dict[str, dict]) -> dict[str, Any]:
    """DNS + optional HTTPS probe for a single TM backend target.

    Results are cached by target within a harvest run so shared App Gateways
    (multiple TM profiles pointing to the same host) are only probed once.
    """
    if target in probe_cache:
        return probe_cache[target]

    result: dict[str, Any] = {
        "dns_resolvable": False,
        "resolved_ip": None,
        "tcp_reachable": None,
        "tls_ok": None,
        "http_status": None,
        "probe_error": None,
    }

    # DNS probe
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_EP_PROBE_TIMEOUT)
        try:
            t0 = time.monotonic()
            infos = socket.getaddrinfo(target, None)
            result["dns_resolvable"] = bool(infos)
            result["resolved_ip"] = infos[0][4][0] if infos else None
            result["dns_latency_ms"] = int((time.monotonic() - t0) * 1000)
        finally:
            socket.setdefaulttimeout(old_timeout)
    except socket.gaierror as exc:
        result["probe_error"] = f"dns:{exc.args[1] if exc.args else exc}"
        probe_cache[target] = result
        return result

    if not result["dns_resolvable"]:
        probe_cache[target] = result
        return result

    # TCP + TLS + HTTP probe on port 443 (best-effort — 4xx/5xx still confirms reachability)
    import ssl
    import urllib.request

    try:
        t0 = time.monotonic()
        with socket.create_connection((target, 443), timeout=_EP_PROBE_TIMEOUT) as sock:
            result["tcp_reachable"] = True
            result["tcp_latency_ms"] = int((time.monotonic() - t0) * 1000)
            ctx = ssl.create_default_context()
            try:
                with ctx.wrap_socket(sock, server_hostname=target):
                    result["tls_ok"] = True
            except ssl.SSLError as tls_err:
                result["tls_ok"] = False
                result["probe_error"] = f"tls:{tls_err.reason}"
    except (socket.timeout, OSError) as exc:
        result["tcp_reachable"] = False
        result["probe_error"] = f"tcp:{exc}"

    # HTTP status (non-200 still confirms listener is up)
    if result["tcp_reachable"]:
        url = f"https://{target}/"
        req = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "Triage-Saurus-Harvest/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_EP_PROBE_TIMEOUT) as resp:
                result["http_status"] = resp.status
        except urllib.error.HTTPError as exc:
            result["http_status"] = exc.code
        except Exception:
            pass

    probe_cache[target] = result
    return result


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["network", "traffic-manager", "profile", "list"], subscription_id)
    results = []

    # Cache probe results per target so shared App Gateways are only probed once
    probe_cache: dict[str, dict] = {}

    for profile in raw:
        # az CLI returns a flattened shape (no nested "properties" wrapper)
        dns_config = profile.get("dnsConfig") or {}
        fqdn = safe_str(dns_config.get("fqdn"))
        profile_enabled = (profile.get("profileStatus") or "").lower() == "enabled"

        # Probe the TM's own FQDN (DNS-level routing entry point)
        tm_endpoints = build_endpoints([(fqdn, None, "dns")] if fqdn else [])

        raw_endpoints = profile.get("endpoints") or []

        # Probe each enabled backend endpoint target
        enriched_endpoints = []
        for ep in raw_endpoints:
            target = safe_str(ep.get("target"))
            ep_status = (ep.get("endpointStatus") or "").lower()
            entry: dict[str, Any] = {
                "name": ep.get("name"),
                "target": target,
                "target_resource_id": ep.get("targetResourceId"),
                "weight": ep.get("weight"),
                "priority": ep.get("priority"),
                "endpoint_status": ep.get("endpointStatus"),
                "probe": None,
            }
            if target and ep_status == "enabled" and _PROBES_ENABLED:
                entry["probe"] = _probe_tm_endpoint(target, probe_cache)
            enriched_endpoints.append(entry)

        monitor_config = profile.get("monitorConfig") or {}
        inbound_traffic_type = "dns"
        extra = {
            "inbound_traffic_type": inbound_traffic_type,
            "routing_method": profile.get("trafficRoutingMethod"),
            "profile_status": profile.get("profileStatus"),
            "dns_ttl": dns_config.get("ttl"),
            "fqdn": fqdn,
            "monitor_protocol": monitor_config.get("protocol"),
            "monitor_port": monitor_config.get("port"),
            "monitor_path": monitor_config.get("path"),
            "endpoint_count": len(raw_endpoints),
            "endpoints": enriched_endpoints,
            "routing_targets": [
                {
                    "name": ep.get("name"),
                    "target": safe_str(ep.get("target")),
                    "target_resource_id": ep.get("targetResourceId"),
                    "weight": ep.get("weight"),
                    "priority": ep.get("priority"),
                    "endpoint_status": ep.get("endpointStatus"),
                }
                for ep in raw_endpoints
            ],
        }

        # is_public is config-based: TM profiles with an FQDN are DNS-publicly advertised.
        # Actual reachability is captured in the endpoint probe results above.
        is_public = 1 if (fqdn and profile_enabled) else 0

        results.append({
            "id": profile["id"],
            "subscription_id": subscription_id,
            "resource_group": profile.get("resourceGroup"),
            "name": profile.get("name"),
            "type": profile.get("type", RESOURCE_TYPE),
            "location": profile.get("location"),
            "sku": None,
            "tags": json.dumps(profile.get("tags") or {}),
            "is_public": is_public,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": tm_endpoints,
            "auth_methods": json.dumps([]),
            "fqdn": fqdn,
            "pipeline_tag": (profile.get("tags") or {}).get("pipeline") or (profile.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**profile, "_extra": extra}),
        })

    return results
