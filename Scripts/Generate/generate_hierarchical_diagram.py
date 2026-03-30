#!/usr/bin/env python3
"""Generate hierarchical Mermaid diagrams with proper subgraph nesting.

Creates cloud-agnostic architecture diagrams showing:
- Internet/Network Client → API Gateway → Backend Services
- APIM (subgraph) → Products (subgraph) → Operations
- Kubernetes/AKS (subgraph) → Services/Deployments
- Service Bus (subgraph) → Topics/Queues/Subscriptions
"""

import sys
import re
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from collections import defaultdict

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import get_db_connection, get_resources_for_diagram, get_connections_for_diagram
import resource_type_db as _rtdb


def sanitize_id(name: str) -> str:
    """Convert resource name to valid Mermaid node ID."""
    return name.replace('-', '_').replace('.', '_').replace(' ', '_').replace(':', '_').replace('/', '_')


def get_friendly_type(resource_type: str) -> str:
    """Get friendly display name for resource type."""
    try:
        conn = _get_lookup_db()
        if conn:
            friendly = _rtdb.get_friendly_name(conn, resource_type)
            if friendly:
                return friendly
    except Exception:
        pass
    
    # Fallback: clean up the type name
    name = resource_type.replace('azurerm_', '').replace('aws_', '').replace('google_', '')
    name = name.replace('_', ' ').title()
    return name


def _get_lookup_db():
    """Get database connection for resource type lookups."""
    global _lookup_conn
    if _lookup_conn is None:
        try:
            if hasattr(_rtdb, 'DB_PATH') and Path(_rtdb.DB_PATH).exists():
                import sqlite3
                _lookup_conn = sqlite3.connect(str(_rtdb.DB_PATH))
                _lookup_conn.row_factory = sqlite3.Row
        except Exception:
            pass
    return _lookup_conn


_lookup_conn = None


