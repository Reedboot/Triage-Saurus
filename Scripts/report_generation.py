from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import sqlite3

from models import RepositoryContext
from template_renderer import render_template
from markdown_validator import validate_markdown_file
import resource_type_db as _rtdb

# Lazy DB connection — initialised on first use, None if DB unavailable
_db_conn: sqlite3.Connection | None = None

def _get_db() -> sqlite3.Connection | None:
    global _db_conn
    if _db_conn is None:
        db_path = Path(__file__).resolve().parents[1] / "Output/Learning/triage.db"
        if db_path.exists():
            _db_conn = sqlite3.connect(str(db_path))
    return _db_conn


def now_uk() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _provider_title(provider: str) -> str:
    return {"azure": "Azure", "aws": "AWS", "gcp": "GCP"}.get(provider, provider.upper())


def _is_key_service_type(resource_type: str) -> bool:
    # Exclude helper/data/control artifacts from executive "Key services" list.
    excluded_exact = {
        "azurerm_client_config",
        "aws_ami",
        "aws_caller_identity",
        "google_compute_zones",
    }
    if resource_type in excluded_exact:
        return False

    excluded_tokens = (
        "policy_assignment",
        "policy_definition",
        "role_definition",
        "security_group_rule",
        "route_table_association",
        "route_table",
        "route",
        "network_interface",
        "subnet",
        "sql_firewall_rule",
        "configuration",
        "iam_access_key",
        "iam_instance_profile",
        "iam_role",
        "iam_user",
        "iam_role_policy",
        "iam_user_policy",
        "db_option_group",
        "db_parameter_group",
        "db_subnet_group",
        "ebs_",
        "volume_attachment",
        "flow_log",
        "internet_gateway",
        "kms_alias",
    )
    return not any(token in resource_type for token in excluded_tokens)


def _summarize_service_names(resource_types: list[str]) -> str:
    service_names = sorted({_rtdb.get_friendly_name(_get_db(), r) for r in resource_types if _is_key_service_type(r)})
    return ", ".join(service_names) if service_names else "None"


def _group_parent_services(resource_types: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for rtype in resource_types:
        parent = _rtdb.get_friendly_name(_get_db(), rtype)
        grouped.setdefault(parent, []).append(rtype)
    return grouped


def _resource_kind_label(resource_type: str) -> str:
    cleaned = resource_type
    for prefix in ("azurerm_", "aws_", "google_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.replace("_", " ").title()


def _child_node_label(resource_type: str, parent_service: str) -> str:
    friendly = _rtdb.get_friendly_name(_get_db(), resource_type)
    if friendly == parent_service:
        return f"{friendly}: {_resource_kind_label(resource_type)}"
    return friendly


def _filter_mermaid_children(_parent_service: str, raw_types: list[str]) -> list[str]:
    """Filter child nodes for diagram clarity — delegates to resource_type_db canonical type logic."""
    return _rtdb.filter_to_canonical(raw_types)


def _is_apim_component(resource_type: str) -> bool:
    """Check if resource is an API Management component."""
    apim_components = (
        "azurerm_api_management_api",
        "azurerm_api_management_api_operation",
        "azurerm_api_management_api_policy",
        "azurerm_api_management_product",
        "azurerm_api_management_product_api",
        "azurerm_api_management_subscription",
        "azurerm_api_management_backend",
        "azurerm_api_management_named_value",
    )
    return resource_type in apim_components


def _group_apim_resources(service_raw: list[str], resources: list, repo_path: Path | None) -> tuple[list[str], dict]:
    """Separate APIM components from other resources and group them by API.
    
    Returns:
        (non_apim_resources, apim_structure)
        where apim_structure contains API names as keys with their components.
    """
    apim_resources = [r for r in service_raw if _is_apim_component(r)]
    non_apim_resources = [r for r in service_raw if not _is_apim_component(r)]
    
    if not apim_resources:
        return service_raw, {}
    
    # Find all APIs
    api_resources = [r for r in resources if r.resource_type == "azurerm_api_management_api"]
    
    # Structure: {api_name: {operations: [], policies: [], products: [], subscriptions: []}}
    apim_structure = {}
    
    # Extract actual operation_id values from Terraform if available
    operation_ids = {}  # {terraform_label: operation_id}
    if repo_path:
        for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management_api_operation"):
            op_id_match = re.search(r'operation_id\s*=\s*"([^"]+)"', body)
            if op_id_match:
                operation_ids[rlabel] = op_id_match.group(1)
    
    for api in api_resources:
        api_name = api.name or "unnamed-api"
        apim_structure[api_name] = {
            "operations": [],
            "policies": [],
            "products": [],
            "subscriptions": [],
        }
        
        # Find operations for this API
        for r in resources:
            if r.resource_type == "azurerm_api_management_api_operation":
                # Use actual operation_id from Terraform if available, else fall back to resource label
                op_name = operation_ids.get(r.name, r.name)
                apim_structure[api_name]["operations"].append(op_name)
            elif r.resource_type == "azurerm_api_management_api_policy":
                apim_structure[api_name]["policies"].append(r.name)
        
        # Products and subscriptions are API-agnostic, add to first API for now
        if api == api_resources[0]:
            for r in resources:
                if r.resource_type == "azurerm_api_management_product":
                    apim_structure[api_name]["products"].append(r.name)
                elif r.resource_type == "azurerm_api_management_subscription":
                    apim_structure[api_name]["subscriptions"].append(r.name)
    
    return non_apim_resources, apim_structure


# Backend detection: Currently uses direct Terraform parsing for speed.
# Opengrep rules exist in Rules/Detection/{Azure,AWS,GCP}/ for:
#   - apim-backend-routing-detection.yml (APIM service_url)
#   - api-gateway-backend-integration-detection.yml (AWS API Gateway URIs)
#   - api-gateway-backend-config-detection.yml (GCP API Gateway)
#   - kubernetes-backend-deployment-detection.yml (AKS deployments)
#   - eks-backend-deployment-detection.yml (AWS EKS)
#   - gke-backend-deployment-detection.yml (GCP GKE)
# To use opengrep instead: import opengrep_backend_detection and call run_opengrep_rules()
# Current approach is faster for Phase 1 (no LLM) baseline scans.


def _extract_apim_backend_url(repo_path: Path | None, api_name: str) -> str | None:
    """Extract the backend service_url from APIM API Terraform config."""
    if not repo_path or not repo_path.exists():
        return None
    
    service_url_pattern = re.compile(r'service_url\s*=\s*"([^"]+)"')
    
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management_api"):
        if rtype == "azurerm_api_management_api" and api_name in rlabel:
            match = service_url_pattern.search(body)
            if match:
                return match.group(1)
    return None


def _extract_aws_api_gateway_backend(repo_path: Path | None, api_id: str) -> str | None:
    """Extract backend integration URI from AWS API Gateway."""
    if not repo_path or not repo_path.exists():
        return None
    
    uri_pattern = re.compile(r'(?:uri|integration_uri)\s*=\s*"([^"]+)"')
    
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="aws_api_gateway"):
        if "integration" in rtype:
            match = uri_pattern.search(body)
            if match:
                return match.group(1)
    
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="aws_apigatewayv2"):
        if "integration" in rtype:
            match = uri_pattern.search(body)
            if match:
                return match.group(1)
    
    return None


def _extract_kubernetes_deployments(repo_path: Path | None) -> list[dict]:
    """Extract Kubernetes/AKS/EKS/GKE backend deployments from Terraform."""
    if not repo_path or not repo_path.exists():
        return []
    
    # First, try to extract locals to resolve variables
    locals_map = {}
    for tf_file in repo_path.rglob("*.tf"):
        try:
            content = tf_file.read_text(encoding="utf-8", errors="ignore")
            locals_block = re.search(r'locals\s*\{([^}]+)\}', content, re.DOTALL)
            if locals_block:
                # Extract key = "value" pairs from locals
                for match in re.finditer(r'(\w+)\s*=\s*"([^"]+)"', locals_block.group(1)):
                    locals_map[match.group(1)] = match.group(2)
        except Exception:
            continue
    
    deployments = []
    app_name_pattern = re.compile(r'app_name\s*=\s*(?:"([^"]+)"|local\.(\w+)|([^"\s]+))')
    namespace_pattern = re.compile(r'namespace\s*=\s*(?:"([^"]+)"|local\.(\w+)|([^"\s]+))')
    helm_name_pattern = re.compile(r'name\s*=\s*"([^"]+)"')
    
    for tf_file in repo_path.rglob("*.tf"):
        if not any(keyword in tf_file.name.lower() for keyword in ["kubernetes", "eks", "aks", "gke", "helm"]):
            continue
        try:
            content = tf_file.read_text(encoding="utf-8", errors="ignore")
            
            # Look for Terraform module blocks (common for AKS)
            module_pattern = re.compile(r'module\s+"([^"]+)"\s*\{([^}]+)\}', re.DOTALL)
            for match in module_pattern.finditer(content):
                module_name = match.group(1)
                module_body = match.group(2)
                
                app_match = app_name_pattern.search(module_body)
                ns_match = namespace_pattern.search(module_body)
                
                if app_match:
                    # Try to resolve the app name
                    app_name = app_match.group(1) or app_match.group(3)  # Direct string or unquoted
                    if app_match.group(2):  # local.variable
                        app_name = locals_map.get(app_match.group(2), f"local.{app_match.group(2)}")
                    
                    ns_name = None
                    if ns_match:
                        ns_name = ns_match.group(1) or ns_match.group(3)
                        if ns_match.group(2):
                            ns_name = locals_map.get(ns_match.group(2), f"local.{ns_match.group(2)}")
                    
                    deployments.append({
                        "module_name": module_name,
                        "app_name": app_name,
                        "namespace": ns_name,
                        "type": "module"
                    })
            
            # Look for Helm releases (common for EKS/GKE)
            helm_pattern = re.compile(r'resource\s+"helm_release"\s+"([^"]+)"\s*\{([^}]+)\}', re.DOTALL)
            for match in helm_pattern.finditer(content):
                release_label = match.group(1)
                release_body = match.group(2)
                
                name_match = helm_name_pattern.search(release_body)
                ns_match = namespace_pattern.search(release_body)
                
                if name_match:
                    deployments.append({
                        "module_name": release_label,
                        "app_name": name_match.group(1),
                        "namespace": ns_match.group(1) if ns_match else None,
                        "type": "helm"
                    })
            
            # Look for kubernetes_deployment resources
            k8s_deploy_pattern = re.compile(r'resource\s+"kubernetes_deployment"\s+"([^"]+)"\s*\{.*?metadata\s*\{.*?name\s*=\s*"([^"]+)"', re.DOTALL)
            for match in k8s_deploy_pattern.finditer(content):
                deployments.append({
                    "module_name": match.group(1),
                    "app_name": match.group(2),
                    "namespace": None,
                    "type": "kubernetes_deployment"
                })
                
        except Exception:
            continue
    
    return deployments


