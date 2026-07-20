from __future__ import annotations

from typing import Callable

try:
    from web.core.parsing import parse_json_dict, parse_json_list
except ImportError:
    from core.parsing import parse_json_dict, parse_json_list  # type: ignore


def normalize_architecture_view_mode(requested_view_mode: str) -> str:
    view_mode = (requested_view_mode or "").strip().lower()
    if view_mode == "mermaid":
        return "full"
    if view_mode == "overview":
        return "overview"
    if view_mode in {"reactflow", "full"}:
        return "full"
    return "overview"


def cloud_architecture_payload(
    *,
    conn,
    subscription_selector: str,
    requested_view_mode: str,
    experiment_id: str,
    repo_name: str | None,
    build_subscription_payload: Callable[[object, str, str], dict],
    latest_experiment_id: Callable[[object], str | None],
    build_experiment_payload: Callable[[object, str, str | None], dict],
) -> dict:
    view_mode = normalize_architecture_view_mode(requested_view_mode)
    if subscription_selector or not experiment_id:
        return build_subscription_payload(conn, subscription_selector, view_mode=view_mode)

    if not experiment_id:
        experiment_id = latest_experiment_id(conn) or ""
    if not experiment_id:
        return {
            "experiment_id": "",
            "repo_name": repo_name or "",
            "summary": {"resource_count": 0, "connection_count": 0, "provider_counts": []},
            "nodes": [],
            "edges": [],
            "message": "No cloud resources found.",
        }
    return build_experiment_payload(conn, experiment_id, repo_name)


def group_members_payload(conn, group_id: str) -> tuple[dict, int]:
    if not group_id or not group_id.startswith("group::"):
        return {"error": "Invalid group_id"}, 400

    parts = group_id.split("::")
    if len(parts) < 4:
        return {"error": "Malformed group_id"}, 400

    asset_type = parts[2]
    access_level = parts[3]
    is_public = 1 if access_level == "Public" else 0
    is_restricted = 1 if access_level == "IP Restricted" else 0

    if access_level == "Private":
        members = conn.execute(
            """
            SELECT
                id, name, type, resource_group, location, sku,
                fqdn, is_public, is_restricted, status
            FROM provisioned_assets
            WHERE type = ? AND is_public = 0 AND is_restricted = 0
            ORDER BY name
            LIMIT 500
            """,
            (asset_type,),
        ).fetchall()
    else:
        members = conn.execute(
            """
            SELECT
                id, name, type, resource_group, location, sku,
                fqdn, is_public, is_restricted, status
            FROM provisioned_assets
            WHERE type = ? AND is_public = ? AND is_restricted = ?
            ORDER BY name
            LIMIT 500
            """,
            (asset_type, is_public, is_restricted),
        ).fetchall()

    formatted_members = [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "icon": "📦",
            "details": {
                "resource_group": row["resource_group"] or "N/A",
                "location": row["location"] or "N/A",
                "sku": row["sku"] or "N/A",
                "fqdn": row["fqdn"] or "N/A",
                "status": row["status"] or "active",
            },
        }
        for row in members
    ]
    return {
        "group_id": group_id,
        "group_type": asset_type,
        "access_level": access_level,
        "members": formatted_members,
        "member_count": len(formatted_members),
    }, 200


