from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional


_ADMIN_EDGE_TYPES = {
    "contains",
    "grants_access_to",
    "parent_of",
    "child_of",
    "resource_group_member",
    "has_role",
    "associates",
}


def _architecture_asset_tier(
    resource: dict,
    classify_layer: Callable[[dict], str],
    is_relay_resource: Callable[[Optional[dict]], bool],
) -> str:
    resource_type = (resource.get("resource_type") or "").lower()

    if any(token in resource_type for token in ("api_management", "apigateway", "api_gateway")):
        return "api"
    if any(token in resource_type for token in ("application_gateway", "frontdoor", "cloudfront", "ingress", "load_balancer", "public_ip")):
        return "entry"
    if is_relay_resource(resource):
        return "entry"

    layer = classify_layer(resource)
    if layer == "identity":
        return "identity"
    if layer == "data":
        return "data"
    if layer == "compute":
        return "backend"
    if layer == "network":
        return "entry"
    return "other"


def _short_name(name: str, max_len: int = 28) -> str:
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


def _primary_name(items: list[dict], limit: int = 2) -> str:
    names = [str(item.get("resource_name") or item.get("friendly_type") or "resource") for item in items if item]
    if not names:
        return "resource"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" +{len(names) - limit}"


def _attack_badges(asset: dict) -> list[str]:
    badges: list[str] = []
    if asset.get("public"):
        badges.append("public")
    if asset.get("tier") in {"backend", "api"}:
        badges.append("exec" if asset.get("tier") == "backend" else "auth")
    elif asset.get("tier") == "identity":
        badges.append("secrets")
    elif asset.get("tier") == "data":
        badges.append("data")
    return badges[:2]


def _asset_label(asset: dict, *, include_badges: bool = False) -> str:
    parts = [asset.get("friendly_type") or "resource", _short_name(asset.get("resource_name") or "resource")]
    if include_badges:
        badges = _attack_badges(asset)
        if badges:
            parts.append(" - ".join(badges))
    return "<br/>".join(part for part in parts if part)


def _html_node(node_id: str, label: str, resource_type: str | None, get_icon_url: Callable[[str], str | None]) -> str:
    if resource_type:
        icon_url = get_icon_url(resource_type)
        if icon_url:
            safe_label = label.replace("'", "&#39;").replace('"', "&quot;")
            html = (
                "<div style='text-align:center;padding:0'>"
                f"<img src='{icon_url}' style='width:24px;height:24px;aspect-ratio:1/1;object-fit:contain;margin-bottom:0;border-radius:2px'/>"
                f"<div style='font-size:0.75em;word-wrap:break-word;white-space:normal;line-height:1.1'>{safe_label}</div>"
                "</div>"
            )
            return f'    {node_id}["{html}"]'
    safe_label = label.replace('"', "&quot;")
    return f'    {node_id}["{safe_label}"]'


def _node_class(asset: dict) -> str:
    tier = asset.get("tier")
    if tier == "entry":
        return "entryPointProtected" if asset.get("protected") else "entryPoint"
    if tier == "api":
        return "apiGateway"
    if tier == "identity":
        return "secretStore"
    if tier == "data":
        return "dataStorePublic" if asset.get("public") else "dataStore"
    if tier == "backend":
        return "publicBackend" if asset.get("public") else "backend"
    return "neutral"