def _resolve_terraform_variable(base_path: str, var_name: str) -> str | None:
    """Try to resolve a Terraform variable from tfvars files or variable defaults."""
    # Check for tfvars files
    for tfvars_pattern in ["*.tfvars", "*.auto.tfvars", "terraform.tfvars"]:
        for tfvars_file in Path(base_path).rglob(tfvars_pattern):
            try:
                content = tfvars_file.read_text(encoding='utf-8', errors='ignore')
                # Look for: var_name = "value"
                match = re.search(rf'{var_name}\s*=\s*"([^"]+)"', content)
                if match:
                    return match.group(1)
            except Exception:
                continue
    
    # Check variable.tf for default value
    for var_file in Path(base_path).rglob("variable.tf"):
        try:
            content = var_file.read_text(encoding='utf-8', errors='ignore')
            # Find variable block and check for default
            var_block = re.search(rf'variable\s+"{var_name}"\s*{{([^}}]+)}}', content, re.DOTALL)
            if var_block:
                default_match = re.search(r'default\s*=\s*"([^"]+)"', var_block.group(1))
                if default_match:
                    return default_match.group(1)
        except Exception:
            continue
    
    return None


def _extract_database_connection(repo_path: Path | None, module_name: str) -> dict | None:
    """Extract database connection info from Kubernetes module config."""
    if not repo_path or not repo_path.exists():
        return None
    
    for tf_file in repo_path.rglob("*.tf"):
        if "kubernetes" not in tf_file.name.lower():
            continue
        try:
            content = tf_file.read_text(encoding="utf-8", errors="ignore")
            
            # Find module block - look for matching braces carefully
            module_start = content.find(f'module "{module_name}"')
            if module_start == -1:
                continue
            
            # Find the config block within this module
            config_start = content.find("config = {", module_start)
            if config_start == -1:
                continue
            
            # Get a reasonable chunk after config start (next 500 chars should have connection string)
            config_chunk = content[config_start:config_start + 1000]
            
            # Look for Database connection string
            db_conn_match = re.search(r'Database__ConnectionString\s*=\s*"[^"]*Server=([^;]+);Database=([^;]+)', config_chunk)
            if db_conn_match:
                server_ref = db_conn_match.group(1)
                # Try to resolve variable reference to actual value
                if "${var." in server_ref or "${local." in server_ref:
                    var_name = re.search(r'\$\{(?:var|local)\.([^}]+)\}', server_ref)
                    if var_name:
                        # Try to find the variable value in terraform files
                        var_value = _resolve_terraform_variable(str(repo_path), var_name.group(1))
                        if var_value:
                            server_ref = var_value
                        else:
                            # Can't resolve - use generic name
                            server_ref = "SQL Server"
                
                return {
                    "server": server_ref,
                    "database": db_conn_match.group(2)
                }
            
            # Look for other database config patterns
            db_match = re.search(r'(?:database|db)_(?:host|server|name)\s*=\s*"?([^"\s]+)"?', config_chunk, re.IGNORECASE)
            if db_match:
                return {
                    "server": "unknown",
                    "database": db_match.group(1)
                }
                
        except Exception:
            continue
    
    return None


def _extract_service_dependencies(repo_path: Path | None, module_name: str) -> list[str]:
    """Extract outbound service calls from Kubernetes module config."""
    if not repo_path or not repo_path.exists():
        return []
    
    dependencies = []
    for tf_file in repo_path.rglob("*.tf"):
        if "kubernetes" not in tf_file.name.lower():
            continue
        try:
            content = tf_file.read_text(encoding="utf-8", errors="ignore")
            
            # Find module block
            module_start = content.find(f'module "{module_name}"')
            if module_start == -1:
                continue
            
            # Find the config block within this module
            config_start = content.find("config = {", module_start)
            if config_start == -1:
                continue
            
            # Get config block (find matching brace)
            config_chunk = content[config_start:config_start + 3000]
            
            # Look for BaseUri patterns: ServiceName__BaseUri = ".../.../service-path/"
            base_uri_matches = re.findall(
                r'(\w+)__BaseUri\s*=\s*"[^"]*\/([^/"]+)\/"',
                config_chunk
            )
            
            for match in base_uri_matches:
                service_path = match[1]
                # Convert path to readable name: "collectiveapproval" -> "Collective Approval"
                service_name = service_path.replace('-', ' ').replace('_', ' ').title()
                if service_name not in dependencies:
                    dependencies.append(service_name)
            
            # Look for ServiceBus
            if "ServiceBus__FullyQualifiedNamespace" in config_chunk:
                dependencies.append("Service Bus")
                
        except Exception:
            continue
    
    return dependencies


def _extract_application_insights(repo_path: Path | None, module_name: str) -> str | None:
    """Extract Application Insights resource name from Kubernetes module config."""
    if not repo_path or not repo_path.exists():
        return None
    
    for tf_file in repo_path.rglob("*.tf"):
        if "kubernetes" not in tf_file.name.lower():
            continue
        try:
            content = tf_file.read_text(encoding="utf-8", errors="ignore")
            
            # Look for ApplicationInsights__InstrumentationKey or ConnectionString
            app_insights_match = re.search(
                r'ApplicationInsights__(?:InstrumentationKey|ConnectionString)\s*=\s*azurerm_application_insights\.([^.]+)',
                content
            )
            if app_insights_match:
                return app_insights_match.group(1)
                
        except Exception:
            continue
    
    return None


def _is_data_routing_resource(resource_type: str) -> bool:
    """Heuristic: resources that typically receive/forward data-plane traffic."""
    exclude_tokens = (
        "policy_",
        "role_",
        "iam_",
        "client_config",
        "diagnostic",
        "monitor",
        "alert_policy",
        "threat_detection",
        "security_",
        "kms_",
        "firewall_rule",
        "network_rules",
        "_configuration",
        "_plan",
        "_node_pool",
        "_sas",                         # SAS tokens are credentials, not infrastructure services
        "auditing_policy",              # Audit config (e.g. extended_auditing_policy)
        "_transparent_data_encryption", # TDE is config, not a service endpoint
        "_virtual_network_rule",        # VNet access rules are network controls, not services
        "_machine_extension",           # VM extensions are agents installed on VMs, not separate services
    )
    if any(token in resource_type for token in exclude_tokens):
        return False

    include_tokens = (
        "application_gateway",
        "api_management",
        "ingress",
        "load_balancer",
        "elb",
        "_lb",
        "forwarding_rule",
        "registry",
        "ecr",
        "firewall",
        "waf",
        "app_service",
        "function",
        "container",
        "cluster",
        "virtual_machine",
        "instance",
        "server",
        "sql",
        "mysql",
        "postgres",
        "storage",
        "bucket",
        "key_vault",
        "redis",
        "cosmos",
        "neptune",
        "rds",
        "bigquery",
    )
    return any(token in resource_type for token in include_tokens)


def _is_paas_resource(resource_type: str) -> bool:
    """Return True for any resource type that has a known DB category — i.e. should
    appear in a layer subgraph rather than being dropped into the network boundary box."""
    cat = _rtdb.get_resource_type(_get_db(), resource_type).get("category", "")
    return bool(cat)



def _is_network_control_resource(resource_type: str) -> bool:
    control_tokens = (
        "network_rules",
        "firewall",
        "security_group",
        "network_security_group",
        "private_endpoint",
        "subnet",
        "virtual_network",
        "vpc",
        "compute_firewall",
    )
    return any(token in resource_type for token in control_tokens)


def _has_private_access_signal(resource_types: list[str]) -> bool:
    private_tokens = (
        "private_endpoint",
        "private_link",
        "private_service_connect",
        "vpc_endpoint",
        "subnet",
        "virtual_network",
        "vpc",
    )
    return any(any(token in r for token in private_tokens) for r in resource_types)


def _has_restriction_signal(resource_types: list[str]) -> bool:
    restriction_tokens = (
        "firewall",
        "network_rules",
        "security_group",
        "network_security_group",
        "authorized_networks",
        "ingress",
    )
    return any(any(token in r for token in restriction_tokens) for r in resource_types)


def _service_signal_tokens(service: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # private_tokens, restriction_tokens
    if any(t in service for t in ("key_vault",)):
        return (
            ("private_endpoint", "private_link"),
            ("network_acls", "firewall", "network_rules", "private_endpoint", "private_link"),
        )
    if any(t in service for t in ("storage", "s3_bucket", "storage_bucket")):
        return (
            ("private_endpoint", "private_link", "vpc_endpoint", "private_service_connect"),
            ("network_rules", "firewall", "bucket_policy", "public_access_block"),
        )
    if any(t in service for t in ("sql", "mysql", "postgres", "rds", "neptune")):
        return (
            ("private_endpoint", "private_link", "private_service_connect", "db_subnet_group", "subnet"),
            ("firewall", "sql_firewall_rule", "security_group", "network_security_group", "authorized_networks", "virtual_network_rule"),
        )
    if any(t in service for t in ("container_cluster", "gke", "app_service", "function_app")):
        return (
            ("private_endpoint", "private_link", "private_cluster", "private_service_connect"),
            ("compute_firewall", "firewall", "security_group", "network_security_group", "network_policy", "master_authorized_networks"),
        )
    if any(t in service for t in ("kubernetes_cluster", "aks")):
        return (
            ("private_cluster_enabled", "private_endpoint", "private_dns_zone"),
            ("authorized_ip_ranges", "network_security_group", "network_policy", "api_server_access_profile"),
        )
    if any(t in service for t in ("bigquery",)):
        return (
            ("private_service_connect", "private_endpoint"),
            ("access", "iam"),
        )
    return (
        ("private_endpoint", "private_link", "private_service_connect"),
        ("firewall", "network_rules", "security_group", "network_security_group"),
    )


def _service_access_signals(service: str, provider_resources: list[str]) -> tuple[bool, bool]:
    private_tokens, restriction_tokens = _service_signal_tokens(service)
    private_signal = any(any(tok in r for tok in private_tokens) for r in provider_resources)
    restriction_signal = any(any(tok in r for tok in restriction_tokens) for r in provider_resources)
    return private_signal, restriction_signal


def _build_paas_exposure_checks(provider_resources: list[str]) -> str:
    paas_types = sorted({t for t in provider_resources if _is_paas_resource(t)})
    if not paas_types:
        return "No PaaS services detected in Phase 1."

    lines = [
        "| PaaS Service Type | Private Access Signal | Restriction Signal | Potential Exposure |",
        "|---|---|---|---|",
    ]
    for service in paas_types:
        has_private, has_restriction = _service_access_signals(service, provider_resources)
        if has_private and has_restriction:
            exposure = "Medium (controls signaled; validate config)"
        elif has_private or has_restriction:
            exposure = "High (partial controls signaled)"
        else:
            exposure = "Critical (no control signals detected)"
        lines.append(
            f"| `{service}` | {'Yes' if has_private else 'No'} | {'Yes' if has_restriction else 'No'} | {exposure} |"
        )

    lines.append("")
    lines.append(
        "Heuristic only: derived from Terraform resource types in Phase 1; requires property-level validation in Phase 2."
    )
    return "\n".join(lines)


def _network_boundary_label(provider: str) -> str:
    return {"azure": "VNet", "aws": "VPC", "gcp": "VPC Network"}.get(provider, "Network")


def _is_network_boundary_resource(provider: str, resource_type: str) -> bool:
    boundary_types = {
        "azure": {"azurerm_virtual_network"},
        "aws": {"aws_vpc"},
        "gcp": {"google_compute_network"},
    }
    return resource_type in boundary_types.get(provider, set())


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def _relationship_label(src_type: str, dst_type: str) -> tuple[str, bool] | None:
    """Return (label, dashed) for meaningful data/telemetry relationships."""
    if "elb" in src_type and "instance" in dst_type:
        return ("routes", False)
    if "instance" in src_type and any(tok in dst_type for tok in ("db_instance", "rds_cluster", "neptune", "sql", "mysql", "postgres")):
        return ("db access", False)
    if "flow_log" in src_type and "s3_bucket" in dst_type:
        return ("logs", True)
    return None


def _is_edge_gateway_service(service_name: str) -> bool:
    """
    Returns True for resources that ARE the internet boundary — they receive
    inbound traffic on behalf of backends (App Gateway, APIM, Load Balancer,
    Ingress controller). These should not get an auto Internet arrow because
    they ARE the entry point, not a target behind one.

    AKS Cluster is NOT an edge gateway — it is a workload platform that may
    be directly internet-exposed via its attached public IPs / load balancer.
    """
    tokens = (
        "Application Gateway",
        "Load Balancing",
        "Api Management",
        "Ingress",
    )
    return any(tok in service_name for tok in tokens)


def _compute_exposure_signals(
    service_name: str,
    raw_types: list[str],
    repo_path: "Path | None",
) -> tuple[bool, bool]:
    """
    Returns (is_private, has_restriction) for compute/cluster resources by
    inspecting actual Terraform block properties — not just resource type presence.

    Augments the raw-type-based _service_access_signals with property-level
    checks so resources like AKS are assessed correctly based on their config.
    """
    # Start with the existing signal tokens as a baseline
    has_private, has_restriction = _service_access_signals(service_name, raw_types)

    if not repo_path:
        return has_private, has_restriction

    _AKS_TYPES = {"azurerm_kubernetes_cluster", "azurerm_kubernetes_cluster_node_pool"}
    if not any(rt in _AKS_TYPES for rt in raw_types):
        return has_private, has_restriction

    # Property-level inspection of the AKS cluster block
    try:
        blocks = _terraform_resource_blocks(repo_path, prefix="azurerm_kubernetes_cluster")
        for _, _, body in blocks:
            body_lower = body.lower()

            # Private cluster: explicit flag or private DNS zone config
            if re.search(r'private_cluster_enabled\s*=\s*true', body_lower):
                has_private = True

            # Authorized IP ranges restrict API server access (partial control)
            if re.search(r'authorized_ip_ranges\s*=', body_lower):
                has_restriction = True

            # Standard LB with no private cluster = internet-facing load balancer
            if (re.search(r'load_balancer_sku\s*=\s*"standard"', body_lower)
                    and not has_private):
                has_private = False   # explicitly reaffirm: NOT private
    except Exception:
        pass

    return has_private, has_restriction


def _terraform_resource_blocks(repo_path: Path, prefix: str | None = None) -> list[tuple[str, str, str]]:
    """Return Terraform resource blocks as (resource_type, resource_name, body_text)."""
    if not repo_path.exists():
        return []
    block_start_re = re.compile(r'^\s*resource\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s+"([^"]+)"\s*\{')
    blocks: list[tuple[str, str, str]] = []
    for tf in repo_path.rglob("*.tf"):
        try:
            lines = tf.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        i = 0
        while i < len(lines):
            m = block_start_re.match(lines[i])
            if not m:
                i += 1
                continue
            rtype, rname = m.group(1), m.group(2)
            brace = lines[i].count("{") - lines[i].count("}")
            body = [lines[i]]
            i += 1
            while i < len(lines) and brace > 0:
                body.append(lines[i])
                brace += lines[i].count("{") - lines[i].count("}")
                i += 1
            if not prefix or rtype.startswith(prefix):
                blocks.append((rtype, rname, "\n".join(body)))
    return blocks


def _terraform_actual_names(repo_path: Path | None) -> dict[tuple[str, str], str]:
    """Return {(resource_type, terraform_label): actual_name} from Terraform name attributes."""
    if not repo_path:
        return {}
    name_re = re.compile(r'^\s*name\s*=\s*"([^"]+)"')
    result: dict[tuple[str, str], str] = {}
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path):
        for line in body.splitlines():
            m = name_re.match(line)
            if m:
                result[(rtype, rlabel)] = m.group(1)
                break
    return result


