#!/usr/bin/env python3
"""Generate Mermaid diagrams from database queries."""

import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db_helpers import get_db_connection, get_resources_for_diagram, get_connections_for_diagram
from internet_exposure_detector import InternetExposureDetector
import resource_type_db as _rtdb
from shared_utils import _normalize_optional_bool

# Setup logging for diagram generation
_logger = logging.getLogger(__name__)
if not _logger.handlers:
    _logger.addHandler(logging.StreamHandler())
    _logger.setLevel(logging.WARNING)

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
    "API":       "#00b4d8",  # Teal — distinct from Identity amber (#f59f00) and alert amber (#e8a202)
}


def get_node_style(resource: dict) -> Optional[str]:
    """Return Mermaid style string for a resource based on finding score or category.

    Severity thresholds (aligned with risk_scorer._SEVERITY_SCORES):
      CRITICAL >= 10 → red (#ff0000), thick border
      HIGH     >= 8  → red (#ff0000), medium border
      MEDIUM   >= 5  → orange (#ff9900), medium border
      LOW      >= 2  → yellow (#ffcc00), thin border
      clean    == 0  → category colour
    """
    node_id = sanitize_id(resource['resource_name'])
    score = resource.get('max_finding_score', 0)

    if score >= 10:
        return f"style {node_id} stroke:#ff0000,stroke-width:5px,color:#ffffff"
    if score >= 8:
        return f"style {node_id} stroke:#ff0000,stroke-width:3px"
    if score >= 5:
        return f"style {node_id} stroke:#ff9900,stroke-width:3px"
    if score >= 2:
        return f"style {node_id} stroke:#ffcc00,stroke-width:2px"

    conn = _get_lookup_db()
    if conn:
        category = _rtdb.get_category(conn, resource.get('resource_type', ''))
        colour = _CATEGORY_COLOURS.get(category)
        if colour:
            return f"style {node_id} stroke:{colour},stroke-width:2px"

    return None


def _display_label(resource: dict) -> str:
    """Return diagram node label using real name + friendly type + emoji icon."""
    conn = _get_lookup_db()
    name = resource['resource_name']
    rtype = resource.get('resource_type', '')

    # Truncate long resource names to prevent diagram overflow
    MAX_NAME_LENGTH = 28
    display_name = name if len(name) <= MAX_NAME_LENGTH else f"{name[:MAX_NAME_LENGTH-3]}..."

    # Emoji icon based on resource type
    icon = _resource_icon(rtype)
    label_name = f"{icon} {display_name}" if icon else display_name

    if conn and rtype:
        friendly = _rtdb.get_friendly_name(conn, rtype)
        return f"{label_name}<br/>{friendly}"
    return label_name


# Resource type keyword → emoji icon mapping
_RESOURCE_ICONS: list[tuple[list[str], str]] = [
    (["internet_gateway", "eip", "public_ip", "front_door", "cloudfront"], "🌐"),
    (["lb", "alb", "elb", "nlb", "load_balancer", "application_gateway", "cdn"], "⚖️"),
    (["waf", "firewall", "security_group", "network_acl", "nsg"], "🛡️"),
    (["instance", "vm", "virtual_machine", "ec2", "compute_instance"], "🖥️"),
    (["function", "lambda", "function_app"], "⚙️"),
    (["eks_cluster", "aks", "gke", "kubernetes_cluster", "ecs_cluster"], "☸️"),
    (["ecr", "container_registry", "acr"], "📦"),
    (["db_instance", "database", "sql", "mysql", "postgresql", "cosmos", "rds", "neptune", "dynamodb", "bigtable"], "🗄️"),
    (["s3_bucket", "storage_account", "gcs", "blob", "bucket", "oss_bucket"], "🪣"),
    (["iam_role", "iam_user", "iam_policy", "service_account", "managed_identity", "access_key"], "🔑"),
    (["vpc", "vnet", "virtual_network", "subnet"], "🔷"),
    (["route_table", "route", "peering"], "🔀"),
    (["sns", "sqs", "eventgrid", "pubsub", "kinesis", "mq"], "📨"),
    (["monitoring", "log_analytics", "cloudwatch", "diagnostic"], "📊"),
    (["key_vault", "kms", "secrets", "certificate"], "🔐"),
    (["api_gateway", "api_management", "apim"], "🔌"),
]