def _render_view(
    *,
    nodes: list[dict],
    edges: list[dict],
    title: str,
    description: str,
    legend: list[str],
    attack_paths: list[dict],
    asset_summary: dict,
    get_icon_url: Callable[[str], str | None],
) -> dict:
    if not nodes:
        nodes = [{"id": "NoData", "label": "No relevant resources found", "class_name": "summary"}]

    lines = ["graph LR"]
    for node in nodes:
        lines.append(_html_node(node["id"], node.get("label") or node["id"], node.get("resource_type"), get_icon_url))

    lines.append("")
    for edge in edges:
        label = str(edge.get("label") or "").replace('"', "&quot;")
        arrow = edge.get("arrow") or "-->"
        if label:
            lines.append(f'    {edge["src"]} {arrow}|"{label}"| {edge["dst"]}')
        else:
            lines.append(f'    {edge["src"]} {arrow} {edge["dst"]}')

    lines.append("")
    for index, edge in enumerate(edges):
        styles = [f'stroke:{edge.get("color", "#ffffff")}', f'stroke-width:{edge.get("width", "2px")}']
        dash = edge.get("dasharray")
        if dash:
            styles.append(f"stroke-dasharray:{dash}")
        lines.append(f'    linkStyle {index} ' + ",".join(styles))

    lines.append("")
    lines.append("    classDef internet stroke:#d32f2f,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef entryPoint stroke:#d32f2f,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef entryPointProtected stroke:#ea580c,stroke-width:2px,fill:#3d1c0d;")
    lines.append("    classDef apiGateway stroke:#0ea5e9,stroke-width:2px,fill:#082f49;")
    lines.append("    classDef backend stroke:#22c55e,stroke-width:2px,fill:#052e16;")
    lines.append("    classDef publicBackend stroke:#ef4444,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef dataStore stroke:#2563eb,stroke-width:2px,fill:#172554;")
    lines.append("    classDef dataStorePublic stroke:#ef4444,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef secretStore stroke:#8b5cf6,stroke-width:2px,fill:#2e1065;")
    lines.append("    classDef summary stroke:#6b7280,stroke-width:2px,fill:#111827;")
    lines.append("    classDef neutral stroke:#6b7280,stroke-width:2px,fill:#111827;")

    for node in nodes:
        if node.get("class_name"):
            lines.append(f'    class {node["id"]} {node["class_name"]};')

    css_lines = [
        "/* Architecture overlay styling */",
        ".internet { stroke: #d32f2f; stroke-width: 2px; fill: #3b0a0a; }",
        ".entryPoint { stroke: #d32f2f; stroke-width: 2px; fill: #3b0a0a; }",
        ".entryPointProtected { stroke: #ea580c; stroke-width: 2px; fill: #3d1c0d; }",
        ".apiGateway { stroke: #0ea5e9; stroke-width: 2px; fill: #082f49; }",
        ".backend { stroke: #22c55e; stroke-width: 2px; fill: #052e16; }",
        ".publicBackend { stroke: #ef4444; stroke-width: 2px; fill: #3b0a0a; }",
        ".dataStore { stroke: #2563eb; stroke-width: 2px; fill: #172554; }",
        ".dataStorePublic { stroke: #ef4444; stroke-width: 2px; fill: #3b0a0a; }",
        ".secretStore { stroke: #8b5cf6; stroke-width: 2px; fill: #2e1065; }",
        ".summary { stroke: #6b7280; stroke-width: 2px; fill: #111827; }",
    ]

    return {
        "code": "\n".join(lines),
        "css_code": "\n".join(css_lines),
        "title": title,
        "description": description,
        "legend": legend,
        "attack_paths": attack_paths,
        "asset_summary": asset_summary,
    }


