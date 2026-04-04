#!/usr/bin/env python3
"""Generate Mermaid diagrams from database queries."""

import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db_helpers import get_db_connection, get_resources_for_diagram, get_connections_for_diagram
import resource_type_db as _rtdb
from shared_utils import _normalize_optional_bool

# Lazy DB connection for resource type lookups
_lookup_conn: sqlite3.Connection | None = None

def _get_lookup_db() -> sqlite3.Connection | None:
    global _lookup_conn
    if _lookup_conn is None:
        # Use the configured DB_PATH from resource_type_db (prefer cozo.db)
        db_path = None
        try:
            if getattr(_rtdb, 'DB_PATH', None) and Path(_rtdb.DB_PATH).exists():
                db_path = Path(_rtdb.DB_PATH)
        except Exception:
            pass
        if db_path:
            _lookup_conn = sqlite3.connect(str(db_path))
            # Return rows as mappings where callers expect dict-like access
            _lookup_conn.row_factory = sqlite3.Row
    return _lookup_conn


# Category → diagram stroke colour
_CATEGORY_COLOURS: dict[str, str] = {
    "Compute":   "#0066cc",
    "Container": "#0066cc",
    "Database":  "#00aa00",
    "Storage":   "#00aa00",
    "Identity":  "#f59f00",
    "Security":  "#ff6b6b",
    "Network":   "#7e57c2",
    "Monitoring":"#888888",
}


def get_node_style(resource: dict) -> Optional[str]:
    """Return Mermaid style string for a resource based on finding score or category."""
    node_id = sanitize_id(resource['resource_name'])
    score = resource.get('max_finding_score', 0)

    if score >= 9:
        return f"style {node_id} stroke:#ff0000, stroke-width:4px"
    if score >= 7:
        return f"style {node_id} stroke:#ff6b00, stroke-width:3px"

    conn = _get_lookup_db()
    if conn:
        category = _rtdb.get_category(conn, resource.get('resource_type', ''))
        colour = _CATEGORY_COLOURS.get(category)
        if colour:
            return f"style {node_id} stroke:{colour}, stroke-width:2px"

    return None


def _display_label(resource: dict) -> str:
    """Return diagram node label using real name + friendly type."""
    conn = _get_lookup_db()
    name = resource['resource_name']
    rtype = resource.get('resource_type', '')
    
    # Truncate long resource names to prevent diagram overflow
    MAX_NAME_LENGTH = 28
    display_name = name if len(name) <= MAX_NAME_LENGTH else f"{name[:MAX_NAME_LENGTH-3]}..."
    
    if conn and rtype:
        friendly = _rtdb.get_friendly_name(conn, rtype)
        return f"{display_name}<br/>{friendly}"
    return display_name


def _category(resource: dict) -> str:
    conn = _get_lookup_db()
    if conn:
        return _rtdb.get_category(conn, resource.get('resource_type', ''))
    return 'Other'


_INVALID_NODE_ID_CHARS = re.compile(r'[^A-Za-z0-9_]')


def sanitize_id(name: str) -> str:
    """Convert resource name to valid Mermaid node ID."""
    raw = str(name or "")
    sanitized = _INVALID_NODE_ID_CHARS.sub("_", raw)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_")

    if not sanitized:
        sanitized = "node"
    if sanitized[0].isdigit():
        sanitized = f"n{sanitized}"

    return sanitized


def _render_resource_subgraph(
    resource: dict,
    parent_children: dict,
    lines: list,
    indent: str = "  ",
    depth: int = 0,
    max_depth: int = 3,
) -> None:
    """Recursively render a resource and its children as Mermaid subgraphs."""
    node_id = sanitize_id(resource['resource_name'])
    children = parent_children.get(resource['id'], [])

    if children and depth < max_depth:
        child_count = len(children)
        try:
            rt_meta = _rtdb.get_resource_type(None, resource.get('resource_type', ''))
            friendly_type = rt_meta.get('friendly_name', resource.get('resource_type', 'Resource'))
        except Exception:
            friendly_type = resource.get('resource_type', 'Resource')

        label = f"{friendly_type}: {resource['resource_name']} ({child_count} sub-asset{'s' if child_count != 1 else ''})"
        lines.append(f"{indent}subgraph {node_id}_sg[\"{label}\"]")
        for child_row in children:
            child_resource = {
                'id': child_row['child_id'],
                'resource_name': child_row['child_name'],
                'resource_type': child_row['child_type'],
            }
            _render_resource_subgraph(child_resource, parent_children, lines, indent + "  ", depth + 1, max_depth)
        lines.append(f"{indent}end")
    else:
        label = _display_label(resource)
        lines.append(f"{indent}{node_id}[{label}]")


