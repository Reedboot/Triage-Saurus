#!/usr/bin/env python3
"""
resource_normalizer.py

Normalize cloud-specific resources to unified roles for exposure analysis.
Maps AWS, Azure, and GCP resource types to standardized categories:
- Entry Point (publicly accessible network entry)
- Countermeasure (security control: WAF, App Gateway, NSG, Firewall)
- Load Balancer (traffic management that may act as countermeasure)
- Compute (workload: VMs, containers, serverless)
- Data (storage/database: buckets, databases, file shares)
"""

from typing import Optional, Dict, Set, List
from dataclasses import dataclass
from enum import Enum


class UnifiedRole(Enum):
    """Unified resource roles across all cloud providers."""
    ENTRY_POINT = "entry_point"
    COUNTERMEASURE = "countermeasure"
    LOAD_BALANCER = "load_balancer"
    COMPUTE = "compute"
    DATA = "data"
    OTHER = "other"


@dataclass
class NormalizedResource:
    """Normalized resource with unified role and provider info."""
    resource_name: str
    resource_type: str
    provider: str
    normalized_role: UnifiedRole
    is_public_facing: bool = False
    is_security_boundary: bool = False


# AWS resource type → (Unified Role, is_public, is_boundary)
_AWS_MAPPINGS = {
    "aws_internet_gateway": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_internet_gateway_attachment": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_eip": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_eip_association": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_waf_web_acl": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_wafv2_web_acl": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_security_group": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_security_group_rule": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_network_acl": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_network_acl_rule": (UnifiedRole.COUNTERMEASURE, False, True),
    "aws_lb": (UnifiedRole.LOAD_BALANCER, False, False),
    "aws_alb": (UnifiedRole.LOAD_BALANCER, False, False),
    "aws_elb": (UnifiedRole.LOAD_BALANCER, False, False),
    # API Gateway is internet-facing by default, treat as entry point for exposure analysis
    "aws_api_gateway": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_apigatewayv2_api": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_api_gateway_rest_api": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_api_gateway_stage": (UnifiedRole.ENTRY_POINT, True, False),
    "aws_lb_listener": (UnifiedRole.LOAD_BALANCER, False, False),
    "aws_lb_target_group": (UnifiedRole.LOAD_BALANCER, False, False),
    "aws_instance": (UnifiedRole.COMPUTE, False, False),
    "aws_ec2_instance": (UnifiedRole.COMPUTE, False, False),
    "aws_ecs_task": (UnifiedRole.COMPUTE, False, False),
    "aws_ecs_cluster": (UnifiedRole.COMPUTE, False, False),
    "aws_ecs_service": (UnifiedRole.COMPUTE, False, False),
    "aws_eks_cluster": (UnifiedRole.COMPUTE, False, False),
    "aws_lambda_function": (UnifiedRole.COMPUTE, False, False),
    "aws_batch_compute_environment": (UnifiedRole.COMPUTE, False, False),
    "aws_s3_bucket": (UnifiedRole.DATA, False, False),
    "aws_s3_bucket_policy": (UnifiedRole.DATA, False, False),
    "aws_s3_bucket_acl": (UnifiedRole.DATA, False, False),
    "aws_s3_bucket_public_access_block": (UnifiedRole.DATA, False, False),
    "aws_db_instance": (UnifiedRole.DATA, False, False),
    "aws_rds_cluster": (UnifiedRole.DATA, False, False),
    "aws_rds_cluster_instance": (UnifiedRole.DATA, False, False),
    "aws_dynamodb_table": (UnifiedRole.DATA, False, False),
    "aws_efs_file_system": (UnifiedRole.DATA, False, False),
    "aws_efs_access_point": (UnifiedRole.DATA, False, False),
    "aws_ebs_volume": (UnifiedRole.DATA, False, False),
    # AWS messaging services contain data
    "aws_sqs_queue": (UnifiedRole.DATA, False, False),
    "aws_sns_topic": (UnifiedRole.DATA, False, False),
    "aws_sns_subscription": (UnifiedRole.DATA, False, False),
    "aws_kinesis_stream": (UnifiedRole.DATA, False, False),
    "aws_mq_broker": (UnifiedRole.DATA, False, False),
}

