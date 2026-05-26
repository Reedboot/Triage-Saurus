"""Shared helpers used by all harvest provider modules."""
from __future__ import annotations

import json
import subprocess
from typing import Any


def az(args: list[str], subscription_id: str) -> list[dict[str, Any]]:
    """Run an az CLI command scoped to a subscription and return parsed JSON.

    Returns an empty list on failure (e.g. no permission, resource type not
    registered in the subscription) so providers degrade gracefully.
    """
    cmd = ["az"] + args + ["--subscription", subscription_id, "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout or "[]") or []
    except Exception:
        return []


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
