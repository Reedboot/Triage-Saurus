#!/usr/bin/env python3
"""Initialize the CozoDB schema for Triage-Saurus learning database."""

from pathlib import Path
from typing import Dict
import sys
from pycozo.client import Client

# Database location
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage.cozo"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _create_relation(db: Client, name: str, schema: str) -> None:
    """Create a CozoDB relation only if it does not already exist."""
    existing = {row[0] for row in db.relations()['rows']}
    if name not in existing:
        db.run(schema)


def _ensure_seq(db: Client, name: str) -> None:
    """Ensure a sequence counter row exists for *name* (starts at 0)."""
    result = db.run('?[v] := *ts_seqs[$n, v]', {'n': name})
    if not result['rows']:
        db.put('ts_seqs', [{'name': name, 'value': 0}])


def _next_id(db: Client, seq_name: str) -> int:
    """Return the next integer ID for *seq_name* and persist the updated counter.

    Note: this is safe for single-process use.  CozoDB's embedded RocksDB
    backend serialises writes, so concurrent *processes* sharing the same
    database directory should use an external lock or a higher-level
    coordination mechanism.
    """
    result = db.run('?[v] := *ts_seqs[$n, v]', {'n': seq_name})
    current = result['rows'][0][0] if result['rows'] else 0
    new_id = current + 1
    db.put('ts_seqs', [{'name': seq_name, 'value': new_id}])
    return new_id


# ---------------------------------------------------------------------------
# Topology backfills
# ---------------------------------------------------------------------------

