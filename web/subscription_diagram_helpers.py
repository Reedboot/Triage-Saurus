from __future__ import annotations

from typing import Callable


SUBSCRIPTION_DRILLABLE_ARM_TYPES = {
    "microsoft.network/applicationgateways",
    "microsoft.apimanagement/service",
    "microsoft.appconfiguration/configurationstores",
    "microsoft.keyvault/vaults",
    "microsoft.storage/storageaccounts",
    "microsoft.sql/servers",
    "microsoft.containerservice/managedclusters",
    "microsoft.documentdb/databaseaccounts",
    "microsoft.web/sites",
    "microsoft.web/serverfarms",
    "microsoft.web/hostingenvironments",
}

SUBSCRIPTION_FQDN_SUFFIXES = {
    "microsoft.keyvault/vaults": "vault.azure.net",
    "microsoft.storage/storageaccounts": "blob.core.windows.net",
    "microsoft.servicebus/namespaces": "servicebus.windows.net",
    "microsoft.eventhub/namespaces": "servicebus.windows.net",
    "microsoft.sql/servers": "database.windows.net",
    "microsoft.cache/redis": "redis.cache.windows.net",
    "microsoft.documentdb/databaseaccounts": "documents.azure.com",
    "microsoft.appconfiguration/configurationstores": "azconfig.io",
}


def subscription_node_id(item: dict, sanitise_node_id: Callable[[str], str]) -> str:
    rg = item.get("rg") or "grp"
    combined = f"{rg}_{item.get('name') or item.get('label') or 'resource'}"
    return sanitise_node_id(combined)