def resource_children_payload(
    conn,
    resource_id: str,
    *,
    friendly_type: Callable[[str], str],
) -> tuple[dict, int]:
    if not resource_id:
        return {"error": "Missing resource_id parameter"}, 400

    parent_row = conn.execute(
        """
        SELECT
            pa.id, pa.name, pa.type, pa.subscription_id, pa.resource_group,
            s.display_name AS sub_name
        FROM provisioned_assets pa
        JOIN subscriptions s ON s.id = pa.subscription_id
        WHERE pa.id = ?
        """,
        (resource_id,),
    ).fetchone()
    if not parent_row:
        return {"error": "Resource not found"}, 404

    parent_type = parent_row["type"].lower()
    parent_name = parent_row["name"]
    subscription_id = parent_row["subscription_id"]
    children = []

    if "applicationgateways" in parent_type or "applicationgateway" in parent_type:
        listener_rows = conn.execute(
            """
            SELECT DISTINCT
                listener_name, hostname, protocol, backend_port, backend_protocol,
                backend_pool_name, waf_policy_name, exposure_level
            FROM appgw_routing_rules
            WHERE gateway_name = ? AND subscription_id = ?
            ORDER BY listener_name
            """,
            (parent_name, subscription_id),
        ).fetchall()
        for row in listener_rows:
            frontend_port = 443 if row["protocol"] == "Https" else 80
            children.append(
                {
                    "id": f"{resource_id}::listener::{row['listener_name']}",
                    "name": row["listener_name"],
                    "type": "HTTP Listener",
                    "icon": "🎧",
                    "details": {
                        "hostname": row["hostname"] or "All",
                        "protocol": row["protocol"] or "HTTP",
                        "frontend_port": frontend_port,
                        "backend_port": row["backend_port"] or "Unknown",
                        "backend_protocol": row["backend_protocol"] or "HTTP",
                        "backend_pool": row["backend_pool_name"] or "None",
                        "waf_policy": row["waf_policy_name"] or "None",
                        "exposure": row["exposure_level"] or "Unknown",
                    },
                }
            )
    elif "sql/servers" in parent_type and "/databases" not in parent_type:
        db_rows = conn.execute(
            """
            SELECT id, name, sku, status
            FROM provisioned_assets
            WHERE subscription_id = ?
            AND type LIKE '%Sql/servers/%/databases%'
            AND id LIKE ?
            AND name NOT IN ('master', 'model', 'msdb', 'tempdb')
            ORDER BY name
            """,
            (subscription_id, f"%{parent_name}/databases%"),
        ).fetchall()
        for row in db_rows:
            children.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "type": "SQL Database",
                    "icon": "🗄️",
                    "details": {"sku": row["sku"] or "Unknown", "status": row["status"] or "active"},
                }
            )
    elif "kubernetes" in parent_type or "containerservice" in parent_type:
        children.append(
            {
                "id": f"{resource_id}::namespace::default",
                "name": "default",
                "type": "Namespace",
                "icon": "📦",
                "details": {"note": "Namespace data requires separate harvesting"},
            }
        )
    elif "apimanagement/service" in parent_type:
        api_rows = conn.execute(
            """
            SELECT DISTINCT
                api_name, api_display_name, api_path, api_protocols, backend_url,
                exposure_level, requires_subscription
            FROM apim_api_routes
            WHERE apim_name = ? AND subscription_id = ?
            ORDER BY api_display_name
            """,
            (parent_name, subscription_id),
        ).fetchall()
        for row in api_rows:
            protocols_list = parse_json_list(row["api_protocols"])
            children.append(
                {
                    "id": f"{resource_id}::api::{row['api_name']}",
                    "name": row["api_display_name"] or row["api_name"],
                    "type": "API",
                    "icon": "🔌",
                    "details": {
                        "path": row["api_path"] or "/",
                        "protocols": ", ".join(protocols_list) if protocols_list else "HTTPS",
                        "backend": row["backend_url"] or "Unknown",
                        "exposure": row["exposure_level"] or "Unknown",
                        "requires_subscription": bool(row["requires_subscription"]),
                    },
                }
            )
    elif "sites" in parent_type and "slots" not in parent_type:
        slot_rows = conn.execute(
            """
            SELECT
                s.name, s.resource_group, s.fqdn, s.is_public, s.is_restricted, s.raw_json
            FROM provisioned_assets s
            WHERE s.subscription_id = ?
              AND LOWER(COALESCE(s.type, '')) LIKE '%/sites/slots%'
              AND (
                    LOWER(COALESCE(json_extract(s.raw_json, '$._extra.slot_parent'), '')) = LOWER(?)
                    OR LOWER(COALESCE(s.id, '')) LIKE '%' || LOWER(?) || '/slots/%'
                  )
            ORDER BY s.name
            LIMIT 50
            """,
            (subscription_id, parent_name, parent_name),
        ).fetchall()
        for row in slot_rows:
            try:
                slot_raw = parse_json_dict(row["raw_json"])
            except Exception:
                slot_raw = {}
            kind = str(slot_raw.get("kind") or "").strip()
            kind_label = "Function App Slot" if "functionapp" in kind.lower() or "function app" in kind.lower() else "App Service Slot"
            children.append(
                {
                    "id": f"{resource_id}::slot::{row['name']}",
                    "name": row["name"],
                    "type": kind_label,
                    "icon": "🌐",
                    "details": {
                        "resource_group": row["resource_group"] or "N/A",
                        "fqdn": row["fqdn"] or "N/A",
                        "exposure": "Public" if row["is_public"] else ("IP Restricted" if row["is_restricted"] else "Private"),
                        "kind": kind or "—",
                        "parent_app": parent_name,
                    },
                }
            )

    return {
        "parent_id": resource_id,
        "parent_name": parent_name,
        "parent_type": parent_row["type"],
        "children": children,
        "child_count": len(children),
    }, 200


