from __future__ import annotations

import re
from typing import Callable


def parse_diagram_request_args(args) -> tuple[str, bool | None, bool]:
    repo_name = (args.get("repo_name") or "").strip()
    include_api_operations_raw = (args.get("include_api_operations") or "").strip().lower()
    include_api_operations_override: bool | None = None
    if include_api_operations_raw in {"1", "true", "yes", "on"}:
        include_api_operations_override = True
    elif include_api_operations_raw in {"0", "false", "no", "off"}:
        include_api_operations_override = False
    return repo_name, include_api_operations_override, include_api_operations_override is not None


def edge_count(code: str) -> int:
    if not code:
        return 0
    return sum(1 for line in code.splitlines() if ("-->" in line or "-.>" in line))


def diagram_icon_provider_mismatch(diagrams: list[dict], canonical_provider_key: Callable[[str], str]) -> bool:
    provider_re = re.compile(r"/static/assets/icons/([^/]+)/", re.IGNORECASE)
    for d in diagrams or []:
        expected = canonical_provider_key((d.get("provider") or "").lower())
        if expected in {"", "unknown", "kubernetes"}:
            continue
        code = d.get("mermaid_code") or ""
        seen = {
            canonical_provider_key(m.group(1).lower())
            for m in provider_re.finditer(code)
        }
        seen.discard("")
        seen.discard("unknown")
        if not seen:
            continue
        if expected not in seen:
            return True
        foreign = seen - {expected, "kubernetes"}
        if foreign:
            return True
    return False


def normalize_diagrams_payload(
    *,
    diagrams: list[dict],
    experiment_id: str,
    repo_name: str,
    include_api_operations_override: bool | None,
    canonical_provider_key: Callable[[str], str],
    sanitize_mermaid: Callable[[str], str],
    regenerate_bundle: Callable[[str, str, str, bool | None], dict],
) -> dict:
    normalized: list[dict] = []
    for d in diagrams:
        code = d.get("mermaid_code") or ""
        css_code = d.get("css_code") or ""
        provider_key = canonical_provider_key(d.get("provider"))
        needs_regen = repo_name and provider_key not in ("", "unknown") and (
            not d.get("views")
            or (":::icon-" in code and "classDef icon-" not in f"{code}\n{css_code}")
        )
        if needs_regen:
            try:
                regenerated = regenerate_bundle(
                    experiment_id,
                    repo_name,
                    provider_key,
                    include_api_operations_override,
                )
                regenerated_code = regenerated.get("code")
                if regenerated_code:
                    d = dict(d)
                    d["mermaid_code"] = regenerated_code
                    d["css_code"] = regenerated.get("css_code", "") or ""
                    d["views"] = regenerated.get("views") or {}
                    d["default_view"] = regenerated.get("default_view") or "connectivity"
                    d["attack_paths"] = regenerated.get("attack_paths") or []
                    d["asset_summary"] = regenerated.get("asset_summary") or {}
            except Exception:
                pass
        normalized.append(d)

    response_diagrams: list[dict] = []
    for d in normalized:
        raw_code = d.get("mermaid_code") or ""
        try:
            sanitized_code = sanitize_mermaid(raw_code) if raw_code else raw_code
        except Exception:
            sanitized_code = raw_code

        raw_views = d.get("views") if isinstance(d.get("views"), dict) else {}
        sanitized_views: dict[str, dict] = {}
        for view_name, view_payload in raw_views.items():
            if not isinstance(view_payload, dict):
                continue
            view_code = view_payload.get("code") or view_payload.get("mermaid") or ""
            try:
                sanitized_view_code = sanitize_mermaid(view_code) if view_code else view_code
            except Exception:
                sanitized_view_code = view_code
            sanitized_views[view_name] = {
                "code": sanitized_view_code,
                "css_code": view_payload.get("css_code", ""),
                "title": view_payload.get("title", ""),
                "description": view_payload.get("description", ""),
                "legend": view_payload.get("legend") or [],
                "attack_paths": view_payload.get("attack_paths") or [],
                "asset_summary": view_payload.get("asset_summary") or {},
                "nodes": view_payload.get("nodes") or [],
                "edges": view_payload.get("edges") or [],
                "type": view_payload.get("type") or "",
            }

        response_diagrams.append(
            {
                "title": d.get("diagram_title"),
                "code": sanitized_code,
                "css_code": d.get("css_code", ""),
                "views": sanitized_views,
                "default_view": d.get("default_view") or ("connectivity" if sanitized_views else ""),
                "attack_paths": d.get("attack_paths") or [],
                "asset_summary": d.get("asset_summary") or {},
            }
        )
    return {"diagrams": [d for d in response_diagrams if d.get("code")]}