def subscription_short_name(name: str, max_len: int = 28) -> str:
    import re

    for prefix in ("cbuk-core-prodgreen-", "cbuk-core-prod-", "cbuk-", "pipeline-customer-production-"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    name = re.sub(r"-(uksouth|ukwest|eastus\d*|westeurope|northeurope|westus\d*)$", "", name, flags=re.IGNORECASE)
    if len(name) > max_len:
        name = name[: max_len - 1] + "..."
    return name


def subscription_is_function_app(item: dict) -> bool:
    name = (item.get("name") or "").lower()
    return "-fn-" in name or name.endswith("-fn") or "funcapp" in name or "functionapp" in name


def subscription_known_fqdn_suffix(arm_type: str) -> str | None:
    return SUBSCRIPTION_FQDN_SUFFIXES.get((arm_type or "").lower())


def subscription_asset_tier(arm_type: str, name: str = "") -> str:
    type_key = (arm_type or "").lower()
    item = {"name": name}
    if "applicationgateway" in type_key or "frontdoor" in type_key or "publicipaddress" in type_key or "trafficmanager" in type_key or "cdn/profiles" in type_key or "network/loadbalancers" in type_key:
        return "entry"
    if "apimanagement" in type_key:
        return "api"
    if (
        "managedcluster" in type_key
        or "containerinstance" in type_key
        or "serverfarms" in type_key
        or "hostingenvironment" in type_key
        or "datafactory" in type_key
        or "cognitiveservices" in type_key
        or "containerregistry" in type_key
        or "servicefabric" in type_key
    ):
        return "backend"
    if "sites" in type_key:
        return "backend" if not subscription_is_function_app(item) else "backend"
    if (
        "sql" in type_key
        or "documentdb" in type_key
        or "storage" in type_key
        or "keyvault" in type_key
        or "servicebus" in type_key
        or "eventhub" in type_key
        or "cache/redis" in type_key
        or "search/search" in type_key
        or "appconfiguration" in type_key
    ):
        return "data"
    return "other"


def subscription_assets_from_rows(rows: list, friendly_type: Callable[[str], str]) -> list[dict]:
    assets: list[dict] = []
    for row in rows:
        name, rtype, rg, fqdn, is_public, sku = row[:6]
        asset_id = row[6] if len(row) > 6 else None
        has_waf = bool(row[7]) if len(row) > 7 else False
        listeners = row[8] if len(row) > 8 else None
        is_restricted = bool(row[9]) if len(row) > 9 else False
        waf_mode = row[10] if len(row) > 10 else None
        asset = {
            "name": name,
            "arm_type": rtype,
            "rg": rg or "default",
            "fqdn": fqdn or "",
            "public": bool(is_public),
            "sku": sku,
            "id": asset_id,
            "has_waf": has_waf,
            "waf_mode": waf_mode,
            "listeners": listeners,
            "is_restricted": is_restricted,
            "tier": subscription_asset_tier(rtype, name),
            "friendly_type": friendly_type(rtype),
            "short_name": subscription_short_name(name or "resource"),
        }
        resolved_fqdn = subscription_primary_fqdn(asset)
        asset["fqdn"] = resolved_fqdn
        asset["fqdns"] = [resolved_fqdn] if resolved_fqdn else []
        assets.append(asset)
    return assets


def subscription_apply_plan_hierarchy(assets: list[dict], plan_links: list | None = None) -> list[dict]:
    """Fold hosted App Services / Function Apps into their hosting parent.

    The returned list keeps App Service Plans and App Service Environments visible,
    hides hosted sites that have a matching parent in scope, and aggregates the
    hosted sites' FQDN/public exposure onto the parent node so the diagram stays
    clickable and accurate.
    """
    from collections import defaultdict

    if not plan_links:
        return [dict(asset) for asset in assets]

    def _key(asset: dict) -> tuple[str, str]:
        return ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower())

    def _type(asset: dict) -> str:
        return (asset.get("arm_type") or asset.get("type") or "").lower()

    asset_map = {_key(asset): dict(asset) for asset in assets}
    hidden_keys: set[tuple[str, str]] = set()
    hosted_by_parent: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for site_rg, site_name, plan_rg, plan_name in plan_links:
        site_key = ((site_name or "").lower(), (site_rg or "").lower())
        plan_key = ((plan_name or "").lower(), (plan_rg or "").lower())
        site_asset = asset_map.get(site_key)
        parent_asset = asset_map.get(plan_key)
        if not site_asset or not parent_asset:
            continue
        parent_type = _type(parent_asset)
        if "sites" not in _type(site_asset) or not any(
            token in parent_type for token in ("serverfarms", "hostingenvironment")
        ):
            continue
        if site_key in hidden_keys:
            continue
        hidden_keys.add(site_key)
        hosted_by_parent[plan_key].append(site_asset)

    visible_assets: list[dict] = []
    for asset in assets:
        asset_copy = dict(asset)
        key = _key(asset_copy)
        if key in hidden_keys:
            continue

        children = hosted_by_parent.get(key)
        if children and any(token in _type(asset_copy) for token in ("serverfarms", "hostingenvironment")):
            child_fqdns = [subscription_primary_fqdn(child) for child in children if subscription_primary_fqdn(child)]
            merged_fqdns = list(dict.fromkeys([*(asset_copy.get("fqdns") or []), *child_fqdns]))
            asset_copy["fqdns"] = merged_fqdns
            asset_copy["public"] = bool(asset_copy.get("public") or any(child.get("public") for child in children))
            asset_copy["is_restricted"] = bool(asset_copy.get("is_restricted") or any(child.get("is_restricted") for child in children))
            asset_copy["hosted_site_count"] = len(children)
        elif "fqdns" not in asset_copy:
            resolved_fqdn = subscription_primary_fqdn(asset_copy)
            if resolved_fqdn:
                asset_copy["fqdns"] = [resolved_fqdn]

        visible_assets.append(asset_copy)

    return visible_assets


def subscription_primary_fqdn(asset: dict) -> str:
    fqdns = asset.get("fqdns") or []
    if fqdns:
        return str(fqdns[0]).strip()
    fqdn = str(asset.get("fqdn") or "").strip()
    if fqdn:
        return fqdn
    suffix = subscription_known_fqdn_suffix(asset.get("arm_type") or asset.get("type") or "")
    if suffix:
        name = str(asset.get("name") or "").strip()
        if name:
            return f"{name}.{suffix}"
    return ""


def subscription_is_secret_store(arm_type: str) -> bool:
    type_key = (arm_type or "").lower()
    return "keyvault" in type_key or "appconfiguration" in type_key


def subscription_data_attack_label(asset: dict) -> str:
    type_key = (asset.get("arm_type") or "").lower()
    if "keyvault" in type_key:
        return "steal secrets"
    if "appconfiguration" in type_key:
        return "read config"
    if "storage" in type_key:
        return "read blobs"
    if "sql" in type_key or "documentdb" in type_key or "search/search" in type_key:
        return "query data"
    if "servicebus" in type_key or "eventhub" in type_key:
        return "abuse messages"
    if "cache/redis" in type_key:
        return "dump cache"
    return "access data"