def build_architecture_view_bundle(
    *,
    connectivity_code: str,
    connectivity_css: str,
    resources: list[dict],
    connections: list[dict],
    emitted_nodes: set[str],
    exposed_resources: dict,
    get_node_id: Callable[[dict], str],
    get_icon_url: Callable[[str], str | None],
    get_friendly_type: Callable[[str], str],
    classify_layer: Callable[[dict], str],
    is_connected_resource: Callable[[Optional[dict]], bool],
    is_exposed_resource: Callable[[Optional[dict]], bool],
    is_relay_resource: Callable[[Optional[dict]], bool],
    internet_edge_annotation: Callable[[object], tuple[str, str]],
) -> dict:
    visible_resources = []
    for resource in resources:
        name = resource.get("resource_name")
        if not name or name not in emitted_nodes:
            continue
        if not (is_connected_resource(resource) or is_exposed_resource(resource)):
            continue
        visible_resources.append(resource)

    asset_by_name: dict[str, dict] = {}
    for resource in visible_resources:
        name = str(resource.get("resource_name") or "")
        tier = _architecture_asset_tier(resource, classify_layer, is_relay_resource)
        public = is_exposed_resource(resource)
        asset_by_name[name] = {
            "resource_name": name,
            "resource_type": resource.get("resource_type"),
            "friendly_type": get_friendly_type(resource.get("resource_type") or ""),
            "tier": tier,
            "public": public,
            "protected": False,
            "node_id": get_node_id(resource),
        }

    public_names = set()
    downstream = defaultdict(list)
    internet_edges: list[tuple[str, str, str]] = []

    for conn in connections:
        conn_type = (conn.get("connection_type") or "").lower()
        src = str(conn.get("source") or "")
        tgt = str(conn.get("target") or "")
        if not src or not tgt or conn_type in _ADMIN_EDGE_TYPES:
            continue
        if src.lower() == "internet" and tgt in asset_by_name and conn.get("confirmed") is True:
            detail = None
            for exposure_detail in exposed_resources.values():
                if getattr(exposure_detail, "resource_name", None) == tgt:
                    detail = exposure_detail
                    break
            label, color = internet_edge_annotation(detail)
            internet_edges.append((tgt, label, color))
            public_names.add(tgt)
            if color != "red":
                asset_by_name[tgt]["protected"] = True
            continue
        if src in asset_by_name and tgt in asset_by_name and src != tgt:
            downstream[src].append((tgt, conn_type))

    for name, asset in asset_by_name.items():
        if asset.get("public"):
            public_names.add(name)

    entry_assets = [asset_by_name[name] for name in sorted(public_names) if name in asset_by_name]
    first_hops: list[dict] = []
    second_hops: list[dict] = []

    seen_first: set[str] = set()
    seen_second: set[str] = set()
    for origin in sorted(public_names):
        for target, _conn_type in downstream.get(origin, []):
            if target not in asset_by_name or target in seen_first:
                continue
            seen_first.add(target)
            first_hops.append(asset_by_name[target])
            for next_target, _next_type in downstream.get(target, []):
                if next_target not in asset_by_name or next_target in seen_second:
                    continue
                seen_second.add(next_target)
                second_hops.append(asset_by_name[next_target])

    identity_targets = [asset for asset in asset_by_name.values() if asset.get("tier") == "identity"]
    data_targets = [asset for asset in asset_by_name.values() if asset.get("tier") == "data"]
    backend_targets = [asset for asset in asset_by_name.values() if asset.get("tier") == "backend"]
    api_targets = [asset for asset in asset_by_name.values() if asset.get("tier") == "api"]

    attack_paths: list[dict] = []
    if entry_assets and (first_hops or second_hops):
        chain = ["Internet", _primary_name(entry_assets, 1)]
        if first_hops:
            chain.append(_primary_name(first_hops, 1))
        if second_hops:
            chain.append(_primary_name(second_hops, 1))
        attack_paths.append(
            {
                "title": "Public ingress into architecture",
                "path": " -> ".join(chain),
                "summary": "An internet-facing entry point appears to route traffic into application workloads or APIs.",
                "impact": "A compromise at the edge could become a foothold deeper inside the platform.",
                "confidence": "medium" if any(asset.get("protected") for asset in entry_assets) else "high",
                "evidence": [f"Public entry points: {_primary_name(entry_assets)}"],
            }
        )
    elif entry_assets:
        attack_paths.append(
            {
                "title": "Public edge exposure",
                "path": f"Internet -> {_primary_name(entry_assets, 2)}",
                "summary": "The architecture exposes internet-facing services even when downstream routing is sparse.",
                "impact": "Treat the public edge as the first likely foothold and validate what sits behind it.",
                "confidence": "medium",
                "evidence": [f"Public entry points: {_primary_name(entry_assets)}"],
            }
        )

    if backend_targets and identity_targets:
        attack_paths.append(
            {
                "title": "Secrets pivot from workloads",
                "path": f"{_primary_name(backend_targets, 1)} -> {_primary_name(identity_targets, 2)}",
                "summary": "Compromised workloads often pivot by reading secrets, certificates, or managed identities.",
                "impact": "Secret theft can widen blast radius into downstream services and data stores.",
                "confidence": "medium",
                "evidence": [f"Identity and secret targets: {_primary_name(identity_targets)}"],
            }
        )

    if (backend_targets or api_targets) and data_targets:
        attack_paths.append(
            {
                "title": "Data access after workload compromise",
                "path": f"{_primary_name(api_targets[:1] or backend_targets[:1], 1)} -> {_primary_name(data_targets, 2)}",
                "summary": "Once an attacker reaches application code, data-plane services become the obvious next objective.",
                "impact": "Could lead to data theft, tampering, queue abuse, or wider operational disruption.",
                "confidence": "medium",
                "evidence": [f"Data targets in scope: {_primary_name(data_targets)}"],
            }
        )

    public_data_targets = [asset for asset in data_targets if asset.get("public")]
    if public_data_targets:
        attack_paths.append(
            {
                "title": "Direct public data exposure",
                "path": f"Internet -> {_primary_name(public_data_targets, 2)}",
                "summary": "Internet-reachable data services create a direct path without an application compromise step.",
                "impact": "Exposure may allow direct data access, enumeration, or brute-force attempts.",
                "confidence": "high",
                "evidence": [f"Public data targets: {_primary_name(public_data_targets)}"],
            }
        )

    if not attack_paths:
        attack_paths.append(
            {
                "title": "No direct public path identified",
                "path": "Private-only or internal-facing topology",
                "summary": "This architecture view did not identify an obvious internet-origin attack chain.",
                "impact": "Focus next on CI/CD, identity, and control-plane pivots rather than direct ingress.",
                "confidence": "low",
                "evidence": ["No confirmed public entry points in the rendered topology"],
            }
        )

    asset_summary = {
        "entry_points": sum(1 for asset in asset_by_name.values() if asset.get("tier") == "entry"),
        "api_layer": sum(1 for asset in asset_by_name.values() if asset.get("tier") == "api"),
        "backends": sum(1 for asset in asset_by_name.values() if asset.get("tier") == "backend"),
        "data_stores": sum(1 for asset in asset_by_name.values() if asset.get("tier") in {"data", "identity"}),
        "public_assets": len(public_names),
    }

    def add_node(nodes: list[dict], seen: set[str], asset: dict, *, badges: bool = False) -> None:
        node_id = asset["node_id"]
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": _asset_label(asset, include_badges=badges),
                "resource_type": asset.get("resource_type"),
                "class_name": _node_class(asset),
            }
        )

    exposure_nodes = [{"id": "Internet", "label": "Internet", "class_name": "internet"}]
    attack_nodes = [{"id": "Internet", "label": "Internet", "class_name": "internet"}]
    exposure_seen = {"Internet"}
    attack_seen = {"Internet"}
    exposure_edges: list[dict] = []
    attack_edges: list[dict] = []
    seen_exposure_edges: set[tuple[str, str, str]] = set()
    seen_attack_edges: set[tuple[str, str, str]] = set()

    for asset in entry_assets + first_hops + second_hops:
        add_node(exposure_nodes, exposure_seen, asset, badges=False)
        add_node(attack_nodes, attack_seen, asset, badges=True)

    def add_exposure_edge(src: str, dst: str, label: str, color: str, width: str = "2px") -> None:
        key = (src, dst, label)
        if key in seen_exposure_edges:
            return
        seen_exposure_edges.add(key)
        exposure_edges.append({"src": src, "dst": dst, "label": label, "color": color, "width": width})

    def add_attack_edge(src: str, dst: str, label: str) -> None:
        key = (src, dst, label)
        if key in seen_attack_edges:
            return
        seen_attack_edges.add(key)
        attack_edges.append({"src": src, "dst": dst, "label": label, "color": "#ef4444", "width": "3px", "dasharray": "6 4"})

    for name, label, color in internet_edges:
        asset = asset_by_name.get(name)
        if not asset:
            continue
        add_exposure_edge("Internet", asset["node_id"], label, color, "3px")
        add_attack_edge("Internet", asset["node_id"], label.lower() if label else "public foothold")

    for origin in sorted(public_names):
        for target, conn_type in downstream.get(origin, []):
            origin_asset = asset_by_name.get(origin)
            target_asset = asset_by_name.get(target)
            if not origin_asset or not target_asset:
                continue
            add_node(exposure_nodes, exposure_seen, target_asset)
            add_node(attack_nodes, attack_seen, target_asset, badges=True)
            add_exposure_edge(origin_asset["node_id"], target_asset["node_id"], "routing" if origin_asset.get("tier") in {"entry", "api"} else "reachable next hop", "#f59e0b" if origin_asset.get("tier") in {"entry", "api"} else "#94a3b8")

            if target_asset.get("tier") == "api":
                attack_label = "route abuse"
            elif target_asset.get("tier") == "identity":
                attack_label = "steal secrets"
            elif target_asset.get("tier") == "data":
                attack_label = "query data"
            else:
                attack_label = "backend exploit"
            if conn_type in {"uses_database", "data_access"}:
                attack_label = "query data"
            add_attack_edge(origin_asset["node_id"], target_asset["node_id"], attack_label)

            for next_target, next_type in downstream.get(target, []):
                next_asset = asset_by_name.get(next_target)
                if not next_asset:
                    continue
                add_node(exposure_nodes, exposure_seen, next_asset)
                add_node(attack_nodes, attack_seen, next_asset, badges=True)
                add_exposure_edge(target_asset["node_id"], next_asset["node_id"], "reachable next hop", "#94a3b8")

                if next_asset.get("tier") == "identity":
                    next_label = "steal secrets"
                elif next_asset.get("tier") == "data":
                    next_label = "query data"
                else:
                    next_label = "backend exploit"
                if next_type in {"uses_database", "data_access"}:
                    next_label = "query data"
                add_attack_edge(target_asset["node_id"], next_asset["node_id"], next_label)

    exposure_view = _render_view(
        nodes=exposure_nodes,
        edges=exposure_edges,
        title="Exposure view",
        description="Shows confirmed internet-facing resources and the next internal hops they can likely reach.",
        legend=[
            "Red edges: direct public exposure",
            "Orange edges: internet-reachable routing chains",
            "Grey edges: likely internal next hops after the edge is crossed",
        ],
        attack_paths=attack_paths,
        asset_summary=asset_summary,
        get_icon_url=get_icon_url,
    )
    attack_view = _render_view(
        nodes=attack_nodes,
        edges=attack_edges,
        title="Attack-path view",
        description="Shows plausible attacker movement from public footholds into APIs, workloads, secrets, and data stores.",
        legend=[
            "Dashed red edges: plausible attacker movement",
            "Badges identify public footholds, executable workloads, secret stores, and data targets",
        ],
        attack_paths=attack_paths,
        asset_summary=asset_summary,
        get_icon_url=get_icon_url,
    )

    connectivity_view = {
        "code": connectivity_code,
        "css_code": connectivity_css,
        "title": "Connectivity view",
        "description": "Shows the full provider topology and inferred service relationships.",
        "legend": [
            "Red edges: direct internet exposure",
            "Orange dashed edges: internet-reachable internal paths",
            "White edges: inferred service, data, and hosting relationships",
        ],
        "attack_paths": attack_paths,
        "asset_summary": asset_summary,
    }

    return {
        "code": connectivity_code,
        "css_code": connectivity_css,
        "default_view": "connectivity",
        "attack_paths": attack_paths,
        "asset_summary": asset_summary,
        "views": {
            "connectivity": connectivity_view,
            "exposure": exposure_view,
            "attack_paths": attack_view,
        },
    }