# Azure resource type → (Unified Role, is_public, is_boundary)
_AZURE_MAPPINGS = {
    "azurerm_public_ip": (UnifiedRole.ENTRY_POINT, True, False),
    "azurerm_public_ip_prefix": (UnifiedRole.ENTRY_POINT, True, False),
    "azurerm_application_gateway": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_web_application_firewall_policy": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_network_security_group": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_network_security_rule": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_firewall": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_firewall_policy": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_firewall_policy_rule_collection_group": (UnifiedRole.COUNTERMEASURE, False, True),
    "azurerm_lb": (UnifiedRole.LOAD_BALANCER, False, False),
    # APIM is internet-facing by default (public endpoint), treat as entry point for exposure analysis
    "azurerm_api_management": (UnifiedRole.ENTRY_POINT, True, False),
    "azurerm_api_management_api": (UnifiedRole.ENTRY_POINT, True, False),
    "azurerm_lb_rule": (UnifiedRole.LOAD_BALANCER, False, False),
    "azurerm_lb_backend_address_pool": (UnifiedRole.LOAD_BALANCER, False, False),
    "azurerm_linux_virtual_machine": (UnifiedRole.COMPUTE, False, False),
    "azurerm_windows_virtual_machine": (UnifiedRole.COMPUTE, False, False),
    "azurerm_virtual_machine": (UnifiedRole.COMPUTE, False, False),
    "azurerm_container_group": (UnifiedRole.COMPUTE, False, False),
    "azurerm_kubernetes_cluster": (UnifiedRole.COMPUTE, False, False),
    # Kubernetes/AKS workloads detected from Skaffold/Helm
    "kubernetes_service": (UnifiedRole.COMPUTE, False, False),
    "kubernetes_deployment": (UnifiedRole.COMPUTE, False, False),
    "kubernetes_pod": (UnifiedRole.COMPUTE, False, False),
    "azurerm_container_registry": (UnifiedRole.COMPUTE, False, False),
    "azurerm_app_service": (UnifiedRole.COMPUTE, False, False),
    "azurerm_function_app": (UnifiedRole.COMPUTE, False, False),
    "azurerm_storage_account": (UnifiedRole.DATA, False, False),
    "azurerm_storage_container": (UnifiedRole.DATA, False, False),
    "azurerm_storage_blob": (UnifiedRole.DATA, False, False),
    "azurerm_storage_share": (UnifiedRole.DATA, False, False),
    "azurerm_mssql_server": (UnifiedRole.DATA, False, False),
    "azurerm_mssql_database": (UnifiedRole.DATA, False, False),
    "azurerm_sql_server": (UnifiedRole.DATA, False, False),
    "azurerm_sql_database": (UnifiedRole.DATA, False, False),
    "azurerm_mysql_server": (UnifiedRole.DATA, False, False),
    "azurerm_mysql_database": (UnifiedRole.DATA, False, False),
    "azurerm_postgresql_server": (UnifiedRole.DATA, False, False),
    "azurerm_postgresql_database": (UnifiedRole.DATA, False, False),
    "azurerm_cosmosdb_account": (UnifiedRole.DATA, False, False),
    # Service Bus messaging resources contain data
    "azurerm_servicebus_namespace": (UnifiedRole.DATA, False, False),
    "azurerm_servicebus_queue": (UnifiedRole.DATA, False, False),
    "azurerm_servicebus_topic": (UnifiedRole.DATA, False, False),
    "azurerm_servicebus_subscription": (UnifiedRole.DATA, False, False),
}

