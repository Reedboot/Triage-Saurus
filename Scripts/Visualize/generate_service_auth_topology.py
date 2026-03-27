#!/usr/bin/env python3
"""
Generate service authentication topology diagrams showing how services authenticate with each other.
Cloud-agnostic - handles Azure, AWS, GCP services.
Analyzes resource configurations to identify:
- Auth methods (keys, connection strings, managed identities, roles, etc.)
- Credential exposure (hardcoded, environment variables, secrets)
- Auth relationships between services
"""
import os
import sys
import re
import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

def get_db_connection(db_path: str):
    """Get database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def analyze_service_auth(conn: sqlite3.Connection, repo_id: int) -> Dict:
    """
    Query database and analyze authentication patterns across all services.
    
    Returns dict with:
    - services: List of authenticated services with their auth methods
    - relationships: Service-to-service auth flows
    - credentials: Exposed credential patterns
    - risks: High-risk auth configurations
    """
    
    # Define auth patterns for different service types
    AUTH_PATTERNS = {
        # Azure
        'azurerm_storage_account': {
            'auth_types': ['storage_key', 'sas_token', 'managed_identity'],
            'access_pattern': 'Storage containers/blobs',
            'icon': '💾',
            'risk': 'high' if 'storage_key' else 'medium'
        },
        'azurerm_servicebus_namespace': {
            'auth_types': ['connection_string', 'sas_policy', 'managed_identity'],
            'access_pattern': 'Queues/Topics',
            'icon': '📨',
            'risk': 'high'
        },
        'azurerm_servicebus_topic': {
            'auth_types': ['inherited_from_namespace', 'sas_policy'],
            'access_pattern': 'Topic subscribers',
            'icon': '📬',
            'risk': 'high'
        },
        'azurerm_servicebus_queue': {
            'auth_types': ['inherited_from_namespace', 'sas_policy'],
            'access_pattern': 'Queue consumers/producers',
            'icon': '📤',
            'risk': 'high'
        },
        'azurerm_cosmosdb_account': {
            'auth_types': ['primary_key', 'managed_identity', 'connection_string'],
            'access_pattern': 'Database connections',
            'icon': '🗄️',
            'risk': 'critical'
        },
        'azurerm_sql_server': {
            'auth_types': ['sql_auth', 'managed_identity', 'azure_ad_auth'],
            'access_pattern': 'SQL databases',
            'icon': '🔒',
            'risk': 'critical'
        },
        'azurerm_api_management_api_operation': {
            'auth_types': ['subscription_key', 'oauth2', 'jwt', 'custom_headers'],
            'access_pattern': 'API endpoints',
            'icon': '🔌',
            'risk': 'medium'
        },
        'azurerm_api_management_subscription': {
            'auth_types': ['subscription_key'],
            'access_pattern': 'API access credential',
            'icon': '🔑',
            'risk': 'high'
        },
        'azurerm_key_vault': {
            'auth_types': ['managed_identity', 'azure_ad', 'rbac'],
            'access_pattern': 'Secret/cert storage',
            'icon': '🔐',
            'risk': 'critical'
        },
        'azurerm_app_configuration': {
            'auth_types': ['connection_string', 'managed_identity', 'rbac'],
            'access_pattern': 'Configuration storage',
            'icon': '⚙️',
            'risk': 'high'
        },
        
        # AWS
        'aws_s3_bucket': {
            'auth_types': ['iam_policy', 'bucket_policy', 'access_key', 'presigned_url'],
            'access_pattern': 'Object storage',
            'icon': '💾',
            'risk': 'high'
        },
        'aws_sqs_queue': {
            'auth_types': ['iam_policy', 'queue_policy', 'access_key'],
            'access_pattern': 'Message queue',
            'icon': '📤',
            'risk': 'high'
        },
        'aws_sns_topic': {
            'auth_types': ['iam_policy', 'topic_policy', 'access_key'],
            'access_pattern': 'Pub/Sub topic',
            'icon': '📨',
            'risk': 'high'
        },
        'aws_rds_cluster': {
            'auth_types': ['master_password', 'iam_auth', 'encryption'],
            'access_pattern': 'Database cluster',
            'icon': '🗄️',
            'risk': 'critical'
        },
        'aws_api_gateway_rest_api': {
            'auth_types': ['api_key', 'cognito', 'iam', 'lambda_authorizer'],
            'access_pattern': 'API endpoints',
            'icon': '🔌',
            'risk': 'medium'
        },
        'aws_secrets_manager_secret': {
            'auth_types': ['iam_policy', 'resource_policy'],
            'access_pattern': 'Secret storage',
            'icon': '🔐',
            'risk': 'critical'
        },
        
        # GCP
        'google_storage_bucket': {
            'auth_types': ['iam_binding', 'service_account', 'public_access'],
            'access_pattern': 'Object storage',
            'icon': '💾',
            'risk': 'high'
        },
        'google_pubsub_topic': {
            'auth_types': ['iam_binding', 'service_account'],
            'access_pattern': 'Pub/Sub topic',
            'icon': '📨',
            'risk': 'high'
        },
        'google_sql_database_instance': {
            'auth_types': ['cloudsql_auth', 'service_account', 'cloud_sql_proxy'],
            'access_pattern': 'Cloud SQL database',
            'icon': '🗄️',
            'risk': 'critical'
        },
        'google_cloud_run_service': {
            'auth_types': ['iam_binding', 'service_account', 'no_auth'],
            'access_pattern': 'Serverless compute',
            'icon': '🔌',
            'risk': 'medium'
        },
        'google_secret_manager_secret': {
            'auth_types': ['iam_binding', 'service_account'],
            'access_pattern': 'Secret storage',
            'icon': '🔐',
            'risk': 'critical'
        },
        
        # Kubernetes
        'kubernetes_service': {
            'auth_types': ['service_account', 'rbac', 'network_policy'],
            'access_pattern': 'Pod communication',
            'icon': '☸️',
            'risk': 'medium'
        },
        'kubernetes_secret': {
            'auth_types': ['etcd_encryption', 'rbac', 'tls_certs'],
            'access_pattern': 'Secret storage',
            'icon': '🔐',
            'risk': 'critical'
        },
    }
    
    # Query all resources
    resources = conn.execute("""
        SELECT r.id, r.resource_name, r.resource_type, r.parent_resource_id
        FROM resources r
        WHERE r.repo_id = ?
        ORDER BY r.resource_type, r.resource_name
    """, (repo_id,)).fetchall()
    
    services = {}
    relationships = []
    credentials = []
    risks = []
    
    # Analyze each resource
    for resource in resources:
        res_id = resource['id']
        res_name = resource['resource_name']
        res_type = resource['resource_type']
        parent_id = resource['parent_resource_id']
        
        # Check if this resource type has known auth patterns
        auth_info = AUTH_PATTERNS.get(res_type, {})
        if not auth_info:
            continue
        
        # Build service entry
        services[res_id] = {
            'name': res_name,
            'type': res_type,
            'auth_types': auth_info.get('auth_types', ['unknown']),
            'icon': auth_info.get('icon', '⚙️'),
            'risk': auth_info.get('risk', 'unknown'),
            'access_pattern': auth_info.get('access_pattern', ''),
            'parent_id': parent_id,
        }
        
        # Create parent-child relationships
        if parent_id and parent_id in services:
            parent = services[parent_id]
            relationships.append({
                'from': parent['name'],
                'to': res_name,
                'auth': auth_info.get('auth_types', ['inherited']),
                'parent_type': parent.get('type', ''),
                'child_type': res_type,
            })
        
        # Identify high-risk auth patterns
        if auth_info.get('risk') in ['critical', 'high']:
            auth_summary = ', '.join(auth_info.get('auth_types', ['unknown'])[:2])
            risks.append({
                'service': res_name,
                'type': res_type,
                'auth': auth_summary,
                'risk_level': auth_info.get('risk', 'unknown'),
                'reason': f"Credentials used: {auth_summary}. Verify encryption in transit & at rest."
            })
    
    return {
        'services': services,
        'relationships': relationships,
        'risks': risks,
        'total_authenticated_services': len(services),
    }

_SERVICE_CATEGORY_MAP: Dict[str, str] = {
    # Messaging
    "azurerm_servicebus_namespace": "messaging",
    "azurerm_servicebus_queue": "messaging",
    "azurerm_servicebus_topic": "messaging",
    "azurerm_servicebus_subscription": "messaging",
    "azurerm_eventhub_namespace": "messaging",
    "azurerm_eventhub": "messaging",
    "aws_sqs_queue": "messaging",
    "aws_sns_topic": "messaging",
    "google_pubsub_topic": "messaging",
    # API Gateway
    "azurerm_api_management_api_operation": "api",
    "azurerm_api_management_subscription": "api",
    "aws_api_gateway_rest_api": "api",
    # Database
    "azurerm_cosmosdb_account": "database",
    "azurerm_sql_server": "database",
    "aws_rds_cluster": "database",
    "google_sql_database_instance": "database",
    # Storage
    "azurerm_storage_account": "storage",
    "aws_s3_bucket": "storage",
    "google_storage_bucket": "storage",
    # Secrets
    "azurerm_key_vault": "secrets",
    "azurerm_app_configuration": "secrets",
    "aws_secrets_manager_secret": "secrets",
    "google_secret_manager_secret": "secrets",
    # Compute
    "kubernetes_service": "compute",
    "google_cloud_run_service": "compute",
}

_CATEGORY_LABELS: Dict[str, str] = {
    "messaging": "📨 Messaging",
    "api": "🔌 API Gateway",
    "database": "🗄️ Database",
    "storage": "💾 Storage",
    "secrets": "🔐 Secrets / Config",
    "compute": "☸️ Compute",
}

# Namespace-like resource types that own child resources within them
_NAMESPACE_TYPES = {
    "azurerm_servicebus_namespace",
    "azurerm_eventhub_namespace",
}


def generate_mermaid_topology(analysis: Dict, repo_name: str) -> str:
    """Generate Mermaid diagram of service authentication topology (combined).

    Groups resources by service category (messaging, database, etc.) rather than
    cloud-provider prefix so Service Bus queues/topics are correctly nested under
    their parent namespace instead of appearing as sibling top-level nodes.
    """
    services = analysis['services']
    relationships = analysis['relationships']

    if not services:
        return "# No authenticated services found"

    lines = [
        'graph TD',
        '  internet["🌐 External Clients/Services"]',
        '',
    ]

    # Determine which services are children of namespace resources so we can
    # skip direct internet edges for them.
    child_ids: set = set()
    parent_children: Dict = defaultdict(list)  # parent_id → [child_id, ...]
    for rel in relationships:
        for p_id, p_svc in services.items():
            if p_svc['name'] != rel['from']:
                continue
            for c_id, c_svc in services.items():
                if c_svc['name'] == rel['to'] and p_svc.get('type') in _NAMESPACE_TYPES:
                    child_ids.add(c_id)
                    parent_children[p_id].append(c_id)

    # Group by service category
    by_category: Dict[str, list] = defaultdict(list)
    for service_id, service in services.items():
        category = _SERVICE_CATEGORY_MAP.get(service['type'], service['type'].split('_')[0])
        by_category[category].append((service_id, service))

    service_map: Dict = {}  # service_id → mermaid node_id

    for category in sorted(by_category.keys()):
        cat_label = _CATEGORY_LABELS.get(category, f"🔷 {category.upper()}")
        # Use sanitised subgraph id (no spaces)
        sg_id = f"sg_{category}"
        lines.append(f'  subgraph {sg_id}["{cat_label}"]')

        # Within messaging, group children visually under their namespace subgraph
        if category == "messaging":
            rendered_in_ns: set = set()
            ns_services = [(sid, svc) for sid, svc in by_category[category]
                           if svc.get('type') in _NAMESPACE_TYPES]
            other_services = [(sid, svc) for sid, svc in by_category[category]
                              if svc.get('type') not in _NAMESPACE_TYPES]

            for ns_id, ns_svc in ns_services:
                node_id = f"svc_{ns_id}"
                service_map[ns_id] = node_id
                risk_emoji = '🔴' if ns_svc['risk'] == 'critical' else '🟠' if ns_svc['risk'] == 'high' else '🟡'
                lines.append(f'    {node_id}["{ns_svc["icon"]} {ns_svc["name"]}<br/>{risk_emoji}"]')

                # Emit child nodes in a nested sub-subgraph
                ns_children = parent_children.get(ns_id, [])
                if ns_children:
                    ns_sg = f"sg_ns_{ns_id}"
                    lines.append(f'    subgraph {ns_sg}["{ns_svc["name"]} resources"]')
                    for c_id in ns_children:
                        c_svc = services.get(c_id)
                        if not c_svc:
                            continue
                        c_node_id = f"svc_{c_id}"
                        service_map[c_id] = c_node_id
                        rendered_in_ns.add(c_id)
                        auth_str = '; '.join(c_svc['auth_types'][:2])
                        lines.append(f'      {c_node_id}["{c_svc["icon"]} {c_svc["name"]}<br/><small>{auth_str}</small>"]')
                    lines.append('    end')

            # Orphan messaging resources (no namespace parent found)
            for sid, svc in other_services:
                if sid in rendered_in_ns:
                    continue
                if sid not in service_map:
                    node_id = f"svc_{sid}"
                    service_map[sid] = node_id
                    auth_str = '; '.join(svc['auth_types'][:2])
                    risk_emoji = '🔴' if svc['risk'] == 'critical' else '🟠' if svc['risk'] == 'high' else '🟡'
                    lines.append(f'    {node_id}["{svc["icon"]} {svc["name"]}<br/>{risk_emoji}<br/><small>{auth_str}</small>"]')
        else:
            for service_id, service in by_category[category]:
                node_id = f"svc_{service_id}"
                service_map[service_id] = node_id
                auth_str = '; '.join(service['auth_types'][:2])
                if len(service['auth_types']) > 2:
                    auth_str += f" +{len(service['auth_types'])-2}"
                risk_emoji = '🔴' if service['risk'] == 'critical' else '🟠' if service['risk'] == 'high' else '🟡'
                lines.append(f'    {node_id}["{service["icon"]} {service["name"]}<br/>{risk_emoji}<br/><small>{auth_str}</small>"]')

        lines.append('  end')
        lines.append('')

    # Connect internet only to ROOT-level services (not children of namespace nodes)
    for service_id, service in services.items():
        if service_id not in child_ids and service_id in service_map:
            lines.append(f'  internet -->|API/Client SDK| {service_map[service_id]}')

    # Add inter-service relationships (non-parent/child — those are already implicit via subgraph)
    for rel in relationships:
        from_id = None
        to_id = None
        for sid, svc in services.items():
            if svc['name'] == rel['from']:
                from_id = sid
            if svc['name'] == rel['to']:
                to_id = sid
        if from_id and to_id and from_id in service_map and to_id in service_map:
            # Skip parent→child edges in namespace groups (already shown via subgraph nesting)
            if to_id in child_ids and from_id in parent_children and to_id in parent_children.get(from_id, []):
                continue
            auth_label = rel['auth'][0] if rel['auth'] else 'inherited'
            lines.append(f'  {service_map[from_id]} -->|{auth_label}| {service_map[to_id]}')
    
    return '\n'.join(lines)


def _matches_flow(auth_list: List[str], flow_keywords: List[str]) -> bool:
    """Case-insensitive check if any auth in auth_list matches any keyword in flow_keywords."""
    auth_lower = [a.lower() for a in auth_list]
    for kw in flow_keywords:
        for a in auth_lower:
            if kw in a or a in kw:
                return True
    return False


def generate_mermaid_topology_for_flow(analysis: Dict, flow_name: str, flow_keywords: List[str]) -> str:
    """Generate a Mermaid diagram filtered to only show nodes/edges relevant to a specific auth flow."""
    services = analysis['services']
    relationships = analysis['relationships']

    if not services:
        return "# No authenticated services found"

    lines = [
        'graph TD',
        f'  internet["🌐 External Clients/Services - {flow_name}"]',
        '',
    ]

    # Include only services that support the flow keywords
    selected_services = {sid: svc for sid, svc in services.items() if _matches_flow(svc.get('auth_types', []), flow_keywords)}

    # If no services explicitly match, include services that appear in matching relationships
    for rel in relationships:
        if _matches_flow(rel.get('auth', []), flow_keywords):
            # add source and target services by name
            for sid, svc in services.items():
                if svc['name'] == rel['from'] or svc['name'] == rel['to']:
                    selected_services.setdefault(sid, svc)

    if not selected_services:
        return f"# No services using {flow_name} authentication found"

    # Group selected services by category and render
    by_category = defaultdict(list)
    for service_id, service in selected_services.items():
        category = service['type'].split('_')[0]
        by_category[category].append((service_id, service))

    service_map = {}
    for category in sorted(by_category.keys()):
        lines.append(f'  subgraph {category}["🔷 {category.upper()} Services"]')
        for service_id, service in by_category[category]:
            node_id = f"svc_{service_id}"
            service_map[service_id] = node_id
            auth_str = '; '.join(service['auth_types'][:2])
            if len(service['auth_types']) > 2:
                auth_str += f" +{len(service['auth_types'])-2}"
            risk_emoji = '🔴' if service['risk'] == 'critical' else '🟠' if service['risk'] == 'high' else '🟡'
            lines.append(f'    {node_id}["{service["icon"]} {service["name"]}<br/>{risk_emoji}<br/><small>{auth_str}</small>"]')
        lines.append('  end')
        lines.append('')

    # Connect internet to selected services
    for service_id in selected_services.keys():
        if service_id in service_map:
            lines.append(f'  internet -->|API/Client SDK| {service_map[service_id]}')

    # Add inter-service edges only when the relationship indicates the flow
    for rel in relationships:
        if not _matches_flow(rel.get('auth', []), flow_keywords):
            continue
        from_id = None
        to_id = None
        for sid, svc in services.items():
            if svc['name'] == rel['from']:
                from_id = sid
            if svc['name'] == rel['to']:
                to_id = sid
        if from_id and to_id and from_id in service_map and to_id in service_map:
            auth_label = ','.join(rel.get('auth', []))[:40]
            lines.append(f'  {service_map[from_id]} -->|{auth_label}| {service_map[to_id]}')

    return '\n'.join(lines)


def generate_mermaid_topologies_per_flow(analysis: Dict) -> Dict[str, str]:
    """Create mermaid diagrams for predefined auth flows.
    Returns a dict flow_name -> mermaid string.
    """
    flows = {
        'client_credentials': ['client_credentials', 'managed_identity', 'service_account', 'connection_string', 'client_secret', 'oauth2'],
        'mtls': ['certificate', 'mtls', 'mutual_tls', 'mutual-tls'],
        'jwt_bearer': ['jwt', 'jwt_bearer', 'bearer'],
        'api_key': ['api_key', 'subscription_key', 'subscription', 'api-key', 'api key'],
    }

    results = {}
    for name, keywords in flows.items():
        results[name] = generate_mermaid_topology_for_flow(analysis, name, keywords)
    return results

def generate_auth_report(analysis: Dict) -> str:
    """Generate detailed authentication analysis report."""
    
    services = analysis['services']
    risks = analysis['risks']
    
    lines = [
        '# 🔒 Service Authentication Topology Report',
        '',
        f'**Total Authenticated Services:** {analysis["total_authenticated_services"]}',
        '',
        '## Services by Risk Level',
        '',
    ]
    
    # Group by risk
    critical = [s for s in services.values() if s['risk'] == 'critical']
    high = [s for s in services.values() if s['risk'] == 'high']
    medium = [s for s in services.values() if s['risk'] == 'medium']
    
    if critical:
        lines.append(f"### 🔴 Critical Risk ({len(critical)})")
        lines.append('')
        for service in critical:
            auth = ', '.join(service['auth_types'][:3])
            lines.append(f"- **{service['icon']} {service['name']}** ({service['type']})")
            lines.append(f"  - Auth: {auth}")
            lines.append(f"  - Pattern: {service['access_pattern']}")
            lines.append('')
    
    if high:
        lines.append(f"### 🟠 High Risk ({len(high)})")
        lines.append('')
        for service in high:
            auth = ', '.join(service['auth_types'][:3])
            lines.append(f"- **{service['icon']} {service['name']}** ({service['type']})")
            lines.append(f"  - Auth: {auth}")
            lines.append('')
    
    if medium:
        lines.append(f"### 🟡 Medium Risk ({len(medium)})")
        lines.append('')
        for service in medium:
            auth = ', '.join(service['auth_types'][:3])
            lines.append(f"- **{service['icon']} {service['name']}** ({service['type']})")
            lines.append(f"  - Auth: {auth}")
            lines.append('')
    
    lines.append('## 🔐 Authentication Methods Used')
    lines.append('')
    
    # Collect all unique auth methods
    all_auth = set()
    for service in services.values():
        all_auth.update(service['auth_types'])
    
    for auth_method in sorted(all_auth):
        count = sum(1 for s in services.values() if auth_method in s['auth_types'])
        lines.append(f"- **{auth_method}**: {count} service(s)")
    
    lines.append('')
    lines.append('## ⚠️ Recommendations')
    lines.append('')
    lines.append('### For Critical Services:')
    lines.append('1. **Use Managed Identities** - Eliminate hardcoded credentials')
    lines.append('2. **Encryption in Transit** - TLS 1.2+ for all communications')
    lines.append('3. **Encryption at Rest** - Enable for sensitive data stores')
    lines.append('4. **Access Control** - Implement least-privilege RBAC')
    lines.append('5. **Audit Logging** - Track all authentication attempts')
    lines.append('6. **Rotation Policy** - Regular key/credential rotation')
    lines.append('')
    lines.append('### For High-Risk Services:')
    lines.append('1. **Key Vault/Secrets Manager** - Store credentials securely')
    lines.append('2. **Connection String Encryption** - Never hardcode in code')
    lines.append('3. **SAS Token Expiration** - Set appropriate TTLs')
    lines.append('4. **Network Policies** - Restrict access by source')
    lines.append('')
    
    return '\n'.join(lines)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate service authentication topology diagrams')
    parser.add_argument('--repo-id', type=int, required=True, help='Repository ID in database')
    parser.add_argument('--db-path', default='Output/Data/cozo.db', help='Database path')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    
    args = parser.parse_args()
    
    # Get database path
    db_path = args.db_path
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(__file__), '../../', db_path)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Analyze authentication topology
    print(f'[Service Auth Topology] Analyzing repository (repo_id={args.repo_id})...')
    with get_db_connection(db_path) as conn:
        analysis = analyze_service_auth(conn, args.repo_id)
    
    if analysis['total_authenticated_services'] == 0:
        print('[Service Auth Topology] No authenticated services found')
        return
    
    print(f'[Service Auth Topology] Found {analysis["total_authenticated_services"]} authenticated services')
    
    # Generate Mermaid diagram
    mermaid_code = generate_mermaid_topology(analysis, "account-viewing-permissions")
    mermaid_file = output_dir / 'service_auth_topology.mmd'
    with open(mermaid_file, 'w') as f:
        f.write(mermaid_code)
    print(f'✓ Mermaid diagram: {mermaid_file}')
    
    # Generate report
    report = generate_auth_report(analysis)
    report_file = output_dir / 'service_auth_topology_report.md'
    with open(report_file, 'w') as f:
        f.write(report)
    print(f'✓ Auth report: {report_file}')

if __name__ == '__main__':
    main()