def apim_child_apis_payload(conn, resource_id: str) -> tuple[dict, int]:
    if not resource_id:
        return {"error": "Missing resource_id parameter"}, 400

    apim_row = conn.execute(
        """
        SELECT
            pa.id, pa.name, pa.type, pa.subscription_id, pa.raw_json,
            s.display_name AS sub_name
        FROM provisioned_assets pa
        JOIN subscriptions s ON s.id = pa.subscription_id
        WHERE pa.id = ? AND pa.type LIKE '%ApiManagement/service%'
        """,
        (resource_id,),
    ).fetchone()
    if not apim_row:
        return {"error": "APIM resource not found"}, 404

    apim_name = apim_row["name"]
    subscription_id = apim_row["subscription_id"]
    api_rows = conn.execute(
        """
        SELECT DISTINCT
            api_name, api_display_name, api_path, api_protocols,
            backend_url, service_url, exposure_level, requires_subscription
        FROM apim_api_routes
        WHERE apim_name = ? AND subscription_id = ?
        ORDER BY api_display_name
        """,
        (apim_name, subscription_id),
    ).fetchall()

    operations_by_api: dict[str, list[dict]] = {}
    operation_rows = conn.execute(
        """
        SELECT
            api_name, operation_id, display_name, method, url_template, description, requires_subscription
        FROM apim_api_operations
        WHERE apim_name = ? AND subscription_id = ?
        ORDER BY api_name, method, url_template
        LIMIT 2000
        """,
        (apim_name, subscription_id),
    ).fetchall()
    for op in operation_rows:
        api_name_key = str(op["api_name"] or "")
        bucket = operations_by_api.setdefault(api_name_key, [])
        if len(bucket) >= 50:
            continue
        bucket.append(
            {
                "id": op["operation_id"],
                "name": op["display_name"] or op["operation_id"],
                "method": op["method"] or "GET",
                "path": op["url_template"] or "/",
                "description": op["description"] or "",
                "requires_subscription": bool(op["requires_subscription"]),
            }
        )

    child_apis = []
    for row in api_rows:
        protocols_list = parse_json_list(row["api_protocols"])
        api_name = row["api_name"]
        operations = operations_by_api.get(str(api_name), [])
        child_apis.append(
            {
                "id": f"{resource_id}::api::{api_name}",
                "name": row["api_display_name"] or api_name,
                "api_name": api_name,
                "path": row["api_path"] or "/",
                "protocols": protocols_list,
                "backend_url": row["backend_url"],
                "service_url": row["service_url"],
                "exposure": row["exposure_level"] or "Unknown",
                "requires_subscription": bool(row["requires_subscription"]),
                "operations": operations,
                "operation_count": len(operations),
            }
        )

    columns = [
        "API",
        "Path",
        "Protocols",
        "Exposure",
        "Subscription Key",
        "Operations",
        "Backend URL",
        "Service URL",
    ]
    rows = [
        [
            api["name"],
            api["path"],
            ", ".join(api["protocols"]) if api["protocols"] else "—",
            api["exposure"],
            "Required" if api["requires_subscription"] else "Not required",
            api.get("operation_count", 0),
            api["backend_url"] or "—",
            api["service_url"] or "—",
        ]
        for api in child_apis
    ]
    return {
        "id": resource_id,
        "title": f"{apim_name} — APIs",
        "name": apim_name,
        "type_label": "API Management",
        "resource_group": "",
        "view_type": "table",
        "columns": columns,
        "rows": rows,
        "empty_message": "No APIs were found for this APIM instance.",
        "api_count": len(child_apis),
        "child_apis": child_apis,
    }, 200
