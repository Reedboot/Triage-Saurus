#!/usr/bin/env python3
"""Fast, non-security context discovery for a local repository (writes summary/knowledge).

This script is the "Phase 1 - Context Discovery" step described in:
- Agents/ContextDiscoveryAgent.md
- Templates/Workflows.md (Repository Scan Flow - Step 3)

It performs quick file-based discovery (no network, no git commands by default) and writes:
- Output/Summary/Repos/<RepoName>.md
- Output/Knowledge/Repos.md (repo inventory + repo root directory)

Usage:
  python3 Scripts/discover_repo_context.py /abs/path/to/repo
  python3 Scripts/discover_repo_context.py /abs/path/to/repo --repos-root /abs/path/to/repos

Exit codes:
  0 success
  2 invalid arguments
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

from output_paths import OUTPUT_KNOWLEDGE_DIR, OUTPUT_SUMMARY_DIR
from markdown_validator import validate_markdown_file


SKIP_DIR_NAMES = {
    ".git",
    ".terraform",
    "node_modules",
    "bin",
    "obj",
    "dist",
    "build",
    "target",
    "vendor",
    ".venv",
    "venv",
}

CODE_EXTS = {".cs", ".fs", ".vb", ".go", ".py", ".js", ".ts", ".java", ".kt", ".rb", ".php"}
CFG_EXTS = {".yml", ".yaml", ".json", ".toml", ".ini", ".config", ".env"}
IAC_EXTS = {".tf", ".tfvars", ".bicep"}
DOC_EXTS = {".md", ".txt"}
SQL_EXTS = {".sql"}

INGRESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HTTP server bind/listen", re.compile(r"\b(Listen|ListenAndServe|app\.Run|http\.ListenAndServe|BindAddress)\b")),
    ("API endpoints defined", re.compile(r"(@app\.route|@RestController|@RequestMapping|app\.(get|post|put|delete)\(|Map(Get|Post|Put|Delete)\b|Route\[)")),
    ("Ports/exposed", re.compile(r"\b(port:|PORT=|--port\b|EXPOSE\s+\d+)\b")),
    ("APIM integration", re.compile(r"\b(azure-api\.net|ApiManagementUrl|ApiManagerBaseUrl)\b", re.IGNORECASE)),
    ("Kubernetes Ingress", re.compile(r"^\s*kind:\s*Ingress\s*$", re.IGNORECASE | re.MULTILINE)),
    ("Kubernetes Service", re.compile(r"^\s*kind:\s*Service\s*$", re.IGNORECASE | re.MULTILINE)),
]

EGRESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HTTP client usage", re.compile(r"\b(HttpClient|requests\.(get|post)|axios|fetch\()\b")),
    ("Database connection strings", re.compile(r"\b(Server=|Host=|Data Source=|ConnectionString|DATABASE_URL)\b", re.IGNORECASE)),
    ("Messaging/queues", re.compile(r"\b(ServiceBus|EventHub|Kafka|RabbitMQ|SQS|PubSub)\b", re.IGNORECASE)),
    ("Cloud storage endpoints", re.compile(r"\b(blob\.core\.windows\.net|s3\.amazonaws\.com|storage\.googleapis\.com)\b", re.IGNORECASE)),
]

PROVIDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Azure", re.compile(r"\bazurerm\b|provider\s+\"azurerm\"", re.IGNORECASE)),
    ("AWS", re.compile(r"\baws\b|provider\s+\"aws\"", re.IGNORECASE)),
    ("GCP", re.compile(r"\bgoogle\b|provider\s+\"google\"", re.IGNORECASE)),
]

CI_MARKERS: list[tuple[str, str]] = [
    ("GitHub Actions", ".github/workflows"),
    ("Azure Pipelines", "azure-pipelines.yml"),
    ("GitLab CI", ".gitlab-ci.yml"),
]

LANG_MARKERS: list[tuple[str, list[str]]] = [
    ("Terraform", ["*.tf", "*.tfvars"]),
    ("Bicep", ["*.bicep"]),
    ("C#", ["*.csproj", "*.cs"]),
    ("F#", ["*.fsproj", "*.fs"]),
    ("VB.NET", ["*.vbproj", "*.vb"]),
    (".NET Solution", ["*.sln"]),
    ("TypeScript", ["tsconfig.json", "*.ts"]),
    ("JavaScript", ["*.js", "*.jsx"]),
    ("Node.js", ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]),
    ("Python", ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py"]),
    ("Go", ["go.mod", "go.sum"]),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"]),
    ("Kotlin", ["*.kt", "*.kts"]),
    ("Kubernetes", ["kustomization.yaml", "Chart.yaml", "values.yaml", "deployment.yaml", "service.yaml", "ingress.yaml"]),
    ("Skaffold", ["skaffold.yaml"]),
    ("Tilt", ["Tiltfile"]),
    ("Containers", ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]),
]


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


@dataclass(frozen=True)
class Evidence:
    label: str
    path: str
    line: int | None = None
    excerpt: str | None = None

    def fmt(self) -> str:
        if self.line is None:
            return f"- 💡 {self.label} — evidence: `{self.path}`"
        if self.excerpt:
            return f"- 💡 {self.label} — evidence: `{self.path}:{self.line}:{self.excerpt}`"
        return f"- 💡 {self.label} — evidence: `{self.path}:{self.line}`"


def iter_files(repo: Path, *, max_depth: int = 12) -> list[Path]:
    repo = repo.resolve()
    out: list[Path] = []
    for root, dirs, files in os.walk(repo):
        rel_depth = len(Path(root).relative_to(repo).parts)
        if rel_depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            out.append(Path(root) / name)
    return out


def rel(repo: Path, p: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return str(p)


def _matches_marker(path_str: str, marker: str) -> bool:
    if "*" not in marker:
        return path_str.endswith("/" + marker) or path_str == marker
    if marker.startswith("*."):
        return path_str.endswith(marker[1:])
    return False


def detect_languages(files: list[Path], repo: Path) -> list[tuple[str, str]]:
    rels = [rel(repo, p) for p in files]
    detected: list[tuple[str, str]] = []
    for lang, markers in LANG_MARKERS:
        evidence = None
        for m in markers:
            for r in rels:
                if _matches_marker(r, m):
                    evidence = r
                    break
            if evidence:
                break
        if evidence:
            detected.append((lang, evidence))
    return detected


def detect_ci(repo: Path) -> str:
    """Detect CI/CD platform (simple version)."""
    for name, marker in CI_MARKERS:
        if (repo / marker).exists():
            return name
    # Common Azure Pipelines variants in sample repos.
    if any(repo.glob("azure-pipelines*.yml")) or any(repo.glob("azure-pipelines*.yaml")):
        return "Azure Pipelines"
    # Check for .vsts-ci.yml and .azurepipelines/*
    if (repo / ".vsts-ci.yml").exists() or (repo / ".azurepipelines").exists():
        return "Azure Pipelines"
    return "Unknown"


def parse_ci_cd_details(repo: Path) -> dict[str, any]:
    """Extract detailed CI/CD information from pipeline files."""
    info: dict[str, str] = {"platform": "Unknown", "files": []}
    
    # Check for Azure Pipelines
    patterns = [".vsts-ci.yml", "azure-pipelines*.yml", "azure-pipelines*.yaml"]
    for pattern in patterns:
        matches = list(repo.glob(pattern))
        if matches:
            info["platform"] = "Azure Pipelines"
            info["files"] = [p.name for p in matches[:3]]
            break
    
    # Check .azurepipelines directory
    azpipelines = repo / ".azurepipelines"
    if azpipelines.exists():
        yml_files = list(azpipelines.glob("*.yml")) + list(azpipelines.glob("*.yaml"))
        if yml_files:
            info["platform"] = "Azure Pipelines"
            info["files"] = [f".azurepipelines/{p.name}" for p in yml_files[:3]]
    
    # Check for GitHub Actions
    gh_workflows = repo / ".github" / "workflows"
    if gh_workflows.exists():
        yml_files = list(gh_workflows.glob("*.yml")) + list(gh_workflows.glob("*.yaml"))
        if yml_files:
            info["platform"] = "GitHub Actions"
            info["files"] = [p.name for p in yml_files[:3]]
    
    # Check for GitLab CI
    if (repo / ".gitlab-ci.yml").exists():
        info["platform"] = "GitLab CI"
        info["files"] = [".gitlab-ci.yml"]
    
    return info


def _scan_text_file(repo: Path, path: Path, patterns: list[tuple[str, re.Pattern[str]]], *, limit: int) -> list[Evidence]:
    out: list[Evidence] = []
    rp = rel(repo, path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for label, rx in patterns:
        if len(out) >= limit:
            break
        for m in rx.finditer(text):
            if len(out) >= limit:
                break
            line = text.count("\n", 0, m.start()) + 1
            # Keep excerpt short and single-line.
            excerpt = text.splitlines()[line - 1].strip() if 0 < line <= len(text.splitlines()) else None
            out.append(Evidence(label=label, path=rp, line=line, excerpt=excerpt))
            break  # one hit per pattern per file is enough for context
    return out


def detect_ingress_from_code(files: list[Path], repo: Path) -> dict[str, any]:
    """Detect ingress patterns from application code (headers, middleware, etc.)."""
    ingress_info = {"type": None, "evidence": []}
    
    # Ingress header patterns that indicate specific services
    patterns = [
        ("Application Gateway", re.compile(r'X-Original-Host|X-Forwarded-Host.*appgw|ApplicationGateway', re.IGNORECASE)),
        ("Azure Front Door", re.compile(r'X-Azure-FDID|X-FD-HealthProbe|X-Azure-SocketIP', re.IGNORECASE)),
        ("API Management", re.compile(r'Ocp-Apim-Subscription-Key|X-APIM-Request-Id', re.IGNORECASE)),
        ("Load Balancer", re.compile(r'X-Forwarded-For|X-Real-IP', re.IGNORECASE)),
    ]
    
    for p in files:
        if p.suffix.lower() not in {".cs", ".js", ".ts", ".json", ".config", ".yaml", ".yml"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        for ingress_type, pattern in patterns:
            if pattern.search(text):
                if ingress_info["type"] is None or ingress_type == "Application Gateway":
                    ingress_info["type"] = ingress_type
                    ingress_info["evidence"].append(f"{p.relative_to(repo)}")
                    break
    
    return ingress_info


def detect_cloud_provider(files: list[Path], repo: Path) -> list[str]:
    providers: set[str] = set()
    for p in files:
        if p.suffix not in IAC_EXTS and p.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, rx in PROVIDER_PATTERNS:
            if rx.search(text):
                providers.add(name)
    return sorted(providers)


def infer_repo_type(langs: list[str], repo_name: str) -> str:
    n = repo_name.lower()
    if "Terraform" in langs or "Bicep" in langs or any(k in n for k in ["terraform", "iac", "infra", "infrastructure", "platform", "modules"]):
        return "Infrastructure"
    return "Application"


def repo_purpose(repo: Path, repo_name: str) -> tuple[str, Evidence | None]:
    for name in ["README.md", "readme.md"]:
        p = repo / name
        if p.is_file():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                break
            for line in lines:
                s = line.strip()
                if s and not s.startswith("#"):
                    if s.startswith("- "):
                        s = s[2:].strip()
                    return s[:160], Evidence(label="README purpose hint", path=rel(repo, p), line=lines.index(line) + 1, excerpt=s)
            return f"{repo_name} repository", Evidence(label="README present", path=rel(repo, p))
    return f"{repo_name} repository", None


TF_RESOURCE_RE = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.IGNORECASE | re.MULTILINE)


def detect_terraform_resources(files: list[Path], repo: Path) -> set[str]:
    types: set[str] = set()
    for p in files:
        if p.suffix.lower() != ".tf":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in TF_RESOURCE_RE.finditer(text):
            types.add(m.group(1))
    return types


def detect_hosting_from_terraform(files: list[Path], repo: Path) -> dict[str, any]:
    """Detect where the application is hosted from Terraform resources."""
    hosting: dict[str, any] = {"type": None, "evidence": []}
    
    for p in files:
        if p.suffix.lower() != ".tf":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Check for different hosting types (order matters - most specific first)
        if re.search(r'resource\s+"azurerm_windows_web_app"', text):
            hosting["type"] = "Windows App Service"
            hosting["evidence"].append(rel(repo, p))
        elif re.search(r'resource\s+"azurerm_linux_web_app"', text):
            hosting["type"] = "Linux App Service"
            hosting["evidence"].append(rel(repo, p))
        elif re.search(r'resource\s+"azurerm_kubernetes_cluster"', text):
            hosting["type"] = "AKS"
            hosting["evidence"].append(rel(repo, p))
        elif re.search(r'resource\s+"azurerm_container_app"', text):
            hosting["type"] = "Container Apps"
            hosting["evidence"].append(rel(repo, p))
        elif re.search(r'resource\s+"azurerm_function_app"', text):
            hosting["type"] = "Azure Functions"
            hosting["evidence"].append(rel(repo, p))
    
    return hosting


def detect_network_topology(files: list[Path], repo: Path) -> dict[str, any]:
    """Detect network topology from Terraform/Bicep IaC."""
    network_info = {
        "vnets": [],
        "subnets": [],
        "nsgs": [],
        "private_endpoints": [],
        "peerings": [],
        "evidence": []
    }
    
    for p in files:
        if p.suffix.lower() not in {".tf", ".bicep"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Virtual Networks
        vnet_matches = re.finditer(r'resource\s+"azurerm_virtual_network"\s+"([^"]+)"', text)
        for match in vnet_matches:
            vnet_name = match.group(1)
            # Try to extract address space
            addr_match = re.search(rf'resource\s+"azurerm_virtual_network"\s+"{vnet_name}".*?address_space\s*=\s*\[([^\]]+)\]', text, re.DOTALL)
            if addr_match:
                cidr = addr_match.group(1).strip().strip('"\'').split(',')[0].strip().strip('"\'')
                network_info["vnets"].append(f"{vnet_name} ({cidr})")
            else:
                network_info["vnets"].append(vnet_name)
            if rel(repo, p) not in network_info["evidence"]:
                network_info["evidence"].append(rel(repo, p))
        
        # Subnets
        subnet_matches = re.finditer(r'resource\s+"azurerm_subnet"\s+"([^"]+)"', text)
        for match in subnet_matches:
            subnet_name = match.group(1)
            # Try to extract address prefix
            addr_match = re.search(rf'resource\s+"azurerm_subnet"\s+"{subnet_name}".*?address_prefixes?\s*=\s*\[?"([^"\]]+)"', text, re.DOTALL)
            if addr_match:
                cidr = addr_match.group(1).strip()
                network_info["subnets"].append(f"{subnet_name} ({cidr})")
            else:
                network_info["subnets"].append(subnet_name)
            if rel(repo, p) not in network_info["evidence"]:
                network_info["evidence"].append(rel(repo, p))
        
        # Network Security Groups
        nsg_matches = re.finditer(r'resource\s+"azurerm_network_security_group"\s+"([^"]+)"', text)
        for match in nsg_matches:
            network_info["nsgs"].append(match.group(1))
            if rel(repo, p) not in network_info["evidence"]:
                network_info["evidence"].append(rel(repo, p))
        
        # Private Endpoints
        pe_matches = re.finditer(r'resource\s+"azurerm_private_endpoint"\s+"([^"]+)"', text)
        for match in pe_matches:
            network_info["private_endpoints"].append(match.group(1))
            if rel(repo, p) not in network_info["evidence"]:
                network_info["evidence"].append(rel(repo, p))
        
        # VNet Peerings
        peer_matches = re.finditer(r'resource\s+"azurerm_virtual_network_peering"\s+"([^"]+)"', text)
        for match in peer_matches:
            network_info["peerings"].append(match.group(1))
            if rel(repo, p) not in network_info["evidence"]:
                network_info["evidence"].append(rel(repo, p))
    
    # Deduplicate
    network_info["vnets"] = sorted(set(network_info["vnets"]))
    network_info["subnets"] = sorted(set(network_info["subnets"]))
    network_info["nsgs"] = sorted(set(network_info["nsgs"]))
    network_info["private_endpoints"] = sorted(set(network_info["private_endpoints"]))
    network_info["peerings"] = sorted(set(network_info["peerings"]))
    
    return network_info


def parse_dockerfiles(repo: Path) -> dict[str, any]:
    """Parse Dockerfiles to extract runtime information."""
    result = {
        "base_images": [],
        "exposed_ports": [],
        "user": None,
        "multi_stage": False,
        "healthcheck": False,
        "evidence": []
    }
    
    # Find all Dockerfiles
    dockerfile_patterns = ["Dockerfile", "Dockerfile.*", "*.Dockerfile"]
    dockerfiles = []
    for pattern in dockerfile_patterns:
        dockerfiles.extend(repo.glob(pattern))
        dockerfiles.extend(repo.glob(f"**/{pattern}"))
    
    for dockerfile in dockerfiles:
        try:
            text = dockerfile.read_text(encoding="utf-8", errors="replace")
            result["evidence"].append(rel(repo, dockerfile))
            
            # Extract base images (FROM statements)
            from_matches = re.findall(r'^FROM\s+([^\s]+)', text, re.MULTILINE | re.IGNORECASE)
            result["base_images"].extend(from_matches)
            if len(from_matches) > 1:
                result["multi_stage"] = True
            
            # Extract exposed ports
            expose_matches = re.findall(r'^EXPOSE\s+(\d+)', text, re.MULTILINE | re.IGNORECASE)
            result["exposed_ports"].extend(expose_matches)
            
            # Extract runtime user (last USER directive wins)
            user_matches = re.findall(r'^USER\s+([^\s]+)', text, re.MULTILINE | re.IGNORECASE)
            if user_matches:
                result["user"] = user_matches[-1]  # Last one wins
            
            # Check for healthcheck
            if re.search(r'^HEALTHCHECK', text, re.MULTILINE | re.IGNORECASE):
                result["healthcheck"] = True
                
        except OSError:
            continue
    
    # Deduplicate and sort
    result["base_images"] = sorted(set(result["base_images"]))
    result["exposed_ports"] = sorted(set(result["exposed_ports"]), key=int)
    
    return result



DOTNET_ENDPOINT_RE = re.compile(r'\bMap(Get|Post|Put|Delete|Patch)\s*\(\s*"([^"]+)"', re.IGNORECASE)
DOTNET_ROUTE_ATTR_RE = re.compile(r'\[(Http(Get|Post|Put|Delete|Patch))\s*\(\s*"([^"]+)"\s*\)\]', re.IGNORECASE)
DOTNET_ROUTE_PREFIX_RE = re.compile(r'\[Route\s*\(\s*"([^"]+)"\s*\)\]', re.IGNORECASE)


def detect_dotnet_endpoints(files: list[Path], repo: Path, *, limit: int = 8) -> list[str]:
    endpoints: list[str] = []
    route_prefix = ""
    
    for p in files:
        # Check for route mapping JSON files (reverse proxy config)
        if p.name.lower().endswith("routemappings.json") or p.name.lower().endswith("routemapping.json"):
            try:
                import json
                text = p.read_text(encoding="utf-8", errors="replace")
                mappings = json.loads(text)
                if isinstance(mappings, list):
                    for mapping in mappings[:limit]:
                        if isinstance(mapping, dict) and "patterns" in mapping:
                            name = mapping.get("name", "unknown")
                            prefix = mapping.get("prefix", "")
                            for pattern in mapping.get("patterns", [])[:2]:  # Max 2 patterns per mapping
                                # Simplify pattern for display
                                pattern = pattern.replace("*", ":wildcard")
                                endpoints.append(f"PROXY {pattern} → {prefix}")
                                if len(endpoints) >= limit:
                                    return endpoints
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        
        if p.suffix.lower() != ".cs":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Detect route prefix from [Route] attribute (controller-level)
        route_match = DOTNET_ROUTE_PREFIX_RE.search(text)
        if route_match:
            route_prefix = route_match.group(1).strip("/")
        
        # Minimal API style: MapGet/MapPost/etc
        for m in DOTNET_ENDPOINT_RE.finditer(text):
            verb = m.group(1).upper()
            path = m.group(2).strip()
            # Avoid Mermaid-incompatible braces in labels.
            path = re.sub(r"\{[^}]+\}", ":param", path)
            endpoints.append(f"{verb} {path}")
            if len(endpoints) >= limit:
                return endpoints
        
        # Controller-style: [HttpGet], [HttpPost], etc
        for m in DOTNET_ROUTE_ATTR_RE.finditer(text):
            verb = m.group(2).upper()
            path = m.group(3).strip()
            # Combine with route prefix if exists
            if route_prefix and not path.startswith("/"):
                path = f"/{route_prefix}/{path}"
            path = re.sub(r"\{[^}]+\}", ":param", path)
            endpoints.append(f"{verb} {path}")
            if len(endpoints) >= limit:
                return endpoints
    
    return endpoints


def detect_apim_routing_config(files: list[Path], repo: Path) -> dict[str, any]:
    """Check if APIM has actual backend routing vs just API definitions/mocks."""
    result = {"has_routing": False, "evidence": [], "has_mock_only": False}
    
    # Look for evidence of real backend routing
    routing_patterns = [
        (r'set-backend-service', "set-backend-service policy"),
        (r'service_url\s*=\s*"[^"]+"', "backend service_url"),
        (r'backend_id\s*=', "backend_id reference"),
        (r'azurerm_api_management_backend', "APIM backend resource"),
    ]
    
    # Look for evidence of mock-only APIs
    mock_patterns = [
        (r'mock-response', "mock-response policy"),
    ]
    
    for p in files:
        if p.suffix.lower() != ".tf":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Check for routing evidence
        for pattern, label in routing_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                result["has_routing"] = True
                result["evidence"].append(f"{label} in {p.relative_to(repo)}")
        
        # Check for mock-only evidence
        for pattern, label in mock_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                result["has_mock_only"] = True
    
    return result


def detect_apim_backend_services(files: list[Path], repo: Path) -> dict[str, any]:
    """Extract APIM backend service names from HttpClient configuration and route mappings."""
    backends: set[str] = set()
    auth_service = None
    
    for p in files:
        if p.suffix.lower() not in {".cs", ".json"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Check if text contains authentication client registration
        if re.search(r'AuthenticationClient|IAuthenticationClient', text, re.IGNORECASE):
            # Look for /fiauthentication/ or similar in HttpClient config
            for m in re.finditer(r'/([a-z]+authentication)/', text, re.IGNORECASE):
                auth_service = m.group(1).lower()
                break
        
        # Look for APIM URLs with paths (e.g., /fiauthentication, /accounts)
        for m in re.finditer(r'azure-api\.net/([a-zA-Z0-9_-]+)', text, re.IGNORECASE):
            service = m.group(1).lower()
            if service not in {"v2", "api", "management"}:  # filter generic
                backends.add(service)
        
        # Look for BaseAddress concatenation
        for m in re.finditer(r'ApiManagerBaseUrl.*?["\']/([\w-]+)/', text, re.IGNORECASE):
            backends.add(m.group(1).lower())
        
        # Extract from JSON route mapping files (e.g., ApiManagerRouteMappings.json)
        if p.suffix.lower() == ".json" and "routemapping" in p.name.lower():
            try:
                import json
                mappings = json.loads(text)
                if isinstance(mappings, list):
                    for mapping in mappings:
                        if isinstance(mapping, dict) and "prefix" in mapping:
                            # Extract service name from prefix like "/external/bacs" or "/fiapibacs"
                            prefix = mapping["prefix"].strip("/")
                            # Remove "external/" prefix if present
                            if prefix.startswith("external/"):
                                prefix = prefix.replace("external/", "")
                            # Extract the backend service name (e.g., "bacs" from "/fiapibacs" or "bacs" from "external/bacs")
                            # Pattern: /fiapi{service} or just {service}
                            service_match = re.search(r'(?:fiapi)?([a-z]+)$', prefix, re.IGNORECASE)
                            if service_match:
                                service_name = service_match.group(1).lower()
                                if service_name not in {"api", "v1", "v2"}:  # filter generic
                                    backends.add(f"fi-api-{service_name}")
            except (json.JSONDecodeError, ValueError):
                pass
    
    # Remove auth service from backends list (it's separate)
    if auth_service and auth_service in backends:
        backends.discard(auth_service)
    
    # Deduplicate similar names
    unique: dict[str, str] = {}
    for backend in backends:
        key = backend.replace("-", "").replace("_", "")
        if key not in unique:
            unique[key] = backend
    
    return {
        "auth_service": auth_service,
        "backends": sorted(unique.values())[:6]
    }


def detect_authentication_methods(files: list[Path], repo: Path) -> dict[str, any]:
    """Detect authentication and authorization patterns."""
    auth_methods = {
        "methods": set(),
        "details": []
    }
    
    for p in files:
        if p.suffix.lower() not in {".cs", ".json", ".config"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # JWT/Bearer tokens
        if re.search(r'Bearer|JWT|JwtBearer|AuthenticationHeaderValue.*Authorization', text, re.IGNORECASE):
            auth_methods["methods"].add("JWT Bearer Token")
            if "JwtParser" in text or "IJwtParser" in text:
                auth_methods["details"].append("Custom JWT parsing/validation")
        
        # OAuth/OpenID Connect
        if re.search(r'OAuth|OpenIdConnect|\.AddJwtBearer\(|UseAuthentication', text, re.IGNORECASE):
            auth_methods["methods"].add("OAuth 2.0 / OIDC")
        
        # API Keys
        if re.search(r'ApiKey|API-Key|X-API-Key|Ocp-Apim-Subscription-Key', text, re.IGNORECASE):
            auth_methods["methods"].add("API Key (Subscription Key)")
        
        # Digital Signatures
        if re.search(r'DigitalSignature|HMAC|RequestSignature', text, re.IGNORECASE):
            auth_methods["methods"].add("Digital Signature / HMAC")
            auth_methods["details"].append("Request signing for integrity validation")
        
        # Certificate auth
        if re.search(r'ClientCertificate|X509Certificate|Mutual TLS|mTLS', text, re.IGNORECASE):
            auth_methods["methods"].add("Client Certificate (mTLS)")
        
        # External authentication service
        if re.search(r'AuthenticationClient|AuthenticationMiddleware|Authenticate\(', text, re.IGNORECASE):
            if "fiauthentication" in text.lower() or "authentication" in p.name.lower():
                auth_methods["details"].append("Delegated auth to backend service")
    
    return {
        "methods": sorted(auth_methods["methods"]),
        "details": auth_methods["details"][:3]
    }


def detect_external_dependencies(files: list[Path], repo: Path) -> dict[str, list[str]]:
    """Extract external service dependencies (databases, storage, queues, etc.)."""
    deps = {
        "databases": set(),
        "storage": set(),
        "queues": set(),
        "external_apis": set(),
        "monitoring": set(),
    }
    
    for p in files:
        if p.suffix.lower() not in {".cs", ".json", ".config", ".yaml", ".yml", ".tf", ".md", ".txt"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Database endpoints - parse connection strings for details
        for m in re.finditer(r'Server=([^;"\s]+)', text, re.IGNORECASE):
            server = m.group(1).strip()
            if "database.windows.net" in server.lower():
                deps["databases"].add("Azure SQL Database")
            elif server not in {"localhost", "127.0.0.1", "(localdb)", "#", "}", "{"}:
                deps["databases"].add(f"SQL Server ({server})")
        
        # Check connection string patterns for authentication methods and database types
        for m in re.finditer(r'ConnectionString["\s:=]+([^"}\n]+)', text, re.IGNORECASE):
            conn_str = m.group(1).strip()
            
            # Application Insights - track as monitoring dependency
            if "InstrumentationKey=" in conn_str or "IngestionEndpoint=" in conn_str or "applicationinsights.azure.com" in conn_str.lower():
                deps["monitoring"].add("Application Insights")
                continue
            
            # Look for SQL patterns
            if any(keyword in conn_str for keyword in ["Server=", "Data Source=", "Initial Catalog=", "Database="]):
                if "database.windows.net" in conn_str.lower():
                    deps["databases"].add("Azure SQL Database")
                    # Check authentication method
                    if "Authentication=Active Directory" in conn_str or "Authentication=ActiveDirectory" in conn_str:
                        deps["databases"].add("SQL (Azure AD Auth)")
                    elif "User ID=" in conn_str or "UID=" in conn_str:
                        deps["databases"].add("SQL (SQL Auth)")
                elif "Integrated Security=True" in conn_str or "Trusted_Connection=True" in conn_str:
                    deps["databases"].add("SQL Server (Windows Auth)")
                elif "SQL" in conn_str.upper():
                    deps["databases"].add("SQL Server")
        
        # Check for monitoring services
        if re.search(r'ApplicationInsights|Microsoft\.ApplicationInsights|TelemetryClient', text, re.IGNORECASE):
            deps["monitoring"].add("Application Insights")
        if re.search(r'Datadog|DatadogTracer', text, re.IGNORECASE):
            deps["monitoring"].add("Datadog")
        if re.search(r'NewRelic|New Relic', text, re.IGNORECASE):
            deps["monitoring"].add("New Relic")
        
        # Check for ORM/database framework usage
        if p.suffix.lower() == ".cs":
            if re.search(r'DbContext|Entity Framework|EF\.Core|UseSqlServer', text, re.IGNORECASE):
                if not deps["databases"]:
                    deps["databases"].add("SQL Database (Entity Framework)")
            elif re.search(r'Dapper|SqlConnection|SqlCommand', text, re.IGNORECASE):
                if not deps["databases"]:
                    deps["databases"].add("SQL Database (ADO.NET/Dapper)")
        
        # Extract architecture info from README files
        if p.name.upper().startswith("README"):
            # Look for dependency mentions
            if re.search(r'Azure SQL|SQL Server|PostgreSQL|MySQL', text, re.IGNORECASE):
                if not deps["databases"]:
                    deps["databases"].add("Database (mentioned in README)")
            if re.search(r'Redis|Memcached', text, re.IGNORECASE):
                deps["storage"].add("Cache (mentioned in README)")
            if re.search(r'Kafka|RabbitMQ|Service Bus|Event Hub', text, re.IGNORECASE):
                if not deps["queues"]:
                    deps["queues"].add("Messaging (mentioned in README)")
        
        # Storage accounts - handle tokenized names with #{...}#
        if ".blob.core.windows.net" in text.lower():
            deps["storage"].add("Azure Blob Storage")
        
        # Service Bus / Event Hub
        if re.search(r'ServiceBusConnection|servicebus\.windows\.net', text, re.IGNORECASE):
            deps["queues"].add("Azure Service Bus")
        if re.search(r'EventHubConnection|eventhub\.windows\.net', text, re.IGNORECASE):
            deps["queues"].add("Azure Event Hub")
        
        # External HTTP APIs (exclude localhost/tests)
        for m in re.finditer(r'BaseAddress.*?new Uri\(["\']([^"\']+)["\']', text):
            url = m.group(1)
            if "localhost" not in url and "127.0.0.1" not in url and url.startswith("http"):
                # Extract domain
                domain = url.split("//")[-1].split("/")[0]
                if "azure-api.net" not in domain:  # Already captured in backend services
                    deps["external_apis"].add(domain)
    
    return {k: sorted(v)[:5] for k, v in deps.items()}


def ensure_repos_knowledge(repos_root: Path) -> Path:
    OUTPUT_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_KNOWLEDGE_DIR / "Repos.md"
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "**Repo root directory:**" not in text:
            path.write_text(
                text.rstrip()
                + "\n\n## Repo Roots\n"
                + f"- **Repo root directory:** `{repos_root}`\n",
                encoding="utf-8",
            )
        return path

    path.write_text(
        "# 🟣 Repositories\n\n"
        "## Repo Roots\n"
        f"- **Repo root directory:** `{repos_root}`\n\n"
        "## Repository Inventory\n\n"
        "### Application Repos\n\n"
        "### Infrastructure Repos\n\n",
        encoding="utf-8",
    )
    return path


def upsert_repo_inventory(repos_md: Path, *, repo_name: str, repo_type: str, purpose: str, langs: list[str]) -> None:
    text = repos_md.read_text(encoding="utf-8", errors="replace")
    entry_line = f"- **{repo_name}** - {purpose} ({', '.join(langs)})"
    if entry_line in text:
        return

    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    target_heading = "### Infrastructure Repos" if repo_type == "Infrastructure" else "### Application Repos"
    for i, l in enumerate(lines):
        out.append(l)
        if l.strip() == target_heading and not inserted:
            # insert after heading and any blank lines
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            out.append(entry_line)
            inserted = True
    if not inserted:
        out.append("")
        out.append(target_heading)
        out.append(entry_line)

    repos_md.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    repo_type: str,
    purpose: str,
    langs: list[tuple[str, str]],
    ci: str,
    providers: list[str],
    ingress: list[Evidence],
    egress: list[Evidence],
    extra_evidence: list[Evidence],
    scan_scope: str,
) -> Path:
    OUTPUT_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_SUMMARY_DIR / "Repos").mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_SUMMARY_DIR / "Repos" / f"{repo_name}.md"

    lang_lines = "\n".join([f"- {l} — evidence: `{e}`" for l, e in langs]) if langs else "- (none detected)"

    provider_line = ", ".join(providers) if providers else "Unknown"
    repo_url = "N/A"

    tf_resource_types = detect_terraform_resources(iter_files(repo), repo)
    endpoints = detect_dotnet_endpoints(iter_files(repo), repo, limit=6)
    hosting_info = detect_hosting_from_terraform(iter_files(repo), repo)
    ingress_info = detect_ingress_from_code(iter_files(repo), repo)
    apim_routing = detect_apim_routing_config(iter_files(repo), repo)
    backend_result = detect_apim_backend_services(iter_files(repo), repo)
    auth_service = backend_result["auth_service"]
    backend_services = backend_result["backends"]
    external_deps = detect_external_dependencies(iter_files(repo), repo)
    auth_methods = detect_authentication_methods(iter_files(repo), repo)
    dockerfile_info = parse_dockerfiles(repo)
    network_info = detect_network_topology(iter_files(repo), repo)

    has_kv = "azurerm_key_vault" in tf_resource_types
    has_sql = any(t in tf_resource_types for t in {"azurerm_mssql_server", "azurerm_mssql_database", "azurerm_sql_server", "azurerm_sql_database"})
    has_ai = "azurerm_application_insights" in tf_resource_types
    has_apim = any(t.startswith("azurerm_api_management") for t in tf_resource_types)
    has_appgw = "azurerm_application_gateway" in tf_resource_types or ingress_info["type"] == "Application Gateway"
    has_frontdoor = "azurerm_frontdoor" in tf_resource_types or ingress_info["type"] == "Azure Front Door"
    has_webapp = hosting_info["type"] in {"Windows App Service", "Linux App Service"} or "azurerm_linux_web_app" in tf_resource_types or "azurerm_windows_web_app" in tf_resource_types
    has_plan = "azurerm_service_plan" in tf_resource_types or "azurerm_app_service_plan" in tf_resource_types
    has_state_backend = any(t in tf_resource_types for t in {"azurerm_storage_account", "azurerm_storage_container"})

    # Diagram building: keep labels ASCII and avoid braces/parentheses for renderer compatibility.
    diagram_lines: list[str] = ["flowchart TB", "  client[Client]"]
    
    # Track assumptions for dashed borders
    assumptions: list[str] = []

    if provider_line == "Azure" or "Azure" in providers:
        diagram_lines.append("  subgraph azure[Azure]")
        
        # Add ingress layer (App Gateway, Front Door)
        if has_appgw:
            diagram_lines.append("    appgw[Application Gateway]")
            if ingress_info["type"] == "Application Gateway" and "azurerm_application_gateway" not in tf_resource_types:
                assumptions.append("appgw")
        elif has_frontdoor:
            diagram_lines.append("    fd[Front Door]")
            if ingress_info["type"] == "Azure Front Door" and "azurerm_frontdoor" not in tf_resource_types:
                assumptions.append("fd")
        
        # Add APIM if present and has routing
        if has_apim and apim_routing["has_routing"]:
            diagram_lines.append("    apim[API Management]")
        elif has_apim:
            # APIM exists but routing config not in this repo
            # If backend services detected via code, APIM is used for routing (even if Terraform only shows mocks)
            if backend_services:
                diagram_lines.append("    apim[API Management]")
            else:
                diagram_lines.append("    apim[API Management - Mock Only]")
                assumptions.append("apim")
        
        if has_webapp:
            # Show service name, not just hosting platform
            web_label = repo_name.replace("_", "-")
            if hosting_info["type"]:
                web_label = f"{web_label}<br/>({hosting_info['type']})"
            diagram_lines.append(f"    web[{web_label}]")
        if has_kv:
            diagram_lines.append("    kv[Key Vault]")
        if has_sql:
            diagram_lines.append("    sql[Azure SQL]")
        if has_ai:
            diagram_lines.append("    ai[Application Insights]")
        
        # Add authentication service if detected (separate from other backends)
        if auth_service:
            diagram_lines.append(f"    auth[{auth_service}]")
        
        # Add backend services if detected (typically via APIM)
        if backend_services:
            diagram_lines.append("    subgraph backends[Backend Services]")
            for idx, svc in enumerate(backend_services, start=1):
                diagram_lines.append(f"      backend{idx}[{svc}]")
            diagram_lines.append("    end")
        
        if has_state_backend:
            diagram_lines.append("    tfstate[Terraform state storage]")
        diagram_lines.append("  end")

        if detect_ci(repo) == "Azure Pipelines":
            diagram_lines.append("  pipeline[Azure Pipelines]")

        # Basic flows.
        # Add ingress routing
        if has_appgw:
            diagram_lines.append("  client --> appgw")
            if has_webapp:
                diagram_lines.append("  appgw --> web")
        elif has_frontdoor:
            diagram_lines.append("  client --> fd")
            if has_webapp:
                diagram_lines.append("  fd --> web")
        elif has_webapp:
            diagram_lines.append("  client --> web")
        
        if has_webapp:
            # Connect web app to data stores directly (no endpoints in main diagram)
            if has_sql:
                diagram_lines.append("  web --> sql")

            if has_kv:
                diagram_lines.append("  web --> kv")
            if has_ai:
                diagram_lines.append("  web -.-> ai")
            
            # Add authentication flow if auth service detected (simple connections, no numbered steps)
            if auth_service and has_apim:
                diagram_lines.append("  web --> apim")
                diagram_lines.append("  apim --> auth")
            
            # Add connections to backend services
            # If app calls APIM which routes to backends (reverse proxy pattern)
            if backend_services and has_apim:
                # APIM routes to backends (simple connections)
                if not auth_service:
                    diagram_lines.append("  web --> apim")
                for idx in range(1, len(backend_services) + 1):
                    diagram_lines.append(f"  apim --> backend{idx}")
            elif backend_services:
                # Direct calls to backends (no APIM in middle)
                for idx in range(1, len(backend_services) + 1):
                    diagram_lines.append(f"  web -.-> backend{idx}")

        if has_state_backend:
            # Use dotted line because this is a provisioning/control-plane linkage.
            if "pipeline" in "\n".join(diagram_lines):
                diagram_lines.append("  pipeline -.-> tfstate")
        if "pipeline" in "\n".join(diagram_lines) and has_webapp:
            diagram_lines.append("  pipeline -.-> web")
    else:
        # Fallback: generic service with inferred deps.
        diagram_lines.extend(["  subgraph appbox[Application]", "    app[Service]", "  end", "  client --> app"])

    # Group evidence by label to reduce noise
    def group_evidence(evidence_list: list) -> list[str]:
        """Group evidence items by label, showing count if multiple."""
        grouped: dict[str, list] = {}
        for ev in evidence_list:
            grouped.setdefault(ev.label, []).append(ev)
        
        result = []
        for label, items in grouped.items():
            if len(items) == 1:
                # Single item - show full detail
                result.append(items[0].fmt())
            else:
                # Multiple items - show count and first example
                first_path = items[0].path.split(":")[0] if ":" in items[0].path else items[0].path
                result.append(f"- 💡 {label} — {len(items)} files (e.g., `{first_path}`)")
        return result
    
    evidence_lines: list[str] = group_evidence((extra_evidence + ingress + egress)[:30])
    if not evidence_lines:
        evidence_lines.append("- 💡 (no notable evidence captured)")

    ingress_lines = "\n".join(group_evidence(ingress)) if ingress else "- (no ingress signals detected in quick scan)"
    egress_lines = "\n".join(group_evidence(egress)) if egress else "- (no egress signals detected in quick scan)"
    
    # Get CI/CD details
    ci_cd_info = parse_ci_cd_details(repo)
    ci_string = ci_cd_info["platform"]
    if ci_cd_info["files"]:
        ci_string += f" ({', '.join(ci_cd_info['files'])})"
    
    # Build hosting string
    hosting_string = hosting_info["type"] if hosting_info["type"] else "Unknown"
    if hosting_info["evidence"]:
        hosting_string += f" (Terraform: {', '.join(hosting_info['evidence'][:2])})"
    
    # Build external dependencies string
    ext_deps_lines = []
    if backend_services:
        ext_deps_lines.append(f"  - **Backend APIs (via APIM):** {', '.join(backend_services)}")
    if external_deps["databases"]:
        ext_deps_lines.append(f"  - **Databases:** {', '.join(external_deps['databases'])}")
    if external_deps["storage"]:
        ext_deps_lines.append(f"  - **Storage:** {', '.join(external_deps['storage'])}")
    if external_deps["queues"]:
        ext_deps_lines.append(f"  - **Messaging:** {', '.join(external_deps['queues'])}")
    if external_deps["monitoring"]:
        ext_deps_lines.append(f"  - **Monitoring:** {', '.join(external_deps['monitoring'])}")
    if external_deps["external_apis"]:
        ext_deps_lines.append(f"  - **External APIs:** {', '.join(external_deps['external_apis'])}")
    
    ext_deps_section = ""
    if ext_deps_lines:
        ext_deps_section = "\n- **External Dependencies:**\n" + "\n".join(ext_deps_lines) + "\n"
    
    # Add styling for assumptions (dashed borders)
    style_lines = []
    if assumptions:
        for node in assumptions:
            style_lines.append(f"  style {node} stroke-dasharray: 5 5")
    
    # Add colored borders for component types (theme-aware per Settings/Styling.md)
    # Security/Gateway components - thick red border
    if has_appgw:
        style_lines.append("  style appgw stroke:#ff0000,stroke-width:3px")
    if has_frontdoor:
        style_lines.append("  style fd stroke:#ff0000,stroke-width:3px")
    
    # API Management - orange border (gateway layer)
    if has_apim or "apim" in "\n".join(diagram_lines):
        style_lines.append("  style apim stroke:#ff6600,stroke-width:2px")
    
    # Application service - blue border (trusted internal)
    if has_webapp:
        style_lines.append("  style web stroke:#0066cc,stroke-width:2px")
    
    # Authentication service - green border (security control)
    if auth_service:
        style_lines.append("  style auth stroke:#00cc00,stroke-width:3px")
    
    # Backend services - purple border
    for idx in range(1, len(backend_services) + 1):
        style_lines.append(f"  style backend{idx} stroke:#9966cc,stroke-width:2px")
    
    # Data/infrastructure - gray thicker border
    if has_sql:
        style_lines.append("  style sql stroke:#666666,stroke-width:3px")
    if has_kv:
        style_lines.append("  style kv stroke:#666666,stroke-width:3px")
    
    if style_lines:
        diagram_lines.append("")
        diagram_lines.append("  %% Styling")
        diagram_lines.extend(style_lines)

    content = (
        f"# 🟣 Repo {repo_name}\n\n"
        "## 🗺️ Architecture Diagram\n"
        "```mermaid\n"
        + "\n".join(diagram_lines)
        + "\n"
        "```\n\n"
        + ("**Legend:** \n"
           "- Border colors: 🔴 Security gateway | 🟠 API gateway | 🔵 Application | 🟢 Auth service | 🟣 Backend | ⚫ Data\n"
           "- Dashed borders = assumptions (not confirmed by infrastructure config)\n\n" if assumptions 
           else "**Legend:** Border colors indicate component type: 🔴 Security gateway | 🟠 API gateway | 🔵 Application | 🟢 Auth service | 🟣 Backend | ⚫ Data\n\n")
        + f"- **Overall Score:** 🟢 **0/10** (INFO) — *Phase 1 complete; awaiting Phase 2 analysis and security review*\n\n"
        "## 📊 TL;DR - Executive Summary\n\n"
        "| Aspect | Value |\n"
        "|--------|-------|\n"
        "| **Final Score** | 🟢 **0/10** (INFO - Awaiting Security Review) |\n"
        "| **Initial Score** | Phase 1 context discovery complete |\n"
        "| **Adjustments** | Pending: Security review → Dev Skeptic → Platform Skeptic |\n"
        "| **Key Takeaway** | **[PHASE 2 TODO]** Populate after explore agent completes |\n\n"
        "**Top 3 Actions:**\n"
        "1. **[PHASE 2 TODO]** - Complete after security review\n"
        "2. **[PHASE 2 TODO]** - Complete after security review\n"
        "3. **[PHASE 2 TODO]** - Complete after security review\n\n"
        "**Material Risks:** \n"
        "**[PHASE 2 TODO]** Complete after security review using gathered context.\n\n"
        "**Why Score Changed/Stayed:** \n"
        "**[PHASE 2 TODO]** Document security review → Dev Skeptic → Platform Skeptic reasoning.\n\n"
        "---\n\n"
        "## 🛡️ Security Observations\n\n"
        "**[PHASE 2 TODO]** After Phase 2 explore agent completes, perform security review based on gathered context:\n"
        "- Review authentication/authorization flows for bypass risks\n"
        "- Check IaC configurations for misconfigurations (public exposure, weak encryption, missing network controls)\n"
        "- Review routing logic and middleware for security gaps\n"
        "- Identify injection risks, insecure deserialization, secrets in code\n"
        "- Review error handling and logging for information disclosure\n\n"
        "Then invoke Dev Skeptic and Platform Skeptic for scoring adjustments.\n\n"
        "### ✅ Confirmed Security Controls\n"
        "*Phase 1 detected (validate during Phase 2 review):*\n"
    )
    
    # Add detected security-relevant controls
    if auth_methods["methods"]:
        content += f"1. **Authentication mechanisms detected** 🔐 - {', '.join(list(auth_methods['methods'])[:3])}\n"
    if has_kv:
        content += "2. **Key Vault usage** 🔒 - Secrets management infrastructure present\n"
    if network_info["nsgs"]:
        content += f"3. **Network Security Groups** 🛡️ - {len(network_info['nsgs'])} NSG(s) configured\n"
    if network_info["private_endpoints"]:
        content += f"4. **Private Endpoints** 🔒 - {len(network_info['private_endpoints'])} configured for network isolation\n"
    
    if not (auth_methods["methods"] or has_kv or network_info["nsgs"] or network_info["private_endpoints"]):
        content += "1. *No security controls detected in Phase 1 scan - review during Phase 2*\n"
    
    content += (
        "\n### ⚠️ Areas for Security Review\n"
        "**[PHASE 2 TODO]** Document findings from security review here.\n\n"
        "---\n\n"
        "## 🧭 Overview\n"
        f"- **Purpose:** {purpose}\n"
        f"- **Repo type:** {repo_type}\n"
        f"- **Hosting:** {hosting_string}\n"
        f"- **Cloud provider(s) referenced:** {provider_line}\n"
        f"- **CI/CD:** {ci_string}\n"
        + ext_deps_section
    )
    
    # Add authentication section if methods detected
    if auth_methods["methods"]:
        content += "\n- **Authentication:**\n"
        for method in auth_methods["methods"]:
            content += f"  - {method}\n"
        if auth_methods["details"]:
            for detail in auth_methods["details"]:
                content += f"  - _{detail}_\n"
    
    # Add Dockerfile section if containers detected
    if dockerfile_info["base_images"]:
        content += "\n- **Container Runtime:**\n"
        for image in dockerfile_info["base_images"][:3]:  # Limit to 3
            content += f"  - Base image: `{image}`\n"
        if dockerfile_info["multi_stage"]:
            content += "  - Multi-stage build detected\n"
        if dockerfile_info["exposed_ports"]:
            ports_str = ", ".join(dockerfile_info["exposed_ports"])
            content += f"  - Exposed ports: {ports_str}\n"
        if dockerfile_info["user"]:
            user_str = dockerfile_info["user"]
            security_note = " ⚠️ (security risk)" if user_str.lower() in {"root", "0"} else " ✅"
            content += f"  - Runtime user: `{user_str}`{security_note}\n"
        elif dockerfile_info["evidence"]:
            content += "  - Runtime user: `root` ⚠️ (no USER directive found)\n"
        if dockerfile_info["healthcheck"]:
            content += "  - Health check: ✅ configured\n"
    
    # Add network topology if detected
    if network_info["vnets"] or network_info["subnets"] or network_info["nsgs"]:
        content += "\n- **Network Topology:**\n"
        if network_info["vnets"]:
            content += f"  - VNets: {', '.join(network_info['vnets'][:3])}\n"
            if len(network_info["vnets"]) > 3:
                content += f"    _(+{len(network_info['vnets'])-3} more)_\n"
        if network_info["subnets"]:
            content += f"  - Subnets: {len(network_info['subnets'])} detected\n"
        if network_info["nsgs"]:
            content += f"  - NSGs: {len(network_info['nsgs'])} configured\n"
        if network_info["private_endpoints"]:
            content += f"  - Private Endpoints: {len(network_info['private_endpoints'])} configured\n"
        if network_info["peerings"]:
            content += f"  - VNet Peerings: {len(network_info['peerings'])} configured\n"
    
    content += (
        "\n"
        "## 🚦 Traffic Flow\n\n"
        "**[PHASE 2 TODO]:** Complete this section using an explore agent to trace the actual request path.\n\n"
        "**Phase 1 (Script) detected:**\n"
    )
    
    # Add detected ingress/egress as hints for Phase 2
    if has_appgw:
        content += f"- **Ingress:** Application Gateway detected (from {'Terraform' if 'azurerm_application_gateway' in tf_resource_types else 'code patterns'})\n"
    elif has_frontdoor:
        content += f"- **Ingress:** Azure Front Door detected (from {'Terraform' if 'azurerm_frontdoor' in tf_resource_types else 'code patterns'})\n"
    
    if auth_methods["methods"]:
        content += f"- **Authentication methods:** {', '.join(auth_methods['methods'][:3])}\n"
    
    if backend_services or auth_service:
        services = ([auth_service] if auth_service else []) + backend_services
        content += f"- **Backend services:** {', '.join(services[:5])}\n"
    
    if endpoints:
        content += f"- **Routes detected:** {len(endpoints)} endpoint(s) - see Traffic Flow section below\n"
    
    content += (
        "\n**Phase 2 agent should document:**\n"
        "1. Complete request path with middleware execution order\n"
        "2. Authentication/authorization validation points\n"
        "3. Routing logic (how backend is selected)\n"
        "4. Header transformations\n"
        "5. External service calls and resilience patterns\n\n"
    )
    
    # Add Route Mappings table if endpoints detected
    if endpoints:
        content += (
            "### Route Mappings\n\n"
            "| Incoming Path | Backend Destination | Notes |\n"
            "|---------------|---------------------|-------|\n"
        )
        for ep in endpoints:
            # Parse endpoint format: "GET /path" or "PROXY /path → /backend"
            if "PROXY" in ep:
                # Format: "PROXY /incoming/path → /backend/path" or "PROXY /incoming  /backend"
                ep_clean = ep.replace("PROXY ", "")
                if "→" in ep_clean:
                    parts = ep_clean.split("→")
                else:
                    parts = ep_clean.split("  ")  # Two spaces
                
                if len(parts) >= 2:
                    incoming = parts[0].strip()
                    backend = parts[1].strip()
                    content += f"| `{incoming}` | `{backend}` | Proxied via APIM |\n"
                else:
                    content += f"| `{ep}` | - | - |\n"
            else:
                # Format: "GET /path" or "POST /path"
                content += f"| `{ep}` | Internal | - |\n"
        content += "\n"
    
    content += (
        "## 🛡️ Security Review\n"
        "### Languages & Frameworks (extracted)\n"
        f"{lang_lines}\n\n"
        "### 🧾 Summary\n"
        "**Phase 1 (Script) complete:** Context and architecture baseline established.\n\n"
        "**Phase 2 (Manual Review) pending:** Code review, security analysis, and skeptic reviews.\n\n"
        "**Automated Scanning Status:**\n"
        "- ❌ **SCA (Software Composition Analysis):** Not performed - dependency vulnerability scanning pending\n"
        "- ❌ **SAST (Static Application Security Testing):** Not performed - automated code scanning pending\n"
        "- ❌ **Secrets Scanning:** Not performed - credential detection pending\n"
        "- ❌ **IaC Scanning:** Not performed - infrastructure misconfiguration detection pending\n\n"
        "### ✅ Applicability\n"
        "**[PHASE 2 TODO]** Determine after security review.\n\n"
        "### ⚠️ Assumptions\n"
        "- This summary is based on Phase 1 file-based heuristics; validate during Phase 2 review.\n"
        "- Dashed borders in diagram indicate assumptions (not confirmed by infrastructure config).\n\n"
        "### 🔎 Key Evidence (deep dive)\n"
        + "\n".join(evidence_lines)
        + "\n\n"
        "### Ingress Signals (quick)\n"
        f"{ingress_lines}\n\n"
        "### Egress Signals (quick)\n"
        f"{egress_lines}\n\n"
        "### Cloud Environment Implications\n"
        f"- **Provider(s) referenced:** {provider_line}\n"
        "- **Note:** If this repo deploys/targets cloud resources, promote reusable facts into `Output/Knowledge/<Provider>.md` once confirmed.\n\n"
        "## 🤔 Skeptic\n"
        "**[PHASE 2 TODO]** After security review, invoke Dev Skeptic and Platform Skeptic agents:\n"
        "1. **Dev Skeptic:** Review from developer perspective (app patterns, mitigations, org conventions)\n"
        "2. **Platform Skeptic:** Review from platform perspective (networking, CI/CD, guardrails, rollout realities)\n"
        "3. Document scoring adjustments and reasoning in TL;DR section.\n\n"
        "## 🤝 Collaboration\n"
        "- **Outcome:** Context discovery created/updated repo summary.\n"
        "- **Next step:** Choose scan types (IaC/SCA/SAST/Secrets).\n\n"
        "## Compounding Findings\n"
        "- **Compounds with:** None identified (context discovery only)\n\n"
        "## Meta Data\n"
        "<!-- Meta Data must remain the final section in the file. -->\n"
        f"- **Repo Name:** {repo_name}\n"
        f"- **Repo Path:** {repo.resolve()}\n"
        f"- **Repo URL:** {repo_url}\n"
        f"- **Repo Type:** {repo_type}\n"
        f"- **Languages/Frameworks:** {', '.join([l for l, _ in langs]) if langs else 'Unknown'}\n"
        f"- **CI/CD:** {ci}\n"
        f"- **Scan Scope:** {scan_scope}\n"
        "- **Scanner:** Context discovery (local heuristics)\n"
        f"- 🗓️ **Last updated:** {now_uk()}\n"
    )

    out_path.write_text(content, encoding="utf-8")

    # Validate Mermaid fenced blocks so issues are caught (and safely auto-fixed) before viewing/rendering.
    probs = validate_markdown_file(out_path, fix=True)
    errs = [p for p in probs if p.level == "ERROR"]
    warns = [p for p in probs if p.level == "WARN"]
    for p in warns:
        line = f":{p.line}" if p.line else ""
        print(f"WARN: {out_path}{line} - {p.message}")
    if errs:
        raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast, non-security context discovery for a local repo.")
    parser.add_argument("repo", help="Absolute or relative path to the repo to discover.")
    parser.add_argument(
        "--repos-root",
        default=None,
        help="Repo root directory to record in Output/Knowledge/Repos.md (default: parent of repo).",
    )
    args = parser.parse_args()

    repo = Path(args.repo).expanduser()
    if not repo.is_absolute():
        repo = repo.resolve()
    if not repo.is_dir():
        print(f"ERROR: repo path not found: {repo}")
        return 2

    repo_name = repo.name
    repos_root = Path(args.repos_root).expanduser().resolve() if args.repos_root else repo.parent.resolve()

    files = iter_files(repo)
    langs = detect_languages(files, repo)
    lang_names = [l for l, _ in langs]
    rtype = infer_repo_type(lang_names, repo_name)
    purpose, purpose_ev = repo_purpose(repo, repo_name)
    ci = detect_ci(repo)
    providers = detect_cloud_provider(files, repo)

    extra: list[Evidence] = []
    if purpose_ev:
        extra.append(purpose_ev)
    if (repo / "Dockerfile").exists():
        extra.append(Evidence(label="Dockerfile present", path="Dockerfile"))
    if (repo / ".github" / "workflows").exists():
        extra.append(Evidence(label="GitHub Actions workflows present", path=".github/workflows"))

    ingress: list[Evidence] = []
    egress: list[Evidence] = []
    text_files = [
        p
        for p in files
        if p.suffix.lower() in (CODE_EXTS | CFG_EXTS | IAC_EXTS | DOC_EXTS | SQL_EXTS)
        or p.name in {"Dockerfile", "docker-compose.yml"}
    ]

    for p in sorted(text_files)[:500]:
        if len(ingress) < 20:
            ingress.extend(_scan_text_file(repo, p, INGRESS_PATTERNS, limit=20 - len(ingress)))
        if len(egress) < 20:
            egress.extend(_scan_text_file(repo, p, EGRESS_PATTERNS, limit=20 - len(egress)))
        if len(ingress) >= 20 and len(egress) >= 20:
            break

    repos_md = ensure_repos_knowledge(repos_root)
    upsert_repo_inventory(
        repos_md,
        repo_name=repo_name,
        repo_type=rtype,
        purpose=purpose,
        langs=lang_names or ["Unknown"],
    )

    summary_path = write_repo_summary(
        repo=repo,
        repo_name=repo_name,
        repo_type=rtype,
        purpose=purpose,
        langs=langs,
        ci=ci,
        providers=providers,
        ingress=ingress,
        egress=egress,
        extra_evidence=extra,
        scan_scope="Context discovery",
    )

    print("== Context discovery complete ==")
    print(f"repo: {repo}")
    print(f"summary: {summary_path}")
    print(f"knowledge: {repos_md}")
    print(f"timestamp: {now_uk()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