# GCP resource type → (Unified Role, is_public, is_boundary)
_GCP_MAPPINGS = {
    "google_compute_address": (UnifiedRole.ENTRY_POINT, True, False),
    "google_compute_global_address": (UnifiedRole.ENTRY_POINT, True, False),
    "google_cloud_run_service": (UnifiedRole.ENTRY_POINT, True, False),
    # GCP API Gateway is internet-facing by default
    "google_api_gateway_api": (UnifiedRole.ENTRY_POINT, True, False),
    "google_api_gateway_api_config": (UnifiedRole.ENTRY_POINT, True, False),
    "google_api_gateway_gateway": (UnifiedRole.ENTRY_POINT, True, False),
    "google_compute_firewall": (UnifiedRole.COUNTERMEASURE, False, True),
    "google_compute_security_policy": (UnifiedRole.COUNTERMEASURE, False, True),
    "google_compute_network": (UnifiedRole.COUNTERMEASURE, False, True),
    "google_compute_subnetwork": (UnifiedRole.COUNTERMEASURE, False, True),
    "google_compute_route": (UnifiedRole.COUNTERMEASURE, False, True),
    "google_compute_backend_service": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_backend_bucket": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_forwarding_rule": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_health_check": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_target_pool": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_target_https_proxy": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_target_http_proxy": (UnifiedRole.LOAD_BALANCER, False, False),
    "google_compute_instance": (UnifiedRole.COMPUTE, False, False),
    "google_compute_instance_group": (UnifiedRole.COMPUTE, False, False),
    "google_compute_instance_template": (UnifiedRole.COMPUTE, False, False),
    "google_container_cluster": (UnifiedRole.COMPUTE, False, False),
    "google_container_node_pool": (UnifiedRole.COMPUTE, False, False),
    "google_cloud_run_service": (UnifiedRole.COMPUTE, False, False),
    "google_storage_bucket": (UnifiedRole.DATA, False, False),
    "google_storage_bucket_object": (UnifiedRole.DATA, False, False),
    "google_sql_database_instance": (UnifiedRole.DATA, False, False),
    "google_sql_database": (UnifiedRole.DATA, False, False),
    "google_bigtable_instance": (UnifiedRole.DATA, False, False),
    "google_bigtable_table": (UnifiedRole.DATA, False, False),
    "google_firestore_database": (UnifiedRole.DATA, False, False),
    "google_datastore_database": (UnifiedRole.DATA, False, False),
    # GCP messaging services contain data
    "google_pubsub_topic": (UnifiedRole.DATA, False, False),
    "google_pubsub_subscription": (UnifiedRole.DATA, False, False),
}

# Alicloud resource type → (Unified Role, is_public, is_boundary)
_ALICLOUD_MAPPINGS = {
    "alicloud_slb": (UnifiedRole.LOAD_BALANCER, False, False),
    "alicloud_cdn_domain": (UnifiedRole.ENTRY_POINT, True, False),
    "alicloud_eip": (UnifiedRole.ENTRY_POINT, True, False),
    "alicloud_eip_association": (UnifiedRole.ENTRY_POINT, True, False),
    "alicloud_oss_bucket": (UnifiedRole.DATA, False, False),
    "alicloud_db_instance": (UnifiedRole.DATA, False, False),
    "alicloud_rds_instance": (UnifiedRole.DATA, False, False),
    "alicloud_ecs_instance": (UnifiedRole.COMPUTE, False, False),
    "alicloud_instance": (UnifiedRole.COMPUTE, False, False),
    "alicloud_vpc": (UnifiedRole.OTHER, False, False),
    "alicloud_vswitch": (UnifiedRole.OTHER, False, False),
    "alicloud_ram_role": (UnifiedRole.OTHER, False, False),
    "alicloud_ram_policy": (UnifiedRole.OTHER, False, False),
    "alicloud_actiontrail_trail": (UnifiedRole.OTHER, False, False),
    "alicloud_security_group": (UnifiedRole.COUNTERMEASURE, False, True),
    "alicloud_security_group_rule": (UnifiedRole.COUNTERMEASURE, False, True),
}

