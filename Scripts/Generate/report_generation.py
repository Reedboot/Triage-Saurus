from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import sqlite3

from models import RepositoryContext
from template_renderer import render_template
from markdown_validator import validate_markdown_file
import resource_type_db as _rtdb
import db_helpers as _db
from shared_utils import now_uk, _normalize_optional_bool
from service_auth_topology import render_service_auth_topology


def _get_db():
    """Return a sqlite3.Connection if the learning DB exists, otherwise None."""
    if not _db.DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_db.DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None

def _provider_title(provider: str) -> str:
    return {"azure": "Azure", "aws": "AWS", "gcp": "GCP"}.get(provider, provider.upper())


_K8S_PROVIDER_PREFERENCE = ("azure", "aws", "gcp")
_K8S_CLUSTER_RESOURCE_TYPES: dict[str, set[str]] = {
    "azure": {"azurerm_kubernetes_cluster"},
    "aws": {"aws_eks_cluster"},
    "gcp": {"google_container_cluster"},
}
_INFERRED_K8S_CLUSTER_RAW_TYPES = ("helm_release", "kubernetes_deployment")
_INFERRED_K8S_CLUSTER_LABEL = "☸️ Kubernetes Cluster (inferred)"


def _infer_k8s_provider_hint(text: str | None) -> str | None:
    if not text:
        return None
    lower = text.lower()
    if any(token in lower for token in ("azurerm", "aks", "azure")):
        return "azure"
    if any(token in lower for token in ("eks", "aws")):
        return "aws"
    if any(token in lower for token in ("gke", "google")):
        return "gcp"
    return None


def _format_inferred_k8s_cluster_label(deployments: list[dict], base_label: str = _INFERRED_K8S_CLUSTER_LABEL) -> str:
    names: list[str] = []
    for deployment in deployments:
        raw_name = deployment.get("app_name") or deployment.get("module_name")
        if not raw_name:
            continue
        clean_name = raw_name.replace('"', "").replace("'", "").strip()
        if clean_name and clean_name not in names:
            names.append(clean_name)
    if not names:
        return base_label
    if len(names) == 1:
        return f"{base_label} ({names[0]})"
    return f"{base_label} ({len(names)} apps)"


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
    
    # Track which resources belong to known patterns
    pattern_resources = set()
    
    # Group resources by service patterns
    pattern_names = ["api_gateway", "storage", "messaging", "serverless", "key_vault", 
                     "database", "cosmos_db", "kubernetes", "app_service", "monitoring"]
    
    for pattern_name in pattern_names:
        pattern_types = []
        for rtype in resource_types:
            matched_pattern, _ = _rtdb.get_service_pattern(rtype)
            if matched_pattern == pattern_name:
                pattern_types.append(rtype)
                pattern_resources.add(rtype)
        
        if pattern_types:
            # Determine friendly name based on pattern and resources
            if pattern_name == "api_gateway":
                # Separate APIM instance from APIs, operations, products for proper nesting
                instance_types = [rt for rt in pattern_types if rt in (
                    "azurerm_api_management", "aws_api_gateway_rest_api", "aws_apigatewayv2_api",
                    "google_api_gateway_gateway", "oci_apigateway_gateway", "alicloud_api_gateway_group"
                )]
                child_types = [rt for rt in pattern_types if rt not in instance_types]
                
                if instance_types:
                    grouped["API Management"] = instance_types
                    pattern_resources.update(instance_types)
                
                # Group API children by their specific type
                for child_rt in child_types:
                    child_friendly = _rtdb.get_friendly_name(_get_db(), child_rt)
                    grouped.setdefault(child_friendly, []).append(child_rt)
                    pattern_resources.add(child_rt)
                
                continue
            elif pattern_name == "storage":
                if any(rt.startswith("aws_") for rt in pattern_types):
                    friendly = "S3 Bucket"
                elif any(rt.startswith("azurerm_") for rt in pattern_types):
                    friendly = "Storage Account"
                else:
                    friendly = "Storage"
                grouped[friendly] = pattern_types
            elif pattern_name == "messaging":
                # Check which messaging service
                if any("servicebus" in rt for rt in pattern_types):
                    # Separate namespace from topics/queues for proper nesting
                    namespace_types = [rt for rt in pattern_types if rt in ("azurerm_servicebus_namespace",)]
                    child_types = [rt for rt in pattern_types if rt in (
                        "azurerm_servicebus_queue", "azurerm_servicebus_topic", 
                        "azurerm_servicebus_subscription", "azurerm_servicebus_subscription_rule"
                    )]
                    
                    if namespace_types:
                        grouped["Service Bus Namespace"] = namespace_types
                        pattern_resources.update(namespace_types)
                    
                    # Group children by their specific type for proper service grouping
                    for child_rt in child_types:
                        child_friendly = _rtdb.get_friendly_name(_get_db(), child_rt)
                        grouped.setdefault(child_friendly, []).append(child_rt)
                        pattern_resources.add(child_rt)
                    
                    # Skip default grouping for servicebus since we handled it above
                    continue
                    
                elif any("eventhub" in rt for rt in pattern_types):
                    # Similar logic for Event Hub
                    namespace_types = [rt for rt in pattern_types if rt == "azurerm_eventhub_namespace"]
                    child_types = [rt for rt in pattern_types if rt in (
                        "azurerm_eventhub", "azurerm_eventhub_consumer_group"
                    )]
                    
                    if namespace_types:
                        grouped["Event Hub Namespace"] = namespace_types
                        pattern_resources.update(namespace_types)
                    
                    for child_rt in child_types:
                        child_friendly = _rtdb.get_friendly_name(_get_db(), child_rt)
                        grouped.setdefault(child_friendly, []).append(child_rt)
                        pattern_resources.add(child_rt)
                    
                    continue
                    
                elif any("sns" in rt or "sqs" in rt for rt in pattern_types):
                    friendly = "Messaging"
                elif any("pubsub" in rt for rt in pattern_types):
                    friendly = "Pub/Sub"
                else:
                    friendly = "Messaging"
                
                # Default grouping for non-ServiceBus messaging
                grouped[friendly] = pattern_types
            elif pattern_name == "serverless":
                friendly = "Serverless Functions"
            elif pattern_name == "key_vault":
                friendly = "Key Vault"
            elif pattern_name == "database":
                # Determine database type
                if any("mssql" in rt or "sql_server" in rt for rt in pattern_types):
                    friendly = "SQL Server"
                elif any("mysql" in rt for rt in pattern_types):
                    friendly = "MySQL Server"
                elif any("postgresql" in rt for rt in pattern_types):
                    friendly = "PostgreSQL Server"
                elif any("aws_db_instance" in rt for rt in pattern_types):
                    friendly = "RDS Database"
                elif any("google_sql" in rt for rt in pattern_types):
                    friendly = "Cloud SQL"
                else:
                    friendly = "Database Server"
            elif pattern_name == "cosmos_db":
                friendly = "Cosmos DB Account" if any(rt.startswith("azurerm_") for rt in pattern_types) else "NoSQL Database"
            elif pattern_name == "kubernetes":
                friendly = "Kubernetes Cluster"  # Will be overridden by inferred cluster logic
            elif pattern_name == "app_service":
                friendly = "App Service Plan"
            elif pattern_name == "monitoring":
                if any("application_insights" in rt for rt in pattern_types):
                    friendly = "Application Insights"
                elif any("log_analytics" in rt for rt in pattern_types):
                    friendly = "Log Analytics Workspace"
                else:
                    friendly = "Monitoring"
            else:
                friendly = pattern_name.replace("_", " ").title()
                grouped[friendly] = pattern_types
    
    # Group remaining resources by friendly name (legacy behavior)
    for rtype in resource_types:
        if rtype in pattern_resources:
            continue  # Already grouped by pattern
        parent = _rtdb.get_friendly_name(_get_db(), rtype)
        grouped.setdefault(parent, []).append(rtype)
    
    return grouped


