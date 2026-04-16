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

ROOT = Path(__file__).resolve().parents[2]
COZO_DB = ROOT / "Output/Data/cozo.db"
DB_PATH = COZO_DB

# ---------------------------------------------------------------------------
# Seed / fallback data — init_database.py reads this dict directly at startup.
# Adding entries here is sufficient; no separate _SEED_ROWS list exists.
# ---------------------------------------------------------------------------
_FALLBACK: dict[str, dict] = {
    # Azure — Identity
    "azurerm_key_vault":                          {"friendly_name": "Key Vault",                "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": True},
    "azurerm_key_vault_key":                      {"friendly_name": "Key Vault Key",            "category": "Security",    "icon": "🔑", "display_on_architecture_chart": False, "parent_type": "azurerm_key_vault"},
    "azurerm_key_vault_secret":                   {"friendly_name": "Key Vault Secret",         "category": "Security",    "icon": "🤫", "display_on_architecture_chart": False, "parent_type": "azurerm_key_vault"},
    "azurerm_key_vault_certificate":              {"friendly_name": "Key Vault Certificate",    "category": "Security",    "icon": "📜", "display_on_architecture_chart": False, "parent_type": "azurerm_key_vault"},
    "azurerm_user_assigned_identity":             {"friendly_name": "Managed Identity",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": True},
    # Azure — RBAC / Policy (don't display as architecture nodes)
    "azurerm_role_definition":                    {"friendly_name": "Role Definition",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azurerm_role_assignment":                    {"friendly_name": "Role Assignment",         "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azurerm_policy_definition":                  {"friendly_name": "Policy Definition",       "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    "azurerm_policy_assignment":                  {"friendly_name": "Policy Assignment",       "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    "azurerm_policy_set_definition":              {"friendly_name": "Policy Set",              "category": "Identity",    "icon": "📜", "display_on_architecture_chart": False},
    # Azure — Identity (Azure AD)
    "azuread_application":                        {"friendly_name": "Azure AD Application",                 "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azuread_application_password":               {"friendly_name": "Azure AD Application Password",        "category": "Identity",    "icon": "🔐", "display_on_architecture_chart": False},
    "azuread_directory_role":                     {"friendly_name": "Azure AD Directory Role",              "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azuread_directory_role_assignment":          {"friendly_name": "Azure AD Directory Role Assignment",   "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azuread_domains":                            {"friendly_name": "Azure AD Domain",                      "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azuread_group":                              {"friendly_name": "Azure AD Group",                       "category": "Identity",    "icon": "👥", "display_on_architecture_chart": False},
    "azuread_group_member":                       {"friendly_name": "Azure AD Group Member",                "category": "Identity",    "icon": "👥", "display_on_architecture_chart": False},
    "azuread_service_principal":                  {"friendly_name": "Azure AD Service Principal",           "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azuread_service_principal_password":         {"friendly_name": "Azure AD Service Principal Password",  "category": "Identity",    "icon": "🔐", "display_on_architecture_chart": False},
    "azuread_user":                               {"friendly_name": "Azure AD User",                        "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "azurerm_ssh_public_key":                     {"friendly_name": "SSH Public Key",                       "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False, "parent_type": "azurerm_linux_virtual_machine"},
    # Azure — Database
    "azurerm_mssql_server":                       {"friendly_name": "SQL Server",               "category": "Database",    "icon": "🗃️"},
    "azurerm_sql_server":                         {"friendly_name": "SQL Server",               "category": "Database",    "icon": "🗃️"},
    "azurerm_mssql_database":                     {"friendly_name": "SQL Database",             "category": "Database",    "icon": "🗃️", "display_on_architecture_chart": False, "parent_type": "azurerm_mssql_server"},
    "azurerm_mssql_firewall_rule":                {"friendly_name": "SQL Firewall Rule",         "category": "Database",    "icon": "🔥", "display_on_architecture_chart": False, "parent_type": "azurerm_mssql_server"},
    "azurerm_mssql_server_security_alert_policy": {"friendly_name": "SQL Alert Policy",          "category": "Security",    "icon": "🚨"},
    "azurerm_mysql_server":                       {"friendly_name": "MySQL Server",             "category": "Database",    "icon": "🗃️"},
    "azurerm_mysql_database":                     {"friendly_name": "MySQL Database",           "category": "Database",    "icon": "🗃️", "display_on_architecture_chart": False, "parent_type": "azurerm_mysql_server"},
    "azurerm_mysql_flexible_database":            {"friendly_name": "MySQL Flexible Database",  "category": "Database",    "icon": "🗃️", "display_on_architecture_chart": False, "parent_type": "azurerm_mysql_flexible_server"},
    "azurerm_postgresql_server":                  {"friendly_name": "PostgreSQL Server",        "category": "Database",    "icon": "🗃️"},
    "azurerm_postgresql_database":                {"friendly_name": "PostgreSQL Database",      "category": "Database",    "icon": "🗃️", "display_on_architecture_chart": False, "parent_type": "azurerm_postgresql_server"},
    "azurerm_postgresql_configuration":           {"friendly_name": "PostgreSQL Server",        "category": "Database",    "icon": "🗃️"},
    "azurerm_cosmosdb_account":                   {"friendly_name": "Cosmos DB",                "category": "Database",    "icon": "🗃️"},
    "azurerm_cosmosdb_sql_database":              {"friendly_name": "CosmosDB SQL Database",    "category": "Database",    "icon": "🗃️", "display_on_architecture_chart": False, "parent_type": "azurerm_cosmosdb_account"},
    "azurerm_cosmosdb_sql_container":             {"friendly_name": "CosmosDB SQL Container",   "category": "Database",    "icon": "📦", "display_on_architecture_chart": False, "parent_type": "azurerm_cosmosdb_account"},
    # Azure — Storage
    "azurerm_storage_account":                    {"friendly_name": "Storage Account",          "category": "Storage",     "icon": "🗄️"},
    "azurerm_storage_account_network_rules":      {"friendly_name": "Storage Account",          "category": "Storage",     "icon": "🗄️"},
    "azurerm_storage_container":                  {"friendly_name": "Storage Container",        "category": "Storage",     "icon": "📦", "display_on_architecture_chart": False, "parent_type": "azurerm_storage_account"},
    "azurerm_storage_blob":                       {"friendly_name": "Storage Blob",             "category": "Storage",     "icon": "📄", "display_on_architecture_chart": False, "parent_type": "azurerm_storage_account"},
    "azurerm_storage_queue":                      {"friendly_name": "Storage Queue",            "category": "Storage",     "icon": "📋", "display_on_architecture_chart": False, "parent_type": "azurerm_storage_account"},
    "azurerm_storage_share":                      {"friendly_name": "Storage File Share",       "category": "Storage",     "icon": "📁", "display_on_architecture_chart": False, "parent_type": "azurerm_storage_account"},
    "azurerm_managed_disk":                       {"friendly_name": "Managed Disk",             "category": "Storage",     "icon": "💾"},
    # Azure — Auth/Credentials (classified as Identity; excluded from diagram via _is_data_routing_resource)
    "azurerm_storage_account_sas":                {"friendly_name": "Storage Account SAS",      "category": "Identity",    "icon": "🔑"},
    # Azure — Database governance config (excluded from diagram via _is_data_routing_resource)
    "azurerm_mssql_database_extended_auditing_policy":        {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_extended_auditing_policy":          {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_microsoft_support_auditing_policy": {"friendly_name": "SQL Auditing Policy",        "category": "",   "icon": "📋"},
    "azurerm_mssql_server_transparent_data_encryption":       {"friendly_name": "SQL Transparent Encryption", "category": "",   "icon": "📋"},
    "azurerm_mssql_virtual_network_rule":                     {"friendly_name": "SQL VNet Rule",              "category": "Security", "icon": "🛡️"},
    # Azure — VM extensions (agents on VMs; excluded from diagram via _is_data_routing_resource)
    "azurerm_virtual_machine_extension":                      {"friendly_name": "VM Extension",               "category": "Compute",    "icon": "🧩", "display_on_architecture_chart": False, "parent_type": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine"},
    "azurerm_linux_virtual_machine_extension":                {"friendly_name": "VM Extension",               "category": "",   "icon": "🔧", "display_on_architecture_chart": False, "parent_type": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine"},
    "azurerm_windows_virtual_machine_extension":              {"friendly_name": "VM Extension",               "category": "",   "icon": "🔧", "display_on_architecture_chart": False, "parent_type": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine"},
    # Azure — Compute
    "azurerm_linux_virtual_machine":              {"friendly_name": "Linux VM",                 "category": "Compute",     "icon": "🖥️"},
    "azurerm_windows_virtual_machine":            {"friendly_name": "Windows VM",               "category": "Compute",     "icon": "🖥️"},
    "azurerm_app_service":                        {"friendly_name": "App Service",              "category": "Compute",     "icon": "🌐", "parent_type": "azurerm_app_service_plan|azurerm_service_plan"},
    "azurerm_app_service_plan":                   {"friendly_name": "App Service Plan",         "category": "Compute",     "icon": "⚙️"},
    "azurerm_function_app":                       {"friendly_name": "Function App",             "category": "Compute",     "icon": "⚡", "parent_type": "azurerm_app_service_plan|azurerm_service_plan"},
    "azurerm_linux_function_app":                 {"friendly_name": "Function App",             "category": "Compute",     "icon": "⚡", "parent_type": "azurerm_app_service_plan|azurerm_service_plan"},
    "azurerm_windows_function_app":               {"friendly_name": "Function App",             "category": "Compute",     "icon": "⚡", "parent_type": "azurerm_app_service_plan|azurerm_service_plan"},
    "azurerm_linux_web_app":                      {"friendly_name": "Linux Web App",             "category": "Compute",     "icon": "🌐"},
    "azurerm_service_plan":                       {"friendly_name": "Service Plan",              "category": "Compute",     "icon": "⚙️"},
    "azurerm_automation_account":                 {"friendly_name": "Automation Account",       "category": "Other",       "icon": "⚙️"},
    "azurerm_automation_runbook":                 {"friendly_name": "Automation Runbook",       "category": "Other",       "icon": "📜", "display_on_architecture_chart": False, "parent_type": "azurerm_automation_account"},
    "azurerm_virtual_machine":                    {"friendly_name": "Virtual Machine",          "category": "Compute",     "icon": "🖥️"},
    # Azure — Container
    "azurerm_kubernetes_cluster":                 {"friendly_name": "AKS Cluster",              "category": "Container",   "icon": "☸️"},
    "azurerm_kubernetes_cluster_node_pool":       {"friendly_name": "AKS Node Pool",            "category": "Container",   "icon": "🏊", "display_on_architecture_chart": False, "parent_type": "azurerm_kubernetes_cluster"},
    "azurerm_container_registry":                 {"friendly_name": "Container Registry",       "category": "Container",   "icon": "📦"},
    "azurerm_container_group":                    {"friendly_name": "Container Instance",       "category": "Container",   "icon": "📦"},
    # Azure — Network & Gateways
    "azurerm_application_gateway":                {"friendly_name": "Application Gateway",      "category": "Network",     "icon": "🌐"},
    "azurerm_application_gateway_backend_pool":   {"friendly_name": "App Gateway Backend Pool", "category": "Network",     "icon": "🎯", "display_on_architecture_chart": False, "parent_type": "azurerm_application_gateway"},
    "azurerm_lb":                                 {"friendly_name": "Load Balancer",            "category": "Network",     "icon": "🌐"},
    "azurerm_lb_rule":                            {"friendly_name": "Load Balancer Rule",       "category": "Network",     "icon": "⚖️", "display_on_architecture_chart": False, "parent_type": "azurerm_lb"},
    "azurerm_lb_probe":                           {"friendly_name": "Load Balancer Probe",      "category": "Network",     "icon": "🔍", "display_on_architecture_chart": False, "parent_type": "azurerm_lb"},
    "azurerm_api_management":                     {"friendly_name": "API Management",           "category": "API",         "icon": "🔌"},
    "azurerm_api_management_api":                 {"friendly_name": "APIM API",                 "category": "API",         "icon": "🔗", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management"},
    "azurerm_api_management_api_operation":       {"friendly_name": "API Operation",            "category": "API",         "icon": "🔌", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management_api"},
    "azurerm_api_management_api_policy":          {"friendly_name": "API Policy",               "category": "API",         "icon": "📜", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management_api"},
    "azurerm_api_management_product":             {"friendly_name": "APIM Product",             "category": "API",         "icon": "📦", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management"},
    "azurerm_api_management_product_api":         {"friendly_name": "Product API Link",         "category": "API",         "icon": "🔗", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management_product"},
    "azurerm_api_management_subscription":        {"friendly_name": "API Management Subscription", "category": "API",     "icon": "🔑", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management"},
    "azurerm_api_management_backend":             {"friendly_name": "APIM Backend",             "category": "API",         "icon": "🎯", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management"},
    "azurerm_api_management_named_value":         {"friendly_name": "APIM Named Value",         "category": "API",         "icon": "🔐", "display_on_architecture_chart": True,  "parent_type": "azurerm_api_management"},
    "azurerm_virtual_network":                    {"friendly_name": "Virtual Network",          "category": "Network",     "icon": "🔷"},
    "azurerm_subnet":                             {"friendly_name": "Subnet",                   "category": "Network",     "icon": "🕸️", "display_on_architecture_chart": False, "parent_type": "azurerm_virtual_network"},
    "azurerm_network_interface":                  {"friendly_name": "Network Interface",        "category": "Network",     "icon": "🔌", "display_on_architecture_chart": False, "parent_type": "azurerm_virtual_machine|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine"},
    "azurerm_public_ip":                          {"friendly_name": "Public IP",                "category": "Compute",     "icon": "🌐", "display_on_architecture_chart": True, "parent_type": "azurerm_linux_virtual_machine|azurerm_windows_virtual_machine|azurerm_virtual_machine|azurerm_lb"},
    "azurerm_private_endpoint":                   {"friendly_name": "Private Endpoint",         "category": "Network",     "icon": "🔒", "display_on_architecture_chart": False, "parent_type": None},
    "azurerm_network_interface_security_group_association":{"friendly_name": "NIC Security Group Association","category": "Security","icon": "🔗","display_on_architecture_chart": False},
    "azurerm_network_security_rule":              {"friendly_name": "NSG Rule",                 "category": "Security",    "icon": "🛡️", "display_on_architecture_chart": False, "parent_type": "azurerm_network_security_group"},
    "azurerm_network_watcher":                    {"friendly_name": "Network Watcher",          "category": "Monitoring",  "icon": "📡", "display_on_architecture_chart": False},
    "azurerm_network_watcher_flow_log":           {"friendly_name": "Network Watcher Flow Log", "category": "Monitoring",  "icon": "📡", "display_on_architecture_chart": False},
    "azurerm_resource_group":                     {"friendly_name": "Resource Group",           "category": "Group",     "icon": "📦", "display_on_architecture_chart": False},
    "azurerm_resources":                          {"friendly_name": "Resources",                "category": "Other",       "icon": "📦", "display_on_architecture_chart": False},
    # Terraform meta-resources (lifecycle helpers, not actual infrastructure)
    "terraform_data":                             {"friendly_name": "Terraform Data",           "category": "Other",       "icon": "⚙️", "display_on_architecture_chart": False},
    "null_resource":                              {"friendly_name": "Null Resource",            "category": "Other",       "icon": "⚙️", "display_on_architecture_chart": False},
    "random_id":                                  {"friendly_name": "Random ID",                "category": "Other",       "icon": "🎲", "display_on_architecture_chart": False},
    "random_string":                              {"friendly_name": "Random String",            "category": "Other",       "icon": "🎲", "display_on_architecture_chart": False},
    "random_password":                            {"friendly_name": "Random Password",          "category": "Other",       "icon": "🎲", "display_on_architecture_chart": False},
    "time_sleep":                                 {"friendly_name": "Time Sleep",               "category": "Other",       "icon": "⏱️", "display_on_architecture_chart": False},
    # Azure — Security
    "azurerm_network_security_group":             {"friendly_name": "Network Security Group",   "category": "Security",    "icon": "🛡️"},
    "azurerm_firewall":                           {"friendly_name": "Azure Firewall",           "category": "Security",    "icon": "🛡️"},
    "azurerm_web_application_firewall_policy":    {"friendly_name": "WAF Policy",               "category": "Security",    "icon": "🛡️"},
    "azurerm_security_center_contact":             {"friendly_name": "Security Center Contact",  "category": "Security",    "icon": "🛡️", "display_on_architecture_chart": False},
    "azurerm_security_center_subscription_pricing":{"friendly_name": "Security Center Pricing",  "category": "Security",    "icon": "🛡️", "display_on_architecture_chart": False},
    # Azure — Monitoring
    "azurerm_application_insights":              {"friendly_name": "Application Insights",     "category": "Monitoring",  "icon": "📊"},
    "azurerm_monitor_diagnostic_setting":         {"friendly_name": "Diagnostic Settings",      "category": "Monitoring",  "icon": "📊"},
    "azurerm_monitor_log_profile":                {"friendly_name": "Log Profile",              "category": "Monitoring",  "icon": "📊", "display_on_architecture_chart": False},
    "azurerm_log_analytics_workspace":            {"friendly_name": "Log Analytics Workspace",  "category": "Monitoring",  "icon": "📊"},
    "azurerm_monitor_action_group":               {"friendly_name": "Monitor Action Group",     "category": "Monitoring",  "icon": "🔔"},
    "azurerm_monitor_metric_alert":               {"friendly_name": "Metric Alert",             "category": "Monitoring",  "icon": "🔔"},
    "azurerm_monitor_scheduled_query_rules_alert":{"friendly_name": "Query Alert",              "category": "Monitoring",  "icon": "🔔"},
    # Azure — Messaging
    "azurerm_servicebus_namespace":               {"friendly_name": "Service Bus Namespace",    "category": "Messaging",   "icon": "📦"},
    "azurerm_servicebus_queue":                   {"friendly_name": "Service Bus Queue",        "category": "Messaging",   "icon": "📨", "display_on_architecture_chart": False, "parent_type": "azurerm_servicebus_namespace"},
    "azurerm_servicebus_topic":                   {"friendly_name": "Service Bus Topic",        "category": "Messaging",   "icon": "📢", "display_on_architecture_chart": False, "parent_type": "azurerm_servicebus_namespace"},
    "azurerm_servicebus_subscription":            {"friendly_name": "Service Bus Subscription", "category": "Messaging",   "icon": "📬", "display_on_architecture_chart": False, "parent_type": "azurerm_servicebus_topic"},
    "azurerm_eventhub_namespace":                 {"friendly_name": "Event Hub Namespace",      "category": "Messaging",   "icon": "📦"},
    "azurerm_eventhub":                           {"friendly_name": "Event Hub",                "category": "Messaging",   "icon": "📡", "display_on_architecture_chart": False, "parent_type": "azurerm_eventhub_namespace"},
    "azurerm_eventhub_consumer_group":            {"friendly_name": "Event Hub Consumer Group", "category": "Messaging",   "icon": "👥", "display_on_architecture_chart": False, "parent_type": "azurerm_eventhub"},
    "azurerm_eventgrid_topic":                    {"friendly_name": "Event Grid Topic",         "category": "Messaging",   "icon": "🌐"},
    "azurerm_eventgrid_event_subscription":       {"friendly_name": "Event Grid Subscription",  "category": "Messaging",   "icon": "📬", "display_on_architecture_chart": False, "parent_type": "azurerm_eventgrid_topic"},
    # Alibaba Cloud
    "alicloud_actiontrail_trail":                 {"friendly_name": "Actiontrail Trail",        "category": "Monitoring",  "icon": "📜"},
    "alicloud_instance":                          {"friendly_name": "ECS Instance",             "category": "Compute",     "icon": "🖥️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_cs_managed_kubernetes":             {"friendly_name": "ACK Cluster",              "category": "Container",   "icon": "☸️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_cs_kubernetes_node_pool":           {"friendly_name": "ACK Node Pool",            "category": "Container",   "icon": "🏊",  "display_on_architecture_chart": False, "parent_type": "alicloud_cs_managed_kubernetes"},
    "alicloud_oss_bucket":                        {"friendly_name": "OSS Bucket",               "category": "Storage",     "icon": "🪣",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_db_instance":                       {"friendly_name": "RDS Instance",             "category": "Database",    "icon": "🗄️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_kms_key":                           {"friendly_name": "KMS Key",                  "category": "Security",    "icon": "🔑",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_kms_secret":                        {"friendly_name": "KMS Secret",               "category": "Security",    "icon": "🤫",  "display_on_architecture_chart": False, "parent_type": "alicloud_kms_key"},
    "alicloud_vpc":                               {"friendly_name": "VPC",                      "category": "Network",     "icon": "🕸️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_vswitch":                           {"friendly_name": "VSwitch",                  "category": "Network",     "icon": "🔌",  "display_on_architecture_chart": False, "parent_type": "alicloud_vpc"},
    "alicloud_security_group":                    {"friendly_name": "Security Group",           "category": "Security",    "icon": "🛡️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_security_group_rule":               {"friendly_name": "Security Group Rule",      "category": "Security",    "icon": "📋",  "display_on_architecture_chart": False, "parent_type": "alicloud_security_group"},
    "alicloud_ram_role":                          {"friendly_name": "RAM Role",                 "category": "Identity",    "icon": "👤",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_ram_policy":                        {"friendly_name": "RAM Policy",               "category": "Identity",    "icon": "📜",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_ram_access_key":                    {"friendly_name": "RAM Access Key",           "category": "Identity",    "icon": "🔑",  "display_on_architecture_chart": False, "parent_type": None},
    "alicloud_api_gateway_api":                   {"friendly_name": "API Gateway",              "category": "API",         "icon": "🔌",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_api_gateway_app":                   {"friendly_name": "API Gateway App Key",      "category": "API",         "icon": "🔑",  "display_on_architecture_chart": False, "parent_type": "alicloud_api_gateway_api"},
    "alicloud_api_gateway_group":                 {"friendly_name": "API Gateway Group",        "category": "API",         "icon": "📦",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_log_project":                       {"friendly_name": "Log Service Project",      "category": "Logging",     "icon": "📊",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_log_store":                         {"friendly_name": "Log Store",                "category": "Logging",     "icon": "📝",  "display_on_architecture_chart": False, "parent_type": "alicloud_log_project"},
    "alicloud_slb_load_balancer":                 {"friendly_name": "SLB Load Balancer",        "category": "Network",     "icon": "⚖️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_alb_load_balancer":                 {"friendly_name": "ALB Load Balancer",        "category": "Network",     "icon": "⚖️",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_fc_function":                       {"friendly_name": "Function Compute",         "category": "Serverless",  "icon": "⚡",  "display_on_architecture_chart": True,  "parent_type": None},
    "alicloud_kvstore_instance":                  {"friendly_name": "ApsaraDB for Redis",       "category": "Cache",       "icon": "⚡",  "display_on_architecture_chart": True,  "parent_type": None},
    # --- Oracle Cloud Infrastructure ---
    "oci_core_instance":                          {"friendly_name": "OCI Compute Instance",     "category": "Compute",     "icon": "🖥️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_containerengine_cluster":                {"friendly_name": "OKE Cluster",              "category": "Container",   "icon": "☸️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_containerengine_node_pool":              {"friendly_name": "OKE Node Pool",            "category": "Container",   "icon": "🏊",  "display_on_architecture_chart": False, "parent_type": "oci_containerengine_cluster"},
    "oci_objectstorage_bucket":                   {"friendly_name": "OCI Object Storage Bucket","category": "Storage",     "icon": "🪣",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_database_autonomous_database":           {"friendly_name": "Autonomous Database",      "category": "Database",    "icon": "🗄️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_database_db_system":                     {"friendly_name": "OCI DB System",            "category": "Database",    "icon": "🗄️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_mysql_mysql_db_system":                  {"friendly_name": "OCI MySQL DB System",      "category": "Database",    "icon": "🗃️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_kms_vault":                              {"friendly_name": "OCI KMS Vault",            "category": "Security",    "icon": "🏦",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_kms_key":                                {"friendly_name": "OCI KMS Key",              "category": "Security",    "icon": "🔑",  "display_on_architecture_chart": False, "parent_type": "oci_kms_vault"},
    "oci_vault_secret":                           {"friendly_name": "OCI Vault Secret",         "category": "Security",    "icon": "🤫",  "display_on_architecture_chart": False, "parent_type": "oci_kms_vault"},
    "oci_core_vcn":                               {"friendly_name": "Virtual Cloud Network",    "category": "Network",     "icon": "🕸️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_core_subnet":                            {"friendly_name": "OCI Subnet",               "category": "Network",     "icon": "🔌",  "display_on_architecture_chart": False, "parent_type": "oci_core_vcn"},
    "oci_core_network_security_group":            {"friendly_name": "OCI Network Security Group","category": "Security",   "icon": "🛡️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_load_balancer_load_balancer":            {"friendly_name": "OCI Load Balancer",        "category": "Network",     "icon": "⚖️",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_network_load_balancer_network_load_balancer": {"friendly_name": "OCI Network Load Balancer", "category": "Network", "icon": "⚖️", "display_on_architecture_chart": True, "parent_type": None},
    "oci_functions_application":                  {"friendly_name": "OCI Functions Application","category": "Serverless",  "icon": "📦",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_functions_function":                     {"friendly_name": "OCI Function",             "category": "Serverless",  "icon": "⚡",  "display_on_architecture_chart": False, "parent_type": "oci_functions_application"},
    "oci_apigateway_gateway":                     {"friendly_name": "OCI API Gateway",          "category": "API",         "icon": "🔗",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_apigateway_deployment":                  {"friendly_name": "OCI API Deployment",       "category": "API",         "icon": "🚀",  "display_on_architecture_chart": False, "parent_type": "oci_apigateway_gateway"},
    "oci_identity_api_key":                       {"friendly_name": "OCI API Key",              "category": "Identity",    "icon": "🔑",  "display_on_architecture_chart": False, "parent_type": None},
    "oci_identity_auth_token":                    {"friendly_name": "OCI Auth Token",           "category": "Identity",    "icon": "🔑",  "display_on_architecture_chart": False, "parent_type": None},
    "oci_logging_log_group":                      {"friendly_name": "OCI Log Group",            "category": "Logging",     "icon": "📊",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_logging_log":                            {"friendly_name": "OCI Log",                  "category": "Logging",     "icon": "📝",  "display_on_architecture_chart": False, "parent_type": "oci_logging_log_group"},
    "oci_identity_policy":                        {"friendly_name": "OCI IAM Policy",           "category": "Identity",    "icon": "📜",  "display_on_architecture_chart": True,  "parent_type": None},
    "oci_artifacts_container_repository":         {"friendly_name": "OCI Container Registry",   "category": "Container",   "icon": "🐋",  "display_on_architecture_chart": True,  "parent_type": None},
    # AWS — Storage
    "aws_s3_bucket":                              {"friendly_name": "S3 Bucket",                "category": "Storage",     "icon": "🗄️"},
    "aws_s3_bucket_object":                       {"friendly_name": "S3 Bucket",                "category": "Storage",     "icon": "🗄️"},
    "aws_s3_bucket_acl":                          {"friendly_name": "S3 Bucket ACL",            "category": "Storage",     "icon": "🔒", "display_on_architecture_chart": False, "parent_type": "aws_s3_bucket"},
    "aws_s3_bucket_ownership_controls":            {"friendly_name": "S3 Bucket Ownership Controls", "category": "Storage", "icon": "🔒", "display_on_architecture_chart": False, "parent_type": "aws_s3_bucket"},
    "aws_s3_bucket_public_access_block":          {"friendly_name": "Public Access Block",      "category": "Storage",     "icon": "🔒", "display_on_architecture_chart": False, "parent_type": "aws_s3_bucket"},
    "aws_s3_bucket_policy":                       {"friendly_name": "S3 Bucket Policy",         "category": "Storage",     "icon": "📜", "display_on_architecture_chart": False, "parent_type": "aws_s3_bucket"},
    "aws_ebs_volume":                             {"friendly_name": "EBS Volume",               "category": "Storage",     "icon": "💾"},
    "aws_ecr_repository":                         {"friendly_name": "ECR Repository",           "category": "Storage",     "icon": "🗄️"},
    "aws_volume_attachment":                      {"friendly_name": "Volume Attachment",        "category": "Storage",     "icon": "🔗"},
    # AWS — Database
    "aws_rds_cluster":                            {"friendly_name": "RDS Cluster",              "category": "Database",    "icon": "🗃️"},
    "aws_db_instance":                            {"friendly_name": "RDS Instance",             "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster":                        {"friendly_name": "Neptune Cluster",          "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster_instance":               {"friendly_name": "Neptune Instance",         "category": "Database",    "icon": "🗃️"},
    "aws_neptune_cluster_snapshot":               {"friendly_name": "Neptune Snapshot",         "category": "Database",    "icon": "🗃️"},
    "aws_elasticsearch_domain":                   {"friendly_name": "OpenSearch Domain",        "category": "Database",    "icon": "🔍"},
    "aws_elasticsearch_domain_policy":            {"friendly_name": "OpenSearch Domain",        "category": "Database",    "icon": "🔍", "display_on_architecture_chart": False},
    "aws_dynamodb_table":                         {"friendly_name": "DynamoDB Table",           "category": "Database",    "icon": "🗃️"},
    # AWS — Compute
    "aws_instance":                               {"friendly_name": "EC2 Instance",             "category": "Compute",     "icon": "🖥️", "parent_type": "aws_subnet"},
    "aws_network_interface":                      {"friendly_name": "Network Interface",        "category": "Network",     "icon": "🔌", "display_on_architecture_chart": False, "parent_type": "aws_subnet"},
    "aws_lambda_function":                        {"friendly_name": "Lambda Function",          "category": "Compute",     "icon": "⚡"},
    "aws_ami":                                    {"friendly_name": "AMI",                      "category": "Compute",     "icon": "🖥️"},
    "aws_eks_cluster":                            {"friendly_name": "EKS Cluster",              "category": "Container",   "icon": "☸️"},
    "aws_eks_addon":                              {"friendly_name": "EKS Addon",                "category": "Container",   "icon": "☸️"},
    "aws_ecs_cluster":                            {"friendly_name": "ECS Cluster",              "category": "Container",   "icon": "☸️"},
    "aws_ecs_service":                            {"friendly_name": "ECS Service",              "category": "Container",   "icon": "☸️"},
    "helm_release":                               {"friendly_name": "Helm Release",             "category": "Container",   "icon": "⎈"},
    # AWS — Network & API Gateway
    "aws_api_gateway_rest_api":                   {"friendly_name": "API Gateway",              "category": "API",         "icon": "🔌", "display_on_architecture_chart": True},
    "aws_api_gateway_resource":                   {"friendly_name": "API Resource",             "category": "API",         "icon": "🔗", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_rest_api"},
    "aws_api_gateway_method":                     {"friendly_name": "API Method",               "category": "API",         "icon": "🔌", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_resource"},
    "aws_api_gateway_integration":                {"friendly_name": "API Integration",          "category": "API",         "icon": "🎯", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_method"},
    "aws_api_gateway_deployment":                 {"friendly_name": "API Deployment",           "category": "API",         "icon": "🚀", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_rest_api"},
    "aws_api_gateway_stage":                      {"friendly_name": "API Stage",                "category": "API",         "icon": "🎭", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_deployment"},
    "aws_api_gateway_api_key":                    {"friendly_name": "API Gateway Key",          "category": "API",         "icon": "🔑", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_rest_api"},
    "aws_api_gateway_usage_plan":                 {"friendly_name": "API Gateway Usage Plan",   "category": "API",         "icon": "🔑", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_rest_api"},
    "aws_api_gateway_usage_plan_key":             {"friendly_name": "API Gateway Usage Plan Key", "category": "API",       "icon": "🔑", "display_on_architecture_chart": True,  "parent_type": "aws_api_gateway_usage_plan"},
    "aws_apigatewayv2_api":                       {"friendly_name": "API Gateway v2",           "category": "API",         "icon": "🔌"},
    "aws_apigatewayv2_integration":               {"friendly_name": "API v2 Integration",       "category": "API",         "icon": "🎯", "display_on_architecture_chart": False, "parent_type": "aws_apigatewayv2_api"},
    "aws_apigatewayv2_route":                     {"friendly_name": "API v2 Route",             "category": "API",         "icon": "🔌", "display_on_architecture_chart": False, "parent_type": "aws_apigatewayv2_api"},
    "aws_apigatewayv2_stage":                     {"friendly_name": "API v2 Stage",             "category": "API",         "icon": "🎭", "display_on_architecture_chart": False, "parent_type": "aws_apigatewayv2_api"},
    "aws_elb":                                    {"friendly_name": "Load Balancer",            "category": "Network",     "icon": "🌐"},
    "aws_alb":                                    {"friendly_name": "App Load Balancer",        "category": "Network",     "icon": "🌐"},
    "aws_lb":                                     {"friendly_name": "Network Load Balancer",    "category": "Network",     "icon": "🌐"},
    # AWS — Load Balancer components (listeners/target groups shown nested inside LB)
    "aws_lb_listener":                             {"friendly_name": "Load Balancer Listener",  "category": "Network",     "icon": "🎧", "display_on_architecture_chart": False, "parent_type": "aws_lb"},
    "aws_alb_listener":                            {"friendly_name": "Load Balancer Listener",  "category": "Network",     "icon": "🎧", "display_on_architecture_chart": False, "parent_type": "aws_alb"},
    "aws_lb_target_group":                         {"friendly_name": "Target Group",            "category": "Network",     "icon": "🎯", "display_on_architecture_chart": False, "parent_type": "aws_lb"},
    "aws_alb_target_group":                        {"friendly_name": "Target Group",            "category": "Network",     "icon": "🎯", "display_on_architecture_chart": False, "parent_type": "aws_alb"},
    "aws_lb_target_group_attachment":              {"friendly_name": "Target Attachment",       "category": "Network",     "icon": "🔗", "display_on_architecture_chart": False, "parent_type": "aws_lb_target_group"},
    "aws_eip":                                    {"friendly_name": "Elastic IP",               "category": "Compute",     "icon": "🌍", "display_on_architecture_chart": True, "parent_type": "aws_instance|aws_lb|aws_elb"},
    "aws_route":                                  {"friendly_name": "Route",                    "category": "Network",     "icon": "🛣️"},
    "aws_route_table":                            {"friendly_name": "Route Table",              "category": "Network",     "icon": "🛣️"},
    "aws_route_table_association":                {"friendly_name": "Route Table Association",  "category": "Network",     "icon": "🔗"},

    "aws_vpc":                                    {"friendly_name": "VPC",                      "category": "Network",     "icon": "🔷"},
    "aws_subnet":                                 {"friendly_name": "Subnet",                   "category": "Network",     "icon": "🔷", "parent_type": "aws_vpc"},
    "aws_security_group":                         {"friendly_name": "Security Group",           "category": "Security",    "icon": "🛡️"},
    "aws_security_group_rule":                    {"friendly_name": "Security Group Rule",      "category": "Security",    "icon": "🛡️"},
    "aws_internet_gateway":                       {"friendly_name": "Internet Gateway",         "category": "Network",     "icon": "🌍"},
    # AWS — Identity
    "aws_iam_role":                               {"friendly_name": "IAM Role",                 "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_policy":                             {"friendly_name": "IAM Policy",               "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_policy_document":                    {"friendly_name": "IAM Policy Document",      "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_user":                               {"friendly_name": "IAM User",                 "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_iam_instance_profile":                   {"friendly_name": "IAM Instance Profile",     "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "aws_kms_key":                                {"friendly_name": "KMS Key",                  "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "aws_kms_alias":                              {"friendly_name": "KMS Key Alias",            "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "aws_key_pair":                               {"friendly_name": "Key Pair",                 "category": "Identity",    "icon": "🔑", "display_on_architecture_chart": False},
    "aws_ssm_parameter":                          {"friendly_name": "SSM Parameter",            "category": "Identity",    "icon": "🔐", "display_on_architecture_chart": False},
    # AWS — Messaging
    "aws_sqs_queue":                              {"friendly_name": "SQS Queue",                "category": "Messaging",   "icon": "📨"},
    "aws_sns_topic":                              {"friendly_name": "SNS Topic",                "category": "Messaging",   "icon": "📢"},
    "aws_sns_topic_subscription":                 {"friendly_name": "SNS Subscription",         "category": "Messaging",   "icon": "📬", "display_on_architecture_chart": False, "parent_type": "aws_sns_topic"},
    "aws_kinesis_stream":                         {"friendly_name": "Kinesis Stream",           "category": "Messaging",   "icon": "📡"},
    "aws_kinesis_firehose_delivery_stream":       {"friendly_name": "Kinesis Firehose",         "category": "Messaging",   "icon": "🔥"},
    # GCP — Storage
    "google_storage_bucket":                      {"friendly_name": "GCS Bucket",               "category": "Storage",     "icon": "🗄️"},
    "google_storage_bucket_object":               {"friendly_name": "Storage Object",           "category": "Storage",     "icon": "🪣", "display_on_architecture_chart": False, "parent_type": "google_storage_bucket"},
    "google_storage_bucket_iam_binding":          {"friendly_name": "GCS Bucket",               "category": "Storage",     "icon": "🗄️", "display_on_architecture_chart": False, "parent_type": "google_storage_bucket"},
    "google_storage_bucket_iam_member":           {"friendly_name": "Bucket IAM Member",        "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False, "parent_type": "google_storage_bucket"},
    # GCP — Database
    "google_sql_database_instance":               {"friendly_name": "Cloud SQL Instance",       "category": "Database",    "icon": "🗃️"},
    "google_bigquery_dataset":                    {"friendly_name": "BigQuery Dataset",         "category": "Database",    "icon": "🗃️"},
    "google_bigtable_instance":                   {"friendly_name": "Bigtable Instance",        "category": "Database",    "icon": "🗃️"},
    "google_firestore_database":                  {"friendly_name": "Firestore Database",       "category": "Database",    "icon": "🗄️"},
    "google_firestore_document":                  {"friendly_name": "Firestore Document",       "category": "Database",    "icon": "📄", "display_on_architecture_chart": False, "parent_type": "google_firestore_database"},
    # GCP — Compute
    "google_compute_zone":                        {"friendly_name": "Zone",                     "category": "System",      "icon": "🗺️", "display_on_architecture_chart": False},
    "google_compute_instance":                    {"friendly_name": "Compute Instance",         "category": "Compute",     "icon": "🖥️", "parent_type": "google_compute_zone"},
    "google_cloudfunctions_function":             {"friendly_name": "Cloud Function",           "category": "Compute",     "icon": "⚡"},
    "google_cloudfunctions_function_iam_member":  {"friendly_name": "Function IAM Member",      "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False, "parent_type": "google_cloudfunctions_function"},
    "google_app_engine_application":              {"friendly_name": "App Engine Application",   "category": "Compute",     "icon": "⚙️"},
    # GCP — Container
    "google_container_cluster":                   {"friendly_name": "GKE Cluster",              "category": "Container",   "icon": "☸️"},
    "google_container_node_pool":                 {"friendly_name": "GKE Node Pool",            "category": "Container",   "icon": "☸️"},
    # GCP — API Gateway
    "google_api_gateway_api":                     {"friendly_name": "API Gateway API",          "category": "API",         "icon": "🔌"},
    "google_api_gateway_api_config":              {"friendly_name": "API Config",               "category": "API",         "icon": "⚙️", "display_on_architecture_chart": False, "parent_type": "google_api_gateway_api"},
    "google_api_gateway_gateway":                 {"friendly_name": "API Gateway",              "category": "API",         "icon": "🔌", "display_on_architecture_chart": False, "parent_type": "google_api_gateway_api_config"},
    # GCP — Network
    "google_cloud_run_service":                   {"friendly_name": "Cloud Run Service",        "category": "Compute",     "icon": "🌐"},
    "google_compute_url_map":                     {"friendly_name": "Load Balancer",            "category": "Network",     "icon": "⚖️"},
    "google_compute_network":                     {"friendly_name": "VPC Network",              "category": "Network",     "icon": "🔷"},
    "google_compute_subnetwork":                  {"friendly_name": "Subnetwork",               "category": "Network",     "icon": "🔷", "parent_type": "google_compute_network"},
    "google_compute_firewall":                    {"friendly_name": "Firewall Rule",            "category": "Security",    "icon": "🛡️"},
    # GCP — Identity
    "google_project_iam_binding":                 {"friendly_name": "IAM Binding",              "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "google_project_iam_member":                  {"friendly_name": "IAM Member",               "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "google_project_iam_custom_role":             {"friendly_name": "IAM Custom Role",          "category": "Identity",    "icon": "👤", "display_on_architecture_chart": False},
    "google_project":                             {"friendly_name": "GCP Project",              "category": "System",      "icon": "🗂️", "display_on_architecture_chart": False},
    "google_project_service":                     {"friendly_name": "Project Service",          "category": "System",      "icon": "🔧", "display_on_architecture_chart": False},
    "google_kms_crypto_key":                      {"friendly_name": "KMS Crypto Key",           "category": "Identity",    "icon": "🔑"},
    "google_service_account":                     {"friendly_name": "Service Account",          "category": "Identity",    "icon": "🔑"},
    # GCP — Messaging
    "google_pubsub_topic":                        {"friendly_name": "Pub/Sub Topic",            "category": "Messaging",   "icon": "📢"},
    "google_pubsub_subscription":                 {"friendly_name": "Pub/Sub Subscription",     "category": "Messaging",   "icon": "📬", "display_on_architecture_chart": False, "parent_type": "google_pubsub_topic"},
}

# ---------------------------------------------------------------------------
# Service Patterns — Generalized hierarchical service patterns across providers
# ---------------------------------------------------------------------------
# Defines common patterns for services with:
# - Parent/child hierarchies
# - Ingress endpoints (operations, methods, endpoints)
# - Authorization mechanisms (keys, subscriptions, policies)
# - Egress patterns (backends, databases, logging)

_SERVICE_PATTERNS = {
    "api_gateway": {
        "description": "API Gateway pattern: parent → API → operations with auth",
        "providers": {
            "azure": {
                "parent": "azurerm_api_management",
                "api_resource": "azurerm_api_management_api",
                "operation": "azurerm_api_management_api_operation",
                "auth_resources": ["azurerm_api_management_subscription", "azurerm_api_management_api_key"],
                "policy": "azurerm_api_management_api_policy",
                "backend": "azurerm_api_management_backend",
            },
            "aws": {
                "parent": "aws_api_gateway_rest_api",
                "api_resource": "aws_api_gateway_resource",
                "operation": "aws_api_gateway_method",
                "auth_resources": ["aws_api_gateway_api_key", "aws_api_gateway_usage_plan_key"],
                "policy": "aws_api_gateway_method_settings",
                "backend": "aws_api_gateway_integration",
            },
            "aws_v2": {
                "parent": "aws_apigatewayv2_api",
                "operation": "aws_apigatewayv2_route",
                "auth_resources": ["aws_apigatewayv2_authorizer"],
                "backend": "aws_apigatewayv2_integration",
            },
            "gcp": {
                "parent": "google_api_gateway_api",
                "api_resource": "google_api_gateway_api_config",
                "operation": "google_api_gateway_gateway",
            },
            "oracle": {
                "parent": "oci_apigateway_gateway",
                "operation": "oci_apigateway_deployment",
            },
            "alibaba": {
                "parent": "alicloud_api_gateway_api",
                "auth_resources": ["alicloud_api_gateway_app"],
            },
        },
        "ingress_pattern": "internet_to_operations",
        "auth_detection": ["subscription", "api_key", "oauth", "jwt"],
    },
    
    "storage": {
        "description": "Storage pattern: account → containers/buckets → blobs/objects",
        "providers": {
            "azure": {
                "parent": "azurerm_storage_account",
                "container": "azurerm_storage_container",
                "object": "azurerm_storage_blob",
                "auth_resources": ["azurerm_storage_account_sas", "azurerm_storage_container_sas"],
                "queue": "azurerm_storage_queue",
                "table": "azurerm_storage_table",
            },
            "aws": {
                "parent": "aws_s3_bucket",
                "object": "aws_s3_bucket_object",
                "auth_resources": [
                    "aws_s3_bucket_acl",
                    "aws_s3_bucket_ownership_controls",
                    "aws_s3_bucket_public_access_block",
                    "aws_s3_bucket_policy",
                ],
            },
            "gcp": {
                "parent": "google_storage_bucket",
                "object": "google_storage_bucket_object",
                "auth_resources": ["google_storage_bucket_iam_binding"],
            },
        },
        "ingress_pattern": "client_to_containers",
        "auth_detection": ["sas_token", "iam_policy", "access_key"],
    },
    
    "messaging": {
        "description": "Messaging pattern: namespace → topics/queues → subscriptions",
        "providers": {
            "azure_servicebus": {
                "parent": "azurerm_servicebus_namespace",
                "topic": "azurerm_servicebus_topic",
                "queue": "azurerm_servicebus_queue",
                "subscription": "azurerm_servicebus_subscription",
                "rule": "azurerm_servicebus_subscription_rule",
            },
            "azure_eventhub": {
                "parent": "azurerm_eventhub_namespace",
                "topic": "azurerm_eventhub",
                "subscription": "azurerm_eventhub_consumer_group",
            },
            "aws": {
                "topic": "aws_sns_topic",
                "queue": "aws_sqs_queue",
                "subscription": "aws_sns_topic_subscription",
            },
            "gcp": {
                "topic": "google_pubsub_topic",
                "subscription": "google_pubsub_subscription",
            },
        },
        "ingress_pattern": "app_to_topics",
        "egress_pattern": "subscriptions_to_apps",
    },
    
    "serverless": {
        "description": "Serverless pattern: function with triggers and outputs",
        "providers": {
            "azure": {
                "parent": "azurerm_function_app",
                "trigger": "azurerm_function_app_function",
                "binding": "azurerm_function_app_host_keys",
            },
            "aws": {
                "parent": "aws_lambda_function",
                "trigger": "aws_lambda_event_source_mapping",
                "permission": "aws_lambda_permission",
            },
            "gcp": {
                "parent": "google_cloudfunctions_function",
                "trigger": "google_cloudfunctions_function_iam_binding",
            },
        },
        "ingress_pattern": "event_driven",
        "auth_detection": ["function_key", "iam_role"],
    },
    
    "key_vault": {
        "description": "Key Vault pattern: vault → secrets/keys/certificates",
        "providers": {
            "azure": {
                "parent": "azurerm_key_vault",
                "secret": "azurerm_key_vault_secret",
                "key": "azurerm_key_vault_key",
                "certificate": "azurerm_key_vault_certificate",
                "access_policy": "azurerm_key_vault_access_policy",
            },
            "aws": {
                "parent": "aws_kms_key",
                "secret": "aws_secretsmanager_secret",
            },
            "gcp": {
                "parent": "google_kms_key_ring",
                "key": "google_kms_crypto_key",
                "secret": "google_secret_manager_secret",
            },
        },
        "ingress_pattern": "client_with_auth",
        "auth_detection": ["access_policy", "iam_binding", "rbac"],
    },
    
    "database": {
        "description": "Database pattern: server → databases → users/logins with auth",
        "providers": {
            "azure_sql": {
                "parent": "azurerm_mssql_server",
                "database": "azurerm_mssql_database",
                "auth_resources": ["azurerm_sql_active_directory_administrator", "azurerm_mssql_server_microsoft_support_auditing_policy"],
                "firewall": "azurerm_mssql_firewall_rule",
                "user": "azurerm_sql_database_user",
            },
            "azure_mysql": {
                "parent": "azurerm_mysql_server",
                "database": "azurerm_mysql_database",
                "firewall": "azurerm_mysql_firewall_rule",
                "config": "azurerm_mysql_configuration",
            },
            "azure_postgres": {
                "parent": "azurerm_postgresql_server",
                "database": "azurerm_postgresql_database",
                "firewall": "azurerm_postgresql_firewall_rule",
                "config": "azurerm_postgresql_configuration",
            },
            "aws_rds": {
                "parent": "aws_db_instance",
                "subnet_group": "aws_db_subnet_group",
                "parameter_group": "aws_db_parameter_group",
                "option_group": "aws_db_option_group",
            },
            "gcp_sql": {
                "parent": "google_sql_database_instance",
                "database": "google_sql_database",
                "user": "google_sql_user",
            },
        },
        "ingress_pattern": "app_to_database",
        "auth_detection": ["sql_auth", "aad_auth", "iam_auth", "ssl_cert"],
        "egress_pattern": "logs_to_monitoring",
    },
    
    "cosmos_db": {
        "description": "Cosmos DB pattern: account → databases → containers/collections (auth required)",
        "providers": {
            "azure": {
                "parent": "azurerm_cosmosdb_account",
                "database": ["azurerm_cosmosdb_sql_database", "azurerm_cosmosdb_mongo_database", "azurerm_cosmosdb_cassandra_keyspace"],
                "container": ["azurerm_cosmosdb_sql_container", "azurerm_cosmosdb_mongo_collection", "azurerm_cosmosdb_cassandra_table"],
                "auth_resources": ["azurerm_cosmosdb_sql_role_assignment", "azurerm_cosmosdb_sql_role_definition"],
            },
            "aws": {
                "parent": "aws_dynamodb_table",
                "auth_resources": ["aws_dynamodb_table_item"],
            },
            "gcp": {
                "parent": "google_firestore_database",
                "document": "google_firestore_document",
            },
        },
        "ingress_pattern": "app_with_connection_string",
        "auth_detection": ["connection_string", "rbac", "resource_token"],
        "egress_pattern": "replication_change_feed",
    },
    
    "kubernetes": {
        "description": "Kubernetes pattern: cluster → namespaces → workloads (ingress/egress)",
        "providers": {
            "azure": {
                "parent": "azurerm_kubernetes_cluster",
                "node_pool": "azurerm_kubernetes_cluster_node_pool",
                "workload": ["kubernetes_deployment", "kubernetes_stateful_set", "kubernetes_daemon_set"],
                "service": "kubernetes_service",
                "ingress": "kubernetes_ingress",
                "config": ["kubernetes_config_map", "kubernetes_secret"],
            },
            "aws": {
                "parent": "aws_eks_cluster",
                "node_group": "aws_eks_node_group",
                "addon": "aws_eks_addon",
            },
            "gcp": {
                "parent": "google_container_cluster",
                "node_pool": "google_container_node_pool",
            },
        },
        "ingress_pattern": "ingress_controller_to_services",
        "egress_pattern": "services_to_external",
        "auth_detection": ["rbac", "service_account", "api_token"],
    },
    
    "app_service": {
        "description": "App Service pattern: plan → app service (hosted on VM)",
        "providers": {
            "azure": {
                "parent": "azurerm_service_plan",
                "app": ["azurerm_linux_web_app", "azurerm_windows_web_app", "azurerm_linux_function_app", "azurerm_windows_function_app"],
                "slot": "azurerm_web_app_deployment_slot",
                "config": ["azurerm_app_service_virtual_network_swift_connection", "azurerm_app_service_custom_hostname_binding"],
            },
            "aws": {
                "parent": "aws_elastic_beanstalk_environment",
                "app": "aws_elastic_beanstalk_application",
                "version": "aws_elastic_beanstalk_application_version",
            },
            "gcp": {
                "parent": "google_app_engine_application",
                "service": "google_app_engine_standard_app_version",
            },
        },
        "ingress_pattern": "https_to_app",
        "egress_pattern": "app_to_database_storage",
        "auth_detection": ["managed_identity", "connection_string", "app_settings"],
    },
    
    "monitoring": {
        "description": "Monitoring pattern: workspace/insights → logs/metrics → alerts",
        "providers": {
            "azure": {
                "parent": ["azurerm_application_insights", "azurerm_log_analytics_workspace"],
                "metric": "azurerm_monitor_metric_alert",
                "log_alert": "azurerm_monitor_scheduled_query_rules_alert",
                "action_group": "azurerm_monitor_action_group",
                "diagnostic": "azurerm_monitor_diagnostic_setting",
            },
            "aws": {
                "parent": "aws_cloudwatch_log_group",
                "metric_alarm": "aws_cloudwatch_metric_alarm",
                "dashboard": "aws_cloudwatch_dashboard",
            },
            "gcp": {
                "parent": "google_logging_project_sink",
                "alert": "google_monitoring_alert_policy",
            },
        },
        "ingress_pattern": "telemetry_from_resources",
        "egress_pattern": "alerts_to_action_groups",
    },
}

_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("azurerm_", "azure"),
    ("azuread_", "azure"),
    ("kubernetes_", "kubernetes"),
    ("random_", "terraform"),
    ("time_", "terraform"),
    ("null_resource", "terraform"),
    ("terraform_data", "terraform"),  # Will be inferred from context
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
        is_data_store, is_internet_facing_capable,
        display_on_architecture_chart, parent_type

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
            return {
                **_FALLBACK[terraform_type],
                "provider": provider,
                "is_data_store": False,
                "is_internet_facing_capable": False,
                "display_on_architecture_chart": bool(
                    _FALLBACK[terraform_type].get("display_on_architecture_chart", True)
                ),
                "parent_type": _FALLBACK[terraform_type].get("parent_type"),
            }
        derived = _derive(terraform_type)
        return {
            **derived,
            "is_data_store": False,
            "is_internet_facing_capable": False,
            "display_on_architecture_chart": bool(derived.get("display_on_architecture_chart", True)),
            "parent_type": derived.get("parent_type"),
        }
    try:
        table_cols = {c[1] for c in conn.execute("PRAGMA table_info(resource_types)").fetchall()}
        has_display = "display_on_architecture_chart" in table_cols
        has_parent = "parent_type" in table_cols
        select_extra = []
        if has_display:
            select_extra.append("rt.display_on_architecture_chart")
        if has_parent:
            select_extra.append("rt.parent_type")
        select_extra_sql = (", " + ", ".join(select_extra)) if select_extra else ""
        row = conn.execute(
            f"""
            SELECT rt.friendly_name, rt.category, rt.icon,
                   p.key AS provider,
                   rt.is_data_store, rt.is_internet_facing_capable
                   {select_extra_sql}
            FROM resource_types rt
            LEFT JOIN providers p ON rt.provider_id = p.id
            WHERE rt.terraform_type = ?
            """,
            (terraform_type,),
        ).fetchone()
        if row:
            idx = 6
            display_value = True
            parent_type = None
            if has_display:
                display_value = bool(row[idx]) if row[idx] is not None else True
                idx += 1
            if has_parent:
                parent_type = row[idx] if row[idx] else None
            return {
                "friendly_name": row[0],
                "category":      row[1],
                "icon":          row[2] or "📦",
                "provider":      row[3] or "unknown",
                "is_data_store":               bool(row[4]),
                "is_internet_facing_capable":  bool(row[5]),
                "display_on_architecture_chart": display_value,
                "parent_type": parent_type,
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
        entry = {
            **_FALLBACK[terraform_type],
            "provider": provider,
            "is_data_store": False,
            "is_internet_facing_capable": False,
            "display_on_architecture_chart": bool(
                _FALLBACK[terraform_type].get("display_on_architecture_chart", True)
            ),
            "parent_type": _FALLBACK[terraform_type].get("parent_type"),
        }
        _auto_insert(conn, terraform_type, entry)
        return entry

    # 3. Derive from type string and auto-insert for future calls
    derived = {
        **_derive(terraform_type),
        "is_data_store": False,
        "is_internet_facing_capable": False,
    }
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


# New: render category (provider-agnostic grouping used for architecture layout)
# Possible values: Compute, Container, Database, Storage, Network, Firewall, Identity, Security, Monitoring, Other
def get_render_category(conn: sqlite3.Connection | None, terraform_type: str) -> str:
    """Return a canonical render category for layout/styling.

    Lookup order: resource_types table -> in-memory fallback -> derived heuristics.
    """
    info = get_resource_type(conn, terraform_type)
    # Override: NSG should be in Network tier for diagram layout, not Security
    lower_type = (terraform_type or '').lower()
    if any(k in lower_type for k in ['nsg', 'network_security_group']):
        return 'Network'
    # Use category if present and maps to a known render category
    cat = (info.get('category') or '').strip()
    if cat:
        # Normalize common category names to render categories
        mapping = {
            'Compute': 'Compute',
            'Container': 'Container',
            'Database': 'Database',
            'Storage': 'Storage',
            'Identity': 'Identity',
            'Security': 'Security',
            'Network': 'Network',
            'Messaging': 'Messaging',
            'Monitoring': 'Monitoring',
            'Logging': 'Logging',
            'Cache': 'Cache',
            'Serverless': 'Serverless',
            'API': 'API',
            'Other': 'Other',
        }
        if cat in mapping:
            return mapping[cat]
    # Fallback derive from terraform_type keywords
    lower = (terraform_type or '').lower()
    if any(k in lower for k in ['vm', 'instance', 'virtual_machine', 'linux_virtual_machine', 'windows_virtual_machine', 'ec2', 'instance']):
        return 'Compute'
    if any(k in lower for k in ['kubernetes', 'aks', 'eks', 'gke', 'cluster', 'container', 'ecs']):
        return 'Container'
    if any(k in lower for k in ['sql', 'rds', 'database', 'cosmos', 'postgresql', 'mysql', 'mssql', 'bigquery', 'db_']):
        return 'Database'
    if any(k in lower for k in ['storage', 's3', 'blob', 'bucket', 'disk', 'volume']):
        return 'Storage'
    if any(k in lower for k in ['vnet', 'virtual_network', 'subnet', 'network', 'public_ip', 'vpc', 'route', 'gateway', 'lb', 'load_balancer', 'nsg', 'network_security_group', 'security_group']):
        return 'Network'
    if any(k in lower for k in ['firewall', 'waf']):
        return 'Firewall'
    if any(k in lower for k in ['role', 'azuread', 'iam', 'user', 'group', 'service_principal']):
        return 'Identity'
    if any(k in lower for k in ['monitor', 'insights', 'log', 'alert', 'diagnostic']):
        return 'Monitoring'
    if any(k in lower for k in ['servicebus', 'eventhub', 'eventgrid', 'sqs', 'sns', 'kinesis', 'pubsub', 'queue', 'topic']):
        return 'Messaging'
    # Default
    return 'Other'


# New helper: determine whether a network-type resource should be treated as a physical network device
def is_physical_network_device(conn: sqlite3.Connection | None, terraform_type: str) -> bool:
    """Return True if terraform_type corresponds to a physical/edge network device we want on architecture diagrams.

    Heuristics: match common appliance keywords (firewall, load balancer, gateway, appliance, nat).
    Can be extended by adding an explicit flag to resource_types rows.
    """
    if not terraform_type:
        return False
    lower = terraform_type.lower()
    # If DB has explicit flag, prefer it
    try:
        if conn is not None:
            row = conn.execute("SELECT display_on_architecture_chart FROM resource_types WHERE terraform_type = ?", (terraform_type,)).fetchone()
            if row is not None:
                # If display_on_architecture_chart is true but category is Network, we still require it to be physical device
                # So don't return True solely based on display flag here.
                pass
    except Exception:
        pass

    physical_tokens = ('firewall', 'application_gateway', 'load_balancer', 'lb', 'nat_gateway', 'gateway', 'appliance', 'virtual_appliance', 'edge', 'vpn_gateway', 'nsg', 'network_security_group')
    return any(tok in lower for tok in physical_tokens)


# ---------------------------------------------------------------------------
# Service Pattern Helpers — Use _SERVICE_PATTERNS for consistent cross-provider behavior
# ---------------------------------------------------------------------------

def get_service_pattern(resource_type: str) -> tuple[str | None, dict | None]:
    """Return (pattern_name, pattern_config) for a resource type if it matches a known pattern.
    
    Returns (None, None) if no pattern matches.
    
    Examples:
        azurerm_api_management_api -> ("api_gateway", {...})
        aws_s3_bucket -> ("storage", {...})
        azurerm_servicebus_topic -> ("messaging", {...})
    """
    for pattern_name, pattern in _SERVICE_PATTERNS.items():
        for provider_key, provider_config in pattern.get("providers", {}).items():
            for component_key, component_type in provider_config.items():
                if isinstance(component_type, list):
                    if resource_type in component_type:
                        return (pattern_name, pattern)
                elif resource_type == component_type:
                    return (pattern_name, pattern)
    return (None, None)


def get_pattern_components(pattern_name: str, resource_types: list[str]) -> dict[str, list[str]]:
    """Given a pattern name and list of resource types, return components grouped by role.
    
    Returns dict like:
        {
            "parent": ["azurerm_api_management"],
            "operation": ["azurerm_api_management_api_operation", ...],
            "auth_resources": ["azurerm_api_management_subscription", ...],
        }
    """
    pattern = _SERVICE_PATTERNS.get(pattern_name)
    if not pattern:
        return {}
    
    components = {}
    for provider_key, provider_config in pattern.get("providers", {}).items():
        for component_key, expected_types in provider_config.items():
            if isinstance(expected_types, str):
                expected_types = [expected_types]
            
            matches = [rt for rt in resource_types if rt in expected_types]
            if matches:
                components.setdefault(component_key, []).extend(matches)
    
    return components


def is_ingress_resource(resource_type: str) -> bool:
    """Return True if this resource type is an ingress endpoint (operations, methods, routes)."""
    pattern_name, pattern = get_service_pattern(resource_type)
    if not pattern:
        return False
    
    # Check if this is an "operation" component in any pattern
    for provider_config in pattern.get("providers", {}).values():
        operation_types = provider_config.get("operation")
        if operation_types:
            if isinstance(operation_types, str):
                operation_types = [operation_types]
            if resource_type in operation_types:
                return True
    return False


def is_auth_resource(resource_type: str) -> bool:
    """Return True if this resource type is an authorization mechanism (keys, subscriptions, policies)."""
    pattern_name, pattern = get_service_pattern(resource_type)
    if not pattern:
        return False
    
    # Check if this is an "auth_resources" component in any pattern
    for provider_config in pattern.get("providers", {}).values():
        auth_types = provider_config.get("auth_resources", [])
        if resource_type in auth_types:
            return True
    return False


# ---------------------------------------------------------------------------
# Canonical type preferences — for diagram de-duplication.
# When multiple related terraform types map to the same logical service,
# prefer the canonical (primary resource) type for display.
# Each entry: canonical_type -> [other types in the same family]
# ---------------------------------------------------------------------------
_PREFERRED_TYPES: dict[str, list[str]] = {
    "azurerm_key_vault":          ["azurerm_key_vault_key", "azurerm_key_vault_secret"],
    "azurerm_mssql_server":       ["azurerm_sql_server"],
    "azurerm_function_app":       ["azurerm_linux_function_app", "azurerm_windows_function_app"],
    "azurerm_service_plan":       ["azurerm_app_service_plan"],
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

    hidden_tokens = (
        "policy",
        "role_assignment",
        "role_definition",
        "iam_",
        "_iam_",
        "kms_",
        "binding",
        # Utility providers / helpers which should not appear on architecture diagrams
        "random_",
        "time_",
        "null_resource",
    )
    display_on_architecture_chart = not any(token in lower for token in hidden_tokens)

    parent_type = None
    if terraform_type in {"aws_s3_bucket_acl", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_policy", "aws_s3_bucket_public_access_block"}:
        parent_type = "aws_s3_bucket"
    elif terraform_type == "aws_lb_listener":
        parent_type = "aws_lb"
    elif terraform_type == "aws_alb_listener":
        parent_type = "aws_alb"
    elif terraform_type == "aws_lb_target_group":
        parent_type = "aws_lb"
    elif terraform_type == "aws_alb_target_group":
        parent_type = "aws_alb"
    elif terraform_type == "aws_lb_target_group_attachment":
        parent_type = "aws_lb_target_group"

    if parent_type:
        display_on_architecture_chart = False

    return {
        "friendly_name": friendly,
        "category": category,
        "icon": "📦",
        "provider": provider,
        "display_on_architecture_chart": display_on_architecture_chart,
        "parent_type": parent_type,
    }


def _auto_insert(conn: sqlite3.Connection, terraform_type: str, entry: dict) -> None:
    """Insert an unknown type so it only needs deriving once."""
    try:
        row = conn.execute("SELECT id FROM providers WHERE key = ?", (entry["provider"],)).fetchone()
        pid = row[0] if row else None
        columns = {c[1] for c in conn.execute("PRAGMA table_info(resource_types)").fetchall()}
        if {"display_on_architecture_chart", "parent_type"}.issubset(columns):
            conn.execute(
                """
                INSERT OR IGNORE INTO resource_types
                (
                  provider_id,
                  terraform_type,
                  friendly_name,
                  category,
                  icon,
                  display_on_architecture_chart,
                  parent_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    terraform_type,
                    entry["friendly_name"],
                    entry["category"],
                    entry["icon"],
                    1 if entry.get("display_on_architecture_chart", True) else 0,
                    entry.get("parent_type"),
                ),
            )
        else:
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
