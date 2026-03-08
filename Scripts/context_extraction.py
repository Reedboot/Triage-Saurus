# context_extraction.py
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple

from models import Resource, Connection, Relationship, RelationshipType, RepositoryContext


# ---------------------------------------------------------------------------
# Endpoint → (resource_type, name_group_index) patterns
# name_group_index: which regex capture group holds the resource canonical name
# ---------------------------------------------------------------------------
_ENDPOINT_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    # Azure SQL / MSSQL  "Server=tycho-sql.database.windows.net"
    (re.compile(r'(?:Server|Data Source)\s*=\s*([\w\-]+)\.database\.windows\.net', re.I),
     "azurerm_mssql_server", 1),
    # Azure Blob Storage  "https://labpallas.blob.core.windows.net"
    (re.compile(r'https?://([\w\-]+)\.blob\.core\.windows\.net', re.I),
     "azurerm_storage_account", 1),
    # Azure Storage connection string  "AccountName=labpallas"
    (re.compile(r'AccountName=([\w\-]+)', re.I),
     "azurerm_storage_account", 1),
    # Azure Key Vault  "https://ganymede-kv.vault.azure.net"
    (re.compile(r'https?://([\w\-]+)\.vault\.azure\.net', re.I),
     "azurerm_key_vault", 1),
    # Azure Service Bus  "Endpoint=sb://mybus.servicebus.windows.net"
    (re.compile(r'(?:Endpoint=sb|amqps)://([\w\-]+)\.servicebus\.windows\.net', re.I),
     "azurerm_servicebus_namespace", 1),
    # Azure Redis Cache  "myredis.redis.cache.windows.net"
    (re.compile(r'([\w\-]+)\.redis\.cache\.windows\.net', re.I),
     "azurerm_redis_cache", 1),
    # Azure APIM  "https://myapim.azure-api.net"
    (re.compile(r'https?://([\w\-]+)\.azure\-api\.net', re.I),
     "azurerm_api_management", 1),
    # Azure App Service / Function  "https://myapp.azurewebsites.net"
    (re.compile(r'https?://([\w\-]+)\.azurewebsites\.net', re.I),
     "azurerm_linux_web_app", 1),
    # Azure Cosmos DB  "AccountEndpoint=https://mycosmos.documents.azure.com"
    (re.compile(r'https?://([\w\-]+)\.documents\.azure\.com', re.I),
     "azurerm_cosmosdb_account", 1),
    # Azure Event Hub (shares servicebus namespace)
    (re.compile(r'([\w\-]+)\.servicebus\.windows\.net', re.I),
     "azurerm_eventhub_namespace", 1),
    # AWS RDS  "mydb.abcdef.us-east-1.rds.amazonaws.com"
    (re.compile(r'([\w\-]+)\.\w+\.[\w\-]+\.rds\.amazonaws\.com', re.I),
     "aws_db_instance", 1),
    # AWS S3 path-style  "s3.amazonaws.com/mybucket"
    (re.compile(r's3\.amazonaws\.com/([\w\-\.]+)', re.I),
     "aws_s3_bucket", 1),
    # AWS S3 virtual-hosted  "mybucket.s3.amazonaws.com" / "mybucket.s3.us-east-1.amazonaws.com"
    (re.compile(r'([\w\-]+)\.s3(?:\.[\w\-]+)?\.amazonaws\.com', re.I),
     "aws_s3_bucket", 1),
    # GCP Cloud SQL  "/<project>:<region>:<instance>"  (socket path pattern)
    (re.compile(r'(?:/cloudsql/|socketPath.*?)([\w\-]+:[\w\-]+:[\w\-]+)', re.I),
     "google_sql_database_instance", 1),
]

# Files worth scanning for connection strings (cheapest signal, checked first)
_CONN_STRING_GLOBS = [
    "appsettings*.json",
    "appsettings*.yml",
    "appsettings*.yaml",
    ".env",
    ".env.*",
    "web.config",
    "app.config",
    "*.env",
    "values.yaml",
    "values*.yaml",
    "**/configmap*.yaml",
    "**/configmap*.yml",
    "**/secret*.yaml",
]
# Code files scanned as lower-priority fallback
_CODE_GLOBS = ["*.cs", "*.py", "*.js", "*.ts", "*.go", "*.java"]
# Directories to skip
_SKIP_DIRS = {".git", "node_modules", ".terraform", "__pycache__", "bin", "obj", "dist", "build"}


