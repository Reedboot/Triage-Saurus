# context_extraction.py
import json
import os
import re
import signal
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Dict, Set, Tuple, Optional

import sys
from pathlib import Path

# Ensure Scripts/Utils is importable when this module is called directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Utils'))

from models import Resource, Connection, Relationship, RelationshipType, RepositoryContext


# ---------------------------------------------------------------------------
# Endpoint/resource detection is now handled by opengrep rules in Rules/Detection.
# Custom regex patterns for endpoints have been removed in favor of opengrep-first approach.
# ---------------------------------------------------------------------------

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
_DOCKER_FROM_RE = re.compile(r'^\s*FROM\s+([^\s]+)(?:\s+AS\s+([A-Za-z0-9_\-\.]+))?', re.I)

# Timeout for opengrep subprocesses (seconds). Prevents pipeline from hanging
# indefinitely if opengrep blocks or encounters unexpected input.
# Each rule file gets this budget; 19 rules × 30s = 570s worst-case.
OPENGREP_TIMEOUT_SECONDS = 30


def _run_opengrep_scan(config_path: Path, target_path: Path) -> list[dict]:
    if not config_path.exists():
        return []
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    # Restrict scan to relevant IaC and config file types only — avoids scanning
    # large compiled artefacts, vendor trees, and binary files which dramatically
    # slow down generic-language rules.
    _INCLUDE_EXTS = ["*.tf", "*.tfvars", "*.hcl", "*.yaml", "*.yml", "*.json",
                     "*.py", "*.js", "*.ts", "*.cs", "*.go", "*.java", "*.rb"]
    _EXCLUDE_DIRS = [".git", ".terraform", "node_modules", "vendor", "__pycache__",
                     ".tox", "dist", "build", "target", ".eggs"]
    include_flags: list[str] = []
    for ext in _INCLUDE_EXTS:
        include_flags += ["--include", ext]
    exclude_flags: list[str] = []
    for d in _EXCLUDE_DIRS:
        exclude_flags += ["--exclude-dir", d]
    cmd = [
        "opengrep", "scan",
        "--config", str(config_path),
        str(target_path),
        "--json",
        "--output", str(tmp_path),
        "--quiet",
        "--no-git-ignore",   # prevents spawning git subprocesses that outlive timeout
    ] + include_flags + exclude_flags
    proc = None
    try:
        # Run in its own process group so we can kill the entire tree on timeout.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.wait(timeout=OPENGREP_TIMEOUT_SECONDS)
    except FileNotFoundError:
        # opengrep not installed — treat as no results
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return []
    except subprocess.TimeoutExpired:
        # Kill entire process group (opengrep + any git/child processes)
        if proc is not None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return []

    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        return []
    try:
        payload = json.loads(tmp_path.read_text())
    except Exception:
        return []
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return payload.get("results", []) if isinstance(payload, dict) else []


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

    Runs ALL rules under Rules/Detection/AppConfig/ (not just
    cloud-endpoint-dependency-detection.yml) so that SQL, Service Bus,
    Application Insights, Redis and Storage connections are also captured.
    """
    repo_name = context.repository_name

    already_found: set[tuple[str, str]] = set()  # (resource_type, canonical_name)
    for existing in context.resources:
        canonical = (existing.properties or {}).get("canonical_name")
        if canonical:
            already_found.add((existing.resource_type, str(canonical).lower().rstrip("/")))

    repo_root = Path(__file__).resolve().parents[2]
    appconfig_dir = repo_root / "Rules" / "Detection" / "AppConfig"

    # Collect all rule files in AppConfig — each is run independently so that a
    # failure in one rule doesn't suppress results from the others.
    rule_paths = sorted(appconfig_dir.glob("*.yml")) if appconfig_dir.is_dir() else []

    for rule_path in rule_paths:
        hits = _run_opengrep_scan(rule_path, repo_path)
        for result in hits:
            rtype = result.get("resource_type") or result.get("type")
            canonical_name = result.get("canonical_name")
            if not rtype or not canonical_name:
                continue
            key = (rtype, canonical_name.lower().rstrip("/"))
            if key in already_found:
                continue
            already_found.add(key)
            source_path = Path(str(result.get("path", "")))
            source_file = _relative_repo_path(repo_path, source_path)
            start_line = int((result.get("start") or {}).get("line", 1) or 1)
            # Synthetic resource so the node appears in the graph
            synth = Resource(
                name=f"__inferred__{canonical_name}",
                resource_type=rtype,
                file_path=source_file,
                line_number=start_line,
                properties={"inferred": "true", "canonical_name": canonical_name},
            )
            context.resources.append(synth)
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


def iter_files(repo_path: Path) -> List[Path]:
    """Iterate over files in the repository, excluding certain directories."""
    # This is a simplified version of the original iter_files.
    # In a real implementation, you'd want to handle .gitignore, etc.
    return list(repo_path.glob("**/*"))


def iter_dockerfiles(repo_path: Path) -> Iterable[Path]:
    """Yield Dockerfile-style manifests (Dockerfile, Dockerfile.*, */Dockerfile)."""
    patterns = ("Dockerfile", "Dockerfile.*", "**/Dockerfile", "**/Dockerfile.*")
    seen: set[Path] = set()
    for pattern in patterns:
        for candidate in repo_path.glob(pattern):
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                yield candidate


def _relative_repo_path(repo_path: Path, target: Path) -> str:
    try:
        rel = str(target.relative_to(repo_path))
    except ValueError:
        rel = str(target)
    return rel.replace("\\", "/")

def is_valid_azure_resource_name(name: str) -> bool:
    """Validate that a name is a valid Azure resource name, not a reference or Terraform syntax.
    
    Returns False if the name contains:
    - Dots (.) which indicate Terraform interpolation like azurerm_public_ip.VM_PublicIP.name
    - Resource type prefixes (azurerm_, aws_, etc.) which indicate it's a reference
    - Interpolation syntax ($, {, }) which indicate dynamic values
    """
    if not name:
        return False
    # Check for Terraform reference syntax
    if "." in name:
        return False
    # Check for resource type prefixes (indicates a reference like "azurerm_public_ip")
    for prefix in ["azurerm_", "aws_", "google_", "azuread_", "alicloud_", "oci_", "tencentcloud_", "huaweicloud_", "data."]:
        if name.startswith(prefix):
            return False
    # Check for interpolation syntax
    if any(c in name for c in "${}"):
        return False
    return True


def extract_resource_names(files: List[Path], repo_path: Path, resource_type: str) -> List[str]:
    """Extract resource names of a given type from Terraform files.
    
    Only extracts from 'resource' blocks, not 'data' blocks.
    """
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
    """Detect all Terraform resource types in the repository.
    
    Only detects 'resource' blocks, not 'data' blocks.
    """
    resource_types = set()
    for file in files:
        if file.suffix == ".tf":
            try:
                content = file.read_text()
                # Only match 'resource' blocks, not 'data' blocks
                matches = re.findall(r'^\s*resource\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', content, re.MULTILINE)
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
    repo_root = Path(__file__).resolve().parents[2]
    ingress_rule_path = repo_root / "Rules" / "Detection" / "Code" / "ingress-wildcard-bind-detection.yml"
    ingress_rule_hits = _run_opengrep_scan(ingress_rule_path, repo_path)

    ingress_code_patterns: list[tuple[re.Pattern, str]] = [
        (re.compile(r'kind:\s*Ingress\b', re.I), "Kubernetes Ingress manifest"),
    ]
    if not ingress_rule_hits:
        ingress_code_patterns = [
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

    for result in ingress_rule_hits:
        source_path = Path(str(result.get("path", "")))
        rel = _relative_repo_path(repo_path, source_path)
        line_no = int((result.get("start") or {}).get("line", 1) or 1)
        check_id = str(result.get("check_id", "ingress-wildcard-bind"))
        evidence = f"{rel}:{line_no} (opengrep {check_id})"
        if evidence not in seen_evidence:
            seen_evidence.add(evidence)
            ingress_signals["evidence"].append(evidence)

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


def extract_kubernetes_manifest_resources(files: List[Path], repo_path: Path) -> List[Resource]:
    """Extract concrete Kubernetes resources from manifests (opengrep-first)."""
    repo_root = Path(__file__).resolve().parents[2]
    rules_dir = repo_root / "Rules" / "Detection" / "Kubernetes"

    def _run_opengrep_k8s_detection() -> List[dict]:
        if not rules_dir.exists():
            return []
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cmd = [
            "opengrep", "scan",
            "--config", str(rules_dir),
            str(repo_path),
            "--json",
            "--output", str(tmp_path),
            "--quiet",
            "--no-git-ignore",
            "--include", "*.yaml",
            "--include", "*.yml",
            "--exclude-dir", ".git",
            "--exclude-dir", ".terraform",
            "--exclude-dir", "node_modules",
        ]
        proc_k8s = None
        try:
            proc_k8s = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            proc_k8s.wait(timeout=OPENGREP_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return []
        except subprocess.TimeoutExpired:
            if proc_k8s is not None:
                try:
                    os.killpg(os.getpgid(proc_k8s.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc_k8s.kill()
                    except Exception:
                        pass
                try:
                    proc_k8s.wait(timeout=5)
                except Exception:
                    pass
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return []

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            return []
        try:
            payload = json.loads(tmp_path.read_text())
        except Exception:
            return []
        return payload.get("results", [])

    def _metavar_text(metavars: dict, key: str) -> str:
        entry = metavars.get(key)
        if not isinstance(entry, dict):
            return ""
        for attr in ("abstract_content", "value", "content"):
            value = entry.get(attr)
            if value:
                return str(value).strip().strip('"').strip("'")
        return ""

    detected_rows = _run_opengrep_k8s_detection()
    if detected_rows:
        resources: List[Resource] = []
        seen: set[tuple[str, str, str, int]] = set()
        for result in detected_rows:
            check_id = str(result.get("check_id", ""))
            if not check_id.endswith("context-kubernetes-manifest"):
                continue

            extra = result.get("extra", {}) or {}
            metavars = extra.get("metavars", {}) or {}
            kind = _metavar_text(metavars, "$KIND")
            name = _metavar_text(metavars, "$NAME")

            if not kind:
                snippet = str(extra.get("lines", ""))
                kind_match = re.search(r'\bkind:\s*([A-Za-z0-9]+)', snippet)
                if kind_match:
                    kind = kind_match.group(1)
            if not name or "{{" in name:
                continue

            source_path = Path(str(result.get("path", "")))
            try:
                rel = str(source_path.relative_to(repo_path))
            except Exception:
                rel = str(source_path)
            line_number = int((result.get("start") or {}).get("line", 1) or 1)
            resource_type = f"kubernetes_{kind.lower()}"

            # Try to extract namespace from snippet for non-Namespace resources
            namespace = None
            if kind.lower() != "namespace":
                snippet = str(extra.get("lines", ""))
                ns_match = re.search(r'namespace:\s*([A-Za-z0-9\-_]+)', snippet)
                if ns_match:
                    namespace = ns_match.group(1)

            identity = (resource_type, name, rel, line_number)
            if identity in seen:
                continue
            seen.add(identity)
            
            props = {"manifest_kind": kind, "source": "opengrep_detection"}
            if namespace:
                props["k8s_namespace"] = namespace

            # For Services, read source file and find the specific YAML doc to extract spec.type
            if kind == "Service":
                try:
                    svc_text = source_path.read_text(errors="ignore")
                    # Split into YAML documents and find the one matching this service name
                    for svc_doc in re.split(r'^\s*---\s*$', svc_text, flags=re.MULTILINE):
                        name_chk = re.search(r'^\s*name:\s*(\S+)', svc_doc, re.MULTILINE)
                        kind_chk = re.search(r'^\s*kind:\s*Service\b', svc_doc, re.MULTILINE)
                        if kind_chk and name_chk and name_chk.group(1).strip() == name:
                            svc_type_m = re.search(r'^\s*type:\s*(\S+)', svc_doc, re.MULTILINE)
                            if svc_type_m:
                                svc_type = svc_type_m.group(1).strip()
                                props["service_type"] = svc_type
                                if svc_type == "LoadBalancer":
                                    props["internet_ingress_open"] = "true"
                            break
                except Exception:
                    pass
            
            resources.append(
                Resource(
                    name=name,
                    resource_type=resource_type,
                    file_path=rel,
                    line_number=line_number,
                    properties=props,
                )
            )
        if resources:
            return resources

    resources: List[Resource] = []

    def _clean_value(value: str) -> str:
        cleaned = value.strip().strip('"').strip("'")
        if "{{" in cleaned and "}}" in cleaned:
            return "<templated>"
        return cleaned

    for file in files:
        if not file.is_file():
            continue
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        if file.suffix.lower() not in (".yaml", ".yml"):
            continue

        try:
            rel = str(file.relative_to(repo_path))
        except ValueError:
            rel = str(file)

        try:
            text = file.read_text(errors="ignore")
        except Exception:
            continue

        offset = 0
        for doc in re.split(r'^\s*---\s*$', text, flags=re.MULTILINE):
            kind_m = re.search(r'^\s*kind:\s*([A-Za-z0-9]+)\s*$', doc, re.MULTILINE)
            if not kind_m:
                offset += len(doc) + 1
                continue

            name_m = re.search(r'(?ms)^\s*metadata:\s*(?:\n\s+[^\n]+)*?\n\s*name:\s*([^\s#]+)', doc)
            if not name_m:
                name_m = re.search(r'^\s*name:\s*([^\s#]+)', doc, re.MULTILINE)

            raw_name = name_m.group(1) if name_m else ""
            name = _clean_value(raw_name) if raw_name else ""
            if not name or name == "<templated>":
                offset += len(doc) + 1
                continue

            kind = kind_m.group(1)
            resource_type = f"kubernetes_{kind.lower()}"
            line_number = text[:offset + kind_m.start()].count("\n") + 1

            # Extract namespace from metadata for non-Namespace resources
            namespace = None
            if kind.lower() != "namespace":
                ns_m = re.search(r'^\s*namespace:\s*([^\s#]+)', doc, re.MULTILINE)
                if ns_m:
                    namespace = _clean_value(ns_m.group(1))

            props = {"manifest_kind": kind, "source": "kubernetes_manifest"}
            if namespace:
                props["k8s_namespace"] = namespace

            # For Services, extract spec.type so LoadBalancer exposure is detectable
            if kind == "Service":
                svc_type_m = re.search(r'^\s*type:\s*(\S+)', doc, re.MULTILINE)
                if svc_type_m:
                    svc_type = _clean_value(svc_type_m.group(1))
                    props["service_type"] = svc_type
                    if svc_type == "LoadBalancer":
                        props["internet_ingress_open"] = "true"

            resources.append(
                Resource(
                    name=name,
                    resource_type=resource_type,
                    file_path=rel,
                    line_number=line_number,
                    properties=props,
                )
            )
            offset += len(doc) + 1

    return resources

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
        # AWS Load Balancers
        "aws_alb_listener": "aws_alb",
        "aws_alb_target_group": "aws_alb",
        "aws_lb_listener": "aws_lb",
        "aws_lb_target_group": "aws_lb",
        "aws_lb_target_group_attachment": "aws_lb_target_group",
        # AWS API Gateway REST API
        "aws_api_gateway_api_key": "aws_api_gateway_rest_api",
        "aws_api_gateway_deployment": "aws_api_gateway_rest_api",
        "aws_api_gateway_resource": "aws_api_gateway_rest_api",
        "aws_api_gateway_usage_plan": "aws_api_gateway_rest_api",
        "aws_api_gateway_method": "aws_api_gateway_resource",
        "aws_api_gateway_integration": "aws_api_gateway_method",
        "aws_api_gateway_method_response": "aws_api_gateway_method",
        "aws_api_gateway_integration_response": "aws_api_gateway_integration",
        "aws_api_gateway_stage": "aws_api_gateway_deployment",
        "aws_api_gateway_usage_plan_key": "aws_api_gateway_usage_plan",
        # AWS API Gateway v2
        "aws_apigatewayv2_integration": "aws_apigatewayv2_api",
        "aws_apigatewayv2_route": "aws_apigatewayv2_api",
        "aws_apigatewayv2_stage": "aws_apigatewayv2_api",
        # AWS Networking
        "aws_instance": "aws_subnet",
        "aws_network_interface": "aws_subnet",
        "aws_eks_cluster": "aws_subnet",
        "aws_subnet": "aws_vpc",
        "aws_eip": "aws_instance|aws_lb|aws_elb",
        # AWS S3
        "aws_s3_bucket_acl": "aws_s3_bucket",
        "aws_s3_bucket_cors_configuration": "aws_s3_bucket",
        "aws_s3_bucket_ownership_controls": "aws_s3_bucket",
        "aws_s3_bucket_public_access_block": "aws_s3_bucket",
        "aws_s3_bucket_policy": "aws_s3_bucket",
        # AWS Security Group
        "aws_security_group_rule": "aws_security_group",
        # AWS SNS
        "aws_sns_topic_subscription": "aws_sns_topic",
        # Azure Load Balancer
        "azurerm_lb_backend_address_pool": "azurerm_lb",
        "azurerm_lb_probe": "azurerm_lb",
        "azurerm_lb_rule": "azurerm_lb",
        # Azure Application Gateway
        "azurerm_application_gateway_backend_pool": "azurerm_application_gateway",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
        # Azure Key Vault
        "azurerm_key_vault_key": "azurerm_key_vault",
        "azurerm_key_vault_secret": "azurerm_key_vault",
        "azurerm_key_vault_certificate": "azurerm_key_vault",
        # Azure App Service
        "azurerm_app_service": "azurerm_app_service_plan|azurerm_service_plan",
        "azurerm_function_app": "azurerm_app_service_plan|azurerm_service_plan",
        "azurerm_linux_function_app": "azurerm_app_service_plan|azurerm_service_plan",
        "azurerm_windows_function_app": "azurerm_app_service_plan|azurerm_service_plan",
        # Azure Network Security
        "azurerm_network_interface_security_group_association": "azurerm_network_security_group",
        "azurerm_network_interface": "azurerm_subnet|azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine",
        "azurerm_network_security_rule": "azurerm_network_security_group",
        "azurerm_public_ip": "azurerm_linux_virtual_machine|azurerm_windows_virtual_machine|azurerm_virtual_machine|azurerm_lb",
        # Azure Virtual Machines
        "azurerm_virtual_machine_extension": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine",
        "azurerm_linux_virtual_machine_extension": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine",
        "azurerm_windows_virtual_machine_extension": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine",
        "azurerm_ssh_public_key": "azurerm_linux_virtual_machine",
        # Azure Automation
        "azurerm_automation_runbook": "azurerm_automation_account",
        # Azure Network hierarchy
        "azurerm_subnet": "azurerm_virtual_network",
        "azurerm_virtual_network_peering": "azurerm_virtual_network",
        "azurerm_virtual_network_gateway": "azurerm_virtual_network",
        "azurerm_virtual_network_gateway_connection": "azurerm_virtual_network_gateway",
        "azurerm_bastion_host": "azurerm_virtual_network",
        "azurerm_private_endpoint": "azurerm_virtual_network",
        "azurerm_vpn_gateway": "azurerm_virtual_network",
        "azurerm_vpn_site": "azurerm_vpn_gateway",
        "azurerm_vpn_gateway_connection": "azurerm_vpn_gateway",
        # Azure Networking - Route
        "azurerm_route_table": "azurerm_resource_group",
        "azurerm_route": "azurerm_route_table",
        "azurerm_nat_gateway": "azurerm_resource_group",
        "azurerm_nat_gateway_public_ip_association": "azurerm_nat_gateway",
        "azurerm_nat_gateway_public_ip_prefix_association": "azurerm_nat_gateway",
        # Azure Networking - NSG & DNS
        "azurerm_network_security_group": "azurerm_resource_group",
        "azurerm_dns_zone": "azurerm_resource_group",
        "azurerm_dns_a_record": "azurerm_dns_zone",
        "azurerm_dns_aaaa_record": "azurerm_dns_zone",
        "azurerm_dns_caa_record": "azurerm_dns_zone",
        "azurerm_dns_cname_record": "azurerm_dns_zone",
        "azurerm_dns_mx_record": "azurerm_dns_zone",
        "azurerm_dns_ns_record": "azurerm_dns_zone",
        "azurerm_dns_ptr_record": "azurerm_dns_zone",
        "azurerm_dns_srv_record": "azurerm_dns_zone",
        "azurerm_dns_txt_record": "azurerm_dns_zone",
        "azurerm_private_dns_zone": "azurerm_resource_group",
        "azurerm_private_dns_zone_virtual_network_link": "azurerm_private_dns_zone",
        "azurerm_private_dns_a_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_cname_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_mx_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_ptr_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_srv_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_txt_record": "azurerm_private_dns_zone",
        "azurerm_private_dns_zone_group": "azurerm_private_endpoint",
        # Azure Networking - NIC associations
        "azurerm_network_interface_backend_address_pool_association": "azurerm_network_interface",
        "azurerm_network_interface_nat_rule_association": "azurerm_network_interface",
        "azurerm_network_interface_application_gateway_backend_address_pool_association": "azurerm_network_interface",
        "azurerm_network_interface_application_security_group_association": "azurerm_network_interface",
        "azurerm_private_endpoint_network_interface": "azurerm_private_endpoint",
        # Azure Storage hierarchy
        "azurerm_storage_container": "azurerm_storage_account",
        "azurerm_storage_blob": "azurerm_storage_container",
        "azurerm_storage_queue": "azurerm_storage_account",
        "azurerm_storage_share": "azurerm_storage_account",
        "azurerm_storage_account_customer_managed_key": "azurerm_storage_account",
        "azurerm_storage_account_network_rules": "azurerm_storage_account",
        "azurerm_storage_data_lake_gen2_filesystem": "azurerm_storage_account",
        "azurerm_storage_encryption_scope": "azurerm_storage_account",
        "azurerm_storage_account_sas": "azurerm_storage_account",
        "azurerm_storage_management_policy": "azurerm_storage_account",
        # Azure Load Balancer
        "azurerm_lb_backend_address_pool": "azurerm_lb",
        "azurerm_lb_outbound_rule": "azurerm_lb",
        "azurerm_lb_nat_pool": "azurerm_lb",
        "azurerm_lb_nat_rule": "azurerm_lb",
        # Azure Application Gateway
        "azurerm_application_gateway_backend_address_pool": "azurerm_application_gateway",
        "azurerm_application_gateway_backend_http_settings": "azurerm_application_gateway",
        "azurerm_application_gateway_frontend_ip_configuration": "azurerm_application_gateway",
        "azurerm_application_gateway_frontend_port": "azurerm_application_gateway",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
        "azurerm_application_gateway_http_settings": "azurerm_application_gateway",
        "azurerm_application_gateway_path_rule": "azurerm_application_gateway",
        "azurerm_application_gateway_redirect_configuration": "azurerm_application_gateway",
        "azurerm_application_gateway_request_routing_rule": "azurerm_application_gateway",
        "azurerm_application_gateway_rewrite_rule_set": "azurerm_application_gateway",
        "azurerm_application_gateway_ssl_policy": "azurerm_application_gateway",
        "azurerm_application_gateway_url_path_map": "azurerm_application_gateway",
        # Azure App Service
        "azurerm_app_service_certificate": "azurerm_app_service",
        "azurerm_app_service_certificate_binding": "azurerm_app_service",
        "azurerm_app_service_custom_hostname_binding": "azurerm_app_service",
        "azurerm_app_service_slot": "azurerm_app_service",
        "azurerm_app_service_managed_certificate": "azurerm_app_service",
        # Azure App Configuration
        "azurerm_app_configuration_key": "azurerm_app_configuration",
        # Azure Virtual Machines
        "azurerm_virtual_machine_data_disk_attachment": "azurerm_virtual_machine",
        "azurerm_virtual_machine_scale_set": "azurerm_resource_group",
        "azurerm_virtual_machine_scale_set_extension": "azurerm_virtual_machine_scale_set",
        "azurerm_monitor_data_collection_rule_association": "azurerm_virtual_machine",
        # Azure Service Bus hierarchy
        "azurerm_servicebus_queue": "azurerm_servicebus_namespace",
        "azurerm_servicebus_topic": "azurerm_servicebus_namespace",
        "azurerm_servicebus_subscription": "azurerm_servicebus_topic",
        "azurerm_servicebus_subscription_rule": "azurerm_servicebus_subscription",
        # Azure Event Hub hierarchy
        "azurerm_eventhub": "azurerm_eventhub_namespace",
        "azurerm_eventhub_consumer_group": "azurerm_eventhub",
        # Azure API Management
        "azurerm_api_management_api": "azurerm_api_management",
        "azurerm_api_management_api_operation": "azurerm_api_management_api",
        "azurerm_api_management_api_policy": "azurerm_api_management_api",
        "azurerm_api_management_backend": "azurerm_api_management",
        "azurerm_api_management_named_value": "azurerm_api_management",
        "azurerm_api_management_product": "azurerm_api_management",
        "azurerm_api_management_product_api": "azurerm_api_management_product",
        "azurerm_api_management_subscription": "azurerm_api_management",
        "azurerm_api_management_identity_provider": "azurerm_api_management",
        "azurerm_api_management_logger": "azurerm_api_management",
        "azurerm_api_management_certificate": "azurerm_api_management",
        "azurerm_api_management_property": "azurerm_api_management",
        "azurerm_api_management_gateway": "azurerm_api_management",
        "azurerm_api_management_gateway_api": "azurerm_api_management_gateway",
        # Azure Databases - SQL Server
        "azurerm_mssql_database": "azurerm_mssql_server",
        "azurerm_mssql_firewall_rule": "azurerm_mssql_server",
        "azurerm_mssql_server_extended_auditing_policy": "azurerm_mssql_server",
        "azurerm_mssql_server_security_alert_policy": "azurerm_mssql_server",
        "azurerm_mssql_server_transparent_data_encryption": "azurerm_mssql_server",
        "azurerm_mssql_server_vulnerability_assessment": "azurerm_mssql_server",
        "azurerm_mssql_elasticpool": "azurerm_mssql_server",
        "azurerm_mssql_database_extended_auditing_policy": "azurerm_mssql_database",
        "azurerm_mssql_database_threat_detection_policy": "azurerm_mssql_database",
        "azurerm_mssql_database_transparent_data_encryption": "azurerm_mssql_database",
        "azurerm_mssql_database_vulnerability_assessment_rule_baseline": "azurerm_mssql_database",
        # Azure Databases - MySQL
        "azurerm_mysql_database": "azurerm_mysql_server",
        "azurerm_mysql_flexible_database": "azurerm_mysql_flexible_server",
        "azurerm_mysql_server_key": "azurerm_mysql_server",
        "azurerm_mysql_configuration": "azurerm_mysql_server",
        "azurerm_mysql_firewall_rule": "azurerm_mysql_server",
        "azurerm_mysql_virtual_network_rule": "azurerm_mysql_server",
        # Azure Databases - PostgreSQL
        "azurerm_postgresql_database": "azurerm_postgresql_server",
        "azurerm_postgresql_server_key": "azurerm_postgresql_server",
        "azurerm_postgresql_configuration": "azurerm_postgresql_server",
        "azurerm_postgresql_firewall_rule": "azurerm_postgresql_server",
        "azurerm_postgresql_virtual_network_rule": "azurerm_postgresql_server",
        # Azure CosmosDB
        "azurerm_cosmosdb_sql_container": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_sql_database": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_sql_stored_procedure": "azurerm_cosmosdb_sql_container",
        "azurerm_cosmosdb_sql_trigger": "azurerm_cosmosdb_sql_container",
        "azurerm_cosmosdb_sql_user_defined_function": "azurerm_cosmosdb_sql_container",
        "azurerm_cosmosdb_mongo_database": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_mongo_collection": "azurerm_cosmosdb_mongo_database",
        "azurerm_cosmosdb_gremlin_database": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_gremlin_graph": "azurerm_cosmosdb_gremlin_database",
        "azurerm_cosmosdb_cassandra_keyspace": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_cassandra_table": "azurerm_cosmosdb_cassandra_keyspace",
        "azurerm_cosmosdb_table": "azurerm_cosmosdb_account",
        # Azure Containers
        "azurerm_container_group": "azurerm_resource_group",
        "azurerm_container_registry_webhook": "azurerm_container_registry",
        "azurerm_container_registry_cache_rule": "azurerm_container_registry",
        "azurerm_container_registry_token": "azurerm_container_registry",
        "azurerm_container_registry_scope_map": "azurerm_container_registry",
        # Azure Kubernetes
        "azurerm_kubernetes_cluster_node_pool": "azurerm_kubernetes_cluster",
        "azurerm_kubernetes_cluster_extension": "azurerm_kubernetes_cluster",
        # Azure Event Grid
        "azurerm_eventgrid_event_subscription": "azurerm_eventgrid_topic",
        # Azure Data Factory
        "azurerm_data_factory_integration_runtime_azure": "azurerm_data_factory",
        "azurerm_data_factory_integration_runtime_self_hosted": "azurerm_data_factory",
        "azurerm_data_factory_pipeline": "azurerm_data_factory",
        "azurerm_data_factory_dataset_azure_blob": "azurerm_data_factory",
        "azurerm_data_factory_dataset_delimited_text": "azurerm_data_factory",
        "azurerm_data_factory_linked_service_azure_blob_storage": "azurerm_data_factory",
        "azurerm_data_factory_linked_service_azure_sql_database": "azurerm_data_factory",
        # Azure IoT
        "azurerm_iothub_consumer_group": "azurerm_iothub",
        "azurerm_iothub_shared_access_policy": "azurerm_iothub",
        "azurerm_iothub_certificate": "azurerm_iothub",
        "azurerm_iot_security_solution": "azurerm_iothub",
        "azurerm_iot_security_device_group": "azurerm_iot_security_solution",
        # Azure Monitoring & Logging
        "azurerm_monitor_aad_diagnostic_setting": "azurerm_subscription",
        "azurerm_monitor_action_group_action_receiver": "azurerm_monitor_action_group",
        "azurerm_monitor_activity_log_alert": "azurerm_resource_group",
        "azurerm_monitor_metric_alert": "azurerm_resource_group",
        "azurerm_monitor_scheduled_query_rules_alert": "azurerm_resource_group",
        "azurerm_monitor_data_collection_rule": "azurerm_resource_group",
        "azurerm_monitor_diagnostic_setting": "azurerm_eventhub_namespace",
        "azurerm_log_analytics_query_pack_query": "azurerm_log_analytics_query_pack",
        "azurerm_log_analytics_saved_search": "azurerm_log_analytics_workspace",
        "azurerm_application_insights_analytics_item": "azurerm_application_insights",
        "azurerm_application_insights_api_key": "azurerm_application_insights",
        "azurerm_application_insights_smart_detection_rule": "azurerm_application_insights",
        "azurerm_application_insights_web_test": "azurerm_application_insights",
        # Azure Recovery Services & Backup
        "azurerm_backup_policy_vm": "azurerm_recovery_services_vault",
        "azurerm_backup_policy_file_share": "azurerm_recovery_services_vault",
        "azurerm_backup_protected_vm": "azurerm_recovery_services_vault",
        "azurerm_backup_protected_file_share": "azurerm_recovery_services_vault",
        "azurerm_site_recovery_fabric": "azurerm_recovery_services_vault",
        # Azure Service Fabric
        "azurerm_service_fabric_cluster": "azurerm_resource_group",
        "azurerm_service_fabric_managed_cluster": "azurerm_resource_group",
        # Azure APIM - Deep nesting (Policies, Schema, Diagnostics)
        "azurerm_api_management_api_policy": "azurerm_api_management_api",
        "azurerm_api_management_operation_policy": "azurerm_api_management_api_operation",
        "azurerm_api_management_product_policy": "azurerm_api_management_product",
        "azurerm_api_management_schema": "azurerm_api_management",
        "azurerm_api_management_diagnostic": "azurerm_api_management",
        "azurerm_api_management_issue": "azurerm_api_management",
        "azurerm_api_management_authorization_server": "azurerm_api_management",
        "azurerm_api_management_openid_connect_provider": "azurerm_api_management",
        "azurerm_api_management_backend_reconnect": "azurerm_api_management_backend",
        "azurerm_api_management_api_version_set": "azurerm_api_management",
        "azurerm_api_management_api_release": "azurerm_api_management_api",
        "azurerm_api_management_api_revision": "azurerm_api_management_api",
        "azurerm_api_management_tag": "azurerm_api_management",
        "azurerm_api_management_tag_description": "azurerm_api_management",
        # Azure App Gateway - HTTP Listeners, Probes, SSL Profiles
        "azurerm_application_gateway_http_setting": "azurerm_application_gateway",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
        "azurerm_application_gateway_probe": "azurerm_application_gateway",
        "azurerm_application_gateway_ssl_profile": "azurerm_application_gateway",
        "azurerm_application_gateway_trusted_root_certificate": "azurerm_application_gateway",
        "azurerm_application_gateway_private_link_configuration": "azurerm_application_gateway",
        # Azure WAF Policies
        "azurerm_web_application_firewall_policy": "azurerm_resource_group",
        "azurerm_web_application_firewall_policy_custom_rule": "azurerm_web_application_firewall_policy",
        "azurerm_web_application_firewall_policy_managed_rule": "azurerm_web_application_firewall_policy",
        "azurerm_web_application_firewall_policy_exclusion": "azurerm_web_application_firewall_policy",
        # Azure Load Balancer - Probes, Rules, NAT
        "azurerm_lb_probe": "azurerm_lb",
        "azurerm_lb_rule": "azurerm_lb",
        "azurerm_lb_inbound_nat_rule": "azurerm_lb",
        "azurerm_lb_inbound_nat_pool": "azurerm_lb",
        # Azure Network Security Group Rules
        "azurerm_network_security_rule": "azurerm_network_security_group",
        "azurerm_network_watcher_flow_log": "azurerm_network_watcher",
        # Azure App Service - Deep connections & settings
        "azurerm_app_service_virtual_network_swift_connection": "azurerm_app_service",
        "azurerm_app_service_connection": "azurerm_app_service",
        "azurerm_app_service_source_control": "azurerm_app_service",
        "azurerm_app_service_hybrid_connection": "azurerm_app_service",
        "azurerm_app_service_backup": "azurerm_app_service",
        "azurerm_app_service_active_slot": "azurerm_app_service",
        "azurerm_app_service_slot_virtual_network_swift_connection": "azurerm_app_service_slot",
        # Azure Function App
        "azurerm_function_app": "azurerm_app_service_plan",
        "azurerm_function_app_function": "azurerm_function_app",
        "azurerm_function_app_slot": "azurerm_function_app",
        # Azure SQL Database - Deep auditing, backup, sync
        "azurerm_sql_database_extended_auditing_policy": "azurerm_sql_database",
        "azurerm_sql_database_threat_detection_policy": "azurerm_sql_database",
        "azurerm_sql_replication_link": "azurerm_sql_database",
        "azurerm_sql_failover_group": "azurerm_mssql_server",
        "azurerm_sql_virtual_network_rule": "azurerm_mssql_server",
        "azurerm_sql_server_security_alert_policy": "azurerm_mssql_server",
        "azurerm_sql_sync_group": "azurerm_sql_database",
        "azurerm_sql_sync_member": "azurerm_sql_sync_group",
        # Azure Storage - Share directories, tables, entities
        "azurerm_storage_share_directory": "azurerm_storage_share",
        "azurerm_storage_table": "azurerm_storage_account",
        "azurerm_storage_table_entity": "azurerm_storage_table",
        "azurerm_storage_account_static_website": "azurerm_storage_account",
        # Azure Container Registry - Tasks, retention
        "azurerm_container_registry_retention_policy": "azurerm_container_registry",
        "azurerm_container_registry_task": "azurerm_container_registry",
        "azurerm_container_registry_task_schedule": "azurerm_container_registry_task",
        # Azure CosmosDB - SQL roles, failover
        "azurerm_cosmosdb_sql_role_definition": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_sql_role_assignment": "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_account_failover_priority": "azurerm_cosmosdb_account",
        # Azure Key Vault - Certificates, keys, secrets, access policies
        "azurerm_key_vault_certificate": "azurerm_key_vault",
        "azurerm_key_vault_key": "azurerm_key_vault",
        "azurerm_key_vault_secret": "azurerm_key_vault",
        "azurerm_key_vault_managed_storage_account": "azurerm_key_vault",
        "azurerm_key_vault_access_policy": "azurerm_key_vault",
        # Azure Monitoring - Autoscale, smart detector
        "azurerm_monitor_autoscale_setting": "azurerm_resource_group",
        "azurerm_monitor_smart_detector_alert_rule": "azurerm_resource_group",
        # Azure Logic Apps
        "azurerm_logic_app_workflow": "azurerm_resource_group",
        "azurerm_logic_app_trigger_http_request": "azurerm_logic_app_workflow",
        "azurerm_logic_app_action_custom": "azurerm_logic_app_workflow",
        # Azure Search Service
        "azurerm_search_service": "azurerm_resource_group",
        "azurerm_search_service_synonym_map": "azurerm_search_service",
        "azurerm_search_service_index": "azurerm_search_service",
        "azurerm_search_service_indexer": "azurerm_search_service",
        "azurerm_search_service_data_source": "azurerm_search_service",
        # Azure Bot Service
        "azurerm_bot_service": "azurerm_resource_group",
        "azurerm_bot_channel_ms_teams": "azurerm_bot_service",
        "azurerm_bot_channel_slack": "azurerm_bot_service",
        "azurerm_bot_connection": "azurerm_bot_service",
        # Azure Media Services
        "azurerm_media_services_account": "azurerm_resource_group",
        "azurerm_media_services_account_filter": "azurerm_media_services_account",
        "azurerm_media_services_streaming_locator": "azurerm_media_services_account",
        "azurerm_media_services_streaming_policy": "azurerm_media_services_account",
        "azurerm_media_services_streaming_endpoint": "azurerm_media_services_account",
        "azurerm_media_services_transform": "azurerm_media_services_account",
        "azurerm_media_services_job": "azurerm_media_services_transform",
        # Azure Stream Analytics
        "azurerm_stream_analytics_job": "azurerm_resource_group",
        "azurerm_stream_analytics_stream_input_blob": "azurerm_stream_analytics_job",
        "azurerm_stream_analytics_stream_input_eventhub": "azurerm_stream_analytics_job",
        "azurerm_stream_analytics_stream_input_iothub": "azurerm_stream_analytics_job",
        "azurerm_stream_analytics_output_blob": "azurerm_stream_analytics_job",
        "azurerm_stream_analytics_output_eventhub": "azurerm_stream_analytics_job",
        "azurerm_stream_analytics_job_schedule": "azurerm_stream_analytics_job",
        # Azure Service Bus - Authorization rules, disaster recovery
        "azurerm_servicebus_namespace_authorization_rule": "azurerm_servicebus_namespace",
        "azurerm_servicebus_queue_authorization_rule": "azurerm_servicebus_queue",
        "azurerm_servicebus_topic_authorization_rule": "azurerm_servicebus_topic",
        "azurerm_servicebus_subscription_rule": "azurerm_servicebus_subscription",
        "azurerm_servicebus_namespace_disaster_recovery_config": "azurerm_servicebus_namespace",
        # Azure Event Hub - Authorization rules, disaster recovery
        "azurerm_eventhub_namespace_authorization_rule": "azurerm_eventhub_namespace",
        "azurerm_eventhub_authorization_rule": "azurerm_eventhub",
        "azurerm_eventhub_namespace_disaster_recovery_config": "azurerm_eventhub_namespace",
        # Azure Relay
        "azurerm_relay_namespace": "azurerm_resource_group",
        "azurerm_relay_hybrid_connection": "azurerm_relay_namespace",
        "azurerm_relay_namespace_authorization_rule": "azurerm_relay_namespace",
        # Azure Synapse
        "azurerm_synapse_workspace": "azurerm_resource_group",
        "azurerm_synapse_sql_pool": "azurerm_synapse_workspace",
        "azurerm_synapse_spark_pool": "azurerm_synapse_workspace",
        "azurerm_synapse_pipeline": "azurerm_synapse_workspace",
        "azurerm_synapse_dataset": "azurerm_synapse_workspace",
        "azurerm_synapse_firewall_rule": "azurerm_synapse_workspace",
        # AWS VPC & Networking
        "aws_subnet": "aws_vpc",
        "aws_security_group": "aws_vpc",
        "aws_security_group_rule": "aws_security_group",
        "aws_network_acl": "aws_vpc",
        "aws_network_acl_rule": "aws_network_acl",
        "aws_route_table": "aws_vpc",
        "aws_route": "aws_route_table",
        "aws_nat_gateway": "aws_subnet",
        "aws_internet_gateway": "aws_vpc",
        "aws_vpc_peering_connection": "aws_vpc",
        # aws_vpn_gateway is also standalone gateway infrastructure.
        "aws_vpn_connection": "aws_vpn_gateway",
        "aws_flow_log": "aws_vpc",
        # AWS EC2 & Auto Scaling
        "aws_instance": "aws_subnet",
        "aws_autoscaling_attachment": "aws_autoscaling_group",
        "aws_autoscaling_lifecycle_hook": "aws_autoscaling_group",
        "aws_autoscaling_policy": "aws_autoscaling_group",
        "aws_autoscaling_schedule": "aws_autoscaling_group",
        # AWS Load Balancing
        "aws_lb": "aws_vpc",
        "aws_lb_target_group": "aws_vpc",
        "aws_lb_target_group_attachment": "aws_lb_target_group",
        "aws_lb_listener": "aws_lb",
        "aws_lb_listener_rule": "aws_lb_listener",
        # AWS RDS
        "aws_rds_cluster_instance": "aws_rds_cluster",
        "aws_db_instance": None,
        "aws_db_subnet_group": "aws_vpc",
        "aws_db_snapshot": None,
        # AWS S3
        "aws_s3_bucket_object": "aws_s3_bucket",
        "aws_s3_bucket_versioning": "aws_s3_bucket",
        "aws_s3_bucket_lifecycle_configuration": "aws_s3_bucket",
        "aws_s3_bucket_acl": "aws_s3_bucket",
        "aws_s3_bucket_policy": "aws_s3_bucket",
        "aws_s3_bucket_cors_configuration": "aws_s3_bucket",
        "aws_s3_bucket_server_side_encryption_configuration": "aws_s3_bucket",
        # AWS DynamoDB
        "aws_dynamodb_table_item": "aws_dynamodb_table",
        "aws_dynamodb_table_replica": "aws_dynamodb_table",
        "aws_dynamodb_ttl": "aws_dynamodb_table",
        # AWS Lambda
        "aws_lambda_function_event_invoke_config": "aws_lambda_function",
        "aws_lambda_permission": "aws_lambda_function",
        "aws_lambda_function_url": "aws_lambda_function",
        # AWS API Gateway
        "aws_api_gateway_resource": "aws_api_gateway_rest_api",
        "aws_api_gateway_method": "aws_api_gateway_resource",
        "aws_api_gateway_method_response": "aws_api_gateway_method",
        "aws_api_gateway_integration": "aws_api_gateway_method",
        "aws_api_gateway_integration_response": "aws_api_gateway_integration",
        "aws_api_gateway_stage": "aws_api_gateway_rest_api",
        "aws_api_gateway_deployment": "aws_api_gateway_rest_api",
        # AWS IAM
        "aws_iam_role_policy": "aws_iam_role",
        "aws_iam_role_policy_attachment": "aws_iam_role",
        "aws_iam_instance_profile": "aws_iam_role",
        "aws_iam_user_policy": "aws_iam_user",
        "aws_iam_user_policy_attachment": "aws_iam_user",
        "aws_iam_group_policy": "aws_iam_group",
        "aws_iam_group_membership": "aws_iam_group",
        # AWS ECS
        "aws_ecs_service": "aws_ecs_cluster",
        "aws_ecs_cluster_capacity_providers": "aws_ecs_cluster",
        # AWS CloudWatch
        "aws_cloudwatch_log_stream": "aws_cloudwatch_log_group",
        "aws_cloudwatch_event_target": "aws_cloudwatch_event_rule",
        # AWS Elasticache
        "aws_elasticache_subnet_group": "aws_vpc",
        # AWS API Gateway v2 (HTTP API) - deeper nesting
        "aws_apigatewayv2_api": None,
        "aws_apigatewayv2_stage": "aws_apigatewayv2_api",
        "aws_apigatewayv2_route": "aws_apigatewayv2_api",
        "aws_apigatewayv2_integration": "aws_apigatewayv2_api",
        "aws_apigatewayv2_model": "aws_apigatewayv2_api",
        "aws_apigatewayv2_authorizer": "aws_apigatewayv2_api",
        # AWS WAF v2 - web ACLs and logging
        "aws_wafv2_web_acl": None,
        "aws_wafv2_web_acl_logging_configuration": "aws_wafv2_web_acl",
        "aws_wafv2_ip_set": None,
        "aws_wafv2_regex_pattern_set": None,
        # AWS CloudFront - distributions and policies
        "aws_cloudfront_distribution": None,
        "aws_cloudfront_cache_policy": None,
        "aws_cloudfront_origin_request_policy": None,
        "aws_cloudfront_response_headers_policy": None,
        "aws_cloudfront_field_level_encryption_config": None,
        # AWS VPC - Endpoints and peering deeper
        "aws_vpc_endpoint": "aws_vpc",
        "aws_vpc_endpoint_service": None,
        "aws_vpc_endpoint_service_configuration": None,
        "aws_vpc_peering_connection_accepter": "aws_vpc_peering_connection",
        # AWS EC2 - Security group attachments
        "aws_network_interface_sg_attachment": "aws_network_interface",
        "aws_instance_sg_attachment": "aws_instance",
        # AWS ECS - Task definitions deeper
        "aws_ecs_task_definition": None,
        "aws_ecs_container_definition": "aws_ecs_task_definition",
        "aws_ecs_service_placement_constraints": "aws_ecs_service",
        # AWS RDS - Snapshots, cluster endpoints, parameter groups
        "aws_db_cluster_snapshot": "aws_rds_cluster",
        "aws_db_instance_snapshot": "aws_db_instance",
        "aws_db_parameter_group": None,
        "aws_db_cluster_parameter_group": "aws_rds_cluster",
        "aws_rds_cluster_endpoint": "aws_rds_cluster",
        # AWS ElastiCache - Cluster members
        "aws_elasticache_cluster_node": "aws_elasticache_cluster",
        "aws_elasticache_replication_group_member": "aws_elasticache_replication_group",
        # AWS Backup - Vault and plans
        "aws_backup_vault": None,
        "aws_backup_plan": None,
        "aws_backup_plan_rule": "aws_backup_plan",
        "aws_backup_resource_assignment": "aws_backup_plan",
        # AWS Glue - Jobs, crawlers, catalog
        "aws_glue_job": None,
        "aws_glue_crawler": None,
        "aws_glue_catalog_table": None,
        "aws_glue_connection": None,
        # AWS Systems Manager
        "aws_ssm_document": None,
        "aws_ssm_parameter": None,
        "aws_ssm_patch_group": None,
        # GCP Compute & Networking — prefer VPC parent when network_interface references one
        "google_compute_instance": "google_compute_subnetwork|google_compute_network",
        "google_compute_subnetwork": "google_compute_network",
        "google_compute_firewall": "google_compute_network",
        "google_compute_route": "google_compute_network",
        "google_compute_instance_group": "google_compute_zone",
        "google_compute_instance_from_template": "google_compute_zone",
        # GCP Kubernetes
        "google_container_node_pool": "google_container_cluster",
        # GCP Storage
        "google_storage_bucket_object": "google_storage_bucket",
        "google_storage_bucket_versioning": "google_storage_bucket",
        "google_storage_bucket_lifecycle": "google_storage_bucket",
        # GCP Cloud SQL
        "google_sql_database": "google_sql_database_instance",
        "google_sql_user": "google_sql_database_instance",
        # GCP BigQuery
        "google_bigquery_table": "google_bigquery_dataset",
        # GCP Pub/Sub
        "google_pubsub_subscription": "google_pubsub_topic",
        # GCP Cloud Functions
        "google_cloudfunctions_function_iam_member": "google_cloudfunctions_function",
        # GCP API Gateway
        "google_api_gateway_api_config": "google_api_gateway_api",
        "google_api_gateway_gateway": "google_api_gateway_api_config",
        # GCP Cloud Run
        "google_cloud_run_service_iam_member": "google_cloud_run_service",
        # GCP Firestore
        "google_firestore_document": "google_firestore_database",
        # GCP IAM
        "google_service_account_key": "google_service_account",
        "google_service_account_iam_member": "google_service_account",
        # GCP VPN
        "google_compute_vpn_gateway": "google_compute_network",
        "google_compute_vpn_tunnel": "google_compute_vpn_gateway",
        # GCP Storage IAM
        "google_storage_bucket_iam_binding": "google_storage_bucket",
        "google_storage_bucket_iam_member": "google_storage_bucket",
        # GCP Compute - Load Balancing & backends
        "google_compute_address": None,
        "google_compute_backend_service": None,
        "google_compute_backend_bucket": None,
        "google_compute_health_check": None,
        "google_compute_http_health_check": None,
        "google_compute_https_health_check": None,
        "google_compute_url_map": None,
        "google_compute_target_http_proxy": None,
        "google_compute_target_https_proxy": None,
        "google_compute_forwarding_rule": None,
        "google_compute_backend_service_backend": "google_compute_backend_service",
        "google_compute_backend_service_health_checks": "google_compute_backend_service",
        "google_compute_backend_service_signed_url_key": "google_compute_backend_service",
        # GCP Logging & Monitoring
        "google_logging_project_bucket_config": None,
        "google_logging_project_sink": None,
        "google_monitoring_notification_channel": None,
        "google_monitoring_alert_policy": None,
        "google_monitoring_uptime_check_config": None,
        # GCP IAM Custom Roles
        "google_service_account_custom_role": None,
        "google_organization_iam_member": None,
        # OCI VCN & Networking
        "oci_core_subnet": "oci_core_vcn",
        "oci_core_security_list": "oci_core_vcn",
        "oci_core_route_table": "oci_core_vcn",
        "oci_core_nat_gateway": "oci_core_vcn",
        "oci_core_internet_gateway": "oci_core_vcn",
        "oci_core_network_security_group": "oci_core_vcn",
        "oci_core_network_security_group_security_rule": "oci_core_network_security_group",
        # OCI Compute
        "oci_core_instance": "oci_core_subnet",
        "oci_core_volume_attachment": "oci_core_instance",
        # OCI Load Balancer
        "oci_load_balancer": "oci_core_subnet",
        "oci_load_balancer_backend_set": "oci_load_balancer",
        "oci_load_balancer_listener": "oci_load_balancer",
        "oci_load_balancer_certificate": "oci_load_balancer",
        # OCI Container Engine
        "oci_containerengine_node_pool": "oci_containerengine_cluster",
        # OCI Object Storage
        "oci_objectstorage_object": "oci_objectstorage_bucket",
        # OCI Functions
        "oci_functions_function": "oci_functions_application",
        # OCI API Gateway
        "oci_apigateway_deployment": "oci_apigateway_gateway",
        # OCI KMS
        "oci_kms_key": "oci_kms_vault",
        "oci_vault_secret": "oci_kms_vault",
        # OCI Logging
        "oci_logging_log": "oci_logging_log_group",
        # Alibaba VPC & Networking
        "alicloud_vswitch": "alicloud_vpc",
        "alicloud_security_group": "alicloud_vpc",
        "alicloud_security_group_rule": "alicloud_security_group",
        "alicloud_route_table": "alicloud_vpc",
        "alicloud_route_entry": "alicloud_route_table",
        "alicloud_nat_gateway": "alicloud_vpc",
        # Alibaba ECS & Compute
        "alicloud_instance": "alicloud_vswitch",
        # Alibaba Database
        "alicloud_db_database": "alicloud_db_instance",
        "alicloud_db_account": "alicloud_db_instance",
        "alicloud_rds_account": "alicloud_db_instance",
        # Alibaba OSS
        "alicloud_oss_bucket_object": "alicloud_oss_bucket",
        "alicloud_oss_bucket_versioning": "alicloud_oss_bucket",
        "alicloud_oss_bucket_acl": "alicloud_oss_bucket",
        # Alibaba SLB
        "alicloud_slb_server_group": "alicloud_slb",
        "alicloud_slb_listener": "alicloud_slb",
        "alicloud_slb_attachment": "alicloud_slb_server_group",
        # Alibaba Kubernetes
        "alicloud_cs_kubernetes_node_pool": "alicloud_cs_managed_kubernetes",
        # Alibaba API Gateway
        "alicloud_api_gateway_app": "alicloud_api_gateway_api",
        "alicloud_api_gateway_authorization": "alicloud_api_gateway_api",
        # Alibaba Log Service
        "alicloud_log_store": "alicloud_log_project",
        "alicloud_log_dashboard": "alicloud_log_project",
        # Alibaba KMS
        "alicloud_kms_secret": "alicloud_kms_key",
        "alicloud_kms_ciphertext": "alicloud_kms_key",
        # Kubernetes
        "kubernetes_namespace": "kubernetes_cluster",
        "kubernetes_config_map": "kubernetes_namespace",
        "kubernetes_daemon_set": "kubernetes_namespace",
        "kubernetes_daemonset": "kubernetes_namespace",
        "kubernetes_deployment": "kubernetes_namespace",
        "kubernetes_ingress": "kubernetes_namespace",
        "kubernetes_job": "kubernetes_namespace",
        "kubernetes_cronjob": "kubernetes_namespace",
        "kubernetes_pod": "kubernetes_namespace",
        "kubernetes_role": "kubernetes_namespace",
        "kubernetes_role_binding": "kubernetes_namespace",
        "kubernetes_rolebinding": "kubernetes_namespace",
        "kubernetes_clusterrole": "kubernetes_cluster",
        "kubernetes_clusterrolebinding": "kubernetes_clusterrole",
        "kubernetes_secret": "kubernetes_namespace",
        "kubernetes_service": "kubernetes_namespace",
        "kubernetes_serviceaccount": "kubernetes_namespace",
        "kubernetes_stateful_set": "kubernetes_namespace",
    }
    try:
        from ..Persist import resource_type_db as _rtdb
        db_path = _rtdb.DB_PATH
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT terraform_type, parent_type FROM resource_types WHERE parent_type IS NOT NULL"
        ).fetchall()
        conn.close()
        db_map = {row[0]: row[1] for row in rows}
        return {**fallback_map, **db_map}
    except Exception:
        return fallback_map


def _extract_tf_locals(files: List[Path]) -> Dict[str, str]:
    """Parse Terraform ``locals {}`` blocks and return ``{local_name: string_value}``."""
    result: Dict[str, str] = {}
    assignment_re = re.compile(r'^\s+([a-z][a-z0-9_]+)\s*=\s*"([^"]*)"', re.M)
    for f in files:
        if not hasattr(f, "suffix") or f.suffix != ".tf":
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"^\s*locals\s*\{", text, re.M):
            start = m.end()
            depth = 1
            i = start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block = text[start : i - 1]
            for assign in assignment_re.finditer(block):
                result[assign.group(1)] = assign.group(2)
    return result


def _resolve_apim_service_name(service_url: str, locals_dict: Dict[str, str]) -> Optional[str]:
    """Resolve an APIM ``service_url`` to the target workload's service name.

    Handles Terraform interpolation patterns such as::

        "https://${var.environment}-${local.app_name}.${var.domain_name}"

    Steps:
    1. Substitute ``${local.xxx}`` references with values from *locals_dict*.
    2. Extract the hostname from the resolved URL.
    3. Return the service-name segment (the literal part between the env prefix
       and the domain suffix), normalised to lower-case with hyphens.

    Returns ``None`` when the service name cannot be determined.
    """

    def _sub_local(m: re.Match) -> str:
        return locals_dict.get(m.group(1), m.group(0))

    resolved = re.sub(r"\$\{local\.([a-z][a-z0-9_]+)\}", _sub_local, service_url, flags=re.I)

    host_m = re.search(r"https?://([^/\s]+)", resolved, re.I)
    if not host_m:
        return None
    host = host_m.group(1)

    # Pattern A: "...}-SERVICE_NAME.${...}" — one or more ${...} tokens remain but
    # the service name is now a fully-resolved literal segment.
    svc_m = re.search(r"\}-([a-z][a-z0-9\-]+)\.", host)
    if svc_m:
        return svc_m.group(1).lower()

    # Pattern B: fully resolved host such as "prod-aks-helloworld.internal.example.com"
    if "${" not in host:
        stem = host.split(".")[0]          # e.g. "prod-aks-helloworld"
        parts = stem.split("-")
        if len(parts) > 1:
            return "-".join(parts[1:]).lower()  # strip env prefix

    return None


def extract_context(repo_path_str: str) -> RepositoryContext:
    """
    Extracts context from the repository and returns a data model.
    """
    repo_path = Path(repo_path_str)
    files = iter_files(repo_path)
    dockerfiles = list(iter_dockerfiles(repo_path))
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
    # Also match module call blocks: module "name" { source = "..." }
    # We do NOT add these as resources — instead we resolve their source and
    # include the resources they create.  Registry modules (no leading ./ or ../)
    # are handled via a known-module lookup table; local modules are scanned recursively.
    module_block_re = re.compile(r'^\s*module\s+"([^"]+)"')

    # Known registry-module → resource-type mappings (covers common public modules)
    KNOWN_MODULE_RESOURCES: dict[str, list[str]] = {
        "terraform-aws-modules/vpc/aws":           ["aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table"],
        "terraform-aws-modules/eks/aws":           ["aws_eks_cluster", "aws_eks_node_group"],
        "terraform-aws-modules/rds/aws":           ["aws_db_instance", "aws_db_subnet_group"],
        "terraform-aws-modules/s3-bucket/aws":     ["aws_s3_bucket"],
        "terraform-aws-modules/iam/aws//modules/iam-role": ["aws_iam_role"],
        "terraform-aws-modules/security-group/aws": ["aws_security_group"],
        "terraform-aws-modules/alb/aws":           ["aws_lb", "aws_lb_listener", "aws_lb_target_group"],
        "terraform-aws-modules/lambda/aws":        ["aws_lambda_function"],
        "terraform-google-modules/network/google": ["google_compute_network", "google_compute_subnetwork"],
        "terraform-google-modules/kubernetes-engine/google": ["google_container_cluster", "google_container_node_pool"],
        "Azure/aks/azurerm":                       ["azurerm_kubernetes_cluster"],
        "Azure/network/azurerm":                   ["azurerm_virtual_network", "azurerm_subnet"],
    }

    def _resolve_module_source(file: "Path", source: str, module_name: str) -> list[Resource]:
        """Resolve a module source to concrete Resource objects.

        For local modules (source starts with ./ or ../) the module directory
        is scanned for .tf resource blocks.  For registry modules a lookup
        table provides a best-effort list of likely resource types."""
        resolved: list[Resource] = []
        if source.startswith("./") or source.startswith("../"):
            module_dir = (file.parent / source).resolve()
            if module_dir.is_dir():
                tf_files = list(module_dir.glob("*.tf"))
                inner_re = re.compile(r'^\s*resource\s+"([A-Za-z_][A-Za-z0-9_]*)"\s+"([^"]+)"')
                for tf_file in tf_files:
                    try:
                        inner_content = tf_file.read_text(errors="ignore")
                    except Exception:
                        continue
                    for line in inner_content.splitlines():
                        im = inner_re.match(line)
                        if im:
                            rtype, rname = im.groups()
                            resolved.append(Resource(
                                name=rname,
                                resource_type=rtype,
                                file_path=str(tf_file),
                                properties={
                                    "terraform_block": "resource",
                                    "via_module": module_name,
                                    "module_source": source,
                                },
                            ))
        else:
            # Registry module — normalise the source key (strip version suffix)
            source_key = source.split("?")[0].rstrip("/")
            for pattern, types in KNOWN_MODULE_RESOURCES.items():
                if source_key == pattern or source_key.startswith(pattern.split("//")[0]):
                    for rtype in types:
                        resolved.append(Resource(
                            name=f"{module_name}_{rtype.split('_', 1)[-1]}",
                            resource_type=rtype,
                            file_path=str(file),
                            properties={
                                "terraform_block": "resource",
                                "via_module": module_name,
                                "module_source": source,
                                "inferred_from_registry_module": "true",
                            },
                        ))
                    break
        return resolved

    # Detect attribute references to other resources: type.name.attr
    # Note: this regex matches TYPE.NAME (the capture group) followed by .ATTR.
    # For data source references like data.TYPE.NAME.attr, the leading `data.TYPE`
    # segment is matched first and TYPE.NAME is never captured — see data_ref_re.
    ref_re = re.compile(r'\b([a-z][a-z0-9_]+\.[a-z][a-z0-9_\-]+)\.[a-z_]+\b')
    # Detect data source references explicitly: data.<resource_type>.<block_label>.<attr>
    data_ref_re = re.compile(r'\bdata\.([a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.', re.I)

    # First pass: collect all resources and build a lookup map
    resource_blocks: list[tuple[Resource, str]] = []  # (resource, raw_block_text)
    # Track (resource_type, name) pairs to avoid duplicates — including module-inferred resources
    known_resource_keys: set[tuple[str, str]] = set()
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
            # Check for module block first — must NOT become a resource node
            mm = module_block_re.match(lines[i])
            if mm:
                module_name = mm.group(1)
                # Collect the block body
                depth = 0
                mod_lines = []
                for j in range(i, min(i + 60, len(lines))):
                    mod_lines.append(lines[j])
                    depth += lines[j].count("{") - lines[j].count("}")
                    if j > i and depth <= 0:
                        break
                mod_text = "\n".join(mod_lines)
                src_m = re.search(r'source\s*=\s*"([^"]+)"', mod_text)
                if src_m:
                    module_resources = _resolve_module_source(file, src_m.group(1), module_name)
                    for mr in module_resources:
                        key = (mr.resource_type, mr.name)
                        if key not in known_resource_keys:
                            context.resources.append(mr)
                            resource_blocks.append((mr, ""))
                            known_resource_keys.add(key)
                i += len(mod_lines)
                continue

            m = block_re.match(lines[i])
            if m:
                block_kind, resource_type, name = m.groups()
                
                # Skip data blocks - they are references to existing resources, not deployable resources
                if block_kind == "data":
                    i += 1
                    continue
                
                # Validate resource name - skip invalid names that are likely references
                if not is_valid_azure_resource_name(name):
                    i += 1
                    continue
                
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
                known_resource_keys.add((resource_type, name))
            i += 1

    # Build lookup: "type.name" → resource
    res_lookup: Dict[str, Resource] = {
        f"{r.resource_type}.{r.name}": r for r, _ in resource_blocks
    }
    # known_resource_keys already populated above (including module-inferred resources)

    for manifest_resource in extract_kubernetes_manifest_resources(files, repo_path):
        key = (manifest_resource.resource_type, manifest_resource.name)
        if key in known_resource_keys:
            continue
        context.resources.append(manifest_resource)
        resource_blocks.append((manifest_resource, ""))  # Add to resource_blocks so parent assignment logic can find it
        known_resource_keys.add(key)

    # Infer provider for terraform_data and other meta-resources from file context
    # Group resources by file
    resources_by_file: Dict[str, list[Resource]] = {}
    for resource, _ in resource_blocks:
        file_path = resource.file_path or "unknown"
        if file_path not in resources_by_file:
            resources_by_file[file_path] = []
        resources_by_file[file_path].append(resource)
    
    # For each file, determine the dominant provider
    for file_path, file_resources in resources_by_file.items():
        # Count providers in this file
        provider_counts: Dict[str, int] = {}
        for res in file_resources:
            # Skip meta-resources for counting
            if res.resource_type in ('terraform_data', 'null_resource', 'random_id', 'random_string', 
                                      'random_password', 'time_sleep', 'time_rotating'):
                continue
            # Detect provider from resource type prefix
            for prefix, provider in [
                ("azurerm_", "azure"), ("azuread_", "azure"),
                ("aws_", "aws"), ("google_", "gcp"),
                ("alicloud_", "alicloud"), ("oci_", "oci"),
                ("tencentcloud_", "tencentcloud"), ("huaweicloud_", "huaweicloud")
            ]:
                if res.resource_type.startswith(prefix):
                    provider_counts[provider] = provider_counts.get(provider, 0) + 1
                    break
        
        # Determine dominant provider
        dominant_provider = "unknown"
        if provider_counts:
            dominant_provider = max(provider_counts, key=provider_counts.get)
        
        # Apply to meta-resources in this file
        for res in file_resources:
            if res.resource_type in ('terraform_data', 'null_resource', 'random_id', 'random_string',
                                      'random_password', 'time_sleep', 'time_rotating'):
                if not res.properties:
                    res.properties = {}
                res.properties["inferred_provider"] = dominant_provider

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

    def _is_unresolved_tf_expression(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        lower = text.lower()
        if "${" in lower or "}" in lower:
            return True
        prefixes = (
            "local.",
            "var.",
            "module.",
            "data.",
            "path.",
            "terraform.",
            "each.",
            "count.",
        )
        return lower.startswith(prefixes)

    def _is_world_source(value: object) -> bool:
        if value is None:
            return False
        text = str(value).lower()
        return any(token in text for token in ("0.0.0.0/0", "::/0", "\"*\"", " internet", "any"))

    # RFC1918 private ranges — never considered internet-facing
    _RFC1918_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                         "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                         "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                         "172.30.", "172.31.", "192.168.", "127.", "169.254.")
    _SPECIAL_PREFIXES = ("fd", "fe80", "::1")  # IPv6 private/link-local

    def _cidr_exposure_level(cidr_list: list[str]) -> str:
        """Classify a list of source CIDRs into an exposure tier.

        Returns:
          'open'       – contains 0.0.0.0/0 or ::/0  (fully internet exposed)
          'broad'      – contains a public CIDR with prefix ≤ /16 (broad internet exposure)
          'restricted' – contains at least one public CIDR but all are /17 or narrower
          'private'    – all CIDRs are RFC1918 / loopback / link-local
        """
        import ipaddress
        has_public = False
        has_broad_public = False
        for cidr in cidr_list:
            cidr = cidr.strip()
            if not cidr:
                continue
            if cidr in ("0.0.0.0/0", "::/0"):
                return "open"
            try:
                net = ipaddress.ip_network(cidr, strict=False)
                if net.is_private or net.is_loopback or net.is_link_local:
                    continue
                # It's a public address
                has_public = True
                # Broad: /16 or larger (prefix_length <= 16 means more hosts)
                if net.prefixlen <= 16:
                    has_broad_public = True
            except ValueError:
                # Can't parse — treat conservatively as potentially public
                has_public = True
        if has_broad_public:
            return "broad"
        if has_public:
            return "restricted"
        return "private"

    for resource, block_text in resource_blocks:
        props = resource.properties or {}
        attrs = _parse_simple_block_attributes(block_text)
        resource_type = resource.resource_type

        top_level_name_match = re.search(r'^\s*name\s*=\s*"([^"]+)"', block_text, re.MULTILINE)
        if top_level_name_match:
            name_value = top_level_name_match.group(1)
            # Only use resolved names, never local/var/module expressions.
            if not _is_unresolved_tf_expression(name_value):
                props["actual_name"] = name_value
        elif "name" in attrs and attrs["name"]:
            name_value = attrs["name"].strip('"').strip("'")
            # Only use resolved names, never local/var/module expressions.
            if not _is_unresolved_tf_expression(name_value):
                props["actual_name"] = name_value

        # For API operations, also extract operation_id
        if resource_type == "azurerm_api_management_api_operation":
            op_id_match = re.search(r'^\s*operation_id\s*=\s*"([^"]+)"', block_text, re.MULTILINE)
            if op_id_match:
                op_id_value = op_id_match.group(1)
                if not _is_unresolved_tf_expression(op_id_value):
                    props["actual_name"] = op_id_value
            elif "operation_id" in attrs and attrs["operation_id"]:
                op_id_value = attrs["operation_id"].strip('"').strip("'")
                if not _is_unresolved_tf_expression(op_id_value):
                    props["actual_name"] = op_id_value

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
            
            # Extract subnet reference to establish NIC→Subnet parent relationship
            nic_subnet_id = attrs.get("subnet_id")
            if nic_subnet_id:
                subnet_names = _extract_arm_resource_names(nic_subnet_id, "subnets")
                if subnet_names:
                    props["subnet_name"] = subnet_names[0]

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
            rule_type = str(attrs.get("type", "")).strip().lower()
            if rule_type == "ingress" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group rule ingress from internet")
                
                # Extract port information for better labeling
                from_port = attrs.get("from_port")
                to_port = attrs.get("to_port")
                protocol = attrs.get("protocol", "")
                
                if from_port:
                    props["from_port"] = str(from_port)
                if to_port:
                    props["to_port"] = str(to_port)
                if protocol:
                    props["protocol"] = str(protocol).lower()

        # AliCloud security group rule
        if resource_type == "alicloud_security_group_rule":
            rule_type = str(attrs.get("type", "")).strip().lower()
            if rule_type == "ingress" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group rule ingress from internet")
                
                # Extract port information for better labeling
                from_port = attrs.get("from_port")
                to_port = attrs.get("to_port")
                protocol = attrs.get("ip_protocol", "")
                
                if from_port:
                    props["from_port"] = str(from_port)
                if to_port:
                    props["to_port"] = str(to_port)
                if protocol:
                    props["protocol"] = str(protocol).lower()

        # GCP compute instance — external IP via access_config block
        if resource_type == "google_compute_instance":
            if re.search(r'access_config\s*\{', block_text, re.I):
                props["has_public_ip"] = "true"
                _append_signal(props, "compute instance has access_config (external/NAT IP)")
            if re.search(r'network_tier\s*=\s*"(PREMIUM|STANDARD)"', block_text, re.I):
                props["network_tier"] = "external"
            # Extract network_interface.network to link VM to its VPC
            ni_network_match = re.search(
                r'network_interface\s*\{[^}]*\bnetwork\s*=\s*(?:google_compute_network\.[A-Za-z0-9_]+\.name|"([^"]+)")',
                block_text, re.S | re.I,
            )
            ni_subnet_sym_match = re.search(
                r'network_interface\s*\{[^}]*\bsubnetwork\s*=\s*google_compute_subnetwork\.([A-Za-z0-9_-]+)',
                block_text, re.S | re.I,
            )
            if ni_subnet_sym_match:
                # subnetwork = google_compute_subnetwork.xxx.name — link to subnetwork
                props["subnet_ref_sym"] = ni_subnet_sym_match.group(1)
            elif ni_network_match:
                # May be a reference like google_compute_network.vpc.name or a literal "vm-vpc"
                ni_subnet_ref = re.search(
                    r'network_interface\s*\{[^}]*\bsubnetwork\s*=\s*(?:google_compute_subnetwork\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?|"([^"]+)")',
                    block_text, re.S | re.I,
                )
                if ni_subnet_ref and ni_subnet_ref.group(1):
                    props["vpc_ref"] = ni_subnet_ref.group(1)
                elif ni_network_match.group(1):
                    props["vpc_ref"] = ni_network_match.group(1)
                else:
                    # Resolve symbolic reference like google_compute_network.vpc.name
                    sym_match = re.search(r'network\s*=\s*google_compute_network\.([A-Za-z0-9_]+)', block_text, re.I)
                    if sym_match:
                        props["vpc_ref_sym"] = sym_match.group(1)  # terraform resource label


        # GCP Cloud Function — HTTP trigger makes it internet-accessible
        if resource_type == "google_cloudfunctions_function":
            # trigger_http present = HTTPS endpoint exposed
            if re.search(r'trigger_http\s*=\s*true', block_text, re.I):
                props["internet_access"] = "true"
                _append_signal(props, "cloud function has trigger_http=true")
            # https_trigger_url reference in block also signals HTTP trigger
            elif re.search(r'https_trigger_url', block_text, re.I):
                props["internet_access"] = "true"
                _append_signal(props, "cloud function exposes https_trigger_url")

        # GCP firewall rule
        if resource_type == "google_compute_firewall":
            # GCP default direction is INGRESS when the attribute is omitted
            direction = str(attrs.get("direction", "INGRESS")).strip().upper() or "INGRESS"
            
            # Extract source_ranges explicitly
            source_ranges_match = re.search(r'source_ranges\s*=\s*\[(.*?)\]', block_text, re.S | re.I)
            source_ranges = []
            if source_ranges_match:
                ranges_str = source_ranges_match.group(1)
                source_ranges = [r.strip().strip('"').strip("'") for r in ranges_str.split(',')]
                props["source_ranges"] = ",".join(source_ranges)

            if direction == "INGRESS" and source_ranges:
                exposure = _cidr_exposure_level(source_ranges)
                # Extract allowed ports for context regardless of exposure level
                allowed_blocks = re.findall(r'allowed\s*\{(.*?)\}', block_text, re.S | re.I)
                ports = []
                if allowed_blocks:
                    for allowed_body in allowed_blocks:
                        port_matches = re.findall(r'ports\s*=\s*\[(.*?)\]', allowed_body, re.S)
                        for port_match in port_matches:
                            ports.extend([p.strip().strip('"').strip("'") for p in port_match.split(',')])
                if ports:
                    props["ports"] = ",".join(ports)

                if exposure == "open":
                    props["internet_ingress_open"] = "true"
                    _append_signal(props, "firewall rule: ingress from 0.0.0.0/0")
                elif exposure == "broad":
                    props["broad_cidr_ingress"] = "true"
                    props["exposure_scope"] = ",".join(source_ranges)
                    _append_signal(props, f"firewall rule: ingress from broad public CIDR {','.join(source_ranges)}")
                elif exposure == "restricted":
                    props["restricted_ingress"] = "true"
                    props["exposure_scope"] = ",".join(source_ranges)
                    _append_signal(props, f"firewall rule: ingress restricted to {','.join(source_ranges)}")

        # Azure Network Security Rule
        if resource_type == "azurerm_network_security_rule":
            direction = str(attrs.get("direction", "")).strip().upper()
            access = str(attrs.get("access", "")).strip().lower()
            if direction == "INBOUND" and access == "allow" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "network security rule inbound from internet")
                
                # Extract port information for better labeling
                dst_port = attrs.get("destination_port_range", "")
                protocol = attrs.get("protocol", "")
                
                if dst_port:
                    props["destination_port_range"] = str(dst_port)
                if protocol:
                    props["protocol"] = str(protocol).lower()

        # Huawei security group rule
        if resource_type == "huaweicloud_networking_secgroup_rule":
            direction = str(attrs.get("direction", "")).strip().lower()
            if direction == "ingress" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group rule ingress from internet")
                
                # Extract port information for better labeling
                from_port = attrs.get("from_port")
                to_port = attrs.get("to_port")
                protocol = attrs.get("protocol", "")
                
                if from_port:
                    props["from_port"] = str(from_port)
                if to_port:
                    props["to_port"] = str(to_port)
                if protocol:
                    props["protocol"] = str(protocol).lower()

        # Tencent Cloud security group rule
        if resource_type == "tencentcloud_security_group_rule":
            rule_type = str(attrs.get("type", "")).strip().lower()
            if rule_type == "ingress" and _is_world_source(block_text):
                props["internet_ingress_open"] = "true"
                _append_signal(props, "security group rule ingress from internet")
                
                # Extract port information for better labeling
                from_port = attrs.get("from_port")
                to_port = attrs.get("to_port")
                protocol = attrs.get("protocol", "")
                
                if from_port:
                    props["from_port"] = str(from_port)
                if to_port:
                    props["to_port"] = str(to_port)
                if protocol:
                    props["protocol"] = str(protocol).lower()

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

        # AWS RDS: extract SG refs from vpc_security_group_ids
        if resource_type in {"aws_db_instance", "aws_rds_cluster"}:
            sg_refs = _extract_tf_reference_labels(attrs.get("vpc_security_group_ids"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        # AWS ECS: security_groups lives inside network_configuration block
        if resource_type in {"aws_ecs_service", "aws_ecs_task_definition"}:
            net_cfg = attrs.get("network_configuration") or attrs.get("network_configuration.security_groups")
            sg_refs = _extract_tf_reference_labels(net_cfg, "aws_security_group") if net_cfg else []
            if not sg_refs:
                # Fallback: some parsers flatten it as security_groups directly
                sg_refs = _extract_tf_reference_labels(attrs.get("security_groups"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        # AWS Autoscaling / Launch Config / Launch Template
        if resource_type in {"aws_autoscaling_group", "aws_launch_configuration", "aws_launch_template"}:
            sg_refs = _extract_tf_reference_labels(attrs.get("security_groups"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        # AWS Lambda (VPC-attached functions)
        if resource_type == "aws_lambda_function":
            vpc_cfg = attrs.get("vpc_config") or attrs.get("vpc_config.security_group_ids")
            sg_refs = _extract_tf_reference_labels(vpc_cfg, "aws_security_group") if vpc_cfg else []
            if not sg_refs:
                sg_refs = _extract_tf_reference_labels(attrs.get("security_group_ids"), "aws_security_group")
            if sg_refs:
                props["security_group_refs"] = sg_refs

        if resource_type in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine"}:
            # First try ARM path format: /networkInterfaces/name
            nic_names = _extract_arm_resource_names(attrs.get("network_interface_ids"), "networkInterfaces")
            if not nic_names:
                # Fall back to Terraform reference format: azurerm_network_interface.LABEL.id
                ni_ids_raw = str(attrs.get("network_interface_ids") or "")
                nic_names = re.findall(r'azurerm_network_interface\.([A-Za-z0-9_-]+)', ni_ids_raw)
            if nic_names:
                props["network_interface_names"] = nic_names
        
        # Extract parent APIM instance name for API resources (supports shared/external APIM)
        if resource_type in ("azurerm_api_management_api", "azurerm_api_management_api_operation", 
                             "azurerm_api_management_product", "azurerm_api_management_subscription"):
            apim_name_raw = attrs.get("api_management_name")
            if apim_name_raw:
                # Extract actual value (could be var.name, data.ref, or literal)
                apim_name_str = str(apim_name_raw).strip().strip('"').strip("'")
                props["api_management_instance_name"] = apim_name_str
                
                # Track as shared resource if it's a variable/data reference
                if apim_name_str.startswith("var.") or apim_name_str.startswith("data."):
                    props["shared_resource_reference"] = "azurerm_api_management"
                    props["shared_resource_identifier"] = apim_name_str

        resource.properties = props

    # Second pass: resolve parent references
    no_generic_parent_types = {
        "aws_lb",
        "aws_alb",
        "aws_elb",
        "azurerm_lb",
        "azurerm_application_gateway",
        # VMs are compute tier roots, not children of NICs (even though referenced)
        "azurerm_virtual_machine",
        "azurerm_linux_virtual_machine",
        "azurerm_windows_virtual_machine",
        "aws_instance",
        "aws_launch_template",
    }

    # Parent preference order for resources with multiple valid parent types.
    # When multiple valid parent types exist in a resource's references, the first
    # matching type in this list is used. This ensures consistent parent assignment.
    parent_preference_order = {
        "aws_api_gateway_integration": ["aws_api_gateway_method", "aws_api_gateway_rest_api"],
        "aws_api_gateway_method": ["aws_api_gateway_resource", "aws_api_gateway_rest_api"],
        "aws_api_gateway_method_response": ["aws_api_gateway_method"],
        "aws_api_gateway_integration_response": ["aws_api_gateway_integration"],
        "azurerm_network_interface": ["azurerm_subnet", "azurerm_virtual_machine"],
        "azurerm_public_ip": ["azurerm_virtual_machine", "azurerm_lb"],
        "aws_eip": ["aws_instance", "aws_lb"],
    }

    for resource, block_text in resource_blocks:
        if resource.parent:
            continue

        ref_candidates: list[str] = []
        for ref_match in ref_re.finditer(block_text):
            ref_key = ref_match.group(1)
            if ref_key in res_lookup and res_lookup[ref_key] is not resource:
                ref_candidates.append(ref_key)
        # data.<resource_type>.<block_label>.attr references are consumed by ref_re
        # as `data.<resource_type>` which is never in res_lookup. Scan explicitly.
        for data_match in data_ref_re.finditer(block_text):
            ref_key = f"{data_match.group(1)}.{data_match.group(2)}"
            if ref_key in res_lookup and res_lookup[ref_key] is not resource and ref_key not in ref_candidates:
                ref_candidates.append(ref_key)

        preferred_parent_type = parent_type_map.get(resource.resource_type)

        def _parent_candidate_for_type(parent_tf_type: str) -> Optional[str]:
            parent_types = [part.strip() for part in parent_tf_type.split("|") if part.strip()]
            if not parent_types:
                return None
            for candidate_parent_type in parent_types:
                for ref_key in ref_candidates:
                    parts = ref_key.split(".", 1)
                    if len(parts) == 2 and parts[0] == candidate_parent_type:
                        return ref_key
                # Same-file fallback for unresolved references (keeps deterministic local grouping)
                for candidate, _ in resource_blocks:
                    if (
                        candidate is not resource
                        and candidate.resource_type == candidate_parent_type
                        and candidate.file_path == resource.file_path
                    ):
                        return f"{candidate.resource_type}.{candidate.name}"
                # Repo-wide fallback when same-file parent is not present
                for candidate, _ in resource_blocks:
                    if candidate is not resource and candidate.resource_type == candidate_parent_type:
                        return f"{candidate.resource_type}.{candidate.name}"
            return None

        # Special handling: AWS security group rules are control/filtering layers
        # Rule hierarchy: SecurityGroup → Rule → [all resources using that SG]
        # The rule becomes a control point showing what traffic is allowed/denied
        # (Process BEFORE preferred_parent_type so we can skip that logic for rules)
        if resource.resource_type == "aws_security_group_rule":
            props = resource.properties or {}
            # Extract security group ID/name reference from the rule
            sg_ref = props.get("security_group_id")
            if not sg_ref:
                # Try to extract from block_text if not in props
                sg_match = re.search(r'security_group_id\s*=\s*["\']?([^"\'\s,}]+)', block_text)
                if sg_match:
                    sg_ref = sg_match.group(1).strip()
            
            if sg_ref:
                # Find the security group name referenced by this rule
                # Handle both direct name references (sg_name) and data source references (data.aws_security_group.default.id)
                sg_name = None
                sg_resource_type = "aws_security_group"
                if "." in sg_ref:
                    # data.aws_security_group.default.id → extract "default"
                    parts = sg_ref.split(".")
                    if len(parts) >= 3 and parts[0] == "data" and parts[1] == "aws_security_group":
                        sg_name = parts[2]
                else:
                    sg_name = sg_ref
                
                if sg_name:
                    # Try to find the security group resource in resource_blocks
                    sg_found = False
                    for candidate, _ in resource_blocks:
                        if candidate.resource_type == "aws_security_group" and candidate.name == sg_name:
                            resource.parent = f"{candidate.resource_type}.{candidate.name}"
                            sg_found = True
                            break
                    
                    # If SG not found as explicit resource, use synthetic parent reference
                    # This handles data sources like data.aws_security_group.default
                    if not sg_found:
                        resource.parent = f"{sg_resource_type}.{sg_name}"
                    
                    # Store the SG name in properties for diagram rendering (rendering transformation will use this)
                    if not props:
                        props = {}
                    props["_sg_name"] = sg_name
                    resource.properties = props
            
            # If parent was set, skip to next resource
            if resource.parent:
                continue

        # If type metadata defines a preferred parent, only accept that parent type.
        if preferred_parent_type:
            resolved_parent = _parent_candidate_for_type(preferred_parent_type)
            if resolved_parent:
                resource.parent = resolved_parent
            continue
        
        # Check if resource has custom parent preference order
        # This handles multi-parent resources by trying parents in order
        if resource.resource_type in parent_preference_order:
            preferred_types = parent_preference_order[resource.resource_type]
            for parent_type in preferred_types:
                # Try to find parent of this type in ref_candidates
                for ref_key in ref_candidates:
                    parts = ref_key.split(".", 1)
                    if len(parts) == 2 and parts[0] == parent_type:
                        resource.parent = ref_key
                        break
                if resource.parent:
                    break
            if resource.parent:
                continue
        
        # Special handling: NSG associations should be children of their NIC (for threat model tracing)
        # This enables diagram generation to trace NSG → NIC → VM security boundaries
        if resource.resource_type == "azurerm_network_interface_security_group_association" and not resource.parent:
            props = resource.properties or {}
            nic_name = props.get("network_interface_name")
            if nic_name:
                # Look for the NIC resource by name
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == "azurerm_network_interface" and candidate.name == nic_name:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break

        # Special handling: NICs should be children of their subnet
        if resource.resource_type == "azurerm_network_interface" and not resource.parent:
            props = resource.properties or {}
            subnet_name = props.get("subnet_name")
            if subnet_name:
                # Look for the subnet resource by name
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == "azurerm_subnet" and candidate.name == subnet_name:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break

        # Special handling: Kubernetes resources should be children of their namespace
        if resource.resource_type.startswith("kubernetes_") and not resource.parent:
            props = resource.properties or {}
            namespace_name = props.get("k8s_namespace")
            if namespace_name:
                # Look for the namespace resource by name
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == "kubernetes_namespace" and candidate.name == namespace_name:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break

        # Special handling: Kubernetes namespaces should be children of their EKS cluster
        if resource.resource_type == "kubernetes_namespace" and not resource.parent:
            # Find the EKS cluster (heuristic: usually exactly one per scan)
            for candidate, _ in resource_blocks:
                if candidate.resource_type == "aws_eks_cluster":
                    resource.parent = f"{candidate.resource_type}.{candidate.name}"
                    break

        # GCP compute instance: parent via subnetwork reference (network_interface.subnetwork)
        if resource.resource_type == "google_compute_instance" and not resource.parent:
            props = resource.properties or {}
            subnet_sym = props.get("subnet_ref_sym")
            vpc_sym = props.get("vpc_ref_sym")
            if subnet_sym:
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == "google_compute_subnetwork" and candidate.name == subnet_sym:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break
            elif vpc_sym:
                for candidate, _ in resource_blocks:
                    if candidate.resource_type == "google_compute_network" and candidate.name == vpc_sym:
                        resource.parent = f"{candidate.resource_type}.{candidate.name}"
                        break

        # Otherwise, use the first explicit resource reference unless this is a top-level edge
        # component where generic reference-parenting frequently misclassifies ownership.
        if resource.resource_type in no_generic_parent_types:
            continue

        if ref_candidates:
            resource.parent = ref_candidates[0]

    # -------------------------------------------------------------------------
    # Post-loop: Azure VM parent via NIC chain (requires NIC parents already resolved)
    # -------------------------------------------------------------------------
    for resource, _ in resource_blocks:
        if resource.resource_type not in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine"}:
            continue
        if resource.parent:
            continue
        props = resource.properties or {}
        nic_names_raw = props.get("network_interface_names")
        if nic_names_raw is None:
            nic_names = []
        elif isinstance(nic_names_raw, list):
            nic_names = [str(x) for x in nic_names_raw if str(x).strip()]
        else:
            nic_names = [str(nic_names_raw)]
        for nic_name in nic_names:
            for candidate, _ in resource_blocks:
                if candidate.resource_type == "azurerm_network_interface" and candidate.name == nic_name:
                    if candidate.parent:
                        resource.parent = candidate.parent  # inherit NI's subnet parent
                    break
            if resource.parent:
                break

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

        if resource.resource_type == "alicloud_security_group_rule" and _is_prop_true(props, "internet_ingress_open"):
            parent_props["internet_ingress_open"] = "true"
            _append_signal(parent_props, "security group ingress rule allows internet")

        if resource.resource_type == "huaweicloud_networking_secgroup_rule" and _is_prop_true(props, "internet_ingress_open"):
            parent_props["internet_ingress_open"] = "true"
            _append_signal(parent_props, "security group ingress rule allows internet")

        if resource.resource_type == "tencentcloud_security_group_rule" and _is_prop_true(props, "internet_ingress_open"):
            parent_props["internet_ingress_open"] = "true"
            _append_signal(parent_props, "security group ingress rule allows internet")

        # API Gateway operations/methods/routes are ingress points - mark them and their parents
        api_operation_types = {
            "azurerm_api_management_api_operation",
            "aws_api_gateway_method",
            "aws_api_gateway_resource",
            "aws_apigatewayv2_route",
            "google_api_gateway_gateway",
            "oci_apigateway_deployment",
            "alicloud_api_gateway_api",
        }
        
        if resource.resource_type in api_operation_types:
            # Mark the operation itself as an ingress point
            props["is_ingress_endpoint"] = "true"
            props["ingress_type"] = "api_operation"
            _mark_internet_access(props, f"API operation/endpoint exposed via {resource.resource_type}")
            
            # Propagate to parent API gateway
            if parent:
                _mark_internet_access(parent_props, f"API gateway hosts public operations")
                parent.properties = parent_props
        
        # Load balancer listeners are also ingress points
        lb_listener_types = {
            "aws_lb_listener",
            "aws_alb_listener",
            "azurerm_lb_rule",
        }
        
        if resource.resource_type in lb_listener_types:
            props["is_ingress_endpoint"] = "true"
            props["ingress_type"] = "load_balancer_listener"
            if parent:
                _mark_internet_access(parent_props, f"Load balancer has configured listeners")
                parent.properties = parent_props

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

        # monitors — diagnostic setting source_resource_id / APIM logger → App Insights
        (re.compile(
            r'(?:target_resource_id|instrumentation_key|workspace_id|log_analytics_workspace_id)\s*=\s*'
            r'(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.',
            re.I,
        ),
         RelationshipType.MONITORS, False),

        # depends_on (telemetry/logger wiring) — api_management_logger_id, logger_id
        (re.compile(
            r'(?:api_management_logger_id|logger_id)\s*=\s*(?:[a-z][a-z0-9_]+)\.([a-z][a-z0-9_\-]+)\.',
            re.I,
        ),
         RelationshipType.DEPENDS_ON, False),

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

            # opengrep-first: endpoint/resource detection should be handled by opengrep rules.
            # If opengrep did not extract a relationship, fallback to generic unresolved host.

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
    
    # -------------------------------------------------------------------------
    # Detect AKS workloads from Skaffold configuration
    # -------------------------------------------------------------------------
    try:
        from skaffold_parser import extract_skaffold_workloads

        skaffold_workloads = extract_skaffold_workloads(repo_path)
        if skaffold_workloads:
            print(f"[+] Found {len(skaffold_workloads)} Skaffold workloads")

            # If we have k8s workloads but no explicit AKS cluster resource in this repo,
            # create an inferred cluster parent so assets/diagrams can nest correctly.
            has_explicit_cluster = any(
                r.resource_type == 'azurerm_kubernetes_cluster'
                for r in context.resources
            )
            inferred_cluster_name = None
            if not has_explicit_cluster:
                inferred_cluster_name = _ensure_inferred_resource(
                    'azurerm_kubernetes_cluster',
                    canonical_name=f"{repo_name}-aks-cluster",
                    source_file='skaffold.yaml',
                    line_number=1,
                )

            # Pre-compute APIM service_url → workload name mapping so that the
            # routing logic below can use the explicit backend URL rather than
            # relying solely on name prefix matching (which is too broad when one
            # API name is a prefix of another, e.g. "aks-helloworld" vs
            # "aks-helloworld-dr-testapp-api").
            _tf_locals = _extract_tf_locals(
                [f for f in files if hasattr(f, "suffix") and f.suffix == ".tf"]
            )
            _svc_url_re = re.compile(r'^\s*service_url\s*=\s*"([^"]*)"', re.M)
            _apim_resolved_service: Dict[str, Optional[str]] = {}
            for _res, _btext in resource_blocks:
                if _res.resource_type == "azurerm_api_management_api":
                    _url_m = _svc_url_re.search(_btext)
                    _apim_resolved_service[_res.name] = (
                        _resolve_apim_service_name(_url_m.group(1), _tf_locals)
                        if _url_m
                        else None
                    )

            # Add workloads as resources
            for wl in skaffold_workloads:
                # Create a resource for the AKS workload
                workload_resource = Resource(
                    name=wl.name,
                    resource_type=wl.resource_type,
                    file_path="skaffold.yaml",
                    line_number=1,
                    properties={
                        "image": wl.image_name,
                        "dockerfile": wl.dockerfile,
                        "helm_chart": wl.helm_chart,
                        "namespace": wl.namespace,
                        "replicas": wl.replicas,
                        "health_endpoint": wl.health_check_path,
                        "health_port": wl.health_check_port,
                        "workload_type": "aks_workload",
                        "is_ingress_endpoint": wl.health_check_path is not None,
                    }
                )
                # Link inferred cluster → workload
                if inferred_cluster_name:
                    workload_resource.parent = f"azurerm_kubernetes_cluster.{inferred_cluster_name}"
                context.resources.append(workload_resource)

                # Infer connections from API Gateway to API/service workloads.
                # Match kubernetes_service workloads (API services deployed via cbi-api chart)
                # and any workload whose name suggests it is an API backend (contains "api"
                # but is not a background worker/listener).
                _worker_keywords = ("queuelistener", "worker", "consumer", "subscriber", "job", "cron", "batch")
                is_api_workload = (
                    wl.resource_type == "kubernetes_service"
                    or ("api" in wl.name and not any(kw in wl.name for kw in _worker_keywords))
                )
                if is_api_workload:
                    # Find API Gateway resources (Azure, AWS, GCP)
                    api_gateway_types = [
                        # Azure
                        "azurerm_api_management_api",
                        # AWS
                        "aws_api_gateway",
                        "aws_apigatewayv2_api",
                        "aws_api_gateway_rest_api",
                        # GCP
                        "google_api_gateway_api",
                        "google_api_gateway_gateway",
                    ]

                    for resource in context.resources:
                        if resource.resource_type in api_gateway_types:
                            wl_clean = wl.name.lower()
                            apim_clean = resource.name.replace("_", "-").lower()

                            # --- Primary: service_url-based exact matching ---
                            # When the APIM resource has a resolvable service_url (e.g.
                            # "https://${env}-${local.app_name}.${domain}"), use the
                            # resolved service name for an exact match.  This is the most
                            # accurate signal and prevents e.g. "aks_helloworld" APIM
                            # from accidentally routing to "aks-helloworld-dr-testapp-api"
                            # just because the workload name starts with "aks-helloworld-".
                            resolved_svc = _apim_resolved_service.get(resource.name)
                            if resolved_svc is not None:
                                if wl_clean != resolved_svc:
                                    continue  # this APIM routes to a different workload

                            else:
                                # --- Fallback: name-based prefix matching ---
                                # Only match when the APIM API name appears as a prefix of
                                # the workload name (normalising underscores/hyphens).
                                prefix_match = (
                                    wl_clean == apim_clean
                                    or wl_clean.startswith(apim_clean + "-")
                                )
                                if not prefix_match:
                                    # Skip if another APIM has a better (more specific)
                                    # prefix match for this workload.
                                    better_match_exists = any(
                                        (wl_clean == r2.name.replace("_", "-").lower()
                                         or wl_clean.startswith(r2.name.replace("_", "-").lower() + "-"))
                                        for r2 in context.resources
                                        if r2.resource_type in api_gateway_types and r2 is not resource
                                    )
                                    if better_match_exists:
                                        continue

                            # Create connection: API Gateway → Kubernetes API workload
                            conn = Connection(
                                source=f"{resource.resource_type}.{resource.name}",
                                target=f"{wl.resource_type}.{wl.name}",
                                connection_type="routes_to_backend"
                            )
                            context.connections.append(conn)

                            # Also create relationship
                            rel = Relationship(
                                source_type=resource.resource_type,
                                source_name=resource.name,
                                target_type=wl.resource_type,
                                target_name=wl.name,
                                relationship_type=RelationshipType.ROUTES_INGRESS_TO,
                                notes="Inferred from API Gateway and Skaffold API workload"
                            )
                            context.relationships.append(rel)

                # Infer connections from messaging services to queue listener/worker workloads.
                # Direction: workload → messaging service (the workload DEPENDS ON the bus).
                if any(keyword in wl.name for keyword in ["queuelistener", "worker", "consumer", "subscriber"]) or \
                   any(keyword in (wl.helm_chart or "") for keyword in ["background-worker", "consumer", "worker"]):
                    # Find messaging resources (Service Bus, SQS/SNS, Pub/Sub)
                    messaging_types = [
                        # Azure Service Bus
                        "azurerm_servicebus_namespace",
                        "azurerm_servicebus_queue",
                        "azurerm_servicebus_topic",
                        "azurerm_servicebus_subscription",
                        # AWS SQS/SNS/Kinesis
                        "aws_sqs_queue",
                        "aws_sns_topic",
                        "aws_sns_subscription",
                        "aws_kinesis_stream",
                        "aws_mq_broker",
                        # GCP Pub/Sub
                        "google_pubsub_topic",
                        "google_pubsub_subscription",
                    ]
                    
                    for resource in context.resources:
                        if resource.resource_type in messaging_types:
                            # Direction: workload → messaging service (workload consumes the bus).
                            # Using source=workload / target=messaging keeps the arrow in the
                            # semantically correct direction: listener depends on the service bus.
                            conn = Connection(
                                source=f"{wl.resource_type}.{wl.name}",
                                target=f"{resource.resource_type}.{resource.name}",
                                connection_type="depends_on"
                            )
                            context.connections.append(conn)
                            
                            # Also create relationship (workload depends_on messaging service)
                            rel = Relationship(
                                source_type=wl.resource_type,
                                source_name=wl.name,
                                target_type=resource.resource_type,
                                target_name=resource.name,
                                relationship_type=RelationshipType.DEPENDS_ON,
                                notes="Inferred from Skaffold worker workload and messaging service"
                            )
                            context.relationships.append(rel)
                
                print(f"  [+] Added Kubernetes workload: {wl.name} ({wl.resource_type})")
    except ImportError:
        pass  # Skaffold parser not available
    except Exception as e:
        print(f"[WARN] Failed to parse Skaffold: {e}")

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