def _resource_kind_label(resource_type: str) -> str:
    cleaned = resource_type
    for prefix in ("azurerm_", "aws_", "google_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    # Normalize mssql -> SQL for nicer labels (e.g., "Mssql Server" -> "SQL Server")
    parts = cleaned.split("_")
    normalized_parts = []
    for p in parts:
        if p.lower() in ("mssql", "sql"):
            normalized_parts.append("SQL")
        else:
            normalized_parts.append(p.capitalize())
    return " ".join(normalized_parts)


def _child_node_label(resource_type: str, parent_service: str) -> str:
    friendly = _rtdb.get_friendly_name(_get_db(), resource_type)
    if friendly == parent_service:
        return f"{friendly}: {_resource_kind_label(resource_type)}"
    return friendly


def _filter_mermaid_children(_parent_service: str, raw_types: list[str]) -> list[str]:
    """Filter child nodes for diagram clarity — delegates to resource_type_db canonical type logic."""
    return _rtdb.filter_to_canonical(raw_types)


def _is_apim_component(resource_type: str) -> bool:
    """Check if resource is an API Management/Gateway component (all providers)."""
    apim_components = (
        # Azure API Management
        "azurerm_api_management_api",
        "azurerm_api_management_api_operation",
        "azurerm_api_management_api_policy",
        "azurerm_api_management_product",
        "azurerm_api_management_product_api",
        "azurerm_api_management_subscription",
        "azurerm_api_management_backend",
        "azurerm_api_management_named_value",
        # AWS API Gateway
        "aws_api_gateway_rest_api",
        "aws_api_gateway_resource",
        "aws_api_gateway_method",
        "aws_api_gateway_integration",
        "aws_api_gateway_deployment",
        "aws_api_gateway_stage",
        "aws_api_gateway_api_key",
        "aws_api_gateway_usage_plan",
        "aws_api_gateway_usage_plan_key",
        "aws_apigatewayv2_api",
        "aws_apigatewayv2_route",
        "aws_apigatewayv2_integration",
        "aws_apigatewayv2_stage",
        # GCP API Gateway
        "google_api_gateway_api",
        "google_api_gateway_api_config",
        "google_api_gateway_gateway",
        # Oracle API Gateway
        "oci_apigateway_gateway",
        "oci_apigateway_deployment",
        # Alibaba API Gateway
        "alicloud_api_gateway_api",
        "alicloud_api_gateway_group",
        "alicloud_api_gateway_app",
    )
    return resource_type in apim_components


def _group_apim_resources(service_raw: list[str], resources: list, repo_path: Path | None) -> tuple[list[str], dict]:
    """Separate API Gateway/APIM components from other resources and group them by API (all providers).
    
    Returns:
        (non_apim_resources, apim_structure)
        where apim_structure contains API names as keys with their components.
    """
    apim_resources = [r for r in service_raw if _is_apim_component(r)]
    non_apim_resources = [r for r in service_raw if not _is_apim_component(r)]
    
    if not apim_resources:
        return service_raw, {}
    
    # Determine provider
    provider = None
    if any(r.startswith("azurerm_") for r in apim_resources):
        provider = "azure"
    elif any(r.startswith("aws_") for r in apim_resources):
        provider = "aws"
    elif any(r.startswith("google_") for r in apim_resources):
        provider = "gcp"
    elif any(r.startswith("oci_") for r in apim_resources):
        provider = "oci"
    elif any(r.startswith("alicloud_") for r in apim_resources):
        provider = "alibaba"
    
    # Structure: {api_name: {operations: [], policies: [], products: [], subscriptions: []}}
    apim_structure = {}
    
    if provider == "azure":
        # Find all APIs
        api_resources = [r for r in resources if r.resource_type == "azurerm_api_management_api"]
        
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
    
    elif provider == "aws":
        # AWS API Gateway: rest_api → resource → method
        api_resources = [r for r in resources if r.resource_type in ("aws_api_gateway_rest_api", "aws_apigatewayv2_api")]
        
        for api in api_resources:
            api_name = api.name or "unnamed-api"
            apim_structure[api_name] = {
                "operations": [],
                "policies": [],
                "products": [],
                "subscriptions": [],
            }
            
            # Find methods/routes for this API
            for r in resources:
                if r.resource_type in ("aws_api_gateway_method", "aws_apigatewayv2_route"):
                    apim_structure[api_name]["operations"].append(r.name)
            
            # API keys and usage plans
            if api == api_resources[0]:
                for r in resources:
                    if r.resource_type in ("aws_api_gateway_api_key", "aws_api_gateway_usage_plan_key"):
                        apim_structure[api_name]["subscriptions"].append(r.name)
    
    elif provider in ("gcp", "oci", "alibaba"):
        # Simple structure for other providers (no nested operations extracted yet)
        # Group all resources under a single API entry
        apim_structure["api"] = {
            "operations": [],
            "policies": [],
            "products": [],
            "subscriptions": [],
        }
    
    return non_apim_resources, apim_structure


# Backend detection: Currently uses direct Terraform parsing for speed.
# Opengrep rules exist in Rules/Detection/{Azure,AWS,GCP}/ for:
#   - apim-backend-routing-detection.yml (APIM service_url)
#   - api-gateway-backend-integration-detection.yml (AWS API Gateway URIs)
#   - api-gateway-backend-config-detection.yml (GCP API Gateway)
#   - kubernetes-backend-deployment-detection.yml (AKS deployments)
#   - eks-backend-deployment-detection.yml (AWS EKS)
#   - gke-backend-deployment-detection.yml (GCP GKE)
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


def _extract_apim_backends(repo_path: Path | None) -> dict[str, dict]:
    """Extract all APIM backend resources and their URLs.
    
    Returns:
        dict: {backend_resource_name: {"name": backend_name, "url": backend_url}}
    """
    if not repo_path or not repo_path.exists():
        return {}
    
    backends = {}
    name_pattern = re.compile(r'name\s*=\s*"([^"]+)"')
    url_pattern = re.compile(r'url\s*=\s*"([^"]+)"')
    
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management_backend"):
        if rtype == "azurerm_api_management_backend":
            name_match = name_pattern.search(body)
            url_match = url_pattern.search(body)
            
            if url_match:
                backends[rlabel] = {
                    "name": name_match.group(1) if name_match else rlabel,
                    "url": url_match.group(1),
                    "terraform_label": rlabel
                }
    
    return backends


def _extract_backend_from_policy(repo_path: Path | None, operation_label: str) -> str | None:
    """Extract backend-id reference from APIM operation policy XML.
    
    Args:
        operation_label: The operation name/label (e.g., "v1getaccount")
    
    Returns:
        Backend terraform resource label (e.g., "accounts_external_backend_aks")
    """
    if not repo_path or not repo_path.exists():
        return None
    
    # Pattern to match: <set-backend-service backend-id="${azurerm_api_management_backend.BACKEND_NAME.name}" />
    backend_pattern = re.compile(r'<set-backend-service\s+backend-id="\$\{azurerm_api_management_backend\.([^.]+)\.name\}')
    
    # Look for the operation's Terraform file or inline policy
    # Operations might be defined with suffix like "v1getaccount" but files/resources might have "_policy" suffix
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management_api_operation"):
        # Match if the operation label is contained in the resource label (handles both with/without _policy suffix)
        if operation_label in rlabel or rlabel in operation_label:
            # Check for inline policy or policy file
            match = backend_pattern.search(body)
            if match:
                return match.group(1)
    
    # Also check policy resources
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management_api_policy"):
        if operation_label in rlabel or rlabel in operation_label:
            match = backend_pattern.search(body)
            if match:
                return match.group(1)
    
    # Also check the base API management file for global backend policies
    for rtype, rlabel, body in _terraform_resource_blocks(repo_path, prefix="azurerm_api_management"):
        if "api_management_api" not in rtype:  # Skip API resources, focus on the base APIM resource
            match = backend_pattern.search(body)
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

                    provider_hint = _infer_k8s_provider_hint(f"{module_name} {module_body}")
                    deployments.append({
                        "module_name": module_name,
                        "app_name": app_name,
                        "namespace": ns_name,
                        "type": "module",
                        "provider_hint": provider_hint,
                    })
            
            # Look for Helm releases (common for EKS/GKE)
            helm_pattern = re.compile(r'resource\s+"helm_release"\s+"([^"]+)"\s*\{([^}]+)\}', re.DOTALL)
            for match in helm_pattern.finditer(content):
                release_label = match.group(1)
                release_body = match.group(2)
                
                name_match = helm_name_pattern.search(release_body)
                ns_match = namespace_pattern.search(release_body)
                
                if name_match:
                    provider_hint = _infer_k8s_provider_hint(f"{release_label} {release_body}")
                    deployments.append({
                        "module_name": release_label,
                        "app_name": name_match.group(1),
                        "namespace": ns_match.group(1) if ns_match else None,
                        "type": "helm",
                        "provider_hint": provider_hint,
                    })
            
            # Look for kubernetes_deployment resources
            k8s_deploy_pattern = re.compile(r'resource\s+"kubernetes_deployment"\s+"([^"]+)"\s*\{.*?metadata\s*\{.*?name\s*=\s*"([^"]+)"', re.DOTALL)
            for match in k8s_deploy_pattern.finditer(content):
                provider_hint = _infer_k8s_provider_hint(f"{tf_file.name} {match.group(0)}")
                deployments.append({
                    "module_name": match.group(1),
                    "app_name": match.group(2),
                    "namespace": None,
                    "type": "kubernetes_deployment",
                    "provider_hint": provider_hint,
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
        "kubernetes",
        "helm",
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
        "application gateway",
        "load balancer",
        "load balancing",
        "api management",
        "api gateway",
        "front door",
        "ingress",
        "cloudfront",
        "gateway",
    )
    service_name_lower = service_name.lower()
    return any(tok in service_name_lower for tok in tokens)


def _detect_api_auth_mechanism(service_name: str, service_raw_types: list[str], assets: list[object]) -> str:
    """
    Detect authentication mechanism for API Gateway/APIM from related resources.
    Returns label like "API Key", "OAuth", "Anonymous", etc.
    
    Uses service patterns to identify auth resources across all cloud providers.
    """
    # Get pattern components
    auth_resources = []
    for rt in service_raw_types:
        if _rtdb.is_auth_resource(rt):
            auth_resources.append(rt)
    
    # Also check service assets for auth-related resources
    service_assets = [a for a in assets if getattr(a, 'service_name', '') == service_name]
    for asset in service_assets:
        rtype = getattr(asset, 'resource_type', '')
        if _rtdb.is_auth_resource(rtype):
            auth_resources.append(rtype)
    
    # Determine label based on found auth resources
    auth_types = set()
    for rtype in auth_resources:
        lower = rtype.lower()
        if 'oauth' in lower or 'identity_provider' in lower or 'authorizer' in lower:
            auth_types.add('oauth')
        elif 'jwt' in lower or 'token' in lower:
            auth_types.add('jwt')
        elif 'certificate' in lower or 'cert' in lower or 'mtls' in lower:
            auth_types.add('cert')
        elif 'subscription' in lower or 'api_key' in lower or '_key' in lower:
            auth_types.add('key')
        elif 'sas' in lower:
            auth_types.add('sas')
        elif 'iam' in lower or 'policy' in lower:
            auth_types.add('iam')
    
    # Priority order for label
    if 'oauth' in auth_types:
        return "OAuth"
    elif 'jwt' in auth_types:
        return "JWT"
    elif 'cert' in auth_types:
        return "mTLS"
    elif 'key' in auth_types:
        return "API Key"
    elif 'sas' in auth_types:
        return "SAS Token"
    elif 'iam' in auth_types:
        return "IAM"
    
    # Default to "HTTPS" if no specific auth detected
    return "HTTPS"


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
        return ("Known ingress", False)
    if provider != "azure" or "application gateway" not in service_name.lower():
        return ("Known ingress", False)

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
        return ("Known ingress", False)
    if protocols == {"https"}:
        return ("Known ingress (HTTPS)", False)
    if protocols == {"http"}:
        return ("Known ingress (HTTP)", True)
    if "http" in protocols and "https" in protocols:
        return ("Known ingress (HTTP/HTTPS)", True)
    return (f"Known ingress ({'/'.join(sorted(p.upper() for p in protocols))})", False)


def _boolish(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().strip('"').strip("'").lower()
    if normalized in {"1", "true", "yes", "enabled", "on"}:
        return True
    if normalized in {"0", "false", "no", "disabled", "off"}:
        return False
    return None


def _has_nonempty_public_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if _boolish(value.get("is_public")) is True:
            return True
        for key in ("ip_address", "domain_name_label", "public_ip_address_id", "public_dns"):
            if value.get(key):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_has_nonempty_public_value(v) for v in value)
    normalized = str(value).strip().lower()
    return normalized not in {"", "0", "false", "no", "none", "null", "disabled", "off"}


def _is_iam_policy_role_type(resource_type: str) -> bool:
    lower = (resource_type or "").lower()
    if (
        "policy" in lower
        or "iam_" in lower
        or "_iam_" in lower
        or "binding" in lower
        or "rbac" in lower
    ):
        return True
    return bool(re.search(r"(?:^|[_-])role(?:$|[_-])", lower))


def _is_non_endpoint_control_service(raw_types: list[str]) -> bool:
    if not raw_types:
        return True
    control_tokens = (
        "security_group",
        "network_security_group",
        "firewall",
        "route_table",
        "monitor",
        "diagnostic",
    )
    return all(
        _is_iam_policy_role_type(rt) or any(token in rt.lower() for token in control_tokens)
        for rt in raw_types
    )


def _resource_has_explicit_public_signal(resource: object) -> bool:
    props = getattr(resource, "properties", {}) or {}

    for key in (
        "internet_access",
        "public",
        "public_access",
        "publicly_accessible",
        "public_network_access_enabled",
        "internet_facing",
        "allow_blob_public_access",
        "s3_public_acl",
        "s3_public_policy",
        "sql_firewall_public",
        "has_public_ip",
        "public_fqdn_enabled",
        "is_ingress_endpoint",  # Added for API operations and LB listeners
    ):
        if _boolish(props.get(key)) is True:
            return True

    if _boolish(props.get("private_cluster_enabled")) is False:
        return True
    if _boolish(props.get("internal")) is False:
        return True

    public_network_access = str(props.get("public_network_access", "")).strip().lower()
    if public_network_access in {"enabled", "public", "true", "yes", "1"}:
        return True

    scheme = str(props.get("scheme", "")).strip().lower()
    if scheme == "internet-facing":
        return True

    acl = str(props.get("acl", "")).strip().lower()
    if acl.startswith("public-"):
        return True

    for key in ("public_ip", "public_ip_id", "public_ip_address_id", "public_ip_address", "public_dns", "public_dns_name"):
        if _has_nonempty_public_value(props.get(key)):
            return True

    return getattr(resource, "resource_type", "") in {"azurerm_public_ip", "aws_eip"}


def _evaluate_service_internet_access(
    *,
    repo_path: Path | None,
    provider: str,
    service_name: str,
    service_raw_types: list[str],
    provider_scoped_resources: list[object],
) -> tuple[bool, str, bool]:
    """
    Return (is_public, label, insecure_http) based on explicit per-resource evidence.
    """
    if not service_raw_types or _is_non_endpoint_control_service(service_raw_types):
        return (False, "Known ingress", False)

    service_type_set = set(service_raw_types)
    service_resources = [
        res for res in provider_scoped_resources
        if getattr(res, "resource_type", "") in service_type_set
    ]
    if not service_resources:
        return (False, "Known ingress", False)

    if not any(_resource_has_explicit_public_signal(res) for res in service_resources):
        return (False, "Known ingress", False)

    if _is_edge_gateway_service(service_name):
        label, insecure_http = _ingress_posture_for_service(repo_path, provider, service_name)
        return (True, label, insecure_http)
    return (True, "Known ingress", False)


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


def _load_vulnerable_resource_keys(repo_name: str) -> set[tuple[str, str]]:
    """
    Return {(resource_type, resource_name)} for resources in the latest repo scan
    that have at least one linked finding with severity_score > 0.
    """
    from db_helpers import get_db_connection

    try:
        with get_db_connection() as conn:
            repo_row = conn.execute(
                """
                SELECT id
                FROM repositories
                WHERE repo_name = ?
                ORDER BY scanned_at DESC, id DESC
                LIMIT 1
                """,
                (repo_name,),
            ).fetchone()
            if not repo_row:
                return set()
            repo_id = repo_row["id"]
            rows = conn.execute(
                """
                SELECT r.resource_type, r.resource_name
                FROM resources r
                JOIN findings f ON f.resource_id = r.id
                WHERE r.repo_id = ?
                GROUP BY r.id
                HAVING MAX(COALESCE(f.severity_score, 0)) > 0
                """,
                (repo_id,),
            ).fetchall()
    except Exception:
        return set()

    return {
        (str(row["resource_type"]), str(row["resource_name"]))
        for row in rows
        if row["resource_type"] and row["resource_name"]
    }


def _build_simple_architecture_diagram(
    repo_name: str,
    provider_resources: dict[str, list[object]],
    repo_path: Path | None = None,
    compact_apim: bool = False,
) -> str:
    lines = ["flowchart TB"]
    edge_lines: list[str] = []
    link_styles: list[str] = []
    node_styles: list[str] = []
    styled_ids: set[str] = set()
    used_node_ids: set[str] = set()  # Track all node IDs to prevent duplicates
    link_index = 0
    vulnerable_resource_keys = _load_vulnerable_resource_keys(repo_name)
    repo_k8s_deployments = _extract_kubernetes_deployments(repo_path)
    k8s_deployments_by_provider = {prov: [] for prov in _K8S_PROVIDER_PREFERENCE}
    for deployment in repo_k8s_deployments:
        hint = deployment.get("provider_hint")
        if hint in k8s_deployments_by_provider:
            k8s_deployments_by_provider[hint].append(deployment)
    k8s_hint_present = any(k8s_deployments_by_provider.values())
    k8s_deployments_without_hint = [
        deployment for deployment in repo_k8s_deployments if not deployment.get("provider_hint")
    ]
    available_providers = list(provider_resources.keys())
    k8s_fallback_provider = next(
        (p for p in _K8S_PROVIDER_PREFERENCE if p in available_providers),
        available_providers[0] if available_providers else None,
    )
    k8s_deployments_present = bool(repo_k8s_deployments)

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
        "Messaging": "app",
        "Cache": "data",
        "Serverless": "app",
        "API": "app",
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
    lines.append('  Client[Client / Unknown Source]')
    if not provider_resources:
        lines.append('  Internet -->|No cloud provider evidence| Unknown[No cloud provider resources detected]')
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
        provider_deployments = k8s_deployments_by_provider.get(provider, [])
        fallback_allowed = (
            not k8s_hint_present and k8s_deployments_present and provider == k8s_fallback_provider
        )
        should_infer_cluster = k8s_deployments_present and (
            bool(provider_deployments) or fallback_allowed
        )
        provider_cluster_types = _K8S_CLUSTER_RESOURCE_TYPES.get(provider, frozenset())
        has_cluster_resource = bool(set(resource_types) & provider_cluster_types)
        if should_infer_cluster and not has_cluster_resource:
            cluster_deployments = provider_deployments or k8s_deployments_without_hint or repo_k8s_deployments
            cluster_label = _format_inferred_k8s_cluster_label(cluster_deployments)
            if cluster_label not in non_boundary_parents:
                non_boundary_parents[cluster_label] = list(_INFERRED_K8S_CLUSTER_RAW_TYPES)
                parent_groups[cluster_label] = list(_INFERRED_K8S_CLUSTER_RAW_TYPES)

        _internet_posture_cache: dict[tuple[str, tuple[str, ...]], tuple[bool, str, bool]] = {}

        def _service_internet_posture(service_name: str, service_raw_types: list[str]) -> tuple[bool, str, bool]:
            cache_key = (service_name, tuple(sorted(service_raw_types)))
            if cache_key not in _internet_posture_cache:
                _internet_posture_cache[cache_key] = _evaluate_service_internet_access(
                    repo_path=repo_path,
                    provider=provider,
                    service_name=service_name,
                    service_raw_types=service_raw_types,
                    provider_scoped_resources=resources,
                )
            return _internet_posture_cache[cache_key]

        def _raw_types_have_vulnerability(raw_types: list[str]) -> bool:
            if not raw_types or not vulnerable_resource_keys:
                return False
            raw_type_set = set(raw_types)
            for res in resources:
                res_type = str(getattr(res, "resource_type", "") or "")
                res_name = str(getattr(res, "name", "") or "")
                if res_type in raw_type_set and (res_type, res_name) in vulnerable_resource_keys:
                    return True
            return False


        def _is_non_visual_control_service(raw_types: list[str]) -> bool:
            control_tokens = (
                "security_group",
                "network_security_group",
                "firewall",
                "route_table",
                "monitor",
                "diagnostic",
            )
            for rt in raw_types:
                lower = rt.lower()
                # Exclude API Gateway components from policy/role filtering
                if any(tok in lower for tok in ('api_management', 'api_gateway', 'apigateway')):
                    continue
                if _is_iam_policy_role_type(lower) or any(token in lower for token in control_tokens):
                    return True
            return False

        def _is_vulnerable_child_candidate(raw_types: list[str]) -> bool:
            if not raw_types or _is_non_visual_control_service(raw_types):
                return False
            db = _get_db()
            return any(bool(_rtdb.get_resource_type(db, rt).get("parent_type")) for rt in raw_types)

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

        # Identity resources will be rendered in the Identity layer during layer iteration.
        identity_resources = [r for r in resources if "key_vault" in r.resource_type]
        identity_rendered = False

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
            # Debug API Management
            if service == "API Management":
                try:
                    with open('Output/Summary/apim_layer_debug.log','w') as _dbg:
                        _dbg.write(f"Service: {service}\n")
                        _dbg.write(f"Raw types: {raw_for_cat}\n")
                        _dbg.write(f"Category/Layer: {cat}\n")
                except:
                    pass

        # Ensure identity resources (e.g., Key Vault, Managed Identity) appear in the Identity layer
        # BUT: Skip API Gateway child components that are already grouped under "API Management"
        try:
            db = _get_db()
            identity_raw_types = [rt for rt in resource_types if _rtdb.get_resource_type(db, rt).get('category') == 'Identity']
            # Build mapping friendly_name -> raw types so filtering later can check display flags
            identity_map: dict[str, list[str]] = {}
            for rt in identity_raw_types:
                # Skip APIM child components - they're already grouped
                if _is_apim_component(rt):
                    continue
                fn = _rtdb.get_friendly_name(db, rt)
                if not fn:
                    continue
                identity_map.setdefault(fn, []).append(rt)
                # ensure friendly name present in services_by_layer
                if fn not in services_by_layer.get('identity', []):
                    services_by_layer.setdefault('identity', []).append(fn)
            # Merge identity_map into non_boundary_parents so later filtering sees the raw types
            for fn, rts in identity_map.items():
                existing = non_boundary_parents.get(fn, [])
                merged = sorted(set(existing + rts))
                non_boundary_parents[fn] = merged
        except Exception:
            pass

        # Debugging: capture a small snapshot of services_by_layer to aid troubleshooting
        try:
            dbg = {
                'provider': provider,
                'layer_counts': {k: len(v) for k,v in services_by_layer.items()},
                'identity_contents': services_by_layer.get('identity', [])
            }
            with open('Output/Summary/report_generation_debug.log','a') as _dbg:
                _dbg.write(str(dbg) + "\n")
        except Exception:
            pass

        # Filter out service entries that should not appear on the architecture chart
        try:
            db = _get_db()
            # Debug: Log services BEFORE filtering
            try:
                with open('Output/Summary/before_filter.log','w') as _dbg:
                    _dbg.write(f"Services before filtering:\n")
                    for layer_key, svc_list in services_by_layer.items():
                        _dbg.write(f"  {layer_key}: {svc_list}\n")
            except:
                pass
            
            for layer_key, svc_list in list(services_by_layer.items()):
                filtered = []
                # Debug: Log what we're filtering
                if layer_key == 'security':
                    try:
                        with open('Output/Summary/security_filtering.log','w') as _dbg:
                            _dbg.write(f"Filtering security layer, input services: {svc_list}\n")
                    except:
                        pass
                
                for svc in svc_list:
                    raw_types = non_boundary_parents.get(svc, [])
                    
                    # Debug security layer processing
                    if layer_key == 'security' and svc == 'API Management':
                        try:
                            with open('Output/Summary/api_mgmt_debug.log','w') as _dbg:
                                _dbg.write(f"Processing: {svc}\n")
                                _dbg.write(f"Raw types: {raw_types}\n")
                        except:
                            pass
                    
                    if _is_non_visual_control_service(raw_types):
                        if layer_key == 'security' and svc == 'API Management':
                            try:
                                with open('Output/Summary/api_mgmt_debug.log','a') as _dbg:
                                    _dbg.write(f"FILTERED by _is_non_visual_control_service\n")
                            except:
                                pass
                        continue
                    # If any raw_type is allowed to display, keep the service
                    keep = False
                    for rt in raw_types:
                        rt_meta = _rtdb.get_resource_type(db, rt)
                        display_flag = rt_meta.get('display_on_architecture_chart', False)
                        if display_flag:
                            keep = True
                            break
                    # Hidden child components can still be rendered when vulnerable and parented.
                    if (
                        not keep
                        and _raw_types_have_vulnerability(raw_types)
                        and _is_vulnerable_child_candidate(raw_types)
                    ):
                        keep = True
                    
                    # Always keep API Gateway/Management services (critical for architecture)
                    if not keep and any(tok in svc.lower() for tok in ('api management', 'api gateway')):
                        keep = True
                    
                    if keep:
                        filtered.append(svc)
                
                # Debug: Log what was kept
                if layer_key == 'security':
                    try:
                        with open('Output/Summary/security_filtering.log','a') as _dbg:
                            _dbg.write(f"After filtering, kept services: {filtered}\n")
                    except:
                        pass
                
                services_by_layer[layer_key] = filtered
        except Exception as e:
            # Log any exceptions
            try:
                with open('Output/Summary/filter_exception.log','w') as _dbg:
                    _dbg.write(f"Exception during filtering: {e}\n")
                    import traceback
                    _dbg.write(traceback.format_exc())
            except:
                pass
            pass

        svc_global_idx = 0
        for layer_key in layer_order:
            layer_services = services_by_layer.get(layer_key, [])
            # Debug: Log layer services
            if layer_key == 'security':
                try:
                    with open('Output/Summary/security_layer.log','w') as _dbg:
                        _dbg.write(f"Security layer services: {layer_services}\n")
                except:
                    pass
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
            
            # Identify storage hierarchies:
            # - Storage Account -> Storage Container/Blob/Queue/File/Table
            # Friendly-name defaults (ensure variables exist regardless of detection path)
            _STORAGE_PARENT = "Storage Account"
            _STORAGE_CONTAINER = "Storage Container"
            _STORAGE_BLOB = "Storage Blob"
            _STORAGE_PARENT_TYPES = frozenset({"azurerm_storage_account"})
            _STORAGE_CHILD_TYPES = frozenset({
                "azurerm_storage_container", "azurerm_storage_blob", "azurerm_storage_queue",
                "azurerm_storage_share", "azurerm_storage_table",
            })
            storage_parent_service = next(
                (s for s in layer_services if set(non_boundary_parents.get(s, [])) & _STORAGE_PARENT_TYPES),
                None,
            )
            storage_nested_services: set[str] = set()
            if storage_parent_service:
                for s in layer_services:
                    if s == storage_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_STORAGE_CHILD_TYPES):
                        storage_nested_services.add(s)
                # Fallback to friendly-name detection for backwards compatibility
                if not storage_nested_services:
                    _STORAGE_PARENT = "Storage Account"
                    _STORAGE_CONTAINER = "Storage Container"
                    _STORAGE_BLOB = "Storage Blob"
                    if _STORAGE_PARENT in layer_services:
                        if _STORAGE_CONTAINER in layer_services:
                            storage_nested_services.add(_STORAGE_CONTAINER)
                        if _STORAGE_BLOB in layer_services:
                            storage_nested_services.add(_STORAGE_BLOB)

            _S3_PARENT = "S3 Bucket"
            # Policy resources remain context-only; do not render policy nodes on architecture.
            _S3_CHILDREN = {"Public Access Block", "S3 Bucket Acl", "S3 Bucket ACL", "S3 Bucket Ownership Controls"}
            s3_nested_services: set[str] = set()
            if _S3_PARENT in layer_services:
                for s in layer_services:
                    if s in _S3_CHILDREN:
                        s3_nested_services.add(s)

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

            # Identify Cosmos DB: account -> databases/containers
            _COSMOS_PARENT_TYPES = frozenset({"azurerm_cosmosdb_account"})
            _COSMOS_CHILD_TYPES = frozenset({
                "azurerm_cosmosdb_sql_database", "azurerm_cosmosdb_sql_container",
                "azurerm_cosmosdb_mongo_database", "azurerm_cosmosdb_mongo_collection",
                "azurerm_cosmosdb_cassandra_keyspace", "azurerm_cosmosdb_cassandra_table",
                "azurerm_cosmosdb_table", "azurerm_cosmosdb_gremlin_graph",
            })
            cosmos_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _COSMOS_PARENT_TYPES),
                None,
            )
            cosmos_nested_services: set[str] = set()
            if cosmos_parent_service:
                for s in layer_services:
                    if s == cosmos_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_COSMOS_CHILD_TYPES):
                        cosmos_nested_services.add(s)

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

            # Identify Load Balancer hierarchy: listeners and target groups nested inside LB
            _LB_PARENT_TYPES = frozenset({"aws_elb", "aws_alb", "aws_lb", "azurerm_lb", "azurerm_application_gateway"})
            _LB_CHILD_TYPES = frozenset({
                "aws_lb_listener", "aws_alb_listener", "aws_lb_target_group", "aws_alb_target_group",
                "aws_lb_target_group_attachment", "aws_lb_listener_rule", "azurerm_lb_backend_address_pool", "azurerm_lb_rule",
                "azurerm_application_gateway_http_listener",
            })
            lb_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _LB_PARENT_TYPES),
                None,
            )
            lb_nested_services: set[str] = set()
            if lb_parent_service:
                for s in layer_services:
                    if s == lb_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_LB_CHILD_TYPES):
                        lb_nested_services.add(s)

            # Identify Service Bus hierarchy: topics/queues/subscriptions nested inside namespace
            _SB_PARENT_TYPES = frozenset({"azurerm_servicebus_namespace", "azurerm_eventhub_namespace"})
            _SB_CHILD_TYPES = frozenset({
                "azurerm_servicebus_queue", "azurerm_servicebus_topic", "azurerm_servicebus_subscription",
                "azurerm_eventhub", "azurerm_eventhub_consumer_group",
            })
            sb_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _SB_PARENT_TYPES),
                None,
            )
            sb_nested_services: set[str] = set()
            if sb_parent_service:
                for s in layer_services:
                    if s == sb_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types.issubset(_SB_CHILD_TYPES):
                        sb_nested_services.add(s)

            # Identify ECS clustering: nest EC2 instances inside ECS Cluster when EC2-backed
            _ECS_PARENT_TYPES = frozenset({"aws_ecs_cluster"})
            _ECS_INSTANCE_TYPES = frozenset({"aws_instance", "aws_autoscaling_group", "aws_launch_configuration", "aws_launch_template"})
            ecs_parent_service = next(
                (s for s in layer_services
                 if set(non_boundary_parents.get(s, [])) & _ECS_PARENT_TYPES),
                None,
            )
            ecs_nested_instances: set[str] = set()
            if ecs_parent_service:
                # If the repo contains instance-like resources, assume EC2-backed cluster
                for s in layer_services:
                    if s == ecs_parent_service:
                        continue
                    svc_types = set(non_boundary_parents.get(s, []))
                    if svc_types and svc_types & _ECS_INSTANCE_TYPES:
                        ecs_nested_instances.add(s)

            # Keep child services hidden as standalone nodes; render them inside their parents.
            storage_child_candidates = set(storage_nested_services)
            s3_child_candidates = set(s3_nested_services)
            sql_child_candidates = set(sql_nested_services)
            kv_child_candidates = set(kv_nested_services)
            lb_child_candidates = set(lb_nested_services)
            sb_child_candidates = set(sb_nested_services)
            ecs_child_candidates = set(ecs_nested_instances)
            cosmos_child_candidates = set(cosmos_nested_services)

            storage_nested_services = storage_child_candidates
            s3_nested_services = s3_child_candidates
            sql_nested_services = sql_child_candidates
            kv_nested_services = kv_child_candidates
            lb_nested_services = lb_child_candidates
            sb_nested_services = sb_child_candidates
            ecs_nested_instances = ecs_child_candidates
            cosmos_nested_services = cosmos_child_candidates

            all_nested = (
                storage_child_candidates
                | s3_child_candidates
                | sql_child_candidates
                | kv_child_candidates
                | lb_child_candidates
                | sb_child_candidates
                | ecs_child_candidates
                | cosmos_child_candidates
            )

            # Adjust layer service count to account for all nested services
            visible_layer_services = [s for s in layer_services if s not in all_nested]

            # Skip layer wrapper if only one service (reduces visual noise)
            skip_layer_wrapper = len(visible_layer_services) == 1 and not (layer_key == "monitoring" and has_alerting_signal)
            # Force Identity layer wrapper even when there's a single identity service (improves clarity)
            if layer_key == "identity":
                skip_layer_wrapper = False
            
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
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
                    continue

                # S3 bucket with nested vulnerable child controls (policy/public-access-block)
                is_s3_with_children = service == _S3_PARENT and bool(s3_nested_services)
                if is_s3_with_children:
                    s3_names = service_instances.get(service, [])
                    s3_label = f"S3 Bucket ({s3_names[0]})" if len(s3_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{s3_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for s3_child in sorted(s3_nested_services):
                        child_names = service_instances.get(s3_child, [])
                        child_label = f"{s3_child} ({child_names[0]})" if len(child_names) == 1 else s3_child
                        child_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', s3_child)}"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
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
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
                    continue

                # Cosmos DB with nested databases/containers
                is_cosmos_with_children = cosmos_parent_service and service == cosmos_parent_service and bool(cosmos_nested_services)
                if is_cosmos_with_children:
                    cosmos_names = service_instances.get(service, [])
                    cosmos_label = f"Cosmos DB ({cosmos_names[0]})" if len(cosmos_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{cosmos_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for cos_child in sorted(cosmos_nested_services):
                        child_names = service_instances.get(cos_child, [])
                        child_label = f"{cos_child} ({child_names[0]})" if len(child_names) == 1 else cos_child
                        child_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', cos_child)}"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
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
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
                    continue

                # Load Balancer with nested listeners/target groups
                is_lb_with_children = service == lb_parent_service and bool(lb_nested_services)
                if is_lb_with_children:
                    lb_names = service_instances.get(service, [])
                    lb_label = f"{service} ({lb_names[0]})" if len(lb_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{lb_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for lb_child in sorted(lb_nested_services):
                        child_names = service_instances.get(lb_child, [])
                        child_label = f"{lb_child} ({child_names[0]})" if len(child_names) == 1 else lb_child
                        child_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', lb_child)}"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
                    continue

                # Service Bus with nested topics/queues/subscriptions
                is_sb_with_children = service == sb_parent_service and bool(sb_nested_services)
                if is_sb_with_children:
                    sb_names = service_instances.get(service, [])
                    sb_label = f"📨 {service} ({sb_names[0]})" if len(sb_names) == 1 else f"📨 {service}"
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{sb_label}"]')
                    style_id(svc_subgraph, 'messaging')
                    for sb_child in sorted(sb_nested_services):
                        child_names = service_instances.get(sb_child, [])
                        child_label = f"{sb_child} ({child_names[0]})" if len(child_names) == 1 else sb_child
                        child_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', sb_child)}"
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, 'messaging')
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    service_anchor_nodes[service] = exposure_target
                    # Service Bus is not directly internet-facing, no Internet link
                    continue

                # ECS Cluster with EC2-backed instances nested
                is_ecs_with_instances = service == ecs_parent_service and bool(ecs_nested_instances)
                if is_ecs_with_instances:
                    ecs_names = service_instances.get(service, [])
                    ecs_label = f"{service} ({ecs_names[0]})" if len(ecs_names) == 1 else service
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{ecs_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    for inst_svc in sorted(ecs_nested_instances):
                        inst_names = service_instances.get(inst_svc, [])
                        inst_label = f"{inst_svc} ({inst_names[0]})" if len(inst_names) == 1 else inst_svc
                        inst_node = f"{svc_subgraph}_{re.sub(r'[^A-Za-z0-9]', '_', inst_svc)}"
                        lines.append(f'        {inst_node}["{inst_label}"]')
                        style_id(inst_node, layer_cat)
                    lines.append("      end")
                    exposure_target = svc_subgraph
                    if exposure_target:
                        service_anchor_nodes[service] = exposure_target
                        ecs_exposure_raw_types = set(service_raw_all)
                        for instance_service in ecs_nested_instances:
                            ecs_exposure_raw_types.update(non_boundary_parents.get(instance_service, []))
                        is_public, ingress_label, insecure_http = _service_internet_posture(
                            service,
                            sorted(ecs_exposure_raw_types),
                        )
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
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
                    # Extract actual APIM instance name from resources
                    apim_instance_name = None
                    for r in resources:
                        if r.resource_type in ("azurerm_api_management_api", "aws_api_gateway_rest_api", 
                                               "google_api_gateway_api", "oci_apigateway_gateway"):
                            props = getattr(r, 'properties', {}) or {}
                            apim_instance_name = props.get("api_management_instance_name")
                            if apim_instance_name:
                                # Clean up var. or data. prefixes
                                apim_instance_name = apim_instance_name.replace("var.", "").replace("data.", "")
                                break
                    
                    # Use instance name if found, mark as shared if variable reference
                    if apim_instance_name:
                        # If it's still a variable name, indicate it's a shared instance
                        if "_name" in apim_instance_name or apim_instance_name.startswith("api_management"):
                            apim_label = f"🔌 API Management (shared: {apim_instance_name})"
                        else:
                            apim_label = f"🔌 {apim_instance_name}"
                    else:
                        apim_label = f"🔌 {service}"
                    
                    svc_subgraph = f"{layer_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{apim_label}"]')
                    style_id(svc_subgraph, layer_cat)
                    operation_nodes = []  # Collect all operation nodes for ingress connections
                    operation_start_idx = 0  # Default start index for operation nodes (safe fallback)
                    apim_requires_auth = False  # Track if any API requires auth
                    backend_services = []  # Track backend services to render outside APIM
                    
                    # Extract all APIM backend resources
                    apim_backends = _extract_apim_backends(repo_path) if repo_path else {}
                    backend_nodes_created = {}  # Track backend nodes: {backend_label: node_id}
                    
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
                        
                        # Track operation -> backend mappings for routing arrows
                        operation_backend_mappings = []
                        
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
                            
                            # Check if this operation references a backend
                            backend_ref = _extract_backend_from_policy(repo_path, op) if repo_path else None
                            if backend_ref and backend_ref in apim_backends:
                                operation_backend_mappings.append({
                                    "operation_node": op_node,
                                    "backend_label": backend_ref,
                                    "backend_info": apim_backends[backend_ref]
                                })
                        
                        # Note: Policies are documented in markdown, not shown in diagram
                        
                        lines.append("        end")
                        
                        # Create backend nodes and connections if any operations use backends
                        if operation_backend_mappings:
                            for mapping in operation_backend_mappings:
                                backend_label = mapping["backend_label"]
                                backend_info = mapping["backend_info"]
                                
                                # Create backend node if not already created
                                if backend_label not in backend_nodes_created:
                                    backend_node_id = f"{svc_subgraph}_Backend_{backend_label}"
                                    backend_display_name = backend_info["name"]
                                    backend_url = backend_info["url"]
                                    
                                    # Clean up variable references in URL for display
                                    display_url = backend_url
                                    if "${var." in display_url:
                                        # Simplify: https://${var.env}-api.${var.domain} → https://{env}-api.{domain}
                                        display_url = re.sub(r'\$\{var\.([^}]+)\}', r'{\1}', display_url)
                                    
                                    lines.append(f'        {backend_node_id}["🎯 Backend: {backend_display_name}<br/>{display_url}"]')
                                    style_id(backend_node_id, 'app')
                                    backend_nodes_created[backend_label] = backend_node_id
                                
                                # Add arrow from operation to backend
                                backend_node_id = backend_nodes_created[backend_label]
                                add_link(mapping["operation_node"], backend_node_id, label="routes to")
                        
                        # Extract backend routing info (render later outside APIM)
                        backend_url = _extract_apim_backend_url(repo_path, api_name)
                        if backend_url and repo_k8s_deployments:
                            for deployment in repo_k8s_deployments:
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
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        
                        # API Gateways always show ingress to operations (Internet if public, Client otherwise)
                        auth_label = _detect_api_auth_mechanism(service, service_raw_all, resources)
                        source = "Internet" if is_public else "Client"
                        for op_node in operation_nodes:
                            add_link(source, op_node, label=auth_label, red=insecure_http)
                    else:
                        # Standard case: single exposure point
                        service_anchor_nodes[service] = exposure_target
                        is_public, ingress_label, insecure_http = _service_internet_posture(service, service_raw_all)
                        if not _is_edge_gateway_service(service) and is_public:
                            add_link("Internet", exposure_target, label=ingress_label, red=insecure_http)
                    
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

        # Explicitly show ingress to non-API gateway edge services (Load Balancers, App Gateway, etc.)
        # API Gateways are handled above with direct operation connections
        for service_name, node_id in service_anchor_nodes.items():
            if not node_id or not _is_edge_gateway_service(service_name):
                continue
            
            # Skip API Gateways - they're handled with operation-level connections above
            is_api_gateway = any(tok in service_name.lower() for tok in ('api management', 'api gateway', 'apim'))
            if is_api_gateway:
                continue
            
            service_raw_all = sorted(set(non_boundary_parents.get(service_name, [])))
            is_public, ingress_label, insecure_http = _service_internet_posture(service_name, service_raw_all)
            
            if is_public:
                add_link("Internet", node_id, label=ingress_label, red=insecure_http)

        lines.append("  end")

    # Remove unused ingress nodes (Internet/Client)
    has_internet_edges = any(line.strip().startswith("Internet -->") for line in edge_lines)
    has_client_edges = any(line.strip().startswith("Client -->") for line in edge_lines)
    
    if not has_internet_edges:
        lines = [line for line in lines if line.strip() != "Internet[Internet Users]"]
    if not has_client_edges:
        lines = [line for line in lines if line.strip() != "Client[Client / Unknown Source]"]

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
    if "alert_policy" in resource_type or "security_alert" in resource_type:
        return " `[security alerting — not on diagram]`"
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

    lines: list[str] = []
    for item in assumptions[:20]:
        gap = item.get("gap_type", "assumption")
        confidence = item.get("confidence", "unknown")
        assumption_text = item.get("assumption_text") or item.get("context") or "Unspecified assumption"
        suggested = item.get("suggested_value")
        suffix = f" (suggested next step: {suggested})" if suggested else ""
        lines.append(f"- [{gap} / {confidence}] {assumption_text}{suffix}")
    if len(assumptions) > 20:
        lines.append(f"- ... {len(assumptions) - 20} more pending assumptions")
    return "\n".join(lines)


def _get_opengrep_misconfig_findings(
    db_conn, experiment_id: str | None, repo_name: str
) -> list[dict]:
    """Query the findings table for opengrep misconfiguration results for this repo."""
    if db_conn is None or not experiment_id:
        return []
    try:
        rows = db_conn.execute(
            """
            SELECT f.rule_id, f.message, f.base_severity, f.file_path, f.line_start,
                   r.resource_type, r.resource_name
            FROM findings f
            LEFT JOIN resources r ON f.resource_id = r.id
            JOIN repositories repo ON f.repo_id = repo.id
            WHERE LOWER(repo.repo_name) = LOWER(?)
              AND repo.experiment_id = ?
              AND f.base_severity != 'INFO'
              AND f.rule_id NOT LIKE '%-detection'
              AND f.rule_id NOT LIKE '%context%'
            ORDER BY
              CASE f.base_severity
                WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'WARNING' THEN 3
                WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END,
              f.rule_id
            """,
            (repo_name, experiment_id),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _build_auto_findings(
    context: RepositoryContext,
    repo_path: Path | None,
    experiment_id: str | None = None,
    repo_name: str | None = None,
) -> str:
    """Auto-detect misconfigurations from opengrep DB findings and heuristics.

    Primary source: findings table (opengrep scan results).
    Fallback heuristics:
    - SQL auditing/alert policies present but disabled + permissive SQL firewall (0.0.0.0) => HIGH RISK
    - Presence of Transparent Data Encryption (TDE) noted as a positive control
    """
    lines: list[str] = []
    db_findings: list[dict] = []
    try:
        from db_helpers import get_db_connection
        _eff_repo_name = repo_name or (getattr(context, "repo_name", None))
        if _eff_repo_name and experiment_id:
            with get_db_connection() as _conn:
                db_findings = _get_opengrep_misconfig_findings(_conn, experiment_id, _eff_repo_name)
    except Exception:
        pass

    if db_findings:
        lines.append("## Misconfigurations Found\n")
        lines.append("| Severity | Rule | Resource | File | Message |")
        lines.append("|---|---|---|---|---|")
        for f in db_findings:
            sev = f.get("base_severity") or ""
            rule = f.get("rule_id") or ""
            res_name = f.get("resource_name") or ""
            res_type = f.get("resource_type") or ""
            resource_col = f"{res_name} ({res_type})" if res_name else (res_type or "—")
            fp = f.get("file_path") or ""
            ls = f.get("line_start")
            file_col = f"{fp}:{ls}" if fp and ls else (fp or "—")
            msg = (f.get("message") or "").replace("|", "\\|")
            lines.append(f"| {sev} | {rule} | {resource_col} | {file_col} | {msg} |")
        lines.append("")

    try:
        import re
        from pathlib import Path as _Path

        has_audit_policy = []
        audit_disabled = False
        has_alert_policy = []
        alert_disabled = False
        has_tde = False
        sql_firewall_resources = []
        open_sql_firewalls = []
        tf_blocks: list[str] = []
        if repo_path:
            repo_p = _Path(repo_path)
            for tf in repo_p.rglob("*.tf"):
                try:
                    tf_blocks.append(tf.read_text(errors="ignore"))
                except Exception:
                    continue

        for r in context.resources:
            t = r.resource_type.lower()
            if "auditing_policy" in t or "auditing" in t:
                has_audit_policy.append(r)
            if "security_alert_policy" in t or "alert_policy" in t or "security_alert" in t:
                has_alert_policy.append(r)
            if "transparent_data_encryption" in t or "transparent" in t and "encryption" in t:
                has_tde = True
            if "firewall_rule" in t and "mssql" in t:
                sql_firewall_resources.append(r)

        # Helper: inspect TF block for a resource name to find disabled flags
        def _block_has_disabled(name: str) -> bool:
            if not tf_blocks:
                return False
            blk_re = re.compile(rf'resource\s+"[^"]+"\s+"{re.escape(name)}"\s*\{{', re.I)
            for txt in tf_blocks:
                m = blk_re.search(txt)
                if not m:
                    continue
                start = m.start()
                # crude block capture: from m.start() to next closing brace at same level
                i = m.end()
                brace = 1
                while i < len(txt) and brace > 0:
                    if txt[i] == '{':
                        brace += 1
                    elif txt[i] == '}':
                        brace -= 1
                    i += 1
                block = txt[m.start():i]
                if re.search(r"^\s*enabled\s*=\s*false", block, re.I | re.M):
                    return True
                if re.search(r"state\s*=\s*\"Disabled\"", block, re.I):
                    return True
                if re.search(r"log_monitoring_enabled\s*=\s*false", block, re.I):
                    return True
            return False

        for a in has_audit_policy:
            if _block_has_disabled(a.name):
                audit_disabled = True
        for a in has_alert_policy:
            if _block_has_disabled(a.name):
                alert_disabled = True
        for fw in sql_firewall_resources:
            if not tf_blocks:
                continue
            # check firewall block for 0.0.0.0
            blk_re = re.compile(rf'resource\s+"[^"]+"\s+"{re.escape(fw.name)}"\s*\{{', re.I)
            for txt in tf_blocks:
                m = blk_re.search(txt)
                if not m:
                    continue
                start = m.start()
                i = m.end()
                brace = 1
                while i < len(txt) and brace > 0:
                    if txt[i] == '{':
                        brace += 1
                    elif txt[i] == '}':
                        brace -= 1
                    i += 1
                block = txt[m.start():i]
                if re.search(r"0\.0\.0\.0", block):
                    open_sql_firewalls.append(fw)
                    break

        # Build findings
        if (audit_disabled or alert_disabled) and open_sql_firewalls:
            lines.append("### 🔥 High risk: Auditing/alerts disabled with permissive SQL firewall")
            if audit_disabled:
                lines.append("- SQL auditing resources are present but disabled (enabled = false or log_monitoring_enabled = false). This removes logging/forensics.")
            if alert_disabled:
                lines.append("- SQL server security alert policy is disabled (state = \"Disabled\"). Alerts will not be raised.")
            lines.append("- Found SQL firewall rule(s) with 0.0.0.0 allowing broad access:")
            for fw in open_sql_firewalls:
                lines.append(f"  - {fw.name} ({fw.resource_type})")
            lines.append("")
            lines.append("Recommendation: enable auditing (enabled = true, log_monitoring_enabled = true), set server security alert policy state to \"Enabled\", and remove or restrict any 0.0.0.0 firewall rules. Send diagnostics to Log Analytics or Storage for retention.")
        else:
            if not db_findings:
                lines.append("- No misconfigurations detected by opengrep scan.")
            # If db_findings exist, heuristics found nothing extra — omit the redundant message

        if has_tde:
            lines.append("\n- Note: Transparent Data Encryption (TDE) is configured — this protects data at rest but does not replace auditing/alerting.")

    except Exception as e:
        return f"- Error building auto-findings: {e}"

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


def _relationship_kind_value(rel: object) -> str:
    kind = getattr(rel, "relationship_type", "")
    return kind.value if hasattr(kind, "value") else str(kind)


def _load_repo_topology_connections(repo_name: str) -> list[dict]:
    from db_helpers import get_connections_for_diagram, get_db_connection

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT experiment_id
            FROM repositories
            WHERE repo_name = ?
            ORDER BY scanned_at DESC, id DESC
            LIMIT 1
            """,
            [repo_name],
        ).fetchone()

    if not row:
        return []

    experiment_id = row["experiment_id"]
    return get_connections_for_diagram(experiment_id, repo_name=repo_name)


def _is_edge_gateway_signal(resource_type: str, resource_name: str) -> bool:
    signal = f"{resource_type}.{resource_name}".lower()
    edge_tokens = (
        "api_management",
        "api_gateway",
        "application_gateway",
        "front_door",
        "load_balancer",
        "ingress",
        "gateway",
        "waf",
        "cloudfront",
    )
    return any(token in signal for token in edge_tokens)


def _infer_protocol_port(resource_type: str, resource_name: str) -> tuple[str | None, str | None]:
    """Infer protocol and port from a target resource type/name for egress and ports tabs."""
    signal = f"{resource_type}.{resource_name}".lower()
    if "service_bus" in signal or "servicebus" in signal:
        return ("AMQP", "5671")
    if "sql" in signal:
        return ("TCP", "1433")
    if "redis" in signal or "cache" in signal:
        return ("TCP", "6380")
    if "storage" in signal or "blob" in signal or "queue" in signal and "service_bus" not in signal:
        return ("HTTPS", "443")
    if "vault" in signal or "key_vault" in signal:
        return ("HTTPS", "443")
    if "apim" in signal or "api_management" in signal:
        return ("HTTPS", "443")
    if "kubernetes" in signal or "aks" in signal or "container" in signal:
        return ("HTTPS", "443")
    if "http" in signal:
        return ("HTTP", "80")
    return (None, None)


def _mermaid_node_id(label: str, used_ids: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", label.strip()) or "node"
    if base[0].isdigit():
        base = f"n_{base}"
    node_id = base
    idx = 2
    while node_id in used_ids:
        node_id = f"{base}_{idx}"
        idx += 1
    used_ids.add(node_id)
    return node_id


def _build_flow_mermaid(
    edges: list[tuple[str, str, str]],
    *,
    include_internet: bool,
    direction: str = "LR",
) -> str:
    if not edges:
        return ""

    lines = ["```mermaid", f"flowchart {direction}"]
    if include_internet:
        lines.append("    Internet[Internet]")

    node_ids: dict[str, str] = {}
    used_ids: set[str] = set()
    for src, dst, _ in edges:
        for raw in (src, dst):
            if raw in node_ids:
                continue
            node_ids[raw] = _mermaid_node_id(raw, used_ids)
            pretty = raw.replace("__inferred__", "").replace('"', "'")
            if not pretty.strip():
                pretty = "Unresolved target"
            lines.append(f'    {node_ids[raw]}["{pretty}"]')

    if include_internet:
        linked_sources: set[str] = set()
        for src, _, _ in edges:
            src_id = node_ids[src]
            if src_id in linked_sources:
                continue
            lines.append(f"    Internet --> {src_id}")
            linked_sources.add(src_id)

    for src, dst, label in edges:
        src_id = node_ids[src]
        dst_id = node_ids[dst]
        edge_label = (label or "").replace("_", " ")
        if edge_label:
            lines.append(f"    {src_id} -->|{edge_label}| {dst_id}")
        else:
            lines.append(f"    {src_id} --> {dst_id}")

    lines.append("```")
    return "\n".join(lines)


def _collect_relationship_topology(
    context: RepositoryContext,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], set[str]]:
    relationships = list(getattr(context, "relationships", []) or [])
    ingress_rel_types = {"routes_ingress_to"}
    egress_rel_types = {
        "depends_on",
        "grants_access_to",
        "authenticates_via",
        "encrypts",
        "restricts_access",
        "monitors",
    }

    ingress_seen: set[tuple[str, str, str]] = set()
    egress_seen: set[tuple[str, str, str]] = set()
    ingress_edges: list[tuple[str, str, str]] = []
    egress_edges: list[tuple[str, str, str]] = []
    unresolved_targets: set[str] = set()

    for rel in relationships:
        src_name = str(getattr(rel, "source_name", "") or "").strip()
        dst_name = str(getattr(rel, "target_name", "") or "").strip()
        src_type = str(getattr(rel, "source_type", "") or "")
        dst_type = str(getattr(rel, "target_type", "") or "")
        rel_type = _relationship_kind_value(rel)
        if not src_name or not dst_name:
            continue

        if dst_type == "unknown":
            unresolved_targets.add(dst_name)
            continue

        edge = (src_name, dst_name, rel_type)
        if rel_type in ingress_rel_types or _is_edge_gateway_signal(src_type, src_name):
            if edge not in ingress_seen:
                ingress_seen.add(edge)
                ingress_edges.append(edge)
        if rel_type in egress_rel_types:
            if edge not in egress_seen:
                egress_seen.add(edge)
                egress_edges.append(edge)

    return ingress_edges, egress_edges, unresolved_targets


def _connection_summary_label(connection: dict) -> str:
    connection_type = str(connection.get("connection_type") or "").strip().replace("_", " ")
    auth_method = str(connection.get("auth_method") or "").strip()
    via_component = str(connection.get("via_component") or "").strip()
    source_name = str(connection.get("source") or "").strip()
    target_name = str(connection.get("target") or "").strip()
    encryption = _normalize_optional_bool(connection.get("is_encrypted"))

    parts: list[str] = []
    if connection_type:
        parts.append(connection_type)
    if auth_method:
        parts.append(f"auth={auth_method}")
    if encryption is True:
        parts.append("encrypted")
    elif encryption is False:
        parts.append("not encrypted")
    if via_component and via_component not in {source_name, target_name}:
        parts.append(f"via {via_component}")
    return ", ".join(parts)


def _collect_db_topology_edges(
    repo_name: str,
    db_connections: list[dict],
    exclude_connection_types: set[str] | None = None,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    ingress_types = {"routes_ingress_to", "ingress", "public_ingress", "internet_ingress"}
    egress_types = {
        "depends_on",
        "grants_access_to",
        "authenticates_via",
        "encrypts",
        "restricts_access",
        "monitors",
        "egress",
    }

    ingress_seen: set[tuple[str, str, str]] = set()
    egress_seen: set[tuple[str, str, str]] = set()
    ingress_edges: list[tuple[str, str, str]] = []
    egress_edges: list[tuple[str, str, str]] = []

    for connection in db_connections:
        src_name = str(connection.get("source") or "").strip()
        dst_name = str(connection.get("target") or "").strip()
        if not src_name or not dst_name:
            continue

        connection_type = str(connection.get("connection_type") or "").strip()
        # Optionally exclude connection types (e.g., permissions) from diagram edge generation
        if exclude_connection_types and connection_type in exclude_connection_types:
            continue

        source_repo = str(connection.get("source_repo") or "").strip()
        target_repo = str(connection.get("target_repo") or "").strip()
        label = _connection_summary_label(connection) or connection_type.replace("_", " ")
        edge = (src_name, dst_name, label)

        is_cross_repo_ingress = target_repo == repo_name and source_repo and source_repo != repo_name
        is_cross_repo_egress = source_repo == repo_name and target_repo and target_repo != repo_name
        is_ingress = (
            is_cross_repo_ingress
            or connection_type in ingress_types
            or _is_edge_gateway_signal(str(connection.get("source_type") or ""), src_name)
        )
        is_egress = is_cross_repo_egress or connection_type in egress_types

        if is_ingress and edge not in ingress_seen:
            ingress_seen.add(edge)
            ingress_edges.append(edge)
        if is_egress and edge not in egress_seen:
            egress_seen.add(edge)
            egress_edges.append(edge)

    return ingress_edges, egress_edges


def _collect_permissions_edges(context: RepositoryContext, repo_name: str, db_connections: list[dict] | None = None) -> list[tuple[str, str, str]]:
    """Collect edges representing permissions relationships (grants_access_to) from both
    extracted relationships and DB-backed topology connections. Returns a deduplicated
    list of (source, target, label) tuples suitable for rendering as a Mermaid flow.
    """
    edges: set[tuple[str, str, str]] = set()

    # Relationships extracted into the RepositoryContext
    relationships = list(getattr(context, "relationships", []) or [])
    for rel in relationships:
        rel_type = _relationship_kind_value(rel)
        if rel_type == "grants_access_to":
            src = str(getattr(rel, "source_name", "") or "").strip()
            dst = str(getattr(rel, "target_name", "") or "").strip()
            if src and dst:
                edges.add((src, dst, "grants access to"))

    # DB-backed connections
    if db_connections is None:
        try:
            db_connections = _load_repo_topology_connections(repo_name)
        except Exception:
            db_connections = []

    for conn in db_connections or []:
        ct = str(conn.get("connection_type") or "").strip()
        if ct == "grants_access_to":
            src = str(conn.get("source") or "").strip()
            dst = str(conn.get("target") or "").strip()
            if src and dst:
                label = _connection_summary_label(conn) or "grants access to"
                edges.add((src, dst, label))

    return sorted(edges)


def _build_permissions_section(context: RepositoryContext, *, repo_name: str, db_connections: list[dict] | None = None) -> str:
    """Render a permissions mapping section (Mermaid) for grant-style relationships.

    This is intended for repository-level summaries and MUST NOT be included on
    provider/architecture diagrams.
    """
    edges = _collect_permissions_edges(context, repo_name, db_connections=db_connections)
    if not edges:
        return "- No permissions mapping detected (no 'grants_access_to' relationships captured)."
    # Render as a fenced Mermaid flowchart without Internet node
    return _build_flow_mermaid(edges, include_internet=False)


def _build_ingress_egress_summaries(
    context: RepositoryContext,
    *,
    repo_name: str,
    db_connections: list[dict] | None = None,
) -> tuple[str, str, dict[str, bool]]:
    rel_ingress_edges, rel_egress_edges, unresolved_targets = _collect_relationship_topology(context)
    connections = db_connections if db_connections is not None else _load_repo_topology_connections(repo_name)
    db_ingress_edges, db_egress_edges = _collect_db_topology_edges(repo_name, connections)

    if db_ingress_edges:
        ingress_summary = _build_flow_mermaid(db_ingress_edges[:12], include_internet=True)
    elif rel_ingress_edges:
        ingress_summary = (
            "Fallback (no DB-backed ingress topology signals detected):\n"
            + _build_flow_mermaid(rel_ingress_edges[:12], include_internet=True)
        )
    else:
        ingress_summary = "- Fallback: no DB-backed ingress topology signals detected."

    if db_egress_edges:
        egress_summary = _build_flow_mermaid(db_egress_edges[:12], include_internet=False)
    elif rel_egress_edges:
        egress_summary = (
            "Fallback (no DB-backed egress topology signals detected):\n"
            + _build_flow_mermaid(rel_egress_edges[:12], include_internet=False)
        )
    elif unresolved_targets:
        lines = ["- Fallback: no DB-backed egress topology signals; unresolved extracted dependencies:"]
        for target in sorted(unresolved_targets)[:10]:
            lines.append(f"  - {target}")
        egress_summary = "\n".join(lines)
    else:
        egress_summary = "- Fallback: no DB-backed egress topology signals detected."

    return ingress_summary, egress_summary, {
        "ingress_from_db": bool(db_ingress_edges),
        "egress_from_db": bool(db_egress_edges),
        "ingress_has_edges": bool(db_ingress_edges or rel_ingress_edges),
        "egress_has_edges": bool(db_egress_edges or rel_egress_edges),
    }


def _build_external_dependencies_summary(
    context: RepositoryContext,
    *,
    repo_name: str,
    repo_path: Path | None,
    db_connections: list[dict],
) -> str:
    def _detect_sql_dependency_targets() -> set[str]:
        sql_targets: set[str] = set()
        sql_tokens = ('sql', 'mssql', 'database')

        # DB topology signals
        for connection in db_connections:
            src_name = str(connection.get("source") or "").strip().lower()
            dst_name = str(connection.get("target") or "").strip().lower()
            if any(tok in src_name for tok in sql_tokens) or any(tok in dst_name for tok in sql_tokens):
                sql_targets.add("SQL Server")

        # Extracted resources signals
        for resource in context.resources:
            r_name = str(getattr(resource, "name", "") or "").strip().lower()
            r_type = str(getattr(resource, "resource_type", "") or "").strip().lower()
            if any(tok in r_type for tok in ('sql', 'database', 'mssql')):
                sql_targets.add("SQL Server")
                continue
            # Keep name-based inference conservative by requiring sql/mssql tokens.
            if 'sql' in r_name or 'mssql' in r_name:
                sql_targets.add("SQL Server")

        # File-content signals (connection strings/runtime SQL images)
        if repo_path and repo_path.exists():
            patterns = [
                re.compile(r"ConnectionStrings?(__|\.|:)?SqlServer", re.IGNORECASE),
                re.compile(r"Server=[^;]+;Database=[^;]+;", re.IGNORECASE),
                re.compile(r"azure-sql-edge|mssql-tools|database\.windows\.net", re.IGNORECASE),
            ]
            candidate_suffixes = {
                '.json', '.yaml', '.yml', '.env', '.md', '.txt', '.config', '.tf', '.cs'
            }
            candidate_names = {
                'docker-compose.yml', 'docker-compose.yaml', 'dockerfile', 'appsettings.json',
                'appsettings.development.json', '.env'
            }

            scanned = 0
            for file in repo_path.rglob('*'):
                if scanned >= 400:
                    break
                if not file.is_file():
                    continue
                if any(part in {'.git', '.terraform', '.venv', 'node_modules', '__pycache__'} for part in file.parts):
                    continue

                name_lower = file.name.lower()
                if file.suffix.lower() not in candidate_suffixes and name_lower not in candidate_names:
                    continue

                scanned += 1
                try:
                    text = file.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    continue

                if any(p.search(text) for p in patterns):
                    sql_targets.add("SQL Server")
                    break

        return sql_targets

    local_resource_names = {r.name for r in context.resources if getattr(r, "name", None)}
    db_external_targets: set[str] = set()

    for connection in db_connections:
        src_name = str(connection.get("source") or "").strip()
        dst_name = str(connection.get("target") or "").strip()
        source_repo = str(connection.get("source_repo") or "").strip()
        target_repo = str(connection.get("target_repo") or "").strip()
        if not src_name or not dst_name or dst_name == "Internet":
            continue

        if source_repo == repo_name and target_repo and target_repo != repo_name:
            db_external_targets.add(dst_name)
            continue
        if src_name in local_resource_names and dst_name not in local_resource_names:
            db_external_targets.add(dst_name)

    sql_dependency_targets = _detect_sql_dependency_targets()
    merged_targets = set(db_external_targets)
    merged_targets.update(sql_dependency_targets)

    if merged_targets:
        lines = ["- External dependencies detected:"]
        for target in sorted(merged_targets):
            lines.append(f"  - {target}")
        return "\n".join(lines)

    fallback_targets: set[str] = set()
    for connection in context.connections:
        src_name = str(getattr(connection, "source", "") or "").strip()
        dst_name = str(getattr(connection, "target", "") or "").strip()
        if not dst_name or dst_name == "Internet":
            continue
        if (src_name == repo_name or src_name in local_resource_names) and dst_name not in local_resource_names:
            fallback_targets.add(dst_name)

    if repo_path and repo_path.exists():
        deployments = _extract_kubernetes_deployments(repo_path)
        for deployment in deployments:
            module_name = deployment.get("module_name")
            for dependency in _extract_service_dependencies(repo_path, module_name):
                fallback_targets.add(dependency)

    if fallback_targets:
        fallback_targets.update(sql_dependency_targets)
        lines = [
            "- Fallback externLoad previous resultal dependencies (DB topology had no outbound external dependency signals):"
        ]
        for target in sorted(fallback_targets):
            lines.append(f"  - {target}")
        return "\n".join(lines)

    if sql_dependency_targets:
        lines = ["- External dependencies detected:"]
        for target in sorted(sql_dependency_targets):
            lines.append(f"  - {target}")
        return "\n".join(lines)

    return "- No external dependencies detected."


def _build_service_only_architecture_diagram(repo_name: str, context: RepositoryContext, repo_path: "Path | None" = None) -> str:
    """
    Build a Mermaid flowchart focused only on detected services (no cloud/provider labels).
    Prefer DB topology edges when available; otherwise fall back to a simple service -> repo diagram.

    Returns a Mermaid diagram body (starting with 'flowchart ...') without fenced code block.
    """
    # Attempt to use DB topology if present
    try:
        db_connections = _load_repo_topology_connections(repo_name)
        # Exclude permission-style topology edges (e.g., grants_access_to) from the
        # service-only architecture diagram so permissions are shown only in the
        # Repo summary permissions section.
        db_ingress_edges, db_egress_edges = _collect_db_topology_edges(
            repo_name, db_connections, exclude_connection_types={"grants_access_to"}
        )
        all_db_edges = db_ingress_edges + db_egress_edges
    except Exception:
        all_db_edges = []

    if all_db_edges:
        # _build_flow_mermaid returns a fenced code block; strip fences and return the inner diagram
        fenced = _build_flow_mermaid(all_db_edges[:48], include_internet=True, direction="TB")
        # fenced starts with ```mermaid\nflowchart ... and ends with ```
        inner = fenced.split('\n', 1)[1].rsplit('\n', 1)[0] if '\n' in fenced else fenced
        return inner

    # Fallback: build a simple flowchart connecting detected services to the repo
    # Build friendly -> raw types grouping
    db = _get_db()
    actual_names = _terraform_actual_names(repo_path)
    resource_types = sorted({r.resource_type for r in context.resources})
    all_groups = _group_parent_services(resource_types)

    # Create nodes and edges
    used_ids: set[str] = set()
    node_ids: dict[str, str] = {}
    def _id_for(label: str) -> str:
        if label in node_ids:
            return node_ids[label]
        nid = _mermaid_node_id(label, used_ids)
        node_ids[label] = nid
        return nid

    lines = ["flowchart TB"]
    # Repo node
    repo_id = _id_for(f"{repo_name}")
    lines.append(f'  {repo_id}["{repo_name}"]')

    # Add Internet node
    internet_id = _id_for("Internet")
    lines.append(f'  {internet_id}["Internet"]')

    services_added = []
    for friendly, raw_types in all_groups.items():
        # Skip empty friendly names
        if not friendly:
            continue
        svc_id = _id_for(friendly)
        # Define node
        lines.append(f'  {svc_id}["{friendly}"]')
        services_added.append((friendly, svc_id, raw_types))

    # Connect services to repo and optionally to Internet if edge-like
    for friendly, svc_id, raw_types in services_added:
        lines.append(f'  {svc_id} --> {repo_id}')
        if _is_edge_gateway_service(friendly):
            lines.append(f'  {internet_id} --> {svc_id}')

    # If any DB-extractable external deps exist, add them (best-effort)
    try:
        ext_summary = _build_external_dependencies_summary(context, repo_name=repo_name, repo_path=repo_path, db_connections=[])
        # Parse simple bullet list lines like '- External dependencies detected from DB topology:' followed by '  - Name'
        ext_targets = []
        for line in ext_summary.splitlines():
            m = re.match(r"\s*-\s+(.+)", line)
            if m:
                name = m.group(1).strip()
                # ignore header lines
                if name.lower().startswith("external dependencies"):
                    continue
                ext_targets.append(name)
        for tgt in sorted(set(ext_targets))[:20]:
            tgt_id = _id_for(tgt)
            lines.append(f'  {tgt_id}["{tgt}"]')
            # Connect repo or services to external target heuristically
            # If a service name appears in target, connect service -> target; else repo -> target
            connected = False
            for friendly, svc_id, _ in services_added:
                if friendly.lower() in tgt.lower() or tgt.lower() in friendly.lower():
                    lines.append(f'  {svc_id} --> {tgt_id}')
                    connected = True
                    break
            if not connected:
                lines.append(f'  {repo_id} --> {tgt_id}')
    except Exception:
        pass

    return "\n".join(lines)


def _md_to_html(md: str) -> str:
    """Convert a minimal subset of Markdown to HTML for DB persistence.

    Handles: headings (##/###), bullet lists, inline code, bold, Mermaid fences,
    horizontal rules, and plain paragraphs.
    """
    import html as _html_mod

    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    in_mermaid = False
    mermaid_lines: list[str] = []

    def _flush_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def _inline(text: str) -> str:
        # Bold **text**
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # Inline code `text`
        text = re.sub(r"`([^`]+)`", lambda m: f"<code>{_html_mod.escape(m.group(1))}</code>", text)
        return text

    for line in lines:
        if in_mermaid:
            if line.strip() == "```":
                out.append(f'<div class="mermaid">\n{chr(10).join(mermaid_lines)}\n</div>')
                mermaid_lines = []
                in_mermaid = False
            else:
                mermaid_lines.append(line)
            continue

        if line.strip().startswith("```mermaid"):
            _flush_list()
            in_mermaid = True
            mermaid_lines = []
            continue

        if line.strip().startswith("```"):
            _flush_list()
            # skip other code fences
            continue

        if re.match(r"^#{1,6}\s", line):
            _flush_list()
            level = len(line) - len(line.lstrip("#"))
            text = line.lstrip("#").strip()
            out.append(f"<h{level}>{_inline(text)}</h{level}>")
            continue

        if line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            indent = len(line) - len(line.lstrip(" "))
            text = line.lstrip(" -").strip()
            out.append(f"{'  ' * (indent // 2)}<li>{_inline(text)}</li>")
            continue

        if line.strip() in ("---", "***", "___"):
            _flush_list()
            out.append("<hr>")
            continue

        # Markdown table row (contains | chars) — skip raw, already rendered as structured data
        if "|" in line and line.strip().startswith("|"):
            _flush_list()
            # Very simple table: collect and render
            out.append(f"<p>{_inline(line.strip())}</p>")
            continue

        _flush_list()
        stripped = line.strip()
        if stripped:
            out.append(f"<p>{_inline(stripped)}</p>")

    _flush_list()
    return "\n".join(out)


def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    context: RepositoryContext,
    summary_dir: Path,
    experiment_id: str = "001",
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

    service_auth_topology_md = render_service_auth_topology(experiment_id, repo_name)

    # Roles & Permission Assignments
    roles_permissions = ""
    role_assignments = [r for r in context.resources if r.resource_type == "azurerm_role_assignment"]
    if role_assignments:
        roles_permissions += "| Role | Resource | Principal |\n|------|----------|----------|\n"
        for r in role_assignments:
            # role assignment properties are recorded in r.properties by the extractor
            props = getattr(r, 'properties', {}) or {}
            role = props.get('role_definition_name') or props.get('role_definition_id') or props.get('role_name') or 'Unknown'
            resource = props.get('scope') or props.get('resource_id') or 'Unknown'
            principal = props.get('principal_id') or props.get('principal') or props.get('principal_name') or 'Unknown'
            roles_permissions += f"| {role} | {resource} | {principal} |\n"
    else:
        roles_permissions = "- No role assignments detected."

    db_topology_connections = _load_repo_topology_connections(repo_name)
    permissions_mapping = _build_permissions_section(context, repo_name=repo_name, db_connections=db_topology_connections)
    ingress_summary, egress_summary, topology_flags = _build_ingress_egress_summaries(
        context,
        repo_name=repo_name,
        db_connections=db_topology_connections,
    )

    # Build service-only diagram (inner mermaid) and prefer provider-level cloud architecture if available.
    svc_diagram = _build_service_only_architecture_diagram(repo_name, context, repo_path=repo)
    architecture_diagram_content = svc_diagram
    try:
        if providers:
            provider_file = summary_dir / "Cloud" / f"Architecture_{_provider_title(providers[0])}.md"
            if provider_file.exists():
                txt = provider_file.read_text(encoding="utf-8", errors="ignore")
                start_idx = txt.find("```mermaid")
                if start_idx != -1:
                    start_idx = txt.find("\n", start_idx) + 1
                    end_idx = txt.find("```", start_idx)
                    if end_idx != -1:
                        architecture_diagram_content = txt[start_idx:end_idx].strip()
    except Exception:
        architecture_diagram_content = svc_diagram

    # If the generated service diagram looks like a permissions map and no explicit permissions
    # mapping exists, promote the service diagram into the permissions section.
    if "grants access to" in svc_diagram.lower() and permissions_mapping.strip().lower().startswith("- no permissions mapping"):
        permissions_mapping = "```mermaid\n" + svc_diagram + "\n```"
        # Try to use cloud architecture for the main architecture diagram (architecture_diagram_content already set above).

    # Optional fallback when APIs exist but no topology edges were captured from DB/extraction.
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
    if apis and not topology_flags["ingress_has_edges"]:
        ingress_lines = [
            "- Fallback assumption (no DB-backed ingress topology signals detected):",
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

    if apis and not topology_flags["egress_has_edges"]:
        egress_lines = [
            "- Fallback assumption (no DB-backed egress topology signals detected):",
            "```mermaid",
            "flowchart LR",
            f'    Service["{repo_name}"]',
        ]
        for i, api in enumerate(apis[:5], start=1):
            api_id = f"API{i}"
            egress_lines.append(f'    Service --> {api_id}["{api}"]')
        egress_lines.append("```")
        egress_summary = "\n".join(egress_lines)

    external_deps = _build_external_dependencies_summary(
        context,
        repo_name=repo_name,
        repo_path=repo,
        db_connections=db_topology_connections,
    )

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

    auto_findings_md = _build_auto_findings(context, repo, experiment_id=experiment_id, repo_name=repo_name)
    resource_inventory_md = _build_resource_inventory(provider_resources, repo_path=repo)

    content = render_template(
        "RepoSummary.md",
        {
            "repo_name": repo_name,
            "repo_type": "Infrastructure (likely IaC/platform)" if resource_types else "Application/Other",
            "timestamp": now_uk(),
            "architecture_diagram": architecture_diagram_content,
            "languages": language_text,
            "hosting": hosting_text,
            "ci_cd": ci_cd_text,
            "providers": provider_text,
            "auth_summary": auth_summary,
            "roles_permissions": roles_permissions,
            "permissions_mapping": permissions_mapping,
            "network_summary": network_summary,
            "ingress_summary": ingress_summary,
            "egress_summary": egress_summary,
            "external_deps": external_deps,
            "evidence_list": evidence_list,
            "notes": notes,
            "terraform_modules": modules_list_md,
            "resource_inventory": resource_inventory_md,
            "auto_findings": auto_findings_md,
            "enrichment_assumptions": _build_enrichment_assumptions(repo_name),
        },
    )
    try:
        # Use the summary_dir-provided log path when available
        try:
            log_path = out_path.parent.parent / "Repos" / "report_debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"[DEBUG] Writing summary to: {out_path}\n")
                log.write(f"[DEBUG] Extracted resources: {len(context.resources)}\n")
                log.write(f"[DEBUG] Providers: {providers}\n")
                log.write(f"[DEBUG] Repo name: {repo_name}\n")
        except Exception:
            # Fallback to legacy path if something goes wrong
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
        try:
            log_path = out_path.parent.parent / "Repos" / "report_debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"[ERROR] Failed to write summary content: {e}\n")
        except Exception:
            # Last-resort fallback to legacy path
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
        # Skip meta-providers that shouldn't have architecture diagrams
        if provider.lower() in ('terraform', 'kubernetes', 'unknown', ''):
            continue
        
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
                    value = json.dumps({"source": mod.get('source'), "file": mod.get('file'), "line": mod.get('line')})
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
        db_topology_connections = _load_repo_topology_connections(repo_name)
        external_dependencies = _build_external_dependencies_summary(
            context,
            repo_name=repo_name,
            repo_path=repo,
            db_connections=db_topology_connections,
        )
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
                "external_dependencies": external_dependencies,
                "paas_exposure_checks": paas_exposure_checks,
                "recommendations": recommendations,
            },
        )
        content = content.replace("\\`\\`\\`", "```")
        out_path.write_text(content, encoding="utf-8")
        validate_markdown_file(out_path, fix=True)
        out_files.append(out_path)

    return out_files


def generate_reports(
    context: RepositoryContext,
    summary_dir_str: str,
    repo_path: Path | None = None,
    experiment_id: str = "001",
) -> list[Path]:
    summary_dir = Path(summary_dir_str)
    summary_dir.mkdir(parents=True, exist_ok=True)
    repo = repo_path if repo_path is not None else Path(context.repository_name)

    # Ensure debug log lives under the provided summary_dir so experiments write locally
    log_path = summary_dir / "Repos" / "report_debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
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
            experiment_id=experiment_id,
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
    from db_helpers import insert_repository, insert_resource, insert_connection

    def _is_unresolved_tf_expression(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        lower = text.lower()
        # Unresolved references should not replace canonical resource labels.
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

    # Keep DB writes scoped to each helper call; holding a long-lived bootstrap
    # connection here causes sqlite write-lock contention in nested inserts.
    insert_repository(
        experiment_id=experiment_id,
        repo_path=Path(context.repository_name),
    )

    # Reruns can change display labels (for example unresolved var.* names later
    # resolving to concrete names). Clear prior repo-scoped resource rows so each
    # context discovery write is a clean snapshot for this repo/experiment.
    from db_helpers import get_db_connection as _gdb
    with _gdb(db_path) as conn:
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
            (experiment_id, context.repository_name),
        ).fetchone()
        if repo_row:
            repo_id = repo_row[0]
            resource_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM resources WHERE experiment_id = ? AND repo_id = ?",
                    (experiment_id, repo_id),
                ).fetchall()
            ]
            if resource_ids:
                placeholders = ",".join("?" for _ in resource_ids)
                conn.execute(
                    f"DELETE FROM resource_properties WHERE resource_id IN ({placeholders})",
                    resource_ids,
                )
                conn.execute(
                    f"DELETE FROM resource_connections WHERE source_resource_id IN ({placeholders}) OR target_resource_id IN ({placeholders})",
                    resource_ids + resource_ids,
                )
                conn.execute(
                    f"DELETE FROM findings WHERE resource_id IN ({placeholders})",
                    resource_ids,
                )
                # Optional table in newer schemas; best-effort cleanup.
                try:
                    conn.execute(
                        f"DELETE FROM shared_resource_references WHERE local_resource_id IN ({placeholders})",
                        resource_ids,
                    )
                except Exception:
                    pass
                conn.execute(
                    "DELETE FROM resources WHERE experiment_id = ? AND repo_id = ?",
                    (experiment_id, repo_id),
                )

    # First pass: insert all resources, collect type.name → db_id map
    res_db_ids: dict[str, int] = {}
    for resource in context.resources:
        # Use actual_name from properties if available (from Terraform attributes), 
        # otherwise fall back to resource.name (Terraform block label)
        display_name = resource.name
        
        if resource.properties and resource.properties.get("actual_name"):
            candidate_name = resource.properties["actual_name"]
            if not _is_unresolved_tf_expression(candidate_name):
                display_name = candidate_name
        
        db_id = insert_resource(
            experiment_id=experiment_id,
            repo_name=context.repository_name,
            resource_name=display_name,
            resource_type=resource.resource_type,
            provider=_rtdb.get_provider_key(_get_db(), resource.resource_type),
            source_file=resource.file_path,
            source_line=resource.line_number,
            properties=getattr(resource, 'properties', None),
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
    
    # Third pass: detect and register shared/external resources
    try:
        from shared_resource_helpers import register_shared_resource, link_repo_to_shared_resource
        
        shared_count = 0
        for resource in context.resources:
            props = getattr(resource, 'properties', {}) or {}
            shared_ref_type = props.get("shared_resource_reference")
            shared_ref_id = props.get("shared_resource_identifier")
            
            if shared_ref_type and shared_ref_id:
                shared_count += 1
                provider = _rtdb.get_provider_key(_get_db(), resource.resource_type)
                category = _rtdb.get_resource_type(_get_db(), shared_ref_type).get("category", "Unknown")
                
                # Register the shared resource
                shared_id = register_shared_resource(
                    resource_type=shared_ref_type,
                    resource_identifier=shared_ref_id,
                    provider=provider,
                    friendly_name=_rtdb.get_friendly_name(_get_db(), shared_ref_type),
                    category=category,
                    discovered_from_repo=context.repository_name,
                    variable_name=shared_ref_id.replace("var.", "").replace("data.", ""),
                )
                
                # Link this repo to the shared resource
                local_id = res_db_ids.get(f"{resource.resource_type}.{resource.name}")
                if local_id:
                    link_repo_to_shared_resource(
                        shared_id, 
                        context.repository_name, 
                        experiment_id, 
                        local_id,
                        "uses_parent"
                    )
        if shared_count > 0:
            print(f"✓ Registered {shared_count} shared resource references")
    except ImportError as ie:
        print(f"⚠️  Import failed: {ie}")  # shared_resource_helpers not available
    except Exception as e:
        print(f"⚠️  Shared resource registration failed: {e}")  # Non-fatal, continue with scan

    for connection in context.connections:
        insert_connection(
            experiment_id=experiment_id,
            source_name=connection.source,
            target_name=connection.target,
            connection_type=connection.connection_type,
        )

    # Persist typed relationships as concrete connections for DB-first queries/diagrams.
    def _provider_prefix(resource_type: str) -> str:
        """Return the cloud provider prefix for a resource type (e.g. 'azurerm', 'aws', 'google')."""
        rt = (resource_type or "").lower()
        for pfx in ("azurerm_", "azuread_", "aws_", "google_", "alicloud_", "oci_", "kubernetes_", "helm_"):
            if rt.startswith(pfx):
                return pfx.rstrip("_")
        return ""

    for rel in getattr(context, "relationships", []) or []:
        src_name = str(getattr(rel, "source_name", "") or "").strip()
        tgt_name = str(getattr(rel, "target_name", "") or "").strip()
        src_type = str(getattr(rel, "source_type", "") or "")
        tgt_type = str(getattr(rel, "target_type", "") or "")
        if not src_name or not tgt_name:
            continue
        if tgt_type == "unknown":
            # Ambiguous refs are tracked in enrichment_queue, not resource_connections.
            continue

        # Skip self-connections (resource referencing itself).
        if src_name == tgt_name and src_type == tgt_type:
            continue

        # Skip cross-provider 'contains' edges — they are parse artefacts, not real topology.
        rel_type = _relationship_kind_value(rel)
        if rel_type == "contains":
            src_pfx = _provider_prefix(src_type)
            tgt_pfx = _provider_prefix(tgt_type)
            if src_pfx and tgt_pfx and src_pfx != tgt_pfx:
                continue
        relation_notes = str(getattr(rel, "notes", "") or "")
        inferred_auth = "inferred" if rel_type == "authenticates_via" else None
        inferred_authorization = "rbac" if rel_type == "grants_access_to" else None
        inferred_encryption = True if rel_type == "encrypts" else None
        inferred_via_component = src_name if (
            rel_type == "routes_ingress_to" and _is_edge_gateway_signal(src_type, src_name)
        ) else None
        inferred_protocol, inferred_port = _infer_protocol_port(tgt_type, tgt_name)

        insert_connection(
            experiment_id=experiment_id,
            source_name=src_name,
            target_name=tgt_name,
            connection_type=rel_type,
            protocol=inferred_protocol,
            port=inferred_port,
            authentication=inferred_auth,
            source_repo=context.repository_name,
            target_repo=context.repository_name,
            authorization=inferred_authorization,
            auth_method=inferred_auth,
            is_encrypted=inferred_encryption,
            via_component=inferred_via_component,
            notes=relation_notes or None,
        )