def extract_connection_string_dependencies(
    repo_path: Path,
    context: RepositoryContext,
) -> None:
    """
    Scan config files and code for endpoint patterns that imply a runtime
    dependency on a cloud resource.  Appends inferred Relationship objects
    (depends_on, confidence='inferred') to *context.relationships* and
    creates synthetic Resource entries for each discovered external target
    (so they appear in resource_nodes after persist_graph runs).
    """
    repo_name = context.repository_name

    # Collect candidate files
    def _walk(globs: list[str]) -> list[Path]:
        seen: set[Path] = set()
        for pattern in globs:
            for p in repo_path.glob(f"**/{pattern}"):
                if not any(part in _SKIP_DIRS for part in p.parts):
                    if p not in seen:
                        seen.add(p)
                        yield p

    already_found: set[tuple[str, str]] = set()  # (resource_type, canonical_name)

    def _scan_text(text: str, source_file: str) -> None:
        for pattern, rtype, grp in _ENDPOINT_PATTERNS:
            for m in pattern.finditer(text):
                canonical_name = m.group(grp).lower().rstrip("/")
                key = (rtype, canonical_name)
                if key in already_found:
                    continue
                already_found.add(key)

                # Add a synthetic inferred resource so it gets a node in the graph
                synth = Resource(
                    name=f"__inferred__{canonical_name}",
                    resource_type=rtype,
                    file_path=source_file,
                    line_number=text[:m.start()].count("\n") + 1,
                    properties={"inferred": "true", "canonical_name": canonical_name},
                )
                context.resources.append(synth)

                # Emit a depends_on from the repo itself (represented as a
                # placeholder resource) to the inferred external resource
                context.relationships.append(Relationship(
                    source_type="repository",
                    source_name=repo_name,
                    target_type=rtype,
                    target_name=f"__inferred__{canonical_name}",
                    relationship_type=RelationshipType.DEPENDS_ON,
                    source_repo=repo_name,
                    confidence="inferred",
                    notes=(
                        f"Connection string in {source_file} references "
                        f"{canonical_name} ({rtype.replace('azurerm_','').replace('aws_','').replace('_',' ').title()})"
                    ),
                ))

    # Priority 1 — dedicated config files
    for f in _walk(_CONN_STRING_GLOBS):
        try:
            _scan_text(f.read_text(errors="ignore"),
                       str(f.relative_to(repo_path)))
        except Exception:
            continue

    # Priority 2 — source code (only if not already a pure IaC repo)
    has_tf = any(repo_path.rglob("*.tf"))
    if not has_tf:
        for f in _walk(_CODE_GLOBS):
            try:
                _scan_text(f.read_text(errors="ignore"),
                           str(f.relative_to(repo_path)))
            except Exception:
                continue


def iter_files(repo_path: Path) -> List[Path]:
    """Iterate over files in the repository, excluding certain directories."""
    # This is a simplified version of the original iter_files.
    # In a real implementation, you'd want to handle .gitignore, etc.
    return list(repo_path.glob("**/*"))

def extract_resource_names(files: List[Path], repo_path: Path, resource_type: str) -> List[str]:
    """Extract resource names of a given type from Terraform files."""
    names = []
    for file in files:
        if file.suffix == ".tf":
            try:
                content = file.read_text()
                matches = re.findall(rf'resource "?{resource_type}"? "([^"]+)"', content)
                names.extend(matches)
            except Exception:
                continue
    return names

def extract_resource_names_with_property(files: List[Path], repo_path: Path, resource_type: str, property_name: str) -> List[str]:
    """Extract resource names that have a specific property."""
    # Simplified for brevity
    return extract_resource_names(files, repo_path, resource_type)


def detect_terraform_resources(files: List[Path], repo_path: Path) -> Set[str]:
    """Detect all Terraform resource types in the repository."""
    resource_types = set()
    for file in files:
        if file.suffix == ".tf":
            try:
                content = file.read_text()
                matches = re.findall(r'resource "?([A-Za-z_][A-Za-z0-9_]*)"?', content)
                resource_types.update(matches)
            except Exception:
                continue
    return resource_types


