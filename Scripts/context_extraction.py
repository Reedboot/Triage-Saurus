# context_extraction.py
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

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
    for existing in context.resources:
        canonical = (existing.properties or {}).get("canonical_name")
        if canonical:
            already_found.add((existing.resource_type, str(canonical).lower().rstrip("/")))

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
    ingress_signals = {
        "type": "Unknown",
        "edge_resources": [],
        "hosts": [],
        "evidence": [],
    }

    edge_resource_tokens = (
        "api_gateway",
        "api_management",
        "application_gateway",
        "front_door",
        "load_balancer",
        "ingress",
        "cloudfront",
        "gateway",
    )
    ingress_code_patterns: list[tuple[re.Pattern, str]] = [
        (re.compile(r'app\.run\([^)]*host\s*=\s*["\']0\.0\.0\.0["\']', re.I), "Flask host=0.0.0.0"),
        (re.compile(r'listen\s*\([^)]*(?:0\.0\.0\.0|\*)', re.I), "Server listens on wildcard/public interface"),
        (re.compile(r'UseUrls\([^)]*(?:http://\*|https://\*|0\.0\.0\.0)', re.I), ".NET UseUrls wildcard binding"),
        (re.compile(r'ASPNETCORE_URLS\s*=\s*["\'][^"\']*(?:http://\+|http://0\.0\.0\.0)', re.I), "ASPNETCORE_URLS wildcard binding"),
        (re.compile(r'kind:\s*Ingress\b', re.I), "Kubernetes Ingress manifest"),
    ]

    seen_resources: set[str] = set()
    seen_hosts: set[str] = set()
    seen_evidence: set[str] = set()
    candidate_suffixes = {".tf", ".py", ".js", ".ts", ".cs", ".go", ".java", ".yaml", ".yml", ".json", ".xml", ".config"}

    for file in files:
        if not file.is_file():
            continue
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        if file.suffix.lower() not in candidate_suffixes and file.name not in {"Dockerfile", "web.config", "app.config"}:
            continue
        try:
            rel = str(file.relative_to(repo_path))
        except ValueError:
            rel = str(file)

        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue

        if file.suffix.lower() == ".tf":
            for m in re.finditer(r'^\s*resource\s+"([A-Za-z_][A-Za-z0-9_]*)"\s+"([^"]+)"', text, re.M):
                rtype, rname = m.group(1), m.group(2)
                if any(token in rtype.lower() for token in edge_resource_tokens):
                    identity = f"{rtype}.{rname}"
                    if identity not in seen_resources:
                        seen_resources.add(identity)
                        ingress_signals["edge_resources"].append(identity)
                    evidence = f"{rel}:{text[:m.start()].count(chr(10)) + 1} ({identity})"
                    if evidence not in seen_evidence:
                        seen_evidence.add(evidence)
                        ingress_signals["evidence"].append(evidence)

            for host_m in re.finditer(r'(?i)\b(?:host_name|hostname|host|fqdn|domain_name)\s*=\s*"([^"]+)"', text):
                host = host_m.group(1).strip().lower()
                if host and "{{" not in host and host not in seen_hosts:
                    seen_hosts.add(host)
                    ingress_signals["hosts"].append(host)

        for pattern, label in ingress_code_patterns:
            match = pattern.search(text)
            if not match:
                continue
            evidence = f"{rel}:{text[:match.start()].count(chr(10)) + 1} ({label})"
            if evidence not in seen_evidence:
                seen_evidence.add(evidence)
                ingress_signals["evidence"].append(evidence)

    if ingress_signals["edge_resources"]:
        ingress_signals["type"] = "IaC"
    elif ingress_signals["evidence"]:
        ingress_signals["type"] = "Code"
    return ingress_signals


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
    topology = {
        "vnets": [],
        "nsgs": [],
        "private_endpoints": [],
        "edge_gateways": [],
        "evidence": [],
    }

    categories = {
        "vnets": ("virtual_network", "aws_vpc", "compute_network"),
        "nsgs": ("network_security_group", "security_group", "compute_firewall"),
        "private_endpoints": ("private_endpoint", "vpc_endpoint", "private_service_connection"),
        "edge_gateways": ("api_gateway", "api_management", "application_gateway", "front_door", "load_balancer", "ingress"),
    }

    for file in files:
        if not file.is_file() or file.suffix.lower() != ".tf":
            continue
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        try:
            rel = str(file.relative_to(repo_path))
        except ValueError:
            rel = str(file)
        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue

        for m in re.finditer(r'^\s*resource\s+"([A-Za-z_][A-Za-z0-9_]*)"\s+"([^"]+)"', text, re.M):
            rtype, rname = m.group(1), m.group(2)
            lowered = rtype.lower()
            for key, hints in categories.items():
                if any(h in lowered for h in hints):
                    identity = f"{rtype}.{rname}"
                    if identity not in topology[key]:
                        topology[key].append(identity)
                        line_no = text[:m.start()].count("\n") + 1
                        topology["evidence"].append(f"{rel}:{line_no} ({identity})")
                    break

    return topology

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
    prefixes = prefixes or []
    prefix_filters = tuple(p.lower() for p in prefixes if p)

    signals = {
        "ingress_names": [],
        "service_names": [],
        "manifest_secret_names": [],
        "ingress_classes": [],
        "controller_hints": [],
        "lb_hints": [],
        "evidence_files": [],
        "ingress_to_service": [],
    }

    def _add_unique(key: str, value: str) -> None:
        if value and value not in signals[key]:
            signals[key].append(value)

    def _clean_value(value: str) -> str:
        cleaned = value.strip().strip('"').strip("'")
        if "{{" in cleaned and "}}" in cleaned:
            return "<templated>"
        return cleaned

    def _extract_backend_services(doc: str) -> list[str]:
        names: list[str] = []
        for m in re.finditer(r'\bserviceName\s*:\s*([^\s#]+)', doc):
            svc = _clean_value(m.group(1))
            if svc and svc not in names:
                names.append(svc)
        for service_block in re.finditer(r'(?ms)\bservice\s*:\s*((?:\n\s+[^\n]+){1,8})', doc):
            name_m = re.search(r'\bname\s*:\s*([^\s#]+)', service_block.group(1))
            if not name_m:
                continue
            svc = _clean_value(name_m.group(1))
            if svc and svc not in names:
                names.append(svc)
        return names

    for file in files:
        if not file.is_file():
            continue
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        if file.suffix.lower() not in (".yaml", ".yml", ".tf"):
            continue

        try:
            rel = str(file.relative_to(repo_path))
        except ValueError:
            rel = str(file)

        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue

        file_has_signal = False
        if file.suffix.lower() in (".yaml", ".yml"):
            for doc in re.split(r'^\s*---\s*$', text, flags=re.MULTILINE):
                kind_m = re.search(r'^\s*kind:\s*([A-Za-z0-9]+)\s*$', doc, re.MULTILINE)
                if not kind_m:
                    continue
                kind = kind_m.group(1)

                name_m = re.search(r'(?ms)^\s*metadata:\s*(?:\n\s+[^\n]+)*?\n\s*name:\s*([^\s#]+)', doc)
                if not name_m:
                    name_m = re.search(r'^\s*name:\s*([^\s#]+)', doc, re.MULTILINE)
                name = _clean_value(name_m.group(1)) if name_m else ""

                if prefix_filters and name and not any(name.lower().startswith(p) for p in prefix_filters):
                    continue

                if kind == "Ingress":
                    if name and name != "<templated>":
                        _add_unique("ingress_names", name)
                    class_m = re.search(r'^\s*ingressClassName:\s*([^\s#]+)', doc, re.MULTILINE)
                    if class_m:
                        ingress_class = _clean_value(class_m.group(1))
                        if ingress_class and ingress_class != "<templated>":
                            _add_unique("ingress_classes", ingress_class)
                    ann_class_m = re.search(r'kubernetes\.io/ingress\.class\s*:\s*([^\s#]+)', doc)
                    if ann_class_m:
                        ingress_class = _clean_value(ann_class_m.group(1))
                        if ingress_class and ingress_class != "<templated>":
                            _add_unique("ingress_classes", ingress_class)

                    for backend in _extract_backend_services(doc):
                        if backend != "<templated>":
                            _add_unique("service_names", backend)
                        if name:
                            signals["ingress_to_service"].append(
                                {"ingress": name, "service": backend, "evidence_file": rel}
                            )
                    file_has_signal = True

                if kind == "Service":
                    if name and name != "<templated>":
                        _add_unique("service_names", name)
                    if re.search(r'^\s*type:\s*LoadBalancer\b', doc, re.MULTILINE):
                        _add_unique("lb_hints", name or "<unnamed-service>")
                    file_has_signal = True

                if kind == "Secret" and name and name != "<templated>":
                    _add_unique("manifest_secret_names", name)
                    file_has_signal = True

        if file.suffix.lower() == ".tf":
            block_re = re.compile(r'^\s*resource\s+"([A-Za-z_][A-Za-z0-9_]*)"\s+"([^"]+)"', re.M)
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                m = block_re.match(lines[i])
                if not m:
                    i += 1
                    continue
                rtype, rname = m.groups()
                depth = 0
                block_lines = []
                j = i
                while j < min(i + 120, len(lines)):
                    block_lines.append(lines[j])
                    depth += lines[j].count("{") - lines[j].count("}")
                    if j > i and depth <= 0:
                        break
                    j += 1
                block_text = "\n".join(block_lines)

                if rtype.startswith("kubernetes_ingress"):
                    _add_unique("ingress_names", rname)
                    class_m = re.search(r'ingress_class_name\s*=\s*"([^"]+)"', block_text)
                    if class_m:
                        _add_unique("ingress_classes", _clean_value(class_m.group(1)))
                    for svc_m in re.finditer(r'\bservice_name\s*=\s*"([^"]+)"', block_text):
                        svc_name = _clean_value(svc_m.group(1))
                        if svc_name:
                            _add_unique("service_names", svc_name)
                            signals["ingress_to_service"].append(
                                {"ingress": rname, "service": svc_name, "evidence_file": rel}
                            )
                    for svc_block in re.finditer(r'(?ms)\bservice\s*\{([^}]+)\}', block_text):
                        name_m = re.search(r'\bname\s*=\s*"([^"]+)"', svc_block.group(1))
                        if not name_m:
                            continue
                        svc_name = _clean_value(name_m.group(1))
                        if svc_name:
                            _add_unique("service_names", svc_name)
                            signals["ingress_to_service"].append(
                                {"ingress": rname, "service": svc_name, "evidence_file": rel}
                            )
                    file_has_signal = True

                if rtype.startswith("kubernetes_service"):
                    _add_unique("service_names", rname)
                    if re.search(r'\btype\s*=\s*"LoadBalancer"', block_text, re.I):
                        _add_unique("lb_hints", rname)
                    file_has_signal = True

                i = j + 1

        for ingress_class in list(signals["ingress_classes"]):
            class_lower = ingress_class.lower()
            if "nginx" in class_lower:
                _add_unique("controller_hints", "ingress-nginx")
            if "alb" in class_lower:
                _add_unique("controller_hints", "aws-load-balancer-controller")
            if "application-gateway" in class_lower or "azure" in class_lower:
                _add_unique("controller_hints", "azure-application-gateway")
            if "gce" in class_lower:
                _add_unique("controller_hints", "gce-ingress")

        if file_has_signal:
            _add_unique("evidence_files", rel)

    deduped_routes = []
    seen_routes: set[tuple[str, str, str]] = set()
    for route in signals["ingress_to_service"]:
        key = (
            str(route.get("ingress", "")).strip(),
            str(route.get("service", "")).strip(),
            str(route.get("evidence_file", "")).strip(),
        )
        if key in seen_routes:
            continue
        seen_routes.add(key)
        deduped_routes.append(route)
    signals["ingress_to_service"] = deduped_routes
    return signals

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
    fallback_map: Dict[str, str] = {
        "aws_lb_listener": "aws_lb",
        "aws_alb_listener": "aws_alb",
        "aws_lb_target_group": "aws_lb",
        "aws_alb_target_group": "aws_alb",
        "aws_lb_target_group_attachment": "aws_lb_target_group",
        "aws_s3_bucket_public_access_block": "aws_s3_bucket",
        "aws_s3_bucket_policy": "aws_s3_bucket",
        "azurerm_lb_backend_address_pool": "azurerm_lb",
        "azurerm_lb_rule": "azurerm_lb",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
        "azurerm_key_vault_key": "azurerm_key_vault",
        "azurerm_key_vault_secret": "azurerm_key_vault",
    }
    try:
        from pathlib import Path as _Path
        db_path = _Path(__file__).parent.parent / "Output" / "Learning" / "triage.db"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT terraform_type, parent_type FROM resource_types WHERE parent_type IS NOT NULL"
        ).fetchall()
        conn.close()
        db_map = {row[0]: row[1] for row in rows}
        return {**fallback_map, **db_map}
    except Exception:
        return fallback_map