class HierarchicalDiagramBuilder:
    """Build hierarchical architecture diagrams with proper nesting."""
    
    def __init__(self, experiment_id: str, repo_name: Optional[str] = None):
        self.experiment_id = experiment_id
        self.repo_name = repo_name
        self.resources = []
        self.connections = []
        self.sql_hints: Dict[str, Optional[str]] = {}
        self.resource_by_name = {}
        self.resource_by_id = {}
        self.children_by_parent = defaultdict(list)
        self.emitted_nodes = set()
        self.connected_resource_names: Set[str] = set()

    def _is_connected_name(self, name: str) -> bool:
        """Return True when a resource should be rendered based on connection participation.

        If no connections were detected, do not prune and keep all nodes.
        """
        if not self.connected_resource_names:
            return True
        return name in self.connected_resource_names
        
    def load_data(self):
        """Load resources and connections from database."""
        self.resources = get_resources_for_diagram(self.experiment_id)
        self.connections = get_connections_for_diagram(self.experiment_id, repo_name=self.repo_name)
        
        # Filter to specific repo if requested
        if self.repo_name:
            self.resources = [r for r in self.resources if r.get('repo_name') == self.repo_name]
        
        # Remove duplicates (keep first occurrence based on ID)
        seen_ids = set()
        unique_resources = []
        for r in self.resources:
            if r['id'] not in seen_ids:
                seen_ids.add(r['id'])
                unique_resources.append(r)
        self.resources = unique_resources
        
        # Build lookup maps
        self.resource_by_name = {}
        for r in self.resources:
            # For duplicates by name, keep the one with more specific type
            if r['resource_name'] not in self.resource_by_name:
                self.resource_by_name[r['resource_name']] = r
            elif 'operation' in r.get('resource_type', '').lower():
                # Prefer operations over products
                self.resource_by_name[r['resource_name']] = r
        
        self.resource_by_id = {r['id']: r for r in self.resources}
        
        # Build parent-child relationships from database
        with get_db_connection() as conn:
            rows = conn.execute("""
                SELECT parent_resource_id, id as child_id
                FROM resources
                WHERE experiment_id = ? AND parent_resource_id IS NOT NULL
            """, [self.experiment_id]).fetchall()
            
            for row in rows:
                parent_id = row['parent_resource_id']
                child_id = row['child_id']
                if child_id in self.resource_by_id:
                    self.children_by_parent[parent_id].append(self.resource_by_id[child_id])
    
    def is_api_gateway(self, resource: dict) -> bool:
        """Check if resource is an API Gateway (APIM, API Gateway, etc)."""
        rtype = (resource.get('resource_type') or '').lower()
        return any(tok in rtype for tok in [
            'api_management_api', 
            'apim', 
            'api_gateway',
            'apigateway'
        ])
    
    def is_api_product(self, resource: dict) -> bool:
        """Check if resource is an API Product."""
        rtype = (resource.get('resource_type') or '').lower()
        return 'api_management_product' in rtype or 'api_product' in rtype
    
    def is_api_operation(self, resource: dict) -> bool:
        """Check if resource is an API Operation."""
        rtype = (resource.get('resource_type') or '').lower()
        return 'api_operation' in rtype or 'api_management_api_operation' in rtype
    
    def is_kubernetes(self, resource: dict) -> bool:
        """Check if resource is Kubernetes-related."""
        rtype = (resource.get('resource_type') or '').lower()
        provider = (resource.get('provider') or '').lower()
        return provider == 'kubernetes' or 'kubernetes' in rtype or 'aks' in rtype

    def get_kubernetes_namespace(self, resource: dict) -> str:
        """Resolve Kubernetes namespace from resource properties with sane fallbacks."""
        props = resource.get('properties') or {}
        candidates = [
            props.get('namespace'),
            props.get('kubernetes_namespace'),
            props.get('k8s_namespace'),
            props.get('release_namespace'),
            props.get('target_namespace'),
        ]

        for candidate in candidates:
            value = str(candidate or '').strip()
            if value and not value.startswith('${'):
                return value

        rtype = (resource.get('resource_type') or '').lower()
        if 'namespace' in rtype:
            name = str(resource.get('resource_name') or '').strip()
            if name:
                return name

        return 'default'

    def is_public_edge_resource(self, resource: dict) -> bool:
        """Heuristic for resources that can plausibly receive traffic from Internet."""
        rtype = (resource.get('resource_type') or '').lower()
        name = (resource.get('resource_name') or '').lower()

        # API gateways are internet entry points by design.
        if self.is_api_gateway(resource) or self.is_api_operation(resource):
            return True

        # Common edge/service-entry resource types across clouds.
        edge_type_tokens = [
            'application_gateway', 'app_gateway',
            'frontdoor', 'cloudfront',
            'load_balancer', 'lb', 'alb', 'elb',
            'ingress', 'gateway',
            'api_gateway', 'apigateway',
            'web_app', 'app_service'
        ]
        if any(tok in rtype for tok in edge_type_tokens):
            return True

        # Kubernetes-specific: avoid marking background workers/jobs/listeners as internet-facing.
        if self.is_kubernetes(resource):
            blocked_name_tokens = [
                'listener', 'worker', 'consumer', 'job', 'cron', 'batch', 'queue', 'processor'
            ]
            if any(tok in name for tok in blocked_name_tokens):
                return False

            if 'ingress' in rtype or 'gateway' in rtype or 'load_balancer' in rtype:
                return True

            # Service-like resources are considered edge only if name suggests frontend/API role.
            if 'service' in rtype:
                public_name_tokens = ['api', 'web', 'frontend', 'front-end', 'gateway', 'public']
                return any(tok in name for tok in public_name_tokens)

            return False

        return False

    def is_identity_principal_like(self, resource: dict) -> bool:
        """Detect identity principal/group resources that are often unconnected noise in diagrams."""
        rtype = (resource.get('resource_type') or '').lower()
        name = (resource.get('resource_name') or '').lower()

        principal_type_tokens = ['identity', 'iam', 'principal', 'role', 'group', 'user', 'serviceaccount']
        principal_name_tokens = ['principal', 'role', 'group', 'user', 'service_account', 'serviceaccount']
        return any(tok in rtype for tok in principal_type_tokens) or any(tok in name for tok in principal_name_tokens)
    
    def is_service_bus(self, resource: dict) -> bool:
        """Check if resource is Service Bus/messaging related."""
        rtype = (resource.get('resource_type') or '').lower()
        return any(tok in rtype for tok in [
            'servicebus', 'service_bus', 
            'sqs', 'sns', 
            'pubsub', 'pub_sub',
            'eventbridge', 'event_hub'
        ])
    
    def is_service_bus_topic(self, resource: dict) -> bool:
        """Check if resource is a topic/SNS."""
        rtype = (resource.get('resource_type') or '').lower()
        return 'topic' in rtype or 'sns' in rtype
    
    def is_service_bus_queue(self, resource: dict) -> bool:
        """Check if resource is a queue/SQS."""
        rtype = (resource.get('resource_type') or '').lower()
        return 'queue' in rtype or 'sqs' in rtype
    
    def is_service_bus_subscription(self, resource: dict) -> bool:
        """Check if resource is a subscription."""
        rtype = (resource.get('resource_type') or '').lower()
        return 'subscription' in rtype and 'servicebus' in rtype

    def is_database_resource(self, resource: dict) -> bool:
        """Check if resource looks like a SQL/database endpoint."""
        rtype = (resource.get('resource_type') or '').lower()
        name = (resource.get('resource_name') or '').lower()
        type_tokens = ['sql', 'database', 'mssql', 'postgres', 'mysql', 'cosmos', 'rds']
        # Type-based match is authoritative; name-based match requires the type to not be a non-DB resource
        if any(tok in rtype for tok in type_tokens):
            # Exclude APIM subscriptions/revisions that happen to have 'sql' in their name
            if 'subscription' in rtype or 'apim' in rtype or 'api_management' in rtype:
                return False
            return True
        # Name-based match only if type also has a database-like token (avoids false positives)
        name_tokens = ['mssql', 'sqlserver']
        return any(tok in name for tok in name_tokens) and not ('subscription' in rtype or 'apim' in rtype or 'api_management' in rtype)

    def is_application_service(self, resource: dict) -> bool:
        """Heuristic for application workloads that may connect to data stores."""
        if not self.is_kubernetes(resource):
            return False

        rtype = (resource.get('resource_type') or '').lower()
        name = (resource.get('resource_name') or '').lower()

        blocked_name_tokens = ['listener', 'worker', 'consumer', 'job', 'queue', 'cron', 'batch']
        if any(tok in name for tok in blocked_name_tokens):
            return False

        # Prefer API/front-end workloads first.
        if any(tok in name for tok in ['api', 'web', 'frontend', 'front-end', 'service', 'app']):
            return True

        return any(tok in rtype for tok in ['deployment', 'service', 'container'])

    def _ensure_synthetic_sql_server_node(self, server_name: Optional[str] = None) -> str:
        """Ensure an explicit SQL Server node exists for architecture dependency views."""
        fallback_name = 'SQL Server'
        candidate = str(server_name or '').strip()
        if self._score_sql_hint_value(candidate) < 2:
            candidate = fallback_name

        sql_node_name = candidate
        existing = self.resource_by_name.get(fallback_name)
        if existing and candidate != fallback_name:
            existing['resource_name'] = candidate
            self.resource_by_name.pop(fallback_name, None)
            self.resource_by_name[candidate] = existing
            return candidate

        if sql_node_name in self.resource_by_name:
            return sql_node_name

        synthetic_id = -1
        while synthetic_id in self.resource_by_id:
            synthetic_id -= 1

        synthetic_resource = {
            'id': synthetic_id,
            'resource_name': sql_node_name,
            'resource_type': 'synthetic_sql_server',
            'provider': 'external',
            'repo_name': self.repo_name or '',
            'properties': {'synthetic': True},
        }
        self.resources.append(synthetic_resource)
        self.resource_by_name[sql_node_name] = synthetic_resource
        self.resource_by_id[synthetic_id] = synthetic_resource
        return sql_node_name

    def _clean_conn_value(self, value: str) -> str:
        """Normalize connection-string values for display."""
        cleaned = str(value or '').strip().strip('"\'')
        if cleaned.startswith('tcp:'):
            cleaned = cleaned[4:]
        return cleaned

    def _parse_sql_connection_string(self, text: str) -> Dict[str, Optional[str]]:
        """Parse server/database/auth hints from SQL-style connection strings."""
        result: Dict[str, Optional[str]] = {'server': None, 'database': None, 'auth_method': None, 'port': None}
        if not text:
            return result

        raw = str(text)
        kv_matches = re.findall(r'([A-Za-z][A-Za-z0-9_ ]*)\s*=\s*([^;\n\r]+)', raw)
        if not kv_matches:
            return result

        values: Dict[str, str] = {}
        for key, value in kv_matches:
            values[key.strip().lower()] = self._clean_conn_value(value)

        server = (
            values.get('server')
            or values.get('data source')
            or values.get('address')
            or values.get('addr')
            or values.get('network address')
        )
        if server:
            # SQL server endpoints are commonly host,port or host:port.
            host = server
            port = None
            if ',' in server:
                host_part, port_part = server.rsplit(',', 1)
                host = host_part.strip()
                if port_part.strip().isdigit():
                    port = port_part.strip()
            elif ':' in server and server.rsplit(':', 1)[1].isdigit():
                host_part, port_part = server.rsplit(':', 1)
                host = host_part.strip()
                port = port_part.strip()

            result['server'] = host or server
            if port:
                result['port'] = port

        result['database'] = values.get('database') or values.get('initial catalog')

        auth_value = values.get('authentication', '')
        auth_value_lower = auth_value.lower()

        has_user = 'user id' in values or 'uid' in values
        has_password = 'password' in values or 'pwd' in values
        is_integrated = values.get('integrated security', '').lower() in ('true', 'sspi')
        is_trusted = values.get('trusted_connection', '').lower() in ('true', 'yes')

        if 'managed identity' in auth_value_lower or 'active directory msi' in auth_value_lower:
            result['auth_method'] = 'Managed Identity'
        elif 'active directory' in auth_value_lower:
            result['auth_method'] = 'Azure AD'
        elif has_user and has_password:
            result['auth_method'] = 'Credentials'
        elif is_integrated or is_trusted:
            result['auth_method'] = 'Integrated Security'

        return result

    def _score_sql_hint_value(self, value: Optional[str]) -> int:
        """Score hint quality, preferring concrete over placeholder values."""
        if not value:
            return 0
        candidate = str(value).strip().lower()
        if not candidate:
            return 0

        placeholders = ['your-', 'changeme', 'example', 'placeholder', '<', 'localhost', 'host.docker.internal']
        if any(tok in candidate for tok in placeholders):
            return 1
        return 2

    def _collect_sql_connection_hints(self) -> Dict[str, Optional[str]]:
        """Collect SQL server/database/auth hints from connections and findings."""
        hints: Dict[str, Optional[str]] = {'server': None, 'database': None, 'auth_method': None, 'port': None}

        def _maybe_update(key: str, value: Optional[str]):
            if not value:
                return
            current = hints.get(key)
            if self._score_sql_hint_value(value) >= self._score_sql_hint_value(current):
                hints[key] = str(value).strip()

        # 1) Existing connection metadata already loaded for this diagram.
        for conn in self.connections:
            parsed = self._parse_sql_connection_string(str(conn.get('notes') or ''))
            for field in ('server', 'database', 'auth_method', 'port'):
                _maybe_update(field, parsed.get(field))

            _maybe_update('server', conn.get('server'))
            _maybe_update('database', conn.get('database'))

            auth = self.get_auth_method(conn)
            if auth and auth.lower() not in {'connection string', 'inferred'}:
                _maybe_update('auth_method', auth)

            port = conn.get('port')
            if port:
                _maybe_update('port', str(port))

        # 2) Findings table can contain direct connection string snippets.
        with get_db_connection() as conn:
            params: List[object] = [self.experiment_id]
            where_repo = ''
            if self.repo_name:
                where_repo = ' AND (repo.repo_name = ? OR f.source_file LIKE ?)'
                params.extend([self.repo_name, f'%/{self.repo_name}/%'])

            rows = conn.execute(
                f"""
                SELECT f.code_snippet, f.description
                FROM findings f
                LEFT JOIN repositories repo ON f.repo_id = repo.id
                WHERE f.experiment_id = ?
                  AND (
                    LOWER(COALESCE(f.rule_id, '')) LIKE '%sql-connection-string%'
                    OR COALESCE(f.code_snippet, '') LIKE '%Server=%'
                    OR COALESCE(f.code_snippet, '') LIKE '%Data Source=%'
                  )
                  {where_repo}
                ORDER BY f.id
                """,
                params,
            ).fetchall()

            for row in rows:
                snippet = str(row['code_snippet'] or '')
                description = str(row['description'] or '')

                for text in (snippet, description):
                    parsed = self._parse_sql_connection_string(text)
                    for field in ('server', 'database', 'auth_method', 'port'):
                        _maybe_update(field, parsed.get(field))

        return hints
    
    def get_auth_method(self, connection: dict) -> str:
        """Extract authentication method from connection."""
        auth = connection.get('auth_method') or connection.get('authentication') or ''
        normalized = str(auth).strip()
        if not normalized:
            return ''

        lowered = normalized.lower()
        if lowered in {'connection string', 'inferred', 'unknown'}:
            return ''

        parsed = self._parse_sql_connection_string(normalized)
        return str(parsed.get('auth_method') or normalized).strip()

    def render_sql_hierarchy(self, sql_resources: List[dict]) -> List[str]:
        """Render SQL server nodes, optionally as subgraphs with child database nodes."""
        if not sql_resources:
            return []

        lines: List[str] = []
        preferred_database = str(self.sql_hints.get('database') or '').strip()

        for res in sql_resources:
            server_name = res['resource_name']
            server_id = sanitize_id(server_name)
            props = res.get('properties') or {}
            database_name = str(props.get('database') or preferred_database or '').strip()

            if database_name:
                db_node_id = sanitize_id(f"{server_name}_{database_name}")
                # Use server_id as the subgraph ID so that existing connection edges
                # (which reference the server by name/id) correctly target the subgraph.
                # Databases are rendered as child nodes inside.
                lines.append(f"  subgraph {server_id}[\"SQL Server: {server_name}\"]")
                lines.append(f"    {db_node_id}[\"{database_name}\"]")
                lines.append("  end")
                self.emitted_nodes.add(server_name)
                self.emitted_nodes.add(database_name)
            else:
                lines.append(f"  {server_id}[\"{server_name}\"]")
                self.emitted_nodes.add(server_name)

        return lines
    
    def render_node(self, resource: dict, indent: str = "  ") -> str:
        """Render a single node."""
        node_id = sanitize_id(resource['resource_name'])
        name = resource['resource_name']
        # Truncate long names to fit in box
        if len(name) > 50:
            name = name[:47] + "..."
        
        label = name
        self.emitted_nodes.add(resource['resource_name'])
        return f"{indent}{node_id}[\"{label}\"]"
    
    def render_subgraph(self, title: str, resources: List[dict], indent: str = "  ") -> List[str]:
        """Render a subgraph containing resources."""
        if not resources:
            return []
        
        lines = []
        subgraph_id = sanitize_id(title.lower().replace(' ', '_'))
        lines.append(f"{indent}subgraph {subgraph_id}[{title}]")
        
        for res in resources:
            # Check if this resource has children that should be nested
            children = self.children_by_parent.get(res['id'], [])
            if children:
                # Render as nested subgraph
                child_lines = self.render_subgraph(
                    res['resource_name'], 
                    children, 
                    indent=indent + "  "
                )
                lines.extend(child_lines)
            else:
                # Render as simple node
                lines.append(self.render_node(res, indent=indent + "  "))
        
        lines.append(f"{indent}end")
        return lines
    
    def render_apim_hierarchy(self, apim_apis: List[dict], products: List[dict]) -> List[str]:
        """Render APIM with Products and Operations nested properly.
        
        Structure: APIM → Products (as nodes with operations inside as subgraph)
        """
        if not apim_apis and not products:
            return []
        
        lines = []
        lines.append("  subgraph apim[API Management]")
        
        # Get all API operations
        all_operations = []
        for api in apim_apis:
            api_children = self.children_by_parent.get(api['id'], [])
            all_operations.extend([c for c in api_children if self.is_api_operation(c)])

        # Prune APIM entries that are not connected to any edge.
        products = [p for p in products if self._is_connected_name(p.get('resource_name', ''))]
        all_operations = [op for op in all_operations if self._is_connected_name(op.get('resource_name', ''))]
        
        if products and all_operations:
            # Render the first product as a proper subgraph with operations
            # (In reality each product may have different operations, but for simplicity we'll show the main product)
            main_product = products[0]
            product_id = sanitize_id(main_product['resource_name'])
            lines.append(f"    subgraph {product_id}[\"{main_product['resource_name']}\"]")
            
            for op in all_operations:
                lines.append(self.render_node(op, indent="      "))
            
            lines.append("    end")
            self.emitted_nodes.add(main_product['resource_name'])  # Mark as emitted for connections
        elif products:
            # Products exist but operations are not available in extracted data; show products directly.
            for product in products:
                lines.append(self.render_node(product, indent="    "))
        elif all_operations:
            # Just operations, no products
            for op in all_operations:
                lines.append(self.render_node(op, indent="    "))
        
        lines.append("  end")
        return lines
    
    def render_kubernetes_cluster(self, k8s_resources: List[dict]) -> List[str]:
        """Render Kubernetes/AKS cluster with namespace subgraphs and workloads."""
        if not k8s_resources:
            return []

        # Separate the AKS cluster resource(s) from the workloads inside them.
        # Cluster resources should label the outer subgraph, not appear as
        # namespace-bucketed workload nodes inside it.
        cluster_resources = [
            r for r in k8s_resources
            if 'kubernetes_cluster' in (r.get('resource_type') or '').lower()
        ]
        workload_resources = [
            r for r in k8s_resources
            if r not in cluster_resources and self._is_connected_name(r.get('resource_name', ''))
        ]

        if not workload_resources:
            return []

        # Build the outer subgraph label, incorporating cluster names when known.
        if cluster_resources:
            cluster_names = ", ".join(r['resource_name'] for r in cluster_resources)
            cluster_label = f"Kubernetes Cluster<br/>{cluster_names}"
            # Mark cluster resources as emitted so connections to them still render.
            for r in cluster_resources:
                self.emitted_nodes.add(r['resource_name'])
        else:
            cluster_label = "Kubernetes Cluster"

        lines = []
        lines.append(f"  subgraph k8s[\"{cluster_label}\"]")

        resources_by_namespace: Dict[str, List[dict]] = defaultdict(list)
        emitted_node_ids: Set[str] = set()
        for res in workload_resources:
            node_id = sanitize_id(res['resource_name'])
            if node_id in emitted_node_ids:
                continue
            emitted_node_ids.add(node_id)
            namespace = self.get_kubernetes_namespace(res)
            resources_by_namespace[namespace].append(res)

        for namespace, namespace_resources in resources_by_namespace.items():
            namespace_id = f"k8s_ns_{sanitize_id(namespace)}"
            lines.append(f"    subgraph {namespace_id}[\"{namespace}\"]")

            for res in namespace_resources:
                props = res.get('properties', {})
                image = str(props.get('image', '') or '').strip()
                dockerfile = str(props.get('dockerfile', '') or '').strip()

                node_id = sanitize_id(res['resource_name'])
                name = res['resource_name']

                # Only show image when it adds information beyond the resource name.
                # If the image tag is already a prefix of the workload name (e.g.
                # image="aks-helloworld" and name="aks-helloworld-dr-testapp-api"),
                # the label would appear to repeat part of the name, so suppress it.
                image_is_redundant = image and name.lower().startswith(image.lower())

                if image and not image_is_redundant:
                    label = f"{name}<br/>📦 Image: {image}"
                elif dockerfile:
                    label = f"{name}<br/>🐳 Dockerfile: {Path(dockerfile).name}"
                else:
                    label = name

                lines.append(f"      {node_id}[\"{label}\"]")
                self.emitted_nodes.add(res['resource_name'])

            lines.append("    end")

        lines.append("  end")
        return lines
    
    def render_service_bus(self, sb_resources: List[dict]) -> List[str]:
        """Render Service Bus with Topics/Queues/Subscriptions nested."""
        if not sb_resources:
            return []
        
        lines = []
        lines.append("  subgraph servicebus[Service Bus]")

        namespaces = [r for r in sb_resources if 'namespace' in r.get('resource_type', '').lower()]
        rendered_ids = set()

        def _render_topic(topic: dict, indent: str = "    "):
            topic_subs = [
                s for s in self.children_by_parent.get(topic['id'], [])
                if self.is_service_bus_subscription(s)
            ]

            if topic_subs:
                topic_id = sanitize_id(topic['resource_name'])
                lines.append(f"{indent}subgraph {topic_id}[\"📬 {topic['resource_name']}\"]")
                for sub in topic_subs:
                    lines.append(self.render_node(sub, indent=indent + "  "))
                    rendered_ids.add(sub['id'])
                lines.append(f"{indent}end")
            else:
                lines.append(f"{indent}{sanitize_id(topic['resource_name'])}[\"📬 {topic['resource_name']}\"]")
                self.emitted_nodes.add(topic['resource_name'])

            rendered_ids.add(topic['id'])

        def _render_queue(queue: dict, indent: str = "    "):
            lines.append(f"{indent}{sanitize_id(queue['resource_name'])}[\"📥 {queue['resource_name']}\"]")
            self.emitted_nodes.add(queue['resource_name'])
            rendered_ids.add(queue['id'])

        # Render each namespace and its known children.
        for namespace in namespaces:
            namespace_name = namespace['resource_name']
            namespace_id = sanitize_id(namespace_name)
            ns_children = self.children_by_parent.get(namespace['id'], [])
            topics = [r for r in ns_children if self.is_service_bus_topic(r)]
            queues = [r for r in ns_children if self.is_service_bus_queue(r)]
            topics_to_render = []
            for topic in topics:
                topic_subs = [
                    s for s in self.children_by_parent.get(topic['id'], [])
                    if self.is_service_bus_subscription(s) and self._is_connected_name(s.get('resource_name', ''))
                ]
                if self._is_connected_name(topic.get('resource_name', '')) or topic_subs:
                    topics_to_render.append(topic)
            queues_to_render = [q for q in queues if self._is_connected_name(q.get('resource_name', ''))]

            namespace_connected = self._is_connected_name(namespace_name)
            if not namespace_connected and not topics_to_render and not queues_to_render:
                continue

            namespace_subgraph_id = f"sb_ns_{namespace_id}"
            lines.append(f"    subgraph {namespace_subgraph_id}[\"🚌 {namespace_name}\"]")
            # Keep a concrete namespace node only when it actually participates in connections.
            if namespace_connected:
                lines.append(f"      {namespace_id}[\"{namespace_name}\"]")
                self.emitted_nodes.add(namespace_name)
            rendered_ids.add(namespace['id'])

            for topic in topics_to_render:
                _render_topic(topic, indent="      ")

            for queue in queues_to_render:
                _render_queue(queue, indent="      ")

            lines.append("    end")

        # Render orphan Service Bus resources that are known but not parent-linked.
        orphan_topics = [
            r for r in sb_resources
            if self.is_service_bus_topic(r) and r['id'] not in rendered_ids
        ]
        orphan_queues = [
            r for r in sb_resources
            if self.is_service_bus_queue(r) and r['id'] not in rendered_ids
        ]
        orphan_subscriptions = [
            r for r in sb_resources
            if self.is_service_bus_subscription(r) and r['id'] not in rendered_ids
        ]

        for topic in orphan_topics:
            if self._is_connected_name(topic.get('resource_name', '')):
                _render_topic(topic)

        for queue in orphan_queues:
            if self._is_connected_name(queue.get('resource_name', '')):
                _render_queue(queue)

        for subscription in orphan_subscriptions:
            if self._is_connected_name(subscription.get('resource_name', '')):
                lines.append(self.render_node(subscription, indent="    "))
                rendered_ids.add(subscription['id'])
        
        lines.append("  end")
        return lines
    
    def render_connections(self) -> List[str]:
        """Render all connections with labels and line styles."""
        lines = []
        lines.append("")
        
        for conn in self.connections:
            src = conn.get('source')
            tgt = conn.get('target')
            
            if not src or not tgt:
                continue

            # Never render self-referential edges (node -> same node); these add noise.
            if src == tgt or sanitize_id(src) == sanitize_id(tgt):
                continue
            
            # Skip if nodes weren't emitted
            if src != 'Internet' and src not in self.emitted_nodes:
                continue
            if tgt != 'Internet' and tgt not in self.emitted_nodes:
                continue
            
            src_id = sanitize_id(src) if src != 'Internet' else 'internet'
            tgt_id = sanitize_id(tgt) if tgt != 'Internet' else 'internet'
            
            # Build label
            label_parts = []
            
            # Authentication: prefer explicit key names (SharedAccessKeyName/Key) over protocol labels.
            auth = self.get_auth_method(conn)
            # Some connections store SAS key name under 'key_name' or 'shared_access_key_name' — normalize
            key_name = conn.get('key_name') or conn.get('shared_access_key_name') or conn.get('SharedAccessKeyName') or conn.get('sharedAccessKeyName')
            if key_name:
                label_parts.append(f"🔐 Key: {key_name}")
            elif auth:
                # Only show raw auth string if it definitively represents a method (e.g., 'SAS', 'ManagedIdentity')
                label_parts.append(f"🔐 {auth}")

            protocol = conn.get('protocol', '')
            if protocol:
                # Only include protocol if it was actually detected on the connection record
                label_parts.append(protocol)
            
            port = conn.get('port')
            if port:
                label_parts.append(f":{port}")
            
            label = " ".join(label_parts) if label_parts else ""
            
            # Determine line style (solid or dashed)
            is_confirmed = conn.get('confirmed', True)  # Default to solid if not specified
            arrow = "-->" if is_confirmed else "-.->"  # Dashed for unconfirmed
            
            if label:
                lines.append(f"  {src_id} {arrow}|{label}| {tgt_id}")
            else:
                lines.append(f"  {src_id} {arrow} {tgt_id}")
        
        return lines
    
    def infer_connections(self) -> bool:
        """Infer connections from resource relationships and properties when resource_connections is empty."""
        has_internet = False
        
        # If we already have connections from DB, filter out technical ones
        if len(self.connections) > 10:
            filtered_connections = []
            for conn in self.connections:
                conn_type = str(conn.get('connection_type', '')).lower()
                if conn_type not in ('depends_on', 'contains'):
                    filtered_connections.append(conn)
            self.connections = filtered_connections
        
        # Track connected pairs to avoid duplicates
        connected_pairs = set()
        for conn in self.connections:
            src = conn.get('source')
            tgt = conn.get('target')
            if src and tgt:
                connected_pairs.add((src, tgt))
        
        with get_db_connection() as conn:
            # Check for CONFIRMED internet exposure
            rows = conn.execute("""
                SELECT DISTINCT r.resource_name
                FROM resources r
                JOIN resource_properties rp ON r.id = rp.resource_id
                WHERE r.experiment_id = ?
                  AND rp.property_key = 'internet_access'
                  AND LOWER(rp.property_value) = 'true'
            """, [self.experiment_id]).fetchall()
            
            for row in rows:
                name = row['resource_name']
                pair_key = ('Internet', name)
                if name in self.resource_by_name and pair_key not in connected_pairs:
                    self.connections.append({
                        'source': 'Internet',
                        'target': name,
                        'connection_type': 'confirmed_public',
                        'protocol': 'https',
                        'confirmed': True
                    })
                    connected_pairs.add(pair_key)
                    has_internet = True
        
        for r in self.resources:
            if self.is_public_edge_resource(r):
                pair_key = ('Internet', r['resource_name'])
                if pair_key not in connected_pairs:
                    self.connections.append({
                        'source': 'Internet',
                        'target': r['resource_name'],
                        'connection_type': 'unconfirmed_public',
                        'protocol': 'https',
                        'auth_method': 'Subscription Key' if self.is_api_gateway(r) or self.is_api_operation(r) else '',
                        'confirmed': False
                    })
                    connected_pairs.add(pair_key)
                    has_internet = True
        
        # API Operations → Backend (each operation connects to backend)
        api_operations = [r for r in self.resources if self.is_api_operation(r)]
        k8s_services = [r for r in self.resources if self.is_kubernetes(r) and 'service' in r.get('resource_type', '').lower()]
        
        # Find the main product name to match against services
        products = [r for r in self.resources if self.is_api_product(r)]
        product_name_prefixes = [p['resource_name'] for p in products]
        
        for api_op in api_operations:
            op_name = api_op['resource_name']
            
            for svc in k8s_services:
                svc_name = svc['resource_name']
                # Match if service name contains any product prefix that might relate to this operation
                matched = False
                for prefix in product_name_prefixes:
                    if svc_name.startswith(prefix) or prefix in svc_name:
                        matched = True
                        break
                
                if matched:
                    # Avoid emitting operation -> service self-loop when names collide.
                    if op_name == svc_name:
                        continue
                    pair_key = (op_name, svc_name)
                    if pair_key not in connected_pairs:
                        self.connections.append({
                            'source': op_name,
                            'target': svc_name,
                            'connection_type': 'routes_to',
                            'protocol': 'http'
                        })
                        connected_pairs.add(pair_key)
        
        # Service Bus → Listener
        k8s_deployments = [r for r in self.resources if self.is_kubernetes(r) and 'deployment' in r.get('resource_type', '').lower()]
        sb_queues = [r for r in self.resources if self.is_service_bus_queue(r)]
        sb_topics = [r for r in self.resources if self.is_service_bus_topic(r)]
        
        for deployment in k8s_deployments:
            dep_name = deployment['resource_name'].lower()
            # Include explicitly service-bus-oriented workloads even when they
            # are not named as listeners/workers.
            if any(kw in dep_name for kw in ['queue', 'listener', 'worker', 'consumer', 'servicebus']):
                for queue in sb_queues:
                    pair_key = (queue['resource_name'], deployment['resource_name'])
                    if pair_key not in connected_pairs:
                        self.connections.append({
                            'source': queue['resource_name'],
                            'target': deployment['resource_name'],
                            'connection_type': 'consumed_by'
                        })
                        connected_pairs.add(pair_key)
                for topic in sb_topics:
                    pair_key = (topic['resource_name'], deployment['resource_name'])
                    if pair_key not in connected_pairs:
                        self.connections.append({
                            'source': topic['resource_name'],
                            'target': deployment['resource_name'],
                            'connection_type': 'consumed_by'
                        })
                        connected_pairs.add(pair_key)

        # App → SQL data dependency. If SQL signals exist, always show an explicit
        # SQL Server node so data-store dependencies are visible in architecture diagrams.
        sql_signal_names = set()
        for resource in self.resources:
            if self.is_database_resource(resource):
                sql_signal_names.add((resource.get('resource_name') or '').strip())

        for conn in self.connections:
            src = str(conn.get('source') or '').strip()
            tgt = str(conn.get('target') or '').strip()
            if 'sql' in src.lower() or 'database' in src.lower() or 'mssql' in src.lower():
                sql_signal_names.add(src)
            if 'sql' in tgt.lower() or 'database' in tgt.lower() or 'mssql' in tgt.lower():
                sql_signal_names.add(tgt)

        sql_signal_names = {n for n in sql_signal_names if n}
        if sql_signal_names:
            sql_hints = self._collect_sql_connection_hints()
            self.sql_hints = dict(sql_hints)
            sql_node_name = self._ensure_synthetic_sql_server_node(sql_hints.get('server'))

            sql_resource = self.resource_by_name.get(sql_node_name)
            if sql_resource is not None:
                props = dict(sql_resource.get('properties') or {})
                if sql_hints.get('database'):
                    props['database'] = sql_hints.get('database')
                sql_resource['properties'] = props

            app_services = [r for r in self.resources if self.is_application_service(r)]

            # Prefer API-like services for SQL dependency edges.
            app_services.sort(
                key=lambda r: 0 if 'api' in (r.get('resource_name') or '').lower() else 1
            )

            for app in app_services[:2]:
                app_name = app['resource_name']
                pair_key = (app_name, sql_node_name)
                if pair_key in connected_pairs:
                    continue

                has_existing_sql_link = any(
                    (
                        (str(existing.get('source') or '').strip() == app_name and str(existing.get('target') or '').strip() in sql_signal_names)
                        or (str(existing.get('target') or '').strip() == app_name and str(existing.get('source') or '').strip() in sql_signal_names)
                    )
                    for existing in self.connections
                )

                self.connections.append({
                    'source': app_name,
                    'target': sql_node_name,
                    'connection_type': 'uses_database',
                    'protocol': 'tcp',
                    'port': sql_hints.get('port') or 1433,
                    'auth_method': sql_hints.get('auth_method') or 'Credentials',
                    'server': sql_hints.get('server') or sql_node_name,
                    'database': sql_hints.get('database'),
                    'confirmed': has_existing_sql_link,
                })
                connected_pairs.add(pair_key)
        
        return has_internet
    
    def generate(self) -> str:
        """Generate the complete hierarchical diagram."""
        self.load_data()
        
        if not self.resources:
            return "flowchart LR\n  empty[No resources found]"
        
        lines = ["flowchart LR"]
        
        # Infer connections if resource_connections table is empty/sparse
        if self.infer_connections():
            lines.append("  internet[🌐 Internet]")

        # Track resources that actually participate in at least one edge so we
        # can prune isolated boxes that add visual noise.
        self.connected_resource_names = {
            n for c in self.connections for n in (c.get('source'), c.get('target'))
            if n and n != 'Internet'
        }
        
        # Filter out children that will be rendered in subgraphs
        all_children = set()
        for children in self.children_by_parent.values():
            all_children.update(c['id'] for c in children)
        
        # Categorize resources  
        apim_apis = [r for r in self.resources if self.is_api_gateway(r) and r['id'] not in all_children
                    and not r.get('resource_name', '').startswith('${var.') and not r.get('resource_name', '').startswith('${local.')]
        apim_products = [r for r in self.resources if self.is_api_product(r) and r['id'] not in all_children
                        and not r.get('resource_name', '').startswith('${var.') and not r.get('resource_name', '').startswith('${local.')]
        k8s_resources = [r for r in self.resources if self.is_kubernetes(r) and r['id'] not in all_children
                        and not r.get('resource_name', '').startswith('${var.') and not r.get('resource_name', '').startswith('${local.')]
        # Don't filter SB by all_children - we'll handle parent-child internally
        sb_resources = [r for r in self.resources if self.is_service_bus(r)
                       and not r.get('resource_name', '').startswith('${var.') and not r.get('resource_name', '').startswith('${local.')]
        
        # Collect IDs that will be rendered in subgraphs
        apim_related_ids = set()
        for api in apim_apis:
            apim_related_ids.add(api['id'])
            for child in self.children_by_parent.get(api['id'], []):
                apim_related_ids.add(child['id'])
        
        for product in apim_products:
            apim_related_ids.add(product['id'])
        
        # Collect Service Bus IDs
        sb_related_ids = {r['id'] for r in sb_resources}
        
        # Collect K8s IDs
        k8s_related_ids = {r['id'] for r in k8s_resources}
        
        # Render APIM hierarchy
        apim_lines = self.render_apim_hierarchy(apim_apis, apim_products)
        if apim_lines:
            lines.extend(apim_lines)
            lines.append("")
        
        # Render Kubernetes cluster
        k8s_lines = self.render_kubernetes_cluster(k8s_resources)
        if k8s_lines:
            lines.extend(k8s_lines)
            lines.append("")
        
        # Render Service Bus
        sb_lines = self.render_service_bus(sb_resources)
        if sb_lines:
            lines.extend(sb_lines)
            lines.append("")
        
        # Render other resources not in above categories (exclude subscriptions which are metadata)
        connected_resource_names = set(self.connected_resource_names)

        sql_resources = [
            r for r in self.resources
            if self.is_database_resource(r)
            and r['id'] not in all_children
            and r['id'] not in apim_related_ids
            and r['id'] not in sb_related_ids
            and r['id'] not in k8s_related_ids
            and not r.get('resource_name', '').startswith('${var.')
            and not r.get('resource_name', '').startswith('${local.')
        ]

        sql_lines = self.render_sql_hierarchy(sql_resources)
        if sql_lines:
            lines.extend(sql_lines)
            lines.append("")

        other_resources = [
            r for r in self.resources 
            if r['id'] not in all_children 
            and r['id'] not in apim_related_ids
            and r['id'] not in sb_related_ids
            and r['id'] not in k8s_related_ids
            and r not in sql_resources
            and not self.is_api_gateway(r)
            and not self.is_kubernetes(r)
            and not self.is_service_bus(r)
            and not self.is_api_product(r)
            and 'subscription' not in r.get('resource_type', '').lower()  # Exclude subscriptions - they're metadata
            and 'resource_group' not in r.get('resource_type', '').lower()  # Exclude resource groups
            and 'terraform_data' not in r.get('resource_type', '').lower()  # Exclude terraform data
            and not r.get('resource_name', '').startswith('${var.')  # Exclude unresolved variables
            and not r.get('resource_name', '').startswith('${local.')  # Exclude unresolved locals
            and not (
                self.is_identity_principal_like(r)
                and r.get('resource_name') not in connected_resource_names
            )
            and self._is_connected_name(r.get('resource_name', ''))
        ]
        
        for res in other_resources:
            lines.append(self.render_node(res))
        
        if other_resources:
            lines.append("")
        
        # Render connections
        conn_lines = self.render_connections()
        lines.extend(conn_lines)
        
        # Add styling for resource categories
        style_lines = self.render_styles()
        if style_lines:
            lines.append("")
            lines.extend(style_lines)
        
        # Add CSS animation for arrows
        lines.append("")
      #  lines.append("%%{init: {'theme':'dark'} }%%")
        
        return "\n".join(lines)
    
    def render_styles(self) -> List[str]:
        """Generate color-coded borders for resource categories."""
        lines = []
        
        # Category colors from old generator
        category_colors = {
            "Compute": "#0066cc",
            "Container": "#0066cc",
            "Database": "#00aa00",
            "Storage": "#00aa00",
            "Identity": "#f59f00",
            "Security": "#ff6b6b",
            "Network": "#7e57c2",
            "Monitoring": "#888888",
        }
        
        # Resolve style per rendered node id (not resource name) to avoid duplicate
        # style lines when multiple resources sanitize to the same Mermaid id.
        category_priority = {
            "Security": 8,
            "Identity": 7,
            "Database": 6,
            "Storage": 5,
            "Network": 4,
            "Container": 3,
            "Compute": 2,
            "Monitoring": 1,
            "Other": 0,
        }
        style_by_node_id: Dict[str, Tuple[int, str]] = {}

        # Group emitted nodes by category
        for resource_name in self.emitted_nodes:
            if resource_name == 'Internet':
                continue
            
            resource = self.resource_by_name.get(resource_name)
            if not resource:
                continue
            
            # Get category
            category = self._get_category(resource)
            color = category_colors.get(category)
            
            if color:
                node_id = sanitize_id(resource_name)
                priority = category_priority.get(category, 0)
                existing = style_by_node_id.get(node_id)
                if existing is None or priority >= existing[0]:
                    style_by_node_id[node_id] = (priority, color)

        for node_id in sorted(style_by_node_id.keys()):
            color = style_by_node_id[node_id][1]
            lines.append(f"  style {node_id} stroke:{color}, stroke-width:2px")
        
        return lines
    
    def _get_category(self, resource: dict) -> str:
        """Get resource category for styling."""
        rtype = (resource.get('resource_type') or '').lower()
        
        # Map resource types to categories
        if any(t in rtype for t in ['compute', 'vm', 'ec2', 'instance']):
            return 'Compute'
        if any(t in rtype for t in ['kubernetes', 'aks', 'eks', 'gke', 'container', 'deployment', 'service']):
            return 'Container'
        if any(t in rtype for t in ['database', 'sql', 'rds', 'cosmos', 'dynamodb']):
            return 'Database'
        if any(t in rtype for t in ['storage', 's3', 'blob', 'bucket']):
            return 'Storage'
        if any(t in rtype for t in ['identity', 'iam', 'principal', 'role']):
            return 'Identity'
        if any(t in rtype for t in ['keyvault', 'secret', 'kms']):
            return 'Security'
        if any(t in rtype for t in ['network', 'vpc', 'vnet', 'subnet', 'nsg', 'security_group']):
            return 'Network'
        if any(t in rtype for t in ['monitor', 'alert', 'metric', 'log']):
            return 'Monitoring'
        
        # API Management gets Identity color (authentication boundary)
        if any(t in rtype for t in ['api_management', 'api_gateway', 'apim']):
            return 'Identity'
        
        # Service Bus is Network
        if 'servicebus' in rtype or 'queue' in rtype or 'topic' in rtype:
            return 'Network'
        
        return 'Other'
    
    def detect_cloud_provider(self) -> str:
        """Detect the primary cloud provider from resources."""
        provider_counts = {}
        
        for resource in self.resources:
            provider = (resource.get('provider') or '').lower()
            if provider and provider != 'unknown':
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
        
        if not provider_counts:
            return 'Cloud'
        
        # Return most common provider
        primary_provider = max(provider_counts.items(), key=lambda x: x[1])[0]
        
        # Capitalize
        provider_map = {
            'azure': 'Azure',
            'aws': 'AWS',
            'gcp': 'GCP',
            'google': 'GCP',
            'kubernetes': 'Kubernetes',
            'terraform': 'Terraform',
        }
        
        return provider_map.get(primary_provider, primary_provider.title())


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate hierarchical architecture diagrams")
    parser.add_argument("--experiment-id", required=True, help="Experiment ID")
    parser.add_argument("--repo", help="Filter to specific repository")
    parser.add_argument("--output", type=Path, help="Output file path")
    parser.add_argument("--persist-db", action="store_true", help="Persist diagram to cloud_diagrams table")
    
    args = parser.parse_args()
    
    builder = HierarchicalDiagramBuilder(args.experiment_id, repo_name=args.repo)
    diagram = builder.generate()
    
    # Detect cloud provider for title
    provider = builder.detect_cloud_provider()
    diagram_title = f"{provider} Architecture"
    
    # Persist to database if requested
    if args.persist_db:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
            from db_helpers import get_db_connection
            
            with get_db_connection() as conn:
                # Check if diagram already exists
                existing = conn.execute(
                    "SELECT id FROM cloud_diagrams WHERE experiment_id = ? AND provider = ?",
                    [args.experiment_id, provider.lower()]
                ).fetchone()
                
                if existing:
                    # Update existing
                    conn.execute(
                        "UPDATE cloud_diagrams SET mermaid_code = ?, diagram_title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        [diagram, diagram_title, existing['id']]
                    )
                else:
                    # Insert new
                    conn.execute(
                        """INSERT INTO cloud_diagrams (experiment_id, provider, diagram_title, mermaid_code, display_order)
                           VALUES (?, ?, ?, ?, ?)""",
                        [args.experiment_id, provider.lower(), diagram_title, diagram, 0]
                    )
                conn.commit()
                print(f"Diagram persisted to cloud_diagrams table as '{diagram_title}'")
        except Exception as e:
            print(f"Warning: Failed to persist diagram to DB: {e}", file=sys.stderr)
    
    if args.output:
        args.output.write_text(diagram)
        print(f"Diagram written to {args.output}")
    else:
        print(diagram)


if __name__ == "__main__":
    main()