def _should_show_on_diagram(resource: dict, child_ids: set) -> bool:
    """Return False for resources marked as hidden that aren't shown as children."""
    if resource['id'] in child_ids:
        return False  # Will be shown under parent
    try:
        rt_meta = _rtdb.get_resource_type(None, resource.get('resource_type', ''))
        return rt_meta.get('display_on_architecture_chart', True)
    except Exception:
        return True


def _is_operation_resource_type(resource_type: str) -> bool:
    """Return True when a resource type represents an API operation-level entity.

    Architecture diagrams should focus on service/API-level nodes. Operation-level
    resources are extremely high-cardinality and make graphs unreadable.
    """
    rt = (resource_type or '').strip().lower()
    if not rt:
        return False
    return (
        'api_operation' in rt
        or rt.endswith('_operation')
        or '.operation' in rt
    )


def _is_non_service_resource_type(resource_type: str) -> bool:
    """Return True when a resource is config/metadata rather than a cloud service.

    Architecture diagrams should emphasize deployable/routable services, not
    settings, dashboard widgets, alerts, identity/group metadata, or template
    helper artifacts.
    """
    rt = (resource_type or '').strip().lower()
    if not rt:
        return False

    exact_exclusions = {
        'azurerm_app_configuration',
        'azurerm_app_configuration_key',
        'azurerm_client_config',
        'azurerm_subscription',
        'azurerm_api_management_subscription',
        'azurerm_api_management_user',
        'azurerm_portal_dashboard',
        'azurerm_monitor_metric_alert',
        'azurerm_monitor_scheduled_query_rules_alert',
        'azurerm_monitor_scheduled_query_rules_alert_v2',
        'template_file',
        'kubernetes_config',
    }
    if rt in exact_exclusions:
        return True

    token_exclusions = (
        'configuration_key',
        'client_config',
        'template_file',
        'portal_dashboard',
        'metric_alert',
        'scheduled_query_rules_alert',
        'api_management_subscription',
        'api_management_user',
    )
    return any(tok in rt for tok in token_exclusions)


def _connection_label(connection: dict, *, ip_restricted: bool = False) -> str:
    protocol = str(connection.get("protocol") or "").strip()
    port = str(connection.get("port") or "").strip()
    connection_type = str(connection.get("connection_type") or "").strip().replace("_", " ")
    auth_method = str(connection.get("auth_method") or "").strip()
    source_name = str(connection.get("source") or "").strip()
    target_name = str(connection.get("target") or "").strip()
    via_component = str(connection.get("via_component") or "").strip()
    encrypted = _normalize_optional_bool(connection.get("is_encrypted"))

    details: List[str] = []
    transport = ""
    if protocol and port:
        transport = f"{protocol}:{port}"
    elif protocol:
        transport = protocol
    elif connection_type:
        transport = connection_type
    if transport:
        details.append(transport)
    if connection_type and connection_type.lower() != transport.lower():
        details.append(connection_type)
    if auth_method:
        details.append(f"auth={auth_method}")
    if encrypted is True:
        details.append("encrypted")
    elif encrypted is False:
        details.append("unencrypted")
    if via_component and via_component not in {source_name, target_name}:
        details.append(f"via {via_component}")
    if ip_restricted:
        details.append("IP-restricted")

    if not details:
        return ""
    return f"|{'; '.join(details)}|"


def _add_internet_connections(connections: list, experiment_id: str, repo_name: str | None = None) -> list:
    """Synthesise Internet→resource edges from internet-exposure findings.

    Looks for findings whose rules carry internet_exposure=true metadata,
    plus legacy context keys for backward compatibility.
    """
    existing_internet_targets = {c['target'] for c in connections if c.get('source') == 'Internet'}

    try:
        with get_db_connection() as conn:
            repo_filter = ""
            params_base = [experiment_id]
            if repo_name:
                repo_filter = "AND LOWER(repo.repo_name) = LOWER(?)"
                params_base.append(repo_name)

            # Primary: metadata.internet_exposure = true
            rows = conn.execute(f"""
                SELECT DISTINCT
                    COALESCE(parent.resource_name, r.resource_name) AS target_name,
                    COALESCE(parent.resource_type, r.resource_type) AS target_type
                FROM findings f
                JOIN resources r ON f.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                JOIN finding_context fc ON fc.finding_id = f.id
                WHERE f.experiment_id = ?
                  AND fc.context_key = 'metadata.internet_exposure'
                  AND LOWER(fc.context_value) = 'true'
                  {repo_filter}
            """, params_base).fetchall()

            for row in rows:
                name = row['target_name']
                if name and name not in existing_internet_targets:
                    connections.append({
                        'source': 'Internet', 'target': name,
                        'label': 'internet exposed', 'connection_type': 'internet_access',
                        'is_cross_repo': 0
                    })
                    existing_internet_targets.add(name)

            # Fallback: start_ip_address = 0.0.0.0 (legacy / firewall rule direct)
            rows2 = conn.execute(f"""
                SELECT DISTINCT COALESCE(parent.resource_name, r.resource_name) AS target_name
                FROM findings f
                JOIN resources r ON f.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                JOIN finding_context fc ON fc.finding_id = f.id
                WHERE f.experiment_id = ?
                  AND LOWER(fc.context_key) IN ('start_ip_address', 'start_ip', '$val')
                  AND fc.context_value = '0.0.0.0'
                  {repo_filter}
            """, params_base).fetchall()
            for row in rows2:
                name = row['target_name']
                if name and name not in existing_internet_targets:
                    connections.append({
                        'source': 'Internet', 'target': name,
                        'label': 'firewall: 0.0.0.0', 'connection_type': 'internet_access',
                        'is_cross_repo': 0
                    })
                    existing_internet_targets.add(name)

    except Exception:
        pass

    return connections