def _resource_icon(resource_type: str) -> str:
    """Return an emoji icon for a resource type."""
    rtype = (resource_type or "").lower()
    for keywords, emoji in _RESOURCE_ICONS:
        if any(kw in rtype for kw in keywords):
            return emoji
    return ""


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
    _emitted_ids: set | None = None,
) -> None:
    """Recursively render a resource and its children as Mermaid subgraphs.

    ``_emitted_ids`` is a set shared across all recursive calls so that
    resources with the same sanitized name (e.g. multiple Terraform resources
    all named ``bad`` or ``good``) receive a unique, qualified node ID instead
    of silently collapsing into a single Mermaid node.
    """
    if _emitted_ids is None:
        _emitted_ids = set()

    base_id = sanitize_id(resource['resource_name'])
    rtype = resource.get('resource_type', '')
    # Qualify with resource type when the base ID is already used by a different resource.
    if base_id in _emitted_ids:
        type_short = rtype.split('_', 2)[-1] if '_' in rtype else rtype
        candidate = sanitize_id(f"{type_short}_{resource['resource_name']}")
        if candidate in _emitted_ids:
            # Last resort: append DB id to guarantee uniqueness
            candidate = sanitize_id(f"{rtype}_{resource['resource_name']}_{resource.get('id', '')}")
        node_id = candidate
    else:
        node_id = base_id
    _emitted_ids.add(node_id)

    children = parent_children.get(resource['id'], [])

    if children and depth < max_depth:
        child_count = len(children)
        try:
            rt_meta = _rtdb.get_resource_type(None, rtype)
            friendly_type = rt_meta.get('friendly_name', rtype or 'Resource')
        except Exception:
            friendly_type = rtype or 'Resource'

        label = f"{friendly_type}: {resource['resource_name']} ({child_count} sub-asset{'s' if child_count != 1 else ''})"
        lines.append(f"{indent}subgraph {node_id}_sg[\"{label}\"]")
        for child_row in children:
            child_resource = {
                'id': child_row['child_id'],
                'resource_name': child_row['child_name'],
                'resource_type': child_row['child_type'],
            }
            _render_resource_subgraph(child_resource, parent_children, lines, indent + "  ", depth + 1, max_depth, _emitted_ids)
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
    settings, dashboard widgets, alerts, identity/group metadata, template
    helper artifacts, or terraform meta-resources (null, random, local, time).
    """
    rt = (resource_type or '').strip().lower()
    if not rt:
        return False

    # Explicit early checks for terraform meta-resources (case-insensitive)
    terraform_meta_prefixes = ('null_', 'random_', 'local_', 'time_', 'terraform_')
    if any(rt.startswith(prefix) for prefix in terraform_meta_prefixes):
        return True

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
        'azurerm_role_assignment',
        'azurerm_role_assignment_schedule',
        'azurerm_role_definition',
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


def _add_internet_connections(connections: list, experiment_id: str, repo_name: str | None = None, provider: str | None = None) -> list:
    """Synthesise Internet→resource edges from internet-exposure findings.

    Looks for findings whose rules carry internet_exposure=true metadata,
    plus legacy context keys for backward compatibility.
    Also adds edges for well-known internet-facing resource types (IGW, ELB, App Gateway, etc.)
    """
    existing_internet_targets = {c['target'] for c in connections if c.get('source') == 'Internet'}

    repo_filter = ""
    params_base = [experiment_id]
    if repo_name:
        repo_filter = "AND LOWER(repo.repo_name) = LOWER(?)"
        params_base.append(repo_name)

    with get_db_connection() as conn:
        # Primary: metadata.internet_exposure = true (requires finding_context table)
        try:
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
        except Exception:
            pass

        # Fallback: start_ip_address = 0.0.0.0 (requires finding_context table)
        try:
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

        # Type-based fallback: well-known internet-facing resource types
        try:
            INTERNET_FACING_TYPES = tuple(sorted(InternetExposureDetector.get_public_entry_types()))
            placeholder = ','.join('?' * len(INTERNET_FACING_TYPES))
            type_params = [experiment_id] + list(INTERNET_FACING_TYPES)
            repo_join3 = ""
            repo_where3 = ""
            prov_where3 = ""
            if repo_name:
                repo_join3 = "JOIN repositories rp3 ON r.repo_id = rp3.id"
                repo_where3 = "AND LOWER(rp3.repo_name) = LOWER(?)"
                type_params.append(repo_name)
            if provider:
                prov_where3 = "AND LOWER(r.provider) = LOWER(?)"
                type_params.append(provider)
            rows3 = conn.execute(f"""
                SELECT DISTINCT r.resource_name, r.resource_type
                FROM resources r
                {repo_join3}
                WHERE r.experiment_id = ?
                  AND r.resource_type IN ({placeholder})
                  {repo_where3}
                  {prov_where3}
            """, type_params).fetchall()
            for row in rows3:
                name = row['resource_name']
                rt = row['resource_type']
                if name and name not in existing_internet_targets:
                    label = 'internet gateway' if 'gateway' in rt.lower() else 'internet facing'
                    connections.append({
                        'source': 'Internet', 'target': name,
                        'label': label, 'connection_type': 'internet_access',
                        'is_cross_repo': 0
                    })
                    existing_internet_targets.add(name)
        except Exception:
            pass

        # Exposure-analysis fallback: query exposure_analysis for resources
        # classified as direct_exposure or mitigated. This catches:
        #   - Resources exposed via BFS from entry points (IGW/ELB/public IPs)
        #   - Resources with property-based public access (buckets, public DBs)
        # This is the most authoritative source — it reflects actual analysis results.
        try:
            prov_where4 = ""
            repo_join4 = ""
            repo_where4 = ""
            ea_params: list = [experiment_id]
            if provider:
                prov_where4 = "AND LOWER(r.provider) = LOWER(?)"
                ea_params.append(provider)
            if repo_name:
                repo_join4 = "JOIN repositories rp4 ON r.repo_id = rp4.id"
                repo_where4 = "AND LOWER(rp4.repo_name) = LOWER(?)"
                ea_params.append(repo_name)
            ea_rows = conn.execute(f"""
                SELECT r.resource_name, ea.exposure_level
                FROM exposure_analysis ea
                JOIN resources r ON r.id = ea.resource_id
                {repo_join4}
                WHERE ea.experiment_id = ?
                  AND ea.exposure_level IN ('direct_exposure', 'mitigated')
                  {prov_where4}
                  {repo_where4}
            """, ea_params).fetchall()
            for row in ea_rows:
                name = row['resource_name']
                level = row['exposure_level']
                if name and name not in existing_internet_targets:
                    label = '⚠️ exposed' if level == 'direct_exposure' else 'mitigated'
                    connections.append({
                        'source': 'Internet', 'target': name,
                        'label': label,
                        'connection_type': 'exposed' if level == 'direct_exposure' else 'mitigated',
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
    
    # Backfill parent_resource_id for orphaned Public IPs using connections table
    def _associate_orphaned_public_ips_to_parents():
        """Best-effort enrichment: associate orphaned Public IPs to their parent VM/LB via connections."""
        try:
            with get_db_connection() as conn:
                orphaned_ips = conn.execute("""
                    SELECT r.id
                    FROM resources r
                    WHERE r.experiment_id = ?
                      AND (r.resource_type LIKE '%public_ip%' OR r.resource_type LIKE '%elastic_ip%')
                      AND r.parent_resource_id IS NULL
                """, [experiment_id]).fetchall()
                
                for ip in orphaned_ips:
                    ip_id = ip['id']
                    # Check connections table for parent
                    parent = conn.execute("""
                        SELECT DISTINCT r.id
                        FROM resource_connections rc
                        JOIN resources r ON rc.target_resource_id = r.id
                        WHERE rc.source_resource_id = ? AND (
                            r.resource_type LIKE '%virtual_machine%'
                            OR r.resource_type LIKE '%ec2%'
                            OR r.resource_type LIKE '%load_balancer%'
                            OR r.resource_type LIKE '%lb%'
                        )
                        UNION
                        SELECT DISTINCT r.id
                        FROM resource_connections rc
                        JOIN resources r ON rc.source_resource_id = r.id
                        WHERE rc.target_resource_id = ? AND (
                            r.resource_type LIKE '%virtual_machine%'
                            OR r.resource_type LIKE '%ec2%'
                            OR r.resource_type LIKE '%load_balancer%'
                            OR r.resource_type LIKE '%lb%'
                        )
                        LIMIT 1
                    """, [ip_id, ip_id]).fetchone()
                    
                    if parent:
                        conn.execute("UPDATE resources SET parent_resource_id = ? WHERE id = ?", [parent['id'], ip_id])
                conn.commit()
        except Exception:
            pass
    
    _associate_orphaned_public_ips_to_parents()
    
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
    connections = _add_internet_connections(connections, experiment_id, repo_name=repo_name, provider=provider)

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
        log_msg = f"No resources found for diagram generation (experiment_id={experiment_id}, repo_name={repo_name}, provider={provider})"
        _logger.warning(log_msg)
        return "flowchart TB\n  empty[No resources found]"
    
    lines = ["flowchart TB"]
    
    # Build parent-child mapping
    parent_children: Dict[int, List] = {}
    child_ids = set()
    for h in hierarchies:
        parent_id = h['parent_id']
        if parent_id not in parent_children:
            parent_children[parent_id] = []
        parent_children[parent_id].append(h)
        child_ids.add(h['child_id'])

    # Promote orphaned children: resources whose DIRECT parent has display_on_architecture_chart=False
    # (e.g. children of azurerm_resource_group). We only promote one level — children of promoted
    # resources remain as children (rendered inside the promoted parent's subgraph).
    display_filtered_ids = {r['id'] for r in resources}
    parent_id_of_child = {h['child_id']: h['parent_id'] for h in hierarchies}

    # Build map from resource.id -> resource_type for all raw resources (before filtering)
    with get_db_connection() as _pconn:
        all_raw = _pconn.execute(
            "SELECT id, resource_name, resource_type, provider, repo_id FROM resources WHERE experiment_id = ?",
            [experiment_id]
        ).fetchall()
    all_raw_map = {r['id']: dict(r) for r in all_raw}

    def _parent_is_hidden(child_id: int) -> bool:
        """Return True if child's direct parent is filtered out (display=False or resource_group)."""
        parent_id = parent_id_of_child.get(child_id)
        if parent_id is None:
            return False  # no parent, already a root
        if parent_id in display_filtered_ids:
            return False  # parent visible — stay as child
        parent_r = all_raw_map.get(parent_id)
        if not parent_r:
            return True  # orphaned
        prt = (parent_r.get('resource_type') or '').strip()
        prt_info = _rtdb.get_resource_type(None, prt)
        return (
            not prt_info.get('display_on_architecture_chart', True)
            or 'resource_group' in prt.lower()
        )

    promoted_child_ids = {cid for cid in child_ids if _parent_is_hidden(cid)}
    child_ids = child_ids - promoted_child_ids

    # Add promoted resources to the resources list (only if same provider when filtering)
    current_ids = {r['id'] for r in resources}
    prov_filter = provider.lower() if provider else None
    for promoted_id in promoted_child_ids:
        promoted_r = all_raw_map.get(promoted_id)
        if not promoted_r or promoted_r['id'] in current_ids:
            continue
        if prov_filter and (promoted_r.get('provider') or '').lower() != prov_filter:
            continue
        rt_info = _rtdb.get_resource_type(None, (promoted_r.get('resource_type') or '').strip())
        if rt_info.get('display_on_architecture_chart', True):
            resources.append(promoted_r)
            current_ids.add(promoted_r['id'])

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

    def _is_public_ip_orphaned(r: dict) -> bool:
        """Return True if a Public IP has no parent_resource_id."""
        return _is_public_ip_resource_obj(r) and not r.get('parent_resource_id')

    def _is_application_tier_resource(r: dict) -> bool:
        """Return True if resource is an application-tier resource (app service plan, function app, web app, etc.)"""
        if not r or not r.get('resource_type'):
            return False
        rt = (r.get('resource_type') or '').lower()
        app_tier_keywords = (
            'app_service_plan', 'service_plan',
            'function_app', 'linux_function_app', 'windows_function_app',
            'app_service', 'linux_web_app', 'windows_web_app',
            'elastic_beanstalk',
        )
        return any(kw in rt for kw in app_tier_keywords)

    def _is_compute_tier_resource(r: dict) -> bool:
        """Return True if resource should ALWAYS be in Compute tier (VMs, instances).
        
        VMs must go to Compute tier regardless of parent, since they represent
        compute capacity even when provisioned via NICs.
        """
        if not r or not r.get('resource_type'):
            return False
        rt = (r.get('resource_type') or '').lower()
        compute_keywords = (
            'virtual_machine', 'linux_virtual_machine', 'windows_virtual_machine',
            'ec2', 'instance', 'vm',
        )
        return any(kw in rt for kw in compute_keywords)

    def _is_data_tier_resource(r: dict) -> bool:
        """Return True if resource should ALWAYS be in Data tier (databases).
        
        Databases must go to Data tier regardless of categorization anomalies.
        """
        if not r or not r.get('resource_type'):
            return False
        rt = (r.get('resource_type') or '').lower()
        data_keywords = (
            'database', 'sql', 'rds', 'cosmos', 'postgresql', 'mysql',
            'mssql', 'bigquery', 'db_', 'cosmosdb',
        )
        return any(kw in rt for kw in data_keywords)

    # Exclude only orphaned (parentless) public IP resources — those with parents stay in filtered_roots for hierarchical rendering
    filtered_roots = [r for r in root_resources if not _is_public_ip_orphaned(r)]
    # Exclude resources explicitly hidden from architecture diagrams (unless already filtered as children)
    filtered_roots = [r for r in filtered_roots if _should_show_on_diagram(r, child_ids)]

    # Application tier: function apps, app services (web apps), and app service plans
    app_tier       = [r for r in filtered_roots if _is_application_tier_resource(r)]
    # Compute tier: VMs and load balancers (force VMs to Compute tier regardless of parent)
    vms            = [r for r in filtered_roots if (_is_compute_tier_resource(r) or _in_render_cat(r, 'Compute')) and not _is_application_tier_resource(r) and not _is_public_ip_resource_obj(r)]
    aks            = [r for r in filtered_roots if _in_render_cat(r, 'Container')]
    # Data tier: ensure databases always go to Data tier
    sql_servers    = [r for r in filtered_roots if _is_data_tier_resource(r) or _in_render_cat(r, 'Database')]
    storage_accounts = [r for r in filtered_roots if _in_render_cat(r, 'Storage')]
    # Network/Firewall nodes: only include true firewall/appliance devices for Network category
    nsgs           = [r for r in filtered_roots if _in_render_cat(r, 'Firewall') or (_in_render_cat(r, 'Security') and 'nsg' in r.get('resource_type','').lower()) or (_in_render_cat(r, 'Network') and _rtdb.is_physical_network_device(None, r.get('resource_type','')))]
    paas           = [r for r in filtered_roots if _in_render_cat(r, 'Identity') and r['id'] not in child_ids]
    # Include Load Balancers in Compute tier
    lbs            = [r for r in filtered_roots if _in_render_cat(r, 'Network') and any(tok in r.get('resource_type','').lower() for tok in ('load_balancer', 'lb', 'elastic_load'))]
    # Other resources: exclude those already categorized
    other          = [r for r in filtered_roots if r not in app_tier + vms + aks + sql_servers + storage_accounts + nsgs + paas + lbs]

    # Internet exposure is rendered through edges from the shared Internet node.
    # Keep exposed resources in their normal tiers so they remain connected to
    # the rest of the topology instead of being split into a separate zone.
    other_remaining  = list(other)

    # Shared set to track emitted Mermaid node IDs across all zones — prevents
    # duplicate node ID collisions when multiple resources share the same name
    # (e.g., Terraform test fixtures named 'bad'/'good' across resource types).
    _diagram_emitted_ids: set = set()

    # ── Internet Node (External Reference) ──
    # Internet is rendered at root level, not inside any zone, to avoid circular references
    lines.append("  internet[🌐 Internet]")

    # ── Internal Zone (Compute, Containers, Network Security) ──
    internal_resources = vms + lbs + aks + nsgs
    internal_has_children = any(
        res['id'] in parent_children and parent_children[res['id']]
        for res in internal_resources
    )
    if internal_resources and internal_has_children:
        lines.append("  subgraph zone_internal[\"🔷 Internal\"]")
        # Compute tier subgraph (VMs + Load Balancers)
        if vms or lbs:
            lines.append("    subgraph compute_tier[\"🖥️ Compute Tier\"]")
            for vm in vms:
                _render_resource_subgraph(vm, parent_children, lines, indent="      ", _emitted_ids=_diagram_emitted_ids)
            for lb in lbs:
                if lb['id'] in parent_children:
                    _render_resource_subgraph(lb, parent_children, lines, indent="      ", _emitted_ids=_diagram_emitted_ids)
                else:
                    base_nid = sanitize_id(lb['resource_name'])
                    if base_nid in _diagram_emitted_ids:
                        rtype = lb.get('resource_type', '')
                        ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                        base_nid = sanitize_id(f"{ts}_{lb['resource_name']}")
                    _diagram_emitted_ids.add(base_nid)
                    lines.append(f"      {base_nid}[{_display_label(lb)}]")
            lines.append("    end")
        # VNet/VPC subgraph for AKS
        if aks:
            lines.append("    subgraph vnet[VNet]")
            for aks_cluster in aks:
                _render_resource_subgraph(aks_cluster, parent_children, lines, indent="      ", _emitted_ids=_diagram_emitted_ids)
            lines.append("    end")
        # Network Security
        for nsg in nsgs:
            base_nid = sanitize_id(nsg['resource_name'])
            if base_nid in _diagram_emitted_ids:
                rtype = nsg.get('resource_type', '')
                ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                base_nid = sanitize_id(f"{ts}_{nsg['resource_name']}")
            _diagram_emitted_ids.add(base_nid)
            lines.append(f"      {base_nid}[{_display_label(nsg)}]")
        lines.append("  end")
    elif internal_resources:
        # No child hierarchy to wrap — render the resources directly.
        # Still create Compute subgraph for organization
        if vms or lbs:
            lines.append("  subgraph compute_tier[\"🖥️ Compute Tier\"]")
            for vm in vms:
                _render_resource_subgraph(vm, parent_children, lines, indent="    ", _emitted_ids=_diagram_emitted_ids)
            for lb in lbs:
                base_nid = sanitize_id(lb['resource_name'])
                if base_nid in _diagram_emitted_ids:
                    rtype = lb.get('resource_type', '')
                    ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                    base_nid = sanitize_id(f"{ts}_{lb['resource_name']}")
                _diagram_emitted_ids.add(base_nid)
                lines.append(f"    {base_nid}[{_display_label(lb)}]")
            lines.append("  end")
        for aks_cluster in aks:
            _render_resource_subgraph(aks_cluster, parent_children, lines, indent="  ", _emitted_ids=_diagram_emitted_ids)
        for nsg in nsgs:
            base_nid = sanitize_id(nsg['resource_name'])
            if base_nid in _diagram_emitted_ids:
                rtype = nsg.get('resource_type', '')
                ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                base_nid = sanitize_id(f"{ts}_{nsg['resource_name']}")
            _diagram_emitted_ids.add(base_nid)
            lines.append(f"  {base_nid}[{_display_label(nsg)}]")

    # ── Application Tier Zone (App Service Plans, Function Apps, Web Apps) ──
    if app_tier:
        lines.append("  subgraph zone_app[\"⚙️ Application Tier\"]")
        for app in app_tier:
            if app['id'] in parent_children:
                _render_resource_subgraph(app, parent_children, lines, indent="    ", _emitted_ids=_diagram_emitted_ids)
            else:
                base_nid = sanitize_id(app['resource_name'])
                if base_nid in _diagram_emitted_ids:
                    rtype = app.get('resource_type', '')
                    ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                    base_nid = sanitize_id(f"{ts}_{app['resource_name']}")
                _diagram_emitted_ids.add(base_nid)
                lines.append(f"    {base_nid}[{_display_label(app)}]")
        lines.append("  end")

    # ── Data Tier Zone ──
    if sql_servers or storage_accounts:
        lines.append("  subgraph zone_data[\"🗄️ Data Tier\"]")
        for db in sql_servers:
            _render_resource_subgraph(db, parent_children, lines, indent="    ", _emitted_ids=_diagram_emitted_ids)
        for sa in storage_accounts:
            _render_resource_subgraph(sa, parent_children, lines, indent="    ", _emitted_ids=_diagram_emitted_ids)
        lines.append("  end")

    # PaaS/Identity subgraph — use subgraph rendering to show children (e.g. OCI compartments with buckets)
    if paas:
        lines.append(f"  subgraph paas[PaaS / Identity]")
        for p in paas:
            _render_resource_subgraph(p, parent_children, lines, indent="    ", _emitted_ids=_diagram_emitted_ids)
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
                        if op_id in _diagram_emitted_ids:
                            op_id = sanitize_id(f"op_{op['resource_name']}_{op.get('id','')}")
                        _diagram_emitted_ids.add(op_id)
                        lines.append(f"    {op_id}[Operation: {op['resource_name']}]")
                    lines.append("  end")
    except Exception:
        pass

    # Other resources (outside subgraphs) — use subgraph rendering when resource has children
    for res in other_remaining:
        if res['resource_name'] not in ('Internet', 'NSG'):
            if res['id'] in parent_children:
                _render_resource_subgraph(res, parent_children, lines, indent="  ", _emitted_ids=_diagram_emitted_ids)
            else:
                base_nid = sanitize_id(res['resource_name'])
                if base_nid in _diagram_emitted_ids:
                    rtype = res.get('resource_type', '')
                    ts = rtype.split('_', 2)[-1] if '_' in rtype else rtype
                    base_nid = sanitize_id(f"{ts}_{res['resource_name']}")
                _diagram_emitted_ids.add(base_nid)
                lines.append(f"  {base_nid}[{_display_label(res)}]")

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
    node_names_present = {'Internet', 'internet'} | {r['resource_name'] for r in resources}

    # Helper to ensure a resource node exists in the diagram for a given resource name
    def _ensure_node_exists(resource_name: str):
        if not resource_name or str(resource_name).strip().lower() == 'internet' or resource_name in node_names_present:
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
        if str(name).strip().lower() == 'internet':
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
    edge_styles = []
    edge_index = 0

    def _mark_risky_edge() -> None:
        edge_styles.append(f"  linkStyle {edge_index} stroke:red,stroke-width:2px")

    for conn in connections_sorted:
        src_name = conn.get('source')
        tgt_name = conn.get('target')
        if str(src_name or "").strip().lower() == str(tgt_name or "").strip().lower():
            continue

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
            if 'Internet' not in node_names_present and 'internet' not in node_names_present:
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
                # direction Internet -> real resource (always use lowercase 'internet' to match node definition)
                tgt = 'internet' if str(real_name or "").strip().lower() == 'internet' else sanitize_id(real_name)
                if tgt == 'internet':
                    continue
                lines.append(f"  internet -->{new_label} {tgt}")
                _mark_risky_edge()
                edge_index += 1
            # Skip normal processing for this connection
            continue

        # Ensure endpoint nodes exist so arrows are drawn
        _ensure_node_exists(src_name)
        _ensure_node_exists(tgt_name)

        src = 'internet' if str(conn['source'] or "").strip().lower() == 'internet' else sanitize_id(conn['source'])
        tgt = 'internet' if str(conn['target'] or "").strip().lower() == 'internet' else sanitize_id(conn['target'])

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

        # Cross-repo or semantic data_access connections use dashed lines; mitigated exposure too
        is_cross = conn.get('is_cross_repo') if isinstance(conn, dict) else conn['is_cross_repo']
        conn_type = str(conn.get("connection_type") or "").strip()
        if is_cross or conn_type in ("data_access", "mitigated"):
            # Mermaid dashed arrow with label: A -. label .-> B (no pipe characters)
            inner = label.strip('|') if label else ''
            if inner:
                lines.append(f"  {src} -. {inner} .-> {tgt}")
            else:
                lines.append(f"  {src} -.-> {tgt}")
        else:
            lines.append(f"  {src} -->{label} {tgt}")

        if src_name == 'Internet' and tgt_name and tgt_name != 'Internet':
            _mark_risky_edge()

        edge_index += 1
    
    lines.append("")
    lines.extend(edge_styles)
    lines.append("")
    
    # Styling based on findings
    for r in resources:
        style = get_node_style(r)
        if style:
            lines.append(f"  {style}")

    has_internet_connections = any(
        str(c.get('source') or "").strip().lower() == 'internet' or str(c.get('target') or "").strip().lower() == 'internet'
        for c in connections
    )
    if has_internet_connections:
        lines.append("  style internet stroke:#ff0000, stroke-width:3px")

    # Style security zone subgraphs
    lines.append("  style zone_internal fill:#0a0a1a,stroke:#4444ff,stroke-width:1px")
    lines.append("  style zone_data fill:#0a1a0a,stroke:#44aa44,stroke-width:1px")

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
    """Show what attacker can reach from compromised resource.
    
    Raises:
        ValueError: If the resource is not found in the experiment.
    """
    
    with get_db_connection() as conn:
        # First, verify the resource exists in the experiment
        resource_check = conn.execute("""
            SELECT id, resource_name, resource_type
            FROM resources
            WHERE experiment_id = ? AND resource_name = ?
            LIMIT 1
        """, [experiment_id, compromised_resource]).fetchone()
        
        if not resource_check:
            # Get available resources for a helpful error message
            available_resources = conn.execute("""
                SELECT DISTINCT resource_name, resource_type
                FROM resources
                WHERE experiment_id = ?
                ORDER BY resource_name
                LIMIT 10
            """, [experiment_id]).fetchall()
            
            available_list = ", ".join([r['resource_name'] for r in available_resources])
            error_msg = f"Resource not found: '{compromised_resource}' in experiment '{experiment_id}'"
            if available_resources:
                error_msg += f"\nAvailable resources: {available_list}"
                if len(available_resources) == 10:
                    error_msg += "... (more)"
            else:
                error_msg += "\nNo resources found in this experiment."
            
            _logger.warning(f"Blast radius diagram requested for non-existent resource: {compromised_resource} in experiment {experiment_id}")
            raise ValueError(error_msg)
        
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