def subscription_allowlist_label(asset: dict) -> str:
    family = str(asset.get("type") or asset.get("label") or asset.get("friendly_type") or "").strip()
    return f"IP allowlist ({family})" if family else "IP allowlist"


def subscription_is_allowlist_target(asset: dict) -> bool:
    type_key = (asset.get("arm_type") or asset.get("type") or "").lower()
    return any(
        token in type_key
        for token in (
            "apimanagement",
            "sites",
            "managedcluster",
            "containerinstance",
            "datafactory",
            "cognitiveservices",
            "containerregistry",
            "servicefabric",
            "keyvault",
            "storage",
            "sql",
            "documentdb",
            "servicebus",
            "eventhub",
            "cache/redis",
            "search/search",
            "appconfiguration",
        )
    )


def subscription_attack_badges(asset: dict) -> list[str]:
    badges: list[str] = []
    if asset.get("public"):
        badges.append("public")
    if asset.get("has_waf"):
        badges.append("waf")
    if asset.get("tier") == "backend":
        badges.append("exec")
    elif subscription_is_secret_store(asset.get("arm_type") or ""):
        badges.append("secrets")
    elif asset.get("tier") == "data":
        badges.append("data")
    elif asset.get("tier") == "api":
        badges.append("auth")
    return badges[:2]


def subscription_asset_label(asset: dict, include_badges: bool = False, include_fqdn: bool = False) -> str:
    parts = [
        asset.get("friendly_type") or asset.get("arm_type") or "resource",
        asset.get("short_name") or asset.get("name") or "resource",
    ]
    fqdn = subscription_primary_fqdn(asset)
    if include_fqdn and fqdn:
        parts.append(fqdn if len(fqdn) <= 42 else fqdn[:40] + "...")
    hosted_site_count = asset.get("hosted_site_count")
    if hosted_site_count:
        parts.append(f"{hosted_site_count} app{'s' if hosted_site_count != 1 else ''}")
    badges = subscription_attack_badges(asset) if include_badges else []
    if badges:
        parts.append(" - ".join(badges))
    return "<br/>".join(p for p in parts if p)


def subscription_html_node(node_id: str, label: str, arm_type: str | None, get_icon_path: Callable[[str], str | None]) -> str:
    if arm_type:
        icon_path = get_icon_path(arm_type)
        if icon_path:
            safe_label = label.replace("'", "&#39;").replace('"', "&quot;")
            html = (
                "<div style='text-align:center;padding:0'>"
                f"<img src='{icon_path}' style='width:24px;height:24px;aspect-ratio:1/1;"
                "object-fit:contain;margin-bottom:0;border-radius:2px'/>"
                f"<div style='font-size:0.75em;word-wrap:break-word;white-space:normal;line-height:1.1'>{safe_label}</div>"
                "</div>"
            )
            return f'    {node_id}["{html}"]'
    safe_label = label.replace('"', "&quot;")
    return f'    {node_id}["{safe_label}"]'


def subscription_node_class(asset: dict) -> str:
    tier = asset.get("tier")
    if tier == "entry":
        return "entryPointProtected" if asset.get("has_waf") else "entryPoint"
    if tier == "api":
        return "apiGateway"
    if subscription_is_secret_store(asset.get("arm_type") or ""):
        return "secretStore"
    if tier == "data":
        return "dataStorePublic" if asset.get("public") else "dataStore"
    if asset.get("public"):
        return "publicBackend"
    if tier == "backend":
        return "backend"
    return "neutral"


def subscription_register_node(
    node_map: dict,
    asset: dict,
    sanitise_node_id: Callable[[str], str],
) -> None:
    arm_type = (asset.get("arm_type") or "").lower()
    resources = asset.get("resources") or [{"rg": asset.get("rg"), "name": asset.get("name")}]
    node_map[subscription_node_id(asset, sanitise_node_id)] = {
        "title": asset.get("name") or asset.get("friendly_type") or "resource",
        "arm_type": asset.get("arm_type"),
        "resources": resources,
        "can_drill": bool(resources) and (
            arm_type in SUBSCRIPTION_DRILLABLE_ARM_TYPES or len(resources) > 1
        ),
    }


def subscription_join_names(items: list[dict], limit: int = 2) -> str:
    names = [str(item.get("name") or item.get("friendly_type") or "resource").strip() for item in items if item]
    if not names:
        return "resource"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" +{len(names) - limit}"


