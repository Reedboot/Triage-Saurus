#!/usr/bin/env python3
"""resource_type_db.py — Single source of truth for resource type lookups.

Query order:
  1. resource_types table in DB (populated by init_database.py seed + auto-insert)
  2. In-memory fallback dict  (mirrors seed data; works before DB is initialised)
  3. Derive from type string  (prefix-strip + title-case; unknown types auto-inserted)

All scripts should import from here instead of maintaining their own dicts.
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage.db"

# ---------------------------------------------------------------------------
# Seed / fallback data  (kept in sync with init_database.py seed rows)
# New entries here should also be added to _SEED_ROWS in init_database.py
# ---------------------------------------------------------------------------
_FALLBACK: dict[str, dict] = {
    # Azure — Identity
    "azurerm_key_vault":                          {"friendly_name": "Key Vault",                "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": True},
    "azurerm_key_vault_key":                      {"friendly_name": "Key Vault",                "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "azurerm_key_vault_secret":                   {"friendly_name": "Key Vault",                "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "azurerm_user_assigned_identity":             {"friendly_name": "Managed Identity",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": True},
    # Azure — RBAC / Policy (don't display as architecture nodes)
    "azurerm_role_definition":                    {"friendly_name": "Role Definition",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azurerm_role_assignment":                    {"friendly_name": "Role Assignment",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azurerm_policy_definition":                  {"friendly_name": "Policy Definition",       "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    "azurerm_policy_assignment":                  {"friendly_name": "Policy Assignment",       "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    "azurerm_policy_set_definition":              {"friendly_name": "Policy Set",              "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    # Azure — Database
    "azurerm_mssql_server":                       {"friendly_name": "SQL Server",               "category": "Database",    "icon": "🗃️"},
    "azurerm_sql_server":                         {"friendly_name": "SQL Server",               "category": "Database",    "icon": "🗃️"},
    "azurerm_mssql_database":                     {"friendly_name": "SQL Database",             "category": "Database",    "icon": "🗃️"},
    "azurerm_mssql_server_security_alert_policy": {"friendly_name": "SQL Alert Policy",          "category": "Security",    "icon": "🚨"},
    "azurerm_mysql_server":                       {"friendly_name": "MySQL Server",             "category": "Database",    "icon": "🗃️"},
    "azurerm_postgresql_server":                  {"friendly_name": "PostgreSQL Server",        "category": "Database",    "icon": "🗃️"},
    "azurerm_postgresql_configuration":           {"friendly_name": "PostgreSQL Server",        "category": "Database",    "icon": "🗃️"},
    "azurerm_cosmosdb_account":                   {"friendly_name": "Cosmos DB",                "category": "Database",    "icon": "🗃️"},
    # Azure — Storage
    "azurerm_storage_account":                    {"friendly_name": "Storage Account",          "category": "Storage",     "icon": "🗄️"},
    "azurerm_storage_account_network_rules":      {"friendly_name": "Storage Account",          "category": "Storage",     "icon": "🗄️"},
    "azurerm_storage_container":                  {"friendly_name": "Storage Container",        "category": "Storage",     "icon": "🗄️"},
    "azurerm_storage_blob":                       {"friendly_name": "Storage Blob",             "category": "Storage",     "icon": "🗄️"},
    # Azure — Auth/Credentials (classified as Identity; excluded from diagram via _is_data_routing_resource)
    "azurerm_storage_account_sas":                {"friendly_name": "Storage Account SAS",      "category": "Identity",    "icon": "🔑"},
    # Azure — Database governance config (excluded from diagram via _is_data_routing_resource)
    "azurerm_mssql_database_extended_auditing_policy":        {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_extended_auditing_policy":          {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_microsoft_support_auditing_policy": {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_transparent_data_encryption":       {"friendly_name": "SQL Transparent Encryption", "category": "",   "icon": "📋"},
    "azurerm_mssql_virtual_network_rule":                     {"friendly_name": "SQL VNet Rule",              "category": "Security", "icon": "🛡️"},
    # Azure — VM extensions (agents on VMs; excluded from diagram via _is_data_routing_resource)
    "azurerm_virtual_machine_extension":                      {"friendly_name": "VM Extension",               "category": "",   "icon": "🔧"},
    "azurerm_linux_virtual_machine_extension":                {"friendly_name": "VM Extension",               "category": "",   "icon": "🔧"},
    "azurerm_windows_virtual_machine_extension":              {"friendly_name": "VM Extension",               "category": "",   "icon": "🔧"},
    # Azure — Compute
    "azurerm_linux_virtual_machine":              {"friendly_name": "Linux VM",                 "category": "Compute",     "icon": "🖥️"},
    "azurerm_windows_virtual_machine":            {"friendly_name": "Windows VM",               "category": "Compute",     "icon": "🖥️"},
    "azurerm_app_service":                        {"friendly_name": "App Service",              "category": "Compute",     "icon": "🌐"},
    "azurerm_linux_function_app":                 {"friendly_name": "Function App",             "category": "Compute",     "icon": "⚡"},
    "azurerm_windows_function_app":               {"friendly_name": "Function App",             "category": "Compute",     "icon": "⚡"},
    # Azure — Container
    "azurerm_kubernetes_cluster":                 {"friendly_name": "AKS Cluster",              "category": "Container",   "icon": "☸️"},
    "azurerm_container_registry":                 {"friendly_name": "Container Registry",       "category": "Container",   "icon": "📦"},
    "azurerm_container_group":                    {"friendly_name": "Container Instance",       "category": "Container",   "icon": "📦"},
    # Azure — Network & Gateways
    "azurerm_application_gateway":                {"friendly_name": "Application Gateway",      "category": "Network",     "icon": "🌐"},
    "azurerm_api_management":                     {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_api":                 {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_api_operation":       {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_api_policy":          {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_product":             {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_product_api":         {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_subscription":        {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_backend":             {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_api_management_named_value":         {"friendly_name": "API Management",           "category": "Network",     "icon": "🔌"},
    "azurerm_virtual_network":                    {"friendly_name": "Virtual Network",          "category": "Network",     "icon": "🔷"},
    "azurerm_subnet":                             {"friendly_name": "Subnet",                   "category": "Network",     "icon": "🔷"},
    "azurerm_network_interface":                  {"friendly_name": "Network Interface",        "category": "Network",     "icon": "🔷"},
    "azurerm_public_ip":                          {"friendly_name": "Public IP",                "category": "Network",     "icon": "🌍"},
    "azurerm_private_endpoint":                   {"friendly_name": "Private Endpoint",         "category": "Network",     "icon": "🔒"},
    # Azure — Security
    "azurerm_network_security_group":             {"friendly_name": "Network Security Group",   "category": "Security",    "icon": "🛡️"},
    "azurerm_firewall":                           {"friendly_name": "Azure Firewall",           "category": "Security",    "icon": "🛡️"},
    "azurerm_web_application_firewall_policy":    {"friendly_name": "WAF Policy",               "category": "Security",    "icon": "🛡️"},
    # Azure — Monitoring
    "azurerm_application_insights":              {"friendly_name": "Application Insights",     "category": "Monitoring",  "icon": "📊"},
    "azurerm_monitor_diagnostic_setting":         {"friendly_name": "Diagnostic Settings",      "category": "Monitoring",  "icon": "📊"},
    "azurerm_log_analytics_workspace":            {"friendly_name": "Log Analytics Workspace",  "category": "Monitoring",  "icon": "📊"},
    "azurerm_monitor_action_group":               {"friendly_name": "Monitor Action Group",     "category": "Monitoring",  "icon": "🔔"},
    "azurerm_monitor_metric_alert":               {"friendly_name": "Metric Alert",             "category": "Monitoring",  "icon": "🔔"},
    "azurerm_monitor_scheduled_query_rules_alert":{"friendly_name": "Query Alert",              "category": "Monitoring",  "icon": "🔔"},
    # AWS — Storage
    "aws_s3_bucket":                              {"friendly_name": "S3 Bucket",                "category": "Storage",     "icon": "🗄️"},
    "aws_s3_bucket_object":                       {"friendly_name": "S3 Bucket",                "category": "Storage",     "icon": "🗄️"},
    "aws_s3_bucket_public_access_block":          {"friendly_name": "Public Access Block",      "category": "Storage",     "icon": "🔒", "display_on_architecture_chart": False},
    "aws_ebs_volume":                             {"friendly_name": "EBS Volume",               "category": "Storage",     "icon": "💾"},
    # AWS — Database
    "aws_rds_cluster":                            {"friendly_name": "RDS Cluster",              "category": "Database",    "icon": "🗃️"},
    "aws_db_instance":                            {"friendly_name": "RDS Instance",             "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster":                        {"friendly_name": "Neptune Cluster",          "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster_instance":               {"friendly_name": "Neptune Instance",         "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster_snapshot":               {"friendly_name": "Neptune Snapshot",         "category": "Database",    "icon": "🗃️"},
    "aws_elasticsearch_domain":                   {"friendly_name": "OpenSearch Domain",        "category": "Database",    "icon": "🔍"},
    "aws_elasticsearch_domain_policy":            {"friendly_name": "OpenSearch Domain",        "category": "Database",    "icon": "🔍"},
    "aws_dynamodb_table":                         {"friendly_name": "DynamoDB Table",           "category": "Database",    "icon": "🗃️"},
    # AWS — Compute
    "aws_instance":                               {"friendly_name": "EC2 Instance",             "category": "Compute",     "icon": "🖥️"},
    "aws_lambda_function":                        {"friendly_name": "Lambda Function",          "category": "Compute",     "icon": "⚡"},
    "aws_eks_cluster":                            {"friendly_name": "EKS Cluster",              "category": "Container",   "icon": "☸️"},
    "aws_eks_addon":                              {"friendly_name": "EKS Addon",                "category": "Container",   "icon": "☸️"},
    "aws_ecs_cluster":                            {"friendly_name": "ECS Cluster",              "category": "Container",   "icon": "☸️"},
    "aws_ecs_service":                            {"friendly_name": "ECS Service",              "category": "Container",   "icon": "☸️"},
    "helm_release":                               {"friendly_name": "Helm Release",             "category": "Container",   "icon": "⎈"},
    # AWS — Network & API Gateway
    "aws_api_gateway_rest_api":                   {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_api_gateway_resource":                   {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_api_gateway_method":                     {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_api_gateway_integration":                {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_api_gateway_deployment":                 {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_api_gateway_stage":                      {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "aws_apigatewayv2_api":                       {"friendly_name": "API Gateway v2",           "category": "Network",     "icon": "🔌"},
    "aws_apigatewayv2_integration":               {"friendly_name": "API Gateway v2",           "category": "Network",     "icon": "🔌"},
    "aws_apigatewayv2_route":                     {"friendly_name": "API Gateway v2",           "category": "Network",     "icon": "🔌"},
    "aws_apigatewayv2_stage":                     {"friendly_name": "API Gateway v2",           "category": "Network",     "icon": "🔌"},
    "aws_elb":                                    {"friendly_name": "Load Balancer",            "category": "Network",     "icon": "🌐"},
    "aws_alb":                                    {"friendly_name": "App Load Balancer",        "category": "Network",     "icon": "🌐"},
    "aws_lb":                                     {"friendly_name": "Network Load Balancer",    "category": "Network",     "icon": "🌐"},
    # AWS — Load Balancer components (listeners/target groups shown nested inside LB)
    "aws_lb_listener":                             {"friendly_name": "Load Balancer Listener",  "category": "Network",     "icon": "🎧", "display_on_architecture_chart": True},
    "aws_alb_listener":                            {"friendly_name": "Load Balancer Listener",  "category": "Network",     "icon": "🎧", "display_on_architecture_chart": True},
    "aws_lb_target_group":                         {"friendly_name": "Target Group",            "category": "Network",     "icon": "🎯", "display_on_architecture_chart": True},
    "aws_alb_target_group":                        {"friendly_name": "Target Group",            "category": "Network",     "icon": "🎯", "display_on_architecture_chart": True},
    "aws_lb_target_group_attachment":              {"friendly_name": "Target Attachment",       "category": "Network",     "icon": "🔗", "display_on_architecture_chart": False},

    "aws_vpc":                                    {"friendly_name": "VPC",                      "category": "Network",     "icon": "🔷"},
    "aws_subnet":                                 {"friendly_name": "Subnet",                   "category": "Network",     "icon": "🔷"},
    "aws_security_group":                         {"friendly_name": "Security Group",           "category": "Security",    "icon": "🛡️"},
    "aws_security_group_rule":                    {"friendly_name": "Security Group Rule",      "category": "Security",    "icon": "🛡️"},
    "aws_internet_gateway":                       {"friendly_name": "Internet Gateway",         "category": "Network",     "icon": "🌍"},
    # AWS — Identity
    "aws_iam_role":                               {"friendly_name": "IAM Role",                 "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_policy":                             {"friendly_name": "IAM Policy",               "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_user":                               {"friendly_name": "IAM User",                 "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_instance_profile":                   {"friendly_name": "IAM Instance Profile",     "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_kms_key":                                {"friendly_name": "KMS Key",                  "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "aws_kms_alias":                              {"friendly_name": "KMS Key Alias",            "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    # GCP — Storage
    "google_storage_bucket":                      {"friendly_name": "GCS Bucket",               "category": "Storage",     "icon": "🗄️"},
    "google_storage_bucket_iam_binding":          {"friendly_name": "GCS Bucket",               "category": "Storage",     "icon": "🗄️"},
    # GCP — Database
    "google_sql_database_instance":               {"friendly_name": "Cloud SQL Instance",       "category": "Database",    "icon": "🗃️"},
    "google_bigquery_dataset":                    {"friendly_name": "BigQuery Dataset",         "category": "Database",    "icon": "🗃️"},
    "google_bigtable_instance":                   {"friendly_name": "Bigtable Instance",        "category": "Database",    "icon": "🗃️"},
    # GCP — Compute
    "google_compute_instance":                    {"friendly_name": "Compute Instance",         "category": "Compute",     "icon": "🖥️"},
    "google_cloudfunctions_function":             {"friendly_name": "Cloud Function",           "category": "Compute",     "icon": "⚡"},
    # GCP — Container
    "google_container_cluster":                   {"friendly_name": "GKE Cluster",              "category": "Container",   "icon": "☸️"},
    "google_container_node_pool":                 {"friendly_name": "GKE Node Pool",            "category": "Container",   "icon": "☸️"},
    # GCP — Network & API Gateway
    "google_api_gateway_api":                     {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "google_api_gateway_api_config":              {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "google_api_gateway_gateway":                 {"friendly_name": "API Gateway",              "category": "Network",     "icon": "🔌"},
    "google_cloud_run_service":                   {"friendly_name": "Cloud Run Service",        "category": "Compute",     "icon": "🌐"},
    "google_compute_url_map":                     {"friendly_name": "Load Balancer",            "category": "Network",     "icon": "⚖️"},
    "google_compute_network":                     {"friendly_name": "VPC Network",              "category": "Network",     "icon": "🔷"},
    "google_compute_subnetwork":                  {"friendly_name": "Subnetwork",               "category": "Network",     "icon": "🔷"},
    "google_compute_firewall":                    {"friendly_name": "Firewall Rule",            "category": "Security",    "icon": "🛡️"},
    # GCP — Identity
    "google_project_iam_binding":                 {"friendly_name": "IAM Binding",              "category": "Identity",    "icon": "👤"},
    "google_kms_crypto_key":                      {"friendly_name": "KMS Crypto Key",           "category": "Identity",    "icon": "🔑"},
    "google_service_account":                     {"friendly_name": "Service Account",          "category": "Identity",    "icon": "👤"},
}

_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("azurerm_", "azure"),
    ("aws_",     "aws"),
    ("google_",  "gcp"),
    ("alicloud_","alicloud"),
    ("oci_",     "oracle"),
]

# Category keyword patterns for derive fallback — ordered by priority
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("kubernetes", "Container"), ("aks", "Container"), ("eks", "Container"),
    ("gke", "Container"), ("container", "Container"), ("ecs", "Container"),
    ("sql", "Database"), ("database", "Database"), ("_db", "Database"),
    ("rds", "Database"), ("mysql", "Database"), ("postgresql", "Database"),
    ("postgres", "Database"), ("mssql", "Database"), ("cosmosdb", "Database"),
    ("bigquery", "Database"), ("bigtable", "Database"), ("dynamo", "Database"),
    ("neptune", "Database"), ("elasticsearch", "Database"),
    ("storage", "Storage"), ("bucket", "Storage"), ("blob", "Storage"),
    ("s3", "Storage"), ("ebs", "Storage"), ("disk", "Storage"),
    ("virtual_machine", "Compute"), ("instance", "Compute"), ("lambda", "Compute"),
    ("function", "Compute"), ("app_service", "Compute"), ("cloud_run", "Compute"),
    ("key_vault", "Identity"), ("kms", "Identity"), ("iam", "Identity"),
    ("identity", "Identity"), ("service_account", "Identity"),
    ("firewall", "Security"), ("security_group", "Security"), ("waf", "Security"),
    ("nsg", "Security"), ("sentinel", "Security"),
    ("network", "Network"), ("subnet", "Network"), ("vpc", "Network"),
    ("vnet", "Network"), ("gateway", "Network"), ("load_balancer", "Network"),
    ("endpoint", "Network"), ("public_ip", "Network"),
    ("monitor", "Monitoring"), ("log", "Monitoring"), ("diagnostic", "Monitoring"),
    ("insights", "Monitoring"), ("cloudwatch", "Monitoring"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_resource_type(conn: sqlite3.Connection | None, terraform_type: str) -> dict:
    """Return lookup data for a terraform resource type.

    Returns dict with keys:
        friendly_name, category, icon, provider,
        is_data_store, is_internet_facing_capable

    conn may be None; in that case the DB query is skipped and _FALLBACK / _derive() are used.
    """
    # 1. Query DB
    if conn is None:
        # No DB — derive provider then fall back to _FALLBACK / _derive()
        provider = "unknown"
        for prefix, prov in _PROVIDER_PREFIXES:
            if terraform_type.startswith(prefix):
                provider = prov
                break
        if terraform_type in _FALLBACK:
            return {**_FALLBACK[terraform_type], "provider": provider, "is_data_store": False, "is_internet_facing_capable": False}
        return {**_derive(terraform_type), "is_data_store": False, "is_internet_facing_capable": False}
    try:
        row = conn.execute(
            """
            SELECT rt.friendly_name, rt.category, rt.icon,
                   p.key AS provider,
                   rt.is_data_store, rt.is_internet_facing_capable
            FROM resource_types rt
            LEFT JOIN providers p ON rt.provider_id = p.id
            WHERE rt.terraform_type = ?
            """,
            (terraform_type,),
        ).fetchone()
        if row:
            return {
                "friendly_name": row[0],
                "category":      row[1],
                "icon":          row[2] or "📦",
                "provider":      row[3] or "unknown",
                "is_data_store":               bool(row[4]),
                "is_internet_facing_capable":  bool(row[5]),
            }
    except sqlite3.OperationalError:
        pass  # Tables may not exist yet; fall through

    # 2. In-memory fallback
    if terraform_type in _FALLBACK:
        provider = "unknown"
        for prefix, prov in _PROVIDER_PREFIXES:
            if terraform_type.startswith(prefix):
                provider = prov
                break
        entry = {**_FALLBACK[terraform_type], "provider": provider,
                 "is_data_store": False, "is_internet_facing_capable": False}
        _auto_insert(conn, terraform_type, entry)
        return entry

    # 3. Derive from type string and auto-insert for future calls
    derived = {**_derive(terraform_type), "is_data_store": False, "is_internet_facing_capable": False}
    _auto_insert(conn, terraform_type, derived)
    return derived


def get_friendly_name(conn: sqlite3.Connection | None, terraform_type: str) -> str:
    return get_resource_type(conn, terraform_type)["friendly_name"]


def get_category(conn: sqlite3.Connection | None, terraform_type: str) -> str:
    return get_resource_type(conn, terraform_type)["category"]


def get_provider_key(conn: sqlite3.Connection | None, terraform_type: str) -> str:
    return get_resource_type(conn, terraform_type)["provider"]


def get_display_label(conn: sqlite3.Connection | None, resource_name: str, terraform_type: str) -> str:
    """Return diagram label: 'resource_name (Friendly Type)'."""
    return f"{resource_name} ({get_friendly_name(conn, terraform_type)})"


# ---------------------------------------------------------------------------
# Canonical type preferences — for diagram de-duplication.
# When multiple related terraform types map to the same logical service,
# prefer the canonical (primary resource) type for display.
# Each entry: canonical_type -> [other types in the same family]
# ---------------------------------------------------------------------------
_PREFERRED_TYPES: dict[str, list[str]] = {
    "azurerm_key_vault":          ["azurerm_key_vault_key", "azurerm_key_vault_secret"],
    "azurerm_mssql_server":       ["azurerm_sql_server"],
    "aws_rds_cluster":            ["aws_db_instance"],
    "aws_neptune_cluster":        ["aws_neptune_cluster_instance", "aws_neptune_cluster_snapshot"],
    "aws_elasticsearch_domain":   ["aws_elasticsearch_domain_policy"],
    "aws_s3_bucket":              ["aws_s3_bucket_object"],
    "google_container_cluster":   ["google_container_node_pool"],
    "google_storage_bucket":      ["google_storage_bucket_iam_binding"],
}


def filter_to_canonical(types: list[str]) -> list[str]:
    """Return just the canonical type from a list, if one is present.

    If a canonical type (key in _PREFERRED_TYPES) appears in the list, all
    other types that share the same family are removed.  If no canonical type
    is recognised the original list is returned unchanged.
    """
    for canonical, aliases in _PREFERRED_TYPES.items():
        if canonical in types:
            family = {canonical, *aliases}
            filtered = [t for t in types if t == canonical or t not in family]
            return filtered if filtered else types
    return types


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _derive(terraform_type: str) -> dict:
    """Derive friendly_name, category, icon, provider from a raw type string."""
    provider = "unknown"
    cleaned = terraform_type
    for prefix, prov in _PROVIDER_PREFIXES:
        if terraform_type.startswith(prefix):
            provider = prov
            cleaned = terraform_type[len(prefix):]
            break

    friendly = cleaned.replace("_", " ").title()

    category = "Other"
    lower = terraform_type.lower()
    for keyword, cat in _CATEGORY_KEYWORDS:
        if keyword in lower:
            category = cat
            break

    return {"friendly_name": friendly, "category": category, "icon": "📦", "provider": provider}


def _auto_insert(conn: sqlite3.Connection, terraform_type: str, entry: dict) -> None:
    """Insert an unknown type so it only needs deriving once."""
    try:
        row = conn.execute("SELECT id FROM providers WHERE key = ?", (entry["provider"],)).fetchone()
        pid = row[0] if row else None
        conn.execute(
            """INSERT OR IGNORE INTO resource_types
               (provider_id, terraform_type, friendly_name, category, icon)
               VALUES (?, ?, ?, ?, ?)""",
            (pid, terraform_type, entry["friendly_name"], entry["category"], entry["icon"]),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


if __name__ == "__main__":
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    for t in ["azurerm_key_vault", "aws_s3_bucket", "google_container_cluster", "azurerm_quantum_widget"]:
        print(get_display_label(conn, "my-resource", t), "|", get_category(conn, t))
    conn.close()