_INTERP_RE = re.compile(r'\$\{[^}]*\}')


def _mermaid_safe_name(raw: str) -> str | None:
    """
    Sanitize a Terraform resource name for use inside a Mermaid node label.

    - Replaces Terraform interpolations (${...}) with '*' to preserve the pattern.
    - Returns None if the result is uninformative (only wildcards/punctuation).
    - Strips characters that break Mermaid label parsing (double-quotes, brackets).
    - Ensures resulting IDs do not start with digits by prefixing 'n' when necessary.
    """
    sanitized = _INTERP_RE.sub("*", raw)
    # Collapse adjacent wildcards (e.g. ** → *)
    sanitized = re.sub(r'\*+', '*', sanitized)
    # Remove Mermaid-unsafe characters
    sanitized = sanitized.replace('"', "'").replace("[", "").replace("]", "")
    sanitized = sanitized.strip()
    # Reject if nothing useful remains after stripping wildcards and punctuation
    useful = re.sub(r'[\*\-_/. ]', '', sanitized)
    if not useful:
        return None
    # Truncate long names
    if len(sanitized) > 40:
        sanitized = sanitized[:37] + "..."
    # If the safe name starts with a digit, prefix with 'n' to ensure Mermaid accepts it as an ID
    if re.match(r'^[0-9]', sanitized):
        sanitized = 'n' + sanitized
    # Replace hyphens between word chars with underscore to avoid strict token issues
    sanitized = re.sub(r'(?<=\w)-(?=\w)', '_', sanitized)
    # Replace dots with underscores to avoid ellipsis '...' in IDs which can break strict Mermaid parsers
    sanitized = sanitized.replace('.', '_')
    return sanitized


def _ingress_posture_for_service(repo_path: Path | None, provider: str, service_name: str) -> tuple[str, bool]:
    """
    Return (label, insecure_http) for internet ingress edge.
    insecure_http=True when listener explicitly permits HTTP.
    """
    if repo_path is None or not repo_path.exists():
        return ("ingress", False)
    if provider != "azure" or "application gateway" not in service_name.lower():
        return ("ingress", False)

    protocols: set[str] = set()
    listener_re = re.compile(r"http_listener\s*\{(.*?)\}", re.DOTALL)
    protocol_re = re.compile(r'protocol\s*=\s*"([^"]+)"', re.IGNORECASE)
    for rtype, _, body in _terraform_resource_blocks(repo_path, prefix="azurerm_"):
        if rtype != "azurerm_application_gateway":
            continue
        for listener_match in listener_re.finditer(body):
            listener_body = listener_match.group(1)
            pm = protocol_re.search(listener_body)
            if pm:
                protocols.add(pm.group(1).strip().lower())

    if not protocols:
        return ("ingress", False)
    if protocols == {"https"}:
        return ("HTTPS ingress", False)
    if protocols == {"http"}:
        return ("HTTP ingress", True)
    if "http" in protocols and "https" in protocols:
        return ("HTTP/HTTPS ingress", True)
    return (f"{'/'.join(sorted(p.upper() for p in protocols))} ingress", False)


def _ingress_security_warnings(repo_path: Path | None, provider: str) -> list[str]:
    if repo_path is None or not repo_path.exists():
        return []
    warnings: list[str] = []
    if provider == "azure":
        label, insecure = _ingress_posture_for_service(repo_path, provider, "Azure Application Gateway")
        if insecure:
            warnings.append(
                f"- Warning: Azure Application Gateway allows `{label}` from internet; enforce HTTPS-only listeners and TLS."
            )
    return warnings


def _extract_service_relationships(repo_path: Path, provider: str) -> list[tuple[str, str, str, bool]]:
    """Infer service-to-service relationships from Terraform references."""
    prefix = {"aws": "aws_", "azure": "azurerm_", "gcp": "google_"}.get(provider)
    if not prefix or not repo_path.exists():
        return []

    ref_re = re.compile(rf'({re.escape(prefix)}[A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)')
    blocks = _terraform_resource_blocks(repo_path, prefix=prefix)

    defined = {(rtype, rname) for rtype, rname, _ in blocks}
    rels: set[tuple[str, str, str, bool]] = set()
    for src_type, _src_name, body in blocks:
        for dst_type, dst_name in ref_re.findall(body):
            if (dst_type, dst_name) not in defined:
                continue
            lbl = _relationship_label(src_type, dst_type)
            if not lbl:
                continue
            src_service = _rtdb.get_friendly_name(_get_db(), src_type)
            dst_service = _rtdb.get_friendly_name(_get_db(), dst_type)
            if src_service == dst_service:
                continue
            rels.add((src_service, dst_service, lbl[0], lbl[1]))
    return sorted(rels)