def generate_architecture_diagram(
    experiment_id: str,
    repo_name: str | None = None,
    provider: str | None = None,
    include_operation_resources: bool | None = None,
) -> str:
    # Support experiment folder names like '001_001' by falling back to numeric prefix if no rows
    from db_helpers import get_db_connection as _get_db_conn
    with _get_db_conn() as _conn:
        check = _conn.execute("SELECT COUNT(1) as c FROM repositories WHERE experiment_id = ?", [experiment_id]).fetchone()
        if check and check['c'] == 0 and '_' in experiment_id:
            alt = experiment_id.split('_')[0]
            # If numeric prefix exists in DB, use it
            alt_check = _conn.execute("SELECT COUNT(1) as c FROM repositories WHERE experiment_id = ?", [alt]).fetchone()
            if alt_check and alt_check['c'] > 0:
                experiment_id = alt

    """Generate full architecture diagram from database with parent-child hierarchies.

    Optional `provider` can be provided to limit diagram to a single cloud provider
    (e.g., 'aws', 'azure', 'gcp', 'oracle'). When provided, resources and
    connections are filtered to that provider so per-provider Architecture_*.md
    files can be produced.
    """
    
    # Prefer canonical helpers that return merged properties
    resources = get_resources_for_diagram(experiment_id)
    hierarchies = []
    connections = get_connections_for_diagram(experiment_id, repo_name=repo_name)
    # Exclude permission-style edges (grants_access_to) from architecture diagrams
    connections = [c for c in connections if str(c.get("connection_type") or "").strip() != "grants_access_to"]

    # If a specific repo is requested, filter resources to that repo
    if repo_name:
        resources = [r for r in resources if r.get('repo_name') == repo_name]

    # If a provider filter is requested, limit resources and connections to it
    if provider:
        prov_lower = provider.lower()
        resources = [r for r in resources if (r.get('provider') or '').lower() == prov_lower]
        # Only keep connections where at least one endpoint is in the filtered resource set
        resource_names = {r['resource_name'] for r in resources}
        connections = [c for c in connections if (c.get('source') in resource_names or c.get('target') in resource_names)]

    # Exclude resource types that are explicitly marked as not to be displayed on architecture charts
    operation_count_in_scope = sum(
        1 for r in resources if _is_operation_resource_type((r.get('resource_type') or ''))
    )
    if include_operation_resources is None:
        include_operation_resources = operation_count_in_scope < 10

    # If API Management (APIM) or explicit API gateway resources are present,
    # prefer showing operation-level resources so the diagrams show APIs → Operations.
    try:
        apim_present = any(
            ('api_management' in (r.get('resource_type') or '').lower())
            or ('apim' in (r.get('resource_name') or '').lower())
            or ('api_management_api' in (r.get('resource_type') or '').lower())
            for r in resources
        )
        if apim_present:
            include_operation_resources = True
    except Exception:
        pass
    try:
        # Use resource_type_db to determine display preference (fallbacks handled inside)
        _display_filtered = []
        for r in resources:
            rt = (r.get('resource_type') or '').strip()
            rt_info = _rtdb.get_resource_type(None, rt)
            # Explicitly exclude resource groups (some legacy rows use non-standard type names)
            is_resource_group = 'resource_group' in rt.lower() or (r.get('resource_name') or '').lower().endswith('resource group')
            is_operation_resource = _is_operation_resource_type(rt)
            is_non_service_resource = _is_non_service_resource_type(rt)
            if rt_info.get('display_on_architecture_chart', True) and not is_resource_group and (include_operation_resources or not is_operation_resource) and not is_non_service_resource:
                _display_filtered.append(r)
        resources = _display_filtered
        # Also filter connections to endpoints that remain
        allowed_names = {r['resource_name'] for r in resources}
        connections = [c for c in connections if c.get('source') in allowed_names and c.get('target') in allowed_names]
    except Exception:
        # Best-effort: if resource_type_db lookup fails, continue without filtering
        pass

    # Append synthetic Internet connections based on finding_context evidence.
    # This runs AFTER the allowed_names filter so 'Internet' is never wrongly excluded.
    connections = _add_internet_connections(connections, experiment_id, repo_name=repo_name)

    # hierarchies can be built by joining resources with parent relationships if needed
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT parent.id as parent_id, parent.resource_name as parent_name, parent.resource_type as parent_type,
                   child.id as child_id, child.resource_name as child_name, child.resource_type as child_type
            FROM resources parent
            JOIN resources child ON child.parent_resource_id = parent.id
            WHERE parent.experiment_id = ?
            ORDER BY parent.resource_name, child.resource_name
        """, [experiment_id]).fetchall()
        hierarchies = rows

    
    if not resources:
        return "flowchart LR\n  empty[No resources found]"
    
    lines = ["flowchart LR"]
    
    # Build parent-child mapping
    parent_children: Dict[int, List] = {}
    child_ids = set()
    for h in hierarchies:
        parent_id = h['parent_id']
        if parent_id not in parent_children:
            parent_children[parent_id] = []
        parent_children[parent_id].append(h)
        child_ids.add(h['child_id'])
    
    # Group resources by type (excluding children as they'll be in parent subgraphs)
    root_resources = [r for r in resources if r['id'] not in child_ids]

    # Group by canonical render category (provider-agnostic)
    def _in_render_cat(r: dict, *cats: str) -> bool:
        try:
            rc = _rtdb.get_render_category(None, r.get('resource_type') or '')
        except Exception:
            # Fallback to old category
            rc = _category(r)
        return rc in cats

    def _is_public_ip_resource_obj(r: dict) -> bool:
        """Return True if a resource appears to be a standalone Public IP resource (EIP/Azure Public IP)."""
        if not r or not r.get('resource_type'):
            return False
        rt = (r.get('resource_type') or '').lower()
        return any(tok in rt for tok in ('public_ip', 'elastic_ip', 'eip', 'publicip'))

    # Exclude standalone public IP resources from node lists — they'll be collapsed into Internet edges
    filtered_roots = [r for r in root_resources if not _is_public_ip_resource_obj(r)]
    # Exclude resources explicitly hidden from architecture diagrams (unless already filtered as children)
    filtered_roots = [r for r in filtered_roots if _should_show_on_diagram(r, child_ids)]

    vms            = [r for r in filtered_roots if _in_render_cat(r, 'Compute')]
    aks            = [r for r in filtered_roots if _in_render_cat(r, 'Container')]
    sql_servers    = [r for r in filtered_roots if _in_render_cat(r, 'Database')]
    storage_accounts = [r for r in filtered_roots if _in_render_cat(r, 'Storage')]
    # Network/Firewall nodes: only include true firewall/appliance devices for Network category
    nsgs           = [r for r in filtered_roots if _in_render_cat(r, 'Firewall') or (_in_render_cat(r, 'Security') and 'nsg' in r.get('resource_type','').lower()) or (_in_render_cat(r, 'Network') and _rtdb.is_physical_network_device(None, r.get('resource_type','')))]
    paas           = [r for r in filtered_roots if _in_render_cat(r, 'Identity') and r['id'] not in child_ids]
    other          = [r for r in filtered_roots if r not in vms + aks + sql_servers + storage_accounts + nsgs + paas]
    
    # Always add Internet node to show request flow from Internet to services
    # (even if no explicit internet-exposure findings exist)
    lines.append("  internet[Internet]")
    has_internet_connections = any(c['source'] == 'Internet' or c['target'] == 'Internet' for c in connections)
    
    # VNet subgraph (VMs, AKS, NSG)
    if vms or aks or nsgs:
        lines.append("  subgraph vnet[VNet]")
        
        for vm in vms:
            _render_resource_subgraph(vm, parent_children, lines, indent="    ")
        
        for aks_cluster in aks:
            _render_resource_subgraph(aks_cluster, parent_children, lines, indent="    ")
        
        for nsg in nsgs:
            node_id = sanitize_id(nsg['resource_name'])
            lines.append(f"    {node_id}[{_display_label(nsg)}]")
        
        lines.append("  end")
    
    # Database hierarchies (any DB type with children)
    for db in sql_servers:
        _render_resource_subgraph(db, parent_children, lines, indent="  ")
    
    # Storage hierarchies (any storage type with children)
    for sa in storage_accounts:
        _render_resource_subgraph(sa, parent_children, lines, indent="  ")
    
    # PaaS subgraph
    if paas:
        lines.append(f"  subgraph paas[PaaS / Identity]")
        for p in paas:
            node_id = sanitize_id(p['resource_name'])
            lines.append(f"    {node_id}[{_display_label(p)}]")
        lines.append("  end")
    
    # Try to group API Management / API resources and render operations when available
    try:
        api_resources = [r for r in resources if 'api' in (r.get('resource_type') or '').lower() or 'apim' in (r.get('resource_name') or '').lower()]
        if api_resources:
            for api in api_resources:
                api_name = api['resource_name']
                # Find operation-level children (resource_type indicates operation)
                ops = [r for r in resources if _is_operation_resource_type(r.get('resource_type') or '') and (r.get('parent_resource_name') == api_name or r.get('parent_resource_id') == api.get('id'))]
                if ops:
                    api_id = sanitize_id(api_name)
                    lines.append(f"  subgraph {api_id}_api[API: {api_name}]")
                    for op in ops:
                        op_id = sanitize_id(op['resource_name'])
                        lines.append(f"    {op_id}[Operation: {op['resource_name']}]")
                    lines.append("  end")
    except Exception:
        pass

    # Other resources (outside subgraphs)
    for res in other:
        if res['resource_name'] not in ('Internet', 'NSG'):
            node_id = sanitize_id(res['resource_name'])
            lines.append(f"  {node_id}[{_display_label(res)}]")
    
    lines.append("")
    
    # Add connections
    # Build a quick lookup of resources by name for property inspection
    resource_map = {r['resource_name']: r for r in resources}

    # Resource names that correspond to operation-level entities (use DB names to
    # avoid re-introducing operations via _ensure_node_exists).
    operation_resource_names = set()
    non_service_resource_names = {
        r['resource_name']
        for r in get_resources_for_diagram(experiment_id)
        if _is_non_service_resource_type(r.get('resource_type') or '')
        and (not repo_name or r.get('repo_name') == repo_name)
        and (not provider or (r.get('provider') or '').lower() == provider.lower())
    }
    if not include_operation_resources:
        operation_resource_names = {
            r['resource_name']
            for r in get_resources_for_diagram(experiment_id)
            if _is_operation_resource_type(r.get('resource_type') or '')
            and (not repo_name or r.get('repo_name') == repo_name)
            and (not provider or (r.get('provider') or '').lower() == provider.lower())
        }

    # Track which resource nodes have been emitted so we can create missing endpoints
    node_names_present = {'Internet'} | {r['resource_name'] for r in resources}

    # Helper to ensure a resource node exists in the diagram for a given resource name
    def _ensure_node_exists(resource_name: str):
        if not resource_name or resource_name in node_names_present:
            return
        if resource_name in non_service_resource_names:
            return
        if resource_name in operation_resource_names:
            return
        # Try to fetch minimal info from DB if available
        from db_helpers import get_db_connection
        with get_db_connection() as _conn:
            row = _conn.execute("SELECT resource_name, resource_type FROM resources WHERE experiment_id = ? AND resource_name = ? LIMIT 1", [experiment_id, resource_name]).fetchone()
            if row:
                if (not include_operation_resources) and _is_operation_resource_type(row['resource_type']):
                    return
                if _is_non_service_resource_type(row['resource_type']):
                    return
                node_id = sanitize_id(row['resource_name'])
                lines.append(f"  {node_id}[{row['resource_name']}]")
                node_names_present.add(row['resource_name'])

    def _is_public_ip_resource_name(name: str) -> bool:
        if not name:
            return False
        r = resource_map.get(name)
        if not r:
            return False
        rt = (r.get('resource_type') or '').lower()
        if any(tok in rt for tok in ('public_ip', 'elastic_ip', 'eip', 'publicip')):
            return True
        # Also treat explicit 'public' property on a dedicated resource as a hint
        if r.get('public') and r.get('resource_type') and 'ip' in r.get('resource_type').lower():
            return True
        return False

    # Prioritise connections so diagram reads as layers (Internet -> Container -> App -> Identity/KeyVault)
    LAYER_ORDER = {
        'internet': 0,
        'Container': 1,
        'Compute': 2,
        'Network': 3,
        'Firewall': 3,
        'Identity': 4,
        'Database': 5,
        'Storage': 6,
        'Other': 7,
    }

    def _layer_of(name: str) -> int:
        if not name:
            return 7
        if name == 'Internet':
            return LAYER_ORDER['internet']
        r = resource_map.get(name)
        if not r:
            return LAYER_ORDER['Other']
        try:
            cat = _rtdb.get_render_category(None, r.get('resource_type') or '')
            return LAYER_ORDER.get(cat, LAYER_ORDER['Other'])
        except Exception:
            return LAYER_ORDER['Other']

    # Sort connections by source layer then target layer so arrows flow top->down logically
    def _conn_sort_key(c):
        src_name = c.get('source') or ''
        tgt_name = c.get('target') or ''
        return (_layer_of(src_name), _layer_of(tgt_name))

    connections_sorted = sorted(connections, key=_conn_sort_key)

    # Keep track of emitted edges to avoid duplicates when collapsing Public IP nodes
    emitted_edges = set()

    for conn in connections_sorted:
        src_name = conn.get('source')
        tgt_name = conn.get('target')

        # If connection involves a standalone Public IP resource, collapse it into an Internet->resource edge labeled 'public IP'
        if _is_public_ip_resource_name(src_name) or _is_public_ip_resource_name(tgt_name):
            # Determine the 'real' resource (the non-public-ip endpoint)
            if _is_public_ip_resource_name(src_name):
                public_side = src_name
                real_name = tgt_name
            else:
                public_side = tgt_name
                real_name = src_name

            # Ensure the real resource node exists
            _ensure_node_exists(real_name)

            # Ensure Internet node is present
            if 'Internet' not in node_names_present:
                lines.append("  internet[Internet]")
                node_names_present.add('Internet')

            # Build label: include 'public IP' plus any transport/auth details
            ip_restricted = False
            if real_name and real_name in resource_map:
                rreal = resource_map[real_name]
                if rreal.get('network_acls') or rreal.get('firewall_rules'):
                    ip_restricted = True

            base_label = _connection_label(conn, ip_restricted=ip_restricted)
            inner = base_label.strip('|') if base_label else ''
            new_inner = f"public IP; {inner}" if inner else "public IP"
            new_label = f"|{new_inner}|"

            edge = ("Internet", real_name, new_label)
            if edge not in emitted_edges:
                emitted_edges.add(edge)
                # direction Internet -> real resource
                src = sanitize_id('Internet')
                tgt = sanitize_id(real_name)
                lines.append(f"  {src} -->{new_label} {tgt}")
            # Skip normal processing for this connection
            continue

        # Ensure endpoint nodes exist so arrows are drawn
        _ensure_node_exists(src_name)
        _ensure_node_exists(tgt_name)

        src = sanitize_id(conn['source'])
        tgt = sanitize_id(conn['target'])

        # If target resource has network ACLs or firewall rules, mark as IP-restricted
        ip_restricted = False
        target_name = conn.get('target')
        if target_name and target_name in resource_map:
            tgt_res = resource_map[target_name]
            if tgt_res.get('network_acls'):
                ip_restricted = True
            elif tgt_res.get('firewall_rules'):
                ip_restricted = True

        label = _connection_label(conn, ip_restricted=ip_restricted)

        # Cross-repo connections use dashed line; safe access for is_cross_repo
        is_cross = conn.get('is_cross_repo') if isinstance(conn, dict) else conn['is_cross_repo']
        if is_cross:
            lines.append(f"  {src} -.{label}.> {tgt}")
        else:
            lines.append(f"  {src} -->{label} {tgt}")
    
    lines.append("")
    
    # Styling based on findings
    for r in resources:
        style = get_node_style(r)
        if style:
            lines.append(f"  {style}")
    
    if has_internet_connections:
        lines.append("  style internet stroke:#ff0000, stroke-width:3px")
    
    return "\n".join(lines)


def generate_security_view(experiment_id: str, min_score: int = 7) -> str:
    """Generate diagram showing only resources with findings."""
    
    with get_db_connection() as conn:
        # Get vulnerable resources
        vulnerable = conn.execute("""
            SELECT DISTINCT
              r.resource_name,
              r.resource_type,
              MAX(COALESCE(f.severity_score, f.score, 0)) as max_score
            FROM resources r
            JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ? AND COALESCE(f.severity_score, f.score, 0) >= ?
            GROUP BY r.id
            ORDER BY max_score DESC
        """, [experiment_id, min_score]).fetchall()
        
        if not vulnerable:
            return f"flowchart TB\n  empty[No resources with score >= {min_score}]"
        
        # Get connections between vulnerable resources
        vulnerable_names = {r['resource_name'] for r in vulnerable}
        
        connections = conn.execute("""
            SELECT 
              r_src.resource_name as source,
              r_tgt.resource_name as target,
              rc.protocol
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id]).fetchall()
        
        # Filter connections to only those involving vulnerable resources
        relevant_connections = [
            c for c in connections 
            if c['source'] in vulnerable_names or c['target'] in vulnerable_names
        ]
    
    lines = ["flowchart TB"]
    
    # Add vulnerable resources
    for r in vulnerable:
        node_id = sanitize_id(r['resource_name'])
        score = r['max_score']
        severity = "🔴 Critical" if score >= 9 else "🟠 High"
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}<br/>{severity} {score}/10]")
    
    lines.append("")
    
    # Add connections
    for conn in relevant_connections:
        src = sanitize_id(conn['source'])
        tgt = sanitize_id(conn['target'])
        label = f"|{conn['protocol']}|" if conn['protocol'] else ""
        lines.append(f"  {src} -->{label} {tgt}")
    
    lines.append("")
    
    # Styling
    for r in vulnerable:
        style = get_node_style(r)
        if style:
            lines.append(f"  {style}")
    
    return "\n".join(lines)


