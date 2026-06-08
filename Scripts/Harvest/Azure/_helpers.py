"""Shared helpers used by all harvest provider modules."""
from __future__ import annotations

import json
import os
import ipaddress
import re
import socket
import ssl
import subprocess
import signal
import time
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

# ---------------------------------------------------------------------------
# Global probe toggle — set by harvest_azure_assets.py via set_probe_enabled()
# ---------------------------------------------------------------------------
_PROBES_ENABLED: bool = True

# ---------------------------------------------------------------------------
# Az CLI retry configuration
# ---------------------------------------------------------------------------
# The Windows MSAL token cache uses a lockfile that multiple concurrent az
# subprocesses (running from WSL) can race to acquire.  When one loses the
# race the CLI exits non-zero with "Permission denied: ...lockfile".
# Retrying with a short back-off resolves the contention in practice.
_AZ_RETRY_MAX = 3
_AZ_RETRY_BACKOFF = 1.0  # seconds; multiplied by attempt number (1×, 2×, 3×)
_MSAL_LOCK_RE = re.compile(
    r"msal_token_cache.*\.lockfile|Permission denied.*lockfile",
    re.IGNORECASE,
)


def _is_msal_lock_error(stderr: str) -> bool:
    """Return True when az CLI failed due to MSAL token-cache lock contention."""
    return bool(_MSAL_LOCK_RE.search(stderr))


def set_probe_enabled(enabled: bool) -> None:
    global _PROBES_ENABLED
    _PROBES_ENABLED = enabled


def az(args: list[str], subscription_id: str) -> list[dict[str, Any]]:
    """Run an az CLI command scoped to a subscription and return parsed JSON.

    Returns an empty list on failure (e.g. no permission, resource type not
    registered in the subscription) so providers degrade gracefully.
    Retries up to _AZ_RETRY_MAX times when the MSAL token cache is locked.
    """
    cmd = ["az"] + args + ["--subscription", subscription_id, "--output", "json"]
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
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            return []

        if proc.returncode == 0:
            try:
                return json.loads(stdout or "[]") or []
            except Exception:
                return []

        if attempt < _AZ_RETRY_MAX - 1 and _is_msal_lock_error(stderr):
            time.sleep(_AZ_RETRY_BACKOFF * (attempt + 1))
            continue
        return []
    return []


def _az_rest(url: str, resource: str | None = None) -> dict:
    """Call az rest GET and return parsed JSON. Raises RuntimeError on failure.

    Retries up to _AZ_RETRY_MAX times when the MSAL token cache is locked.
    """
    cmd = ["az", "rest", "--method", "GET", "--url", url, "--output", "json"]
    if resource:
        cmd += ["--resource", resource]
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
            stdout, stderr = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise RuntimeError(f"az rest timed out after 60s: {url}") from exc

        if proc.returncode == 0:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                preview = (stdout or "").replace("\n", " ")[:200]
                raise RuntimeError(f"az rest returned invalid JSON: {exc.msg}; output={preview!r}") from exc

        last_stderr = stderr.strip()
        if attempt < _AZ_RETRY_MAX - 1 and _is_msal_lock_error(last_stderr):
            time.sleep(_AZ_RETRY_BACKOFF * (attempt + 1))
            continue
        raise RuntimeError(last_stderr[:200])
    raise RuntimeError(last_stderr[:200])