# OCI (Oracle Cloud Infrastructure) resource type → (Unified Role, is_public, is_boundary)
_OCI_MAPPINGS = {
    "oci_load_balancer": (UnifiedRole.LOAD_BALANCER, False, False),
    "oci_load_balancer_load_balancer": (UnifiedRole.LOAD_BALANCER, False, False),
    "oci_core_internet_gateway": (UnifiedRole.ENTRY_POINT, True, False),
    "oci_core_vcn": (UnifiedRole.OTHER, False, False),
    "oci_core_subnet": (UnifiedRole.OTHER, False, False),
    "oci_objectstorage_bucket": (UnifiedRole.DATA, False, False),
    "oci_database": (UnifiedRole.DATA, False, False),
    "oci_mysql_mysql_db_system": (UnifiedRole.DATA, False, False),
    "oci_core_instance": (UnifiedRole.COMPUTE, False, False),
    "oci_functions_function": (UnifiedRole.COMPUTE, False, False),
    "oci_container_instances_container_instance": (UnifiedRole.COMPUTE, False, False),
    "oci_identity_policy": (UnifiedRole.OTHER, False, False),
    "oci_network_firewall_network_firewall": (UnifiedRole.COUNTERMEASURE, False, True),
    "oci_network_firewall_network_firewall_policy": (UnifiedRole.COUNTERMEASURE, False, True),
}


class ResourceNormalizer:
    """Normalize cloud resources to unified roles for exposure analysis."""

    def __init__(self):
        """Initialize with provider-specific mappings."""
        self._mappings = {
            "aws": _AWS_MAPPINGS,
            "azure": _AZURE_MAPPINGS,
            "gcp": _GCP_MAPPINGS,
            "alicloud": _ALICLOUD_MAPPINGS,
            "oci": _OCI_MAPPINGS,
            "oracle": _OCI_MAPPINGS,
        }
        self._all_mappings: Dict[str, tuple] = {}
        for provider, mapping in self._mappings.items():
            self._all_mappings.update(mapping)

    def detect_provider(self, resource_type: str) -> str:
        """Detect cloud provider from resource type."""
        resource_type_lower = resource_type.lower()
        if resource_type_lower.startswith("aws_"):
            return "aws"
        if resource_type_lower.startswith("azurerm_"):
            return "azure"
        if resource_type_lower.startswith("google_"):
            return "gcp"
        if resource_type_lower.startswith("alicloud_"):
            return "alicloud"
        if resource_type_lower.startswith("oci_"):
            return "oci"
        return "unknown"

    def normalize(
        self,
        resource_name: str,
        resource_type: str,
        provider: Optional[str] = None,
    ) -> NormalizedResource:
        """Normalize a resource to a unified role."""
        if provider is None:
            provider = self.detect_provider(resource_type)
        else:
            provider = (provider or "unknown").lower().strip()
            if provider == "oracle":
                provider = "oci"

        normalized_type = resource_type.lower()
        mapping = self._all_mappings.get(normalized_type)

        if mapping:
            role, is_public, is_boundary = mapping
            return NormalizedResource(
                resource_name=resource_name,
                resource_type=resource_type,
                provider=provider,
                normalized_role=role,
                is_public_facing=is_public,
                is_security_boundary=is_boundary,
            )

        return NormalizedResource(
            resource_name=resource_name,
            resource_type=resource_type,
            provider=provider,
            normalized_role=UnifiedRole.OTHER,
            is_public_facing=False,
            is_security_boundary=False,
        )

    def get_entry_points_by_provider(self) -> Dict[str, Set[str]]:
        """Get all entry point resource types grouped by provider."""
        entry_points = {"aws": set(), "azure": set(), "gcp": set()}
        for provider, mapping in self._mappings.items():
            for rtype, (role, _, _) in mapping.items():
                if role == UnifiedRole.ENTRY_POINT:
                    entry_points[provider].add(rtype)
        return entry_points

    def get_countermeasures_by_provider(self) -> Dict[str, Set[str]]:
        """Get all countermeasure resource types grouped by provider."""
        countermeasures = {"aws": set(), "azure": set(), "gcp": set()}
        for provider, mapping in self._mappings.items():
            for rtype, (role, _, _) in mapping.items():
                if role == UnifiedRole.COUNTERMEASURE:
                    countermeasures[provider].add(rtype)
        return countermeasures

    def get_resources_by_role(self, role: UnifiedRole) -> Dict[str, Set[str]]:
        """Get all resource types for a specific unified role, grouped by provider."""
        result = {"aws": set(), "azure": set(), "gcp": set()}
        for provider, mapping in self._mappings.items():
            for rtype, (mapped_role, _, _) in mapping.items():
                if mapped_role == role:
                    result[provider].add(rtype)
        return result