def render_subscription_view(
    *,
    nodes: list[dict],
    edges: list[dict],
    get_icon_path: Callable[[str], str | None],
    node_map: dict | None = None,
    direction: str = "LR",
    title: str = "",
    description: str = "",
    legend: list[str] | None = None,
    attack_paths: list[dict] | None = None,
    asset_summary: dict | None = None,
) -> dict:
    if not nodes:
        nodes = [{"id": "NoData", "label": "No resources found", "class_name": "summary"}]

    lines = [f"graph {direction}"]
    for node in nodes:
        lines.append(subscription_html_node(node["id"], node.get("label") or node["id"], node.get("arm_type"), get_icon_path))

    lines.append("")
    for edge in edges:
        label = str(edge.get("label") or "").replace('"', "&quot;")
        arrow = edge.get("arrow") or "-->"
        if label:
            lines.append(f'    {edge["src"]} {arrow}|"{label}"| {edge["dst"]}')
        else:
            lines.append(f'    {edge["src"]} {arrow} {edge["dst"]}')

    lines.append("")
    for idx, edge in enumerate(edges):
        style = [f'stroke:{edge.get("color", "#ffffff")}', f'stroke-width:{edge.get("width", "2px")}']
        dash = edge.get("dasharray")
        if dash:
            style.append(f"stroke-dasharray:{dash}")
        lines.append(f'    linkStyle {idx} ' + ",".join(style))

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
        class_name = node.get("class_name")
        if class_name:
            lines.append(f'    class {node["id"]} {class_name};')

    css_lines = [
        "/* Subscription Diagram Styling */",
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
        "mermaid": "\n".join(lines),
        "css_code": "\n".join(css_lines),
        "icon_map": {},
        "node_drilldown_map": node_map or {},
        "title": title,
        "description": description,
        "legend": legend or [],
        "attack_paths": attack_paths or [],
        "asset_summary": asset_summary or {},
    }


def build_subscription_attack_paths(
    assets: list[dict],
    scope_label: str,
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
) -> list[dict]:
    entries = [a for a in assets if a.get("tier") == "entry"]
    apis = [a for a in assets if a.get("tier") == "api"]
    backends = [a for a in assets if a.get("tier") == "backend"]
    data = [a for a in assets if a.get("tier") == "data"]
    public_assets = [a for a in assets if a.get("public")]
    secret_stores = [a for a in data if subscription_is_secret_store(a.get("arm_type") or "")]
    public_data = [a for a in data if a.get("public")]

    raw_paths: list[dict] = []

    if entries and (apis or backends):
        chain = ["Internet", subscription_join_names(entries, 1)]
        if apis:
            chain.append(subscription_join_names(apis, 1))
        if backends:
            chain.append(subscription_join_names(backends, 1))
        raw_paths.append(
            {
                "title": f"Public ingress into {scope_label}",
                "path": " -> ".join(chain),
                "summary": "A public edge service can expose a reachable path into application workloads.",
                "impact": f"Initial foothold in {scope_label} could turn edge exposure into backend compromise.",
                "confidence": "medium" if any(e.get("has_waf") for e in entries) else "high",
                "source": "subscription-diagram",
                "evidence": [f"Public entry points: {subscription_join_names(entries)}"]
                + ([f"API tier: {subscription_join_names(apis)}"] if apis else []),
            }
        )

    public_backends = [a for a in backends if a.get("public")]
    if public_backends:
        raw_paths.append(
            {
                "title": f"Direct workload exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(public_backends, 2)}",
                "summary": "Public compute removes a gateway hop and gives attackers a direct path to code execution surfaces.",
                "impact": "A direct exploit can bypass upstream controls and land in application runtime.",
                "confidence": "high",
                "source": "subscription-diagram",
                "evidence": [f"Public backend workloads: {subscription_join_names(public_backends)}"],
            }
        )

    if not raw_paths and entries:
        raw_paths.append(
            {
                "title": f"Public edge exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(entries, 2)}",
                "summary": "The subscription exposes an internet-facing edge even when downstream routing details are not yet harvested.",
                "impact": "Treat the edge tier as the first attacker foothold and validate what workloads sit behind it.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Public entry points: {subscription_join_names(entries)}"],
            }
        )

    if backends and secret_stores:
        raw_paths.append(
            {
                "title": f"Secrets pivot from compute in {scope_label}",
                "path": f"{subscription_join_names(backends, 1)} -> {subscription_join_names(secret_stores, 2)}",
                "summary": "Compromised compute often pivots by reading secrets, connection strings, or app configuration.",
                "impact": "Secret theft can expand blast radius into databases, storage, or downstream APIs.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Secret-bearing services: {subscription_join_names(secret_stores)}"],
            }
        )

    non_secret_data = [a for a in data if a not in secret_stores]
    if backends and non_secret_data:
        raw_paths.append(
            {
                "title": f"Data access after workload compromise in {scope_label}",
                "path": f"{subscription_join_names(backends, 1)} -> {subscription_join_names(non_secret_data, 2)}",
                "summary": "Once attackers land on compute, data-plane resources become the obvious next objective.",
                "impact": "Could lead to data theft, tampering, or service disruption.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Data services in scope: {subscription_join_names(non_secret_data)}"],
            }
        )

    if public_data:
        raw_paths.append(
            {
                "title": f"Direct public data exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(public_data, 2)}",
                "summary": "Internet-reachable data services create direct attack paths without an application compromise step.",
                "impact": "Exposure may allow direct data access, enumeration, or brute-force attempts.",
                "confidence": "high",
                "source": "subscription-diagram",
                "evidence": [f"Public data services: {subscription_join_names(public_data)}"],
            }
        )

    if not raw_paths and not public_assets:
        raw_paths.append(
            {
                "title": f"No direct public path identified in {scope_label}",
                "path": "Private-only or internal-facing topology",
                "summary": "This view did not find an obvious internet-origin attack path from harvested exposure flags.",
                "impact": "Focus next on identity, CI/CD, and control-plane pivots rather than direct ingress.",
                "confidence": "low",
                "source": "subscription-diagram",
                "evidence": ["No harvested public assets in scope"],
            }
        )

    return normalize_attack_paths(raw_paths, reviewer="subscription-diagram")