def safe_str(value: Any) -> str | None:
    """Coerce a value to string, returning None for empty/null."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def infer_fqdn(resource: dict[str, Any]) -> str | None:
    """Extract the most useful public FQDN from a raw Azure resource dict."""
    props = resource.get("properties") or {}

    for key in (
        "defaultHostName",
        "hostNames",
        "gatewayIpConfigurations",
        "hostname",
        "fqdn",
        "publicIpAddress",
    ):
        val = props.get(key)
        if isinstance(val, list) and val:
            return safe_str(val[0])
        if isinstance(val, str) and val:
            return safe_str(val)

    # AKS: ingress hostnames surface under addonProfiles or fqdn field
    fqdn = props.get("fqdn")
    if fqdn:
        return safe_str(fqdn)

    return None


def infer_sku(resource: dict[str, Any]) -> str | None:
    sku = resource.get("sku") or {}
    if isinstance(sku, dict):
        name = sku.get("name") or sku.get("tier")
        return safe_str(name)
    return safe_str(sku)


def normalize_host_key(host_name: str | None) -> str | None:
    """Normalize a host or host URI to a stable lowercase lookup key."""
    host_value = safe_str(host_name)
    if not host_value:
        return None

    if re.match(r"^[a-z][a-z0-9+.-]*://", host_value):
        try:
            parsed = urlparse(host_value)
        except ValueError:
            return host_value.rstrip(".").lower()
        host_value = safe_str(parsed.hostname or parsed.netloc or parsed.path) or host_value

    return host_value.rstrip(".").lower()


def normalize_route_path(path: str | None) -> str | None:
    """Normalize route paths for stable matching and deduplication."""
    route_path = safe_str(path)
    if not route_path:
        return None

    route_path = route_path.strip()
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    if len(route_path) > 1:
        route_path = route_path.rstrip("/")
    return route_path


def route_path_matches(path_pattern: str | None, route_path: str | None) -> bool:
    """Return True when a route path matches a wildcard/template pattern."""
    normalized_pattern = normalize_route_path(path_pattern)
    normalized_path = normalize_route_path(route_path)

    if not normalized_pattern or not normalized_path:
        return True

    normalized_pattern = re.sub(r"\{[^/{}]+\}", "*", normalized_pattern)
    if normalized_pattern == "/*":
        return True

    if normalized_pattern.endswith("*"):
        path_prefix = normalize_route_path(normalized_pattern.rstrip("*"))
        if not path_prefix:
            return True
        path_prefix = path_prefix.rstrip("/")
        return normalized_path == path_prefix or normalized_path.startswith(f"{path_prefix}/")

    return normalized_path == normalized_pattern


def classify_host_alias_exposure(host_aliases: list[str] | None) -> str:
    """Classify ingress/LB host aliases as Public, Internal, or Unknown."""
    aliases = [safe_str(alias) for alias in (host_aliases or [])]
    aliases = [alias for alias in aliases if alias]
    if not aliases:
        return "Unknown"

    saw_public = False
    saw_internal = False
    internal_keywords = (
        "internal",
        "privatelink",
        ".local",
        ".cluster.local",
        ".svc",
    )

    for alias in aliases:
        normalized_alias = normalize_host_key(alias) or alias.strip().lower()
        try:
            ip = ipaddress.ip_address(normalized_alias)
        except ValueError:
            if any(keyword in normalized_alias for keyword in internal_keywords):
                saw_internal = True
            else:
                saw_public = True
            continue

        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            saw_internal = True
        else:
            saw_public = True

    if saw_public:
        return "Public"
    if saw_internal:
        return "Internal"
    return "Unknown"

# ---------------------------------------------------------------------------
# Exposure helpers
# ---------------------------------------------------------------------------

def fetch_ase_ilb_map(subscription_id: str) -> dict[str, bool]:
    """Return a map of ASE resource ID (lowercased) → web_is_internal.

    A True value means the ASE's HTTP/S endpoint is served through an internal
    load balancer (ILB) and is therefore not internet-accessible.  This covers
    ``internalLoadBalancingMode`` values of ``"Web"`` and ``"Web, Publishing"``.

    An ``internalLoadBalancingMode`` of ``"Publishing"`` only internalises the
    SCM/Kudu endpoint; the web endpoint is still publicly reachable, so those
    ASEs map to False.

    Returns an empty dict if the ASE list cannot be retrieved (e.g. permissions
    or the resource provider is not registered).  Callers must treat a missing
    key as "unknown" and fall through to their existing exposure checks rather
    than assuming the app is public or private.
    """
    environments = az(["appservice", "ase", "list"], subscription_id)
    result: dict[str, bool] = {}
    for ase in environments:
        ase_id = safe_str(ase.get("id") or "")
        if not ase_id:
            continue
        # internalLoadBalancingMode surfaces under properties in the REST/ARM
        # shape but az CLI may flatten it to root level — check both.
        ilb_mode = (
            (ase.get("properties") or {}).get("internalLoadBalancingMode")
            or ase.get("internalLoadBalancingMode")
            or "None"
        )
        result[ase_id.lower()] = "web" in ilb_mode.lower()
    return result


def extract_ip_restrictions(
    network_acls: dict[str, Any] | None = None,
    ip_rules: list[dict[str, Any]] | None = None,
    vnet_rules: list[dict[str, Any]] | None = None,
    rule_value_key: str = "value",
) -> list[str]:
    """Return a de-duplicated list of allowed CIDRs/IP ranges from network ACL structures.

    Handles both Azure Storage/KeyVault networkAcls format and generic
    ipRules/vnetRules lists.  Returns an empty list when no restrictions apply.
    """
    cidrs: list[str] = []

    if network_acls:
        for rule in network_acls.get("ipRules") or []:
            v = rule.get(rule_value_key) or rule.get("ipAddressOrRange") or rule.get("value")
            if v:
                cidrs.append(v)
        for rule in network_acls.get("virtualNetworkRules") or []:
            v = (
                rule.get("virtualNetworkResourceId")
                or rule.get("id")
                or rule.get("subnet", {}).get("id")
            )
            if v:
                cidrs.append(f"vnet:{v.split('/')[-1]}")

    if ip_rules:
        for rule in ip_rules:
            if isinstance(rule, dict):
                v = rule.get("ipAddressOrRange") or rule.get("value") or rule.get("ip")
                if v:
                    cidrs.append(v)
            elif isinstance(rule, str):
                cidrs.append(rule)

    if vnet_rules:
        for rule in vnet_rules:
            if isinstance(rule, dict):
                v = rule.get("id") or rule.get("virtualNetworkResourceId")
                if v:
                    cidrs.append(f"vnet:{v.split('/')[-1]}")

    # De-duplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for c in cidrs:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def build_endpoint(
    address: str | None,
    port: int,
    protocol: str,
    probe: bool = True,
    timeout: int = 5,
) -> dict[str, Any] | None:
    """Build a single endpoint dict, optionally probing connectivity.

    Returns None if address is empty/None.
    """
    addr = safe_str(address)
    if not addr:
        return None
    ep: dict[str, Any] = {"address": addr, "port": port, "protocol": protocol}
    if probe and _PROBES_ENABLED:
        ep.update(_probe_endpoint(addr, port, protocol, timeout))
    return ep


def build_endpoints(
    entries: list[tuple[str | None, int, str]],
    timeout: int = 5,
) -> str:
    """Build a JSON-encoded list of endpoint dicts from (address, port, protocol) tuples.

    Probes are run for each endpoint if probing is enabled.
    """
    if not entries:
        return json.dumps([])

    results: list[dict[str, Any]] = []
    if _PROBES_ENABLED and len(entries) > 1 and all(protocol != "dns" for _, _, protocol in entries):
        with ThreadPoolExecutor(max_workers=min(8, len(entries))) as pool:
            for ep in pool.map(partial(_build_endpoint_from_entry, timeout=timeout), entries):
                if ep is not None:
                    results.append(ep)
    else:
        for address, port, protocol in entries:
            ep = build_endpoint(address, port, protocol, timeout=timeout)
            if ep is not None:
                results.append(ep)
    return json.dumps(results)


def _build_endpoint_from_entry(
    entry: tuple[str | None, int, str],
    timeout: int = 5,
) -> dict[str, Any] | None:
    address, port, protocol = entry
    return build_endpoint(address, port, protocol, timeout=timeout)


# ---------------------------------------------------------------------------
# Connectivity probe
# ---------------------------------------------------------------------------

def _probe_endpoint(
    address: str,
    port: int,
    protocol: str,
    timeout: int = 5,
) -> dict[str, Any]:
    """Attempt a connection to the endpoint and return probe metadata.

    Always returns a dict with at least: reachable (bool), probe_latency_ms (int|None),
    probe_error (str|None), probe_note (str|None).
    """
    result: dict[str, Any] = {
        "reachable": False,
        "probe_latency_ms": None,
        "probe_error": None,
        "probe_note": None,
    }

    try:
        if protocol == "dns":
            t0 = time.monotonic()
            try:
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(timeout)
                infos = socket.getaddrinfo(address, None)
                socket.setdefaulttimeout(old_timeout)
            except socket.gaierror as e:
                result["probe_error"] = str(e)[:120]
                return result
            latency = int((time.monotonic() - t0) * 1000)
            result["reachable"] = bool(infos)
            result["probe_latency_ms"] = latency
            result["probe_note"] = f"dns_resolved:{infos[0][4][0]}" if infos else "dns_nxdomain"
            return result

        # TCP-level connect (works for all TCP-based protocols)
        t0 = time.monotonic()
        with socket.create_connection((address, port), timeout=timeout) as sock:
            latency = int((time.monotonic() - t0) * 1000)
            result["reachable"] = True
            result["probe_latency_ms"] = latency

            # For HTTPS: attempt TLS handshake and optional HTTP GET
            if protocol == "https":
                ctx = ssl.create_default_context()
                try:
                    with ctx.wrap_socket(sock, server_hostname=address) as tls_sock:
                        result["probe_note"] = "tls_ok"
                        # Fire a minimal HTTP GET to see the status code
                        http_note = _http_get_status(address, port, timeout)
                        if http_note:
                            result["probe_note"] = http_note
                except ssl.SSLError as tls_err:
                    result["probe_note"] = f"tls_error:{tls_err.reason}"
            elif protocol == "http":
                http_note = _http_get_status(address, port, timeout, use_tls=False)
                result["probe_note"] = http_note or "tcp_ok"
            else:
                result["probe_note"] = "tcp_ok"

    except socket.timeout:
        result["probe_error"] = "timeout"
    except ConnectionRefusedError:
        result["probe_error"] = "connection_refused"
    except OSError as exc:
        result["probe_error"] = str(exc)[:120]

    return result


def _http_get_status(
    address: str,
    port: int,
    timeout: int,
    use_tls: bool = True,
) -> str | None:
    """Fire a HEAD/GET to the address and return a short note like 'http_200'."""
    scheme = "https" if use_tls else "http"
    url = f"{scheme}://{address}:{port}/"
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Triage-Saurus-Harvest/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return f"http_{resp.status}"
    except urllib.error.HTTPError as exc:
        # 4xx/5xx still means the port is open and serving HTTP
        return f"http_{exc.code}"
    except Exception:
        return None