def _build_simple_architecture_diagram(
    repo_name: str,
    provider_resources: dict[str, list[object]],
    repo_path: Path | None = None,
    compact_apim: bool = False,
) -> str:
    lines = ["flowchart LR"]
    edge_lines: list[str] = []
    link_styles: list[str] = []
    node_styles: list[str] = []
    styled_ids: set[str] = set()
    used_node_ids: set[str] = set()  # Track all node IDs to prevent duplicates
    link_index = 0

    category_colors = {
        "app": "#5a9e5a",         # green  (Compute)
        "data": "#4a90d9",        # blue   (Data Services)
        "identity": "#e07b00",    # orange (Identity & Secrets)
        "security": "#8b5cf6",    # purple (Network boundary)
        "monitoring": "#2ab7a9",  # teal   (Monitoring & Alerts)
    }

    def style_id(target_id: str, category: str) -> None:
        if target_id in styled_ids:
            return
        color = category_colors.get(category, category_colors["app"])
        node_styles.append(f"  style {target_id} stroke:{color}, stroke-width:2px")
        styled_ids.add(target_id)

    # Maps DB category → diagram layer key
    _DB_CAT_TO_LAYER: dict[str, str] = {
        "Database": "data",
        "Storage":  "data",
        "Identity": "identity",
        "Monitoring": "monitoring",
        "Security": "security",
        "Network":  "security",
        "Compute":  "app",
        "Container": "app",
    }

    def category_for_raw_types(raw_types: list[str]) -> str:
        """Return diagram layer key by DB category lookup on the first matching terraform type."""
        db = _get_db()
        for rtype in raw_types:
            cat = _rtdb.get_resource_type(db, rtype).get("category", "")
            layer = _DB_CAT_TO_LAYER.get(cat)
            if layer:
                return layer
        return "app"

    def add_link(
        src: str,
        dst: str,
        label: str | None = None,
        red: bool = False,
        orange: bool = False,
        dashed: bool = False,
    ) -> None:
        nonlocal link_index
        # Escape label for Mermaid: wrap in quotes if contains special chars like parentheses
        if label and ('(' in label or ')' in label):
            edge_lines.append(f'  {src} -->|"{label}"| {dst}')
        elif label:
            edge_lines.append(f'  {src} -->|{label}| {dst}')
        else:
            edge_lines.append(f"  {src} --> {dst}")
        if dashed:
            link_styles.append(f"  linkStyle {link_index} stroke-dasharray: 5 5")
        if red:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff0000, stroke-width:3px")
        elif orange:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff8c00, stroke-width:3px")
        link_index += 1

    lines.append('  Internet[Internet Users]')
    if not provider_resources:
        lines.append("  Internet --> Unknown[No cloud provider resources detected]")
        style_id("Unknown", "security")
        return "\n".join(lines)

    for provider, resources in provider_resources.items():
        provider_title = _provider_title(provider)
        provider_id = _safe_id(provider_title)
        boundary_label = _network_boundary_label(provider)

        resource_types = sorted({r.resource_type for r in resources})
        parent_groups = _group_parent_services(resource_types)
        boundary_resources = [r for r in resources if _is_network_boundary_resource(provider, r.resource_type)]
        # Map friendly service name → sorted unique actual instance names (from Terraform name attribute)
        _actual_names = _terraform_actual_names(repo_path)
        service_instances: dict[str, list[str]] = {}
        for r in resources:
            friendly = _rtdb.get_friendly_name(_get_db(), r.resource_type)
            raw_actual = _actual_names.get((r.resource_type, r.name), r.name) if r.name else None
            actual = _mermaid_safe_name(raw_actual) if raw_actual else None
            if actual:
                service_instances.setdefault(friendly, [])
                if actual not in service_instances[friendly]:
                    service_instances[friendly].append(actual)
        non_boundary_parents = {
            parent: raw_types
            for parent, raw_types in parent_groups.items()
            if not any(_is_network_boundary_resource(provider, raw) for raw in raw_types)
        }
        routable_parents = {}
        for parent, raw_types in non_boundary_parents.items():
            routable_children = [raw for raw in raw_types if _is_data_routing_resource(raw)]
            if routable_children:
                routable_parents[parent] = routable_children
        paas_services = [
            parent for parent, raw_types in sorted(routable_parents.items())
            if any(_is_paas_resource(raw) for raw in raw_types)
        ]
        other_services = [
            parent for parent, raw_types in sorted(routable_parents.items())
            if not any(_is_paas_resource(raw) for raw in raw_types)
        ]
        has_alerting_signal = any(
            any(tok in raw for tok in (
                "alert_policy", "threat_detection", "security_alert",
                "monitor_log_profile", "diagnostic_setting",
                "security_center", "flow_log",
            ))
            for raw_types in non_boundary_parents.values()
            for raw in raw_types
        )
        service_anchor_nodes: dict[str, str] = {}

        lines.append(f'  subgraph {provider_id}_Cloud["{provider_title} Services"]')
        lines.append("    direction TB")
        monitoring_node = f"{provider_id}_Monitoring"
        
        # Style the cloud boundary with neutral gray (same as Internet node)
        node_styles.append(f"  style {provider_id}_Cloud stroke:#666, stroke-width:2px")
        styled_ids.add(f"{provider_id}_Cloud")

        # Group paas_services by category layer and render one subgraph per layer
        layer_meta = {
            "data":       ("🗄️ Data Layer",                "data"),
            "identity":   ("🔐 Identity & Secrets",        "identity"),
            "monitoring": ("📈 Monitoring & Telemetry",    "monitoring"),
            "security":   ("🛡️ Network & Security",        "security"),
            "app":        ("⚙️ Compute Layer",             "app"),
        }
        # Preserve a stable layer order
        layer_order = ["data", "identity", "monitoring", "security", "app"]
        services_by_layer: dict[str, list[str]] = {k: [] for k in layer_order}
        for service in paas_services:
            raw_for_cat = sorted(set(non_boundary_parents.get(service, [])))
            cat = category_for_raw_types(raw_for_cat)
            services_by_layer.setdefault(cat, []).append(service)

        svc_global_idx = 0
        for layer_key in layer_order:
            layer_services = services_by_layer.get(layer_key, [])
            if not layer_services:
                # Still render the Monitoring layer if has_alerting_signal and no services were bucketed here
                if layer_key == "monitoring" and has_alerting_signal:
                    layer_label, layer_cat = layer_meta[layer_key]
                    layer_id = f"{provider_id}_Layer_{layer_key.capitalize()}"
                    lines.append(f'    subgraph {layer_id}["{layer_label}"]')
                    style_id(layer_id, layer_cat)
                    lines.append(f'      {monitoring_node}["Security Monitoring"]')
                    style_id(monitoring_node, layer_cat)
                    lines.append("    end")
                continue
            layer_label, layer_cat = layer_meta[layer_key]
            layer_id = f"{provider_id}_Layer_{layer_key.capitalize()}"
            
            # Identify Storage hierarchy: Container and Blob nested inside Storage Account
            _STORAGE_PARENT = "Storage Account"
            _STORAGE_CONTAINER = "Storage Container"
            _STORAGE_BLOB = "Storage Blob"
            storage_nested_services: set[str] = set()
            if _STORAGE_PARENT in layer_services:
                if _STORAGE_CONTAINER in layer_services:
                    storage_nested_services.add(_STORAGE_CONTAINER)
                if _STORAGE_BLOB in layer_services:
                    storage_nested_services.add(_STORAGE_BLOB)

            # Identify SQL hierarchy: Database nested inside SQL Server
            # Detected by raw resource types so it works regardless of DB friendly-name drift
            _SQL_PARENT_TYPES = frozenset({
                "azurerm_mssql_server", "azurerm_sql_server",
                "azurerm_mysql_server", "azurerm_postgresql_server",
            })
            _SQL_CHILD_TYPES = frozenset({
                "azurerm_mssql_database", "azurerm_sql_database",
                "azurerm_mysql_database",
            })
            sql_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _SQL_PARENT_TYPES),
                None,
            )
            sql_nested_services: set[str] = set()
            if sql_parent_service:
                for s in layer_services:
                    if s == sql_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_SQL_CHILD_TYPES):
                        sql_nested_services.add(s)

            # Identify Key Vault hierarchy: keys/secrets nested inside Key Vault
            _KV_PARENT_TYPES = frozenset({"azurerm_key_vault"})
            _KV_CHILD_TYPES = frozenset({
                "azurerm_key_vault_key", "azurerm_key_vault_secret",
                "azurerm_key_vault_certificate",
            })
            kv_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _KV_PARENT_TYPES),
                None,
            )
            kv_nested_services: set[str] = set()
            if kv_parent_service:
                for s in layer_services:
                    if s == kv_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_KV_CHILD_TYPES):
                        kv_nested_services.add(s)

            all_nested = storage_nested_services | sql_nested_services | kv_nested_services

            # Adjust layer service count to account for all nested services
            visible_layer_services = [s for s in layer_services if s not in all_nested]

            # Skip layer wrapper if only one service (reduces visual noise)
            skip_layer_wrapper = len(visible_layer_services) == 1 and not (layer_key == "monitoring" and has_alerting_signal)
            
            if not skip_layer_wrapper:
                lines.append(f'    subgraph {layer_id}["{layer_label}"]')
                style_id(layer_id, layer_cat)
            
            # Place the monitoring node inside the Monitoring layer
            if layer_key == "monitoring" and has_alerting_signal:
                lines.append(f'      {monitoring_node}["Security Monitoring"]')
                style_id(monitoring_node, layer_cat)
            for service in layer_services:
                # Nested services are rendered inside their parent — skip standalone rendering
                if service in all_nested:
                    continue
                svc_global_idx += 1
                p_idx = svc_global_idx
                service_raw = sorted(set(routable_parents.get(service, [])))
                service_raw = _filter_mermaid_children(service, service_raw)
                service_raw_all = sorted(set(non_boundary_parents.get(service, [])))
                exposure_target = None
                
                # Check if this is an API Management service with multiple components
                is_apim_service = service == "API Management" or any(_is_apim_component(r) for r in service_raw)
                non_apim_resources, apim_structure = _group_apim_resources(service_raw, resources, repo_path) if is_apim_service else (service_raw, {})

                # Check if this is a Storage Account that has child services to nest
                is_storage_with_children = service == _STORAGE_PARENT and storage_nested_services
                if is_storage_with_children:
                    sa_names = service_instances.get(service, [])
                    sa_label = f"Storage Account ({sa_names[0]})" if len(sa_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{sa_label}"]')
                    style_id(svc_subgraph, layer_cat)

                    if _STORAGE_CONTAINER in storage_nested_services:
                        container_names = service_instances.get(_STORAGE_CONTAINER, [])
                        container_label = (
                            f"Storage Container ({container_names[0]})"
                            if len(container_names) == 1
                            else _STORAGE_CONTAINER
                        )
                        if _STORAGE_BLOB in storage_nested_services:
                            container_subgraph = f"{svc_subgraph}_Container"
                            lines.append(f'        subgraph {container_subgraph}["{container_label}"]')
                            style_id(container_subgraph, layer_cat)
                            blob_names = service_instances.get(_STORAGE_BLOB, [])
                            blob_label = (
                                f"Storage Blob ({blob_names[0]})"
                                if len(blob_names) == 1
                                else "Storage Blob"
                            )
                            blob_node = f"{svc_subgraph}_Blob"
                            lines.append(f'          {blob_node}["{blob_label}"]')
                            style_id(blob_node, layer_cat)
                            lines.append("        end")
                        else:
                            container_node = f"{svc_subgraph}_Container"
                            lines.append(f'        {container_node}["{container_label}"]')
                            style_id(container_node, layer_cat)
                    elif _STORAGE_BLOB in storage_nested_services:
                        blob_names = service_instances.get(_STORAGE_BLOB, [])
                        blob_label = (
                            f"Storage Blob ({blob_names[0]})" if len(blob_names) == 1 else "Storage Blob"
                        )
                        blob_node = f"{svc_subgraph}_Blob"
                        lines.append(f'        {blob_node}["{blob_label}"]')
                        style_id(blob_node, layer_cat)

                    lines.append("      end")
                    exposure_target = svc_subgraph
                    # Jump to exposure handling; skip the standard rendering branches below
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        if not _is_edge_gateway_service(service):
                            has_private, has_restriction = _compute_exposure_signals(service, service_raw, repo_path)
                            if not has_private and not has_restriction:
                                add_link("Internet", exposure_target, label="Public exposure", red=True)
                            elif has_private != has_restriction:
                                add_link("Internet", exposure_target, label="Partial controls", orange=True)
                    continue

                # SQL Server with nested databases
                is_sql_with_children = service == sql_parent_service and bool(sql_nested_services)
                if is_sql_with_children:
                    sa_names = service_instances.get(service, [])
                    sql_label = f"SQL Server ({sa_names[0]})" if len(sa_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{sql_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for db_svc in sorted(sql_nested_services):
                        db_names = service_instances.get(db_svc, [])
                        db_label = f"DB: {db_names[0]}" if len(db_names) == 1 else db_svc
                        db_node = f"{svc_subgraph}_DB"
                        lines.append(f'        {db_node}["{db_label}"]')
                        style_id(db_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        if not _is_edge_gateway_service(service):
                            has_private, has_restriction = _compute_exposure_signals(service, service_raw_all, repo_path)
                            if not has_private and not has_restriction:
                                add_link("Internet", exposure_target, label="Public exposure", red=True)
                            elif has_private != has_restriction:
                                add_link("Internet", exposure_target, label="Partial controls", orange=True)
                    continue

                # Key Vault with nested keys/secrets
                is_kv_with_children = service == kv_parent_service and bool(kv_nested_services)
                if is_kv_with_children:
                    kv_names = service_instances.get(service, [])
                    kv_label = f"Key Vault ({kv_names[0]})" if len(kv_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{kv_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for kv_child in sorted(kv_nested_services):
                        child_names = service_instances.get(kv_child, [])
                        child_label = f"{kv_child} ({child_names[0]})" if len(child_names) == 1 else kv_child
                        child_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', kv_child)}"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        if not _is_edge_gateway_service(service):
                            has_private, has_restriction = _compute_exposure_signals(service, service_raw, repo_path)
                            if not has_private and not has_restriction:
                                add_link("Internet", exposure_target, label="Public exposure", red=True)
                            elif has_private != has_restriction:
                                add_link("Internet", exposure_target, label="Partial controls", orange=True)
                    continue
                
                if len(service_raw) <= 1:
                    node_id = f"{layer_id}_Svc_{p_idx}"
                    names = service_instances.get(service, [])
                    name_suffix = f" ({names[0]})" if len(names) == 1 else ""
                    lines.append(f'      {node_id}["{service}{name_suffix}"]')
                    style_id(node_id, layer_cat)
                    exposure_target = node_id
                elif apim_structure:
                    # Special handling for API Management: create nested structure by API
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["🔌 {service}"]')
                    style_id(svc_subgraph, layer_cat)
                    operation_nodes = []  # Collect all operation nodes for ingress connections
                    operation_start_idx = 0  # Default start index for operation nodes (safe fallback)
                    apim_requires_auth = False  # Track if any API requires auth
                    backend_services = []  # Track backend services to render outside APIM
                    
                    # Render each API as a nested subgraph
                    api_idx = 0
                    for api_name, api_components in apim_structure.items():
                        api_idx += 1
                        api_subgraph = f"{svc_subgraph}_API_{api_idx}"
                        
                        # Check if API requires subscription (auth)
                        has_subscriptions = len(api_components.get("subscriptions", [])) > 0
                        if has_subscriptions:
                            apim_requires_auth = True
                        api_label = f"API: {api_name}"
                        if has_subscriptions:
                            api_label += " 🔑"
                        
                        lines.append(f'        subgraph {api_subgraph}["{api_label}"]')
                        style_id(api_subgraph, "app")  # APIs are application endpoints, not security controls
                        
                        # Render API node and its operations (so Internet → operation links can be drawn)
                        lines.append(f'          {api_subgraph}_Node["API: {api_name}"]')
                        style_id(f'{api_subgraph}_Node', 'app')
                        operation_nodes.append(f'{api_subgraph}_Node')
                        # Render operations as separate nodes beneath the API (if present)
                        for op in api_components.get('operations', []):
                            # Sanitize: replace hyphens with underscores
                            safe_op_id = op.replace('-', '_')
                            # Remove non-alphanumeric
                            safe_op_id = re.sub(r'[^A-Za-z0-9_]', '', safe_op_id)
                            # Prefix with 'op' to ensure node ID part doesn't start with digit
                            safe_op_id = 'op' + safe_op_id
                            op_node = f'{api_subgraph}_{safe_op_id}'
                            lines.append(f'          {op_node}["{op}"]')
                            style_id(op_node, 'app')
                            operation_nodes.append(op_node)
                        
                        # Note: Policies are documented in markdown, not shown in diagram
                        
                        lines.append("        end")
                        
                        # Extract backend routing info (render later outside APIM)
                        backend_url = _extract_apim_backend_url(repo_path, api_name)
                        if backend_url:
                            k8s_deployments = _extract_kubernetes_deployments(repo_path)
                            if k8s_deployments:
                                for deployment in k8s_deployments:
                                    if deployment["module_name"] in ("api", "app", "service"):
                                        # Check for database connections in this deployment
                                        db_connection = _extract_database_connection(repo_path, deployment["module_name"])
                                        # Check for Application Insights
                                        app_insights = _extract_application_insights(repo_path, deployment["module_name"])
                                        # Check for service dependencies
                                        dependencies = _extract_service_dependencies(repo_path, deployment["module_name"])
                                        backend_services.append({
                                            "name": deployment["app_name"],
                                            "label": f"☸️ AKS: {deployment['app_name']}",
                                            "ops_slice": (operation_start_idx, len(operation_nodes)),
                                            "database": db_connection,
                                            "app_insights": app_insights,
                                            "dependencies": dependencies
                                        })
                                        break
                                if not backend_services or backend_services[-1]["ops_slice"][0] != operation_start_idx:
                                    deployment = k8s_deployments[0]
                                    deploy_type = deployment.get("type", "module")
                                    if deploy_type == "helm":
                                        label = f"⎈ K8s: {deployment['app_name']}"
                                    else:
                                        label = f"☸️ K8s: {deployment['app_name']}"
                                    db_connection = _extract_database_connection(repo_path, deployment["module_name"])
                                    backend_services.append({
                                        "name": deployment["app_name"],
                                        "label": label,
                                        "ops_slice": (operation_start_idx, len(operation_nodes)),
                                        "database": db_connection
                                    })
                    
                    # Add non-APIM resources if any
                    for resource_type in non_apim_resources:
                        child_idx = api_idx + 1
                        child_node = f"{svc_subgraph}_Child_{child_idx}"
                        child_label = _child_node_label(resource_type, service)
                        child_instances = [
                            _mermaid_safe_name(_actual_names.get((resource_type, r.name), r.name) or "") or ""
                            for r in resources if r.resource_type == resource_type and r.name
                        ]
                        child_instances = [n for n in child_instances if n]
                        if len(child_instances) == 1:
                            child_label = f"{child_label} ({child_instances[0]})"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                    
                    lines.append("      end")
                    
                    # Render backend services outside APIM (in Compute layer context)
                    for backend in backend_services:
                        # Create AKS cluster subgraph with app inside
                        backend_cluster_node = f"{svc_subgraph}_Backend_Cluster_{backend['name'].replace('-', '_')}"
                        # Show as shared/external cluster if not managed in this repo
                        cluster_label = "☸️ AKS Cluster (shared)"
                        lines.append(f'      subgraph {backend_cluster_node}["{cluster_label}"]')
                        style_id(backend_cluster_node, "app")
                        
                        # App service node inside cluster
                        backend_app_node = f"{backend_cluster_node}_App"
                        lines.append(f'        {backend_app_node}["🎯 {backend["name"]}"]')
                        style_id(backend_app_node, "app")
                        lines.append("      end")
                        
                        # Connect operations to the app inside cluster
                        start_idx, end_idx = backend["ops_slice"]
                        for op_node in operation_nodes[start_idx:end_idx]:
                            add_link(op_node, backend_app_node, label="routes to")
                        
                        # Create Data subgraph if database or Service Bus exists
                        has_database = backend.get("database")
                        has_service_bus = backend.get("dependencies") and "Service Bus" in backend.get("dependencies", [])
                        
                        if has_database or has_service_bus:
                            # Create Data layer subgraph
                            data_subgraph = f"{backend_cluster_node}_Data"
                            lines.append(f'      subgraph {data_subgraph}["💾 Data Layer"]')
                            style_id(data_subgraph, "data")
                            
                            # Add SQL Server subgraph with database inside
                            if has_database:
                                db_info = backend["database"]
                                db_name = db_info.get("database", "Unknown DB")
                                server_name = db_info.get("server", "SQL Server")
                                
                                # SQL Server subgraph inside Data layer
                                db_server_node = f"{data_subgraph}_SQLServer"
                                lines.append(f'        subgraph {db_server_node}["🗄️ {server_name}"]')
                                style_id(db_server_node, "data")
                                
                                db_node = f"{db_server_node}_DB"
                                lines.append(f'          {db_node}["🗃️ {db_name}"]')
                                style_id(db_node, "data")
                                lines.append("        end")
                            
                            # Add Service Bus inside Data layer as a subgraph with queues/topics
                            if has_service_bus:
                                sb_subgraph = f"{data_subgraph}_ServiceBus"
                                sb_root = f"{sb_subgraph}_Root"
                                lines.append(f'        subgraph {sb_subgraph}["🚌 Service Bus"]')
                                style_id(sb_subgraph, "data")
                                # Root namespace node
                                lines.append(f'          {sb_root}["Service Bus Namespace"]')
                                style_id(sb_root, "data")
                                # Render topics, queues, subscriptions found in provider resources
                                for r in resources:
                                    if r.resource_type in ("azurerm_servicebus_topic", "azurerm_servicebus_queue", "azurerm_servicebus_subscription"):
                                        # Generate unique node ID by appending counter if duplicate
                                        base_node_id = f"{sb_subgraph}_{_mermaid_safe_name(r.name) or 'res'}"
                                        node_id = base_node_id
                                        counter = 1
                                        while node_id in used_node_ids:
                                            node_id = f"{base_node_id}_{counter}"
                                            counter += 1
                                        used_node_ids.add(node_id)
                                        
                                        label = r.name or r.resource_type
                                        lines.append(f'          {node_id}["{label}"]')
                                        style_id(node_id, "data")
                                        # connect namespace root to child
                                        add_link(sb_root, node_id)
                                lines.append("        end")
                            # Close Data subgraph
                            lines.append("      end")
                            
                            # Create connections
                            if has_database:
                                add_link(backend_app_node, db_node, label="queries")
                            if has_service_bus:
                                # Link app to Service Bus namespace root for messages
                                add_link(backend_app_node, sb_root, label="messages")
                        
                        # Add Application Insights connection if detected
                        if backend.get("app_insights"):
                            app_insights_name = backend["app_insights"]
                            
                            # Create Monitoring subgraph with App Insights inside
                            monitoring_subgraph = f"{backend_cluster_node}_Monitoring"
                            lines.append(f'      subgraph {monitoring_subgraph}["📊 Monitoring"]')
                            style_id(monitoring_subgraph, "monitoring")
                            
                            app_insights_node = f"{monitoring_subgraph}_AppInsights"
                            lines.append(f'        {app_insights_node}["📈 Application Insights"]')
                            style_id(app_insights_node, "monitoring")
                            lines.append("      end")
                            
                            add_link(backend_app_node, app_insights_node, label="telemetry")
                        
                        # Add egress APIM subgraph for service dependencies (APIs via APIM)
                        egress_dependencies = [dep for dep in backend.get("dependencies", []) if dep != "Service Bus"]
                        if egress_dependencies:
                            # Create APIM Egress subgraph
                            egress_apim_subgraph = f"{backend_cluster_node}_APIM_Egress"
                            lines.append(f'      subgraph {egress_apim_subgraph}["🔌 API Management"]')
                            style_id(egress_apim_subgraph, "security")
                            
                            # Add each backend API inside APIM
                            egress_api_nodes = []
                            for dep in egress_dependencies:
                                dep_node = f"{egress_apim_subgraph}_{dep.replace(' ', '_')}"
                                lines.append(f'        {dep_node}["📡 {dep}"]')
                                style_id(dep_node, "app")
                                egress_api_nodes.append(dep_node)
                            
                            lines.append("      end")
                            
                            # Connect app to each API in APIM egress subgraph
                            for egress_node in egress_api_nodes:
                                add_link(backend_app_node, egress_node, label="Subscription Key")
                    
                    # Pass both operation nodes and auth status as tuple
                    exposure_target = (operation_nodes, apim_requires_auth) if operation_nodes else None
                else:
                    # Standard multi-component service rendering
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{service}"]')
                    style_id(svc_subgraph, layer_cat)
                    first_child = None
                    for c_idx, child in enumerate(service_raw, start=1):
                        child_node = f"{svc_subgraph}_Child_{c_idx}"
                        child_label = _child_node_label(child, service)
                        # Append instance name if there's exactly one for this raw type
                        child_instances = [
                            _mermaid_safe_name(_actual_names.get((child, r.name), r.name) or "") or ""
                            for r in resources if r.resource_type == child and r.name
                        ]
                        child_instances = [n for n in child_instances if n]
                        if len(child_instances) == 1:
                            child_label = f"{child_label} ({child_instances[0]})"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                        if first_child is None:
                            first_child = child_node
                    lines.append("      end")
                    exposure_target = first_child

                if exposure_target:
                    # Handle both single target and list of targets (for APIM operations)
                    if isinstance(exposure_target, tuple):
                        # APIM case: tuple of (operation_nodes, requires_auth)
                        operation_nodes, requires_auth = exposure_target
                        service_anchor_nodes[service] = operation_nodes[0] if operation_nodes else None
                        # Edge gateways (App Gateway, LB, APIM) are internet-facing by design
                        # Create links to each operation
                        if not _is_edge_gateway_service(service):
                            label = "Public (requires auth)" if requires_auth else "Public exposure"
                            for op_node in operation_nodes:
                                add_link("Internet", op_node, label=label, red=True)
                    else:
                        # Standard case: single exposure point
                        service_anchor_nodes[service] = exposure_target
                        # Edge gateways (App Gateway, LB, APIM) are internet-facing by design —
                        # skip the generic "Public exposure" arrow; the protocol ingress arrow below handles it.
                        if not _is_edge_gateway_service(service):
                            has_private, has_restriction = _compute_exposure_signals(service, service_raw, repo_path)
                            if not has_private and not has_restriction:
                                add_link("Internet", exposure_target, label="Public exposure", red=True)
                            elif has_private != has_restriction:
                                add_link("Internet", exposure_target, label="Partial controls", orange=True)
                    
                    # Alerting links (use first node if tuple/list)
                    if isinstance(exposure_target, tuple):
                        target_for_alerts = exposure_target[0][0] if exposure_target[0] else None  # First operation node
                    else:
                        target_for_alerts = exposure_target
                    if has_alerting_signal and any(
                        tok in raw for raw in service_raw_all for tok in (
                            "alert_policy", "threat_detection", "security_alert",
                            "monitor_log_profile", "diagnostic_setting",
                            "security_center", "flow_log",
                        )
                    ):
                        add_link(target_for_alerts, monitoring_node, label="logs/alerts", dashed=True)
            
            # Close layer wrapper only if we opened it
            if not skip_layer_wrapper:
                lines.append("    end")


        networks = (boundary_resources if boundary_resources else [None]) if other_services else []
        for idx, boundary in enumerate(networks, start=1):
            net_id = f"{provider_id}_Net_{idx}" if boundary is not None else f"{provider_id}_Net_Default"
            net_name = (
                f"{boundary_label}: {boundary.name}"
                if boundary is not None
                else f"{boundary_label}: Unspecified"
            )
            lines.append(f'    subgraph {net_id}["{net_name}"]')
            style_id(net_id, "security")

            if idx == 1 and other_services:
                for o_idx, service in enumerate(other_services, start=1):
                    service_raw = sorted(set(routable_parents.get(service, [])))
                    service_raw = _filter_mermaid_children(service, service_raw)
                    svc_cat = category_for_raw_types(sorted(set(non_boundary_parents.get(service, []))))
                    if len(service_raw) <= 1:
                        node_id = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      {node_id}["{service}"]')
                        style_id(node_id, svc_cat)
                        service_anchor_nodes[service] = node_id
                    else:
                        svc_subgraph = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      subgraph {svc_subgraph}["{service}"]')
                        style_id(svc_subgraph, svc_cat)
                        first_child = None
                        for c_idx, child in enumerate(service_raw, start=1):
                            child_node = f"{svc_subgraph}_Child_{c_idx}"
                            child_label = _child_node_label(child, service)
                            lines.append(f'        {child_node}["{child_label}"]')
                            style_id(child_node, svc_cat)
                            if first_child is None:
                                first_child = child_node
                        lines.append("      end")
                        if first_child:
                            service_anchor_nodes[service] = first_child

            lines.append("    end")

        # Draw dashed monitoring edges: trace from monitoring-signal resource types
        # back to their parent service anchor node. Deduplicated per anchor.
        _monitoring_tokens = (
            "alert_policy", "threat_detection", "security_alert",
            "monitor_log_profile", "diagnostic_setting",
            "security_center", "flow_log",
        )
        if has_alerting_signal:
            _parent_type_map: dict[str, str] = {}
            try:
                rows = _get_db().execute(
                    "SELECT terraform_type, parent_type FROM resource_types WHERE parent_type IS NOT NULL"
                ).fetchall()
                _parent_type_map = {row[0]: row[1] for row in rows}
            except Exception:
                pass

            monitoring_sourced: set[str] = set()  # deduplicate per anchor node
            for r in resources:
                if not any(tok in r.resource_type for tok in _monitoring_tokens):
                    continue
                anchor: str | None = None
                # Prioritise type-level parent (structural) over TF references (can be storage destinations)
                parent_tf_type = _parent_type_map.get(r.resource_type, "")
                if parent_tf_type:
                    parent_friendly = _rtdb.get_friendly_name(_get_db(), parent_tf_type)
                    anchor = service_anchor_nodes.get(parent_friendly)
                # Fall back to TF reference parent only if no type-level match
                if not anchor and r.parent:
                    parent_tf_type = r.parent.split(".")[0]
                    parent_friendly = _rtdb.get_friendly_name(_get_db(), parent_tf_type)
                    anchor = service_anchor_nodes.get(parent_friendly)
                # Skip subscription-level resources (Security Center, Monitor Log Profile)
                # — they are the monitoring infrastructure, not a source feeding it
                if anchor and anchor not in monitoring_sourced and anchor != monitoring_node:
                    monitoring_sourced.add(anchor)
                    add_link(anchor, monitoring_node, label="logs/alerts", dashed=True)

        if repo_path is not None:
            for src_service, dst_service, rel_label, is_dashed in _extract_service_relationships(repo_path, provider):
                src_node = service_anchor_nodes.get(src_service)
                dst_node = service_anchor_nodes.get(dst_service)
                if src_node and dst_node:
                    add_link(src_node, dst_node, label=rel_label, dashed=is_dashed)

        # Explicitly show ingress to gateway-like edge services.
        for service_name, node_id in service_anchor_nodes.items():
            if _is_edge_gateway_service(service_name):
                ingress_label, insecure_http = _ingress_posture_for_service(repo_path, provider, service_name)
                add_link("Internet", node_id, label=ingress_label, red=insecure_http)

        lines.append("  end")

    lines.extend(edge_lines)
    # Append link and node style directives directly so Mermaid renders colored borders.
    # NOTE: Some Mermaid parsers may be strict; enable at risk of parser incompatibility.
    if link_styles:
        for ls in link_styles:
            lines.append(f'  {ls.strip()}')
    if node_styles:
        for ns in node_styles:
            lines.append(f'  {ns.strip()}')
    
    diagram = "\n".join(lines)
    # Sanitize hyphens inside unquoted tokens (IDs) by replacing hyphens between word chars with underscores.
    # Keeps quoted labels intact.
    def _sanitize_ids(text: str) -> str:
        out_lines: list[str] = []
        for line in text.splitlines():
            # Split on quoted strings (with optional trailing space/punctuation)
            parts = re.split(r'(".*?")', line)
            # The regex splits quoted strings; odd-indexed parts are quoted, even-indexed are not
            for i, part in enumerate(parts):
                if i % 2 == 0:
                    # Replace hyphens between word characters with underscore (v1-get -> v1_get)
                    part = re.sub(r'(?<=\w)-(?=\w)', '_', part)
                    parts[i] = part
            out_lines.append(''.join(parts))
        return '\n'.join(out_lines)

    diagram = _sanitize_ids(diagram)
    return diagram


def _inventory_annotation(resource_type: str) -> str:
    """Return a short annotation tag for resources excluded from the Mermaid diagram."""
    if "_sas" in resource_type:
        return " `[authentication — see Roles & Permissions]`"
    if "role_assignment" in resource_type:
        return " `[RBAC — see Roles & Permissions]`"
    if "role_definition" in resource_type:
        return " `[RBAC role def — not on diagram]`"
    if "auditing_policy" in resource_type:
        return " `[audit policy — not on diagram]`"
    if "_transparent_data_encryption" in resource_type:
        return " `[encryption config — not on diagram]`"
    if "_virtual_network_rule" in resource_type:
        return " `[network control — restricts public access]`"
    if "_machine_extension" in resource_type or "_vm_extension" in resource_type:
        return " `[VM agent/extension — not on diagram]`"
    if "firewall_rule" in resource_type or "sql_firewall" in resource_type:
        return " `[firewall rule — not on diagram]`"
    if "policy_" in resource_type or "_policy" in resource_type:
        return " `[policy — not on diagram]`"
    if "diagnostic" in resource_type or "monitor" in resource_type:
        return " `[monitoring config — not on diagram]`"
    return ""


def _build_enrichment_assumptions(repo_name: str) -> str:
    """
    Query the enrichment_queue for pending assumptions tied to this repo's nodes
    and format them as a readable section for human review.
    """
    try:
        from persist_graph import query_graph_for_repo
        graph = query_graph_for_repo(repo_name)
        assumptions = graph.get("assumptions", [])
    except Exception:
        return "- Knowledge graph not yet populated for this repo."

    if not assumptions:
        return "- No unresolved assumptions. All extracted relationships are confirmed."

    high   = [a for a in assumptions if a.get("confidence") == "high"]
    medium = [a for a in assumptions if a.get("confidence") == "medium"]
    low    = [a for a in assumptions if a.get("confidence") == "low"]

    lines: list[str] = []
    if high:
        lines.append("#### 🟠 Likely — please confirm")
        for a in high:
            text = a.get("assumption_text") or a.get("context", "Unknown")
            hint = a.get("suggested_value", "")
            lines.append(f"- **{text}**")
            if hint:
                lines.append(f"  - *Suggestion:* {hint}")
    if medium:
        lines.append("\n#### 🟡 Possible — needs input")
        for a in medium:
            text = a.get("assumption_text") or a.get("context", "Unknown")
            basis = a.get("assumption_basis", "")
            lines.append(f"- {text}" + (f" *(basis: {basis})*" if basis else ""))
    if low:
        lines.append("\n#### ⚪ Unclear — variable references")
        for a in low:
            ctx = a.get("context", "Unknown")
            lines.append(f"- {ctx}")

    return "\n".join(lines)


def _build_resource_inventory(
    provider_resources: dict,
    repo_path: "Path | None" = None,
) -> str:
    """Build a full indented Markdown resource inventory, including items excluded from the diagram."""
    if not provider_resources:
        return "- No cloud resources detected."

    _DB_CAT_TO_LAYER: dict[str, str] = {
        "Database": "data",
        "Storage":  "data",
        "Identity": "identity",
        "Monitoring": "monitoring",
        "Security": "security",
        "Network":  "security",
        "Compute":  "app",
        "Container": "app",
    }
    _LAYER_LABEL: dict[str, str] = {
        "data":       "🗄️ Data Layer",
        "identity":   "🔐 Identity & Secrets",
        "monitoring": "📈 Monitoring & Telemetry",
        "security":   "🛡️ Network & Security",
        "app":        "⚙️ Compute Layer",
        "other":      "⚙️ Other Resources",
    }
    layer_order = ["data", "identity", "monitoring", "security", "app", "other"]

    db = _get_db()
    actual_names = _terraform_actual_names(repo_path)

    lines: list[str] = []
    for provider, resources in provider_resources.items():
        if not resources:
            continue

        # Build instance names: friendly_name → [actual_names]
        service_instances: dict[str, list[str]] = {}
        # Also build raw_type → [actual_names] for individual raw types
        raw_instances: dict[str, list[str]] = {}
        for r in resources:
            friendly = _rtdb.get_friendly_name(db, r.resource_type)
            raw_actual = actual_names.get((r.resource_type, r.name), r.name) if r.name else None
            actual = _mermaid_safe_name(raw_actual) if raw_actual else r.name
            if actual:
                service_instances.setdefault(friendly, [])
                if actual not in service_instances[friendly]:
                    service_instances[friendly].append(actual)
                raw_instances.setdefault(r.resource_type, [])
                if actual not in raw_instances[r.resource_type]:
                    raw_instances[r.resource_type].append(actual)

        # Group ALL resource types (not filtered) into parent services
        resource_types = sorted({r.resource_type for r in resources})
        all_groups = _group_parent_services(resource_types)

        # Bucket parent services by layer
        by_layer: dict[str, list[str]] = {k: [] for k in layer_order}
        for friendly, raw_types in all_groups.items():
            layer = "other"
            for rtype in raw_types:
                cat = _rtdb.get_resource_type(db, rtype).get("category", "")
                mapped = _DB_CAT_TO_LAYER.get(cat)
                if mapped:
                    layer = mapped
                    break
            by_layer[layer].append(friendly)

        for layer_key in layer_order:
            services = sorted(by_layer[layer_key])
            if not services:
                continue
            lines.append(f"\n### {_LAYER_LABEL[layer_key]}\n")
            for friendly in services:
                raw_types = all_groups[friendly]
                inst_names = service_instances.get(friendly, [])
                inst_suffix = f" *({', '.join(inst_names[:3])}{'...' if len(inst_names) > 3 else ''})*" if inst_names else ""
                on_diagram = any(_is_data_routing_resource(rt) for rt in raw_types)
                annotation = "" if on_diagram else _inventory_annotation(raw_types[0])
                if len(raw_types) == 1:
                    lines.append(f"- **{friendly}**{inst_suffix}{annotation}")
                else:
                    # Multiple raw types share the same friendly name — show parent + children
                    lines.append(f"- **{friendly}**{inst_suffix}")
                    for rtype in sorted(raw_types):
                        child_inst = raw_instances.get(rtype, [])
                        child_suffix = f" *({', '.join(child_inst[:2])}{'...' if len(child_inst) > 2 else ''})*" if child_inst else ""
                        child_ann = _inventory_annotation(rtype) if not _is_data_routing_resource(rtype) else ""
                        type_label = _resource_kind_label(rtype)
                        lines.append(f"  - {type_label}{child_suffix}{child_ann}")

    return "\n".join(lines).strip() if lines else "- No resources detected."


def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    context: RepositoryContext,
    summary_dir: Path,
) -> Path:
    out_path = summary_dir / "Repos" / f"{repo_name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resource_types = [r.resource_type for r in context.resources]
    resource_counter = Counter(resource_types)
    provider_resources = {
        provider: [r for r in context.resources if _rtdb.get_provider_key(_get_db(), r.resource_type) == provider]
        for provider in providers
    }

    provider_text = ", ".join(_provider_title(p) for p in providers) if providers else "Unknown"
    language_text = "Terraform" if resource_types else "Unknown"
    hosting_text = provider_text
    ci_cd_text = "Unknown"

    auth_summary = "- No explicit identity resources detected in Phase 1 extraction."
    if any("key_vault" in t for t in resource_types):
        auth_summary = "- Azure Key Vault resources detected."

    network_summary = "- No explicit network segmentation resources detected."
    nsg_resources = [r for r in context.resources if "network_security_group" in r.resource_type]
    subnet_resources = [r for r in context.resources if "subnet" in r.resource_type]
    vnet_resources = [r for r in context.resources if "virtual_network" in r.resource_type or "vpc" in r.resource_type]
    if vnet_resources or nsg_resources or subnet_resources:
        network_summary = "- Network segmentation resources detected:\n"
        if vnet_resources:
            network_summary += f"  - Virtual Networks: {', '.join([r.name for r in vnet_resources])}\n"
        if nsg_resources:
            network_summary += f"  - Network Security Groups: {', '.join([r.name for r in nsg_resources])}\n"
        if subnet_resources:
            network_summary += f"  - Subnets: {', '.join([r.name for r in subnet_resources])}\n"

    # Roles & Permission Assignments
    roles_permissions = ""
    role_assignments = [r for r in context.resources if r.resource_type == "azurerm_role_assignment"]
    if role_assignments:
        roles_permissions += "| Role | Resource | Principal |\n|------|----------|----------|\n"
        for r in role_assignments:
            role = getattr(r, "role_definition_name", "Unknown")
            resource = getattr(r, "scope", "Unknown")
            principal = getattr(r, "principal_id", "Unknown")
            roles_permissions += f"| {role} | {resource} | {principal} |\n"
    else:
        roles_permissions = "- No role assignments detected."

    ingress_summary = f"""```mermaid\nflowchart LR\n    Internet[Internet] --> APIM[API Management APIM]\n    Service["{repo_name}"]\n    APIM -->|Subscription Key| Service\n    %% Styling for red edge\n    linkStyle 0 stroke:#e3342f, stroke-width:2px\n```"""

    egress_summary = f"""```mermaid\nflowchart LR\n    Service["{repo_name}"]\n    subgraph APIM_Egress[🔌 API Management]\n      subgraph Payments[payments]\n        Payments_health_check[health_check]\n        Payments_v1_get_accounts[v1-get-accounts]\n      end\n      subgraph Notifications[notifications]\n        Notifications_v1_get_users[v1-get-users]\n        Notifications_v1_get_user_hidden_accounts[v1-get-user-hidden-accounts]\n      end\n    end\n    Service -->|APIM Subscription Key| Payments_health_check\n    Service -->|APIM Subscription Key| Payments_v1_get_accounts\n    Service -->|APIM Subscription Key| Notifications_v1_get_users\n    Service -->|APIM Subscription Key| Notifications_v1_get_user_hidden_accounts\n```"""

    # Override ingress/egress examples with detected APIs (if present)
    api_types = (
        "azurerm_api_management_api",
        "aws_api_gateway_rest_api",
        "aws_apigatewayv2_api",
        "google_api_gateway_api",
    )
    # Only include APIs whose resource blocks are present in this repo (context.resources)
    apis = []
    for r in context.resources:
        if getattr(r, "resource_type", None) in api_types and getattr(r, "name", None):
            # Prefer explicit API name if available, else use resource label
            apis.append(getattr(r, "name") or getattr(r, "resource_type"))
    if apis:
        ingress_lines = [
            "```mermaid",
            "flowchart LR",
            "    Internet[Internet] --> APIM[API Management APIM]",
            f'    Service["{repo_name}"]',
        ]
        for i, api in enumerate(apis[:5], start=1):
            api_id = f"API{i}"
            ingress_lines.append(f'    APIM --> {api_id}["{api}"]')
            ingress_lines.append(f'    {api_id} -->|Subscription Key| Service')
        ingress_lines.append("```")
        ingress_summary = "\n".join(ingress_lines)

        egress_lines = [
            "```mermaid",
            "flowchart LR",
            f'    Service["{repo_name}"]',
        ]
        for i, api in enumerate(apis[:5], start=1):
            api_id = f"API{i}"
            egress_lines.append(f'    Service --> {api_id}["{api}"]')
        egress_lines.append("```")
        egress_summary = "\n".join(egress_lines)
    else:
        # No APIs detected in this repository's IaC resources — avoid showing placeholder samples.
        ingress_summary = "- No API Management APIs detected in IaC resources."

        egress_summary = "- No APIM egress detected from IaC resources. External dependencies are listed below if found."

    # Determine external dependencies from detected connections and Kubernetes module configs
    external_targets: set[str] = set()
    # Build set of local resource names to distinguish internal targets
    local_resource_names = {r.name for r in context.resources if getattr(r, 'name', None)}

    # Use explicit connections detected by context_extraction (if present)
    for conn in context.connections:
        try:
            src = getattr(conn, 'source', '')
            tgt = getattr(conn, 'target', '')
            # If source is local (repo or a local resource) and target is not a local resource or Internet
            if (src == repo_name or src in local_resource_names) and tgt and tgt not in local_resource_names and tgt != 'Internet':
                external_targets.add(tgt)
        except Exception:
            continue

    # Also inspect Kubernetes module configs for outbound service BaseUri patterns
    try:
        k8s_deps = []
        deployments = _extract_kubernetes_deployments(repo_path) if repo_path is not None else []
        for d in deployments:
            deps = _extract_service_dependencies(repo_path, d.get('module_name')) if repo_path is not None else []
            for dep in deps:
                external_targets.add(dep)
    except Exception:
        pass

    if external_targets:
        lines = ["- External dependencies detected:"]
        for t in sorted(external_targets):
            lines.append(f"  - {t}")
        external_deps = "\n".join(lines)
    else:
        external_deps = "- No external dependencies detected in Phase 1."

    top_evidence = []
    for resource_type, count in resource_counter.most_common(10):
        top_evidence.append(f"- `{resource_type}` x{count}")
    # Add file name and line number for each evidence item if available
    evidence_details = []
    for resource_type, count in resource_counter.most_common(10):
        evidence_items = [r for r in context.resources if r.resource_type == resource_type]
        for item in evidence_items:
            friendly = _rtdb.get_friendly_name(_get_db(), resource_type)
            alias = getattr(item, 'alias', None)
            alias_str = f" (alias: {alias})" if alias else ""
            if hasattr(item, 'file_path') and hasattr(item, 'line_number'):
                evidence_details.append(f"- `{resource_type}` ({friendly}{alias_str}) [{item.file_path}:{item.line_number}]")
            else:
                evidence_details.append(f"- `{resource_type}` ({friendly}{alias_str})")
        if not evidence_items:
            evidence_details.append(f"- `{resource_type}` x{count}")
    evidence_list = "\n".join(evidence_details) if evidence_details else "- No resources extracted."

    notes = (
        f"Repository path: `{repo}`\n\n"
        f"Extracted resources: {len(context.resources)}\n\n"
        "Generated from Phase 1 local heuristics."
    )

    # Fetch Terraform modules from DB/context if available
    modules_list_md = "- None detected."
    try:
        from analyze_terraform_modules import extract_modules
        modules = extract_modules(repo)
        if modules:
            lines = []
            for m in modules:
                lines.append(f"- **{m['name']}** — {m['source']} ({m['file']})")
            modules_list_md = "\n".join(lines)
    except Exception:
        # Fall back to DB context metadata
        try:
            db = _get_db()
            if db:
                import sqlite3
                conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "Output/Learning/triage.db"))
                cur = conn.execute("SELECT key, value FROM context_metadata WHERE key LIKE 'module:%' AND repo_id IS NOT NULL")
                rows = cur.fetchall()
                if rows:
                    lines = []
                    for key, value in rows:
                        import json
                        name = key.split(':',1)[1]
                        try:
                            j = json.loads(value)
                            src = j.get('source')
                            file = j.get('file')
                            lines.append(f"- **{name}** — {src} ({file})")
                        except Exception:
                            lines.append(f"- **{name}** — {value}")
                    modules_list_md = "\n".join(lines)
                conn.close()
        except Exception:
            pass

    content = render_template(
        "RepoSummary.md",
        {
            "repo_name": repo_name,
            "repo_type": "Infrastructure (likely IaC/platform)" if resource_types else "Application/Other",
            "timestamp": now_uk(),
            "architecture_diagram": _build_simple_architecture_diagram(repo_name, provider_resources, repo_path=repo),
            "languages": language_text,
            "hosting": hosting_text,
            "ci_cd": ci_cd_text,
            "providers": provider_text,
            "auth_summary": auth_summary,
            "roles_permissions": roles_permissions,
            "network_summary": network_summary,
            "ingress_summary": ingress_summary,
            "egress_summary": egress_summary,
            "external_deps": external_deps,
            "evidence_list": evidence_list,
            "notes": notes,
            "terraform_modules": modules_list_md,
            "resource_inventory": _build_resource_inventory(provider_resources, repo_path=repo),
            "enrichment_assumptions": _build_enrichment_assumptions(repo_name),
        },
    )
    try:
        with open("Output/Summary/Repos/report_debug.log", "a") as log:
            log.write(f"[DEBUG] Writing summary to: {out_path}\n")
            log.write(f"[DEBUG] Extracted resources: {len(context.resources)}\n")
            log.write(f"[DEBUG] Providers: {providers}\n")
            log.write(f"[DEBUG] Repo name: {repo_name}\n")
    except Exception as e:
        print(f"[ERROR] Failed to log debug info: {e}")
    try:
        out_path.write_text(content, encoding="utf-8")
        validate_markdown_file(out_path, fix=True)
    except Exception as e:
        with open("Output/Summary/Repos/report_debug.log", "a") as log:
            log.write(f"[ERROR] Failed to write summary content: {e}\n")
    return out_path


def write_experiment_cloud_architecture_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    context: RepositoryContext,
    summary_dir: Path,
    repo_path: Path | None = None,
) -> list[Path]:
    out_files: list[Path] = []
    resource_types = [r.resource_type for r in context.resources]

    for provider in providers:
        # Record module dependencies discovered in Terraform into DB if available
        try:
            from analyze_terraform_modules import extract_modules, classify_module_source
            modules = extract_modules(repo_path or repo)
            db = _get_db()
            if db:
                import json
                conn = db
                for mod in modules:
                    # Insert as context metadata: module:<module_name> -> JSON data about source and file
                    key = f"module:{mod['name']}"
                    value = json.dumps({"source": mod['source'], "file": mod['file']})
                    from db_helpers import upsert_context_metadata
                    upsert_context_metadata(experiment_id="001", repo_name=repo_name, key=key, value=value, source="module_discovery")
        except Exception:
            # Non-critical; continue report generation without DB recording
            pass
        provider_resource_objs = [r for r in context.resources if _rtdb.get_provider_key(_get_db(), r.resource_type) == provider]
        provider_resources = [r.resource_type for r in provider_resource_objs]
        unique_provider_resources = sorted(set(provider_resources))
        edge_gateway_detected = any(
            _is_edge_gateway_service(_rtdb.get_friendly_name(_get_db(), rtype)) for rtype in unique_provider_resources
        )
        if unique_provider_resources:
            db = _get_db()
            # Resource types that are containers-only (not meaningful security parents)
            _INFRA_ONLY = {"azurerm_resource_group", "azurerm_subnet", "aws_vpc", "google_compute_network"}

            # Build parent-child map from DB parent_type column
            parent_map: dict[str, str] = {}
            try:
                rows = db.execute(
                    "SELECT terraform_type, parent_type FROM resource_types WHERE parent_type IS NOT NULL"
                ).fetchall()
                parent_map = {row[0]: row[1] for row in rows}
            except Exception:
                pass

            # Separate top-level types from child types
            child_types = {rt for rt in unique_provider_resources if rt in parent_map}
            # TF reference parents — exclude infra-only containers
            ref_parents: dict[str, set[str]] = {}  # parent_tf_type → {child_tf_type}
            for r in provider_resource_objs:
                if r.parent:
                    parent_tf_type = r.parent.split(".")[0]
                    if parent_tf_type not in _INFRA_ONLY and parent_tf_type != r.resource_type:
                        ref_parents.setdefault(parent_tf_type, set()).add(r.resource_type)

            # Exclude infra-only types from top-level display
            top_level = [rt for rt in unique_provider_resources
                         if rt not in child_types and rt not in _INFRA_ONLY]
            inventory_lines = ["| Service | Sub-services | Terraform Type |", "|---|---|---|"]
            for rtype in top_level:
                friendly = _rtdb.get_friendly_name(db, rtype)
                # Children via type-level map
                children_by_type = sorted({
                    ct for ct in child_types if parent_map.get(ct) == rtype
                })
                # Children via TF references (exclude those already in type map and infra-only)
                children_by_ref = sorted(
                    ref_parents.get(rtype, set())
                    - set(children_by_type)
                    - _INFRA_ONLY
                )
                all_children = children_by_type + children_by_ref
                # Deduplicate children by terraform type, show type suffix if friendly name clashes
                seen_friendly: dict[str, int] = {}
                for ct in all_children:
                    fn = _rtdb.get_friendly_name(db, ct)
                    seen_friendly[fn] = seen_friendly.get(fn, 0) + 1
                child_parts = []
                for ct in all_children:
                    fn = _rtdb.get_friendly_name(db, ct)
                    suffix = f" (`{ct.split('_', 1)[-1]}`)" if seen_friendly[fn] > 1 else ""
                    child_parts.append(f"{fn}{suffix}")
                child_str = ", ".join(child_parts) if child_parts else "—"
                inventory_lines.append(f"| **{friendly}** | {child_str} | `{rtype}` |")
            # Append orphaned children that had no recognised parent in top_level
            for rtype in sorted(child_types):
                parent_rtype = parent_map.get(rtype, "")
                if parent_rtype not in unique_provider_resources:
                    friendly = _rtdb.get_friendly_name(db, rtype)
                    parent_friendly = _rtdb.get_friendly_name(db, parent_rtype) if parent_rtype else "Unknown"
                    inventory_lines.append(f"| {friendly} *(child of {parent_friendly})* | — | `{rtype}` |")
            resource_inventory = "\n".join(inventory_lines)
        else:
            resource_inventory = "None detected."

        paas_types = sorted({t for t in provider_resources if _is_paas_resource(t)})
        network_control_types = sorted({t for t in provider_resources if _is_network_control_resource(t)})
        paas_service_names = sorted({_rtdb.get_friendly_name(_get_db(), t) for t in paas_types})
        network_control_service_names = sorted({_rtdb.get_friendly_name(_get_db(), t) for t in network_control_types})
        paas_exposure_checks = _build_paas_exposure_checks(provider_resources)
        if paas_types:
            controls_line = (
                "- Network control signals detected: "
                + ", ".join(network_control_service_names)
                if network_control_types
                else "- Network control signals detected: none"
            )
            security_controls = "\n".join(
                [
                    "- PaaS services detected: " + ", ".join(paas_service_names),
                    controls_line,
                    "- Action required: validate each PaaS service has explicit access restrictions/private access and default-deny behavior.",
                ]
            )
            if not network_control_types:
                security_controls += (
                    "\n- Warning: PaaS resources are commonly internet-reachable by default; no explicit network-control resources were detected in Phase 1."
                )
            ingress_warnings = _ingress_security_warnings(repo, provider)
            if ingress_warnings:
                security_controls += "\n" + "\n".join(ingress_warnings)
        else:
            security_controls = "- No PaaS services detected in Phase 1."

        recommendations = "\n".join(
            [
                "- Complete Phase 2 route and middleware tracing.",
                "- Run full IaC rule scan and map findings to risk register.",
                "- Add explicit network restrictions and private connectivity where applicable.",
            ]
        )

        provider_title = _provider_title(provider)
        out_path = summary_dir / "Cloud" / f"Architecture_{provider_title}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content = render_template(
            "CloudArchitectureSummary.md",
            {
                "provider": provider,
                "provider_title": provider_title,
                "repo_name": repo_name,
                "timestamp": now_uk(),
                "auth_signals": "Detected from Terraform resource metadata.",
                "services": _summarize_service_names(provider_resources),
                "top_risk": "Public exposure and weak network defaults require validation.",
                "next_step": "Run Phase 2 deep context discovery and skeptic reviews.",
                "attack_surface": "Internet-reachable services must be confirmed in Phase 2.",
                "edge_gateway": "Detected in Phase 1." if edge_gateway_detected else "Not confirmed in Phase 1.",
                "architecture_diagram": _build_simple_architecture_diagram(
                    repo_name, {provider: provider_resource_objs}, repo_path=repo
                ),
                "resource_inventory": resource_inventory,
                "security_controls": security_controls,
                "paas_exposure_checks": paas_exposure_checks,
                "recommendations": recommendations,
            },
        )
        content = content.replace("\\`\\`\\`", "```")
        out_path.write_text(content, encoding="utf-8")
        validate_markdown_file(out_path, fix=True)
        out_files.append(out_path)

    return out_files


def generate_reports(context: RepositoryContext, summary_dir_str: str, repo_path: Path | None = None) -> list[Path]:
    summary_dir = Path(summary_dir_str)
    summary_dir.mkdir(parents=True, exist_ok=True)
    repo = repo_path if repo_path is not None else Path(context.repository_name)
    with open("Output/Summary/Repos/report_debug.log", "a") as log:
        log.write(f"[DEBUG] summary_dir: {summary_dir}, repo: {repo}\n")

    providers = sorted(
        {
            _rtdb.get_provider_key(_get_db(), r.resource_type)
            for r in context.resources
            if _rtdb.get_provider_key(_get_db(), r.resource_type) != "unknown"
        }
    )

    generated: list[Path] = []
    generated.append(
        write_repo_summary(
            repo=repo,
            repo_name=context.repository_name,
            providers=providers,
            context=context,
            summary_dir=summary_dir,
        )
    )
    generated.extend(
        write_experiment_cloud_architecture_summary(
            repo=repo,
            repo_name=context.repository_name,
            providers=providers,
            context=context,
            summary_dir=summary_dir,
            repo_path=repo_path,
        )
    )
    return generated


def write_to_database(context: RepositoryContext, db_path: str = None, experiment_id: str = "001") -> None:
    from db_helpers import insert_repository, insert_resource, insert_connection, get_db_connection

    with get_db_connection(db_path):
        insert_repository(
            experiment_id=experiment_id,
            repo_path=Path(context.repository_name),
        )

        # First pass: insert all resources, collect type.name → db_id map
        res_db_ids: dict[str, int] = {}
        for resource in context.resources:
            db_id = insert_resource(
                experiment_id=experiment_id,
                repo_name=context.repository_name,
                resource_name=resource.name,
                resource_type=resource.resource_type,
                provider=_rtdb.get_provider_key(_get_db(), resource.resource_type),
                source_file=resource.file_path,
                source_line=resource.line_number,
            )
            res_db_ids[f"{resource.resource_type}.{resource.name}"] = db_id

        # Second pass: resolve parent references and update parent_resource_id
        from db_helpers import get_db_connection as _gdb
        for resource in context.resources:
            if not resource.parent:
                continue
            parent_db_id = res_db_ids.get(resource.parent)
            if parent_db_id:
                child_db_id = res_db_ids.get(f"{resource.resource_type}.{resource.name}")
                if child_db_id:
                    with _gdb(db_path) as conn:
                        conn.execute(
                            "UPDATE resources SET parent_resource_id=? WHERE id=?",
                            (parent_db_id, child_db_id),
                        )

        for connection in context.connections:
            insert_connection(
                experiment_id=experiment_id,
                source_name=connection.source,
                target_name=connection.target,
                connection_type=connection.connection_type,
            )
