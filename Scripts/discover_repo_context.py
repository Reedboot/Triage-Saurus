#!/usr/bin/env python3
"""Fast, non-security context discovery for a local repository (writes summary/knowledge).

This script is the "Phase 1 - Context Discovery" step described in:
- Agents/ContextDiscoveryAgent.md
- Templates/Workflows.md (Repository Scan Flow - Step 3)

It performs quick file-based discovery (no network, no git commands by default) and writes:
- Output/Summary/Repos/<RepoName>.md
- Output/Knowledge/Repos.md (repo inventory + repo root directory)
- Populates SQLite database with resources, connections, and context

Usage:
  python3 Scripts/discover_repo_context.py /abs/path/to/repo
  python3 Scripts/discover_repo_context.py /abs/path/to/repo --repos-root /abs/path/to/repos
  python3 Scripts/discover_repo_context.py /abs/path/to/repo --output-dir /path/to/experiment/folder

For experiment isolation, use --output-dir to write to the experiment folder:
  python3 Scripts/discover_repo_context.py /mnt/c/Repos/fi_api --repos-root /mnt/c/Repos \\
      --output-dir Output/Learning/experiments/001_baseline

Exit codes:
  0 success
  2 invalid arguments
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from output_paths import OUTPUT_KNOWLEDGE_DIR, OUTPUT_SUMMARY_DIR
from markdown_validator import validate_markdown_file

# Database integration
try:
    from db_helpers import (
        insert_repository, 
        insert_resource, 
        insert_connection,
        update_repository_stats
    )
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    print("WARN: Database helpers not available, skipping database population", file=sys.stderr)


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
    ("Azure", re.compile(r'provider\s+"azurerm"|resource\s+"azurerm_|data\s+"azurerm_', re.IGNORECASE)),
    ("AWS", re.compile(r'provider\s+"aws"|resource\s+"aws_|data\s+"aws_', re.IGNORECASE)),
    ("GCP", re.compile(r'provider\s+"google"|resource\s+"google_|data\s+"google_', re.IGNORECASE)),
]

NETWORK_SECURITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Azure - Network restrictions
    ("Key Vault network ACLs", re.compile(r'network_acls\s*\{[^}]*default_action\s*=\s*"Deny"', re.IGNORECASE)),
    ("Storage network rules", re.compile(r'network_rules\s*\{[^}]*default_action\s*=\s*"Deny"', re.IGNORECASE)),
    ("SQL Server firewall", re.compile(r'resource\s+"azurerm_mssql_firewall_rule"', re.IGNORECASE)),
    ("AKS authorized IP ranges", re.compile(r'api_server_authorized_ip_ranges\s*=', re.IGNORECASE)),
    ("App Service IP restrictions", re.compile(r'ip_restriction\s*\{', re.IGNORECASE)),
    ("Private endpoints", re.compile(r'resource\s+"azurerm_private_endpoint"', re.IGNORECASE)),
    ("NSG rules", re.compile(r'resource\s+"azurerm_network_security_group"', re.IGNORECASE)),
    ("NSG associations", re.compile(r'resource\s+"azurerm_network_security_rule"', re.IGNORECASE)),
    ("PostgreSQL firewall", re.compile(r'resource\s+"azurerm_postgresql_firewall_rule"', re.IGNORECASE)),
    ("MySQL firewall", re.compile(r'resource\s+"azurerm_mysql_firewall_rule"', re.IGNORECASE)),
    ("Cosmos DB IP filter", re.compile(r'ip_range_filter\s*=', re.IGNORECASE)),
    ("Redis Cache firewall", re.compile(r'resource\s+"azurerm_redis_firewall_rule"', re.IGNORECASE)),
    ("Container Registry network rule", re.compile(r'network_rule_set\s*\{', re.IGNORECASE)),
    ("Function App IP restrictions", re.compile(r'ip_restriction\s*\{', re.IGNORECASE)),
    ("APIM virtual network", re.compile(r'virtual_network_type\s*=\s*"Internal"', re.IGNORECASE)),
    ("Event Hub network rules", re.compile(r'network_rulesets\s*\{', re.IGNORECASE)),
    ("Service Bus network rules", re.compile(r'network_rule_set\s*\{', re.IGNORECASE)),
    ("VNet service endpoints", re.compile(r'service_endpoints\s*=', re.IGNORECASE)),
    ("Private DNS zones", re.compile(r'resource\s+"azurerm_private_dns_zone"', re.IGNORECASE)),
    ("Public IP addresses", re.compile(r'resource\s+"azurerm_public_ip"', re.IGNORECASE)),
    
    # AWS - Network restrictions
    ("S3 public access block", re.compile(r'resource\s+"aws_s3_bucket_public_access_block"', re.IGNORECASE)),
    ("RDS public access", re.compile(r'publicly_accessible\s*=\s*false', re.IGNORECASE)),
    ("EC2 security groups", re.compile(r'resource\s+"aws_security_group"', re.IGNORECASE)),
    ("Lambda VPC config", re.compile(r'vpc_config\s*\{', re.IGNORECASE)),
    ("ALB security groups", re.compile(r'security_groups\s*=', re.IGNORECASE)),
    ("VPC endpoints", re.compile(r'resource\s+"aws_vpc_endpoint"', re.IGNORECASE)),
    ("Network ACLs", re.compile(r'resource\s+"aws_network_acl"', re.IGNORECASE)),
    ("EKS endpoint access", re.compile(r'endpoint_public_access\s*=\s*false', re.IGNORECASE)),
    ("ElastiCache subnet group", re.compile(r'resource\s+"aws_elasticache_subnet_group"', re.IGNORECASE)),
    ("Redshift public access", re.compile(r'publicly_accessible\s*=\s*false', re.IGNORECASE)),
    
    # GCP - Network restrictions
    ("Cloud Storage IAM", re.compile(r'uniform_bucket_level_access\s*=\s*true', re.IGNORECASE)),
    ("Cloud SQL authorized networks", re.compile(r'authorized_networks\s*\{', re.IGNORECASE)),
    ("Compute firewall rules", re.compile(r'resource\s+"google_compute_firewall"', re.IGNORECASE)),
    ("VPC Service Controls", re.compile(r'resource\s+"google_access_context_manager_service_perimeter"', re.IGNORECASE)),
    ("Private Google Access", re.compile(r'private_ip_google_access\s*=\s*true', re.IGNORECASE)),
    ("Cloud SQL private IP", re.compile(r'private_network\s*=', re.IGNORECASE)),
    ("GKE private cluster", re.compile(r'private_cluster_config\s*\{', re.IGNORECASE)),
    ("VPC connector", re.compile(r'resource\s+"google_vpc_access_connector"', re.IGNORECASE)),
    ("Cloud Armor policies", re.compile(r'resource\s+"google_compute_security_policy"', re.IGNORECASE)),
    ("Private Service Connect", re.compile(r'resource\s+"google_compute_forwarding_rule".*purpose\s*=\s*"PRIVATE_SERVICE_CONNECT"', re.IGNORECASE | re.DOTALL)),
]

ACCESS_CONTROL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Azure - Managed Identity & AAD
    ("Managed Identity enabled", re.compile(r'identity\s*\{\s*type\s*=\s*"SystemAssigned"', re.IGNORECASE)),
    ("User Assigned Identity", re.compile(r'resource\s+"azurerm_user_assigned_identity"', re.IGNORECASE)),
    ("Storage shared keys disabled", re.compile(r'shared_access_key_enabled\s*=\s*false', re.IGNORECASE)),
    ("Key Vault RBAC", re.compile(r'enable_rbac_authorization\s*=\s*true', re.IGNORECASE)),
    ("SQL AAD admin", re.compile(r'resource\s+"azurerm_mssql_server_aad_administrator"', re.IGNORECASE)),
    ("AKS AAD RBAC", re.compile(r'azure_active_directory_role_based_access_control\s*\{', re.IGNORECASE)),
    ("VM password auth disabled", re.compile(r'disable_password_authentication\s*=\s*true', re.IGNORECASE)),
    ("VM SSH keys", re.compile(r'admin_ssh_key\s*\{', re.IGNORECASE)),
    ("PostgreSQL AAD admin", re.compile(r'resource\s+"azurerm_postgresql_aad_administrator"', re.IGNORECASE)),
    ("MySQL AAD admin", re.compile(r'resource\s+"azurerm_mysql_aad_administrator"', re.IGNORECASE)),
    ("Container Registry admin disabled", re.compile(r'admin_enabled\s*=\s*false', re.IGNORECASE)),
    ("JIT VM access", re.compile(r'resource\s+"azurerm_security_center_jit_network_access_policy"', re.IGNORECASE)),
    ("Role assignments", re.compile(r'resource\s+"azurerm_role_assignment"', re.IGNORECASE)),
    ("Service Principal", re.compile(r'resource\s+"azuread_service_principal"', re.IGNORECASE)),
    ("Service Principal password", re.compile(r'resource\s+"azuread_service_principal_password"', re.IGNORECASE)),
    ("App registration", re.compile(r'resource\s+"azuread_application"', re.IGNORECASE)),
    ("Cosmos DB RBAC", re.compile(r'resource\s+"azurerm_cosmosdb_sql_role_assignment"', re.IGNORECASE)),
    ("Storage SAS policy", re.compile(r'sas_policy\s*\{', re.IGNORECASE)),
    ("Key Vault access policy", re.compile(r'access_policy\s*\{', re.IGNORECASE)),
    ("Conditional access", re.compile(r'resource\s+"azuread_conditional_access_policy"', re.IGNORECASE)),
    
    # AWS - IAM & Identity
    ("IAM role", re.compile(r'resource\s+"aws_iam_role"', re.IGNORECASE)),
    ("IAM policy", re.compile(r'resource\s+"aws_iam_policy"', re.IGNORECASE)),
    ("IAM role policy attachment", re.compile(r'resource\s+"aws_iam_role_policy_attachment"', re.IGNORECASE)),
    ("RDS IAM auth", re.compile(r'iam_database_authentication_enabled\s*=\s*true', re.IGNORECASE)),
    ("EKS IAM role", re.compile(r'iam_role_arn\s*=', re.IGNORECASE)),
    ("S3 bucket policy", re.compile(r'resource\s+"aws_s3_bucket_policy"', re.IGNORECASE)),
    ("KMS key policy", re.compile(r'resource\s+"aws_kms_key"\s*\{[^}]*policy\s*=', re.IGNORECASE | re.DOTALL)),
    ("Secrets Manager", re.compile(r'resource\s+"aws_secretsmanager_secret"', re.IGNORECASE)),
    ("Systems Manager Parameter", re.compile(r'resource\s+"aws_ssm_parameter"', re.IGNORECASE)),
    ("IAM instance profile", re.compile(r'resource\s+"aws_iam_instance_profile"', re.IGNORECASE)),
    ("Assume role policy", re.compile(r'assume_role_policy\s*=', re.IGNORECASE)),
    ("MFA required", re.compile(r'"aws:MultiFactorAuthPresent"\s*:\s*"true"', re.IGNORECASE)),
    ("IAM password policy", re.compile(r'resource\s+"aws_iam_account_password_policy"', re.IGNORECASE)),
    ("IAM access key", re.compile(r'resource\s+"aws_iam_access_key"', re.IGNORECASE)),
    ("Cognito user pool", re.compile(r'resource\s+"aws_cognito_user_pool"', re.IGNORECASE)),
    
    # GCP - IAM & Service Accounts
    ("Service account", re.compile(r'resource\s+"google_service_account"', re.IGNORECASE)),
    ("IAM policy binding", re.compile(r'resource\s+"google_project_iam_binding"', re.IGNORECASE)),
    ("IAM member", re.compile(r'resource\s+"google_project_iam_member"', re.IGNORECASE)),
    ("Service account key", re.compile(r'resource\s+"google_service_account_key"', re.IGNORECASE)),
    ("Workload Identity", re.compile(r'workload_identity_config\s*\{', re.IGNORECASE)),
    ("Cloud SQL IAM auth", re.compile(r'database_flags\s*\{[^}]*name\s*=\s*"cloudsql.iam_authentication"', re.IGNORECASE | re.DOTALL)),
    ("IAM conditions", re.compile(r'condition\s*\{[^}]*title\s*=', re.IGNORECASE | re.DOTALL)),
    ("Organization policy", re.compile(r'resource\s+"google_organization_policy"', re.IGNORECASE)),
    ("VPC Service Control", re.compile(r'resource\s+"google_access_context_manager"', re.IGNORECASE)),
    ("Secret Manager", re.compile(r'resource\s+"google_secret_manager_secret"', re.IGNORECASE)),
    
    # Legacy auth patterns to flag
    ("Hardcoded passwords", re.compile(r'(password|pwd)\s*=\s*"[^"]+"', re.IGNORECASE)),
    ("Connection strings", re.compile(r'(Server=|Data Source=)[^;]+;.*password=', re.IGNORECASE)),
    ("API keys in code", re.compile(r'(api_key|apikey)\s*=\s*"[A-Za-z0-9]{20,}"', re.IGNORECASE)),
    ("AWS access keys", re.compile(r'(aws_access_key_id|aws_secret_access_key)\s*=', re.IGNORECASE)),
    ("Service account JSON", re.compile(r'GOOGLE_APPLICATION_CREDENTIALS|service-account.*\.json', re.IGNORECASE)),
]

AUDIT_LOGGING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Azure - Diagnostic settings
    ("Monitor diagnostic setting", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"', re.IGNORECASE)),
    ("Key Vault logging", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*key_vault', re.IGNORECASE | re.DOTALL)),
    ("Storage logging", re.compile(r'logging\s*\{[^}]*version\s*=', re.IGNORECASE | re.DOTALL)),
    ("SQL auditing", re.compile(r'resource\s+"azurerm_mssql_server_extended_auditing_policy"', re.IGNORECASE)),
    ("SQL vulnerability assessment", re.compile(r'resource\s+"azurerm_mssql_server_vulnerability_assessment"', re.IGNORECASE)),
    ("NSG flow logs", re.compile(r'resource\s+"azurerm_network_watcher_flow_log"', re.IGNORECASE)),
    ("VM diagnostic extension", re.compile(r'resource\s+"azurerm_virtual_machine_extension".*publisher.*Microsoft\.Insights', re.IGNORECASE | re.DOTALL)),
    ("Application Insights", re.compile(r'resource\s+"azurerm_application_insights"', re.IGNORECASE)),
    ("Log Analytics workspace", re.compile(r'resource\s+"azurerm_log_analytics_workspace"', re.IGNORECASE)),
    ("PostgreSQL logging", re.compile(r'log_checkpoints\s*=\s*"on"|log_connections\s*=\s*"on"', re.IGNORECASE)),
    ("MySQL audit log", re.compile(r'audit_log_enabled\s*=\s*true', re.IGNORECASE)),
    ("Cosmos DB diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*cosmosdb', re.IGNORECASE | re.DOTALL)),
    ("APIM diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*api_management', re.IGNORECASE | re.DOTALL)),
    ("Firewall diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*azurerm_firewall', re.IGNORECASE | re.DOTALL)),
    ("Activity Log export", re.compile(r'resource\s+"azurerm_monitor_log_profile"', re.IGNORECASE)),
    ("Event Hub diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*eventhub', re.IGNORECASE | re.DOTALL)),
    ("Service Bus diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*servicebus', re.IGNORECASE | re.DOTALL)),
    ("AKS diagnostic", re.compile(r'resource\s+"azurerm_monitor_diagnostic_setting"\s*\{[^}]*target_resource_id.*kubernetes', re.IGNORECASE | re.DOTALL)),
    
    # AWS - CloudTrail & Logging
    ("CloudTrail", re.compile(r'resource\s+"aws_cloudtrail"', re.IGNORECASE)),
    ("CloudWatch log group", re.compile(r'resource\s+"aws_cloudwatch_log_group"', re.IGNORECASE)),
    ("VPC flow logs", re.compile(r'resource\s+"aws_flow_log"', re.IGNORECASE)),
    ("S3 bucket logging", re.compile(r'logging\s*\{[^}]*target_bucket\s*=', re.IGNORECASE | re.DOTALL)),
    ("RDS enhanced monitoring", re.compile(r'enabled_cloudwatch_logs_exports\s*=', re.IGNORECASE)),
    ("Lambda CloudWatch logs", re.compile(r'resource\s+"aws_lambda_function"\s*\{[^}]*environment', re.IGNORECASE | re.DOTALL)),
    ("EKS logging", re.compile(r'enabled_cluster_log_types\s*=', re.IGNORECASE)),
    ("ALB access logs", re.compile(r'access_logs\s*\{[^}]*enabled\s*=\s*true', re.IGNORECASE | re.DOTALL)),
    ("CloudFront logging", re.compile(r'logging_config\s*\{', re.IGNORECASE)),
    ("API Gateway logging", re.compile(r'resource\s+"aws_api_gateway_method_settings".*logging_level', re.IGNORECASE | re.DOTALL)),
    ("Config recorder", re.compile(r'resource\s+"aws_config_configuration_recorder"', re.IGNORECASE)),
    
    # GCP - Cloud Audit Logs
    ("Cloud Audit Logs", re.compile(r'resource\s+"google_project_iam_audit_config"', re.IGNORECASE)),
    ("Logging project sink", re.compile(r'resource\s+"google_logging_project_sink"', re.IGNORECASE)),
    ("VPC flow logs", re.compile(r'log_config\s*\{[^}]*enable\s*=\s*true', re.IGNORECASE | re.DOTALL)),
    ("Cloud SQL audit log", re.compile(r'database_flags\s*\{[^}]*name\s*=\s*"log_statement"', re.IGNORECASE | re.DOTALL)),
    ("GKE logging", re.compile(r'logging_service\s*=\s*"logging\.googleapis\.com/kubernetes"', re.IGNORECASE)),
    ("Load balancer logging", re.compile(r'log_config\s*\{[^}]*enable\s*=\s*true', re.IGNORECASE | re.DOTALL)),
    ("Cloud Functions logging", re.compile(r'resource\s+"google_cloudfunctions_function"', re.IGNORECASE)),
    ("Cloud Storage bucket logging", re.compile(r'logging\s*\{[^}]*log_bucket\s*=', re.IGNORECASE | re.DOTALL)),
    ("Firewall rule logging", re.compile(r'log_config\s*\{[^}]*metadata\s*=\s*"INCLUDE_ALL_METADATA"', re.IGNORECASE | re.DOTALL)),
]

MONITORING_INFRASTRUCTURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Azure - SIEM & Monitoring
    ("Azure Sentinel", re.compile(r'resource\s+"azurerm_sentinel_log_analytics_workspace_onboarding"', re.IGNORECASE)),
    ("Sentinel analytics rule", re.compile(r'resource\s+"azurerm_sentinel_alert_rule"', re.IGNORECASE)),
    ("Sentinel automation rule", re.compile(r'resource\s+"azurerm_sentinel_automation_rule"', re.IGNORECASE)),
    ("Sentinel data connector", re.compile(r'resource\s+"azurerm_sentinel_data_connector"', re.IGNORECASE)),
    ("Log Analytics workspace", re.compile(r'resource\s+"azurerm_log_analytics_workspace"', re.IGNORECASE)),
    ("Azure Monitor alerts", re.compile(r'resource\s+"azurerm_monitor_metric_alert"', re.IGNORECASE)),
    ("Action group", re.compile(r'resource\s+"azurerm_monitor_action_group"', re.IGNORECASE)),
    ("Security Center", re.compile(r'resource\s+"azurerm_security_center_subscription_pricing"', re.IGNORECASE)),
    ("Defender for Cloud", re.compile(r'tier\s*=\s*"Standard"', re.IGNORECASE)),
    
    # AWS - SIEM & Monitoring
    ("GuardDuty", re.compile(r'resource\s+"aws_guardduty_detector"', re.IGNORECASE)),
    ("SecurityHub", re.compile(r'resource\s+"aws_securityhub_account"', re.IGNORECASE)),
    ("CloudWatch alarm", re.compile(r'resource\s+"aws_cloudwatch_metric_alarm"', re.IGNORECASE)),
    ("SNS topic", re.compile(r'resource\s+"aws_sns_topic"', re.IGNORECASE)),
    ("EventBridge rule", re.compile(r'resource\s+"aws_cloudwatch_event_rule"', re.IGNORECASE)),
    ("Config rule", re.compile(r'resource\s+"aws_config_config_rule"', re.IGNORECASE)),
    
    # GCP - SIEM & Monitoring
    ("Security Command Center", re.compile(r'resource\s+"google_scc_source"', re.IGNORECASE)),
    ("Cloud Monitoring alert", re.compile(r'resource\s+"google_monitoring_alert_policy"', re.IGNORECASE)),
    ("Notification channel", re.compile(r'resource\s+"google_monitoring_notification_channel"', re.IGNORECASE)),
    ("Event Arc", re.compile(r'resource\s+"google_eventarc_trigger"', re.IGNORECASE)),
    ("Cloud Security Scanner", re.compile(r'resource\s+"google_compute_security_scan_config"', re.IGNORECASE)),
]

DATA_PROTECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Azure - Encryption
    ("SQL TDE", re.compile(r'transparent_data_encryption\s*=\s*true', re.IGNORECASE)),
    ("SQL minimum TLS", re.compile(r'minimum_tls_version\s*=\s*"1\.2"', re.IGNORECASE)),
    ("Storage HTTPS only", re.compile(r'enable_https_traffic_only\s*=\s*true', re.IGNORECASE)),
    ("Storage infrastructure encryption", re.compile(r'infrastructure_encryption_enabled\s*=\s*true', re.IGNORECASE)),
    ("Storage minimum TLS", re.compile(r'min_tls_version\s*=\s*"TLS1_2"', re.IGNORECASE)),
    ("VM disk encryption", re.compile(r'resource\s+"azurerm_disk_encryption_set"', re.IGNORECASE)),
    ("AKS encryption at rest", re.compile(r'encryption_at_rest_enabled\s*=\s*true', re.IGNORECASE)),
    ("Key Vault HSM", re.compile(r'sku_name\s*=\s*"premium"', re.IGNORECASE)),
    ("Customer-managed key", re.compile(r'key_vault_key_id\s*=', re.IGNORECASE)),
    ("Cosmos DB CMK", re.compile(r'resource\s+"azurerm_cosmosdb_account"\s*\{[^}]*key_vault_key_id', re.IGNORECASE | re.DOTALL)),
    ("PostgreSQL SSL", re.compile(r'ssl_enforcement_enabled\s*=\s*true', re.IGNORECASE)),
    ("PostgreSQL TLS version", re.compile(r'ssl_minimal_tls_version_enforced\s*=\s*"TLS1_2"', re.IGNORECASE)),
    ("MySQL SSL", re.compile(r'ssl_enforcement_enabled\s*=\s*true', re.IGNORECASE)),
    ("MySQL TLS version", re.compile(r'tls_version\s*=\s*"TLS1_2"', re.IGNORECASE)),
    ("App Service HTTPS only", re.compile(r'https_only\s*=\s*true', re.IGNORECASE)),
    ("App Service min TLS", re.compile(r'minimum_tls_version\s*=\s*"1\.2"', re.IGNORECASE)),
    ("Redis TLS", re.compile(r'minimum_tls_version\s*=\s*"1\.2"', re.IGNORECASE)),
    ("Redis non-SSL disabled", re.compile(r'enable_non_ssl_port\s*=\s*false', re.IGNORECASE)),
    
    # AWS - Encryption
    ("S3 encryption", re.compile(r'resource\s+"aws_s3_bucket_server_side_encryption_configuration"', re.IGNORECASE)),
    ("S3 bucket encryption", re.compile(r'server_side_encryption_configuration\s*\{', re.IGNORECASE)),
    ("RDS encryption", re.compile(r'storage_encrypted\s*=\s*true', re.IGNORECASE)),
    ("RDS KMS key", re.compile(r'kms_key_id\s*=', re.IGNORECASE)),
    ("EBS encryption", re.compile(r'encrypted\s*=\s*true', re.IGNORECASE)),
    ("KMS key", re.compile(r'resource\s+"aws_kms_key"', re.IGNORECASE)),
    ("EKS secrets encryption", re.compile(r'encryption_config\s*\{', re.IGNORECASE)),
    
    # GCP - Encryption
    ("Disk encryption", re.compile(r'disk_encryption_key\s*\{', re.IGNORECASE)),
    ("KMS crypto key", re.compile(r'resource\s+"google_kms_crypto_key"', re.IGNORECASE)),
    ("Cloud SQL encryption", re.compile(r'encryption_key_name\s*=', re.IGNORECASE)),
    ("GKE database encryption", re.compile(r'database_encryption\s*\{[^}]*state\s*=\s*"ENCRYPTED"', re.IGNORECASE | re.DOTALL)),
    ("Storage bucket encryption", re.compile(r'encryption\s*\{[^}]*default_kms_key_name', re.IGNORECASE | re.DOTALL)),
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


# Regex to extract TargetFramework from .csproj files
TARGET_FRAMEWORK_RE = re.compile(r'<TargetFramework[s]?>([^<]+)</TargetFramework[s]?>', re.IGNORECASE)
# Regex to extract dotnet_version from Terraform
TF_DOTNET_VERSION_RE = re.compile(r'dotnet_version\s*=\s*"([^"]+)"', re.IGNORECASE)


def detect_dotnet_version(files: list[Path], repo: Path) -> dict:
    """Detect .NET version from csproj, global.json, or Terraform.
    
    Returns dict with:
    - version: The detected version (e.g., "net8.0", "v8.0")
    - source: Where it was detected (e.g., "MyProject.csproj", "terraform/app_service.tf")
    - all_versions: List of all versions found (if multiple projects)
    """
    versions: list[tuple[str, str]] = []  # (version, source)
    
    for p in files:
        rp = rel(repo, p)
        
        # Check .csproj files
        if p.suffix.lower() == ".csproj":
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                matches = TARGET_FRAMEWORK_RE.findall(text)
                for m in matches:
                    # Handle multiple targets like "net6.0;net7.0;net8.0"
                    for v in m.split(";"):
                        v = v.strip()
                        if v:
                            versions.append((v, rp))
            except OSError:
                continue
        
        # Check global.json for SDK version
        elif p.name.lower() == "global.json":
            try:
                import json
                data = json.loads(p.read_text(encoding="utf-8"))
                sdk_version = data.get("sdk", {}).get("version")
                if sdk_version:
                    versions.append((f"SDK {sdk_version}", rp))
            except (OSError, json.JSONDecodeError):
                continue
        
        # Check Terraform for dotnet_version
        elif p.suffix.lower() == ".tf":
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                for m in TF_DOTNET_VERSION_RE.finditer(text):
                    versions.append((m.group(1), rp))
            except OSError:
                continue
    
    if not versions:
        return {"version": None, "source": None, "all_versions": []}
    
    # Deduplicate and sort - prefer highest version
    unique_versions = list(set(versions))
    unique_versions.sort(key=lambda x: x[0], reverse=True)
    
    return {
        "version": unique_versions[0][0],
        "source": unique_versions[0][1],
        "all_versions": unique_versions,
    }


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

# Pattern to match Terraform module sources
TF_MODULE_SOURCE_RE = re.compile(
    r'^\s*module\s+"([^"]+)"\s*\{[^}]*?source\s*=\s*"([^"]+)"',
    re.IGNORECASE | re.MULTILINE | re.DOTALL
)


def detect_terraform_module_references(files: list[Path], repo: Path) -> list[dict]:
    """Detect references to other repos via Terraform module sources.
    
    Returns list of:
    {
        "repo_name": "terraform-app_gateway",
        "module_name": "app_gateway",
        "source": "../terraform-app_gateway" or "git::https://...",
        "detected_in_file": "terraform/main.tf",
        "line": 42
    }
    """
    references: list[dict] = []
    seen_repos: set[str] = set()
    
    for p in files:
        if p.suffix.lower() != ".tf":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        rp = rel(repo, p)
        
        for m in TF_MODULE_SOURCE_RE.finditer(text):
            module_name = m.group(1)
            source = m.group(2)
            line = text.count("\n", 0, m.start()) + 1
            
            # Extract repo name from various source formats
            repo_name = None
            
            # Local path: source = "../terraform-app_gateway" or "../../terraform-modules//submodule"
            if source.startswith("../") or source.startswith("./"):
                # Get the first path component after ../
                parts = source.replace("//", "/").split("/")
                for part in parts:
                    if part and part not in (".", ".."):
                        repo_name = part.split("//")[0]  # Handle submodule paths
                        break
            
            # Git URL: source = "git::https://github.com/org/terraform-app_gateway.git"
            elif "git::" in source or source.endswith(".git"):
                # Extract repo name from URL
                git_match = re.search(r'/([^/]+?)(?:\.git)?(?:\?|//|$)', source)
                if git_match:
                    repo_name = git_match.group(1)
            
            # Registry: source = "hashicorp/consul/aws" (skip these - external)
            elif "/" in source and not source.startswith("."):
                # Check if it looks like a local org repo reference
                if re.match(r'^[a-zA-Z0-9_-]+$', source.split("/")[0]):
                    # Could be "org/repo" - take the second part
                    parts = source.split("/")
                    if len(parts) >= 2:
                        repo_name = parts[1]
            
            if repo_name and repo_name not in seen_repos:
                seen_repos.add(repo_name)
                references.append({
                    "repo_name": repo_name,
                    "module_name": module_name,
                    "source": source,
                    "detected_in_file": rp,
                    "line": line,
                })
    
    return references


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


def has_terraform_module_source(files: list[Path], source_pattern: str) -> bool:
    """Return True if any Terraform module block references a source matching source_pattern."""
    source_re = re.compile(source_pattern, re.IGNORECASE)
    for p in files:
        if p.suffix.lower() != ".tf":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in TF_MODULE_SOURCE_RE.finditer(text):
            if source_re.search(m.group(2)):
                return True
    return False


def extract_kubernetes_topology_signals(
    files: list[Path],
    repo: Path,
    include_path_prefixes: list[str] | None = None,
) -> dict[str, list[str]]:
    """Extract Kubernetes ingress/controller/service signals from scoped files."""
    ingress_names: set[str] = set()
    service_names: set[str] = set()
    manifest_secret_names: set[str] = set()
    ingress_classes: set[str] = set()
    controller_hints: set[str] = set()
    lb_hints: set[str] = set()
    evidence_files: set[str] = set()

    ingress_class_re = re.compile(r"ingressClassName:\s*['\"]?([A-Za-z0-9._-]+)['\"]?", re.IGNORECASE)
    aws_lb_type_re = re.compile(r"aws-load-balancer-type:\s*['\"]?([A-Za-z0-9._-]+)['\"]?", re.IGNORECASE)
    aws_lb_scheme_re = re.compile(r"aws-load-balancer-scheme:\s*['\"]?([A-Za-z0-9._-]+)['\"]?", re.IGNORECASE)

    for p in files:
        rp = rel(repo, p)
        if include_path_prefixes and not any(rp.startswith(prefix) for prefix in include_path_prefixes):
            continue
        if p.name.endswith(".sarif"):
            continue
        if p.suffix.lower() not in {".tf", ".yaml", ".yml"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if p.suffix.lower() == ".tf":
            if re.search(r'resource\s+"helm_release"\s+"ingress_nginx"', text, re.IGNORECASE):
                controller_hints.add("ingress-nginx")
                evidence_files.add(rp)
            if re.search(r"aws-load-balancer-controller", text, re.IGNORECASE):
                controller_hints.add("aws-load-balancer-controller")
                evidence_files.add(rp)
            continue

        current_kind = None
        in_metadata = False
        metadata_indent = 0
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))

            if stripped.lower().startswith("kind:"):
                kind_value = stripped.split(":", 1)[1].strip().strip("'\"")
                if kind_value.lower() in {"ingress", "service", "secret"}:
                    current_kind = kind_value.lower()
                else:
                    current_kind = None
                in_metadata = False
                continue

            if current_kind and stripped.lower().startswith("metadata:"):
                in_metadata = True
                metadata_indent = indent
                continue

            if in_metadata:
                if indent <= metadata_indent and ":" in stripped:
                    in_metadata = False
                elif stripped.lower().startswith("name:"):
                    name_value = stripped.split(":", 1)[1].strip().strip("'\"")
                    if name_value:
                        if current_kind == "ingress":
                            ingress_names.add(name_value)
                        elif current_kind == "service":
                            service_names.add(name_value)
                        elif current_kind == "secret":
                            manifest_secret_names.add(name_value)
                        evidence_files.add(rp)
                    in_metadata = False

        for m in ingress_class_re.finditer(text):
            ingress_classes.add(m.group(1))
            evidence_files.add(rp)
        for m in aws_lb_type_re.finditer(text):
            lb_hints.add(f"AWS LB type: {m.group(1)}")
            evidence_files.add(rp)
        for m in aws_lb_scheme_re.finditer(text):
            lb_hints.add(f"AWS LB scheme: {m.group(1)}")
            evidence_files.add(rp)
        if "ingress-nginx" in text.lower():
            controller_hints.add("ingress-nginx")
            evidence_files.add(rp)

    return {
        "ingress_names": sorted(ingress_names),
        "service_names": sorted(service_names),
        "manifest_secret_names": sorted(manifest_secret_names),
        "ingress_classes": sorted(ingress_classes),
        "controller_hints": sorted(controller_hints),
        "lb_hints": sorted(lb_hints),
        "evidence_files": sorted(evidence_files),
    }


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


def ensure_repos_knowledge(repos_root: Path, knowledge_dir: Path | None = None) -> Path:
    kdir = knowledge_dir if knowledge_dir else OUTPUT_KNOWLEDGE_DIR
    kdir.mkdir(parents=True, exist_ok=True)
    path = kdir / "Repos.md"
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
    # Avoid duplicate entries when purpose/language detection changes between runs.
    # If an entry for this repo already exists, drop it and re-insert the new canonical line.
    existing_repo_prefix = f"- **{repo_name}** -"
    lines = [l for l in text.splitlines() if not l.strip().startswith(existing_repo_prefix)]
    text = "\n".join(lines)
    if entry_line in text:
        repos_md.write_text(text.rstrip() + "\n", encoding="utf-8")
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


def classify_terraform_resources(tf_resource_types: set[str], provider: str) -> dict:
    """Classify ALL detected Terraform resources by category for comprehensive architecture diagrams."""
    categories = {
        "compute": [],
        "database": [],
        "storage": [],
        "networking": [],
        "identity": [],
        "security": [],
        "monitoring": [],
    }
    
    provider_lower = provider.lower()
    
    if provider_lower == "azure":
        for rt in tf_resource_types:
            if rt in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine",
                      "azurerm_linux_web_app", "azurerm_windows_web_app", "azurerm_app_service",
                      "azurerm_kubernetes_cluster", "azurerm_container_app", "azurerm_function_app"}:
                categories["compute"].append(rt)
            elif rt in {"azurerm_mssql_server", "azurerm_mssql_database", "azurerm_sql_server", "azurerm_sql_database",
                        "azurerm_mysql_server", "azurerm_postgresql_server", "azurerm_cosmosdb_account"}:
                categories["database"].append(rt)
            elif rt in {"azurerm_storage_account", "azurerm_storage_container", "azurerm_storage_blob",
                        "azurerm_storage_share", "azurerm_storage_queue"}:
                categories["storage"].append(rt)
            elif rt in {"azurerm_virtual_network", "azurerm_subnet", "azurerm_network_security_group",
                        "azurerm_application_gateway", "azurerm_frontdoor", "azurerm_load_balancer",
                        "azurerm_public_ip", "azurerm_private_endpoint"}:
                categories["networking"].append(rt)
            elif rt in {"azurerm_key_vault", "azurerm_key_vault_secret", "azurerm_user_assigned_identity",
                        "azurerm_role_assignment"}:
                categories["identity"].append(rt)
            elif rt in {"azurerm_firewall", "azurerm_web_application_firewall_policy", "azurerm_security_center_subscription_pricing"}:
                categories["security"].append(rt)
            elif rt in {"azurerm_application_insights", "azurerm_log_analytics_workspace", "azurerm_monitor_diagnostic_setting"}:
                categories["monitoring"].append(rt)
    
    elif provider_lower == "aws":
        for rt in tf_resource_types:
            if rt in {"aws_instance", "aws_ecs_cluster", "aws_ecs_service", "aws_eks_cluster", "aws_lambda_function"}:
                categories["compute"].append(rt)
            elif rt in {"aws_db_instance", "aws_rds_cluster", "aws_dynamodb_table"}:
                categories["database"].append(rt)
            elif rt in {"aws_s3_bucket", "aws_s3_object", "aws_efs_file_system"}:
                categories["storage"].append(rt)
            elif rt in {"aws_vpc", "aws_subnet", "aws_security_group", "aws_alb", "aws_elb", "aws_cloudfront_distribution"}:
                categories["networking"].append(rt)
            elif rt in {"aws_iam_role", "aws_iam_policy", "aws_kms_key", "aws_secretsmanager_secret"}:
                categories["identity"].append(rt)
            elif rt in {"aws_wafv2_web_acl", "aws_guardduty_detector"}:
                categories["security"].append(rt)
            elif rt in {"aws_cloudwatch_log_group", "aws_cloudwatch_metric_alarm"}:
                categories["monitoring"].append(rt)
    
    elif provider_lower == "gcp":
        for rt in tf_resource_types:
            if rt in {"google_compute_instance", "google_container_cluster", "google_cloud_run_service", "google_cloudfunctions_function"}:
                categories["compute"].append(rt)
            elif rt in {"google_sql_database_instance", "google_bigtable_instance", "google_firestore_database"}:
                categories["database"].append(rt)
            elif rt in {"google_storage_bucket", "google_storage_bucket_object"}:
                categories["storage"].append(rt)
            elif rt in {"google_compute_network", "google_compute_subnetwork", "google_compute_firewall", "google_compute_address"}:
                categories["networking"].append(rt)
            elif rt in {"google_service_account", "google_project_iam_binding", "google_kms_crypto_key"}:
                categories["identity"].append(rt)
            elif rt in {"google_compute_security_policy"}:
                categories["security"].append(rt)
            elif rt in {"google_logging_project_sink", "google_monitoring_alert_policy"}:
                categories["monitoring"].append(rt)
    
    return categories


def detect_cross_cloud_connectivity(files: list[Path], repo: Path, providers: list[str]) -> dict:
    """Detect actual cross-cloud and cross-subscription connectivity patterns from IaC."""
    connectivity = {
        "vpn_tunnels": [],           # VPN connections between clouds/subscriptions
        "private_links": [],          # ExpressRoute, Direct Connect, Cloud Interconnect
        "vnet_peering": [],          # Cross-subscription VNet peering
        "federated_identity": [],    # SAML, OIDC, trust relationships
        "shared_secrets": [],        # Cross-cloud Key Vault/KMS access
        "cross_cloud_data": [],      # Data replication, backup across clouds
        "service_principals": [],    # Cross-cloud service principals
    }
    
    for p in files:
        if not p.suffix.lower() in {".tf", ".tfvars", ".json", ".yaml", ".yml"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # VPN Gateway connections (Azure)
        if re.search(r'resource\s+"azurerm_virtual_network_gateway"', text):
            connectivity["vpn_tunnels"].append("Azure VPN Gateway detected")
        if re.search(r'resource\s+"azurerm_virtual_network_gateway_connection"', text):
            # Try to extract connection type
            conn_type_match = re.search(r'type\s+=\s+"([^"]+)"', text)
            if conn_type_match:
                conn_type = conn_type_match.group(1)
                connectivity["vpn_tunnels"].append(f"Azure VPN Connection ({conn_type})")
        
        # ExpressRoute (Azure)
        if re.search(r'resource\s+"azurerm_express_route_circuit"', text):
            connectivity["private_links"].append("Azure ExpressRoute Circuit")
        
        # AWS VPN
        if re.search(r'resource\s+"aws_vpn_gateway"|resource\s+"aws_vpn_connection"', text):
            connectivity["vpn_tunnels"].append("AWS VPN Gateway")
        
        # AWS Direct Connect
        if re.search(r'resource\s+"aws_dx_gateway"|resource\s+"aws_dx_connection"', text):
            connectivity["private_links"].append("AWS Direct Connect")
        
        # GCP VPN
        if re.search(r'resource\s+"google_compute_vpn_gateway"|resource\s+"google_compute_vpn_tunnel"', text):
            connectivity["vpn_tunnels"].append("GCP VPN Tunnel")
        
        # GCP Cloud Interconnect
        if re.search(r'resource\s+"google_compute_interconnect_attachment"', text):
            connectivity["private_links"].append("GCP Cloud Interconnect")
        
        # VNet Peering (cross-subscription detection)
        if re.search(r'resource\s+"azurerm_virtual_network_peering"', text):
            # Check if remote_virtual_network_id references different subscription
            peering_match = re.search(r'remote_virtual_network_id\s+=\s+"([^"]+)"', text)
            if peering_match:
                remote_id = peering_match.group(1)
                if "/subscriptions/" in remote_id:
                    connectivity["vnet_peering"].append("Cross-subscription VNet Peering detected")
                else:
                    connectivity["vnet_peering"].append("VNet Peering (same subscription)")
        
        # AWS VPC Peering
        if re.search(r'resource\s+"aws_vpc_peering_connection"', text):
            # Check if peer_owner_id is different (cross-account)
            if re.search(r'peer_owner_id\s+=', text):
                connectivity["vnet_peering"].append("Cross-account VPC Peering")
            else:
                connectivity["vnet_peering"].append("VPC Peering (same account)")
        
        # Federated Identity - Azure AD SAML/OIDC
        if re.search(r'resource\s+"azuread_service_principal"|resource\s+"azuread_application"', text):
            connectivity["federated_identity"].append("Azure AD Service Principal")
        if re.search(r'identifier_uris|reply_urls|web.*redirect_uris', text):
            connectivity["federated_identity"].append("Azure AD App Registration (OIDC/SAML)")
        
        # AWS IAM Identity Provider (SAML, OIDC)
        if re.search(r'resource\s+"aws_iam_saml_provider"|resource\s+"aws_iam_openid_connect_provider"', text):
            connectivity["federated_identity"].append("AWS IAM Identity Provider (Federation)")
        
        # Cross-cloud service principals
        if re.search(r'AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY', text) and "azure" in providers:
            connectivity["service_principals"].append("Azure → AWS credentials detected")
        if re.search(r'ARM_CLIENT_ID|ARM_CLIENT_SECRET|ARM_SUBSCRIPTION_ID', text) and "aws" in providers:
            connectivity["service_principals"].append("AWS → Azure credentials detected")
        
        # Shared Key Vault access (cross-subscription)
        vault_match = re.search(r'data\s+"azurerm_key_vault"[^}]*vault_uri\s+=\s+"([^"]+)"', text, re.DOTALL)
        if vault_match:
            vault_uri = vault_match.group(1)
            if "/subscriptions/" in vault_uri:
                connectivity["shared_secrets"].append(f"Cross-subscription Key Vault access: {vault_uri}")
        
        # AWS Secrets Manager cross-region/cross-account
        if re.search(r'data\s+"aws_secretsmanager_secret"', text):
            # Check if ARN references different account
            arn_match = re.search(r'arn:aws:secretsmanager:[^:]+:(\d+):', text)
            if arn_match:
                connectivity["shared_secrets"].append("Cross-account AWS Secrets Manager access")
        
        # Cross-cloud data replication
        if re.search(r'geo_redundant_backup|geo_replication', text):
            connectivity["cross_cloud_data"].append("Geo-redundant backup/replication enabled")
        if re.search(r'resource\s+"azurerm_storage_account"[^}]*replication_type\s+=\s+"GRS|RAGRS"', text, re.DOTALL):
            connectivity["cross_cloud_data"].append("Azure Storage GRS (geo-replication)")
        if re.search(r'resource\s+"aws_s3_bucket_replication_configuration"', text):
            connectivity["cross_cloud_data"].append("AWS S3 cross-region replication")
    
    return connectivity


def extract_resource_names_with_property(files: list[Path], repo: Path, resource_type: str, property_name: str = "name") -> list[str]:
    """Extract actual resource names from the 'name' property (or other property) rather than Terraform identifiers."""
    names = []
    pattern = re.compile(
        rf'resource\s+"{re.escape(resource_type)}"\s+"[^"]+"\s*\{{([^}}]+)\}}',
        re.DOTALL | re.IGNORECASE
    )
    name_pattern = re.compile(rf'{property_name}\s*=\s*"([^"]+)"', re.IGNORECASE)
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        for match in pattern.finditer(text):
            resource_block = match.group(1)
            name_match = name_pattern.search(resource_block)
            if name_match:
                names.append(name_match.group(1))
    
    return names


def extract_resource_names(files: list[Path], repo: Path, resource_type: str) -> list[str]:
    """Extract individual resource names from Terraform for specific resource types."""
    names = []
    pattern = re.compile(rf'resource\s+"{re.escape(resource_type)}"\s+"([^"]+)"', re.IGNORECASE)
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in names:
                names.append(name)
    
    return names


def check_os_support_status(os_string: str) -> tuple[str, str]:
    """Check if OS version is still in support.
    Returns (status, eol_date) where status is 'supported', 'eol', or 'unknown'."""
    from datetime import datetime
    
    # Current date for comparison
    now = datetime.now()
    
    # Known EOL dates (format: YYYY-MM-DD)
    eol_dates = {
        # Ubuntu LTS releases
        "Ubuntu 14.04": ("2019-04-30", "eol"),
        "Ubuntu 16.04": ("2021-04-30", "eol"),
        "Ubuntu 18.04": ("2023-05-31", "eol"),
        "Ubuntu 20.04": ("2025-04-30", "supported"),
        "Ubuntu 22.04": ("2027-04-30", "supported"),
        "Ubuntu 24.04": ("2029-04-30", "supported"),
        
        # Windows Server
        "Windows Server 2008": ("2020-01-14", "eol"),
        "Windows Server 2012": ("2023-10-10", "eol"),
        "Windows Server 2016": ("2027-01-11", "supported"),
        "Windows Server 2019": ("2029-01-09", "supported"),
        "Windows Server 2022": ("2031-10-13", "supported"),
        
        # Windows Client
        "Windows 10": ("2025-10-14", "supported"),
        "Windows 11": ("2026-10-10", "supported"),
        
        # CentOS
        "CentOS 6": ("2020-11-30", "eol"),
        "CentOS 7": ("2024-06-30", "eol"),
        "CentOS 8": ("2021-12-31", "eol"),
        
        # RHEL
        "RHEL 6": ("2024-06-30", "eol"),
        "RHEL 7": ("2024-06-30", "eol"),
        "RHEL 8": ("2029-05-31", "supported"),
        "RHEL 9": ("2032-05-31", "supported"),
    }
    
    for os_name, (eol_date_str, status) in eol_dates.items():
        if os_name in os_string:
            eol_date = datetime.strptime(eol_date_str, "%Y-%m-%d")
            if now > eol_date:
                return ("eol", eol_date_str)
            else:
                return ("supported", eol_date_str)
    
    return ("unknown", "")


def extract_vm_names_with_os(files: list[Path], repo: Path, provider: str) -> list[tuple[str, str, str]]:
    """Extract VM names with OS type and role. Returns list of (name, os, role) tuples.
    Role can be: 'Bastion', 'Jumpbox', or '' for regular VMs."""
    vms = []
    
    # Keywords to identify bastion/jumpbox VMs
    bastion_keywords = ['bastion', 'jump', 'jumpbox', 'jump-box', 'jumper']
    
    if provider == "azure":
        # Parse each Linux VM to get detailed OS info
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            # Find Linux VMs with their image details
            linux_vm_pattern = re.compile(
                r'resource\s+"azurerm_linux_virtual_machine"\s+"([^"]+)".*?source_image_reference\s*\{[^}]*offer\s*=\s*"([^"]+)"[^}]*sku\s*=\s*"([^"]+)"',
                re.DOTALL | re.IGNORECASE
            )
            for match in linux_vm_pattern.finditer(text):
                vm_name = match.group(1)
                offer = match.group(2)
                sku = match.group(3)
                
                # Detect role (bastion/jumpbox)
                role = ''
                vm_name_lower = vm_name.lower()
                if any(keyword in vm_name_lower for keyword in bastion_keywords):
                    if 'bastion' in vm_name_lower:
                        role = 'Bastion'
                    else:
                        role = 'Jumpbox'
                
                # Parse common offers to friendly OS names
                if "ubuntu" in offer.lower():
                    if "20_04" in sku or "20.04" in sku:
                        os_name = "OS: Ubuntu 20.04"
                    elif "22_04" in sku or "22.04" in sku:
                        os_name = "OS: Ubuntu 22.04"
                    elif "18_04" in sku or "18.04" in sku:
                        os_name = "OS: Ubuntu 18.04"
                    else:
                        os_name = "OS: Ubuntu"
                elif "centos" in offer.lower():
                    if "7" in sku:
                        os_name = "OS: CentOS 7"
                    elif "8" in sku:
                        os_name = "OS: CentOS 8"
                    else:
                        os_name = "OS: CentOS"
                elif "rhel" in offer.lower() or "red-hat" in offer.lower():
                    os_name = f"OS: RHEL {sku.split('_')[0] if '_' in sku else ''}"
                else:
                    os_name = "OS: Linux"
                
                vms.append((vm_name, os_name, role))
            
            # Find Windows VMs
            windows_vm_pattern = re.compile(
                r'resource\s+"azurerm_windows_virtual_machine"\s+"([^"]+)".*?source_image_reference\s*\{[^}]*offer\s*=\s*"([^"]+)"[^}]*sku\s*=\s*"([^"]+)"',
                re.DOTALL | re.IGNORECASE
            )
            for match in windows_vm_pattern.finditer(text):
                vm_name = match.group(1)
                offer = match.group(2)
                sku = match.group(3)
                
                # Detect role (bastion/jumpbox)
                role = ''
                vm_name_lower = vm_name.lower()
                if any(keyword in vm_name_lower for keyword in bastion_keywords):
                    if 'bastion' in vm_name_lower:
                        role = 'Bastion'
                    else:
                        role = 'Jumpbox'
                
                # Parse Windows SKUs
                if "2022" in sku:
                    os_name = "OS: Windows Server 2022"
                elif "2019" in sku:
                    os_name = "OS: Windows Server 2019"
                elif "2016" in sku:
                    os_name = "OS: Windows Server 2016"
                elif "win11" in sku.lower() or "windows-11" in sku.lower():
                    os_name = "OS: Windows 11"
                elif "win10" in sku.lower() or "windows-10" in sku.lower():
                    os_name = "OS: Windows 10"
                else:
                    os_name = "OS: Windows"
                
                vms.append((vm_name, os_name, role))
            
            # Generic VMs without detailed image info
            generic_pattern = re.compile(r'resource\s+"azurerm_virtual_machine"\s+"([^"]+)"', re.IGNORECASE)
            for match in generic_pattern.finditer(text):
                vm_name = match.group(1)
                if vm_name not in [v[0] for v in vms]:
                    role = ''
                    vm_name_lower = vm_name.lower()
                    if any(keyword in vm_name_lower for keyword in bastion_keywords):
                        role = 'Bastion' if 'bastion' in vm_name_lower else 'Jumpbox'
                    vms.append((vm_name, "OS: Unknown", role))
    
    elif provider == "aws":
        # AWS instances - try to detect from AMI
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            instance_pattern = re.compile(r'resource\s+"aws_instance"\s+"([^"]+)"', re.IGNORECASE)
            for match in instance_pattern.finditer(text):
                name = match.group(1)
                
                # Detect role
                role = ''
                name_lower = name.lower()
                if any(keyword in name_lower for keyword in bastion_keywords):
                    role = 'Bastion' if 'bastion' in name_lower else 'Jumpbox'
                
                # Try to infer OS from AMI or tags
                if "ubuntu" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Ubuntu"
                elif "amazon-linux" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Amazon Linux"
                elif "windows" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Windows"
                else:
                    os_name = "OS: Linux"
                vms.append((name, os_name, role))
    
    elif provider == "gcp":
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            instance_pattern = re.compile(r'resource\s+"google_compute_instance"\s+"([^"]+)"', re.IGNORECASE)
            for match in instance_pattern.finditer(text):
                name = match.group(1)
                
                # Detect role
                role = ''
                name_lower = name.lower()
                if any(keyword in name_lower for keyword in bastion_keywords):
                    role = 'Bastion' if 'bastion' in name_lower else 'Jumpbox'
                
                # Try to infer from image family
                if "ubuntu" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Ubuntu"
                elif "debian" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Debian"
                elif "windows" in text[match.start():match.end()+500].lower():
                    os_name = "OS: Windows"
                else:
                    os_name = "OS: Linux"
                vms.append((name, os_name, role))
    
    return vms


def extract_nsg_associations(files: list[Path], repo: Path, provider: str, vm_names: list[tuple[str, str, str]]) -> dict[str, bool]:
    """Detect which resources have NSG/Security Group associations. Returns dict of {resource_name: has_nsg}."""
    associations = {}
    
    # Initialize all VMs as not having NSG
    for vm_name, _, _ in vm_names:
        associations[vm_name] = False
    
    if provider == "azure":
        # Look for azurerm_network_interface_security_group_association resources
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            # Find all network_interface_id lines within association resources
            lines = text.split('\n')
            in_nsg_association = False
            
            for line in lines:
                # Check if we're entering an NSG association resource
                if 'azurerm_network_interface_security_group_association' in line and 'resource' in line:
                    in_nsg_association = True
                    continue
                
                # If we're in an association block, look for network_interface_id
                if in_nsg_association and 'network_interface_id' in line:
                    # Extract NIC name from path like: networkInterfaces/rocinante796"
                    nic_match = re.search(r'networkInterfaces/([^"]+)', line)
                    if nic_match:
                        nic_name = nic_match.group(1)
                        print(f"DEBUG: Found NSG association with NIC: {nic_name}")
                        
                        # Match NIC name to VM name
                        for vm_name, _, _ in vm_names:
                            if vm_name.lower() in nic_name.lower():
                                associations[vm_name] = True
                                print(f"DEBUG: Matched NSG to VM: {vm_name}")
                                break
                
                # Exit association block when we hit closing brace at start of line
                if in_nsg_association and line.strip().startswith('}'):
                    in_nsg_association = False
    
    elif provider == "aws":
        # AWS security groups are typically defined in the instance resource itself
        # Look for instances with security_groups or vpc_security_group_ids
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            # Find instances with security groups
            instance_blocks = re.finditer(r'resource\s+"aws_instance"\s+"([^"]+)"([^}]+)', text, re.DOTALL)
            for block_match in instance_blocks:
                instance_name = block_match.group(1)
                instance_config = block_match.group(2)
                
                if 'vpc_security_group_ids' in instance_config or 'security_groups' in instance_config:
                    if instance_name in [vm[0] for vm in vm_names]:
                        associations[instance_name] = True
    
    elif provider == "gcp":
        # GCP firewall rules are network-wide, not per-instance
        # Instances can have network tags that match firewall rules
        # For simplicity, if any firewall exists, assume instances are protected
        has_firewall = False
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            if re.search(r'resource\s+"google_compute_firewall"', text):
                has_firewall = True
                break
        
        # If firewall exists, mark all instances as potentially protected
        if has_firewall:
            for vm_name, _ in vm_names:
                associations[vm_name] = True
    
    return associations


def detect_terraform_backend(files: list[Path], repo: Path) -> dict[str, str]:
    """Detect Terraform backend configuration. Returns dict with 'type' and 'storage_resource' if applicable."""
    backend_info = {"type": "local", "storage_resource": None}
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Look for backend block in terraform configuration
        backend_match = re.search(r'backend\s+"([^"]+)"\s*\{', text, re.IGNORECASE)
        if backend_match:
            backend_type = backend_match.group(1).lower()
            backend_info["type"] = backend_type
            
            # If it's azurerm (Azure Storage), note that
            if backend_type == "azurerm":
                backend_info["storage_resource"] = "azurerm_storage_account"
            elif backend_type == "s3":
                backend_info["storage_resource"] = "aws_s3_bucket"
            elif backend_type == "gcs":
                backend_info["storage_resource"] = "google_storage_bucket"
            
            return backend_info
    
    return backend_info


def _find_resource_location(files: list[Path], repo: Path, resource_name: str, resource_type: str) -> tuple[str, int, int]:
    """Find the source file and line numbers for a resource.
    
    Returns (relative_file_path, start_line, end_line). Returns (None, None, None) if not found.
    """
    for p in files:
        if not p.suffix.lower() in {".tf", ".yaml", ".yml", ".json"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        lines = text.split('\n')
        for i, line in enumerate(lines):
            # Check for resource definition containing both type and name
            if resource_type in line and resource_name in line and ('resource' in line or 'name:' in line):
                # Found the resource, now find the end of the block
                start_line = i + 1  # Line numbers are 1-indexed
                end_line = start_line
                
                # Simple heuristic: find next blank line or next resource definition
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() == "" or (('resource' in lines[j] or 'kind:' in lines[j]) and j > i + 5):
                        end_line = j
                        break
                else:
                    end_line = len(lines)
                
                relative_path = str(p.relative_to(repo))
                return (relative_path, start_line, min(end_line, start_line + 100))  # Cap at 100 lines
    
    return (None, None, None)


def write_cloud_resource_summaries(
    *,
    repo: Path,
    provider: str,
    summary_dir: Path,
) -> list[Path]:
    """Generate detailed per-resource summaries for key compute resources (VMs, AKS clusters).
    
    Returns list of generated file paths.
    """
    generated_files = []
    files = iter_files(repo)
    provider_key = provider.lower()
    provider_folder = {"aws": "AWS", "gcp": "GCP", "azure": "Azure"}.get(provider_key, provider.title())
    provider_summary_dir = summary_dir / provider_folder
    provider_summary_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract experiment ID for database (summary_dir is .../Summary/Cloud or .../Summary)
    experiment_id = None
    repo_name = repo.name
    
    # Navigate up to find experiment folder: .../experiments/008_Name/Summary/...
    path_parts = summary_dir.parts
    for i, part in enumerate(path_parts):
        if part == "experiments" and i + 1 < len(path_parts):
            exp_folder = path_parts[i + 1]
            # Extract numeric prefix (e.g., "008_Resource_Test" -> "008")
            if '_' in exp_folder:
                exp_num = exp_folder.split('_')[0]
                if exp_num.isdigit():
                    experiment_id = exp_num
                    break
            elif exp_folder.isdigit():
                experiment_id = exp_folder
                break
    
    if provider.lower() == "azure":
        # Extract VMs with OS info
        vm_list = extract_vm_names_with_os(files, repo, "azure")
        nsg_associations = extract_nsg_associations(files, repo, "azure", vm_list)
        
        # Extract AKS clusters
        aks_clusters = extract_resource_names_with_property(files, repo, "azurerm_kubernetes_cluster", "name")
        
        # Process each VM
        for vm_name, os_info, role in vm_list:
            summary_lines = []
            summary_lines.append(f"# Resource Summary: {vm_name}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Virtual Machine")
            if role:
                summary_lines.append(f"**Role:** {role}")
            summary_lines.append(f"**Operating System:** {os_info}")
            summary_lines.append("")
            
            # Find the VM resource block for detailed analysis
            vm_config = _extract_vm_configuration(files, repo, vm_name)
            
            # Detect connections to PaaS services
            paas_connections = _detect_vm_paas_connections(files, repo, vm_name)
            
            # Add resource connection diagram - show full VNet context with this VM highlighted
            summary_lines.append("## 🗺️ Architecture Context")
            summary_lines.append("")
            summary_lines.append("```mermaid")
            summary_lines.append("flowchart TB")
            summary_lines.append("  Internet[Internet]")
            summary_lines.append("  subgraph vnet[VNet: Space-vnet]")
            
            # Show all VMs with current one highlighted
            for other_vm, other_os, other_role in vm_list:
                if other_vm == vm_name:
                    summary_lines.append(f"    VM_{other_vm}[**{other_vm} VM** ⭐<br/>{other_os.replace('OS: ', '')}]")
                else:
                    summary_lines.append(f"    VM_{other_vm}[{other_vm} VM<br/>{other_os.replace('OS: ', '')}]")
            
            # Show AKS if exists
            aks_clusters_list = []
            for p in iter_files(repo):
                if p.suffix.lower() == ".tf":
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                        for line in content.split('\n'):
                            if 'resource' in line and 'azurerm_kubernetes_cluster' in line:
                                match = re.search(r'resource\s+"azurerm_kubernetes_cluster"\s+"([^"]+)"', line)
                                if match and match.group(1) not in aks_clusters_list:
                                    aks_clusters_list.append(match.group(1))
                    except Exception:
                        pass
            for aks in aks_clusters_list:
                summary_lines.append(f"    AKS_{aks}[{aks} AKS]")
            
            summary_lines.append("    NSG[Network Security Group]")
            summary_lines.append("  end")
            
            # Show PaaS services
            summary_lines.append("  subgraph paas[Azure PaaS]")
            summary_lines.append("    App[App Service]")
            if paas_connections.get("sql_databases"):
                summary_lines.append("    SQL[Azure SQL]")
            if paas_connections.get("key_vaults"):
                summary_lines.append("    KV[Key Vault]")
            if paas_connections.get("storage_accounts"):
                summary_lines.append("    Storage[Storage Account]")
            summary_lines.append("  end")
            summary_lines.append("")
            
            # Internet connections
            summary_lines.append("  Internet -->|HTTPS| App")
            for aks in aks_clusters_list:
                summary_lines.append(f"  Internet --> AKS_{aks}")
            if paas_connections.get("sql_databases"):
                summary_lines.append("  Internet --> SQL")
            if paas_connections.get("key_vaults"):
                summary_lines.append("  Internet --> KV")
            if paas_connections.get("storage_accounts"):
                summary_lines.append("  Internet --> Storage")
            summary_lines.append("  Internet -->|All Ports| NSG")
            summary_lines.append("")
            
            # NSG to VMs
            for other_vm, _, _ in vm_list:
                summary_lines.append(f"  NSG --> VM_{other_vm}")
            summary_lines.append("")
            
            # Current VM's connections to PaaS (emphasized)
            if paas_connections.get("sql_databases"):
                summary_lines.append(f"  VM_{vm_name} -->|Managed Identity| SQL")
            if paas_connections.get("key_vaults"):
                summary_lines.append(f"  VM_{vm_name} -->|Managed Identity| KV")
            if paas_connections.get("storage_accounts"):
                summary_lines.append(f"  VM_{vm_name} --> Storage")
            summary_lines.append("")
            
            # Other VMs also connect (lighter)
            for other_vm, _, _ in vm_list:
                if other_vm != vm_name:
                    if paas_connections.get("sql_databases"):
                        summary_lines.append(f"  VM_{other_vm} -.-> SQL")
                    if paas_connections.get("key_vaults"):
                        summary_lines.append(f"  VM_{other_vm} -.-> KV")
                    if paas_connections.get("storage_accounts"):
                        summary_lines.append(f"  VM_{other_vm} -.-> Storage")
            summary_lines.append("")
            
            # App and AKS also connect
            if paas_connections.get("sql_databases"):
                summary_lines.append("  App -.-> SQL")
            if paas_connections.get("key_vaults"):
                summary_lines.append("  App -.-> KV")
            for aks in aks_clusters_list:
                if paas_connections.get("sql_databases"):
                    summary_lines.append(f"  AKS_{aks} -.-> SQL")
                if paas_connections.get("key_vaults"):
                    summary_lines.append(f"  AKS_{aks} -.-> KV")
            
            summary_lines.append("")
            # Styling - highlight current VM
            summary_lines.append(f"  style VM_{vm_name} stroke:#ff0000,stroke-width:4px,fill:#ffe6e6")
            summary_lines.append("  style Internet stroke:#ff0000,stroke-width:2px")
            summary_lines.append("  style NSG stroke:#ff6b6b,stroke-width:2px")
            if paas_connections.get("key_vaults"):
                summary_lines.append("  style KV stroke:#f59f00,stroke-width:2px")
            if paas_connections.get("sql_databases"):
                summary_lines.append("  style SQL stroke:#00aa00,stroke-width:2px")
            if paas_connections.get("storage_accounts"):
                summary_lines.append("  style Storage stroke:#00aa00,stroke-width:2px")
            summary_lines.append("```")
            summary_lines.append("")
            summary_lines.append("*This VM is highlighted (⭐) in the context of the full Azure environment.*")
            summary_lines.append("")
            
            summary_lines.append("## 📋 Resource Configuration")
            summary_lines.append("")
            if vm_config.get("size"):
                summary_lines.append(f"- **VM Size:** {vm_config['size']}")
            if vm_config.get("location"):
                summary_lines.append(f"- **Region:** {vm_config['location']}")
            if vm_config.get("subnet"):
                summary_lines.append(f"- **Subnet:** {vm_config['subnet']}")
            if vm_config.get("public_ip"):
                summary_lines.append(f"- **Public IP:** {vm_config['public_ip']}")
            else:
                summary_lines.append(f"- **Public IP:** None detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔐 Identity & Access")
            summary_lines.append("")
            if vm_config.get("managed_identity"):
                summary_lines.append(f"- **Managed Identity:** {vm_config['managed_identity']}")
            else:
                summary_lines.append("- **Managed Identity:** None detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔗 Dependencies")
            summary_lines.append("")
            
            # NSG protection
            if vm_name in nsg_associations and nsg_associations[vm_name]:
                summary_lines.append(f"- **NSG Protection:** ✅ Protected by Network Security Group")
            else:
                summary_lines.append(f"- **NSG Protection:** ❌ No NSG association detected")
            
            # PaaS connections (already extracted above for diagram)
            if paas_connections.get("key_vaults"):
                summary_lines.append(f"- **Key Vault Access:** {', '.join(paas_connections['key_vaults'])}")
            if paas_connections.get("storage_accounts"):
                summary_lines.append(f"- **Storage Account Access:** {', '.join(paas_connections['storage_accounts'])}")
            if paas_connections.get("sql_databases"):
                summary_lines.append(f"- **SQL Database Access:** {', '.join(paas_connections['sql_databases'])}")
            
            summary_lines.append("")
            
            # Add critical security issues section first
            summary_lines.append("## 🔥 Critical Security Issues")
            summary_lines.append("")
            
            critical_issues = []
            
            # Check for EOL OS - CRITICAL
            os_status = check_os_support_status(os_info)
            if os_status:
                status_type, eol_date = os_status
                if status_type == "eol":
                    critical_issues.append(f"🔴 **UNSUPPORTED OPERATING SYSTEM** - Reached EOL on {eol_date}")
                    critical_issues.append("  - **Risk:** No security patches, vulnerable to known exploits")
                elif status_type == "approaching_eol":
                    critical_issues.append(f"🟠 **OS APPROACHING EOL** - Will reach EOL on {eol_date}")
                    critical_issues.append("  - **Risk:** Plan upgrade soon to avoid security vulnerabilities")
            
            # Check for missing NSG - CRITICAL
            if vm_name not in nsg_associations or not nsg_associations[vm_name]:
                critical_issues.append("🔴 **NO NETWORK SECURITY GROUP** - All ports potentially accessible")
                critical_issues.append("  - **Risk:** Attacker can scan and exploit any open port")
            else:
                # Analyze NSG rules for overly permissive configurations
                nsg_issues = _analyze_nsg_rules(files, repo, vm_name)
                if nsg_issues:
                    for issue in nsg_issues:
                        critical_issues.append(f"🟠 **PERMISSIVE NSG RULE** - {issue}")
            
            # Check for public IP exposure
            if vm_config.get("public_ip"):
                critical_issues.append("🟠 **PUBLIC IP ASSIGNED** - VM directly accessible from internet")
                critical_issues.append("  - **Risk:** Increased attack surface, direct exposure to brute force")
            
            if critical_issues:
                for issue in critical_issues:
                    summary_lines.append(f"- {issue}")
            else:
                summary_lines.append("✅ No critical security issues detected")
            
            summary_lines.append("")
            
            summary_lines.append("## 🛡️ Security Posture Details")
            summary_lines.append("")
            
            # OS status
            if os_status:
                status_type, eol_date = os_status
                if status_type == "eol":
                    summary_lines.append(f"- **OS Support Status:** 🔴 Unsupported (EOL {eol_date})")
                elif status_type == "approaching_eol":
                    summary_lines.append(f"- **OS Support Status:** 🟠 Approaching EOL ({eol_date})")
                else:
                    summary_lines.append(f"- **OS Support Status:** ✅ Supported until {eol_date}")
            else:
                summary_lines.append(f"- **OS Support Status:** ✅ Supported")
            
            # Public exposure
            if vm_config.get("public_ip"):
                summary_lines.append(f"- **Internet Exposure:** 🔴 Public IP assigned")
            else:
                summary_lines.append(f"- **Internet Exposure:** ✅ No public IP - internal only")
            
            # NSG coverage
            if vm_name in nsg_associations and nsg_associations[vm_name]:
                summary_lines.append(f"- **Network Security:** ✅ NSG rules applied")
            else:
                summary_lines.append(f"- **Network Security:** 🔴 No NSG protection")
            
            summary_lines.append("")
            
            summary_lines.append("## 💥 Blast Radius Analysis")
            summary_lines.append("")
            summary_lines.append("**If this resource is compromised, an attacker could:**")
            summary_lines.append("")
            
            blast_radius = []
            if paas_connections.get("key_vaults"):
                blast_radius.append(f"- Access secrets from: {', '.join(paas_connections['key_vaults'])}")
            if paas_connections.get("storage_accounts"):
                blast_radius.append(f"- Read/write data in: {', '.join(paas_connections['storage_accounts'])}")
            if paas_connections.get("sql_databases"):
                blast_radius.append(f"- Query/modify databases: {', '.join(paas_connections['sql_databases'])}")
            if vm_config.get("managed_identity"):
                blast_radius.append(f"- Use managed identity to access other Azure resources")
            if vm_name in nsg_associations and nsg_associations[vm_name]:
                blast_radius.append(f"- Access other VMs in the same subnet (if NSG allows)")
            else:
                blast_radius.append(f"- Potentially access any resource in the VNet (no NSG restrictions)")
            
            if blast_radius:
                summary_lines.extend(blast_radius)
            else:
                summary_lines.append("- Impact appears limited to this VM only")
            
            summary_lines.append("")
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            # Insert resource into database
            if DB_AVAILABLE and experiment_id:
                try:
                    print(f"DEBUG: Attempting to insert VM {vm_name} into database (experiment_id={experiment_id}, repo_name={repo_name})")
                    # Build properties dictionary
                    vm_properties = {
                        "operating_system": os_info,
                        "role": role or "Unknown",
                        "has_public_ip": str(vm_config.get("public_ip", False)),
                        "has_nsg": str(vm_name in nsg_associations and nsg_associations[vm_name]),
                        "has_managed_identity": str(vm_config.get("managed_identity", False)),
                        "location": vm_config.get("location", "Unknown"),
                        "vm_size": vm_config.get("vm_size", "Unknown"),
                    }
                    
                    # Find source file and line number
                    source_file, line_start, line_end = _find_resource_location(files, repo, vm_name, "azurerm_virtual_machine")
                    print(f"DEBUG: Source location: {source_file}:{line_start}-{line_end}")
                    
                    resource_id = insert_resource(
                        experiment_id=experiment_id,
                        repo_name=repo_name,
                        resource_name=vm_name,
                        resource_type="VM",
                        provider=provider,
                        source_file=source_file or "Unknown",
                        source_line=line_start,
                        source_line_end=line_end,
                        properties=vm_properties
                    )
                    
                    # Insert connections to PaaS services
                    if paas_connections.get("key_vaults"):
                        for kv in paas_connections["key_vaults"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=vm_name,
                                target_name=kv,
                                connection_type="accesses",
                                protocol="HTTPS",
                                authentication="Managed Identity"
                            )
                    if paas_connections.get("storage_accounts"):
                        for sa in paas_connections["storage_accounts"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=vm_name,
                                target_name=sa,
                                connection_type="accesses",
                                protocol="HTTPS"
                            )
                    if paas_connections.get("sql_databases"):
                        for db in paas_connections["sql_databases"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=vm_name,
                                target_name=db,
                                connection_type="queries",
                                protocol="TDS/SQL",
                                authentication="SQL Auth"
                            )
                    
                    print(f"  ✅ Inserted VM into database: {vm_name}")
                except Exception as e:
                    import traceback
                    print(f"  WARN: Failed to insert VM into database: {e}")
                    traceback.print_exc()
            
            # Write file
            sanitized_name = vm_name.replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"VM_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated VM summary: {out_path}")
        
        # Process each AKS cluster
        for aks_name in aks_clusters:
            summary_lines = []
            summary_lines.append(f"# Resource Summary: {aks_name}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Azure Kubernetes Service (AKS)")
            summary_lines.append("")
            
            # Find the AKS resource block
            aks_config = _extract_aks_configuration(files, repo, aks_name)
            
            summary_lines.append("## 📋 Resource Configuration")
            summary_lines.append("")
            if aks_config.get("kubernetes_version"):
                summary_lines.append(f"- **Kubernetes Version:** {aks_config['kubernetes_version']}")
            if aks_config.get("location"):
                summary_lines.append(f"- **Region:** {aks_config['location']}")
            if aks_config.get("subnet"):
                summary_lines.append(f"- **Subnet:** {aks_config['subnet']}")
            if aks_config.get("node_count"):
                summary_lines.append(f"- **Node Count:** {aks_config['node_count']}")
            if aks_config.get("vm_size"):
                summary_lines.append(f"- **Node VM Size:** {aks_config['vm_size']}")
            summary_lines.append("")
            
            summary_lines.append("## 🔐 Identity & Access")
            summary_lines.append("")
            if aks_config.get("managed_identity"):
                summary_lines.append(f"- **Managed Identity:** {aks_config['managed_identity']}")
            else:
                summary_lines.append("- **Managed Identity:** None detected")
            if aks_config.get("service_principal"):
                summary_lines.append(f"- **Service Principal:** {aks_config['service_principal']}")
            if aks_config.get("rbac_enabled"):
                summary_lines.append(f"- **RBAC Enabled:** ✅ Yes")
            else:
                summary_lines.append(f"- **RBAC Enabled:** ❌ Not detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔗 Dependencies")
            summary_lines.append("")
            
            # NSG protection
            if aks_name in nsg_associations and nsg_associations[aks_name]:
                summary_lines.append(f"- **NSG Protection:** ✅ Protected by Network Security Group")
            else:
                summary_lines.append(f"- **NSG Protection:** ❌ No NSG association detected")
            
            # Detect PaaS connections
            paas_connections = _detect_vm_paas_connections(files, repo, aks_name)
            if paas_connections.get("key_vaults"):
                summary_lines.append(f"- **Key Vault Access:** {', '.join(paas_connections['key_vaults'])}")
            if paas_connections.get("storage_accounts"):
                summary_lines.append(f"- **Storage Account Access:** {', '.join(paas_connections['storage_accounts'])}")
            if paas_connections.get("sql_databases"):
                summary_lines.append(f"- **SQL Database Access:** {', '.join(paas_connections['sql_databases'])}")
            
            summary_lines.append("")
            
            summary_lines.append("## 🛡️ Security Posture")
            summary_lines.append("")
            
            # Check network policy
            if aks_config.get("network_policy"):
                summary_lines.append(f"- **Network Policy:** ✅ {aks_config['network_policy']} enabled")
            else:
                summary_lines.append(f"- **Network Policy:** 🔴 No network policy detected - pods can communicate freely")
            
            # NSG coverage
            if aks_name in nsg_associations and nsg_associations[aks_name]:
                summary_lines.append(f"- **Network Security:** ✅ NSG rules applied at node level")
            else:
                summary_lines.append(f"- **Network Security:** 🔴 No NSG protection - all ports potentially open")
            
            # Public access
            if aks_config.get("public_cluster"):
                summary_lines.append(f"- **API Server Access:** 🔴 Public endpoint - accessible from internet")
            else:
                summary_lines.append(f"- **API Server Access:** ✅ Private endpoint or not detected")
            
            summary_lines.append("")
            
            summary_lines.append("## 💥 Blast Radius Analysis")
            summary_lines.append("")
            summary_lines.append("**If this AKS cluster is compromised, an attacker could:**")
            summary_lines.append("")
            
            blast_radius = []
            blast_radius.append(f"- Execute code across all {aks_config.get('node_count', 'N')} nodes")
            if paas_connections.get("key_vaults"):
                blast_radius.append(f"- Access secrets from: {', '.join(paas_connections['key_vaults'])}")
            if paas_connections.get("storage_accounts"):
                blast_radius.append(f"- Read/write data in: {', '.join(paas_connections['storage_accounts'])}")
            if paas_connections.get("sql_databases"):
                blast_radius.append(f"- Query/modify databases: {', '.join(paas_connections['sql_databases'])}")
            if aks_config.get("managed_identity"):
                blast_radius.append(f"- Use managed identity to access other Azure resources")
            if not aks_config.get("network_policy"):
                blast_radius.append(f"- Lateral movement between pods (no network policy)")
            blast_radius.append(f"- Deploy malicious workloads and consume cluster resources")
            
            summary_lines.extend(blast_radius)
            
            summary_lines.append("")
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            # Insert AKS resource into database
            if DB_AVAILABLE and experiment_id:
                try:
                    # Build properties dictionary
                    aks_properties = {
                        "kubernetes_version": aks_config.get("kubernetes_version", "Unknown"),
                        "location": aks_config.get("location", "Unknown"),
                        "node_count": str(aks_config.get("node_count", "Unknown")),
                        "vm_size": aks_config.get("vm_size", "Unknown"),
                        "has_managed_identity": str(aks_config.get("managed_identity", False)),
                        "rbac_enabled": str(aks_config.get("rbac_enabled", False)),
                        "network_policy": aks_config.get("network_policy", "None"),
                        "has_nsg": str(aks_name in nsg_associations and nsg_associations[aks_name]),
                    }
                    
                    # Find source file and line number
                    source_file, line_start, line_end = _find_resource_location(files, repo, aks_name, "azurerm_kubernetes_cluster")
                    
                    resource_id = insert_resource(
                        experiment_id=experiment_id,
                        repo_name=repo_name,
                        resource_name=aks_name,
                        resource_type="AKS",
                        provider=provider,
                        source_file=source_file or "Unknown",
                        source_line=line_start,
                        source_line_end=line_end,
                        properties=aks_properties
                    )
                    
                    # Insert connections to PaaS services
                    if paas_connections.get("key_vaults"):
                        for kv in paas_connections["key_vaults"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=aks_name,
                                target_name=kv,
                                connection_type="accesses",
                                protocol="HTTPS",
                                authentication="Managed Identity"
                            )
                    if paas_connections.get("storage_accounts"):
                        for sa in paas_connections["storage_accounts"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=aks_name,
                                target_name=sa,
                                connection_type="accesses",
                                protocol="HTTPS"
                            )
                    if paas_connections.get("sql_databases"):
                        for db in paas_connections["sql_databases"]:
                            insert_connection(
                                experiment_id=experiment_id,
                                source_name=aks_name,
                                target_name=db,
                                connection_type="queries",
                                protocol="TDS/SQL"
                            )
                    
                    print(f"  ✅ Inserted AKS into database: {aks_name}")
                except Exception as e:
                    print(f"  WARN: Failed to insert AKS into database: {e}", file=sys.stderr)
            
            # Write file
            sanitized_name = aks_name.replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"AKS_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated AKS summary: {out_path}")
        
        # Process service principals and managed identities
        service_accounts = _extract_service_accounts(files, repo, "azure")
        for sa_name, sa_info in service_accounts.items():
            summary_lines = []
            summary_lines.append(f"# Service Account Summary: {sa_name}")
            summary_lines.append("")
            summary_lines.append(f"**Identity Type:** {sa_info['type']}")
            summary_lines.append("")
            
            summary_lines.append("## 📋 Configuration")
            summary_lines.append("")
            if sa_info.get("description"):
                summary_lines.append(f"- **Description:** {sa_info['description']}")
            if sa_info.get("scope"):
                summary_lines.append(f"- **Scope:** {sa_info['scope']}")
            summary_lines.append("")
            
            summary_lines.append("## 🔑 Permissions & Role Assignments")
            summary_lines.append("")
            if sa_info.get("role_assignments"):
                summary_lines.append("**Assigned Roles:**")
                for role in sa_info["role_assignments"]:
                    summary_lines.append(f"- **{role['role']}** on `{role['scope']}`")
            else:
                summary_lines.append("- No role assignments detected in IaC")
            summary_lines.append("")
            
            summary_lines.append("## 🔗 Used By")
            summary_lines.append("")
            if sa_info.get("used_by"):
                summary_lines.append("**Resources using this identity:**")
                for resource in sa_info["used_by"]:
                    summary_lines.append(f"- {resource}")
            else:
                summary_lines.append("- No resource associations detected")
            summary_lines.append("")
            
            summary_lines.append("## 🛡️ Security Posture")
            summary_lines.append("")
            
            # Check for overly broad permissions
            risk_level = "🟢 Low"
            risks = []
            
            if sa_info.get("role_assignments"):
                for role in sa_info["role_assignments"]:
                    role_name = role['role'].lower()
                    scope = role['scope'].lower()
                    
                    if any(high_priv in role_name for high_priv in ['owner', 'contributor', 'administrator']):
                        if 'subscription' in scope or scope == '/':
                            risk_level = "🔴 Critical"
                            risks.append(f"Has **{role['role']}** at subscription level - can manage all resources")
                        elif 'resourcegroup' in scope:
                            risk_level = "🟠 High"
                            risks.append(f"Has **{role['role']}** at resource group level - broad access")
                        else:
                            if risk_level == "🟢 Low":
                                risk_level = "🟡 Medium"
                            risks.append(f"Has **{role['role']}** role - elevated privileges")
                    
                    if 'key vault' in role_name and 'administrator' in role_name:
                        if risk_level == "🟢 Low":
                            risk_level = "🟡 Medium"
                        risks.append(f"Can manage Key Vault secrets - sensitive data access")
                    
                    if 'storage' in role_name and any(x in role_name for x in ['owner', 'contributor', 'data owner']):
                        if risk_level == "🟢 Low":
                            risk_level = "🟡 Medium"
                        risks.append(f"Can read/write storage data - potential data exfiltration")
            
            if not sa_info.get("used_by"):
                if risk_level in ["🟢 Low", "🟡 Medium"]:
                    risk_level = "🟡 Medium"
                risks.append("Identity not used by any detected resource - orphaned credentials")
            
            summary_lines.append(f"**Risk Level:** {risk_level}")
            summary_lines.append("")
            if risks:
                summary_lines.append("**Risk Factors:**")
                for risk in risks:
                    summary_lines.append(f"- {risk}")
            else:
                summary_lines.append("- Follows least privilege principle")
                summary_lines.append("- Scoped to specific resources")
            summary_lines.append("")
            
            summary_lines.append("## 💥 Blast Radius Analysis")
            summary_lines.append("")
            summary_lines.append("**If this identity is compromised, an attacker could:**")
            summary_lines.append("")
            
            blast_radius = []
            if sa_info.get("role_assignments"):
                for role in sa_info["role_assignments"]:
                    role_name = role['role']
                    scope = role['scope']
                    
                    if 'owner' in role_name.lower() or 'contributor' in role_name.lower():
                        blast_radius.append(f"- Create, modify, or delete resources in `{scope}`")
                    if 'reader' in role_name.lower():
                        blast_radius.append(f"- Read configuration and data from `{scope}`")
                    if 'key vault' in role_name.lower():
                        blast_radius.append(f"- Access secrets, keys, and certificates in Key Vaults")
                    if 'storage' in role_name.lower() and 'data' in role_name.lower():
                        blast_radius.append(f"- Exfiltrate or modify storage account data")
                    if 'sql' in role_name.lower():
                        blast_radius.append(f"- Access or modify SQL databases")
            
            if sa_info.get("used_by"):
                blast_radius.append(f"- Impersonate: {', '.join(sa_info['used_by'])}")
            
            if not blast_radius:
                blast_radius.append("- Limited impact (minimal permissions detected)")
            
            summary_lines.extend(blast_radius)
            
            summary_lines.append("")
            summary_lines.append("## 🔒 Recommendations")
            summary_lines.append("")
            summary_lines.append("- [ ] Review role assignments - ensure least privilege")
            summary_lines.append("- [ ] Enable managed identity where possible (better than service principals)")
            summary_lines.append("- [ ] Audit access logs for this identity")
            summary_lines.append("- [ ] Set up alerts for suspicious activity")
            if not sa_info.get("used_by"):
                summary_lines.append("- [ ] **Remove unused identity** - reduces attack surface")
            summary_lines.append("")
            
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            # Write file
            sanitized_name = sa_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            out_path = provider_summary_dir / f"ServiceAccount_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated Service Account summary: {out_path}")
        
        # Process PaaS resources
        paas_resources = _extract_paas_resources(files, repo, "azure")
        
        # SQL Server summaries
        for sql in paas_resources["sql_servers"]:
            summary_lines = []
            summary_lines.append(f"# SQL Server Summary: {sql['name']}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Azure SQL Server")
            if sql.get("version"):
                summary_lines.append(f"**Version:** {sql['version']}")
            summary_lines.append("")
            
            summary_lines.append("## 🔥 Critical Security Issues")
            summary_lines.append("")
            
            critical_issues = []
            if sql["public_access"]:
                critical_issues.append("🔴 **PUBLIC INTERNET ACCESS** - SQL Server allows connections from 0.0.0.0")
            if not sql["aad_auth"]:
                critical_issues.append("🟠 **No Azure AD Authentication** - Relies on SQL authentication only")
            if not sql.get("tls_version") or sql["tls_version"] < "1.2":
                critical_issues.append(f"🟠 **Weak TLS Version** - Using TLS {sql.get('tls_version', 'Unknown')} (should be 1.2+)")
            if not sql["auditing"]:
                critical_issues.append("🟡 **No Auditing Enabled** - Cannot detect unauthorized access")
            if not sql["threat_detection"]:
                critical_issues.append("🟡 **No Threat Detection** - Advanced threat protection not enabled")
            
            if critical_issues:
                for issue in critical_issues:
                    summary_lines.append(f"- {issue}")
            else:
                summary_lines.append("✅ No critical issues detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Firewall Configuration")
            summary_lines.append("")
            if sql["firewall_rules"]:
                summary_lines.append(f"**{len(sql['firewall_rules'])} firewall rule(s) configured:**")
                summary_lines.append("")
                for rule in sql["firewall_rules"]:
                    if rule["start_ip"] == "0.0.0.0" and rule["end_ip"] == "0.0.0.0":
                        summary_lines.append(f"- 🔴 **{rule['start_ip']} - {rule['end_ip']}** (Allow Azure Services - allows all Azure IPs)")
                    elif rule["start_ip"] == "0.0.0.0":
                        summary_lines.append(f"- 🔴 **{rule['start_ip']} - {rule['end_ip']}** (PUBLIC INTERNET ACCESS)")
                    else:
                        summary_lines.append(f"- 🟢 **{rule['start_ip']} - {rule['end_ip']}** (Restricted range)")
            else:
                summary_lines.append("⚠️ No firewall rules detected - verify deployment settings")
            summary_lines.append("")
            
            summary_lines.append("## 🔐 Authentication & Access")
            summary_lines.append("")
            if sql["aad_auth"]:
                summary_lines.append("- ✅ Azure AD Authentication enabled")
            else:
                summary_lines.append("- ❌ Azure AD Authentication NOT enabled - using SQL auth only")
                summary_lines.append("  - **Risk:** Passwords can be brute-forced, no MFA support")
            summary_lines.append("")
            
            summary_lines.append("## 📊 Audit & Monitoring")
            summary_lines.append("")
            summary_lines.append(f"- **Auditing:** {'✅ Enabled' if sql['auditing'] else '❌ Not detected'}")
            summary_lines.append(f"- **Threat Detection:** {'✅ Enabled' if sql['threat_detection'] else '❌ Not detected'}")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Recommendations")
            summary_lines.append("")
            if sql["public_access"]:
                summary_lines.append("- [ ] 🔴 **URGENT:** Remove 0.0.0.0 firewall rule - restrict to specific IPs")
                summary_lines.append("- [ ] Consider private endpoint for Azure-only access")
            if not sql["aad_auth"]:
                summary_lines.append("- [ ] Enable Azure AD authentication for MFA support")
            if not sql.get("tls_version") or sql["tls_version"] < "1.2":
                summary_lines.append("- [ ] Enforce TLS 1.2 minimum")
            summary_lines.append("- [ ] Enable auditing to Log Analytics workspace")
            summary_lines.append("- [ ] Enable Advanced Threat Protection")
            summary_lines.append("- [ ] Review database-level permissions")
            summary_lines.append("")
            
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            sanitized_name = sql["name"].replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"SQL_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated SQL Server summary: {out_path}")
        
        # Key Vault summaries
        for kv in paas_resources["key_vaults"]:
            summary_lines = []
            summary_lines.append(f"# Key Vault Summary: {kv['name']}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Azure Key Vault")
            summary_lines.append("")
            
            summary_lines.append("## 🔥 Critical Security Issues")
            summary_lines.append("")
            
            critical_issues = []
            if kv["public_access"]:
                critical_issues.append("🔴 **PUBLIC INTERNET ACCESS** - Key Vault accessible from any IP")
            if not kv["network_acls"]:
                critical_issues.append("🟠 **No Network ACLs** - No IP restrictions configured")
            if not kv["rbac_enabled"] and kv["access_policies"] == 0:
                critical_issues.append("🔴 **No Access Control** - Neither RBAC nor access policies detected")
            if not kv["purge_protection"]:
                critical_issues.append("🟠 **No Purge Protection** - Secrets can be permanently deleted")
            if not kv["soft_delete"]:
                critical_issues.append("🟡 **No Soft Delete** - Deleted secrets cannot be recovered")
            
            if critical_issues:
                for issue in critical_issues:
                    summary_lines.append(f"- {issue}")
            else:
                summary_lines.append("✅ No critical issues detected")
            summary_lines.append("")
            
            summary_lines.append("## 🌐 Network Access")
            summary_lines.append("")
            if kv["network_acls"]:
                if kv["public_access"]:
                    summary_lines.append("- 🟡 Network ACLs configured but still allows public access")
                else:
                    summary_lines.append("- ✅ Network ACLs configured - restricted access")
            else:
                summary_lines.append("- 🔴 No network restrictions - accessible from entire internet")
                summary_lines.append("  - **Risk:** Anyone who obtains credentials can access secrets")
            summary_lines.append("")
            
            summary_lines.append("## 🔐 Access Control")
            summary_lines.append("")
            if kv["rbac_enabled"]:
                summary_lines.append("- ✅ RBAC authorization enabled")
            else:
                summary_lines.append("- Access Policy model (legacy)")
            summary_lines.append(f"- **Access Policies:** {kv['access_policies']} detected")
            summary_lines.append("")
            
            summary_lines.append("## 🛡️ Data Protection")
            summary_lines.append("")
            summary_lines.append(f"- **Soft Delete:** {'✅ Enabled' if kv['soft_delete'] else '❌ Disabled'}")
            summary_lines.append(f"- **Purge Protection:** {'✅ Enabled' if kv['purge_protection'] else '❌ Disabled'}")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Recommendations")
            summary_lines.append("")
            if kv["public_access"]:
                summary_lines.append("- [ ] 🔴 **URGENT:** Restrict network access to specific IPs or VNets")
                summary_lines.append("- [ ] Consider private endpoint for Azure-only access")
            if not kv["rbac_enabled"]:
                summary_lines.append("- [ ] Migrate to RBAC for better access control")
            if not kv["purge_protection"]:
                summary_lines.append("- [ ] Enable purge protection to prevent permanent deletion")
            if not kv["soft_delete"]:
                summary_lines.append("- [ ] Enable soft delete for recovery capability")
            summary_lines.append("- [ ] Enable diagnostic logging")
            summary_lines.append("- [ ] Review access policies/RBAC assignments")
            summary_lines.append("- [ ] Rotate secrets regularly")
            summary_lines.append("")
            
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            sanitized_name = kv["name"].replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"KeyVault_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated Key Vault summary: {out_path}")
        
        # Storage Account summaries
        for sa in paas_resources["storage_accounts"]:
            summary_lines = []
            summary_lines.append(f"# Storage Account Summary: {sa['name']}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Azure Storage Account")
            summary_lines.append("")
            
            summary_lines.append("## 🔥 Critical Security Issues")
            summary_lines.append("")
            
            critical_issues = []
            if sa["public_access"]:
                critical_issues.append("🔴 **PUBLIC INTERNET ACCESS** - Storage accessible from any IP")
            if sa.get("blob_public_access") == True:
                critical_issues.append("🔴 **ANONYMOUS BLOB ACCESS ENABLED** - Containers can be public")
            if not sa["https_only"]:
                critical_issues.append("🔴 **HTTP ALLOWED** - Data can be transmitted unencrypted")
            if not sa.get("min_tls") or sa["min_tls"] < "TLS1_2":
                critical_issues.append(f"🟠 **Weak TLS Version** - Using {sa.get('min_tls', 'Unknown')} (should be TLS1_2+)")
            if not sa["network_rules"]:
                critical_issues.append("🟠 **No Network Rules** - No IP restrictions configured")
            
            if critical_issues:
                for issue in critical_issues:
                    summary_lines.append(f"- {issue}")
            else:
                summary_lines.append("✅ No critical issues detected")
            summary_lines.append("")
            
            summary_lines.append("## 🌐 Network Access")
            summary_lines.append("")
            if sa["network_rules"]:
                if sa["public_access"]:
                    summary_lines.append("- 🟡 Network rules configured but still allows public access")
                else:
                    summary_lines.append("- ✅ Network rules configured - restricted access")
            else:
                summary_lines.append("- 🔴 No network restrictions - accessible from entire internet")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Encryption & Transport")
            summary_lines.append("")
            summary_lines.append(f"- **HTTPS Only:** {'✅ Enforced' if sa['https_only'] else '🔴 NOT enforced - HTTP allowed'}")
            summary_lines.append(f"- **Minimum TLS:** {sa.get('min_tls', '❌ Not specified')}")
            summary_lines.append("")
            
            summary_lines.append("## 🌍 Public Access")
            summary_lines.append("")
            if sa.get("blob_public_access") == True:
                summary_lines.append("- 🔴 **Blob public access ENABLED** - Containers can be set to anonymous access")
                summary_lines.append("  - **Risk:** Data leakage if container misconfigured")
            elif sa.get("blob_public_access") == False:
                summary_lines.append("- ✅ Blob public access disabled")
            else:
                summary_lines.append("- ⚠️ Blob public access setting not detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Recommendations")
            summary_lines.append("")
            if sa["public_access"]:
                summary_lines.append("- [ ] 🔴 **URGENT:** Configure network rules to restrict access")
                summary_lines.append("- [ ] Consider private endpoint for Azure-only access")
            if sa.get("blob_public_access") == True:
                summary_lines.append("- [ ] 🔴 **URGENT:** Disable blob public access unless required")
            if not sa["https_only"]:
                summary_lines.append("- [ ] 🔴 **URGENT:** Enforce HTTPS-only traffic")
            if not sa.get("min_tls") or sa["min_tls"] < "TLS1_2":
                summary_lines.append("- [ ] Set minimum TLS version to TLS1_2")
            summary_lines.append("- [ ] Enable diagnostic logging")
            summary_lines.append("- [ ] Review container access levels")
            summary_lines.append("- [ ] Enable Advanced Threat Protection")
            summary_lines.append("")
            
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            sanitized_name = sa["name"].replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"Storage_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated Storage Account summary: {out_path}")
        
        # App Service summaries
        for app in paas_resources["app_services"]:
            summary_lines = []
            summary_lines.append(f"# App Service Summary: {app['name']}")
            summary_lines.append("")
            summary_lines.append(f"**Resource Type:** Azure App Service")
            summary_lines.append("")
            
            summary_lines.append("## 🔥 Critical Security Issues")
            summary_lines.append("")
            
            critical_issues = []
            if not app["https_only"]:
                critical_issues.append("🔴 **HTTP ALLOWED** - Application accepts unencrypted traffic")
            if not app["managed_identity"]:
                critical_issues.append("🟠 **No Managed Identity** - May be using connection strings/passwords")
            if not app["client_cert"]:
                critical_issues.append("🟡 **No Client Certificate** - No mutual TLS authentication")
            if not app["vnet_integration"]:
                critical_issues.append("🟡 **No VNet Integration** - Cannot access private resources securely")
            
            if critical_issues:
                for issue in critical_issues:
                    summary_lines.append(f"- {issue}")
            else:
                summary_lines.append("✅ No critical issues detected")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Transport Security")
            summary_lines.append("")
            summary_lines.append(f"- **HTTPS Only:** {'✅ Enforced' if app['https_only'] else '🔴 NOT enforced - HTTP allowed'}")
            summary_lines.append(f"- **Client Certificate:** {'✅ Enabled' if app['client_cert'] else '❌ Disabled'}")
            summary_lines.append("")
            
            summary_lines.append("## 🔐 Identity & Authentication")
            summary_lines.append("")
            if app["managed_identity"]:
                summary_lines.append("- ✅ Managed Identity enabled - can authenticate to Azure services")
            else:
                summary_lines.append("- ❌ No Managed Identity - likely using connection strings/passwords")
                summary_lines.append("  - **Risk:** Secrets stored in configuration, harder to rotate")
            summary_lines.append("")
            
            summary_lines.append("## 🌐 Network Configuration")
            summary_lines.append("")
            if app["vnet_integration"]:
                summary_lines.append("- ✅ VNet integration enabled - can access private resources")
            else:
                summary_lines.append("- ❌ No VNet integration - outbound calls go via public internet")
            summary_lines.append("")
            
            summary_lines.append("## 🔒 Recommendations")
            summary_lines.append("")
            if not app["https_only"]:
                summary_lines.append("- [ ] 🔴 **URGENT:** Enforce HTTPS-only to prevent man-in-the-middle attacks")
            if not app["managed_identity"]:
                summary_lines.append("- [ ] Enable Managed Identity for passwordless authentication")
                summary_lines.append("- [ ] Remove connection strings from configuration")
            if not app["vnet_integration"]:
                summary_lines.append("- [ ] Enable VNet integration for private resource access")
            if not app["client_cert"]:
                summary_lines.append("- [ ] Consider client certificates for API endpoints")
            summary_lines.append("- [ ] Enable diagnostic logging")
            summary_lines.append("- [ ] Review App Service authentication settings")
            summary_lines.append("- [ ] Configure IP restrictions if needed")
            summary_lines.append("")
            
            summary_lines.append("---")
            summary_lines.append(f"*Generated: {now_uk()}*")
            
            sanitized_name = app["name"].replace("/", "_").replace("\\", "_")
            out_path = provider_summary_dir / f"AppService_{sanitized_name}.md"
            out_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
            generated_files.append(out_path)
            print(f"Generated App Service summary: {out_path}")
    
    return generated_files


def _extract_vm_configuration(files: list[Path], repo: Path, vm_name: str) -> dict[str, str]:
    """Extract VM configuration details from Terraform."""
    config = {}
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Find the VM resource block
        vm_pattern = rf'resource\s+"azurerm_(linux|windows)_virtual_machine"\s+"{re.escape(vm_name)}"\s*\{{'
        match = re.search(vm_pattern, text, re.IGNORECASE)
        if not match:
            continue
        
        # Extract block content (simple brace matching)
        start = match.end()
        depth = 1
        end = start
        for i, c in enumerate(text[start:], start):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        
        block = text[start:end]
        
        # Extract size
        size_match = re.search(r'size\s*=\s*"([^"]+)"', block)
        if size_match:
            config["size"] = size_match.group(1)
        
        # Extract location
        location_match = re.search(r'location\s*=\s*"([^"]+)"', block)
        if location_match:
            config["location"] = location_match.group(1)
        
        # Extract managed identity
        if 'identity' in block and 'SystemAssigned' in block:
            config["managed_identity"] = "SystemAssigned"
        elif 'identity' in block and 'UserAssigned' in block:
            config["managed_identity"] = "UserAssigned"
        
        # Check for public IP by looking for related resources
        if f'"{vm_name}' in text and 'azurerm_public_ip' in text:
            config["public_ip"] = "Detected"
        
        break
    
    return config


def _extract_aks_configuration(files: list[Path], repo: Path, aks_name: str) -> dict[str, str]:
    """Extract AKS configuration details from Terraform."""
    config = {}
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Find the AKS resource block
        aks_pattern = rf'resource\s+"azurerm_kubernetes_cluster"\s+"{re.escape(aks_name)}"\s*\{{'
        match = re.search(aks_pattern, text, re.IGNORECASE)
        if not match:
            continue
        
        # Extract block content
        start = match.end()
        depth = 1
        end = start
        for i, c in enumerate(text[start:], start):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        
        block = text[start:end]
        
        # Extract Kubernetes version
        k8s_version_match = re.search(r'kubernetes_version\s*=\s*"([^"]+)"', block)
        if k8s_version_match:
            config["kubernetes_version"] = k8s_version_match.group(1)
        
        # Extract location
        location_match = re.search(r'location\s*=\s*"([^"]+)"', block)
        if location_match:
            config["location"] = location_match.group(1)
        
        # Extract node count from default_node_pool
        node_count_match = re.search(r'default_node_pool\s*{[^}]*node_count\s*=\s*(\d+)', block, re.DOTALL)
        if node_count_match:
            config["node_count"] = node_count_match.group(1)
        
        # Extract VM size from default_node_pool
        vm_size_match = re.search(r'default_node_pool\s*{[^}]*vm_size\s*=\s*"([^"]+)"', block, re.DOTALL)
        if vm_size_match:
            config["vm_size"] = vm_size_match.group(1)
        
        # Extract network policy
        network_policy_match = re.search(r'network_profile\s*{[^}]*network_policy\s*=\s*"([^"]+)"', block, re.DOTALL)
        if network_policy_match:
            config["network_policy"] = network_policy_match.group(1)
        
        # Check for RBAC
        if 'role_based_access_control' in block or 'azure_active_directory_role_based_access_control' in block:
            config["rbac_enabled"] = "true"
        
        # Check for managed identity
        if 'identity' in block and 'SystemAssigned' in block:
            config["managed_identity"] = "SystemAssigned"
        elif 'identity' in block and 'UserAssigned' in block:
            config["managed_identity"] = "UserAssigned"
        
        # Check if public cluster (has public FQDN)
        if 'private_cluster_enabled' in block and '= false' in block:
            config["public_cluster"] = "true"
        elif 'private_cluster_enabled' not in block:
            config["public_cluster"] = "true"  # Default is public
        
        break
    
    return config


def _extract_paas_resources(files: list[Path], repo: Path, provider: str) -> dict[str, list[dict]]:
    """Extract PaaS resources (SQL, Key Vault, Storage, App Service) with security configurations."""
    paas_resources = {
        "sql_servers": [],
        "key_vaults": [],
        "storage_accounts": [],
        "app_services": [],
    }
    
    if provider.lower() == "azure":
        # Extract SQL Servers
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            sql_pattern = re.compile(r'resource\s+"azurerm_mssql_server"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in sql_pattern.finditer(text):
                sql_name = match.group(1)
                
                # Extract block
                start = match.end()
                depth = 1
                end = start
                for i, c in enumerate(text[start:], start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
                block = text[start:end]
                
                sql_info = {
                    "name": sql_name,
                    "type": "SQL Server",
                    "version": None,
                    "public_access": False,
                    "firewall_rules": [],
                    "aad_auth": False,
                    "tls_version": None,
                    "auditing": False,
                    "threat_detection": False,
                }
                
                # Check version
                version_match = re.search(r'version\s*=\s*"([^"]+)"', block)
                if version_match:
                    sql_info["version"] = version_match.group(1)
                
                # Check AAD authentication
                if 'azuread_administrator' in block:
                    sql_info["aad_auth"] = True
                
                # Check TLS version
                tls_match = re.search(r'minimum_tls_version\s*=\s*"([^"]+)"', block)
                if tls_match:
                    sql_info["tls_version"] = tls_match.group(1)
                
                paas_resources["sql_servers"].append(sql_info)
            
            # Find firewall rules for SQL servers
            fw_pattern = re.compile(r'resource\s+"azurerm_mssql_firewall_rule"\s+"([^"]+)"\s*\{([^}]+)\}', re.DOTALL)
            for match in fw_pattern.finditer(text):
                block = match.group(2)
                
                server_match = re.search(r'server_id\s*=\s*azurerm_mssql_server\.([^.\s]+)', block)
                if not server_match:
                    continue
                
                server_name = server_match.group(1)
                
                start_ip_match = re.search(r'start_ip_address\s*=\s*"([^"]+)"', block)
                end_ip_match = re.search(r'end_ip_address\s*=\s*"([^"]+)"', block)
                
                if start_ip_match and end_ip_match:
                    start_ip = start_ip_match.group(1)
                    end_ip = end_ip_match.group(1)
                    
                    # Find the SQL server and add firewall rule
                    for sql in paas_resources["sql_servers"]:
                        if sql["name"] == server_name:
                            sql["firewall_rules"].append({
                                "start_ip": start_ip,
                                "end_ip": end_ip,
                            })
                            if start_ip == "0.0.0.0" and end_ip == "0.0.0.0":
                                sql["public_access"] = True
                            elif start_ip == "0.0.0.0":
                                sql["public_access"] = True
        
        # Extract Key Vaults
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            kv_pattern = re.compile(r'resource\s+"azurerm_key_vault"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in kv_pattern.finditer(text):
                kv_name = match.group(1)
                
                # Extract block
                start = match.end()
                depth = 1
                end = start
                for i, c in enumerate(text[start:], start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
                block = text[start:end]
                
                kv_info = {
                    "name": kv_name,
                    "type": "Key Vault",
                    "public_access": True,  # Default
                    "network_acls": False,
                    "rbac_enabled": False,
                    "purge_protection": False,
                    "soft_delete": False,
                    "access_policies": 0,
                }
                
                # Check network ACLs
                if 'network_acls' in block:
                    kv_info["network_acls"] = True
                    if 'default_action' in block and 'Deny' in block:
                        kv_info["public_access"] = False
                
                # Check RBAC
                if 'enable_rbac_authorization' in block and '= true' in block:
                    kv_info["rbac_enabled"] = True
                
                # Check purge protection
                if 'purge_protection_enabled' in block and '= true' in block:
                    kv_info["purge_protection"] = True
                
                # Check soft delete
                if 'soft_delete_retention_days' in block:
                    kv_info["soft_delete"] = True
                
                paas_resources["key_vaults"].append(kv_info)
            
            # Count access policies
            ap_pattern = re.compile(r'resource\s+"azurerm_key_vault_access_policy"\s+"([^"]+)"\s*\{([^}]+)\}', re.DOTALL)
            for match in ap_pattern.finditer(text):
                block = match.group(2)
                kv_match = re.search(r'key_vault_id\s*=\s*azurerm_key_vault\.([^.\s]+)', block)
                if kv_match:
                    kv_name = kv_match.group(1)
                    for kv in paas_resources["key_vaults"]:
                        if kv["name"] == kv_name:
                            kv["access_policies"] += 1
        
        # Extract Storage Accounts
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            sa_pattern = re.compile(r'resource\s+"azurerm_storage_account"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in sa_pattern.finditer(text):
                sa_name = match.group(1)
                
                # Extract block
                start = match.end()
                depth = 1
                end = start
                for i, c in enumerate(text[start:], start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
                block = text[start:end]
                
                sa_info = {
                    "name": sa_name,
                    "type": "Storage Account",
                    "public_access": True,  # Default
                    "https_only": False,
                    "min_tls": None,
                    "network_rules": False,
                    "blob_public_access": None,
                }
                
                # Check HTTPS only
                if 'enable_https_traffic_only' in block and '= true' in block:
                    sa_info["https_only"] = True
                
                # Check minimum TLS
                tls_match = re.search(r'min_tls_version\s*=\s*"([^"]+)"', block)
                if tls_match:
                    sa_info["min_tls"] = tls_match.group(1)
                
                # Check network rules
                if 'network_rules' in block:
                    sa_info["network_rules"] = True
                    if 'default_action' in block and 'Deny' in block:
                        sa_info["public_access"] = False
                
                # Check blob public access
                if 'allow_blob_public_access' in block:
                    if '= false' in block:
                        sa_info["blob_public_access"] = False
                    else:
                        sa_info["blob_public_access"] = True
                
                paas_resources["storage_accounts"].append(sa_info)
        
        # Extract App Services
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            app_pattern = re.compile(r'resource\s+"azurerm_(linux|windows)_web_app"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in app_pattern.finditer(text):
                app_name = match.group(2)
                
                # Extract block
                start = match.end()
                depth = 1
                end = start
                for i, c in enumerate(text[start:], start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
                block = text[start:end]
                
                app_info = {
                    "name": app_name,
                    "type": "App Service",
                    "public_access": True,  # Default
                    "https_only": False,
                    "client_cert": False,
                    "managed_identity": False,
                    "vnet_integration": False,
                }
                
                # Check HTTPS only
                if 'https_only' in block and '= true' in block:
                    app_info["https_only"] = True
                
                # Check client certificate
                if 'client_certificate_enabled' in block and '= true' in block:
                    app_info["client_cert"] = True
                
                # Check managed identity
                if 'identity' in block:
                    app_info["managed_identity"] = True
                
                # Check VNet integration
                if 'virtual_network_subnet_id' in block:
                    app_info["vnet_integration"] = True
                
                paas_resources["app_services"].append(app_info)
    
    return paas_resources


def _detect_vm_paas_connections(files: list[Path], repo: Path, resource_name: str) -> dict[str, list[str]]:
    """Detect connections from VMs/AKS to PaaS services (Key Vault, Storage, SQL)."""
    connections = {
        "key_vaults": [],
        "storage_accounts": [],
        "sql_databases": [],
    }
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Look for Key Vault references
        kv_resources = re.findall(r'resource\s+"azurerm_key_vault"\s+"([^"]+)"', text)
        if kv_resources:
            connections["key_vaults"].extend(kv_resources)
        
        # Look for Storage Account references
        sa_resources = re.findall(r'resource\s+"azurerm_storage_account"\s+"([^"]+)"', text)
        if sa_resources:
            connections["storage_accounts"].extend(sa_resources)
        
        # Look for SQL Database references
        sql_resources = re.findall(r'resource\s+"azurerm_(mssql_server|sql_database)"\s+"([^"]+)"', text)
        if sql_resources:
            connections["sql_databases"].extend([r[1] for r in sql_resources])
    
    # Remove duplicates
    connections["key_vaults"] = list(set(connections["key_vaults"]))
    connections["storage_accounts"] = list(set(connections["storage_accounts"]))
    connections["sql_databases"] = list(set(connections["sql_databases"]))
    
    return connections


def _analyze_nsg_rules(files: list[Path], repo: Path, vm_name: str) -> list[str]:
    """Analyze NSG rules for overly permissive configurations."""
    issues = []
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Find NSG resource
        nsg_pattern = re.compile(r'resource\s+"azurerm_network_security_group"\s+"([^"]+)"\s*\{', re.IGNORECASE)
        for match in nsg_pattern.finditer(text):
            nsg_name = match.group(1)
            
            # Extract block
            start = match.end()
            depth = 1
            end = start
            for i, c in enumerate(text[start:], start):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            
            block = text[start:end]
            
            # Look for security rules within the block
            rule_pattern = re.compile(r'security_rule\s*\{([^}]+)\}', re.DOTALL)
            for rule_match in rule_pattern.finditer(block):
                rule_block = rule_match.group(1)
                
                # Extract rule details
                priority_match = re.search(r'priority\s*=\s*(\d+)', rule_block)
                direction_match = re.search(r'direction\s*=\s*"([^"]+)"', rule_block)
                access_match = re.search(r'access\s*=\s*"([^"]+)"', rule_block)
                protocol_match = re.search(r'protocol\s*=\s*"([^"]+)"', rule_block)
                source_match = re.search(r'source_address_prefix\s*=\s*"([^"]+)"', rule_block)
                dest_port_match = re.search(r'destination_port_range\s*=\s*"([^"]+)"', rule_block)
                
                if not all([direction_match, access_match]):
                    continue
                
                direction = direction_match.group(1)
                access = access_match.group(1)
                protocol = protocol_match.group(1) if protocol_match else "Tcp"
                source = source_match.group(1) if source_match else "*"
                dest_port = dest_port_match.group(1) if dest_port_match else "*"
                
                # Check for overly permissive rules
                if access.lower() == "allow" and direction.lower() == "inbound":
                    # Allow from internet (*) to sensitive ports
                    if source in ["*", "Internet", "0.0.0.0/0", "Any"]:
                        if dest_port in ["*", "22", "3389", "445", "3306", "5432", "1433"]:
                            port_name = {
                                "*": "all ports",
                                "22": "SSH (22)",
                                "3389": "RDP (3389)",
                                "445": "SMB (445)",
                                "3306": "MySQL (3306)",
                                "5432": "PostgreSQL (5432)",
                                "1433": "SQL Server (1433)",
                            }.get(dest_port, f"port {dest_port}")
                            issues.append(f"Allows internet access to {port_name}")
                        elif dest_port == "80" or dest_port == "443":
                            # HTTP/HTTPS from internet might be intentional, but flag it
                            issues.append(f"Allows internet access to HTTP/HTTPS (port {dest_port}) - verify intended")
    
    # Also check for separate NSG rule resources
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        rule_res_pattern = re.compile(
            r'resource\s+"azurerm_network_security_rule"\s+"([^"]+)"\s*\{([^}]+)\}',
            re.DOTALL
        )
        for match in rule_res_pattern.finditer(text):
            rule_block = match.group(2)
            
            # Extract rule details
            direction_match = re.search(r'direction\s*=\s*"([^"]+)"', rule_block)
            access_match = re.search(r'access\s*=\s*"([^"]+)"', rule_block)
            source_match = re.search(r'source_address_prefix\s*=\s*"([^"]+)"', rule_block)
            dest_port_match = re.search(r'destination_port_range\s*=\s*"([^"]+)"', rule_block)
            
            if not all([direction_match, access_match]):
                continue
            
            direction = direction_match.group(1)
            access = access_match.group(1)
            source = source_match.group(1) if source_match else "*"
            dest_port = dest_port_match.group(1) if dest_port_match else "*"
            
            if access.lower() == "allow" and direction.lower() == "inbound":
                if source in ["*", "Internet", "0.0.0.0/0", "Any"]:
                    if dest_port in ["*", "22", "3389", "445", "3306", "5432", "1433"]:
                        port_name = {
                            "*": "all ports",
                            "22": "SSH (22)",
                            "3389": "RDP (3389)",
                            "445": "SMB (445)",
                            "3306": "MySQL (3306)",
                            "5432": "PostgreSQL (5432)",
                            "1433": "SQL Server (1433)",
                        }.get(dest_port, f"port {dest_port}")
                        issues.append(f"Allows internet access to {port_name}")
    
    return issues


def _extract_nsg_allowed_protocols(files: list[Path], repo: Path) -> str:
    """Extract allowed protocols/ports from NSG rules and return a summary label."""
    allowed_ports = set()
    allows_all = False
    
    for p in files:
        if not p.suffix.lower() in {".tf"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        
        # Check NSG rules within azurerm_network_security_group resources
        nsg_pattern = re.compile(r'resource\s+"azurerm_network_security_group"', re.IGNORECASE)
        if nsg_pattern.search(text):
            # Look for security rules
            rule_pattern = re.compile(r'security_rule\s*\{([^}]+)\}', re.DOTALL)
            for rule_match in rule_pattern.finditer(text):
                rule_block = rule_match.group(1)
                
                direction_match = re.search(r'direction\s*=\s*"([^"]+)"', rule_block)
                access_match = re.search(r'access\s*=\s*"([^"]+)"', rule_block)
                dest_port_match = re.search(r'destination_port_range\s*=\s*"([^"]+)"', rule_block)
                
                if not all([direction_match, access_match]):
                    continue
                
                if access_match.group(1).lower() == "allow" and direction_match.group(1).lower() == "inbound":
                    if dest_port_match:
                        port = dest_port_match.group(1)
                        if port == "*":
                            allows_all = True
                        else:
                            allowed_ports.add(port)
        
        # Check standalone NSG rule resources
        rule_res_pattern = re.compile(
            r'resource\s+"azurerm_network_security_rule"\s+"([^"]+)"\s*\{([^}]+)\}',
            re.DOTALL
        )
        for match in rule_res_pattern.finditer(text):
            rule_block = match.group(2)
            
            direction_match = re.search(r'direction\s*=\s*"([^"]+)"', rule_block)
            access_match = re.search(r'access\s*=\s*"([^"]+)"', rule_block)
            dest_port_match = re.search(r'destination_port_range\s*=\s*"([^"]+)"', rule_block)
            
            if not all([direction_match, access_match]):
                continue
            
            if access_match.group(1).lower() == "allow" and direction_match.group(1).lower() == "inbound":
                if dest_port_match:
                    port = dest_port_match.group(1)
                    if port == "*":
                        allows_all = True
                    else:
                        allowed_ports.add(port)
    
    # Create protocol label
    if allows_all:
        return "All Ports"
    
    if not allowed_ports:
        return ""
    
    # Map common ports to protocol names
    port_map = {
        "22": "SSH",
        "3389": "RDP",
        "80": "HTTP",
        "443": "HTTPS",
        "445": "SMB",
        "3306": "MySQL",
        "5432": "PostgreSQL",
        "1433": "SQL",
        "21": "FTP",
        "25": "SMTP",
        "53": "DNS",
    }
    
    protocols = []
    for port in sorted(allowed_ports, key=lambda x: int(x) if x.isdigit() else 9999):
        if port in port_map:
            protocols.append(port_map[port])
        else:
            protocols.append(f"Port {port}")
    
    if len(protocols) > 3:
        return f"{', '.join(protocols[:3])}, +{len(protocols)-3} more"
    
    return ", ".join(protocols)


def _extract_service_accounts(files: list[Path], repo: Path, provider: str) -> dict[str, dict]:
    """Extract service accounts, service principals, and managed identities with their permissions."""
    service_accounts = {}
    
    if provider.lower() == "azure":
        # Track all resources with managed identities
        resources_with_identity = {}
        
        # First pass: Find all resources with managed identities
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            # Find resources with identity blocks
            identity_pattern = re.compile(
                r'resource\s+"(azurerm_[^"]+)"\s+"([^"]+)"\s*\{([^}]*identity\s*\{[^}]*\}[^}]*)\}',
                re.DOTALL
            )
            
            for match in identity_pattern.finditer(text):
                resource_type = match.group(1)
                resource_name = match.group(2)
                block = match.group(3)
                
                if 'SystemAssigned' in block:
                    identity_name = f"{resource_name}_SystemAssigned"
                    if identity_name not in service_accounts:
                        service_accounts[identity_name] = {
                            "type": "Managed Identity (System-Assigned)",
                            "used_by": [],
                            "role_assignments": [],
                        }
                    service_accounts[identity_name]["used_by"].append(f"{resource_type}.{resource_name}")
                    resources_with_identity[resource_name] = identity_name
                
                if 'UserAssigned' in block:
                    # Try to extract the user-assigned identity name
                    ua_match = re.search(r'azurerm_user_assigned_identity\.([^.\s]+)', block)
                    if ua_match:
                        ua_name = ua_match.group(1)
                        if ua_name not in service_accounts:
                            service_accounts[ua_name] = {
                                "type": "Managed Identity (User-Assigned)",
                                "used_by": [],
                                "role_assignments": [],
                            }
                        service_accounts[ua_name]["used_by"].append(f"{resource_type}.{resource_name}")
                        resources_with_identity[resource_name] = ua_name
        
        # Find explicit user-assigned identity resources
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            ua_pattern = re.compile(r'resource\s+"azurerm_user_assigned_identity"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in ua_pattern.finditer(text):
                ua_name = match.group(1)
                if ua_name not in service_accounts:
                    service_accounts[ua_name] = {
                        "type": "Managed Identity (User-Assigned)",
                        "used_by": [],
                        "role_assignments": [],
                    }
                
                # Extract location for context
                start = match.end()
                depth = 1
                end = start
                for i, c in enumerate(text[start:], start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                
                block = text[start:end]
                location_match = re.search(r'location\s*=\s*"([^"]+)"', block)
                if location_match:
                    service_accounts[ua_name]["scope"] = f"Region: {location_match.group(1)}"
        
        # Second pass: Find role assignments
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            # Find role assignments
            role_pattern = re.compile(
                r'resource\s+"azurerm_role_assignment"\s+"([^"]+)"\s*\{([^}]+)\}',
                re.DOTALL
            )
            
            for match in role_pattern.finditer(text):
                block = match.group(2)
                
                # Extract principal_id reference
                principal_match = re.search(
                    r'principal_id\s*=\s*(?:azurerm_user_assigned_identity|azurerm_linux_virtual_machine|azurerm_windows_virtual_machine|azurerm_kubernetes_cluster)\.([^.\s]+)\.(?:principal_id|identity[^.\s]*principal_id)',
                    block
                )
                
                if not principal_match:
                    continue
                
                resource_name = principal_match.group(1)
                
                # Map to service account
                sa_name = None
                if resource_name in service_accounts:
                    sa_name = resource_name
                elif resource_name in resources_with_identity:
                    sa_name = resources_with_identity[resource_name]
                
                if not sa_name:
                    continue
                
                # Extract role and scope
                role_match = re.search(r'role_definition_name\s*=\s*"([^"]+)"', block)
                scope_match = re.search(r'scope\s*=\s*"?([^"\n]+)"?', block)
                
                if role_match:
                    role_name = role_match.group(1)
                    scope = "Unknown"
                    
                    if scope_match:
                        scope_value = scope_match.group(1).strip()
                        # Parse scope to make it human-readable
                        if 'data.azurerm_subscription' in scope_value:
                            scope = "Subscription (entire subscription)"
                        elif 'azurerm_resource_group' in scope_value:
                            rg_match = re.search(r'azurerm_resource_group\.([^.\s]+)', scope_value)
                            if rg_match:
                                scope = f"Resource Group: {rg_match.group(1)}"
                            else:
                                scope = "Resource Group"
                        elif 'azurerm_storage_account' in scope_value:
                            sa_match = re.search(r'azurerm_storage_account\.([^.\s]+)', scope_value)
                            if sa_match:
                                scope = f"Storage Account: {sa_match.group(1)}"
                            else:
                                scope = "Storage Account"
                        elif 'azurerm_key_vault' in scope_value:
                            kv_match = re.search(r'azurerm_key_vault\.([^.\s]+)', scope_value)
                            if kv_match:
                                scope = f"Key Vault: {kv_match.group(1)}"
                            else:
                                scope = "Key Vault"
                        elif scope_value.startswith('/subscriptions'):
                            scope = "Subscription (entire subscription)"
                    
                    service_accounts[sa_name]["role_assignments"].append({
                        "role": role_name,
                        "scope": scope,
                    })
        
        # Find service principals (less common in Terraform, but check)
        for p in files:
            if not p.suffix.lower() in {".tf"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            
            sp_pattern = re.compile(r'resource\s+"azuread_service_principal"\s+"([^"]+)"\s*\{', re.IGNORECASE)
            for match in sp_pattern.finditer(text):
                sp_name = match.group(1)
                if sp_name not in service_accounts:
                    service_accounts[sp_name] = {
                        "type": "Service Principal",
                        "used_by": [],
                        "role_assignments": [],
                        "description": "Azure AD Service Principal",
                    }
    
    return service_accounts


def write_experiment_cloud_architecture_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    summary_dir: Path,
    findings_dir: Path | None = None,
    repo_summary_path: Path | None = None,
) -> Path | None:
    """Write experiment-scoped provider architecture summaries for ALL detected providers.

    This is intentionally *not* platform-wide: it's scoped to what was inferred from this repo.
    Generates separate Architecture_Azure.md, Architecture_Aws.md, Architecture_Gcp.md as needed.
    """

    cloud_providers = [p for p in providers if p.lower() in {"azure", "aws", "gcp"}]
    if not cloud_providers:
        return None

    files = iter_files(repo)
    tf_resource_types = detect_terraform_resources(files, repo)
    hosting_info = detect_hosting_from_terraform(files, repo)
    ingress_info = detect_ingress_from_code(files, repo)
    ci_cd_info = parse_ci_cd_details(repo)
    auth_methods_info = detect_authentication_methods(files, repo)

    cloud_dir = summary_dir / "Cloud"
    cloud_dir.mkdir(parents=True, exist_ok=True)

    def rel_link(target: Path) -> str:
        return os.path.relpath(target, cloud_dir).replace(os.sep, "/")

    finding_link = "(none yet)"
    if findings_dir and findings_dir.exists():
        candidates = list((findings_dir / "Code").glob("*.md")) + list((findings_dir / "Cloud").glob("*.md"))
        if candidates:
            link_text = candidates[0].stem.replace("_", " ")
            finding_link = f"[{link_text}]({rel_link(candidates[0])})"

    repo_summary_link = "(not generated)"
    if repo_summary_path and repo_summary_path.exists():
        repo_summary_link = f"[Repo summary: {repo_name}]({rel_link(repo_summary_path)})"

    methods = auth_methods_info.get("methods", [])
    details = auth_methods_info.get("details", [])
    if methods:
        auth_line = ", ".join(methods)
        if details:
            auth_line = f"{auth_line} — details: {', '.join(details)}"
    else:
        auth_line = "No auth signals detected in quick scan (validate)."

    first_out_path = None
    
    # Detect Terraform backend configuration once
    backend_info = detect_terraform_backend(files, repo)
    backend_uses_storage = backend_info["storage_resource"] is not None

    for provider in cloud_providers:
        provider_title = provider.title()
        provider_lower = provider.lower()
        module_derived_aks = False
        k8s_arch_path: Path | None = None

        classified = classify_terraform_resources(tf_resource_types, provider)

        # Detect services per provider
        sql_names: list[str] = []
        if provider_lower == "azure":
            has_vnet = "azurerm_virtual_network" in tf_resource_types
            vnet_name = None
            if has_vnet:
                # Extract VNet name property (not resource identifier)
                vnet_names = extract_resource_names_with_property(files, repo, "azurerm_virtual_network", "name")
                vnet_name = vnet_names[0] if vnet_names else None
            
            has_kv = "azurerm_key_vault" in tf_resource_types
            has_sql = any(t in tf_resource_types for t in {"azurerm_mssql_server", "azurerm_mssql_database", "azurerm_sql_server", "azurerm_sql_database"})
            sql_names = sorted(
                set(
                    extract_resource_names_with_property(files, repo, "azurerm_mssql_server", "name")
                    + extract_resource_names_with_property(files, repo, "azurerm_sql_server", "name")
                    + extract_resource_names(files, repo, "azurerm_mssql_server")
                    + extract_resource_names(files, repo, "azurerm_sql_server")
                )
            )
            has_ai = "azurerm_application_insights" in tf_resource_types
            has_apim = any(t.startswith("azurerm_api_management") for t in tf_resource_types)
            has_appgw = "azurerm_application_gateway" in tf_resource_types or ingress_info["type"] == "Application Gateway"
            has_frontdoor = "azurerm_frontdoor" in tf_resource_types or ingress_info["type"] == "Azure Front Door"
            has_webapp = (
                hosting_info["type"] in {"Windows App Service", "Linux App Service"}
                or any(t in tf_resource_types for t in {"azurerm_linux_web_app", "azurerm_windows_web_app"})
            )
            has_state_backend = any(t in tf_resource_types for t in {"azurerm_storage_account", "azurerm_storage_container"})
            has_vm = any(t in tf_resource_types for t in {"azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine", "azurerm_virtual_machine"})
            has_aks = "azurerm_kubernetes_cluster" in tf_resource_types
            has_nsg = "azurerm_network_security_group" in tf_resource_types
        elif provider_lower == "aws":
            aws_has_vpc_resource = "aws_vpc" in tf_resource_types
            aws_has_vpc_module = has_terraform_module_source(files, r"terraform-aws-modules/vpc/aws")
            has_vnet = aws_has_vpc_resource or aws_has_vpc_module
            vnet_name = None
            if has_vnet:
                vpc_names = extract_resource_names_with_property(files, repo, "aws_vpc", "tags")
                if vpc_names:
                    vnet_name = vpc_names[0]
                elif aws_has_vpc_module:
                    vnet_name = "module.vpc"
            
            has_kv = "aws_kms_key" in tf_resource_types or "aws_secretsmanager_secret" in tf_resource_types
            has_sql = any(t in tf_resource_types for t in {"aws_db_instance", "aws_rds_cluster"})
            sql_names = sorted(
                set(
                    extract_resource_names_with_property(files, repo, "aws_db_instance", "identifier")
                    + extract_resource_names_with_property(files, repo, "aws_rds_cluster", "cluster_identifier")
                    + extract_resource_names(files, repo, "aws_db_instance")
                    + extract_resource_names(files, repo, "aws_rds_cluster")
                )
            )
            has_ai = any(t in tf_resource_types for t in {"aws_cloudwatch_log_group", "aws_cloudwatch_metric_alarm"})
            has_apim = False
            has_appgw = any(t in tf_resource_types for t in {"aws_alb", "aws_elb"})
            has_frontdoor = "aws_cloudfront_distribution" in tf_resource_types
            has_webapp = any(t in tf_resource_types for t in {"aws_ecs_service", "aws_lambda_function"})
            has_state_backend = "aws_s3_bucket" in tf_resource_types
            has_vm = "aws_instance" in tf_resource_types
            aws_has_eks_resource = "aws_eks_cluster" in tf_resource_types
            aws_has_eks_module = has_terraform_module_source(files, r"terraform-aws-modules/eks/aws")
            has_aks = aws_has_eks_resource or aws_has_eks_module
            module_derived_aks = not aws_has_eks_resource and aws_has_eks_module
            has_nsg = "aws_security_group" in tf_resource_types
        elif provider_lower == "gcp":
            has_vnet = "google_compute_network" in tf_resource_types
            vnet_name = None
            if has_vnet:
                network_names = extract_resource_names_with_property(files, repo, "google_compute_network", "name")
                vnet_name = network_names[0] if network_names else None
            
            has_kv = "google_kms_crypto_key" in tf_resource_types
            has_sql = "google_sql_database_instance" in tf_resource_types
            sql_names = sorted(
                set(
                    extract_resource_names_with_property(files, repo, "google_sql_database_instance", "name")
                    + extract_resource_names(files, repo, "google_sql_database_instance")
                )
            )
            has_ai = any(t in tf_resource_types for t in {"google_logging_project_sink", "google_monitoring_alert_policy"})
            has_apim = False
            has_appgw = False
            has_frontdoor = False
            has_webapp = any(t in tf_resource_types for t in {"google_cloud_run_service", "google_cloudfunctions_function"})
            has_state_backend = "google_storage_bucket" in tf_resource_types
            has_vm = "google_compute_instance" in tf_resource_types
            has_aks = "google_container_cluster" in tf_resource_types
            has_nsg = "google_compute_firewall" in tf_resource_types
        else:
            continue

        services: list[str] = []
        if has_webapp:
            if provider_lower == "azure":
                services.append("App Service")
            elif provider_lower == "aws":
                services.append("ECS/Lambda")
            elif provider_lower == "gcp":
                services.append("Cloud Run")
        if has_vm:
            services.append("Virtual Machines")
        if has_aks:
            if provider_lower == "azure":
                services.append("AKS")
            elif provider_lower == "aws":
                services.append("EKS")
            elif provider_lower == "gcp":
                services.append("GKE")
        if has_appgw:
            if provider_lower == "azure":
                services.append("Application Gateway")
            elif provider_lower == "aws":
                services.append("ALB/ELB")
        if has_frontdoor:
            if provider_lower == "azure":
                services.append("Front Door")
            elif provider_lower == "aws":
                services.append("CloudFront")
        if has_apim:
            services.append("API Management")
        if has_sql:
            if provider_lower == "azure":
                services.append(f"Azure SQL ({len(sql_names)} servers)" if len(sql_names) > 1 else "Azure SQL")
            elif provider_lower == "aws":
                services.append(f"RDS ({len(sql_names)} clusters/instances)" if len(sql_names) > 1 else "RDS")
            elif provider_lower == "gcp":
                services.append(f"Cloud SQL ({len(sql_names)} instances)" if len(sql_names) > 1 else "Cloud SQL")
        if has_kv:
            if provider_lower == "azure":
                services.append("Key Vault")
            elif provider_lower == "aws":
                services.append("KMS/Secrets Manager")
            elif provider_lower == "gcp":
                services.append("KMS")
        if has_ai:
            if provider_lower == "azure":
                services.append("Application Insights")
            elif provider_lower == "aws":
                services.append("CloudWatch")
            elif provider_lower == "gcp":
                services.append("Cloud Logging")
        if has_nsg:
            if provider_lower == "azure":
                services.append("NSG")
            elif provider_lower == "aws":
                services.append("Security Groups")
            elif provider_lower == "gcp":
                services.append("Firewall Rules")
        if has_state_backend:
            services.append("Storage Account")
        if not services:
            services.append("(none inferred)")

        # Optional provider-scoped Kubernetes ingress/services diagram (AKS/EKS/GKE)
        if has_aks:
            if provider_lower == "aws":
                k8s_prefixes = ["insecure-kubernetes-deployments/"]
            elif provider_lower == "azure":
                k8s_prefixes = ["tfscripts/"]
            else:
                k8s_prefixes = None

            k8s_signals = extract_kubernetes_topology_signals(files, repo, k8s_prefixes)
            ingress_names = k8s_signals["ingress_names"][:6]
            service_names = k8s_signals["service_names"][:10]
            manifest_secret_names = k8s_signals["manifest_secret_names"]
            ingress_classes = k8s_signals["ingress_classes"]
            controller_hints = k8s_signals["controller_hints"]
            lb_hints = k8s_signals["lb_hints"]
            evidence_files = k8s_signals["evidence_files"][:8]
            k8s_secret_names = extract_resource_names(files, repo, "kubernetes_secret_v1")
            all_k8s_secret_names = sorted(set(k8s_secret_names + manifest_secret_names))
            if provider_lower == "azure":
                cluster_names = extract_resource_names(files, repo, "azurerm_kubernetes_cluster")
                cluster_label = cluster_names[0] if cluster_names else "AKS Cluster"
            elif provider_lower == "aws":
                cluster_names = extract_resource_names(files, repo, "aws_eks_cluster")
                cluster_label = cluster_names[0] if cluster_names else "EKS Cluster"
            else:
                cluster_names = extract_resource_names(files, repo, "google_container_cluster")
                cluster_label = cluster_names[0] if cluster_names else "GKE Cluster"

            if any("ingress-nginx" in hint for hint in controller_hints) or "nginx" in ingress_classes:
                ingress_controller_label = "NGINX Ingress Controller"
            elif provider_lower == "aws" and any("aws-load-balancer-controller" in hint for hint in controller_hints):
                ingress_controller_label = "AWS Load Balancer Controller"
            else:
                ingress_controller_label = "Ingress Controller"

            if provider_lower == "aws":
                lb_label = "Internet-facing NLB/ALB"
            elif provider_lower == "azure":
                lb_label = "Internet-facing ingress endpoint"
            else:
                lb_label = "Internet-facing ingress endpoint"

            provider_folder = {"aws": "AWS", "gcp": "GCP", "azure": "Azure"}.get(provider_lower, provider_title)
            provider_cloud_dir = cloud_dir / provider_folder
            provider_cloud_dir.mkdir(parents=True, exist_ok=True)

            cluster_filename_token = re.sub(r"[^A-Za-z0-9_-]+", "_", cluster_label).strip("_")
            if not cluster_filename_token:
                cluster_filename_token = "Cluster"
            k8s_arch_filename = f"Architecture_{provider_title}_Kubernetes_{cluster_filename_token}.md"
            k8s_arch_path = provider_cloud_dir / k8s_arch_filename
            for stale_path in cloud_dir.glob(f"Architecture_{provider_title}_Kubernetes*.md"):
                if stale_path != k8s_arch_path and stale_path.exists():
                    try:
                        stale_path.unlink()
                    except OSError:
                        pass
            for stale_path in provider_cloud_dir.glob(f"Architecture_{provider_title}_Kubernetes*.md"):
                if stale_path != k8s_arch_path and stale_path.exists():
                    try:
                        stale_path.unlink()
                    except OSError:
                        pass
            k8s_lines: list[str] = []
            k8s_lines.append(f"# 🗺️ Kubernetes Ingress & Services - {provider_title} ({repo_name})")
            k8s_lines.append("")
            k8s_lines.append("```mermaid")
            k8s_lines.append("flowchart TB")
            k8s_lines.append(f"  cluster[{cluster_label}]")
            has_ingress_evidence = bool(ingress_names or ingress_classes or controller_hints)
            if has_ingress_evidence:
                k8s_lines.append("  internet[Internet]")
                k8s_lines.append(f"  lb[{lb_label}]")
                k8s_lines.append(f"  ic[{ingress_controller_label}]")
                k8s_lines.append("  internet --> lb")
                k8s_lines.append("  lb --> ic")
                k8s_lines.append("  ic --> cluster")
            if ingress_names:
                for idx, ingress_name in enumerate(ingress_names, start=1):
                    ingress_node = f"ing{idx}"
                    k8s_lines.append(f"  {ingress_node}[Ingress: {ingress_name}]")
                    k8s_lines.append(f"  ic --> {ingress_node}")
                    if service_names:
                        svc_idx = min(idx - 1, len(service_names) - 1)
                        k8s_lines.append(f"  svc{svc_idx + 1}[Service: {service_names[svc_idx]}]")
                        k8s_lines.append(f"  {ingress_node} --> svc{svc_idx + 1}")
            elif has_ingress_evidence:
                k8s_lines.append("  ing[Ingress resources]")
                k8s_lines.append("  ic --> ing")
                if service_names:
                    for idx, service_name in enumerate(service_names[:5], start=1):
                        k8s_lines.append(f"  svc{idx}[Service: {service_name}]")
                        k8s_lines.append(f"  ing --> svc{idx}")
                else:
                    k8s_lines.append("  svc[Services]")
                    k8s_lines.append("  ing --> svc")
            if all_k8s_secret_names:
                for idx, secret_name in enumerate(all_k8s_secret_names[:8], start=1):
                    secret_node = f"sec{idx}"
                    k8s_lines.append(f"  {secret_node}[Secret: {secret_name}]")
                    k8s_lines.append(f"  cluster --> {secret_node}")
            k8s_lines.append("")
            if has_ingress_evidence:
                k8s_lines.append("  style lb stroke:#ff6b6b,stroke-width:2px")
                k8s_lines.append("  style ic stroke:#0066cc,stroke-width:2px")
                k8s_lines.append("  style internet stroke:#ff0000,stroke-width:3px")
            k8s_lines.append("  style cluster stroke:#0066cc,stroke-width:2px")
            k8s_lines.append("```")
            k8s_lines.append("")
            k8s_lines.append("## 🧾 Summary")
            k8s_lines.append(f"- **Cluster:** {cluster_label}")
            if ingress_classes:
                k8s_lines.append(f"- **Ingress classes:** {', '.join(ingress_classes)}")
            else:
                k8s_lines.append("- **Ingress classes:** None detected")
            if ingress_names:
                k8s_lines.append(f"- **Ingress resources ({len(ingress_names)} shown):** {', '.join(ingress_names)}")
            else:
                k8s_lines.append("- **Ingress resources:** None explicitly detected in scoped manifests")
            if service_names:
                k8s_lines.append(f"- **Services ({len(service_names)} shown):** {', '.join(service_names)}")
            else:
                k8s_lines.append("- **Services:** None explicitly detected in scoped manifests")
            if all_k8s_secret_names:
                k8s_lines.append(f"- **Secret resources:** {', '.join(all_k8s_secret_names[:8])}")
            if k8s_secret_names:
                k8s_lines.append(f"- **Terraform secret resources:** kubernetes_secret_v1 ({', '.join(k8s_secret_names[:6])})")
            if manifest_secret_names:
                k8s_lines.append(f"- **Manifest secret resources:** kind: Secret ({', '.join(manifest_secret_names[:6])})")
            if lb_hints:
                k8s_lines.append(f"- **Load balancer hints:** {', '.join(lb_hints)}")
            if evidence_files:
                k8s_lines.append(f"- **Evidence files:** {', '.join(f'`{p}`' for p in evidence_files)}")
            k8s_lines.append(f"- 🗓️ **Last updated:** {now_uk()}")
            k8s_arch_path.write_text("\n".join(k8s_lines).rstrip() + "\n", encoding="utf-8")

            probs = validate_markdown_file(k8s_arch_path, fix=True)
            errs = [p for p in probs if p.level == "ERROR"]
            warns = [p for p in probs if p.level == "WARN"]
            for p in warns:
                line = f":{p.line}" if p.line else ""
                print(f"WARN: {k8s_arch_path}{line} - {p.message}")
            if errs:
                raise SystemExit(f"Mermaid validation failed for {k8s_arch_path}: {errs[0].message}")
        
        # Determine edge
        edge_name = "Edge Gateway"
        edge_confirmed = False
        if has_appgw:
            edge_name = "Application Gateway" if provider_lower == "azure" else "ALB"
            edge_confirmed = True
        elif has_frontdoor:
            edge_name = "Front Door" if provider_lower == "azure" else "CloudFront"
            edge_confirmed = True
        elif has_apim:
            edge_name = "API Management"
            edge_confirmed = True

        edge_assumed = not edge_confirmed

        out_path = cloud_dir / f"Architecture_{provider_title}.md"

        content_lines: list[str] = []
        content_lines.append(f"# 🗺️ Architecture {provider_title} (Experiment scoped - {repo_name})")
        content_lines.append("")
        content_lines.append("```mermaid")
        content_lines.append("flowchart TB")
        content_lines.append("  internet[Internet]")
        
        # Only add edge gateway if detected
        if edge_confirmed:
            content_lines.append(f"  edge[{edge_name}]")
        
        # Use VNet/VPC name for subgraph if available - only include compute resources
        if vnet_name:
            subgraph_label = f"VNet: {vnet_name}" if provider_lower == "azure" else (f"VPC: {vnet_name}" if provider_lower == "aws" else f"Network: {vnet_name}")
            content_lines.append(f"  subgraph cloud[{subgraph_label}]")
        else:
            content_lines.append(f"  subgraph cloud[{provider_title}]")
        
        # Inside VNet: VMs and AKS only (compute resources with NICs in subnets)
        
        # Extract individual VM names with OS
        vm_names = []
        if has_vm:
            vm_names = extract_vm_names_with_os(files, repo, provider_lower)
            
            # Show individual VMs if 3 or fewer, otherwise show count
            if len(vm_names) <= 3 and len(vm_names) > 0:
                for vm_name, vm_os, vm_role in vm_names:
                    # Just show OS without EOL info (details in per-resource summary)
                    os_label = vm_os
                    
                    if vm_role:
                        vm_line = f"    vm_{vm_name}[{vm_name} {vm_role}, {os_label}]"
                    else:
                        vm_line = f"    vm_{vm_name}[{vm_name} VM, {os_label}]"
                    print(f"DEBUG: Adding VM line: {repr(vm_line)}")
                    content_lines.append(vm_line)
            elif len(vm_names) > 3:
                content_lines.append(f"    vm[💻 Virtual Machines ({len(vm_names)})]")
            else:
                content_lines.append(f"    vm[💻 Virtual Machines]")
        
        # Extract AKS/EKS/GKE cluster names
        aks_names = []
        if has_aks:
            if provider_lower == "azure":
                aks_names = extract_resource_names(files, repo, "azurerm_kubernetes_cluster")
                aks_label = "AKS"
            elif provider_lower == "aws":
                aks_names = extract_resource_names(files, repo, "aws_eks_cluster")
                aks_label = "EKS"
            elif provider_lower == "gcp":
                aks_names = extract_resource_names(files, repo, "google_container_cluster")
                aks_label = "GKE"
            
            if len(aks_names) <= 3 and len(aks_names) > 0:
                for cluster_name in aks_names:
                    content_lines.append(f"    aks_{cluster_name}[{cluster_name} AKS, Kubernetes {aks_label}]")
            elif len(aks_names) > 3:
                content_lines.append(f"    aks[{aks_label} Cluster, {len(aks_names)} instances]")
            else:
                if module_derived_aks:
                    content_lines.append(f"    aks[{aks_label} Cluster module-defined]")
                else:
                    content_lines.append(f"    aks[{aks_label} Cluster]")
        
        # NSG inside VNet (network-level control)
        if has_nsg:
            nsg_label = "Network Security Group" if provider_lower == "azure" else ("Security Groups" if provider_lower == "aws" else "Firewall Rules")
            content_lines.append(f"    nsg[{nsg_label}]")
        
        # Close VNet subgraph
        content_lines.append("  end")
        
        # PaaS services subgraph (only if we have PaaS services)
        has_paas = has_webapp or has_sql or has_kv or has_ai or has_state_backend
        sql_node_ids: list[str] = []
        sql_server_count = len(sql_names)
        vm_node_count = len(vm_names) if has_vm and len(vm_names) <= 3 and len(vm_names) > 0 else (1 if has_vm else 0)
        aks_node_count = len(aks_names) if has_aks and len(aks_names) <= 3 and len(aks_names) > 0 else (1 if has_aks else 0)
        base_node_count = (
            (1 if has_webapp else 0)
            + vm_node_count
            + aks_node_count
            + (1 if has_kv else 0)
            + (1 if has_ai else 0)
            + (1 if has_state_backend else 0)
            + (1 if has_nsg else 0)
        )
        show_individual_sql = bool(
            has_sql
            and sql_server_count > 0
            and sql_server_count <= 3
            and (base_node_count + sql_server_count) <= 11
        )

        if has_paas:
            paas_label = "Azure PaaS" if provider_lower == "azure" else ("AWS Managed Services" if provider_lower == "aws" else "GCP Managed Services")
            content_lines.append(f"  subgraph paas[{paas_label}]")
            
            if has_webapp:
                host_label = hosting_info["type"] or "App Service"
                content_lines.append(f"    app[{repo_name} App Service, {host_label}]")
            
            if has_sql:
                sql_label = "Azure SQL" if provider_lower == "azure" else ("RDS" if provider_lower == "aws" else "Cloud SQL")
                if show_individual_sql:
                    for idx, sql_name in enumerate(sql_names, start=1):
                        sql_node_id = f"sql{idx}"
                        content_lines.append(f"    {sql_node_id}[{sql_label}: {sql_name}]")
                        sql_node_ids.append(sql_node_id)
                else:
                    if sql_server_count > 1:
                        if provider_lower == "azure":
                            sql_label = f"{sql_label} Servers ({sql_server_count})"
                        elif provider_lower == "aws":
                            sql_label = f"{sql_label} Clusters/Instances ({sql_server_count})"
                        else:
                            sql_label = f"{sql_label} Instances ({sql_server_count})"
                    content_lines.append(f"    sql[{sql_label}]")
                    sql_node_ids.append("sql")
            if has_kv:
                kv_label = "Key Vault" if provider_lower == "azure" else ("Secrets Manager" if provider_lower == "aws" else "Secret Manager")
                content_lines.append(f"    kv[{kv_label}]")
            if has_ai:
                ai_label = "Application Insights" if provider_lower == "azure" else ("CloudWatch" if provider_lower == "aws" else "Cloud Logging")
                content_lines.append(f"    ai[{ai_label}]")
            if has_state_backend:
                content_lines.append(f"    sa[Storage Account]")
            
            content_lines.append("  end")
        
        # Only show CI/CD pipeline if it has connections (i.e., remote backend)
        if backend_uses_storage and has_state_backend:
            if ci_cd_info["platform"] != "Unknown":
                content_lines.append(f"  pipeline[⚙️ {ci_cd_info['platform']}]")
            else:
                content_lines.append(f"  pipeline[⚙️ CI/CD]")
        
        content_lines.append("")
        
        # Connection logic - depends on if edge exists
        if edge_confirmed:
            content_lines.append("  internet -->|HTTPS| edge")
            if has_webapp:
                content_lines.append("  edge --> app")
        else:
            # Direct internet to app (no edge gateway detected)
            if has_webapp:
                content_lines.append("  internet -->|HTTPS| app")
        
        # Extract NSG associations for VMs first (need this for routing logic)
        nsg_associations = {}
        if has_nsg and has_vm:
            nsg_associations = extract_nsg_associations(files, repo, provider_lower, vm_names)
            print(f"DEBUG: NSG associations detected: {nsg_associations}")
        
        # Connect to individual VMs or generic VM node
        # If NSG protects VM, route through NSG; otherwise direct connection
        if has_vm:
            if len(vm_names) <= 3 and len(vm_names) > 0:
                for vm_name, vm_os, vm_role in vm_names:
                    has_nsg_protection = nsg_associations.get(vm_name, False)
                    
                    if edge_confirmed:
                        if has_nsg_protection:
                            # Edge → NSG handled separately, NSG → VM below
                            pass
                        else:
                            content_lines.append(f"  edge --> vm_{vm_name}")
                    else:
                        # No edge gateway
                        if has_nsg_protection:
                            # Internet → NSG → VM (NSG acts as entry point)
                            # Internet → NSG connection added below
                            pass
                        else:
                            # Direct internet exposure (no NSG protection)
                            content_lines.append(f"  internet --> vm_{vm_name}")
            else:
                if edge_confirmed:
                    content_lines.append("  edge --> vm")
                else:
                    content_lines.append("  internet --> vm")
        
        # Connect to individual AKS clusters or generic AKS node
        if has_aks:
            if len(aks_names) <= 3 and len(aks_names) > 0:
                for cluster_name in aks_names:
                    if edge_confirmed:
                        content_lines.append(f"  edge --> aks_{cluster_name}")
                    else:
                        content_lines.append(f"  internet --> aks_{cluster_name}")
            else:
                if edge_confirmed:
                    content_lines.append("  edge --> aks")
                else:
                    content_lines.append("  internet --> aks")
        
        # PaaS services are publicly accessible by default (unless private endpoints configured)
        # Show internet connections to PaaS services to highlight attack surface
        # TODO: Detect private endpoints and skip these connections if configured
        
        if has_sql:
            # Internet can access SQL (public endpoint unless private endpoint configured)
            if not sql_node_ids:
                sql_node_ids = ["sql"]
            for sql_node_id in sql_node_ids:
                content_lines.append(f"  internet --> {sql_node_id}")
            if has_webapp:
                for sql_node_id in sql_node_ids:
                    content_lines.append(f"  app --> {sql_node_id}")
            if has_vm:
                if len(vm_names) <= 3 and len(vm_names) > 0:
                    for vm_name, vm_os, vm_role in vm_names:
                        for sql_node_id in sql_node_ids:
                            content_lines.append(f"  vm_{vm_name} --> {sql_node_id}")
                else:
                    for sql_node_id in sql_node_ids:
                        content_lines.append(f"  vm --> {sql_node_id}")
            if has_aks:
                if len(aks_names) <= 3 and len(aks_names) > 0:
                    for cluster_name in aks_names:
                        for sql_node_id in sql_node_ids:
                            content_lines.append(f"  aks_{cluster_name} --> {sql_node_id}")
                else:
                    for sql_node_id in sql_node_ids:
                        content_lines.append(f"  aks --> {sql_node_id}")
        
        if has_kv:
            # Internet can access Key Vault (public endpoint unless private endpoint configured)
            content_lines.append("  internet --> kv")
            if has_webapp:
                content_lines.append("  app --> kv")
            if has_vm:
                if len(vm_names) <= 3 and len(vm_names) > 0:
                    for vm_name, vm_os, vm_role in vm_names:
                        content_lines.append(f"  vm_{vm_name} --> kv")
                else:
                    content_lines.append("  vm --> kv")
            if has_aks:
                if len(aks_names) <= 3 and len(aks_names) > 0:
                    for cluster_name in aks_names:
                        content_lines.append(f"  aks_{cluster_name} --> kv")
                else:
                    content_lines.append("  aks --> kv")
        
        # Storage Account connections (for deployment packages, scripts, etc.)
        if has_state_backend:
            # Internet can access Storage Account (public endpoint)
            content_lines.append("  internet --> sa")
            # App Service may use storage for deployment packages
            if has_webapp:
                content_lines.append("  app --> sa")
            # VMs may download scripts/packages from storage
            if has_vm:
                if len(vm_names) <= 3 and len(vm_names) > 0:
                    for vm_name, vm_os, vm_role in vm_names:
                        content_lines.append(f"  vm_{vm_name} --> sa")
                else:
                    content_lines.append("  vm --> sa")
        
        
        # Remove duplicate NSG extraction (already done above for routing)
        # nsg_associations already populated
        
        if has_ai:
            content_lines.append("  app -.->|Telemetry| ai")
            if has_vm:
                if len(vm_names) <= 3 and len(vm_names) > 0:
                    for vm_name, vm_os, vm_role in vm_names:
                        content_lines.append(f"  vm_{vm_name} -.->|Telemetry| ai")
                else:
                    content_lines.append("  vm -.->|Telemetry| ai")
            if has_aks:
                if len(aks_names) <= 3 and len(aks_names) > 0:
                    for cluster_name in aks_names:
                        content_lines.append(f"  aks_{cluster_name} -.->|Telemetry| ai")
                else:
                    content_lines.append("  aks -.->|Telemetry| ai")
        
        # Show NSG routing: Internet/Edge → NSG → VMs
        # NSG acts as the entry point for protected VMs
        if has_nsg and has_vm:
            print(f"DEBUG: Checking NSG arrows - has_nsg={has_nsg}, has_vm={has_vm}, vm_count={len(vm_names)}")
            
            # Check if any VMs have NSG protection
            any_protected = any(nsg_associations.values())
            
            if any_protected:
                # Extract NSG protocols for the arrow label
                nsg_protocols = _extract_nsg_allowed_protocols(files, repo)
                
                # Add internet/edge → NSG connection with protocol label
                if edge_confirmed:
                    if nsg_protocols:
                        content_lines.append(f"  edge -->|{nsg_protocols}| nsg")
                    else:
                        content_lines.append("  edge --> nsg")
                else:
                    if nsg_protocols:
                        content_lines.append(f"  internet -->|{nsg_protocols}| nsg")
                    else:
                        content_lines.append("  internet --> nsg")
            
            # Add NSG → VM connections for protected VMs
            if len(vm_names) <= 3 and len(vm_names) > 0:
                for vm_name, vm_os, vm_role in vm_names:
                    has_association = nsg_associations.get(vm_name, False)
                    print(f"DEBUG: VM {vm_name} has NSG association: {has_association}")
                    if has_association:
                        arrow_line = f"  nsg --> vm_{vm_name}"
                        print(f"DEBUG: Adding NSG arrow: {arrow_line}")
                        content_lines.append(arrow_line)
            else:
                # If more than 3 VMs, show generic protection if any have NSG
                if any(nsg_associations.values()):
                    content_lines.append("  nsg --> vm")
        
        # Only show CI/CD → Storage connection if backend actually uses remote storage
        if backend_uses_storage and has_state_backend:
            content_lines.append("  pipeline -.->|State| sa")
        
        content_lines.append("")
        if has_webapp:
            content_lines.append("  style app stroke:#0066cc,stroke-width:2px")
        if has_vm:
            if len(vm_names) <= 3 and len(vm_names) > 0:
                for vm_name, vm_os, vm_role in vm_names:
                    content_lines.append(f"  style vm_{vm_name} stroke:#0066cc,stroke-width:2px")
            else:
                content_lines.append("  style vm stroke:#0066cc,stroke-width:2px")
        if has_aks:
            if len(aks_names) <= 3 and len(aks_names) > 0:
                for cluster_name in aks_names:
                    content_lines.append(f"  style aks_{cluster_name} stroke:#0066cc,stroke-width:2px")
            else:
                content_lines.append("  style aks stroke:#0066cc,stroke-width:2px")
        if has_sql:
            if sql_node_ids:
                for sql_node_id in sql_node_ids:
                    content_lines.append(f"  style {sql_node_id} stroke:#00aa00,stroke-width:3px")
            else:
                content_lines.append("  style sql stroke:#00aa00,stroke-width:3px")
        if has_kv:
            content_lines.append("  style kv stroke:#f59f00,stroke-width:2px")
        if has_nsg:
            content_lines.append("  style nsg stroke:#ff6b6b,stroke-width:2px")
        if has_state_backend:
            content_lines.append("  style sa stroke:#00aa00,stroke-width:3px")
        # Module-derived resources are considered confirmed IaC intent and are styled as solid.
        
        # Only style pipeline if it exists
        if backend_uses_storage and has_state_backend:
            content_lines.append("  style pipeline stroke:#f59f00,stroke-width:2px")
        
        # Style Internet node as red (attack surface source)
        content_lines.append("  style internet stroke:#ff0000,stroke-width:3px")
        
        # Only style edge if it exists
        if edge_confirmed:
            content_lines.append("  style edge stroke:#ff6b6b,stroke-width:3px")
        
        # Style internet-facing connections as red (attack surface)
        if not edge_confirmed:
            # The connections are added in this exact order:
            # 1. internet → app (if webapp)
            # 2. internet → aks (if aks)
            # 3. internet → sql (if sql) <-- THEN internal sql connections
            # 4. internet → kv (if kv) <-- THEN internal kv connections  
            # 5. internet → sa (if storage) <-- THEN internal sa connections
            # 6. internet → nsg (if nsg protects VMs)
            
            link_idx = 0
            
            # Internet → App Service
            if has_webapp:
                content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                link_idx += 1
            
            # Internet → AKS (direct exposure)
            if has_aks and len(aks_names) > 0:
                if len(aks_names) <= 3:
                    for cluster_name in aks_names:
                        content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                        link_idx += 1
                else:
                    content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                    link_idx += 1
            
            # Internet → SQL (then skip internal SQL connections)
            if has_sql:
                sql_node_count = len(sql_node_ids) if sql_node_ids else 1
                for _ in range(sql_node_count):
                    content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                    link_idx += 1

                # Skip internal SQL connections
                if has_webapp:
                    link_idx += sql_node_count
                if has_vm:
                    vm_source_count = len(vm_names) if len(vm_names) <= 3 and len(vm_names) > 0 else 1
                    link_idx += vm_source_count * sql_node_count
                if has_aks:
                    aks_source_count = len(aks_names) if len(aks_names) <= 3 and len(aks_names) > 0 else 1
                    link_idx += aks_source_count * sql_node_count
            
            # Internet → Key Vault (then skip internal KV connections)
            if has_kv:
                content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                link_idx += 1
                
                # Skip internal kv connections
                link_idx += 1  # app → kv
                if has_vm:
                    if len(vm_names) <= 3 and len(vm_names) > 0:
                        link_idx += len(vm_names)
                    else:
                        link_idx += 1
                if has_aks:
                    if len(aks_names) <= 3 and len(aks_names) > 0:
                        link_idx += len(aks_names)
                    else:
                        link_idx += 1
            
            # Internet → Storage Account (then skip internal SA connections)
            if has_state_backend:
                content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                link_idx += 1
                
                # Skip internal sa connections
                link_idx += 1  # app → sa
                if has_vm:
                    if len(vm_names) <= 3 and len(vm_names) > 0:
                        link_idx += len(vm_names)
                    else:
                        link_idx += 1
            
            # Internet → NSG
            if has_nsg and any(nsg_associations.values()):
                content_lines.append(f"  linkStyle {link_idx} stroke:#ff0000,stroke-width:3px")
                link_idx += 1
        
        content_lines.append("```")
        content_lines.append("")
        content_lines.append("**Legend:**")
        content_lines.append("- **Border Colors:** 🔵 Blue = Applications/Services | 🟢 Green = Data Stores | 🟠 Orange = Identity/Secrets/Pipeline | 🔴 Red = Security/Network Controls")
        content_lines.append("- **Line Styles:** Solid = direct dependency | Dashed = protection/monitoring")
        content_lines.append("- **Arrow Colors:** 🔴 Red arrows = Direct internet exposure (attack surface)")
        content_lines.append("- **Arrow Labels:** Only shown where context adds value (e.g., HTTPS protocol, State storage, Telemetry)")
        content_lines.append("")
        content_lines.append("## 🧭 Overview")
        content_lines.append(f"- **Provider:** {provider_title}")
        content_lines.append(f"- **Scope:** Experiment-scoped (inferred from repo `{repo_name}`; not platform-wide)")
        content_lines.append(f"- **Auth signals (quick):** {auth_line}")
        content_lines.append("")
        content_lines.append("## 📊 TL;DR - Executive Summary")
        content_lines.append("")
        content_lines.append("| Aspect | Value |")
        content_lines.append("|--------|-------|")
        content_lines.append(f"| **Key services** | {', '.join(services)} |")
        content_lines.append("| **Top risk** | 🟠 High — validate ingress + authZ/authN enforcement for user/data endpoints |")
        
        # Update next step based on whether edge exists
        if edge_confirmed:
            content_lines.append(f"| **Primary next step** | Validate {edge_name} configuration and add app-layer authZ |")
        else:
            content_lines.append("| **Primary next step** | ⚠️ No edge gateway detected - consider adding WAF/Front Door/App Gateway for ingress protection |")
        
        content_lines.append(f"| **Repo context** | {repo_summary_link} |")
        content_lines.append(f"| **Related finding** | {finding_link} |")
        content_lines.append("")
        content_lines.append("## 📊 Service Risk Order")
        
        # Update risk order based on edge presence
        if edge_confirmed:
            content_lines.append(f"1. 🟠 High — ingress and authentication enforcement ({edge_name} + app)")
        else:
            content_lines.append("1. 🔴 Critical — NO EDGE GATEWAY - direct internet exposure to app/VMs/AKS")
        if has_vm:
            content_lines.append("2. 🟠 High — VM access controls and network isolation")
        if has_aks:
            content_lines.append("3. 🟠 High — Kubernetes RBAC and network policies")
        if has_sql:
            content_lines.append("4. 🟠 High — user/data access backed by SQL (PII exposure if unauthenticated)")
        if has_nsg:
            content_lines.append("5. 🟡 Medium — network security group rules and port restrictions")
        if has_kv:
            content_lines.append("6. 🟡 Medium — secrets access model and network restrictions")
        if has_state_backend:
            content_lines.append("7. 🟡 Medium — Terraform state and pipeline credential scope")
        if has_ai:
            content_lines.append("8. 🟢 Low — telemetry (validate sensitive data logging)")
        content_lines.append("")
        content_lines.append("## 📝 Notes")
        content_lines.append(f"- 🗓️ **Last updated:** {now_uk()}")
        content_lines.append("- This file is generated for experiment isolation; confirm assumptions before treating as environment fact.")
        content_lines.append("- See individual resource summaries (VM_*.md, SQL_*.md, etc.) for detailed security analysis.")
        if k8s_arch_path is not None and k8s_arch_path.exists():
            content_lines.append(f"- Kubernetes ingress/services view: [Kubernetes detail]({rel_link(k8s_arch_path)})")

        out_path.write_text("\n".join(content_lines).rstrip() + "\n", encoding="utf-8")

        probs = validate_markdown_file(out_path, fix=True)
        errs = [p for p in probs if p.level == "ERROR"]
        warns = [p for p in probs if p.level == "WARN"]
        for p in warns:
            line = f":{p.line}" if p.line else ""
            print(f"WARN: {out_path}{line} - {p.message}")
        if errs:
            raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")

        if first_out_path is None:
            first_out_path = out_path

    # Generate multi-cloud overview if multiple providers detected
    if len(cloud_providers) > 1:
        # Detect actual connectivity patterns
        connectivity = detect_cross_cloud_connectivity(files, repo, [p.lower() for p in cloud_providers])
        
        has_vpn = len(connectivity["vpn_tunnels"]) > 0
        has_private_link = len(connectivity["private_links"]) > 0
        has_peering = len(connectivity["vnet_peering"]) > 0
        has_federation = len(connectivity["federated_identity"]) > 0
        has_cross_creds = len(connectivity["service_principals"]) > 0
        has_shared_secrets = len(connectivity["shared_secrets"]) > 0
        has_data_replication = len(connectivity["cross_cloud_data"]) > 0
        
        overview_path = cloud_dir / "Architecture_Overview.md"
        
        overview_lines: list[str] = []
        overview_lines.append(f"# 🗺️ Multi-Cloud Architecture Overview (Experiment scoped - {repo_name})")
        overview_lines.append("")
        overview_lines.append(f"**Detected Providers:** {', '.join([p.title() for p in cloud_providers])}")
        overview_lines.append("")
        overview_lines.append("## 🌍 Cross-Cloud Topology")
        overview_lines.append("")
        overview_lines.append("```mermaid")
        overview_lines.append("flowchart TB")
        overview_lines.append("  internet[🌐 Internet]")
        overview_lines.append("  ")
        
        # Create subgraphs for each provider
        for idx, provider in enumerate(cloud_providers):
            provider_title = provider.title()
            overview_lines.append(f"  subgraph {provider_title.lower()}_cloud[{provider_title}]")
            overview_lines.append(f"    {provider_title.lower()}_services[🧩 Services]")
            overview_lines.append(f"    {provider_title.lower()}_data[🗄️ Data Stores]")
            overview_lines.append(f"    {provider_title.lower()}_identity[🔐 Identity/Secrets]")
            overview_lines.append("  end")
            overview_lines.append("  ")
        
        # Cross-cloud connections
        overview_lines.append("  %% Internet ingress")
        for provider in cloud_providers:
            provider_lower = provider.lower()
            overview_lines.append(f"  internet -->|HTTPS| {provider_lower}_services")
        
        overview_lines.append("  ")
        
        # Show DETECTED connectivity (solid lines) vs ASSUMED (dashed lines)
        if has_vpn or has_private_link or has_peering:
            overview_lines.append("  %% DETECTED cross-cloud connectivity")
            if len(cloud_providers) == 2:
                p1, p2 = [p.lower() for p in cloud_providers]
                if has_vpn:
                    overview_lines.append(f"  {p1}_services -->|🔒 VPN Tunnel| {p2}_services")
                elif has_private_link:
                    overview_lines.append(f"  {p1}_services -->|🔒 Private Link| {p2}_services")
                elif has_peering:
                    overview_lines.append(f"  {p1}_services -->|🔒 VNet/VPC Peering| {p2}_services")
            else:
                # Multi-cloud: show hub-spoke if VPN detected
                hub = cloud_providers[0].lower()
                for spoke in [p.lower() for p in cloud_providers[1:]]:
                    if has_vpn:
                        overview_lines.append(f"  {hub}_services -->|🔒 VPN| {spoke}_services")
                    elif has_private_link:
                        overview_lines.append(f"  {hub}_services -->|🔒 Private Link| {spoke}_services")
        else:
            overview_lines.append("  %% No cross-cloud network connectivity detected (investigate)")
            if len(cloud_providers) == 2:
                p1, p2 = [p.lower() for p in cloud_providers]
                overview_lines.append(f"  {p1}_services -.->|❓ Connectivity?| {p2}_services")
        
        # Data replication
        if has_data_replication:
            overview_lines.append("  ")
            overview_lines.append("  %% DETECTED data replication")
            if len(cloud_providers) == 2:
                p1, p2 = [p.lower() for p in cloud_providers]
                overview_lines.append(f"  {p1}_data -->|📦 Geo-Replication| {p2}_data")
        
        # Federated identity / shared secrets
        overview_lines.append("  ")
        if has_federation or has_shared_secrets:
            overview_lines.append("  %% DETECTED identity federation")
            overview_lines.append("  shared_identity[🔑 Federated Identity]")
            for provider in cloud_providers:
                provider_lower = provider.lower()
                overview_lines.append(f"  shared_identity -->|Trust| {provider_lower}_identity")
        elif has_cross_creds:
            overview_lines.append("  %% WARNING: Cross-cloud credentials detected (security risk)")
            for provider in cloud_providers:
                provider_lower = provider.lower()
                overview_lines.append(f"  {provider_lower}_identity -.->|⚠️ Hard-coded Creds| {provider_lower}_services")
        else:
            overview_lines.append("  %% No federated identity detected (investigate)")
            overview_lines.append("  shared_identity[❓ Identity Provider]")
            for provider in cloud_providers:
                provider_lower = provider.lower()
                overview_lines.append(f"  shared_identity -.->|❓| {provider_lower}_identity")
        
        overview_lines.append("  ")
        overview_lines.append("  style shared_identity stroke:#f59f00,stroke-width:3px,stroke-dasharray: 5 5")
        for provider in cloud_providers:
            provider_lower = provider.lower()
            overview_lines.append(f"  style {provider_lower}_cloud stroke:#0066cc,stroke-width:2px")
        
        overview_lines.append("```")
        overview_lines.append("")
        overview_lines.append("**Legend:**")
        overview_lines.append("- **Line Types:** Solid = DETECTED in IaC | Dashed = investigate/validate")
        overview_lines.append("- **Border Colors:** 🔵 Blue = Cloud Provider Boundary | 🟠 Orange = Shared Identity")
        overview_lines.append("- **Symbols:** ⚠️ = security concern (e.g., hard-coded credentials)")
        overview_lines.append("")
        
        # Detected connectivity details
        overview_lines.append("## 🔗 Detected Cross-Cloud Integration")
        overview_lines.append("")
        
        if any([has_vpn, has_private_link, has_peering, has_federation, has_cross_creds, has_shared_secrets, has_data_replication]):
            if connectivity["vpn_tunnels"]:
                overview_lines.append("### ✅ VPN Tunnels")
                for item in connectivity["vpn_tunnels"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
            
            if connectivity["private_links"]:
                overview_lines.append("### ✅ Private Links")
                for item in connectivity["private_links"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
            
            if connectivity["vnet_peering"]:
                overview_lines.append("### ✅ VNet/VPC Peering")
                for item in connectivity["vnet_peering"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
            
            if connectivity["federated_identity"]:
                overview_lines.append("### ✅ Federated Identity")
                for item in connectivity["federated_identity"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
            
            if connectivity["service_principals"]:
                overview_lines.append("### ⚠️ Cross-Cloud Credentials (Security Risk)")
                for item in connectivity["service_principals"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
                overview_lines.append("**Security Note:** Hard-coded cross-cloud credentials are a security risk. Use federated identity instead.")
                overview_lines.append("")
            
            if connectivity["shared_secrets"]:
                overview_lines.append("### ✅ Shared Secrets Access")
                for item in connectivity["shared_secrets"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
            
            if connectivity["cross_cloud_data"]:
                overview_lines.append("### ✅ Data Replication")
                for item in connectivity["cross_cloud_data"]:
                    overview_lines.append(f"- {item}")
                overview_lines.append("")
        else:
            overview_lines.append("**No cross-cloud connectivity detected in IaC.** This may indicate:")
            overview_lines.append("- Truly isolated cloud environments (good security posture)")
            overview_lines.append("- Connectivity configured outside IaC (manual, need to investigate)")
            overview_lines.append("- Detection patterns incomplete (validate actual deployment)")
            overview_lines.append("")
            # Hub-spoke pattern with first provider as hub
            hub = cloud_providers[0].lower()
            for spoke in [p.lower() for p in cloud_providers[1:]]:
                overview_lines.append(f"  {hub}_services -.->|VPN/Interconnect| {spoke}_services")
        
        overview_lines.append("  ")
        overview_lines.append("  %% Shared identity (typical pattern)")
        overview_lines.append("  shared_identity[🔑 Shared Identity Provider]")
        for provider in cloud_providers:
            provider_lower = provider.lower()
            overview_lines.append(f"  shared_identity -.->|Federation| {provider_lower}_identity")
        
        overview_lines.append("  ")
        overview_lines.append("  style shared_identity stroke:#f59f00,stroke-width:3px,stroke-dasharray: 5 5")
        for provider in cloud_providers:
            provider_lower = provider.lower()
            overview_lines.append(f"  style {provider_lower}_cloud stroke:#0066cc,stroke-width:2px")
        
        overview_lines.append("```")
        overview_lines.append("")
        overview_lines.append("**Key:** Solid lines = confirmed traffic paths, Dashed lines = typical patterns to investigate")
        overview_lines.append("")
        overview_lines.append("## 🔗 Cross-Cloud Integration Points")
        overview_lines.append("")
        overview_lines.append("### Connectivity Patterns to Investigate")
        overview_lines.append("- **Network:** VPN tunnels, ExpressRoute, Direct Connect, Cloud Interconnect")
        overview_lines.append("- **Identity:** Federated auth, SAML/OIDC, cross-cloud service principals")
        overview_lines.append("- **Data:** Cross-region replication, backup strategies, data residency")
        overview_lines.append("- **Secrets:** Shared Key Vault/KMS access, secret rotation across clouds")
        overview_lines.append("- **Monitoring:** Unified logging/SIEM, cross-cloud alerting")
        overview_lines.append("")
        overview_lines.append("## 📊 Provider-Specific Diagrams")
        overview_lines.append("")
        for provider in cloud_providers:
            provider_title = provider.title()
            overview_lines.append(f"- [{provider_title} Architecture](Architecture_{provider_title}.md)")
        overview_lines.append("")
        overview_lines.append("## 🛡️ Security Considerations")
        overview_lines.append("")
        overview_lines.append("### Cross-Cloud Attack Surface")
        overview_lines.append("1. **Network Perimeter:** Each cloud provider adds attack surface")
        overview_lines.append("2. **Identity Federation:** Misconfigured SSO = lateral movement between clouds")
        overview_lines.append("3. **Data Sovereignty:** Cross-cloud data flows may violate compliance")
        overview_lines.append("4. **Credential Management:** Secrets spanning multiple clouds increase risk")
        overview_lines.append("5. **Monitoring Gaps:** Unified SIEM required to detect cross-cloud attacks")
        overview_lines.append("")
        overview_lines.append("### Priority Security Reviews")
        overview_lines.append("- 🔴 **P0:** Validate network isolation between clouds (no open internet bridges)")
        overview_lines.append("- 🔴 **P0:** Audit federated identity trust relationships")
        overview_lines.append("- 🟠 **P1:** Review cross-cloud data classification and encryption")
        overview_lines.append("- 🟠 **P1:** Verify unified monitoring/SIEM covers all providers")
        overview_lines.append("- 🟡 **P2:** Audit secret management and rotation across clouds")
        overview_lines.append("")
        overview_lines.append("## 📝 Notes")
        overview_lines.append(f"- 🗓️ **Last updated:** {now_uk()}")
        overview_lines.append(f"- **Repository:** {repo_name}")
        overview_lines.append("- **Scope:** Cross-cloud patterns inferred from IaC; validate actual deployment")
        overview_lines.append("- This overview is generated for experiment isolation; see provider-specific files for details")
        
        overview_path.write_text("\n".join(overview_lines).rstrip() + "\n", encoding="utf-8")
        
        probs = validate_markdown_file(overview_path, fix=True)
        errs = [p for p in probs if p.level == "ERROR"]
        warns = [p for p in probs if p.level == "WARN"]
        for p in warns:
            line = f":{p.line}" if p.line else ""
            print(f"WARN: {overview_path}{line} - {p.message}")
        if errs:
            raise SystemExit(f"Mermaid validation failed for {overview_path}: {errs[0].message}")
        
        # Generate questions for missing connectivity information and append to Knowledge files
        questions = []
        
        if not has_vpn and not has_private_link and not has_peering:
            questions.append("❓ **Cross-Cloud Network Connectivity:** How do services in {} communicate? (VPN tunnel, ExpressRoute/Direct Connect, internet-only, or isolated?)".format(
                " and ".join([p.title() for p in cloud_providers])
            ))
        
        if not has_federation and not has_cross_creds:
            questions.append("❓ **Cross-Cloud Authentication:** How do applications authenticate across {} and {}? (Federated identity/SAML, separate identity providers, or shared credentials?)".format(
                cloud_providers[0].title(), " and ".join([p.title() for p in cloud_providers[1:]])
            ))
        
        if not has_shared_secrets:
            questions.append("❓ **Secrets Management:** How are secrets shared between {} environments? (Separate Key Vaults/KMS per cloud, cross-cloud access, or manual sync?)".format(
                " and ".join([p.title() for p in cloud_providers])
            ))
        
        if not has_data_replication:
            questions.append("❓ **Data Residency & Replication:** Is data replicated between {} and {}? (Geo-redundant storage, database replication, backup strategy, data sovereignty requirements?)".format(
                cloud_providers[0].title(), " and ".join([p.title() for p in cloud_providers[1:]])
            ))
        
        # Always ask about monitoring in multi-cloud
        questions.append("❓ **Unified Monitoring:** Is there a unified SIEM/logging platform covering all {} environments? (Centralized Log Analytics/CloudWatch/Stackdriver, cross-cloud alerting, or separate per cloud?)".format(
            " and ".join([p.title() for p in cloud_providers])
        ))
        
        # Append questions to each provider's Knowledge file
        if questions:
            knowledge_dir = OUTPUT_KNOWLEDGE_DIR
            if summary_dir and "Learning/experiments" in str(summary_dir):
                # Experiment mode: write to experiment-scoped knowledge dir
                knowledge_dir = summary_dir.parent / "Knowledge"
            
            knowledge_dir.mkdir(parents=True, exist_ok=True)
            
            for provider in cloud_providers:
                provider_title = provider.title()
                knowledge_file = knowledge_dir / f"{provider_title}.md"
                
                if knowledge_file.exists():
                    content = knowledge_file.read_text(encoding="utf-8")
                    
                    # Check if ## Unknowns section exists
                    if "## Unknowns" in content or "## ❓ Open Questions" in content:
                        # Append to existing Unknowns section
                        lines = content.split("\n")
                        unknowns_idx = None
                        next_section_idx = None
                        
                        for i, line in enumerate(lines):
                            if line.strip() in ["## Unknowns", "## ❓ Open Questions"]:
                                unknowns_idx = i
                            elif unknowns_idx is not None and line.startswith("## ") and i > unknowns_idx:
                                next_section_idx = i
                                break
                        
                        if unknowns_idx is not None:
                            # Insert questions after Unknowns heading
                            insert_pos = next_section_idx if next_section_idx else len(lines)
                            
                            # Check if questions already exist (avoid duplicates)
                            existing_questions = "\n".join(lines[unknowns_idx:insert_pos])
                            new_questions = []
                            for q in questions:
                                if q not in existing_questions:
                                    new_questions.append(q)
                            
                            if new_questions:
                                # Insert before next section or at end
                                lines.insert(insert_pos, "")
                                for q in reversed(new_questions):
                                    lines.insert(insert_pos, q)
                                
                                knowledge_file.write_text("\n".join(lines), encoding="utf-8")
                                print(f"Added {len(new_questions)} cross-cloud questions to {knowledge_file}")
                    else:
                        # No Unknowns section, append to end
                        content = content.rstrip() + "\n\n"
                        content += "## ❓ Open Questions\n\n"
                        content += "\n".join(questions) + "\n"
                        knowledge_file.write_text(content, encoding="utf-8")
                        print(f"Created ## ❓ Open Questions section with {len(questions)} questions in {knowledge_file}")
                else:
                    # Create new knowledge file with questions
                    content = f"# {provider_title} Knowledge\n\n"
                    content += "## Confirmed\n\n"
                    content += "_No confirmed information yet._\n\n"
                    content += "## Assumptions\n\n"
                    content += "_No assumptions yet._\n\n"
                    content += "## ❓ Open Questions\n\n"
                    content += "\n".join(questions) + "\n"
                    knowledge_file.write_text(content, encoding="utf-8")
                    print(f"Created new knowledge file {knowledge_file} with {len(questions)} questions")
            print(f"WARN: {overview_path}{line} - {p.message}")
        if errs:
            raise SystemExit(f"Mermaid validation failed for {overview_path}: {errs[0].message}")
    
    # Generate per-resource summaries for all cloud providers
    # Cleanup old top-level resource summaries so Cloud/ contains only Architecture_*.md.
    stale_patterns = (
        "VM_*.md",
        "AKS_*.md",
        "ServiceAccount_*.md",
        "SQL_*.md",
        "KeyVault_*.md",
        "Storage_*.md",
        "AppService_*.md",
    )
    cloud_summary_dir = summary_dir / "Cloud"
    cloud_summary_dir.mkdir(parents=True, exist_ok=True)
    for pattern in stale_patterns:
        for stale_file in cloud_summary_dir.glob(pattern):
            try:
                stale_file.unlink()
            except OSError:
                pass

    for provider in cloud_providers:
        print(f"Generating resource summaries for {provider}...")
        resource_files = write_cloud_resource_summaries(
            repo=repo,
            provider=provider,
            summary_dir=cloud_summary_dir,
        )
        print(f"Generated {len(resource_files)} resource summary files")

    return first_out_path


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
    dotnet_info: dict[str, str | None] = None,
    summary_dir: Path | None = None,
) -> Path:
    sdir = summary_dir if summary_dir else OUTPUT_SUMMARY_DIR
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "Repos").mkdir(parents=True, exist_ok=True)
    out_path = sdir / "Repos" / f"{repo_name}.md"

    lang_lines = "\n".join([f"- {l} — evidence: `{e}`" for l, e in langs]) if langs else "- (none detected)"

    provider_line = ", ".join(providers) if providers else "Unknown"
    repo_url = "N/A"

    tf_resource_types = detect_terraform_resources(iter_files(repo), repo)
    tf_module_refs = detect_terraform_module_references(iter_files(repo), repo)
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
    has_vms = any("virtual_machine" in t for t in tf_resource_types)
    has_aks = "azurerm_kubernetes_cluster" in tf_resource_types
    has_nsg = "azurerm_network_security_group" in tf_resource_types
    has_storage = "azurerm_storage_account" in tf_resource_types and not has_state_backend  # General storage, not just terraform state
    
    # Extract detailed resources for comprehensive diagram
    vm_list = []
    aks_list = []
    nsg_protocol_label = ""
    if provider_line == "Azure" or "Azure" in providers:
        if has_vms:
            vm_list = extract_vm_names_with_os(iter_files(repo), repo, "azure")
        if has_aks:
            aks_list = extract_resource_names_with_property(iter_files(repo), repo, "azurerm_kubernetes_cluster", "name")
        if has_nsg:
            # Check for permissive NSG rules - just detect "All Ports" pattern
            try:
                nsg_protocol_label = _extract_nsg_allowed_protocols(iter_files(repo), repo)
            except Exception:
                nsg_protocol_label = ""

    # Diagram building: keep labels ASCII and avoid braces/parentheses for renderer compatibility.
    diagram_lines: list[str] = ["flowchart TB"]
    
    # Add internet node for attack surface visualization
    if provider_line == "Azure" or "Azure" in providers:
        has_internet_exposure = has_webapp or has_aks or has_vms or has_sql or has_kv or has_storage
        if has_internet_exposure:
            diagram_lines.append("  internet[Internet]")
    
    # Track assumptions for dashed borders
    assumptions: list[str] = []

    if provider_line == "Azure" or "Azure" in providers:
        # Check if we have VNet topology (VMs/AKS need VNet subgraph)
        has_vnet_resources = has_vms or has_aks or has_nsg
        
        if has_vnet_resources:
            # VNet subgraph for compute resources
            vnet_name = "VNet"
            if network_info and isinstance(network_info, dict) and network_info.get("vnets"):
                vnet_name = network_info["vnets"][0].get("name", "VNet") if isinstance(network_info["vnets"][0], dict) else "VNet"
            diagram_lines.append(f"  subgraph vnet[{vnet_name}]")
            
            # Add VMs
            for vm_name, os_info, _ in vm_list:
                os_short = os_info.replace("OS: ", "")
                diagram_lines.append(f"    vm_{vm_name}[{vm_name} VM, {os_short}]")
            
            # Add AKS
            for aks_name in aks_list:
                diagram_lines.append(f"    aks_{aks_name}[{aks_name} AKS]")
            
            # Add NSG
            if has_nsg:
                diagram_lines.append("    nsg[Network Security Group]")
            
            diagram_lines.append("  end")
            
            # PaaS subgraph
            has_paas = has_webapp or has_sql or has_kv or has_storage
            if has_paas:
                diagram_lines.append("  subgraph paas[Azure PaaS]")
        else:
            # No VNet, just Azure subgraph
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
                # Avoid HTML and parentheses inside Mermaid labels for compatibility across renderers.
                web_label = f"{web_label} - {hosting_info['type']}"
            diagram_lines.append(f"    web[{web_label}]")
        if has_kv:
            diagram_lines.append("    kv[Key Vault]")
        if has_sql:
            diagram_lines.append("    sql[Azure SQL]")
        if has_storage and not has_state_backend:
            diagram_lines.append("    sa[Storage Account]")
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
        
        # Close PaaS or Azure subgraph
        diagram_lines.append("  end")

        if detect_ci(repo) == "Azure Pipelines":
            diagram_lines.append("  pipeline[Azure Pipelines]")

        # Connection flows - start with internet exposure (attack surface)
        internet_connections = []
        
        # Internet to App Service
        if has_webapp:
            if has_appgw:
                diagram_lines.append("  internet --> appgw")
                diagram_lines.append("  appgw --> web")
                internet_connections.append(0)  # Track for red styling
            elif has_frontdoor:
                diagram_lines.append("  internet --> fd")
                diagram_lines.append("  fd --> web")
                internet_connections.append(0)
            else:
                diagram_lines.append("  internet -->|HTTPS| web")
                internet_connections.append(len(diagram_lines) - 5)  # Offset for TB declaration + internet node
        
        # Internet to AKS
        for aks_name in aks_list:
            diagram_lines.append(f"  internet --> aks_{aks_name}")
            internet_connections.append(len(diagram_lines) - 5)
        
        # Internet to PaaS (public endpoints)
        if has_sql:
            diagram_lines.append("  internet --> sql")
            internet_connections.append(len(diagram_lines) - 5)
        if has_kv:
            diagram_lines.append("  internet --> kv")
            internet_connections.append(len(diagram_lines) - 5)
        if has_storage and not has_state_backend:
            diagram_lines.append("  internet --> sa")
            internet_connections.append(len(diagram_lines) - 5)
        
        # Internet to NSG (protects VMs)
        if has_nsg and vm_list:
            if nsg_protocol_label:
                diagram_lines.append(f"  internet -->|{nsg_protocol_label}| nsg")
            else:
                diagram_lines.append("  internet --> nsg")
            internet_connections.append(len(diagram_lines) - 5)
        
        # NSG to VMs
        if has_nsg:
            for vm_name, _, _ in vm_list:
                diagram_lines.append(f"  nsg --> vm_{vm_name}")
        
        # App Service connections to data stores
        if has_webapp:
            if has_sql:
                diagram_lines.append("  web --> sql")
            if has_kv:
                diagram_lines.append("  web --> kv")
            if has_storage and not has_state_backend:
                diagram_lines.append("  web --> sa")
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
        
        # VM connections to PaaS
        for vm_name, _, _ in vm_list:
            if has_sql:
                diagram_lines.append(f"  vm_{vm_name} --> sql")
            if has_kv:
                diagram_lines.append(f"  vm_{vm_name} --> kv")
            if has_storage and not has_state_backend:
                diagram_lines.append(f"  vm_{vm_name} --> sa")
        
        # AKS connections to PaaS
        for aks_name in aks_list:
            if has_sql:
                diagram_lines.append(f"  aks_{aks_name} --> sql")
            if has_kv:
                diagram_lines.append(f"  aks_{aks_name} --> kv")
            if has_storage and not has_state_backend:
                diagram_lines.append(f"  aks_{aks_name} --> sa")

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
        style_lines.append("  style appgw stroke:#ff6b6b,stroke-width:3px")
    if has_frontdoor:
        style_lines.append("  style fd stroke:#ff6b6b,stroke-width:3px")
    
    # API Management - network/gateway layer (distinct from app + data + identity)
    if has_apim or "apim" in "\n".join(diagram_lines):
        style_lines.append("  style apim stroke:#1971c2,stroke-width:2px")
    
    # Application service - blue border (trusted internal)
    if has_webapp:
        style_lines.append("  style web stroke:#0066cc,stroke-width:2px")
    
    # Authentication service - green border (security control)
    if auth_service:
        style_lines.append("  style auth stroke:#00cc00,stroke-width:3px")
    
    # Backend services - purple border
    for idx in range(1, len(backend_services) + 1):
        style_lines.append(f"  style backend{idx} stroke:#9966cc,stroke-width:2px")
    
    # Data/infrastructure - green thicker border (stands out better than gray)
    if has_sql:
        style_lines.append("  style sql stroke:#00aa00,stroke-width:3px")
    if has_kv:
        # Identity/secrets should be consistently highlighted across diagrams.
        style_lines.append("  style kv stroke:#f59f00,stroke-width:2px")

    # CI/CD + state are part of the main architecture story; keep their borders consistent too.
    if "pipeline" in "\n".join(diagram_lines):
        style_lines.append("  style pipeline stroke:#f59f00,stroke-width:2px")
    if has_state_backend:
        style_lines.append("  style tfstate stroke:#00aa00,stroke-width:3px")
    
    if style_lines:
        diagram_lines.append("")
        diagram_lines.append("  %% Styling")
        diagram_lines.extend(style_lines)

    # Check if detailed cloud architecture exists
    cloud_arch_ref = ""
    cloud_arch_path = sdir / "Cloud" / f"Architecture_{providers[0]}.md" if providers else None
    if cloud_arch_path and cloud_arch_path.exists():
        cloud_arch_ref = f"📋 **[View Detailed Cloud Architecture](Cloud/Architecture_{providers[0]}.md)** - Complete network topology with all resources, NSG rules, and connections.\n\n"
    
    content = (
        f"# 🟣 Repo {repo_name}\n\n"
        "## 🗺️ Architecture Diagram\n\n"
        + cloud_arch_ref
        + "```mermaid\n"
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
    )
    
    # Add Related Repos section if Terraform module references found
    if tf_module_refs:
        content += "## 🔗 Related Repos\n"
        content += "**Detected from Terraform module source references:**\n\n"
        content += "| Repo | Module | Detected In | Line |\n"
        content += "|------|--------|-------------|------|\n"
        for ref in tf_module_refs:
            content += f"| `{ref['repo_name']}` | `{ref['module_name']}` | `{ref['detected_in_file']}` | L{ref['line']} |\n"
        content += "\n"
        content += "⚠️ **Action Required:** These repos may contain infrastructure for components referenced in this codebase. Consider scanning them for complete coverage.\n\n"
    
    content += (
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
        f"- **Runtime Version:** {(dotnet_info['version'] or 'Unknown') if dotnet_info else 'Unknown'}" + (f" (from `{dotnet_info['source']}`)" if dotnet_info and dotnet_info['source'] else "") + "\n"
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
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Base output directory for Summary/ and Knowledge/ (default: Output/). Use for experiment isolation.",
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
    
    # Determine output directories (support experiment isolation)
    if args.output_dir:
        output_base = Path(args.output_dir).expanduser().resolve()
        summary_dir = output_base / "Summary"
        knowledge_dir = output_base / "Knowledge"
    else:
        output_base = None
        summary_dir = OUTPUT_SUMMARY_DIR
        knowledge_dir = OUTPUT_KNOWLEDGE_DIR
    
    # Extract experiment ID from output directory (e.g., "008_Resource_Summaries_Test" -> "008")
    experiment_id = None
    if output_base:
        experiment_id = output_base.name
        # Clean up experiment ID (remove descriptive suffix if present)
        if '_' in experiment_id:
            exp_num = experiment_id.split('_')[0]
            if exp_num.isdigit():
                experiment_id = exp_num
    
    # Register repository in database
    if DB_AVAILABLE and experiment_id:
        try:
            repo_id, db_repo_name = insert_repository(
                experiment_id=experiment_id,
                repo_path=repo,
                repo_type="Infrastructure"  # Will be refined later
            )
            print(f"✅ Registered repository in database: {db_repo_name} (experiment {experiment_id})")
        except Exception as e:
            print(f"WARN: Failed to register repository in database: {e}", file=sys.stderr)

    files = iter_files(repo)
    langs = detect_languages(files, repo)
    lang_names = [l for l, _ in langs]
    dotnet_info = detect_dotnet_version(iter_files(repo), repo)
    rtype = infer_repo_type(lang_names, repo_name)
    purpose, purpose_ev = repo_purpose(repo, repo_name)
    ci = detect_ci(repo)
    providers = detect_cloud_provider(files, repo)

    extra: list[Evidence] = []
    if purpose_ev:
        extra.append(purpose_ev)
    if dotnet_info["version"]:
        extra.append(Evidence(label=f".NET Version: {dotnet_info['version']}", path=dotnet_info["source"]))
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

    repos_md = ensure_repos_knowledge(repos_root, knowledge_dir=knowledge_dir)
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
        dotnet_info=dotnet_info,
        summary_dir=summary_dir,
    )

    # Experiment isolation: also generate an experiment-scoped provider architecture summary with TL;DR.
    if args.output_dir:
        output_base = Path(args.output_dir).expanduser().resolve()
        _ = write_experiment_cloud_architecture_summary(
            repo=repo,
            repo_name=repo_name,
            providers=providers,
            summary_dir=summary_dir,
            findings_dir=output_base / "Findings",
            repo_summary_path=summary_path,
        )

    print("== Context discovery complete ==")
    print(f"repo: {repo}")
    print(f"summary: {summary_path}")
    print(f"knowledge: {repos_md}")
    print(f"timestamp: {now_uk()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