def detect_arm_resources(files: List[Path], repo_path: Path) -> dict:
    """Detect Azure ARM template resources and extract basic properties.

    Returns a dict with keys:
      - resource_types: set of resource type strings (e.g., 'Microsoft.Storage/storageAccounts')
      - resources: list of dicts {"type": type, "name": name, "properties": {...}, "file": path}

    This is best-effort and tolerant of non-JSON files; it will skip files that cannot
    be parsed as JSON and fall back to regex searches for '"type"' entries.
    """
    types: set = set()
    resources: list = []
    for f in files:
        if f.suffix.lower() != ".json":
            continue
        try:
            text = f.read_text(errors="ignore")
            # Try structured parse first
            parsed = None
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None

            if isinstance(parsed, dict) and "resources" in parsed and isinstance(parsed["resources"], list):
                for res in parsed["resources"]:
                    rtype = res.get("type")
                    name = res.get("name") or res.get("name", "")
                    props = res.get("properties", {}) if isinstance(res.get("properties", {}), dict) else {}
                    if rtype:
                        types.add(rtype)
                        resources.append({"type": rtype, "name": name, "properties": props, "file": str(f)})
            else:
                # Heuristic regex: find occurrences of "type": "Microsoft.X/Y"
                for m in re.finditer(r'"type"\s*:\s*"([A-Za-z0-9._\-/]+)"', text):
                    rtype = m.group(1)
                    types.add(rtype)
                    resources.append({"type": rtype, "name": None, "properties": {}, "file": str(f)})

            # Property heuristics: look for public access / SAS tokens
            if re.search(r'"allowBlobPublicAccess"\s*:\s*true', text, re.I) or re.search(r'"publicAccess"\s*:\s*"(Blob|Container)"', text, re.I):
                types.add("Microsoft.Storage/storageAccounts")
            if re.search(r'sharedAccessSignature|sasToken|sharedAccessSignature', text, re.I):
                # Record presence as heuristic
                types.add("sas_token_detected")

            # SQL firewall wildcard detection
            if re.search(r'"startIpAddress"\s*:\s*"0\.0\.0\.0"', text):
                types.add("mssql_firewall_allow_all")
        except Exception:
            continue

    return {"resource_types": types, "resources": resources}


def detect_bicep_resources(files: List[Path], repo_path: Path) -> dict:
    """Detect resource declarations in Bicep files and extract simple properties.

    Returns same schema as detect_arm_resources.
    """
    types: set = set()
    resources: list = []
    bicep_res_re = re.compile(r"^\s*resource\s+([A-Za-z0-9_]+)\s+'([^']+)@[^']+'\s*=\s*\{", re.I | re.M)
    for f in files:
        if f.suffix.lower() != ".bicep":
            continue
        try:
            text = f.read_text(errors="ignore")
            for m in bicep_res_re.finditer(text):
                rtype = m.group(2)
                types.add(rtype)
                resources.append({"type": rtype, "name": None, "properties": {}, "file": str(f)})

            # Heuristics for public access and SAS
            if re.search(r'allowBlobPublicAccess|publicAccess', text, re.I):
                types.add("Microsoft.Storage/storageAccounts")
            if re.search(r'sharedAccessSignature|sasToken|sharedAccessSignature', text, re.I):
                types.add("sas_token_detected")

            # SQL firewall heuristic
            if re.search(r"startIpAddress\s*:\s*'0.0.0.0'|startIpAddress\s*=\s*'0.0.0.0'", text, re.I):
                types.add("mssql_firewall_allow_all")
        except Exception:
            continue
    return {"resource_types": types, "resources": resources}

def detect_hosting_from_terraform(files: List[Path], repo_path: Path) -> Dict:
    """Detect hosting platform from Terraform files."""
    # Simplified for brevity
    return {"type": "Unknown", "evidence": []}


def detect_ingress_from_code(files: List[Path], repo_path: Path) -> Dict:
    """Detect ingress points from code."""
    # Simplified for brevity
    return {"type": "Unknown", "evidence": []}