def build_subscription_overlay_views(
    rows: list,
    *,
    sanitise_node_id: Callable[[str], str],
    friendly_type: Callable[[str], str],
    get_icon_path: Callable[[str], str | None],
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
    plan_links: list | None = None,
) -> dict:
    assets = subscription_assets_from_rows(rows, friendly_type)
    assets = subscription_apply_plan_hierarchy(assets, plan_links)
    entries = [a for a in assets if a.get("tier") == "entry"]
    apis = [a for a in assets if a.get("tier") == "api"]
    backends = [a for a in assets if a.get("tier") == "backend"]
    data = [a for a in assets if a.get("tier") == "data"]
    public_assets = [a for a in assets if a.get("public")]
    attack_paths = build_subscription_attack_paths(assets, "this subscription", normalize_attack_paths)
    asset_summary = {
        "entry_points": len(entries),
        "api_layer": len(apis),
        "backends": len(backends),
        "data_stores": len(data),
        "public_assets": len(public_assets),
    }

    def make_scope_nodes(selected_assets: list[dict], *, badges: bool, include_fqdn: bool) -> tuple[list[dict], dict]:
        nodes: list[dict] = [{"id": "Internet", "label": "Internet", "class_name": "internet"}]
        node_map: dict = {}
        seen = {"Internet"}
        for asset in selected_assets:
            node_id = subscription_node_id(asset, sanitise_node_id)
            if node_id in seen:
                continue
            seen.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "label": subscription_asset_label(asset, include_badges=badges, include_fqdn=include_fqdn),
                    "arm_type": asset.get("arm_type"),
                    "class_name": subscription_node_class(asset),
                }
            )
            subscription_register_node(node_map, asset, sanitise_node_id)
        return nodes, node_map

    exposure_assets: list[dict] = []
    exposure_assets.extend(entries[:4])
    exposure_assets.extend(apis[:2])
    exposure_assets.extend([a for a in backends if a.get("public")][:4])
    exposure_assets.extend([a for a in backends if not a.get("public")][:3] if (entries or apis) else [])
    exposure_assets.extend([a for a in data if a.get("public")][:3])
    exposure_assets.extend([a for a in data if not a.get("public")][:3] if (entries or apis or [a for a in backends if a.get("public")]) else [])
    exp_nodes, exp_node_map = make_scope_nodes(exposure_assets, badges=False, include_fqdn=True)
    exp_edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_exp_edge(src: str, dst: str, label: str, color: str, width: str = "2px") -> None:
        key = (src, dst, label)
        if key in seen_edges:
            return
        seen_edges.add(key)
        exp_edges.append({"src": src, "dst": dst, "label": label, "color": color, "width": width})

    for entry in entries[:4]:
        has_waf = entry.get("has_waf")
        waf_mode = (entry.get("waf_mode") or "").strip()
        if has_waf:
            if "prevention" in waf_mode.lower():
                waf_label = "WAF (Prev)"
                arrow_color = "#f97316"
            elif "detection" in waf_mode.lower():
                waf_label = "WAF (Det)"
                arrow_color = "#f59e0b"
            else:
                waf_label = "WAF"
                arrow_color = "#f97316"
            label = subscription_primary_fqdn(entry) or waf_label
        else:
            label = subscription_primary_fqdn(entry) or "Public edge"
            arrow_color = "#ef4444"
        add_exp_edge("Internet", subscription_node_id(entry, sanitise_node_id), label, arrow_color, "3px")
    for api in [a for a in apis if a.get("public")][:2]:
        add_exp_edge("Internet", subscription_node_id(api, sanitise_node_id), subscription_primary_fqdn(api) or "Public API", "#ef4444", "3px")
    for api in [a for a in apis if a.get("is_restricted") and not a.get("public")][:2]:
        add_exp_edge("Internet", subscription_node_id(api, sanitise_node_id), subscription_allowlist_label(api), "#f59e0b", "2px")
    for backend in [a for a in backends if a.get("public")][:4]:
        add_exp_edge("Internet", subscription_node_id(backend, sanitise_node_id), subscription_primary_fqdn(backend) or "Direct workload", "#ef4444", "3px")
    for backend in [a for a in backends if a.get("is_restricted") and not a.get("public") and subscription_is_allowlist_target(a)][:3]:
        add_exp_edge("Internet", subscription_node_id(backend, sanitise_node_id), subscription_allowlist_label(backend), "#f59e0b", "2px")
    for store in [a for a in data if a.get("public")][:3]:
        add_exp_edge("Internet", subscription_node_id(store, sanitise_node_id), subscription_primary_fqdn(store) or "Direct data plane", "#ef4444", "3px")
    for store in [a for a in data if a.get("is_restricted") and not a.get("public") and subscription_is_allowlist_target(a)][:3]:
        add_exp_edge("Internet", subscription_node_id(store, sanitise_node_id), subscription_allowlist_label(store), "#f59e0b", "2px")
    if entries and apis:
        for entry in entries[:2]:
            for api in apis[:2]:
                add_exp_edge(subscription_node_id(entry, sanitise_node_id), subscription_node_id(api, sanitise_node_id), "routing", "#f97316")
    elif entries and backends:
        for entry in entries[:2]:
            for backend in backends[:3]:
                add_exp_edge(subscription_node_id(entry, sanitise_node_id), subscription_node_id(backend, sanitise_node_id), "backend reach", "#f97316")
    if apis and backends:
        for api in apis[:2]:
            for backend in backends[:3]:
                add_exp_edge(subscription_node_id(api, sanitise_node_id), subscription_node_id(backend, sanitise_node_id), "backend reach", "#f59e0b")
    if backends and data and (entries or apis or [a for a in backends if a.get("public")]):
        for backend in backends[:2]:
            for store in data[:4]:
                add_exp_edge(subscription_node_id(backend, sanitise_node_id), subscription_node_id(store, sanitise_node_id), "reachable next hop", "#94a3b8")

    if plan_links:
        plan_assets = {
            ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower()): asset
            for asset in assets
            if "serverfarms" in (asset.get("arm_type") or "").lower()
        }
        site_assets = {
            ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower()): asset
            for asset in assets
            if "sites" in (asset.get("arm_type") or "").lower()
        }
        emitted_plan_edges: set[tuple[str, str]] = set()
        for site_rg, site_name, plan_rg, plan_name in plan_links:
            site_asset = site_assets.get(((site_name or "").lower(), (site_rg or "").lower()))
            plan_asset = plan_assets.get(((plan_name or "").lower(), (plan_rg or "").lower()))
            if not site_asset or not plan_asset:
                continue
            edge = (
                subscription_node_id(site_asset, sanitise_node_id),
                subscription_node_id(plan_asset, sanitise_node_id),
            )
            if edge in emitted_plan_edges:
                continue
            emitted_plan_edges.add(edge)
            add_exp_edge(edge[0], edge[1], "hosted on", "#ffffff")

    exposure_view = render_subscription_view(
        nodes=exp_nodes,
        edges=exp_edges,
        get_icon_path=get_icon_path,
        node_map=exp_node_map,
        title="Exposure view",
        description="Shows internet-reachable entry points, directly public assets, and the next internal hops they can reach.",
        legend=[
            "Orange edges: WAF-protected entry point (Prevention mode)",
            "Amber edges: WAF in Detection mode or IP-allowlisted access",
            "Red edges: directly public — no WAF or network restriction",
            "Grey edges: likely next hop once the public edge is crossed",
        ],
        attack_paths=attack_paths,
        asset_summary=asset_summary,
    )

    return {
        "exposure": exposure_view,
        "attack_paths_summary": attack_paths,
        "asset_summary": asset_summary,
    }