def generate_blast_radius_diagram(experiment_id: str, compromised_resource: str) -> str:
    """Show what attacker can reach from compromised resource."""
    
    with get_db_connection() as conn:
        # Recursive CTE to find all reachable resources
        reachable = conn.execute("""
            WITH RECURSIVE blast_radius AS (
              SELECT 
                rc.target_resource_id as resource_id,
                1 as hop_count,
                r_src.resource_name || ' → ' || r_tgt.resource_name as path
              FROM resource_connections rc
              JOIN resources r_src ON rc.source_resource_id = r_src.id
              JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
              WHERE r_src.resource_name = ?
                AND r_src.experiment_id = ?
              
              UNION ALL
              
              SELECT 
                rc.target_resource_id,
                br.hop_count + 1,
                br.path || ' → ' || r.resource_name
              FROM blast_radius br
              JOIN resource_connections rc ON br.resource_id = rc.source_resource_id
              JOIN resources r ON rc.target_resource_id = r.id
              WHERE br.hop_count < 5
            )
            SELECT DISTINCT 
              r.resource_name, 
              r.resource_type,
              br.hop_count, 
              br.path
            FROM blast_radius br
            JOIN resources r ON br.resource_id = r.id
            ORDER BY br.hop_count
        """, [compromised_resource, experiment_id]).fetchall()
    
    if not reachable:
        return f"flowchart LR\n  compromised[{compromised_resource}]\n  empty[No connections found]"
    
    lines = ["flowchart LR"]
    lines.append(f"  compromised[🔴 {compromised_resource}<br/>COMPROMISED]:::compromised")
    
    # Add reachable resources
    for r in reachable:
        node_id = sanitize_id(r['resource_name'])
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}<br/>Hop {r['hop_count']}]")
    
    lines.append("")
    
    # Show paths
    seen_edges = set()
    for r in reachable:
        path_parts = r['path'].split(' → ')
        for i in range(len(path_parts) - 1):
            src = sanitize_id(path_parts[i])
            tgt = sanitize_id(path_parts[i + 1])
            edge = (src, tgt)
            if edge not in seen_edges:
                lines.append(f"  {src} ==> {tgt}")
                seen_edges.add(edge)
    
    lines.append("")
    lines.append("  classDef compromised stroke:#ff0000, stroke-width:6px")
    
    return "\n".join(lines)


