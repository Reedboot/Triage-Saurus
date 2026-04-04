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
    
    def __init__(self, experiment_id: str, repo_name: Optional[str] = None,
                 include_api_operations: Optional[bool] = None,
                 repo_path: Optional[str] = None,
                 provider_filter: Optional[str] = None):
        self.experiment_id = experiment_id
        self.repo_name = repo_name
        # When True, parse OpenAPI specs and add operation nodes under APIM APIs.
        # When None, auto-detect (show ops only if there are <10 operations in DB).
        self.include_api_operations = include_api_operations
        # Filesystem path to the repository root (needed for OpenAPI spec discovery).
        self.repo_path = repo_path
        # Filter to a specific cloud provider (lowercase)
        self.provider_filter = provider_filter
        self.resources = []
        self.connections = []
        self.sql_hints: Dict[str, Optional[str]] = {}
        self.resource_by_name = {}
        self.resource_by_id = {}
        self.children_by_parent = defaultdict(list)
        self.emitted_nodes = set()
        self.connected_resource_names: Set[str] = set()
        # Maps resource_name → Mermaid node ID; populated by render_* methods so that
        # render_connections can use the correct (potentially prefixed) ID for edges.
        self.node_id_override: Dict[str, str] = {}
        # Tracks Mermaid node IDs that have already been emitted to detect duplicates.
        # When the same sanitized name appears for multiple resources (e.g. many Azure
        # resources named "example"), a qualified ID using the resource_type prefix is
        # generated instead so Mermaid doesn't collapse distinct nodes into one.
        self._emitted_mermaid_ids: Set[str] = set()

    def _assign_resource_by_name(self, name: str, resource: dict) -> None:
        """Assign a resource into resource_by_name; preserve duplicates as lists."""
        existing = self.resource_by_name.get(name)
        if existing is None:
            self.resource_by_name[name] = resource
        elif isinstance(existing, list):
            existing.append(resource)
        else:
            self.resource_by_name[name] = [existing, resource]

    def _get_primary_resource(self, name: str):
        """Return primary resource dict for a resource name (first entry if duplicated)."""
        existing = self.resource_by_name.get(name)
        if isinstance(existing, list):
            return existing[0]
        return existing


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
        
        # Filter to specific provider if requested
        if self.provider_filter:
            self.resources = [r for r in self.resources if (r.get('provider') or '').lower() == self.provider_filter.lower()]
        
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
            # Assign into mapping, preserving duplicates as lists when necessary.
            self._assign_resource_by_name(r['resource_name'], r)
        
        self.resource_by_id = {r['id']: r for r in self.resources}
        
        # Filter connections to only include those between resources in this provider/repo
        if self.provider_filter or self.repo_name:
            valid_names = set(r['resource_name'] for r in self.resources)
            self.connections = [
                c for c in self.connections
                if c.get('source') in valid_names and c.get('target') in valid_names
            ]
        
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
    
    def _load_openapi_operations(self) -> None:
        """Discover OpenAPI spec files in the repo and inject synthetic operation resources
        as children of matching APIM API resources (when include_api_operations is True).

        Synthetic resources use negative integer IDs (never clash with DB rows).
        The matching is: OpenAPI filename stem tokens ↔ APIM API name tokens.
        Falls back to a 1-to-1 pairing when only one spec is present.
        """
        if not self.repo_path:
            return

        root = Path(self.repo_path)
        # Gather candidate OpenAPI spec files.
        openapi_candidates: List[Path] = []
        for pat in ("*.openapi.yaml", "*.openapi.yml", "*openapi*.yaml", "*openapi*.yml",
                    "*swagger*.yaml", "*swagger*.yml"):
            openapi_candidates.extend(root.rglob(pat))
        # Deduplicate preserving order.
        seen: Set[str] = set()
        openapi_files: List[Path] = []
        for p in openapi_candidates:
            sp = str(p)
            if sp not in seen:
                seen.add(sp)
                openapi_files.append(p)

        if not openapi_files:
            return

        def _parse_ops(path: Path) -> List[str]:
            """Return list of 'METHOD /path' strings from an OpenAPI YAML."""
            ops: List[str] = []
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
                lines = text.splitlines()
            except Exception:
                return ops
            in_paths = False
            current_path: Optional[str] = None
            for ln in lines:
                if re.match(r'^\s*paths\s*:\s*$', ln):
                    in_paths = True
                    current_path = None
                    continue
                if in_paths and re.match(r'^\s{0,1}[A-Za-z_]+\s*:\s*$', ln):
                    in_paths = False
                    current_path = None
                    continue
                if not in_paths:
                    continue
                m_path = re.match(r'^\s{2,}(/[^\s:]+)\s*:\s*$', ln)
                if m_path:
                    current_path = m_path.group(1)
                    continue
                m_method = re.match(r'^\s{4,}(get|post|put|patch|delete|head|options)\s*:\s*$',
                                    ln, re.IGNORECASE)
                if m_method and current_path:
                    ops.append(f"{m_method.group(1).upper()} {current_path}")
            return ops

        def _tokens(s: str) -> Set[str]:
            return {t for t in re.split(r'[^a-z0-9]+', (s or '').lower()) if len(t) >= 3}

        # Identify APIM API resources in the loaded set.
        apim_apis = [r for r in self.resources if self.is_api_gateway(r)]
        if not apim_apis:
            return

        parsed: Dict[str, List[str]] = {str(p): _parse_ops(p) for p in openapi_files}
        parsed = {k: v for k, v in parsed.items() if v}
        if not parsed:
            return

        unused_files = set(parsed.keys())
        synthetic_id = -1

        for api in apim_apis:
            api_tokens = _tokens(str(api.get('resource_name') or ''))
            best_file: Optional[str] = None
            best_score = -1
            for f in list(unused_files):
                stem_tokens = _tokens(Path(f).stem)
                score = len(api_tokens & stem_tokens)
                if score > best_score:
                    best_score = score
                    best_file = f
            # If no token overlap but only one spec left, assign it.
            if best_file is None and len(unused_files) == 1:
                best_file = next(iter(unused_files))
            if best_file is None:
                continue

            for op_name in parsed[best_file]:
                s_id = synthetic_id
                synthetic_id -= 1
                op_resource = {
                    'id': s_id,
                    'resource_name': op_name,
                    'resource_type': 'azurerm_api_management_api_operation',
                    'provider': api.get('provider') or 'azure',
                    'repo_name': api.get('repo_name'),
                    'parent_resource_id': api['id'],
                    'properties': {},
                }
                self.resources.append(op_resource)
                self._assign_resource_by_name(op_name, op_resource)
                self.resource_by_id[s_id] = op_resource
                self.children_by_parent[api['id']].append(op_resource)

            unused_files.discard(best_file)

    def _infer_from_config_files(self) -> None:
        """Scan application config files for runtime service dependencies not captured in Terraform.

        Handles sources that Terraform doesn't model:
        - appsettings*.json : ConnectionStrings.*, ApplicationInsights.*, ServiceBus.* sections
        - hiera/**/*.yaml   : SQL connection strings with Puppet template variables (%{lookup(...)})
        - docker-compose.yml: environment variables with connection strings

        Creates synthetic resource nodes (negative IDs) and workload→service connection edges
        so the diagram shows these dependencies even without a Terraform resource definition.
        """
        if not self.repo_path:
            return

        import json as _json

        root = Path(self.repo_path)

        def _name_tokens(s: str) -> Set[str]:
            return {t for t in re.split(r'[^a-z0-9]+', s.lower()) if len(t) >= 3}

        # Real workloads only (services + deployments) — exclude catalog-info components/APIs
        # which are metadata, not actual deployed pods.
        _META_TYPES = ('component', 'kubernetes_api', 'kubernetes_config')
        k8s_workloads = [
            r for r in self.resources
            if self.is_kubernetes(r)
            and 'cluster' not in (r.get('resource_type') or '').lower()
            and not any(t in (r.get('resource_type') or '').lower() for t in _META_TYPES)
        ]

        def _best_workload_for_dir(directory: Path) -> Optional[dict]:
            """Return the K8s workload whose name tokens best overlap with the directory name.

            Tie-breaks in favour of kubernetes_service/deployment over other types,
            and longer workload names (more specific matches).
            """
            dir_tokens = _name_tokens(directory.name)
            best: Optional[dict] = None
            best_score = 0
            for wl in k8s_workloads:
                score = len(dir_tokens & _name_tokens(wl['resource_name']))
                if score == 0:
                    continue
                # Tie-break: prefer concrete workload types and more specific names
                type_bonus = 1 if wl.get('resource_type') in (
                    'kubernetes_service', 'kubernetes_deployment') else 0
                eff_score = score * 10 + type_bonus + len(wl['resource_name']) // 10
                if eff_score > best_score:
                    best, best_score = wl, eff_score
            return best if best_score > 0 else None

        # Track synthetic resources already created: canonical_key → resource_name
        synth_added: Dict[str, str] = {}
        _synth_id_counter = [-50000]

        def _next_synth_id() -> int:
            _synth_id_counter[0] -= 1
            return _synth_id_counter[0]

        existing_conn_pairs: Set[tuple] = {
            (c.get('source'), c.get('target')) for c in self.connections
        }

        def _add_edge(src_name: str, tgt_name: str, conn_type: str = 'depends_on') -> None:
            if (src_name, tgt_name) not in existing_conn_pairs:
                self.connections.append({
                    'source': src_name,
                    'target': tgt_name,
                    'connection_type': conn_type,
                    'confirmed': False,
                })
                existing_conn_pairs.add((src_name, tgt_name))

        def _ensure_sql_node(db_name: str, server_hint: str, source_file: str) -> str:
            """Return name of a synthetic SQL server node, creating one if needed."""
            db_slug = re.sub(r'[^a-z0-9]', '-', db_name.strip().lower()).strip('-') or 'sql-db'
            key = f'sql:{db_slug}'
            if key in synth_added:
                return synth_added[key]
            rname = f'sql-{db_slug}'
            # Don't duplicate an existing DB resource
            if rname in self.resource_by_name:
                synth_added[key] = rname
                return rname
            synth = {
                'id': _next_synth_id(),
                'resource_name': rname,
                'resource_type': 'azurerm_mssql_server',
                'provider': 'azure',
                'repo_name': self.repo_name,
                'properties': {
                    'server_hint': server_hint,
                    'database': db_name,
                    'source_file': source_file,
                    'inferred': 'true',
                },
            }
            self.resources.append(synth)
            self._assign_resource_by_name(rname, synth)
            self.resource_by_id[synth['id']] = synth
            synth_added[key] = rname
            return rname

        def _find_or_add_monitoring_node(source_file: str) -> Optional[str]:
            """Return name of an App Insights resource, preferring existing ones."""
            for r in self.resources:
                if 'application_insights' in (r.get('resource_type') or '').lower():
                    return r['resource_name']
            key = 'appinsights:inferred'
            if key in synth_added:
                return synth_added[key]
            rname = 'application-insights'
            synth = {
                'id': _next_synth_id(),
                'resource_name': rname,
                'resource_type': 'azurerm_application_insights',
                'provider': 'azure',
                'repo_name': self.repo_name,
                'properties': {'source_file': source_file, 'inferred': 'true'},
            }
            self.resources.append(synth)
            self._assign_resource_by_name(rname, synth)
            self.resource_by_id[synth['id']] = synth
            synth_added[key] = rname
            return rname

        def _any_sql_node_exists() -> Optional[str]:
            """Return name of any SQL node already in resources, or None."""
            for r in self.resources:
                if self.is_database_resource(r):
                    return r['resource_name']
            return None

        def _find_sb_node() -> Optional[str]:
            """Return name of existing Service Bus resource (namespace or queue), if any."""
            for r in self.resources:
                if self.is_service_bus(r) and 'namespace' in (r.get('resource_type') or '').lower():
                    return r['resource_name']
            for r in self.resources:
                if self.is_service_bus(r):
                    return r['resource_name']
            return None

        _SKIP_DIRS = {'test', '.git', 'bin', 'obj', 'node_modules', '__pycache__'}

        # ── Pass 0: Scan Terraform module blocks for per-workload dependencies ──
        # Terraform ``module`` blocks for AKS workloads contain ``app_name`` (the
        # workload name) and ``secrets``/``config`` maps that explicitly list which
        # Azure resources each workload depends on.  Reading these gives us
        # accurate per-workload connections for:
        #   • ApplicationInsights__ConnectionString → App Insights
        #   • ConnectionStrings__SqlServer          → SQL Server
        # rather than guessing from global hiera files or appsettings.json.
        #
        # This pass runs FIRST so that subsequent passes can skip workloads that
        # are already correctly wired.
        _tf_workload_sql_linked: set = set()    # workload names already linked to SQL
        _tf_workload_ai_linked: set = set()     # workload names already linked to App Insights

        # Helpers to extract locals from .tf files (resolve app_name = local.xxx)
        _locals_dict: Dict[str, str] = {}
        _locals_assign_re = re.compile(r'^\s+([a-z][a-z0-9_]+)\s*=\s*"([^"]*)"', re.M)
        for _tf in root.rglob('*.tf'):
            if any(p in _tf.parts for p in {'.git', '.terraform'}):
                continue
            try:
                _tf_text = _tf.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            for _lm in re.finditer(r'^\s*locals\s*\{', _tf_text, re.M):
                _ls, _ld = _lm.end(), 1
                _li = _ls
                while _li < len(_tf_text) and _ld > 0:
                    if _tf_text[_li] == '{':
                        _ld += 1
                    elif _tf_text[_li] == '}':
                        _ld -= 1
                    _li += 1
                for _am in _locals_assign_re.finditer(_tf_text[_ls:_li - 1]):
                    _locals_dict[_am.group(1)] = _am.group(2)

        # Regex patterns for module-block attribute extraction
        _mod_block_re = re.compile(
            r'^\s*module\s+"[^"]+"\s*\{[^}]*\}(?:\s*\{[^}]*\})*', re.M | re.S
        )
        _app_name_re = re.compile(
            r'app_name\s*=\s*(?:"([^"]+)"|local\.([a-z][a-z0-9_]+))', re.I
        )
        _ai_secret_re = re.compile(
            r'ApplicationInsights__ConnectionString\s*=\s*azurerm_application_insights\.([a-z][a-z0-9_\-]+)\.',
            re.I,
        )
        _sql_secret_re = re.compile(
            r'ConnectionStrings__\w*[Ss]ql\w*\s*=\s*var\.',
            re.I,
        )

        def _extract_module_blocks(tf_text: str):
            """Yield raw text of each top-level module { ... } block."""
            i = 0
            lines = tf_text.splitlines(keepends=True)
            mod_start_re = re.compile(r'^\s*module\s+"[^"]+"\s*\{')
            while i < len(lines):
                if mod_start_re.match(lines[i]):
                    depth = 0
                    start = i
                    while i < len(lines):
                        depth += lines[i].count('{') - lines[i].count('}')
                        i += 1
                        if depth <= 0:
                            break
                    yield ''.join(lines[start:i])
                else:
                    i += 1

        for _tf in root.rglob('*.tf'):
            if any(p in _tf.parts for p in {'.git', '.terraform'}):
                continue
            try:
                _tf_text = _tf.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            _rel_tf = str(_tf.relative_to(root)).replace('\\', '/')
            for _mod_text in _extract_module_blocks(_tf_text):
                # Resolve the workload name from app_name (literal or local ref)
                _an_m = _app_name_re.search(_mod_text)
                if not _an_m:
                    continue
                _wl_name = _an_m.group(1) or _locals_dict.get(_an_m.group(2) or '', '')
                if not _wl_name:
                    continue

                # Confirm this workload exists in our resource set
                _wl_resource = next(
                    (wl for wl in k8s_workloads if wl['resource_name'] == _wl_name),
                    None,
                )
                if not _wl_resource:
                    continue

                # App Insights connection
                _ai_m = _ai_secret_re.search(_mod_text)
                if _ai_m:
                    # Find the matching App Insights resource or create a synthetic one
                    _ai_node = next(
                        (r['resource_name'] for r in self.resources
                         if 'application_insights' in (r.get('resource_type') or '').lower()),
                        None,
                    )
                    if _ai_node is None:
                        _ai_node = _find_or_add_monitoring_node(_rel_tf)
                    if _ai_node:
                        _add_edge(_wl_name, _ai_node)
                        _tf_workload_ai_linked.add(_wl_name)

                # SQL connection
                if _sql_secret_re.search(_mod_text):
                    _sql_node = _any_sql_node_exists()
                    if _sql_node:
                        _add_edge(_wl_name, _sql_node)
                        _tf_workload_sql_linked.add(_wl_name)

        # ── Pass 1: Scan hiera / values YAML for SQL connection strings ────────
        # These contain actual server addresses (even if templated) so we get
        # meaningful database names. Process these BEFORE appsettings so that
        # subsequent passes can link to real SQL nodes rather than creating
        # key-name-derived placeholder nodes.
        #
        # When a hiera variable has a workload-specific prefix (e.g.
        # ``aks_helloworld_drtestapp_connectionstring``), only link workloads
        # whose name tokens overlap with that prefix — not all API workloads.
        _HIERA_SQL_RE = re.compile(
            r'^([a-z][a-z0-9_]*):\s*"?Server=tcp:[^;"\s]*\.database\.windows\.net[^;"\s]*;[^"\n]*Database=([^;"\n"\']+)',
            re.IGNORECASE | re.M,
        )
        for yaml_file in root.rglob('*.yaml'):
            if any(p in yaml_file.parts for p in {'.git', '__pycache__', 'node_modules'}):
                continue
            yaml_str = str(yaml_file)
            is_hiera = 'hiera' in yaml_str
            is_values = yaml_file.name.startswith('values')
            is_config = 'config' in yaml_str.lower()
            if not (is_hiera or is_values or is_config):
                continue
            try:
                text = yaml_file.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            for m in _HIERA_SQL_RE.finditer(text):
                var_name = m.group(1)
                db_name = m.group(2).strip().strip('"\'')
                rel_path = str(yaml_file.relative_to(root)).replace('\\', '/')
                sql_node = _ensure_sql_node(db_name, '*.database.windows.net (hiera)', rel_path)

                # Derive a workload-discriminating stub from the variable name by
                # stripping common connection-string suffixes.
                var_stub = re.sub(
                    r'[_-]?(connection[_-]?string|conn[_-]?str|connectionstring)[_-]?.*$',
                    '',
                    var_name,
                    flags=re.IGNORECASE,
                ).rstrip('_-')
                var_tokens = _name_tokens(var_stub)  # e.g. {"aks", "helloworld", "drtestapp"}

                # Find workloads whose name tokens overlap with the variable stub.
                # Require at least 2 token matches to avoid spurious connections.
                # Skip workloads already linked via Terraform module analysis (Pass 0).
                targeted = [
                    wl for wl in k8s_workloads
                    if wl['resource_name'] not in _tf_workload_sql_linked
                    and len(var_tokens & _name_tokens(wl['resource_name'])) >= 2
                ]
                # When Pass 0 already wired at least one workload to SQL via Terraform
                # module analysis, the remaining workloads almost certainly don't need
                # this SQL connection — they just share common name tokens (e.g. "aks",
                # "helloworld") with the hiera variable.  Only proceed with the hiera
                # fallback if Pass 0 produced no SQL links at all.
                if _tf_workload_sql_linked:
                    targeted = []   # Pass 0 has authoritative SQL wiring; skip hiera
                if not targeted:
                    # Fallback: link to api-type workloads that aren't already wired
                    targeted = [
                        wl for wl in k8s_workloads
                        if wl['resource_name'] not in _tf_workload_sql_linked
                        and (wl.get('resource_type') == 'kubernetes_service'
                             or 'api' in wl['resource_name'].lower())
                    ]
                if not targeted:
                    targeted = [wl for wl in k8s_workloads
                                if wl['resource_name'] not in _tf_workload_sql_linked]
                for wl in targeted:
                    _add_edge(wl['resource_name'], sql_node)

        # ── Pass 2: Scan appsettings*.json ────────────────────────────────────
        # SQL: Link to existing SQL nodes (created in Pass 1) rather than creating
        # new key-name placeholder nodes — avoids duplication.
        for json_file in root.rglob('appsettings*.json'):
            if any(p in json_file.parts for p in _SKIP_DIRS):
                continue
            if 'test' in json_file.name.lower():
                continue
            try:
                data = _json.loads(json_file.read_text(encoding='utf-8', errors='replace'))
            except Exception:
                continue

            workload = _best_workload_for_dir(json_file.parent)
            if not workload:
                continue
            wl_name = workload['resource_name']
            rel_path = str(json_file.relative_to(root)).replace('\\', '/')

            conn_strings: dict = data.get('ConnectionStrings') or {}
            for key in conn_strings:
                k_lower = key.lower()
                if any(tok in k_lower for tok in ('sql', 'database', 'db', 'mssql', 'postgres')):
                    # Prefer an existing SQL node (created from hiera or Terraform).
                    # Only create a new node if no SQL resource exists at all.
                    existing_sql = _any_sql_node_exists()
                    if existing_sql:
                        _add_edge(wl_name, existing_sql)
                    else:
                        db_hint = re.sub(r'(connectionstring|connection)', '', k_lower,
                                         flags=re.IGNORECASE).strip('_- ') or 'sql-db'
                        sql_node = _ensure_sql_node(db_hint, '(appsettings key)', rel_path)
                        _add_edge(wl_name, sql_node)
                elif any(tok in k_lower for tok in ('servicebus', 'service_bus', 'sb', 'amqp')):
                    sb_node = _find_sb_node()
                    if sb_node:
                        _add_edge(wl_name, sb_node)

            # ApplicationInsights section — key existence (even empty value) is enough
            ai_section = data.get('ApplicationInsights')
            if isinstance(ai_section, dict) and 'ConnectionString' in ai_section:
                ai_node = _find_or_add_monitoring_node(rel_path)
                if ai_node:
                    _add_edge(wl_name, ai_node)

            # ServiceBus section with explicit queue/topic config
            sb_section = data.get('ServiceBus')
            if sb_section and isinstance(sb_section, dict):
                sb_node = _find_sb_node()
                if sb_node:
                    _add_edge(wl_name, sb_node)

        # ── Pass 3: Scan docker-compose.yml / *.env for connection strings ────
        _DOCKER_SQL_RE = re.compile(
            r'(?:ConnectionStrings__\w+|CONNECTIONSTRING\w*)\s*[=:]\s*'
            r'["\']?Server=([^;"\'\s,]+)[^;"\'\n]*Database=([^;"\'\n]+)',
            re.IGNORECASE,
        )
        for compose_file in list(root.rglob('docker-compose*.yml')) + list(root.rglob('*.env')):
            if any(p in compose_file.parts for p in {'.git', '__pycache__'}):
                continue
            try:
                text = compose_file.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            for m in _DOCKER_SQL_RE.finditer(text):
                server_hint, db_name = m.group(1).strip(), m.group(2).strip()
                rel_path = str(compose_file.relative_to(root)).replace('\\', '/')
                workload = _best_workload_for_dir(compose_file.parent)
                target_wls = [workload] if workload else k8s_workloads
                # Prefer existing SQL node or create from docker-compose value
                existing_sql = _any_sql_node_exists()
                if existing_sql:
                    for wl in target_wls:
                        _add_edge(wl['resource_name'], existing_sql)
                else:
                    sql_node = _ensure_sql_node(db_name, server_hint, rel_path)
                    for wl in target_wls:
                        _add_edge(wl['resource_name'], sql_node)

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

        # API gateways are not always internet entry points (APIM can be internal/VNet/private).
        # Only treat as public edge when we have explicit internet_access=true evidence.
        if self.is_api_gateway(resource) or self.is_api_operation(resource):
            props = resource.get('properties') or {}
            return str(props.get('internet_access') or '').strip().lower() == 'true'

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
    
    def is_monitoring(self, resource: dict) -> bool:
        """Check if resource is an observability/monitoring resource."""
        rtype = (resource.get('resource_type') or '').lower()
        return any(tok in rtype for tok in [
            'application_insights', 'appinsights',
            'log_analytics', 'loganalytics',
            'monitor', 'alert',
            'cloudwatch', 'stackdriver',
            'diagnostic',
        ])

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

    def is_compute_resource(self, resource: dict) -> bool:
        """Check if resource is a compute/VM resource that can have network interfaces as children."""
        rtype = (resource.get('resource_type') or '').lower()
        vm_tokens = ['virtual_machine', 'linux_virtual_machine', 'windows_virtual_machine', 
                     'instance', 'ec2', 'compute_instance']
        return any(tok in rtype for tok in vm_tokens)

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

    def is_test_placeholder_resource(self, resource: dict) -> bool:
        """Check if resource is a generic test/placeholder resource (e.g., 'example', 'default', 'test').
        
        These are typically created as minimal examples in test IaC and shouldn't clutter the diagram.
        """
        name = (resource.get('resource_name') or '').lower()
        # Generic placeholder names that indicate test/example resources
        placeholder_names = {'example', 'default', 'test', 'current', 'temp', 'tmp', 'demo', 'sample'}
        
        # Exact match placeholders
        if name in placeholder_names:
            return True
        
        # Names that are purely generic without meaningful context
        if name in {'a', 'b', 'c', 'value', 'resource', 'data', 'service', 'app', 'api', 'web'}:
            return True
        
        return False

    def _tokenize_match_name(self, value: str) -> List[str]:
        """Split a resource name into lowercase alphanumeric tokens for fuzzy matching."""
        return [tok for tok in re.split(r'[^a-z0-9]+', str(value or '').lower()) if tok]

    def _candidate_name_forms(self, value: str) -> List[str]:
        """Return progressively more generic normalized forms of a resource name."""
        tokens = self._tokenize_match_name(value)
        if not tokens:
            return []

        generic_suffixes = {'sql', 'api', 'svc', 'service', 'app', 'web', 'http', 'https'}
        forms: List[str] = []

        def _add_form(parts: List[str]) -> None:
            candidate = ''.join(parts)
            if candidate and candidate not in forms:
                forms.append(candidate)

        _add_form(tokens)

        trimmed = list(tokens)
        while trimmed and (trimmed[-1] in generic_suffixes or re.fullmatch(r'v\d+', trimmed[-1])):
            trimmed = trimmed[:-1]
        _add_form(trimmed)

        return forms

    def _match_rank_api_to_workload(self, api_name: str, workload_name: str, workload_type: str) -> Tuple[int, int, int, int, int]:
        """Score an API→workload match, preferring exact service names over broad prefixes."""
        service_tokens = self._tokenize_match_name(workload_name)
        if not service_tokens:
            return (0, 0, 0, 0, 0)

        service_joined = ''.join(service_tokens)
        best = (0, 0, 0, 0, 0)

        for candidate in self._candidate_name_forms(api_name):
            if not candidate:
                continue

            if candidate == service_joined:
                rank = 5
                extra_chars = 0
            else:
                rank = 0
                extra_chars = len(service_joined) - len(candidate)

                prefix_window = ''.join(service_tokens[:len(service_tokens)])
                if service_joined.startswith(candidate):
                    rank = 4
                else:
                    for start in range(len(service_tokens)):
                        for end in range(start + 1, len(service_tokens) + 1):
                            segment = ''.join(service_tokens[start:end])
                            if segment != candidate:
                                continue
                            rank = 3 if start > 0 else 4
                            break
                        if rank:
                            break

                if rank == 0 and candidate in service_joined:
                    rank = 2

                if rank == 0:
                    api_tokens = set(self._tokenize_match_name(api_name))
                    overlap = len(api_tokens & set(service_tokens))
                    if overlap:
                        score = (1, overlap, -len(service_tokens), 1 if workload_type == 'kubernetes_service' else 0, 1 if 'api' in workload_name.lower() else 0)
                        if score > best:
                            best = score
                    continue

            score = (
                rank,
                len(candidate),
                -max(extra_chars, 0),
                1 if workload_type == 'kubernetes_service' else 0,
                1 if 'api' in workload_name.lower() else 0,
            )
            if score > best:
                best = score

        return best

    def _ensure_synthetic_sql_server_node(self, server_name: Optional[str] = None) -> str:
        """Ensure an explicit SQL Server node exists for architecture dependency views."""
        fallback_name = 'SQL Server'
        candidate = str(server_name or '').strip()
        if self._score_sql_hint_value(candidate) < 2:
            candidate = fallback_name

        sql_node_name = candidate
        existing = self.resource_by_name.get(fallback_name)
        if existing and candidate != fallback_name:
            # existing may be a list of duplicates; pick the primary entry to rename
            existing_primary = existing[0] if isinstance(existing, list) else existing
            existing_primary['resource_name'] = candidate
            # Replace the key mapping (preserve list if it was a list)
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
        # Use assign helper to preserve any existing duplicates
        self._assign_resource_by_name(sql_node_name, synthetic_resource)
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
    
    def render_compute_hierarchy(self, compute_resources: List[dict]) -> List[str]:
        """Render VM/compute resources with their network interfaces nested as subgraphs."""
        if not compute_resources:
            return []
        
        lines: List[str] = []
        
        for vm in compute_resources:
            vm_name = vm['resource_name']
            vm_id = sanitize_id(vm_name)
            
            # Get child resources (NICs, disks, etc.)
            children = self.children_by_parent.get(vm['id'], [])
            
            if children:
                # Render VM as subgraph with children
                lines.append(f"  subgraph {vm_id}[\"{vm_name}\"]")
                for child in children:
                    child_name = child['resource_name']
                    child_node_id = sanitize_id(child_name)
                    lines.append(f"    {child_node_id}[\"{child_name}\"]")
                    self.emitted_nodes.add(child_name)
                lines.append("  end")
            else:
                # No children, render as regular node
                lines.append(self.render_node(vm))
            
            self.emitted_nodes.add(vm_name)
        
        return lines
    
    def render_node(self, resource: dict, indent: str = "  ") -> str:
        """Render a single node.

        When multiple resources share the same sanitized name (e.g. dozens of Azure
        resources all named "example"), a qualified node ID is generated using a short
        resource-type prefix so Mermaid does not collapse distinct nodes into one.
        The mapping is stored in node_id_override so render_connections uses the
        correct ID.
        """
        name = resource['resource_name']
        base_node_id = sanitize_id(name)

        # Detect ID collision: if this sanitized name was already emitted for a
        # *different* resource, qualify it with a resource-type prefix.
        if base_node_id in self._emitted_mermaid_ids and name not in self.emitted_nodes:
            # Build a short prefix from the resource type (strip provider prefix)
            rtype = resource.get('resource_type') or ''
            type_short = rtype.split('_', 2)[-1] if '_' in rtype else rtype
            node_id = sanitize_id(f"{type_short}_{name}")
            # If that's still a collision, append the DB id to guarantee uniqueness
            if node_id in self._emitted_mermaid_ids:
                node_id = sanitize_id(f"{rtype}_{name}_{resource.get('id', '')}")
            self.node_id_override[name] = node_id
        else:
            node_id = base_node_id

        # Truncate long labels to fit in box
        label = name if len(name) <= 50 else name[:47] + "..."

        self._emitted_mermaid_ids.add(node_id)
        self.emitted_nodes.add(name)
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

        When API operations are available (from DB or OpenAPI specs), each API is
        rendered as a named subgraph with its operations inside.  When no operations
        are available, products are shown as flat nodes.
        """
        if not apim_apis and not products:
            return []

        lines = []
        lines.append("  subgraph apim[API Management]")

        # Build a map: product_name → APIM API resource (so we can render ops per API)
        # Products and APIs typically share the same name in APIM Terraform.
        api_by_name: Dict[str, dict] = {
            a['resource_name']: a for a in apim_apis
        }
        # Also normalise with hyphen/underscore variants so drtestapp-sql matches drtestapp_sql.
        for a in apim_apis:
            normalised = a['resource_name'].replace('-', '_')
            if normalised not in api_by_name:
                api_by_name[normalised] = a

        # Products to render (always show all — even without connections)
        rendered_product_names: Set[str] = set()

        for product in products:
            pname = product['resource_name']
            if pname in rendered_product_names:
                continue

            # Try to find matching API to get operations.
            matched_api = api_by_name.get(pname)
            ops_for_product: List[dict] = []
            if matched_api:
                api_children = self.children_by_parent.get(matched_api['id'], [])
                ops_for_product = [c for c in api_children if self.is_api_operation(c)]
                # When API ops toggle is off, prune unconnected operations.
                if not self.include_api_operations:
                    ops_for_product = [op for op in ops_for_product
                                       if self._is_connected_name(op.get('resource_name', ''))]

            if ops_for_product:
                product_id = sanitize_id(pname)
                lines.append(f"    subgraph {product_id}[\"{pname}\"]")
                seen_op_ids: Set[str] = set()
                for op in ops_for_product:
                    op_id = sanitize_id(op.get('resource_name', ''))
                    if op_id in seen_op_ids:
                        continue
                    seen_op_ids.add(op_id)
                    lines.append(self.render_node(op, indent="      "))
                lines.append("    end")
                self.emitted_nodes.add(pname)
            else:
                lines.append(self.render_node(product, indent="    "))

            rendered_product_names.add(pname)

        # Render any APIM APIs that don't have a corresponding product node yet.
        for api in apim_apis:
            aname = api['resource_name']
            normalised = aname.replace('-', '_')
            if aname in rendered_product_names or normalised in rendered_product_names:
                # API was already rendered via its matching product — register both
                # name forms in emitted_nodes so render_connections can route edges.
                # Also map the hyphenated name to the subgraph ID (underscore form).
                self.emitted_nodes.add(aname)
                self.emitted_nodes.add(normalised)
                if aname != normalised:
                    self.node_id_override[aname] = sanitize_id(normalised)
                continue
            api_children = self.children_by_parent.get(api['id'], [])
            ops = [c for c in api_children if self.is_api_operation(c)]
            if not self.include_api_operations:
                ops = [op for op in ops if self._is_connected_name(op.get('resource_name', ''))]
            if ops:
                api_id = sanitize_id(aname)
                lines.append(f"    subgraph {api_id}[\"{aname}\"]")
                seen_op_ids = set()
                for op in ops:
                    op_id = sanitize_id(op.get('resource_name', ''))
                    if op_id in seen_op_ids:
                        continue
                    seen_op_ids.add(op_id)
                    lines.append(self.render_node(op, indent="      "))
                lines.append("    end")
                self.emitted_nodes.add(aname)
            else:
                lines.append(self.render_node(api, indent="    "))

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

        # Collect workloads via parent-child DB links (primary source).
        # Skaffold workloads are stored as children of the inferred cluster resource;
        # the k8s_resources list excludes them because they sit inside all_children.
        # Fetching from children_by_parent gives us all workloads regardless of that filter.
        workload_resources: List[dict] = []
        workload_ids_seen: Set[str] = set()
        for cr in cluster_resources:
            for child in self.children_by_parent.get(cr['id'], []):
                if child['id'] not in workload_ids_seen:
                    workload_ids_seen.add(child['id'])
                    workload_resources.append(child)

        # Fallback: non-cluster K8s resources that participate in known connections.
        # Skip catalog-info metadata types (kubernetes_component, kubernetes_api,
        # kubernetes_config) — these are service catalogue entries, not running pods.
        _CATALOG_TYPES = ('component', 'kubernetes_api', 'kubernetes_config')
        for r in k8s_resources:
            if r in cluster_resources or r['id'] in workload_ids_seen:
                continue
            if any(t in (r.get('resource_type') or '').lower() for t in _CATALOG_TYPES):
                continue
            if self._is_connected_name(r.get('resource_name', '')):
                workload_ids_seen.add(r['id'])
                workload_resources.append(r)

        if not workload_resources:
            return []

        # Build the outer subgraph label, incorporating cluster names when known.
        if cluster_resources:
            # Strip the __inferred__ prefix that the context extractor adds to synthesised names.
            cluster_display_names = [
                r['resource_name'].replace("__inferred__", "").lstrip("-")
                for r in cluster_resources
            ]
            cluster_names = ", ".join(cluster_display_names)
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
            # Use a k8s_wl_ prefix to avoid Mermaid node-ID collisions with
            # same-named resources in other subgraphs (e.g. APIM products).
            node_id = "k8s_wl_" + sanitize_id(res['resource_name'])
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

                node_id = "k8s_wl_" + sanitize_id(res['resource_name'])
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
                # Record the prefixed node ID so render_connections uses the right ID.
                self.node_id_override[res['resource_name']] = node_id

            lines.append("    end")

        lines.append("  end")
        return lines
    
    def render_service_bus(self, sb_resources: List[dict]) -> List[str]:
        """Render Service Bus with Topics/Queues/Subscriptions nested."""
        if not sb_resources:
            return []
        
        lines = []
        lines.append("  subgraph servicebus[Service Bus]")

        namespaces = []
        for r in sb_resources:
            rtype = (r.get('resource_type') or '').lower()
            name = (r.get('resource_name') or '').lower()
            if 'namespace' in rtype:
                namespaces.append(r)
                continue
            if any(name.endswith(suffix) for suffix in ('_service_bus', '-service-bus', ' service bus')):
                namespaces.append(r)
                continue
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

            # Determine connectivity FIRST so it can influence which children to show.
            namespace_connected = self._is_connected_name(namespace_name)

            topics_to_render = []
            for topic in topics:
                topic_subs = [
                    s for s in self.children_by_parent.get(topic['id'], [])
                    if self.is_service_bus_subscription(s)
                    # Include all subs when namespace is connected; else filter by name
                    and (namespace_connected or self._is_connected_name(s.get('resource_name', '')))
                ]
                # Show all topics when namespace is connected; else only named-connected ones
                if namespace_connected or self._is_connected_name(topic.get('resource_name', '')) or topic_subs:
                    topics_to_render.append(topic)

            # Show all queues when namespace is connected; else only named-connected ones
            queues_to_render = (
                queues if namespace_connected
                else [q for q in queues if self._is_connected_name(q.get('resource_name', ''))]
            )

            if not namespace_connected and not topics_to_render and not queues_to_render:
                continue

            namespace_subgraph_id = f"sb_ns_{namespace_id}"
            lines.append(f"    subgraph {namespace_subgraph_id}[\"🚌 {namespace_name}\"]")
            # Emit a plain namespace node only when it has no children to show.
            # When topics/queues are rendered, the subgraph title itself provides the label.
            should_render_namespace_node = (
                namespace_connected
                and not topics_to_render
                and not queues_to_render
            )
            if should_render_namespace_node:
                lines.append(f"      {namespace_id}[\"{namespace_name}\"]")
                self.emitted_nodes.add(namespace_name)
            else:
                # Register namespace name → subgraph ID so render_connections can route
                # edges (e.g. workload → dr_test_service_bus) to the namespace subgraph.
                self.node_id_override[namespace_name] = namespace_subgraph_id
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

    def render_monitoring(self, monitoring_resources: List[dict]) -> List[str]:
        """Render observability resources (App Insights, Log Analytics, monitors, alerts) as a subgraph.
        
        Only include resources that have incoming or outgoing arrows (connections).
        """
        if not monitoring_resources:
            return []

        # Build set of all connected resource names (excluding administrative edge types)
        SKIP_EDGE_TYPES = frozenset({
            'contains', 'grants_access_to', 'parent_of', 'child_of',
            'resource_group_member', 'has_role',
        })
        connected_resources = set()
        for conn in self.connections:
            if (conn.get('connection_type') or '').lower() not in SKIP_EDGE_TYPES:
                src = conn.get('source')
                tgt = conn.get('target')
                if src:
                    connected_resources.add(src)
                if tgt:
                    connected_resources.add(tgt)
        
        # Filter monitoring resources to only those with connections
        resources_to_render = [r for r in monitoring_resources 
                              if r['resource_name'] in connected_resources]
        
        if not resources_to_render:
            return []

        lines = ["  subgraph monitoring[Monitoring & Logging]"]
        for res in resources_to_render:
            node_id = sanitize_id(res['resource_name'])
            rtype = (res.get('resource_type') or '').lower()
            if 'application_insights' in rtype or 'appinsights' in rtype:
                icon = "📊"
            elif 'log_analytics' in rtype or 'loganalytics' in rtype:
                icon = "📋"
            elif 'alert' in rtype:
                icon = "🔔"
            elif 'action_group' in rtype:
                icon = "📣"
            else:
                icon = "📈"
            label = f"{icon} {res['resource_name']}"
            # Use render_node for consistent collision-aware ID generation
            node_line = self.render_node(res, indent="    ")
            # Replace the plain label with the icon-prefixed label
            lines.append(node_line.replace(f'["{res["resource_name"]}"]', f'["{label}"]', 1))
        lines.append("  end")
        return lines

    def render_connections(self) -> List[str]:
        """Render all connections with labels and line styles."""
        # Administrative/structural edge types that are captured in the DB but
        # should NOT be drawn as arrows — they are expressed via subgraph nesting.
        SKIP_EDGE_TYPES = frozenset({
            'contains', 'grants_access_to', 'parent_of', 'child_of',
            'resource_group_member', 'has_role',
        })

        lines = []
        lines.append("")

        has_internet = False

        for conn in self.connections:
            src = conn.get('source')
            tgt = conn.get('target')

            if not src or not tgt:
                continue

            # Skip administrative/structural edge types.
            if (conn.get('connection_type') or '').lower() in SKIP_EDGE_TYPES:
                continue

            # Never render self-referential edges (node -> same node); these add noise.
            if src == tgt or sanitize_id(src) == sanitize_id(tgt):
                continue

            # ── Special case: Internet → APIM subgraph ────────────────────────
            # Mermaid allows connecting to subgraph IDs directly.
            if tgt == '__apim_subgraph__':
                label = '🔒 unknown visibility'
                lines.append(f"  internet -.->|\"{label}\"| apim")
                has_internet = True
                continue

            # Skip if nodes weren't emitted
            if src != 'Internet' and src not in self.emitted_nodes:
                continue
            if tgt != 'Internet' and tgt not in self.emitted_nodes:
                continue
            
            src_id = self.node_id_override.get(src) or (sanitize_id(src) if src != 'Internet' else 'internet')
            tgt_id = self.node_id_override.get(tgt) or (sanitize_id(tgt) if tgt != 'Internet' else 'internet')
            if src == 'Internet':
                has_internet = True
            
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

        # Add red styling for unconfirmed connections (Internet connections)
        link_index = 0
        for i, conn in enumerate(self.connections):
            src = conn.get('source')
            tgt = conn.get('target')
            
            if not src or not tgt:
                continue
            if (conn.get('connection_type') or '').lower() in SKIP_EDGE_TYPES:
                continue
            if src == tgt or sanitize_id(src) == sanitize_id(tgt):
                continue
            if tgt == '__apim_subgraph__':
                link_index += 1
                continue
            if src != 'Internet' and src not in self.emitted_nodes:
                continue
            if tgt != 'Internet' and tgt not in self.emitted_nodes:
                continue
            
            # Color unconfirmed Internet connections red
            is_confirmed = conn.get('confirmed', True)
            if not is_confirmed and src == 'Internet':
                lines.append(f"  linkStyle {link_index} stroke:red,stroke-width:2px")
            
            link_index += 1

        # Prepend internet node definition if any Internet edges were emitted.
        if has_internet:
            lines.insert(0, '  internet[/"🌐 Internet"/]')

        return lines
    
    def infer_connections(self) -> bool:
        """Infer connections from resource relationships and properties when resource_connections is empty."""
        has_internet = False

        # If we already have connections from DB, do not drop them.
        # (Dropping 'depends_on' breaks k8s/messaging relationships and causes k8s nodes to be pruned.)
        # Only fall back to inference when there are effectively no connections.
        if len(self.connections) > 10:
            return any(str(c.get('source') or '') == 'Internet' for c in self.connections)
        
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
                    # Only emit unconfirmed Internet->X edges for non-APIM edge resources.
                    # APIM can be internal/private; rely on explicit internet_access=true signals.
                    if self.is_api_gateway(r) or self.is_api_operation(r):
                        continue
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
        
        # Listener → Service Bus (workload depends on the messaging service)
        k8s_deployments = [r for r in self.resources if self.is_kubernetes(r) and 'deployment' in r.get('resource_type', '').lower()]
        sb_queues = [r for r in self.resources if self.is_service_bus_queue(r)]
        sb_topics = [r for r in self.resources if self.is_service_bus_topic(r)]
        
        for deployment in k8s_deployments:
            dep_name = deployment['resource_name'].lower()
            # Include explicitly service-bus-oriented workloads even when they
            # are not named as listeners/workers.
            if any(kw in dep_name for kw in ['queue', 'listener', 'worker', 'consumer', 'servicebus']):
                for queue in sb_queues:
                    pair_key = (deployment['resource_name'], queue['resource_name'])
                    if pair_key not in connected_pairs:
                        self.connections.append({
                            'source': deployment['resource_name'],
                            'target': queue['resource_name'],
                            'connection_type': 'depends_on'
                        })
                        connected_pairs.add(pair_key)
                for topic in sb_topics:
                    pair_key = (deployment['resource_name'], topic['resource_name'])
                    if pair_key not in connected_pairs:
                        self.connections.append({
                            'source': deployment['resource_name'],
                            'target': topic['resource_name'],
                            'connection_type': 'depends_on'
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

            sql_resource = self._get_primary_resource(sql_node_name)
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
        
        # Add unconfirmed Internet → AKS cluster connections when visibility is unknown
        k8s_clusters = [r for r in self.resources if 'kubernetes_cluster' in (r.get('resource_type') or '').lower()]
        for cluster in k8s_clusters:
            cluster_name = cluster['resource_name']
            pair_key = ('Internet', cluster_name)
            
            # Only add if not already connected and if cluster doesn't have explicit internet_access=false
            if pair_key not in connected_pairs:
                has_explicit_private = False
                # Check if this cluster has an explicit private setting
                for conn in self.connections:
                    if conn.get('target') == cluster_name and (conn.get('connection_type') or '').lower() in ['vnet_only', 'private']:
                        has_explicit_private = True
                        break
                
                if not has_explicit_private:
                    self.connections.append({
                        'source': 'Internet',
                        'target': cluster_name,
                        'connection_type': 'unconfirmed_k8s_access',
                        'protocol': 'https',
                        'confirmed': False
                    })
                    connected_pairs.add(pair_key)
                    has_internet = True
        
        return has_internet
    
    def generate(self) -> str:
        """Generate the complete hierarchical diagram."""
        self.load_data()

        # Decide whether to include API operations (from DB or OpenAPI spec files).
        if self.include_api_operations is None:
            # Auto: show operations when there are few of them (avoids clutter on large APIs)
            # and APIM APIs are present.
            apim_count = sum(1 for r in self.resources if self.is_api_gateway(r))
            op_count = sum(1 for r in self.resources if self.is_api_operation(r))
            self.include_api_operations = apim_count > 0 and op_count < 10
        if self.include_api_operations:
            self._load_openapi_operations()

        # Infer runtime connections from application config files (appsettings, hiera, etc.).
        # This supplements Terraform-only topology with dependencies expressed as connection
        # strings, config keys, and environment variables.
        if self.repo_path:
            self._infer_from_config_files()
        
        if not self.resources:
            return "flowchart LR\n  empty[No resources found]"
        
        lines = ["flowchart LR"]
        
        # Infer connections if resource_connections table is empty/sparse
        if self.infer_connections():
            # Use a neutral client label; APIM isn't always internet-facing.
            lines.append("  internet[🖧 Network Client]")

        # Track resources that actually participate in at least one *visible* edge
        # (excluding administrative edge types that are not drawn as arrows) so we
        # can prune isolated boxes that add visual noise.
        _ADMIN_EDGE_TYPES = frozenset({
            'contains', 'grants_access_to', 'parent_of', 'child_of',
            'resource_group_member', 'has_role',
        })
        self.connected_resource_names = {
            n for c in self.connections
            if (c.get('connection_type') or '').lower() not in _ADMIN_EDGE_TYPES
            for n in (c.get('source'), c.get('target'))
            if n and n != 'Internet'
        }
        
        # Filter out children that will be rendered inside their parent subgraph.
        # Resource group membership is purely administrative — resources parented
        # to a resource group should still be treated as top-level diagram nodes.
        rg_ids = {
            r['id'] for r in self.resources
            if 'resource_group' in (r.get('resource_type') or '').lower()
        }
        all_children = set()
        for parent_id, children in self.children_by_parent.items():
            if parent_id in rg_ids:
                continue  # Resource group children are standalone nodes, not nested items.
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
        # Don't filter monitoring by all_children either - render as dedicated subgraph
        monitoring_resources = [r for r in self.resources if self.is_monitoring(r)
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

        # Collect monitoring IDs (handled by dedicated subgraph)
        monitoring_related_ids = {r['id'] for r in monitoring_resources}

        # ── Synthetic edges: monitoring structure (App Insights→alerts→action groups) ──
        # Only skip if a non-administrative (rendered) edge already exists for this pair.
        # DB may have `contains` edges for these which are suppressed in render_connections;
        # we need explicit `monitors`/`triggers` edges so arrows are actually drawn.
        _ADMIN_TYPES = frozenset({'contains', 'grants_access_to', 'parent_of', 'child_of', 'resource_group_member', 'has_role'})
        _mon_rendered = {(c.get('source'), c.get('target')) for c in self.connections
                         if (c.get('connection_type') or '').lower() not in _ADMIN_TYPES}
        _ai_mon  = [r for r in monitoring_resources if 'application_insights' in (r.get('resource_type') or '').lower() or 'log_analytics' in (r.get('resource_type') or '').lower()]
        _alrt_mon = [r for r in monitoring_resources if 'alert' in (r.get('resource_type') or '').lower()]
        _act_mon  = [r for r in monitoring_resources if 'action_group' in (r.get('resource_type') or '').lower()]
        for _ai in _ai_mon:
            for _al in _alrt_mon:
                if (_ai['resource_name'], _al['resource_name']) not in _mon_rendered:
                    self.connections.append({'source': _ai['resource_name'], 'target': _al['resource_name'], 'connection_type': 'monitors', 'confirmed': True})
        for _al in _alrt_mon:
            for _ac in _act_mon:
                if (_al['resource_name'], _ac['resource_name']) not in _mon_rendered:
                    self.connections.append({'source': _al['resource_name'], 'target': _ac['resource_name'], 'connection_type': 'triggers', 'confirmed': True})

        # ── Fallback APIM→K8s routing for APIs with no explicit routes_ingress_to ──
        _routed_apis = {c['source'] for c in self.connections if c.get('connection_type') == 'routes_ingress_to'}
        _k8s_svcs = [r for r in self.resources if r.get('resource_type') in ('kubernetes_service', 'kubernetes_deployment')]
        # Only route actual API resources (not policies, operations, products, etc.)
        _APIM_API_TYPES = {'azurerm_api_management_api', 'apim_api', 'api_management_api'}
        for _api_r in apim_apis:
            if self.is_api_operation(_api_r):
                continue
            if not any(t in ((_api_r.get('resource_type') or '').lower()) for t in ('api_management_api',) if 'policy' not in ((_api_r.get('resource_type') or '').lower()) and 'product' not in ((_api_r.get('resource_type') or '').lower())):
                continue
            if _api_r['resource_name'] in _routed_apis:
                continue
            _best = None
            _best_score = (0, 0, 0, 0, 0)
            for _svc in _k8s_svcs:
                _score = self._match_rank_api_to_workload(
                    _api_r['resource_name'],
                    _svc['resource_name'],
                    _svc.get('resource_type') or '',
                )
                if _score > _best_score:
                    _best_score, _best = _score, _svc
            if _best and _best_score[0] > 0:
                self.connections.append({
                    'source': _api_r['resource_name'],
                    'target': _best['resource_name'],
                    'connection_type': 'routes_ingress_to',
                    'confirmed': False,
                })

        # ── Internet/Client → APIM entry point (visibility unknown) ─────────────
        if apim_apis:
            _internet_targets = {c.get('target') for c in self.connections if c.get('source') == 'Internet'}
            if 'apim' not in _internet_targets:
                self.connections.append({
                    'source': 'Internet',
                    'target': '__apim_subgraph__',
                    'connection_type': 'inferred_entry',
                    'confirmed': False,
                    'notes': 'Visibility unknown — APIM may be internal or public',
                })
        
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

        # Render Monitoring / Logging
        monitoring_lines = self.render_monitoring(monitoring_resources)
        if monitoring_lines:
            lines.extend(monitoring_lines)
            lines.append("")
        
        # Render other resources not in above categories (exclude subscriptions which are metadata)
        connected_resource_names = set(self.connected_resource_names)

        # Collect compute/VM IDs
        compute_resources = [
            r for r in self.resources
            if self.is_compute_resource(r)
            and r['id'] not in all_children
            and r['id'] not in apim_related_ids
            and r['id'] not in sb_related_ids
            and r['id'] not in k8s_related_ids
            and r['id'] not in monitoring_related_ids
            and not r.get('resource_name', '').startswith('${var.')
            and not r.get('resource_name', '').startswith('${local.')
        ]
        compute_related_ids = {r['id'] for r in compute_resources}
        # Also add children of compute resources
        for vm in compute_resources:
            for child in self.children_by_parent.get(vm['id'], []):
                compute_related_ids.add(child['id'])

        sql_resources = [
            r for r in self.resources
            if self.is_database_resource(r)
            and r['id'] not in all_children
            and r['id'] not in apim_related_ids
            and r['id'] not in sb_related_ids
            and r['id'] not in k8s_related_ids
            and r['id'] not in monitoring_related_ids
            and r['id'] not in compute_related_ids
            and not r.get('resource_name', '').startswith('${var.')
            and not r.get('resource_name', '').startswith('${local.')
        ]

        # Render compute hierarchy
        compute_lines = self.render_compute_hierarchy(compute_resources)
        if compute_lines:
            lines.extend(compute_lines)
            lines.append("")

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
            and r['id'] not in monitoring_related_ids
            and r['id'] not in compute_related_ids
            and r not in sql_resources
            and not self.is_api_gateway(r)
            and not self.is_kubernetes(r)
            and not self.is_service_bus(r)
            and not self.is_monitoring(r)
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
            and not (
                self.is_test_placeholder_resource(r)
                and r.get('resource_name') not in connected_resource_names
            )  # Exclude test/placeholder resources unless they're connected
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
            # Skip inferred synthetic resources — they map to subgraph containers,
            # not individual Mermaid nodes, so the style would reference nothing.
            if resource_name.startswith('__inferred__'):
                continue
            
            resource = self._get_primary_resource(resource_name)
            if not resource:
                continue
            
            # Get category
            category = self._get_category(resource)

            # Monitoring nodes get type-specific colours rather than a flat grey.
            if category == 'Monitoring':
                rtype = (resource.get('resource_type') or '').lower()
                if 'application_insights' in rtype or 'log_analytics' in rtype or 'loganalytics' in rtype:
                    color = '#0078d4'   # Azure telemetry blue
                elif 'alert' in rtype:
                    color = '#e8a202'   # Amber — warning/threshold
                elif 'action_group' in rtype:
                    color = '#c50f1f'   # Red — action/notification
                else:
                    color = '#888888'   # Generic grey
            else:
                color = category_colors.get(category)
            
            if color:
                node_id = self.node_id_override.get(resource_name) or sanitize_id(resource_name)
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
    parser.add_argument("--provider", help="Filter to specific cloud provider")
    
    args = parser.parse_args()
    
    # Get list of providers to generate diagrams for
    providers_to_process = []
    if args.provider:
        providers_to_process = [args.provider]
    else:
        # Get all providers from database, excluding meta-providers like terraform/kubernetes
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
            from db_helpers import get_db_connection
            
            with get_db_connection() as conn:
                providers = conn.execute(
                    """SELECT DISTINCT provider FROM resources 
                       WHERE provider IS NOT NULL 
                         AND provider NOT IN ('unknown', 'terraform', 'kubernetes')
                       ORDER BY provider"""
                ).fetchall()
                providers_to_process = [p['provider'] for p in providers]
        except Exception as e:
            print(f"Warning: Could not detect providers: {e}", file=sys.stderr)
            providers_to_process = []
    
    if not providers_to_process:
        # Fallback to single diagram
        builder = HierarchicalDiagramBuilder(args.experiment_id, repo_name=args.repo)
        diagram = builder.generate()
        provider = builder.detect_cloud_provider()
        diagram_title = f"{provider} Architecture"
        diagrams = [(provider.lower(), diagram_title, diagram)]
    else:
        diagrams = []
        for provider in providers_to_process:
            # Create builder with provider filter
            builder = HierarchicalDiagramBuilder(
                args.experiment_id,
                repo_name=args.repo,
                provider_filter=provider
            )
            
            # Load data with provider filtering
            builder.load_data()
            
            if builder.resources:
                diagram = builder.generate()
                # Capitalize provider for display
                provider_map = {
                    'azure': 'Azure',
                    'aws': 'AWS',
                    'gcp': 'GCP',
                    'google': 'GCP',
                    'kubernetes': 'Kubernetes',
                    'terraform': 'Terraform',
                    'alicloud': 'Alicloud',
                    'oracle': 'Oracle',
                }
                provider_display = provider_map.get(provider.lower(), provider.title())
                diagram_title = f"{provider_display} Architecture"
                diagrams.append((provider.lower(), diagram_title, diagram))
    
    # Persist to database if requested
    if args.persist_db:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
            from db_helpers import get_db_connection
            
            with get_db_connection() as conn:
                display_order = 0
                for provider_key, diagram_title, diagram in diagrams:
                    # Check if diagram already exists
                    existing = conn.execute(
                        "SELECT id FROM cloud_diagrams WHERE experiment_id = ? AND provider = ?",
                        [args.experiment_id, provider_key]
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
                            [args.experiment_id, provider_key, diagram_title, diagram, display_order]
                        )
                    display_order += 1
                conn.commit()
                print(f"Persisted {len(diagrams)} diagram(s) to cloud_diagrams table")
                for _, diagram_title, _ in diagrams:
                    print(f"  - {diagram_title}")
        except Exception as e:
            print(f"Warning: Failed to persist diagram to DB: {e}", file=sys.stderr)
    
    if args.output:
        # If single output file, write first diagram
        if len(diagrams) == 1:
            args.output.write_text(diagrams[0][2])
            print(f"Diagram written to {args.output}")
        else:
            # Write diagrams to separate files with provider suffix
            for provider_key, _, diagram in diagrams:
                output_path = args.output.parent / f"{args.output.stem}_{provider_key}{args.output.suffix}"
                output_path.write_text(diagram)
                print(f"Diagram written to {output_path}")
    else:
        for _, diagram_title, diagram in diagrams:
            print(f"\n{'='*60}")
            print(f"{diagram_title}")
            print(f"{'='*60}\n")
            print(diagram)


if __name__ == "__main__":
    main()