def build_subscription_diagrams_by_rg(
    sub_name: str,
    environment: str,
    rows: list,
    *,
    sanitise_node_id: Callable[[str], str],
    friendly_type: Callable[[str], str],
    get_icon_path: Callable[[str], str | None],
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
    plan_links: list | None = None,
) -> list[dict]:
    del sub_name, environment
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for asset in subscription_assets_from_rows(rows, friendly_type):
        groups[asset.get("rg") or "default"].append(asset)

    diagrams = []

    def rg_asset_summary(rg_assets: list[dict]) -> dict:
        return {
            "entry_points": sum(1 for asset in rg_assets if asset.get("tier") == "entry"),
            "api_layer": sum(1 for asset in rg_assets if asset.get("tier") == "api"),
            "backends": sum(1 for asset in rg_assets if asset.get("tier") == "backend"),
            "data_stores": sum(1 for asset in rg_assets if asset.get("tier") == "data"),
            "public_assets": sum(1 for asset in rg_assets if asset.get("public")),
        }

    def build_rg_view(rg: str, rg_assets: list[dict], mode: str) -> tuple[dict, int]:
        rg_assets = subscription_apply_plan_hierarchy(rg_assets, plan_links)
        entries = [a for a in rg_assets if a.get("tier") == "entry"]
        apis = [a for a in rg_assets if a.get("tier") == "api"]
        backends = [a for a in rg_assets if a.get("tier") == "backend"]
        data = [a for a in rg_assets if a.get("tier") == "data"]
        public_assets = [a for a in rg_assets if a.get("public")]
        summary = rg_asset_summary(rg_assets)

        nodes: list[dict] = []
        node_map: dict = {}
        seen_nodes: set[str] = set()
        if public_assets or entries:
            nodes.append({"id": "Internet", "label": "Internet", "class_name": "internet"})
            seen_nodes.add("Internet")

        def add_asset_node(asset: dict, *, badges: bool = False, include_fqdn: bool = False) -> None:
            node_id = subscription_node_id(asset, sanitise_node_id)
            if node_id in seen_nodes:
                return
            seen_nodes.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "label": subscription_asset_label(asset, include_badges=badges, include_fqdn=include_fqdn),
                    "arm_type": asset.get("arm_type"),
                    "class_name": subscription_node_class(asset),
                }
            )
            subscription_register_node(node_map, asset, sanitise_node_id)

        for asset in rg_assets:
            include = mode == "connectivity"
            if mode == "exposure":
                include = asset.get("public") or asset.get("is_restricted") or asset in entries or asset in apis or asset in backends[:3] or asset in data[:3]
            if include:
                add_asset_node(asset, badges=False, include_fqdn=mode == "exposure")

        edges: list[dict] = []
        edge_keys: set[tuple[str, str, str]] = set()

        def add_edge(src: str, dst: str, label: str, color: str, *, width: str = "2px", dasharray: str | None = None) -> None:
            if src not in seen_nodes or dst not in seen_nodes:
                return
            key = (src, dst, label)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edge: dict = {"src": src, "dst": dst, "label": label, "color": color, "width": width}
            if dasharray:
                edge["dasharray"] = dasharray
            edges.append(edge)

        for asset in public_assets:
            has_waf = asset.get("has_waf")
            waf_mode = (asset.get("waf_mode") or "").strip()
            if has_waf and "prevention" in waf_mode.lower():
                color = "#f97316"
            elif has_waf and "detection" in waf_mode.lower():
                color = "#f59e0b"
            elif has_waf:
                color = "#f97316"
            else:
                color = "#ef4444"
            label = subscription_primary_fqdn(asset) or ("public edge" if asset.get("tier") == "entry" else "direct public")
            add_edge("Internet", subscription_node_id(asset, sanitise_node_id), label, color, width="3px")

        for asset in [a for a in rg_assets if a.get("is_restricted") and not a.get("public") and subscription_is_allowlist_target(a)]:
            node_id = subscription_node_id(asset, sanitise_node_id)
            if node_id in seen_nodes:
                add_edge("Internet", node_id, subscription_allowlist_label(asset), "#f59e0b", width="2px")

        for entry in entries:
            if not entry.get("public"):
                continue
            targets = apis[:2] or backends[:3]
            for target in targets:
                add_edge(
                    subscription_node_id(entry, sanitise_node_id),
                    subscription_node_id(target, sanitise_node_id),
                    "routing",
                    "#f97316",
                )

        if apis and backends:
            for api in apis[:2]:
                for backend in backends[:3]:
                    add_edge(
                        subscription_node_id(api, sanitise_node_id),
                        subscription_node_id(backend, sanitise_node_id),
                        "backend reach",
                        "#f59e0b" if mode == "exposure" else "#ffffff",
                    )

        if backends and data:
            for backend in backends[:2]:
                for store in data[:4]:
                    add_edge(
                        subscription_node_id(backend, sanitise_node_id),
                        subscription_node_id(store, sanitise_node_id),
                        "reachable next hop" if mode == "exposure" else "data flow",
                        "#94a3b8" if mode == "exposure" else "#ffffff",
                    )

        if mode == "connectivity" and plan_links:
            plan_assets = {
                ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower()): asset
                for asset in rg_assets
                if "serverfarms" in (asset.get("arm_type") or "").lower()
            }
            site_assets = {
                ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower()): asset
                for asset in rg_assets
                if "sites" in (asset.get("arm_type") or "").lower()
            }
            for site_rg, site_name, plan_rg, plan_name in plan_links:
                site_asset = site_assets.get((site_name.lower(), site_rg.lower()))
                plan_asset = plan_assets.get((plan_name.lower(), plan_rg.lower()))
                if site_asset and plan_asset:
                    add_edge(subscription_node_id(site_asset, sanitise_node_id), subscription_node_id(plan_asset, sanitise_node_id), "hosted on", "#ffffff")

        descriptions = {
            "connectivity": "Shows inferred application, API, data, and hosting relationships inside this resource group.",
            "exposure": "Shows public and IP-restricted assets in this resource group and the next internal hops they appear to expose.",
        }
        legends = {
            "connectivity": [
                "Orange edges: WAF-protected entry point",
                "Red edges: directly public — no WAF or network restriction",
                "Amber edges: WAF in Detection mode or IP-allowlisted access",
                "White edges: inferred internal application or hosting flow",
            ],
            "exposure": [
                "Red edges: direct public surface — no WAF or restriction",
                "Orange edges: WAF (Prevention) protected entry",
                "Amber edges: WAF (Detection) or IP allowlist",
                "Grey edges: next likely internal hop",
            ],
        }
        attack_paths = build_subscription_attack_paths(rg_assets, f"resource group {rg}", normalize_attack_paths)
        return (
            render_subscription_view(
                nodes=nodes,
                edges=edges,
                get_icon_path=get_icon_path,
                node_map=node_map,
                direction="TD",
                title=f"{rg} - {mode.replace('_', ' ').title()}",
                description=descriptions.get(mode, ""),
                legend=legends.get(mode, []),
                attack_paths=attack_paths,
                asset_summary=summary,
            ),
            len(edges),
        )

    for rg in sorted(groups.keys()):
        rg_assets = groups[rg]
        connectivity_view, relationship_count = build_rg_view(rg, rg_assets, "connectivity")
        exposure_view, _ = build_rg_view(rg, rg_assets, "exposure")
        diagrams.append(
            {
                "rg": rg,
                "mermaid": connectivity_view["mermaid"],
                "css_code": connectivity_view["css_code"],
                "icon_map": {},
                "node_drilldown_map": connectivity_view["node_drilldown_map"],
                "asset_count": len(rg_assets),
                "public_count": sum(1 for asset in rg_assets if asset.get("public")),
                "relationship_count": relationship_count,
                "asset_summary": connectivity_view["asset_summary"],
                "attack_paths": connectivity_view.get("attack_paths", []),
                "default_view": "connectivity",
                "views": {
                    "connectivity": connectivity_view,
                    "exposure": exposure_view,
                },
            }
        )

    return diagrams