def apply_topology_backfills(db: Client) -> Dict[str, int]:
    """Fill in derived/denormalised fields that may be null in older rows."""
    updates: Dict[str, int] = {}

    # resource_connections: fill source_repo_id from resources
    rows = db.run('''
        ?[conn_id, repo_id] :=
            *resource_connections{id: conn_id, source_resource_id: src_id, source_repo_id: null},
            *resources{id: src_id, repo_id}
    ''')['rows']
    count = 0
    for conn_id, repo_id in rows:
        db.update('resource_connections', [{'id': conn_id, 'source_repo_id': repo_id}])
        count += 1
    updates['resource_connections_source_repo_id'] = count

    # resource_connections: fill target_repo_id from resources
    rows = db.run('''
        ?[conn_id, repo_id] :=
            *resource_connections{id: conn_id, target_resource_id: tgt_id, target_repo_id: null},
            *resources{id: tgt_id, repo_id}
    ''')['rows']
    count = 0
    for conn_id, repo_id in rows:
        db.update('resource_connections', [{'id': conn_id, 'target_repo_id': repo_id}])
        count += 1
    updates['resource_connections_target_repo_id'] = count

    # is_cross_repo: set based on source/target repo difference
    rows = db.run('''
        ?[id, is_cross] :=
            *resource_connections{id, source_repo_id: s, target_repo_id: t},
            s != null, t != null,
            is_cross = if(s != t, true, false)
    ''')['rows']
    count = 0
    for conn_id, is_cross in rows:
        db.update('resource_connections', [{'id': conn_id, 'is_cross_repo': is_cross}])
        count += 1
    updates['resource_connections_is_cross_repo'] = count

    # findings: fill repo_id from resources
    rows = db.run('''
        ?[fid, repo_id] :=
            *findings{id: fid, resource_id, repo_id: null},
            resource_id != null,
            *resources{id: resource_id, repo_id}
    ''')['rows']
    count = 0
    for fid, repo_id in rows:
        db.update('findings', [{'id': fid, 'repo_id': repo_id}])
        count += 1
    updates['findings_repo_id'] = count

    # resource_nodes: initialise null aliases to empty JSON array
    rows = db.run('?[id] := *resource_nodes{id, aliases: null}')['rows']
    count = 0
    for (node_id,) in rows:
        db.update('resource_nodes', [{'id': node_id, 'aliases': '[]'}])
        count += 1
    updates['resource_nodes_aliases'] = count

    return updates


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_schema(db: Client) -> None:
    """Create all CozoDB relations and seed reference data."""

    # ------------------------------------------------------------------ seqs
    _create_relation(db, 'ts_seqs', ':create ts_seqs { name: String => value: Int }')

    # ----------------------------------------------------------- experiments
    _create_relation(db, 'experiments', '''
        :create experiments {
            id: String =>
            name: String?, parent_experiment_id: String?,
            agent_versions: String?, script_versions: String?,
            strategy_version: String?, model: String?,
            changes_description: String?, changes_files: String?,
            hypothesis: String?, repos: String?,
            status: String, started_at: String?, completed_at: String?,
            duration_sec: Int?, tokens_used: Int?,
            findings_count: Int?, high_value_count: Int?,
            avg_score: Float?, false_positives: Int?,
            false_negatives: Int?, accuracy_rate: Float?,
            precision: Float?, recall: Float?,
            human_reviewed: Bool, human_quality_rating: Int?,
            notes: String?, created_by: String?, tags: String?
        }
    ''')

    # ---------------------------------------------------------- repositories
    _create_relation(db, 'repositories', '''
        :create repositories {
            id: Int =>
            experiment_id: String, repo_name: String,
            repo_url: String?, repo_type: String?,
            primary_language: String?,
            files_scanned: Int?, iac_files_count: Int?,
            code_files_count: Int?, scanned_at: String?
        }
    ''')


    _create_relation(db, 'resources', '''
        :create resources {
            id: Int =>
            experiment_id: String, repo_id: Int,
            resource_name: String, resource_type: String,
            provider: String?, region: String?,
            parent_resource_id: Int?,
            discovered_by: String?, discovery_method: String?,
            source_file: String?, source_line_start: Int?,
            source_line_end: Int?,
            status: String, first_seen: String?, last_seen: String?,
            display_label: String?, tags: String?
        }
    ''')

    # ---------------------------------------------------- resource_properties
    _create_relation(db, 'resource_properties', '''
        :create resource_properties {
            id: Int =>
            resource_id: Int, property_key: String,
            property_value: String?, property_type: String?,
            is_security_relevant: Bool
        }
    ''')

    # ------------------------------------------------------- resource_context
    _create_relation(db, 'resource_context', '''
        :create resource_context {
            resource_id: Int =>
            business_criticality: String?, data_classification: String?,
            environment: String?, purpose: String?, owner_team: String?,
            cost_per_month: Float?, usage_pattern: String?,
            user_count: Int?, uptime_requirement: String?,
            compliance_scope: String?, last_updated: String?
        }
    ''')

    # -------------------------------------------------- resource_connections
    _create_relation(db, 'resource_connections', '''
        :create resource_connections {
            id: Int =>
            experiment_id: String,
            source_resource_id: Int, target_resource_id: Int,
            source_repo_id: Int?, target_repo_id: Int?,
            is_cross_repo: Bool,
            connection_type: String?, protocol: String?, port: String?,
            authentication: String?, authorization: String?,
            auth_method: String?, is_encrypted: Bool?,
            via_component: String?, notes: String?
        }
    ''')

    # ------------------------------------------------------ trust_boundaries
    _create_relation(db, 'trust_boundaries', '''
        :create trust_boundaries {
            id: Int =>
            experiment_id: String, name: String,
            boundary_type: String?, provider: String?, region: String?,
            description: String?, notes: String?, created_at: String?
        }
    ''')

    _create_relation(db, 'trust_boundary_members', '''
        :create trust_boundary_members {
            trust_boundary_id: Int, resource_id: Int =>
        }
    ''')

    # ------------------------------------------------------------ data_flows
    _create_relation(db, 'data_flows', '''
        :create data_flows {
            id: Int =>
            experiment_id: String, name: String,
            flow_type: String?, description: String?,
            notes: String?, created_at: String?
        }
    ''')

    _create_relation(db, 'data_flow_steps', '''
        :create data_flow_steps {
            id: Int =>
            flow_id: Int, step_order: Int,
            resource_id: Int?, component_label: String?,
            protocol: String?, port: String?,
            auth_method: String?, is_encrypted: Bool?,
            notes: String?
        }
    ''')

    # --------------------------------------------------------------- findings
    _create_relation(db, 'findings', '''
        :create findings {
            id: Int =>
            experiment_id: String, repo_id: Int?, resource_id: Int?,
            title: String, description: String?,
            category: String?, severity_score: Int?,
            base_severity: String?, overall_score: String?,
            evidence_location: String?,
            source_file: String?, source_line_start: Int?,
            source_line_end: Int?, finding_path: String?,
            detected_by: String?, detection_method: String?,
            status: String, code_snippet: String?,
            reason: String?, llm_enriched_at: String?,
            rule_id: String?, proposed_fix: String?,
            created_at: String?, updated_at: String?
        }
    ''')

    # ------------------------------------------------------- risk_score_history
    _create_relation(db, 'risk_score_history', '''
        :create risk_score_history {
            id: Int =>
            finding_id: Int, score: Float,
            scored_by: String?, rationale: String?, created_at: String?
        }
    ''')

    # ----------------------------------------------------------- remediations
    _create_relation(db, 'remediations', '''
        :create remediations {
            id: Int =>
            finding_id: Int, title: String,
            description: String?, remediation_type: String?,
            effort: String?, priority: Int?,
            code_fix: String?, reference_url: String?
        }
    ''')

    # ------------------------------------------------------- skeptic_reviews
    _create_relation(db, 'skeptic_reviews', '''
        :create skeptic_reviews {
            id: Int =>
            finding_id: Int, reviewer_type: String,
            score_adjustment: Float?, adjusted_score: Float?,
            confidence: Float?, reasoning: String?,
            key_concerns: String?, mitigating_factors: String?,
            recommendation: String?, reviewed_at: String?
        }
    ''')

    # ------------------------------------------------------ countermeasures
    _create_relation(db, 'countermeasures', '''
        :create countermeasures {
            id: Int =>
            resource_id: Int, control_name: String,
            control_type: String?, effectiveness: String?,
            notes: String?, created_at: String?
        }
    ''')

    # ------------------------------------------------------- compound_risks
    _create_relation(db, 'compound_risks', '''
        :create compound_risks {
            id: Int =>
            experiment_id: String, title: String,
            description: String?, combined_score: Float?,
            created_at: String?
        }
    ''')

    # ----------------------------------------------------- context_questions
    _create_relation(db, 'context_questions', '''
        :create context_questions {
            id: Int =>
            question_key: String, question_text: String,
            question_category: String?
        }
    ''')

    # ------------------------------------------------------- context_answers
    _create_relation(db, 'context_answers', '''
        :create context_answers {
            id: Int =>
            experiment_id: String, question_id: Int,
            answer_value: String?, answer_confidence: String?,
            evidence_source: String?, evidence_type: String?,
            answered_by: String?, answered_at: String?
        }
    ''')

    # ----------------------------------------------------- context_metadata
    _create_relation(db, 'context_metadata', '''
        :create context_metadata {
            id: Int =>
            experiment_id: String, repo_id: Int?,
            namespace: String, key: String,
            value: String?, source: String?, created_at: String?
        }
    ''')

    # ------------------------------------------------------ knowledge_facts
    _create_relation(db, 'knowledge_facts', '''
        :create knowledge_facts {
            id: Int =>
            experiment_id: String,
            subject: String?, predicate: String?, object_: String?,
            confidence: String?, source: String?, created_at: String?
        }
    ''')

    # ---------------------------------------------------- generated_diagrams
    _create_relation(db, 'generated_diagrams', '''
        :create generated_diagrams {
            id: Int =>
            experiment_id: String, repo_name: String?,
            diagram_type: String?, content: String?,
            created_at: String?
        }
    ''')

    # -------------------------------------------------- knowledge-graph nodes
    _create_relation(db, 'resource_nodes', '''
        :create resource_nodes {
            id: Int =>
            resource_type: String, terraform_name: String,
            canonical_name: String?, friendly_name: String?,
            display_label: String?, provider: String?,
            source_repo: String?,
            aliases: String, confidence: String,
            properties: String,
            created_at: String?, updated_at: String?
        }
    ''')

    _create_relation(db, 'resource_relationships', '''
        :create resource_relationships {
            id: Int =>
            source_id: Int, target_id: Int,
            relationship_type: String,
            source_repo: String?, confidence: String,
            notes: String?, created_at: String?
        }
    ''')

    _create_relation(db, 'resource_equivalences', '''
        :create resource_equivalences {
            id: Int =>
            resource_node_id: Int,
            candidate_resource_type: String,
            candidate_terraform_name: String,
            candidate_source_repo: String,
            equivalence_kind: String,
            confidence: String, evidence_level: String,
            provenance: String, context: String?,
            created_at: String?, updated_at: String?
        }
    ''')

    _create_relation(db, 'enrichment_queue', '''
        :create enrichment_queue {
            id: Int =>
            resource_node_id: Int?, relationship_id: Int?,
            gap_type: String, context: String?,
            assumption_text: String?, assumption_basis: String?,
            confidence: String, suggested_value: String?,
            status: String, resolved_by: String?,
            resolved_at: String?, rejection_reason: String?,
            created_at: String?
        }
    ''')

    # ---------------------------------------------------- lookup: providers
    _create_relation(db, 'providers', '''
        :create providers {
            id: Int =>
            key: String, friendly_name: String?, icon: String?
        }
    ''')

    # ------------------------------------------------- lookup: resource_types
    _create_relation(db, 'resource_types', '''
        :create resource_types {
            id: Int =>
            provider_id: Int?, terraform_type: String,
            friendly_name: String?, category: String?,
            icon: String?,
            is_data_store: Bool,
            is_internet_facing_capable: Bool,
            display_on_architecture_chart: Bool,
            parent_type: String?
        }
    ''')

    # ----------------------------------------------------------------- seqs
    _seq_names = [
        'repositories', 'resources', 'resource_properties', 'resource_context',
        'resource_connections', 'trust_boundaries', 'trust_boundary_members',
        'data_flows', 'data_flow_steps', 'findings', 'risk_score_history',
        'remediations', 'skeptic_reviews', 'countermeasures', 'compound_risks',
        'context_questions', 'context_answers', 'context_metadata',
        'knowledge_facts', 'generated_diagrams',
        'resource_nodes', 'resource_relationships', 'resource_equivalences',
        'enrichment_queue', 'providers', 'resource_types',
    ]
    for seq in _seq_names:
        _ensure_seq(db, seq)

    # ---------------------------------------------------------- topology fix
    backfill_stats = apply_topology_backfills(db)
    if any(backfill_stats.values()):
        summary = ", ".join(
            f"{name}={count}" for name, count in sorted(backfill_stats.items()) if count
        )
        print(f"ℹ️ Applied legacy topology backfills: {summary}")

    # ---------------------------------------------------------- seed providers
    _PROVIDERS = [
        ('azure',    'Microsoft Azure',       '☁️'),
        ('aws',      'Amazon Web Services',   '🟠'),
        ('gcp',      'Google Cloud Platform', '🔵'),
        ('alicloud', 'Alibaba Cloud',         '🟡'),
        ('oracle',   'Oracle Cloud',          '🔴'),
    ]
    for key, friendly_name, icon in _PROVIDERS:
        result = db.run('?[id] := *providers{id, key}, key = $k', {'k': key})
        if not result['rows']:
            new_id = _next_id(db, 'providers')
            db.put('providers', [{'id': new_id, 'key': key,
                                  'friendly_name': friendly_name, 'icon': icon}])

    # ---------------------------------------------------- seed resource_types
    _SEED: list[tuple] = [
        # Azure — Identity
        ("azurerm_key_vault",                          "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_key_vault_key",                      "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_key_vault_secret",                   "Key Vault",                  "Identity",   "🔑",  "azure", 0, 0),
        ("azurerm_user_assigned_identity",             "Managed Identity",           "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_role_definition",                    "Role Definition",            "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_role_assignment",                    "Role Assignment",            "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_policy_definition",                  "Policy Definition",          "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_policy_assignment",                  "Policy Assignment",          "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_policy_set_definition",              "Policy Set",                 "Identity",   "📜",  "azure", 0, 0),
        ("azurerm_client_config",                      "Client Config",              "Identity",   "🧭",  "azure", 0, 0),
        # Azure — Identity (Azure AD)
        ("azuread_application",                        "Azure AD Application",                 "Identity",   "👤",  "azure", 0, 0),
        ("azuread_application_password",               "Azure AD Application Password",        "Identity",   "🔐",  "azure", 0, 0),
        ("azuread_directory_role",                     "Azure AD Directory Role",              "Identity",   "👤",  "azure", 0, 0),
        ("azuread_directory_role_assignment",          "Azure AD Directory Role Assignment",   "Identity",   "👤",  "azure", 0, 0),
        ("azuread_domains",                            "Azure AD Domain",                      "Identity",   "👤",  "azure", 0, 0),
        ("azuread_group",                              "Azure AD Group",                       "Identity",   "👥",  "azure", 0, 0),
        ("azuread_group_member",                       "Azure AD Group Member",                "Identity",   "👥",  "azure", 0, 0),
        ("azuread_service_principal",                  "Azure AD Service Principal",           "Identity",   "👤",  "azure", 0, 0),
        ("azuread_service_principal_password",         "Azure AD Service Principal Password",  "Identity",   "🔐",  "azure", 0, 0),
        ("azuread_user",                               "Azure AD User",                        "Identity",   "👤",  "azure", 0, 0),
        ("azurerm_ssh_public_key",                     "SSH Public Key",                       "Identity",   "🔑",  "azure", 0, 0),
        # Azure — Database
        ("azurerm_mssql_server",                       "SQL Server",                 "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_sql_server",                         "SQL Server",                 "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_mssql_database",                     "SQL Database",               "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_mssql_server_security_alert_policy", "SQL Alert Policy",           "Security",   "🚨", "azure", 0, 0),
        ("azurerm_mysql_server",                       "MySQL Server",               "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_postgresql_server",                  "PostgreSQL Server",          "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_postgresql_configuration",           "PostgreSQL Server",          "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_cosmosdb_account",                   "Cosmos DB",                  "Database",   "🗃️", "azure", 1, 1),
        ("azurerm_cosmosdb_sql_database",              "Cosmos DB SQL Database",     "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_cosmosdb_sql_container",             "Cosmos DB SQL Container",    "Database",   "🗃️", "azure", 1, 0),
        ("azurerm_mssql_firewall_rule",                "SQL Firewall Rule",          "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_sql_firewall_rule",                  "SQL Firewall Rule",          "Security",   "🛡️", "azure", 0, 0),
        # Azure — Storage
        ("azurerm_storage_account",                    "Storage Account",            "Storage",    "🗄️", "azure", 1, 1),
        ("azurerm_storage_account_network_rules",      "Storage Account",            "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_storage_container",                  "Storage Container",          "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_storage_blob",                       "Storage Blob",               "Storage",    "🗄️", "azure", 1, 0),
        ("azurerm_managed_disk",                       "Managed Disk",               "Storage",    "💾", "azure", 1, 0),
        # Auth/Credentials — Identity layer, excluded from diagram nodes
        ("azurerm_storage_account_sas",                "Storage Account SAS",        "Identity",   "🔑", "azure", 0, 0),
        # Database governance config — excluded from diagram via routing filter
        ("azurerm_mssql_database_extended_auditing_policy",        "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_extended_auditing_policy",          "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_microsoft_support_auditing_policy", "SQL Auditing Policy",        "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_server_transparent_data_encryption",       "SQL Transparent Encryption", "",  "📋", "azure", 0, 0),
        ("azurerm_mssql_virtual_network_rule",                     "SQL VNet Rule",              "Security", "🛡️", "azure", 0, 0),
        # VM extensions — agents installed on VMs, excluded from diagram
        ("azurerm_virtual_machine_extension",         "VM Extension", "", "🔧", "azure", 0, 0),
        ("azurerm_linux_virtual_machine_extension",   "VM Extension", "", "🔧", "azure", 0, 0),
        ("azurerm_windows_virtual_machine_extension", "VM Extension", "", "🔧", "azure", 0, 0),
        # Azure — Compute
        ("azurerm_linux_virtual_machine",              "Linux VM",                   "Compute",    "🖥️", "azure", 0, 1),
        ("azurerm_windows_virtual_machine",            "Windows VM",                 "Compute",    "🖥️", "azure", 0, 1),
        ("azurerm_app_service",                        "App Service",                "Compute",    "🌐", "azure", 0, 1),
        ("azurerm_linux_function_app",                 "Function App",               "Compute",    "⚡", "azure", 0, 1),
        ("azurerm_windows_function_app",               "Function App",               "Compute",    "⚡", "azure", 0, 1),
        ("azurerm_linux_web_app",                      "Linux Web App",               "Compute",    "🌐", "azure", 0, 1),
        ("azurerm_service_plan",                       "Service Plan",                "Compute",    "⚙️", "azure", 0, 1),
        # Azure — Container
        ("azurerm_kubernetes_cluster",                 "AKS Cluster",                "Container",  "☸️", "azure", 0, 1),
        ("azurerm_container_registry",                 "Container Registry",         "Container",  "📦", "azure", 0, 0),
        ("azurerm_container_group",                    "Container Instance",         "Container",  "📦", "azure", 0, 1),
        # Azure — Network
        ("azurerm_application_gateway",                "Application Gateway",        "Network",    "🌐", "azure", 0, 1),
        ("azurerm_lb",                                 "Load Balancer",              "Network",    "🌐", "azure", 0, 1),
        ("azurerm_virtual_network",                    "Virtual Network",            "Network",    "🔷", "azure", 0, 0),
        ("azurerm_subnet",                             "Subnet",                     "Network",    "🔷", "azure", 0, 0),
        ("azurerm_network_interface",                  "Network Interface",          "Network",    "🔷", "azure", 0, 0),
        ("azurerm_public_ip",                          "Public IP",                  "Network",    "🌍", "azure", 0, 1),
        ("azurerm_private_endpoint",                   "Private Endpoint",           "Network",    "🔒", "azure", 0, 0),
        ("azurerm_network_interface_security_group_association", "NIC Security Group Association", "Security", "🔗", "azure", 0, 0),
        ("azurerm_network_security_rule",              "Network Security Rule",      "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_network_watcher",                    "Network Watcher",            "Monitoring", "📡", "azure", 0, 0),
        ("azurerm_network_watcher_flow_log",           "Network Watcher Flow Log",   "Monitoring", "📡", "azure", 0, 0),
        ("azurerm_resource_group",                     "Resource Group",             "Network",    "📦", "azure", 0, 0),
        ("azurerm_resources",                          "Resources",                  "Other",      "📦", "azure", 0, 0),
        # Azure — Security
        ("azurerm_network_security_group",             "Network Security Group",     "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_firewall",                           "Azure Firewall",             "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_web_application_firewall_policy",    "WAF Policy",                 "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_security_center_contact",             "Security Center Contact",    "Security",   "🛡️", "azure", 0, 0),
        ("azurerm_security_center_subscription_pricing","Security Center Pricing",    "Security",   "🛡️", "azure", 0, 0),
        # Azure — Monitoring
        ("azurerm_monitor_diagnostic_setting",         "Diagnostic Settings",        "Monitoring", "📊", "azure", 0, 0),
        ("azurerm_monitor_log_profile",                "Log Profile",                "Monitoring", "📊", "azure", 0, 0),
        ("azurerm_log_analytics_workspace",            "Log Analytics Workspace",    "Monitoring", "📊", "azure", 0, 0),
        # AWS — Storage
        ("aws_s3_bucket",                              "S3 Bucket",                  "Storage",    "🗄️", "aws",   1, 1),
        ("aws_s3_bucket_object",                       "S3 Bucket",                  "Storage",    "🗄️", "aws",   1, 0),
        ("aws_s3_bucket_policy",                       "S3 Bucket Policy",           "Storage",    "📜", "aws",   0, 0),
        ("aws_s3_bucket_public_access_block",          "Public Access Block",        "Storage",    "🔒", "aws",   0, 0),
        ("aws_ebs_volume",                             "EBS Volume",                 "Storage",    "💾", "aws",   1, 0),
        ("aws_ecr_repository",                         "ECR Repository",             "Storage",    "🗄️", "aws",   0, 0),
        ("aws_volume_attachment",                      "Volume Attachment",          "Storage",    "🔗", "aws",   0, 0),
        # AWS — Database
        ("aws_rds_cluster",                            "RDS Cluster",                "Database",   "🗃️", "aws",   1, 0),
        ("aws_db_instance",                            "RDS Instance",               "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster",                        "Neptune Cluster",            "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster_instance",               "Neptune Instance",           "Database",   "🗃️", "aws",   1, 0),
        ("aws_neptune_cluster_snapshot",               "Neptune Snapshot",           "Database",   "🗃️", "aws",   1, 0),
        ("aws_elasticsearch_domain",                   "OpenSearch Domain",          "Database",   "🔍", "aws",   1, 1),
        ("aws_elasticsearch_domain_policy",            "OpenSearch Domain",          "Database",   "🔍", "aws",   1, 0),
        ("aws_dynamodb_table",                         "DynamoDB Table",             "Database",   "🗃️", "aws",   1, 0),
        # AWS — Compute
        ("aws_ami",                                    "AMI",                        "Compute",    "🖥️", "aws",   0, 0),
        ("aws_instance",                               "EC2 Instance",               "Compute",    "🖥️", "aws",   0, 1),
        ("aws_lambda_function",                        "Lambda Function",            "Compute",    "⚡", "aws",   0, 0),
        # AWS — Container
        ("aws_ecs_cluster",                            "ECS Cluster",                "Container",  "☸️", "aws",   0, 0),
        ("aws_ecs_service",                            "ECS Service",                "Container",  "☸️", "aws",   0, 0),
        # AWS — Network
        ("aws_elb",                                    "Load Balancer",              "Network",    "🌐", "aws",   0, 1),
        ("aws_alb",                                    "App Load Balancer",          "Network",    "🌐", "aws",   0, 1),
        ("aws_lb",                                     "Network Load Balancer",      "Network",    "🌐", "aws",   0, 1),
        ("aws_lb_listener",                            "Load Balancer Listener",     "Network",    "🎧", "aws",   0, 0),
        ("aws_alb_listener",                           "Load Balancer Listener",     "Network",    "🎧", "aws",   0, 0),
        ("aws_lb_target_group",                        "Target Group",               "Network",    "🎯", "aws",   0, 0),
        ("aws_alb_target_group",                       "Target Group",               "Network",    "🎯", "aws",   0, 0),
        ("aws_lb_target_group_attachment",             "Target Attachment",          "Network",    "🔗", "aws",   0, 0),
        ("aws_eip",                                    "Elastic IP",                 "Network",    "🌍", "aws",   0, 1),
        ("aws_route",                                  "Route",                      "Network",    "🛣️", "aws",   0, 0),
        ("aws_route_table",                            "Route Table",                "Network",    "🛣️", "aws",   0, 0),
        ("aws_route_table_association",                "Route Table Association",    "Network",    "🔗", "aws",   0, 0),
        ("aws_vpc",                                    "VPC",                        "Network",    "🔷", "aws",   0, 0),
        ("aws_subnet",                                 "Subnet",                     "Network",    "🔷", "aws",   0, 0),
        ("aws_internet_gateway",                       "Internet Gateway",           "Network",    "🌍", "aws",   0, 0),
        # AWS — Security
        ("aws_security_group",                         "Security Group",             "Security",   "🛡️", "aws",   0, 0),
        ("aws_security_group_rule",                    "Security Group Rule",        "Security",   "🛡️", "aws",   0, 0),
        # AWS — Identity
        ("aws_iam_role",                               "IAM Role",                   "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_policy",                             "IAM Policy",                 "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_policy_document",                    "IAM Policy Document",        "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_role_policy",                        "IAM Role Policy",            "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_role_policy_attachment",             "Iam Role Policy Attachment", "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_user",                               "IAM User",                   "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_user_policy",                        "Iam User Policy",            "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_access_key",                         "Iam Access Key",             "Identity",   "👤", "aws",   0, 0),
        ("aws_iam_instance_profile",                   "IAM Instance Profile",       "Identity",   "👤", "aws",   0, 0),
        ("aws_kms_key",                                "KMS Key",                    "Identity",   "🔑", "aws",   0, 0),
        ("aws_kms_alias",                              "KMS Key Alias",              "Identity",   "🔑", "aws",   0, 0),
        ("aws_key_pair",                               "Key Pair",                   "Identity",   "🔑", "aws",   0, 0),
        ("aws_ssm_parameter",                          "SSM Parameter",              "Identity",   "🔐", "aws",   0, 0),
        # GCP — Storage
        ("google_storage_bucket",                      "GCS Bucket",                 "Storage",    "🗄️", "gcp",   1, 1),
        ("google_storage_bucket_iam_binding",          "GCS Bucket",                 "Storage",    "🗄️", "gcp",   1, 0),
        # GCP — Database
        ("google_sql_database_instance",               "Cloud SQL Instance",         "Database",   "🗃️", "gcp",   1, 1),
        ("google_bigquery_dataset",                    "BigQuery Dataset",           "Database",   "🗃️", "gcp",   1, 1),
        ("google_bigtable_instance",                   "Bigtable Instance",          "Database",   "🗃️", "gcp",   1, 0),
        # GCP — Compute
        ("google_compute_instance",                    "Compute Instance",           "Compute",    "🖥️", "gcp",   0, 1),
        ("google_cloudfunctions_function",             "Cloud Function",             "Compute",    "⚡", "gcp",   0, 0),
        # GCP — Container
        ("google_container_cluster",                   "GKE Cluster",                "Container",  "☸️", "gcp",   0, 0),
        ("google_container_node_pool",                 "GKE Node Pool",              "Container",  "☸️", "gcp",   0, 0),
        # GCP — Network
        ("google_compute_network",                     "VPC Network",                "Network",    "🔷", "gcp",   0, 0),
        ("google_compute_subnetwork",                  "Subnetwork",                 "Network",    "🔷", "gcp",   0, 0),
        # GCP — Security
        ("google_compute_firewall",                    "Firewall Rule",              "Security",   "🛡️", "gcp",   0, 0),
        # GCP — Identity
        ("google_project_iam_binding",                 "IAM Binding",                "Identity",   "👤", "gcp",   0, 0),
        ("google_kms_crypto_key",                      "KMS Crypto Key",             "Identity",   "🔑", "gcp",   0, 0),
        ("google_service_account",                     "Service Account",            "Identity",   "👤", "gcp",   0, 0),
        # Alibaba Cloud
        ("alicloud_actiontrail_trail",                 "Actiontrail Trail",          "Monitoring", "📜", "alicloud", 0, 0),
        ("alicloud_ram_role",                          "RAM Role",                   "Identity",   "👤",  "alicloud", 0, 0),
    ]

    display_overrides = {
        # IAM / RBAC / policy controls are context-only (not architecture nodes)
        "azurerm_role_definition": 0,
        "azurerm_role_assignment": 0,
        "azurerm_policy_definition": 0,
        "azurerm_policy_assignment": 0,
        "azurerm_policy_set_definition": 0,
        "azuread_application": 0,
        "azuread_application_password": 0,
        "azuread_directory_role": 0,
        "azuread_directory_role_assignment": 0,
        "azuread_domains": 0,
        "azuread_group": 0,
        "azuread_group_member": 0,
        "azuread_service_principal": 0,
        "azuread_service_principal_password": 0,
        "azuread_user": 0,
        "azurerm_ssh_public_key": 0,
        "azurerm_security_center_contact": 0,
        "azurerm_security_center_subscription_pricing": 0,
        "aws_iam_role": 0,
        "aws_iam_policy": 0,
        "aws_iam_policy_document": 0,
        "aws_iam_user": 0,
        "aws_iam_instance_profile": 0,
        "aws_iam_role_policy": 0,
        "aws_iam_role_policy_attachment": 0,
        "aws_iam_user_policy": 0,
        "aws_iam_access_key": 0,
        "aws_kms_key": 0,
        "aws_kms_alias": 0,
        "aws_key_pair": 0,
        "aws_ssm_parameter": 0,
        "aws_elasticsearch_domain_policy": 0,
        "aws_lb_listener": 0,
        "aws_alb_listener": 0,
        "aws_lb_target_group": 0,
        "aws_alb_target_group": 0,
        "google_project_iam_binding": 0,
        "google_storage_bucket_iam_binding": 0,
        # Child components only render when vulnerable (nested under parent)
        "aws_s3_bucket_policy": 0,
        "aws_s3_bucket_public_access_block": 0,
    }
    parent_type_overrides = {
        "aws_lb_listener": "aws_lb",
        "aws_alb_listener": "aws_alb",
        "aws_lb_target_group": "aws_lb",
        "aws_alb_target_group": "aws_alb",
        "aws_lb_target_group_attachment": "aws_lb_target_group",
        "aws_s3_bucket_policy": "aws_s3_bucket",
        "aws_s3_bucket_public_access_block": "aws_s3_bucket",
        "google_storage_bucket_iam_binding": "google_storage_bucket",
        "azurerm_lb_backend_address_pool": "azurerm_lb",
        "azurerm_lb_rule": "azurerm_lb",
        "azurerm_application_gateway_http_listener": "azurerm_application_gateway",
    }

    existing_types = {
        r[0] for r in db.run('?[t] := *resource_types{terraform_type: t}')['rows']
    }
    for (tf_type, fname, cat, icon, pkey, is_ds, is_if) in _SEED:
        display = display_overrides.get(tf_type, 1)
        parent = parent_type_overrides.get(tf_type)
        pid_result = db.run('?[id] := *providers{id, key}, key = $k', {'k': pkey})
        pid = pid_result['rows'][0][0] if pid_result['rows'] else None
        if pid is None:
            print(f"⚠️  provider key '{pkey}' not found — resource_type '{tf_type}' will have null provider_id",
                  file=sys.stderr)
        if tf_type not in existing_types:
            new_id = _next_id(db, 'resource_types')
            db.put('resource_types', [{
                'id': new_id, 'provider_id': pid, 'terraform_type': tf_type,
                'friendly_name': fname, 'category': cat, 'icon': icon,
                'is_data_store': bool(is_ds),
                'is_internet_facing_capable': bool(is_if),
                'display_on_architecture_chart': bool(display),
                'parent_type': parent,
            }])
        else:
            id_result = db.run(
                '?[id] := *resource_types{id, terraform_type: t}, t = $t',
                {'t': tf_type},
            )
            if id_result['rows']:
                db.update('resource_types', [{
                    'id': id_result['rows'][0][0],
                    'provider_id': pid, 'friendly_name': fname, 'category': cat,
                    'icon': icon,
                    'is_data_store': bool(is_ds),
                    'is_internet_facing_capable': bool(is_if),
                    'display_on_architecture_chart': bool(display),
                    'parent_type': parent,
                }])

    print("✅ Schema initialized successfully")


def main() -> None:
    """Initialize or upgrade the database schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    db = Client('rocksdb', str(DB_PATH), dataframe=False)
    try:
        init_schema(db)
        print(f"✅ Database ready: {DB_PATH}")
    except Exception as e:
        print(f"❌ Error initializing database: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