def detect_apim_backend_services(files: List[Path], repo_path: Path) -> Dict:
    """Detect APIM backend services."""
    # Simplified for brevity
    return {"auth_service": None, "backends": []}

def detect_external_dependencies(files: List[Path], repo_path: Path) -> Dict:
    """Detect external dependencies."""
    # Simplified for brevity
    return {"databases": [], "storage": [], "external_apis": []}

def detect_authentication_methods(files: List[Path], repo_path: Path) -> Dict:
    """Detect authentication methods."""
    # Simplified for brevity
    return {"methods": [], "details": []}

def detect_network_topology(files: List[Path], repo_path: Path) -> Dict:
    """Detect network topology."""
    # Simplified for brevity
    return {"vnets": [], "nsgs": [], "private_endpoints": []}

def has_terraform_module_source(files: List[Path], pattern: str) -> bool:
    """Check if any Terraform file uses a module with a given source pattern."""
    # Simplified for brevity
    return False

def classify_terraform_resources(resource_types: Set[str], provider: str) -> Dict:
    """Classify Terraform resources by category."""
    # Simplified for brevity
    return {}

def extract_kubernetes_topology_signals(files: List[Path], repo_path: Path, prefixes: List[str] = None) -> Dict:
    """Extract Kubernetes topology signals from manifests."""
    # Simplified for brevity
    return {
        "ingress_names": [],
        "service_names": [],
        "manifest_secret_names": [],
        "ingress_classes": [],
        "controller_hints": [],
        "lb_hints": [],
        "evidence_files": [],
    }

def extract_vm_names_with_os(files: List[Path], repo_path: Path, provider: str) -> List[Tuple[str, str, str]]:
    """Extract VM names with OS and role."""
    # Simplified for brevity
    return []

def extract_nsg_associations(files: list[Path], repo: Path, provider: str, vm_names: list[tuple[str, str, str]]) -> dict[str, bool]:
    """Detect which resources have NSG/Security Group associations."""
    # Simplified for brevity
    return {}

def detect_terraform_backend(files: list[Path], repo: Path) -> dict[str, str]:
    """Detect Terraform backend configuration."""
    # Simplified for brevity
    return {"type": "local", "storage_resource": None}

def _find_resource_location(files: list[Path], repo: Path, resource_name: str, resource_type: str) -> tuple[str, int, int]:
    """Find the source file and line numbers for a resource."""
    # Simplified for brevity
    return (None, None, None)

def _extract_paas_resources(files: list[Path], repo: Path, provider: str) -> dict[str, list[dict]]:
    """Extract PaaS resources and exposure flags."""
    # Simplified for brevity
    return {
        "app_services": [],
        "sql_databases": [],
        "key_vaults": [],
        "storage_accounts": [],
    }

def _detect_vm_paas_connections(files: list[Path], repo: Path, resource_name: str) -> dict[str, list[str]]:
    """Detect per-VM connections to PaaS and mark service protection flags."""
    # Simplified for brevity
    return {
        "key_vaults": [],
        "storage_accounts": [],
        "sql_databases": [],
    }

def _analyze_nsg_rules(files: list[Path], repo: Path, vm_name: str) -> list[str]:
    """Analyze NSG rules for overly permissive configurations."""
    # Simplified for brevity
    return []

def _extract_nsg_allowed_protocols(files: list[Path], repo: Path) -> str:
    """Extract allowed protocols/ports from NSG rules and return a summary label."""
    # Simplified for brevity
    return ""

def _extract_service_accounts(files: list[Path], repo: Path, provider: str) -> dict[str, dict]:
    """Extract service accounts, service principals, and managed identities with their permissions."""
    # Simplified for brevity
    return {}

