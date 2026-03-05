# context_extraction.py
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple

from models import Resource, Connection, RepositoryContext

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

    return context