def generate_multi_repo_diagram(experiment_id: str) -> str:
    """Generate diagram showing all repos and cross-repo connections."""
    
    with get_db_connection() as conn:
        repos = conn.execute("""
            SELECT id, repo_name 
            FROM repositories 
            WHERE experiment_id = ?
        """, [experiment_id]).fetchall()
        
        if not repos:
            return "flowchart TB\n  empty[No repositories found]"
        
        lines = ["flowchart TB"]
        
        # Subgraph per repo
        for repo in repos:
            repo_id = repo['id']
            repo_name = repo['repo_name']
            repo_node = sanitize_id(repo_name)
            
            lines.append(f"  subgraph {repo_node}[Repository: {repo_name}]")
            
            # Get resources in this repo
            resources = conn.execute("""
                SELECT resource_name, resource_type
                FROM resources
                WHERE repo_id = ?
            """, [repo_id]).fetchall()
            
            for r in resources:
                node_id = f"{repo_node}_{sanitize_id(r['resource_name'])}"
                lines.append(f"    {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}]")
            
            lines.append("  end")
        
        lines.append("")
        
        # Add connections
        connections = conn.execute("""
            SELECT 
              repo_src.repo_name as src_repo,
              r_src.resource_name as src_name,
              repo_tgt.repo_name as tgt_repo,
              r_tgt.resource_name as tgt_name,
              rc.is_cross_repo,
              rc.connection_type,
              rc.protocol,
              rc.port,
              COALESCE(rc.auth_method, rc.authentication) as auth_method,
              rc.is_encrypted,
              rc.via_component
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            JOIN repositories repo_src ON r_src.repo_id = repo_src.id
            JOIN repositories repo_tgt ON r_tgt.repo_id = repo_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id]).fetchall()
        
        for c in connections:
            src_repo_node = sanitize_id(c['src_repo'])
            tgt_repo_node = sanitize_id(c['tgt_repo'])
            src = f"{src_repo_node}_{sanitize_id(c['src_name'])}"
            tgt = f"{tgt_repo_node}_{sanitize_id(c['tgt_name'])}"
            label = _connection_label(
                {
                    "source": c["src_name"],
                    "target": c["tgt_name"],
                    "connection_type": c["connection_type"],
                    "protocol": c["protocol"],
                    "port": c["port"],
                    "auth_method": c["auth_method"],
                    "is_encrypted": c["is_encrypted"],
                    "via_component": c["via_component"],
                }
            )
            
            # Style cross-repo connections differently
            if c['is_cross_repo']:
                lines.append(f"  {src} -.{label}.>|cross-repo| {tgt}")
            else:
                lines.append(f"  {src} -->{label} {tgt}")
    
    return "\n".join(lines)


def main():
    """CLI for diagram generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate Mermaid diagrams from database")
    parser.add_argument("experiment_id", help="Experiment ID (e.g., '008')")
    parser.add_argument("--type", choices=['architecture', 'security', 'blast-radius', 'multi-repo'],
                        default='architecture', help="Diagram type")
    parser.add_argument("--min-score", type=int, default=7, 
                        help="Minimum score for security view")
    parser.add_argument("--compromised", help="Compromised resource for blast radius")
    parser.add_argument("--output", type=Path, help="Output file or directory (default: stdout)")
    parser.add_argument("--repo", help="Repository name to limit diagram to (optional)")
    parser.add_argument("--split-by-provider", action="store_true", help="Write separate Architecture_<PROVIDER>.md files into --output directory")
    parser.add_argument("--no-db", action="store_true", help="Skip persisting diagrams to cloud_diagrams table")
    
    args = parser.parse_args()

    # Try to import upsert helper for DB persistence
    _upsert_diagram = None
    if not args.no_db:
        try:
            _root = Path(__file__).resolve().parents[2]
            sys.path.insert(0, str(_root))
            from Scripts.Persist.db_helpers import upsert_cloud_diagram as _upsert_diagram  # type: ignore
        except Exception:
            pass

    # Support per-provider split mode where multiple Architecture_<PROVIDER>.md files are produced
    if args.split_by_provider:
        if not args.output:
            print("Error: --output (directory) is required when using --split-by-provider", file=sys.stderr)
            sys.exit(1)
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Discover providers present in resources
        all_res = get_resources_for_diagram(args.experiment_id)
        providers = sorted({(r.get('provider') or 'unknown').lower() for r in all_res})
        for idx, prov in enumerate(providers):
            diag = generate_architecture_diagram(args.experiment_id, repo_name=args.repo, provider=prov)
            canonical = f"Architecture_{prov.title()}.md"
            fname = out_dir / canonical
            fname.write_text(diag)
            print(f"Wrote {fname}")
            # Remove legacy case-variant files (case-insensitive duplicates)
            for p in out_dir.glob("Architecture_*.md"):
                if p.name != canonical and p.name.lower() == canonical.lower():
                    try:
                        p.unlink()
                        print(f"Removed legacy filename {p}")
                    except Exception as e:
                        print(f"Warning: failed to remove legacy file {p}: {e}", file=sys.stderr)
            # Also persist to DB
            if _upsert_diagram:
                try:
                    _upsert_diagram(
                        experiment_id=args.experiment_id,
                        provider=prov,
                        diagram_title=f"{prov.capitalize()} Architecture",
                        mermaid_code=diag,
                        display_order=idx,
                    )
                    print(f"Persisted {prov} diagram to cloud_diagrams table")
                except Exception as e:
                    print(f"Warning: failed to persist {prov} diagram to DB: {e}", file=sys.stderr)
        sys.exit(0)

    if args.type == 'architecture':
        diagram = generate_architecture_diagram(args.experiment_id, repo_name=args.repo)
        provider = args.repo or 'all'
        title = "Architecture"
    elif args.type == 'security':
        diagram = generate_security_view(args.experiment_id, args.min_score)
        provider = 'security'
        title = "Security View"
    elif args.type == 'blast-radius':
        if not args.compromised:
            print("Error: --compromised required for blast-radius diagram", file=sys.stderr)
            sys.exit(1)
        diagram = generate_blast_radius_diagram(args.experiment_id, args.compromised)
        provider = 'blast-radius'
        title = f"Blast Radius — {args.compromised}"
    elif args.type == 'multi-repo':
        diagram = generate_multi_repo_diagram(args.experiment_id)
        provider = 'multi-repo'
        title = "Multi-Repo Architecture"

    # Persist to DB
    if _upsert_diagram:
        try:
            _upsert_diagram(
                experiment_id=args.experiment_id,
                provider=provider,
                diagram_title=title,
                mermaid_code=diagram,
            )
            print(f"Persisted diagram '{title}' to cloud_diagrams table")
        except Exception as e:
            print(f"Warning: failed to persist diagram to DB: {e}", file=sys.stderr)

    if args.output:
        args.output.write_text(diagram)
        print(f"Diagram written to {args.output}")
    else:
        print(diagram)


if __name__ == "__main__":
    main()