def _load_parent_type_map() -> Dict[str, str]:
    """Load type-level parent relationships from resource_types DB (child_type → parent_type)."""
    try:
        import resource_type_db as rtdb
        from pathlib import Path as _Path
        db_path = _Path(__file__).parent.parent / "Output" / "Learning" / "triage.db"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT terraform_type, parent_type FROM resource_types WHERE parent_type IS NOT NULL"
        ).fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def extract_context(repo_path_str: str) -> RepositoryContext:
    """
    Extracts context from the repository and returns a data model.
    """
    repo_path = Path(repo_path_str)
    files = iter_files(repo_path)
    repo_name = repo_path.name
    context = RepositoryContext(repository_name=repo_name)

    # Parse Terraform resource + data blocks with source location.
    block_re = re.compile(r'^\s*(resource|data)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s+"([^"]+)"')
    # Detect attribute references to other resources: type.name.attr
    ref_re = re.compile(r'\b([a-z][a-z0-9_]+\.[a-z][a-z0-9_\-]+)\.[a-z_]+\b')

    # First pass: collect all resources and build a lookup map
    resource_blocks: list[tuple[Resource, str]] = []  # (resource, raw_block_text)
    for file in sorted(f for f in files if f.suffix == ".tf"):
        try:
            rel = str(file.relative_to(repo_path))
        except ValueError:
            rel = str(file)
        try:
            content = file.read_text(errors="ignore")
        except Exception:
            continue
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            m = block_re.match(lines[i])
            if m:
                block_kind, resource_type, name = m.groups()
                # Collect block body until matching closing brace
                depth = 0
                block_lines = []
                for j in range(i, min(i + 80, len(lines))):
                    block_lines.append(lines[j])
                    depth += lines[j].count("{") - lines[j].count("}")
                    if j > i and depth <= 0:
                        break
                block_text = "\n".join(block_lines)
                resource = Resource(
                    name=name,
                    resource_type=resource_type,
                    file_path=rel,
                    line_number=i + 1,
                    properties={"terraform_block": block_kind},
                )
                resource_blocks.append((resource, block_text))
                context.resources.append(resource)
            i += 1

    # Build lookup: "type.name" → resource
    res_lookup: Dict[str, Resource] = {
        f"{r.resource_type}.{r.name}": r for r, _ in resource_blocks
    }

    # Load type-level parent map as fallback
    parent_type_map = _load_parent_type_map()

    # Second pass: resolve parent references
    for resource, block_text in resource_blocks:
        if resource.parent:
            continue
        # Try explicit TF reference first
        for ref_match in ref_re.finditer(block_text):
            ref_key = ref_match.group(1)
            if ref_key in res_lookup and res_lookup[ref_key] is not resource:
                resource.parent = ref_key  # "type.name"
                break
        # Fall back to type-level map
        if not resource.parent and resource.resource_type in parent_type_map:
            parent_tf_type = parent_type_map[resource.resource_type]
            # Find first resource of that type in the same file
            for candidate, _ in resource_blocks:
                if candidate.resource_type == parent_tf_type and candidate.file_path == resource.file_path:
                    resource.parent = f"{candidate.resource_type}.{candidate.name}"
                    break
            # Widen search to whole repo if not found in same file
            if not resource.parent:
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == parent_tf_type:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break

    # -------------------------------------------------------------------------
    # Third pass: emit typed Relationship objects from block attributes
    # -------------------------------------------------------------------------

    # Patterns that signal specific relationship types within a block:
    #   (attr_regex, relationship_type, swap_direction)
    # swap_direction=True means target→source (block resource IS the target)
    _ATTR_RELATIONSHIP_PATTERNS = [
        # contains — resource references a parent by ID/name attribute
        (re.compile(r'(?:storage_account_name|server_name|vault_name|cluster_name|account_name)\s*=\s*(?:azurerm_[a-z_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.CONTAINS, True),

        # depends_on — explicit TF depends_on block
        (re.compile(r'depends_on\s*=\s*\[([^\]]+)\]', re.S),
         RelationshipType.DEPENDS_ON, False),

        # encrypts — key_vault_key_id attribute points at a KV key
        (re.compile(r'key_vault_key_id\s*=\s*(?:azurerm_key_vault_key)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.ENCRYPTS, False),

        # restricts_access — VNet rule / private endpoint targets
        (re.compile(r'(?:virtual_network_subnet_id|subnet_id)\s*=\s*(?:azurerm_subnet)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.RESTRICTS_ACCESS, False),

        # monitors — diagnostic setting source_resource_id
        (re.compile(r'target_resource_id\s*=\s*(?:azurerm_[a-z_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.MONITORS, False),

        # routes_ingress_to — backend_address_pool / backend pointing at a resource
        (re.compile(r'backend_[a-z_]*address[a-z_]*\s*=\s*(?:azurerm_[a-z_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.ROUTES_INGRESS_TO, False),

        # grants_access_to — role_assignment scope points at a resource
        (re.compile(r'scope\s*=\s*(?:azurerm_[a-z_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.GRANTS_ACCESS_TO, False),

        # authenticates_via — managed identity / identity block reference
        (re.compile(r'(?:identity_ids|user_assigned_identity_id)\s*=\s*\[?\s*(?:azurerm_user_assigned_identity)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.AUTHENTICATES_VIA, False),
    ]

    # Regex to extract type from a reference string like "azurerm_foo.bar.id"
    _full_ref_re = re.compile(r'\b(azurerm_[a-z_]+)\.([a-z][a-z0-9_\-]+)\.')
    # Regex to detect variable references (gap candidates)
    _var_re = re.compile(r'\bvar\.([a-z][a-z0-9_\-]+)\b')

    for resource, block_text in resource_blocks:
        rtype = resource.resource_type
        rname = resource.name

        # --- contains: derive from resolved parent reference ---
        if resource.parent:
            parts = resource.parent.split(".", 1)
            if len(parts) == 2:
                p_type, p_name = parts
                context.relationships.append(Relationship(
                    source_type=p_type,
                    source_name=p_name,
                    target_type=rtype,
                    target_name=rname,
                    relationship_type=RelationshipType.CONTAINS,
                    source_repo=repo_name,
                    confidence="extracted",
                ))

        # --- attribute-pattern relationships ---
        for pattern, rel_type, swap in _ATTR_RELATIONSHIP_PATTERNS:
            if rel_type == RelationshipType.DEPENDS_ON:
                # depends_on lists refs like [azurerm_foo.bar, azurerm_baz.qux]
                m = pattern.search(block_text)
                if m:
                    for ref_m in _full_ref_re.finditer(m.group(1)):
                        t_type, t_name = ref_m.group(1), ref_m.group(2)
                        if t_type != rtype or t_name != rname:
                            context.relationships.append(Relationship(
                                source_type=rtype, source_name=rname,
                                target_type=t_type, target_name=t_name,
                                relationship_type=rel_type,
                                source_repo=repo_name, confidence="extracted",
                            ))
            else:
                for m in pattern.finditer(block_text):
                    ref_name = m.group(1)
                    # Find the matching resource type from the full block text
                    full_m = _full_ref_re.search(block_text[max(0, m.start()-5):m.end()+30])
                    ref_type = full_m.group(1) if full_m else "unknown"
                    if swap:
                        src_type, src_name, tgt_type, tgt_name = ref_type, ref_name, rtype, rname
                    else:
                        src_type, src_name, tgt_type, tgt_name = rtype, rname, ref_type, ref_name
                    if src_name != tgt_name or src_type != tgt_type:
                        context.relationships.append(Relationship(
                            source_type=src_type, source_name=src_name,
                            target_type=tgt_type, target_name=tgt_name,
                            relationship_type=rel_type,
                            source_repo=repo_name, confidence="extracted",
                        ))

        # --- enrichment gaps: variable references in security-relevant attrs ---
        _SENSITIVE_ATTRS = re.compile(
            r'(?:key_vault_key_id|scope|principal_id|storage_account_name|server_name'
            r'|backend_address|subnet_id|target_resource_id)\s*=\s*(var\.[a-z][a-z0-9_\-]+)',
            re.I,
        )
        for gap_m in _SENSITIVE_ATTRS.finditer(block_text):
            var_ref = gap_m.group(1)
            context.relationships.append(Relationship(
                source_type=rtype, source_name=rname,
                target_type="unknown", target_name=var_ref,
                relationship_type=RelationshipType.DEPENDS_ON,
                source_repo=repo_name, confidence="inferred",
                notes=f"Variable reference — actual target unknown: {var_ref}",
            ))

    # -------------------------------------------------------------------------
    # Fourth pass: scan config/code files for connection string dependencies
    # -------------------------------------------------------------------------
    extract_connection_string_dependencies(repo_path, context)

    return context