def extract_context(repo_path_str: str) -> RepositoryContext:
    """
    Extracts context from the repository and returns a data model.
    """
    repo_path = Path(repo_path_str)
    files = iter_files(repo_path)
    repo_name = repo_path.name
    context = RepositoryContext(repository_name=repo_name)
    k8s_topology = extract_kubernetes_topology_signals(files, repo_path)
    ingress_signals = detect_ingress_from_code(files, repo_path)
    k8s_service_names = {
        name for name in k8s_topology.get("service_names", [])
        if isinstance(name, str) and name and not name.startswith("<")
    }

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
    known_resource_keys: set[tuple[str, str]] = {(r.resource_type, r.name) for r in context.resources}

    def _ensure_inferred_resource(resource_type: str, canonical_name: str, source_file: str, line_number: int) -> str:
        inferred_name = f"__inferred__{canonical_name}"
        key = (resource_type, inferred_name)
        if key not in known_resource_keys:
            context.resources.append(
                Resource(
                    name=inferred_name,
                    resource_type=resource_type,
                    file_path=source_file,
                    line_number=line_number,
                    properties={
                        "inferred": "true",
                        "canonical_name": canonical_name,
                        "inference_source": "routing_signal",
                    },
                )
            )
            known_resource_keys.add(key)
        return inferred_name

    # Load type-level parent map as fallback
    parent_type_map = _load_parent_type_map()

    # Heuristics: extract network ACL/IP/public-access details from Terraform blocks
    # and attach them to Resource.properties so report generation can make evidence-based
    # internet exposure decisions.
    def _parse_simple_block_attributes(text: str) -> Dict[str, str]:
        attrs: Dict[str, str] = {}
        sanitized_text = "\n".join(
            line for line in text.splitlines()
            if not re.match(r'^\s*(#|//)', line)
        )
        for m in re.finditer(r'(\b[a-zA-Z0-9_]+\b)\s*=\s*("[^"]*"|\[.*?\]|\S+)', sanitized_text, re.S):
            key = m.group(1)
            val = m.group(2).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            attrs[key] = val
        return attrs

    _TRUTHY = {"1", "true", "yes", "enabled", "on"}
    _FALSY = {"0", "false", "no", "disabled", "off"}

    def _normalize_bool(value: object) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().strip('"').strip("'").lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
        return None

    def _set_bool_prop(props: Dict[str, object], key: str, raw_value: object) -> None:
        normalized = _normalize_bool(raw_value)
        if normalized is None:
            return
        props[key] = "true" if normalized else "false"

    def _append_signal(props: Dict[str, object], signal: str) -> None:
        if not signal:
            return
        existing = props.get("internet_access_signals")
        signals: list[str]
        if isinstance(existing, list):
            signals = [str(s) for s in existing]
        elif existing:
            signals = [str(existing)]
        else:
            signals = []
        if signal not in signals:
            signals.append(signal)
        props["internet_access_signals"] = signals

    def _mark_internet_access(props: Dict[str, object], signal: str) -> None:
        props["internet_access"] = "true"
        _append_signal(props, signal)

    def _extract_tf_reference_labels(value: object, tf_type: str) -> list[str]:
        if value is None:
            return []
        text = str(value)
        pattern = re.compile(
            rf'(?:\${{)?{re.escape(tf_type)}\.([A-Za-z0-9_\-]+)\.[A-Za-z0-9_]+(?:}})?',
            re.I,
        )
        return sorted({m.group(1) for m in pattern.finditer(text)})

    def _extract_arm_resource_names(value: object, segment: str) -> list[str]:
        if value is None:
            return []
        text = str(value)
        pattern = re.compile(rf'/{re.escape(segment)}/([^/"\s\]]+)', re.I)
        return sorted({m.group(1) for m in pattern.finditer(text)})

    def _is_world_source(value: object) -> bool:
        if value is None:
            return False
        text = str(value).lower()
        return any(token in text for token in ("0.0.0.0/0", "::/0", "\"*\"", " internet", "any"))

    for resource, block_text in resource_blocks:
        props = resource.properties or {}
        attrs = _parse_simple_block_attributes(block_text)
        resource_type = resource.resource_type

        top_level_name_match = re.search(r'^\s*name\s*=\s*"([^"]+)"', block_text, re.MULTILINE)
        if top_level_name_match:
            props["actual_name"] = top_level_name_match.group(1)
        elif "name" in attrs and attrs["name"]:
            props["actual_name"] = attrs["name"]

        # Normalize common explicit public-access toggles used across providers.
        for bool_key in (
            "publicly_accessible",
            "public_network_access_enabled",
            "associate_public_ip_address",
            "private_cluster_enabled",
            "public_fqdn_enabled",
            "internal",
            "internet_facing",
            "allow_blob_public_access",
        ):
            if bool_key in attrs:
                _set_bool_prop(props, bool_key, attrs.get(bool_key))

        if "public_network_access" in attrs:
            pna = str(attrs.get("public_network_access", "")).strip()
            if pna:
                props["public_network_access"] = pna
                if pna.lower() in _TRUTHY | {"enabled", "public"}:
                    _mark_internet_access(props, "public_network_access enabled")

        for public_ref_key in (
            "public_ip_address_id",
            "public_ip_address",
            "public_ip_id",
            "public_dns",
            "public_dns_name",
        ):
            raw_value = attrs.get(public_ref_key)
            if raw_value:
                props[public_ref_key] = raw_value
                _mark_internet_access(props, f"{public_ref_key} configured")

        # Network Security Group: gather security_rule entries + internet ingress evidence.
        if resource_type == "azurerm_network_security_group":
            rules: list[dict] = []
            rule_re = re.compile(r'security_rule\s+"?([A-Za-z0-9_\-]+)"?\s*=\s*\{', re.I)
            lines = block_text.splitlines()
            i = 0
            while i < len(lines):
                m = rule_re.search(lines[i])
                if m:
                    depth = 0
                    block_lines = []
                    j = i
                    while j < len(lines):
                        block_lines.append(lines[j])
                        depth += lines[j].count('{') - lines[j].count('}')
                        if j > i and depth <= 0:
                            break
                        j += 1
                    rule_text = "\n".join(block_lines)
                    rule_attrs = _parse_simple_block_attributes(rule_text)
                    rule = {
                        "name": m.group(1),
                        "direction": rule_attrs.get("direction"),
                        "access": rule_attrs.get("access"),
                        "protocol": rule_attrs.get("protocol"),
                        "source_address_prefix": rule_attrs.get("source_address_prefix") or rule_attrs.get("source_address_prefixes"),
                        "destination_address_prefix": rule_attrs.get("destination_address_prefix") or rule_attrs.get("destination_address_prefixes"),
                        "destination_port_range": rule_attrs.get("destination_port_range") or rule_attrs.get("destination_port_ranges"),
                    }
                    rules.append(rule)
                    i = j + 1
                    continue
                i += 1
            if rules:
                props["network_acls"] = rules
                ingress_open = False
                for rule in rules:
                    direction = str(rule.get("direction", "")).strip().lower()
                    access = str(rule.get("access", "")).strip().lower()
                    source = str(rule.get("source_address_prefix", "")).strip().lower()
                    if direction == "inbound" and access == "allow" and (
                        source in {"*", "internet", "any", "0.0.0.0/0", "::/0"} or "0.0.0.0/0" in source or "::/0" in source
                    ):
                        ingress_open = True
                        break
                if ingress_open:
                    props["internet_ingress_open"] = "true"
                    _append_signal(props, "nsg inbound from internet")

        # Public IP: explicit internet-reachable endpoint.
        if resource_type == "azurerm_public_ip":
            public: Dict[str, str] = {}
            if any(k in attrs for k in ("ip_address", "allocation_method", "domain_name_label")):
                public["allocation_method"] = attrs.get("allocation_method")
                public["ip_address"] = attrs.get("ip_address")
                public["domain_name_label"] = attrs.get("domain_name_label")
                public["is_public"] = "true"
                props["public_ip"] = public
                _mark_internet_access(props, "azure public ip resource")

        # Azure NIC / LB frontend evidence
        if resource_type == "azurerm_network_interface":
            nic_public_ip_id = attrs.get("public_ip_address_id")
            if nic_public_ip_id:
                props["public_ip_address_id"] = nic_public_ip_id
                props["has_public_ip"] = "true"
                _append_signal(props, "nic attached to public ip")

        if resource_type == "azurerm_network_interface_security_group_association":
            nic_names = _extract_arm_resource_names(attrs.get("network_interface_id"), "networkInterfaces")
            nsg_names = _extract_arm_resource_names(attrs.get("network_security_group_id"), "networkSecurityGroups")
            if nic_names:
                props["network_interface_name"] = nic_names[0]
            if nsg_names:
                props["network_security_group_name"] = nsg_names[0]

        if resource_type in ("azurerm_lb", "azurerm_lb_frontend_ip_configuration", "azurerm_lb_backend_address_pool"):
            if "private_ip_address" in attrs or "private_ip_address_allocation" in attrs:
                props["private_ip"] = attrs.get("private_ip_address") or attrs.get("private_ip_address_allocation")
            if attrs.get("public_ip_address_id"):
                props["public_ip_address_id"] = attrs.get("public_ip_address_id")
                _mark_internet_access(props, "load balancer frontend has public ip")

        if resource_type == "azurerm_application_gateway":
            if re.search(r'frontend_ip_configuration\s*\{[^}]*public_ip_address_id\s*=', block_text, re.S | re.I):
                _mark_internet_access(props, "application gateway public frontend")

        # Azure Key Vault / SQL / AKS explicit signals.
        if resource_type == "azurerm_key_vault":
            pna_enabled = _normalize_bool(attrs.get("public_network_access_enabled"))
            if pna_enabled is True:
                _mark_internet_access(props, "key vault public network access enabled")

        if resource_type in {"azurerm_mssql_server", "azurerm_sql_server"}:
            pna_enabled = _normalize_bool(attrs.get("public_network_access_enabled"))
            if pna_enabled is True:
                _mark_internet_access(props, "sql server public network access enabled")

        if resource_type in {"azurerm_mssql_firewall_rule", "azurerm_sql_firewall_rule"}:
            start_ip = str(attrs.get("start_ip_address", "")).strip()
            end_ip = str(attrs.get("end_ip_address", "")).strip()
            if start_ip == "0.0.0.0" and end_ip == "0.0.0.0":
                props["sql_firewall_public"] = "true"
                _append_signal(props, "sql firewall allows Azure services")
            elif start_ip == "0.0.0.0" and end_ip in {"255.255.255.255", "0.0.0.0/0"}:
                props["sql_firewall_public"] = "true"
                _append_signal(props, "sql firewall allows world range")

        if resource_type == "azurerm_kubernetes_cluster":
            private_cluster_enabled = _normalize_bool(attrs.get("private_cluster_enabled"))
            if private_cluster_enabled is False:
                _mark_internet_access(props, "aks private_cluster_enabled=false")
            if _normalize_bool(attrs.get("public_fqdn_enabled")) is True:
                _mark_internet_access(props, "aks public fqdn enabled")

        # AWS S3 explicit public signals.
        if resource_type == "aws_s3_bucket":
            acl = attrs.get("acl")
            if acl:
                props["acl"] = acl
                if acl.strip().lower().startswith("public-"):
                    props["s3_public_acl"] = "true"
                    _mark_internet_access(props, f"s3 acl={acl}")

        if resource_type == "aws_s3_bucket_policy":
            allows_public_principal = bool(
                re.search(r'"Effect"\s*:\s*"Allow"', block_text, re.I)
                and (
                    re.search(r'"Principal"\s*:\s*"\*"', block_text, re.I)
                    or re.search(r'"Principal"\s*:\s*\{[^}]*"\*"', block_text, re.S | re.I)
                )
            )
            if allows_public_principal:
                props["s3_public_policy"] = "true"
                _mark_internet_access(props, "s3 bucket policy allows public principal")

        if resource_type == "aws_s3_bucket_public_access_block":
            for block_key in (
                "block_public_acls",
                "ignore_public_acls",
                "block_public_policy",
                "restrict_public_buckets",
            ):
                if block_key in attrs:
                    _set_bool_prop(props, block_key, attrs.get(block_key))
            relaxed = any(
                _normalize_bool(attrs.get(key)) is False
                for key in ("block_public_acls", "ignore_public_acls", "block_public_policy", "restrict_public_buckets")
            )
            if relaxed:
                props["s3_public_access_block_relaxed"] = "true"
                _append_signal(props, "s3 public access block relaxed")

        # AWS compute/network explicit public signals.
        if resource_type == "aws_db_instance":
            if _normalize_bool(attrs.get("publicly_accessible")) is True:
                _mark_internet_access(props, "rds publicly_accessible=true")

        if resource_type == "aws_subnet":
            if "map_public_ip_on_launch" in attrs:
                _set_bool_prop(props, "map_public_ip_on_launch", attrs.get("map_public_ip_on_launch"))

        if resource_type == "aws_security_group":
            ingress_blocks = re.findall(r'ingress\s*\{(.*?)\}', block_text, re.S | re.I)
            if any(_is_world_source(ingress_body) for ingress_body in ingress_blocks):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group ingress from internet")

        if resource_type == "aws_security_group_rule":
            if str(attrs.get("type", "")).strip().lower() == "ingress" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group rule ingress from internet")

        if resource_type == "aws_instance":
            if "associate_public_ip_address" in attrs:
                if _normalize_bool(attrs.get("associate_public_ip_address")) is True:
                    _mark_internet_access(props, "ec2 associate_public_ip_address=true")
            subnet_refs = _extract_tf_reference_labels(attrs.get("subnet_id"), "aws_subnet")
            if subnet_refs:
                props["subnet_refs"] = subnet_refs
            sg_refs = _extract_tf_reference_labels(attrs.get("vpc_security_group_ids"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        if resource_type in {"aws_elb", "aws_lb", "aws_alb"}:
            if "internal" in attrs:
                internal_bool = _normalize_bool(attrs.get("internal"))
                if internal_bool is False:
                    _mark_internet_access(props, "load balancer internal=false")
            scheme = str(attrs.get("scheme", "")).strip().lower()
            if scheme == "internet-facing":
                _mark_internet_access(props, "load balancer scheme=internet-facing")
            subnet_refs = _extract_tf_reference_labels(attrs.get("subnets"), "aws_subnet")
            if subnet_refs:
                props["subnet_refs"] = subnet_refs
            sg_refs = _extract_tf_reference_labels(attrs.get("security_groups"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        if resource_type in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine"}:
            nic_names = _extract_arm_resource_names(attrs.get("network_interface_ids"), "networkInterfaces")
            if nic_names:
                props["network_interface_names"] = nic_names

        resource.properties = props

    # Second pass: resolve parent references
    no_generic_parent_types = {
        "aws_lb",
        "aws_alb",
        "aws_elb",
        "azurerm_lb",
        "azurerm_application_gateway",
    }

    for resource, block_text in resource_blocks:
        if resource.parent:
            continue

        ref_candidates: list[str] = []
        for ref_match in ref_re.finditer(block_text):
            ref_key = ref_match.group(1)
            if ref_key in res_lookup and res_lookup[ref_key] is not resource:
                ref_candidates.append(ref_key)

        preferred_parent_type = parent_type_map.get(resource.resource_type)

        def _parent_candidate_for_type(parent_tf_type: str) -> Optional[str]:
            for ref_key in ref_candidates:
                parts = ref_key.split(".", 1)
                if len(parts) == 2 and parts[0] == parent_tf_type:
                    return ref_key
            # Same-file fallback for unresolved references (keeps deterministic local grouping)
            for candidate, _ in resource_blocks:
                if (
                    candidate is not resource
                    and candidate.resource_type == parent_tf_type
                    and candidate.file_path == resource.file_path
                ):
                    return f"{candidate.resource_type}.{candidate.name}"
            # Repo-wide fallback when same-file parent is not present
            for candidate, _ in resource_blocks:
                if candidate is not resource and candidate.resource_type == parent_tf_type:
                    return f"{candidate.resource_type}.{candidate.name}"
            return None

        # If type metadata defines a preferred parent, only accept that parent type.
        if preferred_parent_type:
            resolved_parent = _parent_candidate_for_type(preferred_parent_type)
            if resolved_parent:
                resource.parent = resolved_parent
            continue

        # Otherwise, use the first explicit resource reference unless this is a top-level edge
        # component where generic reference-parenting frequently misclassifies ownership.
        if resource.resource_type in no_generic_parent_types:
            continue

        if ref_candidates:
            resource.parent = ref_candidates[0]

    # -------------------------------------------------------------------------
    # Public-access signal post-processing:
    # - Propagate child control signals to parent services
    # - Infer compute/LB exposure from subnet + SG + public IP associations
    # -------------------------------------------------------------------------
    def _is_prop_true(props: Dict[str, object], key: str) -> bool:
        return str((props or {}).get(key, "")).strip().lower() in _TRUTHY

    def _list_prop(props: Dict[str, object], key: str) -> list[str]:
        raw = (props or {}).get(key)
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(item) for item in raw if str(item).strip()]
        return [str(raw)]

    for resource, _ in resource_blocks:
        props = resource.properties or {}
        if not resource.parent:
            continue
        parent = res_lookup.get(resource.parent)
        if not parent:
            continue
        parent_props = parent.properties or {}

        if _is_prop_true(props, "sql_firewall_public"):
            _mark_internet_access(parent_props, "sql firewall rule enables public endpoint")

        if _is_prop_true(props, "s3_public_policy") or _is_prop_true(props, "s3_public_acl"):
            _mark_internet_access(parent_props, "s3 bucket child policy/acl is public")

        if _is_prop_true(props, "s3_public_access_block_relaxed"):
            _append_signal(parent_props, "s3 public access block relaxed")

        if resource.resource_type == "aws_security_group_rule" and _is_prop_true(props, "internet_ingress_open"):
            parent_props["internet_ingress_open"] = "true"
            _append_signal(parent_props, "security group ingress rule allows internet")

        parent.properties = parent_props

    aws_public_subnets: set[str] = set()
    aws_open_security_groups: set[str] = set()
    azure_public_nics: set[str] = set()
    azure_open_nsgs: set[str] = set()
    nic_to_nsg: Dict[str, str] = {}

    for resource, _ in resource_blocks:
        props = resource.properties or {}
        if resource.resource_type == "aws_subnet" and _is_prop_true(props, "map_public_ip_on_launch"):
            aws_public_subnets.add(resource.name)
        if resource.resource_type == "aws_security_group" and _is_prop_true(props, "internet_ingress_open"):
            aws_open_security_groups.add(resource.name)

        if resource.resource_type == "azurerm_network_interface":
            has_public_ip = _is_prop_true(props, "has_public_ip") or bool(props.get("public_ip_address_id"))
            if has_public_ip:
                azure_public_nics.add(str(props.get("actual_name") or resource.name))

        if resource.resource_type == "azurerm_network_security_group" and _is_prop_true(props, "internet_ingress_open"):
            azure_open_nsgs.add(str(props.get("actual_name") or resource.name))

        if resource.resource_type == "azurerm_network_interface_security_group_association":
            nic_name = str(props.get("network_interface_name") or "").strip()
            nsg_name = str(props.get("network_security_group_name") or "").strip()
            if nic_name and nsg_name:
                nic_to_nsg[nic_name] = nsg_name

    for resource, _ in resource_blocks:
        props = resource.properties or {}

        if resource.resource_type == "aws_instance":
            associate_public_ip = _normalize_bool(props.get("associate_public_ip_address")) is True
            subnet_refs = _list_prop(props, "subnet_refs")
            sg_refs = _list_prop(props, "security_group_refs")
            uses_public_subnet = any(subnet in aws_public_subnets for subnet in subnet_refs)
            has_open_sg = any(security_group in aws_open_security_groups for security_group in sg_refs)
            if associate_public_ip:
                _mark_internet_access(props, "ec2 associate_public_ip_address=true")
            elif uses_public_subnet and has_open_sg:
                _mark_internet_access(props, "ec2 in public subnet with internet-open security group")
            resource.properties = props
            continue

        if resource.resource_type in {"aws_elb", "aws_lb", "aws_alb"}:
            internal_bool = _normalize_bool(props.get("internal"))
            scheme = str(props.get("scheme", "")).strip().lower()
            subnet_refs = _list_prop(props, "subnet_refs")
            sg_refs = _list_prop(props, "security_group_refs")
            uses_public_subnet = any(subnet in aws_public_subnets for subnet in subnet_refs)
            has_open_sg = any(security_group in aws_open_security_groups for security_group in sg_refs) if sg_refs else True
            if internal_bool is not True and (
                scheme == "internet-facing"
                or _normalize_bool(props.get("internet_access")) is True
                or (uses_public_subnet and has_open_sg)
            ):
                props["internet_facing"] = "true"
                _mark_internet_access(props, "load balancer deployed with public ingress evidence")
            resource.properties = props
            continue

        if resource.resource_type in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine"}:
            nic_names = _list_prop(props, "network_interface_names")
            if not nic_names:
                continue
            for nic_name in nic_names:
                if nic_name not in azure_public_nics:
                    continue
                nsg_name = nic_to_nsg.get(nic_name)
                if nsg_name and nsg_name in azure_open_nsgs:
                    _mark_internet_access(props, "vm nic has public ip with internet-open nsg")
                else:
                    _mark_internet_access(props, "vm nic has public ip")
                break
            resource.properties = props

    # -------------------------------------------------------------------------
    # Third pass: emit typed Relationship objects from block attributes
    # -------------------------------------------------------------------------

    # Patterns that signal specific relationship types within a block:
    #   (attr_regex, relationship_type, swap_direction)
    # swap_direction=True means target→source (block resource IS the target)
    _ATTR_RELATIONSHIP_PATTERNS = [
        # contains — resource references a parent by ID/name attribute
        (re.compile(r'(?:storage_account_name|server_name|vault_name|cluster_name|account_name|api_management_name|api_name|gateway_name|load_balancer_name)\s*=\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.CONTAINS, True),

        # depends_on — explicit TF depends_on block
        (re.compile(r'depends_on\s*=\s*\[([^\]]+)\]', re.S),
         RelationshipType.DEPENDS_ON, False),

        # encrypts — key_vault_key_id attribute points at a KV key
        (re.compile(r'key_vault_key_id\s*=\s*(?:azurerm_key_vault_key)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.ENCRYPTS, False),

        # restricts_access — VNet rule / private endpoint targets
        (re.compile(r'(?:virtual_network_subnet_id|subnet_id|subnet_ids?)\s*=\s*\[?\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.RESTRICTS_ACCESS, False),

        # monitors — diagnostic setting source_resource_id
        (re.compile(r'target_resource_id\s*=\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.MONITORS, False),

        # routes_ingress_to — explicit backend/integration/origin references
        (re.compile(
            r'(?:backend_[a-z_]*address[a-z_]*|backend_[a-z_]*id|service_url|integration_uri|uri'
            r'|backend_uri|origin_id|default_backend[a-z_]*|upstream_[a-z_]*)\s*=\s*'
            r'(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.',
            re.I,
        ),
         RelationshipType.ROUTES_INGRESS_TO, False),

        # grants_access_to — role_assignment scope points at a resource
        (re.compile(r'scope\s*=\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.GRANTS_ACCESS_TO, False),

        # authenticates_via — managed identity / identity block reference
        (re.compile(r'(?:identity_ids|user_assigned_identity_id|identity_id)\s*=\s*\[?\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I),
         RelationshipType.AUTHENTICATES_VIA, False),
    ]

    # Regex to extract type from a reference string like "azurerm_foo.bar.id"
    _full_ref_re = re.compile(r'\b([a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.')
    # Regex to detect variable references (gap candidates)
    _var_re = re.compile(r'\bvar\.([a-z][a-z0-9_\-]+)\b')
    _routing_url_attr_re = re.compile(
        r'^\s*(service_url|integration_uri|uri|backend_url|backend_uri|origin_host_header|origin_url)\s*=\s*"([^"]+)"',
        re.I | re.M,
    )
    _edge_source_tokens = (
        "api_management",
        "api_gateway",
        "application_gateway",
        "front_door",
        "load_balancer",
        "ingress",
        "gateway",
        "cloudfront",
        "waf",
    )

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
                    full_m = _full_ref_re.search(m.group(0))
                    if not full_m:
                        full_m = _full_ref_re.search(block_text[max(0, m.start() - 20):m.end() + 80])
                    ref_type = full_m.group(1) if full_m else "unknown"
                    confidence = "extracted" if full_m else "inferred"
                    rel_notes = "" if full_m else (
                        "Attribute reference matched but target type could not be resolved: "
                        f"{m.group(0).strip()[:120]}"
                    )
                    if swap:
                        src_type, src_name, tgt_type, tgt_name = ref_type, ref_name, rtype, rname
                    else:
                        src_type, src_name, tgt_type, tgt_name = rtype, rname, ref_type, ref_name
                    if src_name != tgt_name or src_type != tgt_type:
                        effective_rel_type = rel_type
                        if rel_type == RelationshipType.ROUTES_INGRESS_TO and not any(
                            token in rtype.lower() for token in _edge_source_tokens
                        ):
                            effective_rel_type = RelationshipType.DEPENDS_ON
                        context.relationships.append(Relationship(
                            source_type=src_type, source_name=src_name,
                            target_type=tgt_type, target_name=tgt_name,
                            relationship_type=effective_rel_type,
                            source_repo=repo_name, confidence=confidence, notes=rel_notes,
                        ))

        # --- enrichment gaps: variable references in security-relevant attrs ---
        _SENSITIVE_ATTRS = re.compile(
            r'(?:key_vault_key_id|scope|principal_id|storage_account_name|server_name'
            r'|backend_address|backend_uri|integration_uri|service_url|uri|origin_url'
            r'|subnet_id|target_resource_id)\s*=\s*(var\.[a-z][a-z0-9_\-]+)',
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

        # --- URL-based routing heuristics (captures backend URLs without direct TF refs) ---
        is_edge_source = any(token in rtype.lower() for token in _edge_source_tokens)
        url_rel_type = RelationshipType.ROUTES_INGRESS_TO if is_edge_source else RelationshipType.DEPENDS_ON
        for url_match in _routing_url_attr_re.finditer(block_text):
            attr_name = url_match.group(1)
            url_value = url_match.group(2).strip()
            if _full_ref_re.search(url_value):
                continue
            host_match = re.search(r'https?://([^/\s]+)', url_value, re.I)
            if not host_match:
                continue
            host = host_match.group(1).lower()

            endpoint_resolved = False
            for endpoint_pattern, endpoint_type, name_group_idx in _ENDPOINT_PATTERNS:
                endpoint_match = endpoint_pattern.search(url_value)
                if not endpoint_match:
                    continue
                canonical_name = endpoint_match.group(name_group_idx).lower().rstrip("/")
                local_key = f"{endpoint_type}.{canonical_name}"
                if local_key in res_lookup:
                    context.relationships.append(Relationship(
                        source_type=rtype,
                        source_name=rname,
                        target_type=endpoint_type,
                        target_name=canonical_name,
                        relationship_type=url_rel_type,
                        source_repo=repo_name,
                        confidence="extracted",
                        notes=f"Routing URL attribute '{attr_name}' references {canonical_name}",
                    ))
                else:
                    inferred_target_name = _ensure_inferred_resource(
                        endpoint_type, canonical_name, resource.file_path, resource.line_number
                    )
                    context.relationships.append(Relationship(
                        source_type=rtype,
                        source_name=rname,
                        target_type=endpoint_type,
                        target_name=inferred_target_name,
                        relationship_type=url_rel_type,
                        source_repo=repo_name,
                        confidence="inferred",
                        notes=(
                            f"Routing URL attribute '{attr_name}' references external endpoint "
                            f"{canonical_name} ({endpoint_type})"
                        ),
                    ))
                endpoint_resolved = True
                break
            if endpoint_resolved:
                continue

            if ".svc" in host:
                service_name = host.split(".")[0]
                if service_name in k8s_service_names:
                    context.relationships.append(Relationship(
                        source_type=rtype,
                        source_name=rname,
                        target_type="kubernetes_service",
                        target_name=service_name,
                        relationship_type=url_rel_type,
                        source_repo=repo_name,
                        confidence="extracted",
                        notes=f"Routing URL attribute '{attr_name}' points to Kubernetes service DNS '{host}'",
                    ))
                else:
                    context.relationships.append(Relationship(
                        source_type=rtype,
                        source_name=rname,
                        target_type="unknown",
                        target_name=f"k8s_service:{service_name}",
                        relationship_type=url_rel_type,
                        source_repo=repo_name,
                        confidence="inferred",
                        notes=f"Routing URL attribute '{attr_name}' points to unresolved Kubernetes DNS '{host}'",
                    ))
                continue

            context.relationships.append(Relationship(
                source_type=rtype,
                source_name=rname,
                target_type="unknown",
                target_name=f"url:{host}",
                relationship_type=url_rel_type,
                source_repo=repo_name,
                confidence="inferred",
                notes=f"Routing URL attribute '{attr_name}' points to unresolved host '{host}'",
            ))

    # Kubernetes manifest ingress chain signals
    for route in k8s_topology.get("ingress_to_service", []):
        ingress_name = str(route.get("ingress", "")).strip()
        service_name = str(route.get("service", "")).strip()
        evidence_file = str(route.get("evidence_file", "")).strip()
        if not ingress_name:
            continue
        if not service_name or service_name == "<templated>":
            context.relationships.append(Relationship(
                source_type="kubernetes_ingress",
                source_name=ingress_name,
                target_type="unknown",
                target_name=f"k8s_service:{service_name or 'unknown'}",
                relationship_type=RelationshipType.ROUTES_INGRESS_TO,
                source_repo=repo_name,
                confidence="inferred",
                notes=(
                    "Kubernetes ingress backend is templated/unresolved"
                    + (f" ({evidence_file})" if evidence_file else "")
                ),
            ))
            continue
        context.relationships.append(Relationship(
            source_type="kubernetes_ingress",
            source_name=ingress_name,
            target_type="kubernetes_service",
            target_name=service_name,
            relationship_type=RelationshipType.ROUTES_INGRESS_TO,
            source_repo=repo_name,
            confidence="extracted",
            notes=(
                "Kubernetes manifest routes ingress to service"
                + (f" ({evidence_file})" if evidence_file else "")
            ),
        ))

    # Emit unresolved routing assumptions for detected edge gateways with no backend match.
    routed_sources: set[tuple[str, str]] = set()
    for rel in context.relationships:
        rel_kind = rel.relationship_type.value if hasattr(rel.relationship_type, "value") else str(rel.relationship_type)
        if rel_kind == RelationshipType.ROUTES_INGRESS_TO.value:
            routed_sources.add((str(rel.source_type), str(rel.source_name)))
    for edge_identity in ingress_signals.get("edge_resources", []):
        if "." not in edge_identity:
            continue
        edge_type, edge_name = edge_identity.split(".", 1)
        if (edge_type, edge_name) in routed_sources:
            continue
        context.relationships.append(Relationship(
            source_type=edge_type,
            source_name=edge_name,
            target_type="unknown",
            target_name=f"unresolved_backend:{edge_identity}",
            relationship_type=RelationshipType.ROUTES_INGRESS_TO,
            source_repo=repo_name,
            confidence="inferred",
            notes="Ingress-capable resource detected but no concrete backend target was extracted",
        ))

    # -------------------------------------------------------------------------
    # Fourth pass: scan config/code files for connection string dependencies
    # -------------------------------------------------------------------------
    extract_connection_string_dependencies(repo_path, context)

    deduped_relationships: list[Relationship] = []
    seen_relationships: set[tuple[str, str, str, str, str, str]] = set()
    for rel in context.relationships:
        rel_kind = rel.relationship_type.value if hasattr(rel.relationship_type, "value") else str(rel.relationship_type)
        key = (
            str(rel.source_type),
            str(rel.source_name),
            str(rel.target_type),
            str(rel.target_name),
            rel_kind,
            str(rel.confidence),
        )
        if key in seen_relationships:
            continue
        seen_relationships.add(key)
        deduped_relationships.append(rel)
    context.relationships = deduped_relationships

    return context
