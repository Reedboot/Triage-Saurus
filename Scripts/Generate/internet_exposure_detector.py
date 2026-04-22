"""
Internet Exposure Detection for Architecture Diagrams

Detects resources that are internet-exposed using multiple methods:
1. Explicit findings (internet_exposure context)
2. Firewall rules open to 0.0.0.0
3. Resource properties (public_access_enabled, publicly_accessible)
4. Resource type heuristics (API Gateway, Load Balancer, etc)

Returns: List of (resource_name, exposure_type, confidence, reason, color)
"""

import json
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass
import re


@dataclass
class ExposureDetail:
    """Information about a resource's internet exposure."""
    resource_name: str
    resource_id: int
    exposure_type: str  # 'finding', 'firewall_rule', 'property', 'heuristic'
    confidence: str    # 'high', 'medium', 'low'
    reason: str        # Human-readable explanation
    color: str         # Hex color code
    detection_methods: List[str] = None  # Multiple detections can apply
    # Enhanced for diagram rendering
    port: Optional[int] = None  # Port number if rule-based exposure
    protocol: Optional[str] = None  # 'tcp', 'udp', etc
    auth_required: Optional[bool] = None  # True if auth detected, False if unauthenticated
    sg_rule_id: Optional[int] = None  # Parent SG rule resource ID for nesting
    target_service: Optional[str] = None  # Service running on port (jenkins, ssh, http, etc)

    def __post_init__(self):
        if self.detection_methods is None:
            self.detection_methods = []


def _clean_resource_type(resource_type: str) -> str:
    """Remove provider prefix from resource type for display."""
    if not resource_type:
        return resource_type
    for prefix in ('azurerm_', 'aws_', 'google_'):
        if resource_type.lower().startswith(prefix):
            return resource_type[len(prefix):]
    return resource_type


class InternetExposureDetector:
    """Detects internet-exposed resources for threat model visualization."""

    # Color codes by detection method
    COLORS = {
        'finding': '#ff0000',           # Red - explicit security finding
        'firewall_rule': '#ff9900',     # Orange - open firewall rule
        'property': '#ffff00',          # Yellow - public property
        'heuristic': '#ffff00',         # Yellow - resource type heuristic
    }

    # Public-by-design resource types (per provider)
    PUBLIC_BY_DESIGN = {
        'aws': {
            'aws_lb', 'aws_alb', 'aws_nlb',  # Load balancers (modern names)
            'apigateway', 'api_gateway', 'api_gateway_stage', 'aws_apigateway',
            'aws_eip', 'aws_eip_association', 'aws_internet_gateway', 'aws_apigatewayv2_api', 'aws_lambda_function_url',
            'elb', 'alb', 'nlb', 'elastic_load_balancing',  # Load balancers (legacy names)
            'application_load_balancer', 'network_load_balancer',
            'cloudfront', 'aws_cloudfront',
        },
        'azure': {
            'api_management_api', 'api_management_product',
            'app_service', 'app_service_plan',
            'function_app', 'app_gateway', 'application_gateway',
            'front_door', 'frontdoor', 'cdn_profile',
            'api_management', 'apim',
            'azurerm_app_service', 'azurerm_function_app',  # Full resource type names
            'azurerm_application_gateway',
            'azurerm_lb', 'azurerm_application_load_balancer',
            'azurerm_api_management',
            'azurerm_frontdoor', 'azurerm_front_door',
            'azurerm_public_ip', 'azurerm_public_ip_prefix',
            'azurerm_storage_account',  # Storage accounts can be publicly accessible
            'azurerm_cosmosdb_account',  # Public network access is enabled by default
        },
        'gcp': {
            'compute_backend_service', 'compute_url_map',
            'compute_target_http_proxy', 'compute_target_https_proxy',
            'https_load_balancer', 'http_load_balancer',
            'cloud_load_balancing',
            'api_gateway', 'google_api_gateway_api',
            'google_compute_address', 'google_compute_global_address',
            'google_compute_backend_service', 'google_compute_forwarding_rule',
            'google_compute_global_forwarding_rule', 'google_compute_target_http_proxy',
            'google_compute_target_https_proxy', 'google_compute_url_map',
               'google_cloud_run_service',
               # Serverless functions (only if trigger_http=true; check properties)
               'google_cloudfunctions_function', 'google_cloudfunctions2_function',
               # API Gateway APIs
               'google_api_gateway_api', 'google_api_gateway_api_config',
               # App Engine (inherently public)
               'google_app_engine_application', 'google_app_engine_standard_app_version',
               # Load balancer backends and frontends
               'google_compute_backend_bucket',
               # Compute instances with external IPs (check access_config property)
               'google_compute_instance', 'compute_instance',
               # SQL instances with public_ip_address property
               'google_sql_database_instance',
        },
        'oci': {
            'load_balancer', 'oci_load_balancer',
            'api_gateway', 'oci_api_gateway',
            'oci_load_balancer_load_balancer',
            'oci_apigateway_gateway', 'oci_network_load_balancer_network_load_balancer',
            'oci_core_internet_gateway',
            'cdn',
        },
        'alicloud': {
            'alicloud_slb', 'alicloud_slb_load_balancer',
            'alicloud_alb_load_balancer',
            'alicloud_api_gateway_api',
            'alicloud_cdn_domain',
            'alicloud_eip', 'alicloud_eip_association',
        },
    }

    # Resource types that should NOT be marked as public even if matched
    PRIVATE_OVERRIDE = {
        'azurerm_api_management': True,  # May not be public if behind APIM
        'aws_security_group': True,  # Security Groups are filters, not services; don't mark as internet-exposed
        'security_group': True,  # Generic name variant
    }

    # Properties indicating public access
    PUBLIC_PROPERTIES = {
        'public_access_enabled',
        'public_network_access_enabled',
        'publicly_accessible',
        'has_public_ip',
        'public_ip_assigned',
        'enable_public_network_access',
        'public_endpoint_enabled',
        'internet_ingress_open',  # Security group/rule allows ingress from 0.0.0.0
    }

    def __init__(self, provider: str):
        """
        Initialize detector for a specific provider.
        
        Args:
            provider: Cloud provider ('aws', 'azure', 'gcp', 'oci', 'alicloud')
        """
        self.provider = provider.lower()

    @classmethod
    def get_public_entry_types(cls, provider: str | None = None) -> set[str]:
        """Return normalized public-by-design resource types.

        When provider is omitted, returns the union of all provider-specific types.
        """
        if provider:
            return {t.lower() for t in cls.PUBLIC_BY_DESIGN.get(provider.lower(), set())}
        combined = set()
        for types in cls.PUBLIC_BY_DESIGN.values():
            combined.update(t.lower() for t in types)
        return combined

    def detect_exposed_resources(
        self,
        resources: List[Dict],
        connections: List[Dict],
        findings: List[Dict] = None,
        properties: Dict[int, Dict[str, str]] = None,
    ) -> Dict[str, ExposureDetail]:
        """
        Detect internet-exposed resources using all detection methods.
        
        Args:
            resources: List of resource dicts with id, resource_name, resource_type, provider
            connections: List of connection dicts (may contain 'Internet' source)
            findings: List of finding dicts with resource_id, context fields
            properties: Dict mapping resource_id → {property_key: property_value}
        
        Returns:
            Dict mapping resource_name → ExposureDetail
        """
        exposed = {}
        confidence_rank = {'high': 3, 'medium': 2, 'low': 1}

        # Method 1: Explicit findings (highest confidence)
        if findings:
            findings_results = self._detect_by_findings(resources, findings)
            exposed.update(findings_results)

        # Method 2: Firewall rules open to 0.0.0.0 (high confidence)
        firewall_results = self._detect_by_firewall_rules(resources, properties)
        for resource_name, detail in firewall_results.items():
            if resource_name not in exposed:
                exposed[resource_name] = detail
            elif confidence_rank.get(detail.confidence, 0) > confidence_rank.get(exposed[resource_name].confidence, 0):
                exposed[resource_name] = detail

        # Method 3: Resource properties indicating public access (medium confidence)
        if properties:
            property_results = self._detect_by_resource_properties(resources, properties)
            for resource_name, detail in property_results.items():
                if resource_name not in exposed:
                    exposed[resource_name] = detail
                elif confidence_rank.get(detail.confidence, 0) > confidence_rank.get(exposed[resource_name].confidence, 0):
                    exposed[resource_name] = detail

        # Method 4: Resource type heuristics (medium confidence)
        heuristic_results = self._detect_by_heuristics(resources)
        for resource_name, detail in heuristic_results.items():
            if resource_name not in exposed:
                exposed[resource_name] = detail
            elif confidence_rank.get(detail.confidence, 0) > confidence_rank.get(exposed[resource_name].confidence, 0):
                exposed[resource_name] = detail

        return exposed

    def _detect_by_findings(
        self,
        resources: List[Dict],
        findings: List[Dict],
    ) -> Dict[str, ExposureDetail]:
        """Detect resources with explicit internet_exposure findings."""
        exposed = {}

        # Build resource_id → resource map
        resource_by_id = {r['id']: r for r in resources}

        for finding in findings:
            resource_id = finding.get('resource_id')
            if not resource_id or resource_id not in resource_by_id:
                continue

            # Check for internet_exposure context
            context = finding.get('context', [])
            if not isinstance(context, list):
                context = []

            is_exposed = False
            for ctx in context:
                if (ctx.get('context_key') == 'internet_exposure' and
                    ctx.get('context_value') == 'true'):
                    is_exposed = True
                    break

            if is_exposed:
                resource = resource_by_id[resource_id]
                resource_name = resource.get('resource_name')
                if resource_name:
                    exposed[resource_name] = ExposureDetail(
                        resource_name=resource_name,
                        resource_id=resource_id,
                        exposure_type='finding',
                        confidence='high',
                        reason='Explicit security finding: internet exposure detected',
                        color=self.COLORS['finding'],
                        detection_methods=['Finding'],
                    )

        return exposed

    def _detect_by_firewall_rules(
        self,
        resources: List[Dict],
        properties: Optional[Dict[int, Dict[str, str]]],
    ) -> Dict[str, ExposureDetail]:
        """Detect resources with firewall rules open to 0.0.0.0."""
        exposed = {}

        if not properties:
            return exposed

        for resource in resources:
            resource_id = resource.get('id')
            resource_name = resource.get('resource_name')
            resource_type = (resource.get('resource_type') or '').lower()

            if not resource_name or resource_id not in properties:
                continue

            props = properties[resource_id]
            reason_parts = []

            # Check for explicit firewall rule properties
            for prop_key in ['firewall_rules', 'security_rules', 'inbound_rules', 
                            'ingress_rules', 'network_rules']:
                if prop_key in props:
                    prop_value = props[prop_key]
                    if self._contains_open_rules(prop_value):
                        reason_parts.append(f'{prop_key}: allows 0.0.0.0/0')

            # Check for explicit start_ip or start_ip_address
            if props.get('start_ip_address') == '0.0.0.0':
                reason_parts.append('Firewall rule: 0.0.0.0/0 allowed')
            if props.get('start_ip') == '0.0.0.0':
                reason_parts.append('Firewall rule: 0.0.0.0/0 allowed')

            # SQL Server: Check for public network access
            if 'sql' in resource_type and props.get('public_network_access_enabled') == 'true':
                reason_parts.append('SQL: public_network_access_enabled')

            if reason_parts:
                exposed[resource_name] = ExposureDetail(
                    resource_name=resource_name,
                    resource_id=resource_id,
                    exposure_type='firewall_rule',
                    confidence='high',
                    reason=' | '.join(reason_parts),
                    color=self.COLORS['firewall_rule'],
                    detection_methods=['Firewall Rule'],
                )

        return exposed

    def _detect_by_resource_properties(
        self,
        resources: List[Dict],
        properties: Optional[Dict[int, Dict[str, str]]],
    ) -> Dict[str, ExposureDetail]:
        """Detect resources with properties indicating public access."""
        exposed = {}

        if not properties:
            return exposed

        for resource in resources:
            resource_id = resource.get('id')
            resource_name = resource.get('resource_name')
            resource_type = (resource.get('resource_type') or '').lower()

            if not resource_name or resource_id not in properties:
                continue

            props = properties[resource_id]
            reason_parts = []

            # Check for public access properties
            for prop_key, prop_value in props.items():
                if prop_key in self.PUBLIC_PROPERTIES:
                    # Normalize boolean values
                    if prop_value and str(prop_value).lower() in ('true', '1', 'yes'):
                        # Special handling for security group rules with port info
                        if prop_key == 'internet_ingress_open' and resource_type == 'aws_security_group_rule':
                            # Try to extract port and protocol from properties
                            from_port = props.get('from_port', props.get('port', ''))
                            to_port = props.get('to_port', '')
                            protocol = props.get('protocol', 'unknown')
                            
                            if from_port and from_port == to_port:
                                reason_parts.append(f'port {from_port} open to 0.0.0.0/0')
                            elif from_port and to_port:
                                reason_parts.append(f'ports {from_port}-{to_port} open to 0.0.0.0/0')
                            elif from_port:
                                reason_parts.append(f'port {from_port} open to 0.0.0.0/0')
                            else:
                                reason_parts.append('allows ingress from 0.0.0.0/0')
                        else:
                            reason_parts.append(f'{prop_key}=true')

            if reason_parts:
                # Skip if this resource has private override
                if resource_type in self.PRIVATE_OVERRIDE:
                    continue

                exposed[resource_name] = ExposureDetail(
                    resource_name=resource_name,
                    resource_id=resource_id,
                    exposure_type='property',
                    confidence='medium',
                    reason=' | '.join(reason_parts),
                    color=self.COLORS['property'],
                    detection_methods=['Property'],
                )

        return exposed

    def _detect_by_heuristics(
        self,
        resources: List[Dict],
    ) -> Dict[str, ExposureDetail]:
        """Detect resources by type heuristics (inherently public types)."""
        exposed = {}

        public_types = self.PUBLIC_BY_DESIGN.get(self.provider, set())

        for resource in resources:
            resource_name = resource.get('resource_name')
            resource_type = (resource.get('resource_type') or '').lower()

            if not resource_name:
                continue

            # Skip if already detected by higher-confidence method
            if resource_name in exposed:
                continue

            # Check if resource type is inherently public
            matches_public_type = False
            matched_type = None

            for public_type in public_types:
                if public_type in resource_type or resource_type in public_type:
                    matches_public_type = True
                    matched_type = public_type
                    break

            if matches_public_type:
                # Additional heuristic: skip resources with "private" in name
                if 'private' in resource_name.lower():
                    continue

                # GCP-specific filters for false positives
                if self.provider == 'gcp':
                    # Cloud Functions require trigger_http=true to be public
                    # Storage buckets require IAM policies to be public (skip them)
                    if 'cloud' in resource_type and 'function' in resource_type:
                        # For now, assume all Cloud Functions are public if no property override
                        # In future: check trigger_http property
                        pass
                    elif 'storage_bucket' in resource_type:
                        # Storage buckets are NOT inherently public - skip heuristic detection
                        continue
                    elif 'artifact_registry' in resource_type:
                        # Artifact registries require IAM - skip
                        continue
                    elif 'compute_instance' in resource_type or 'sql_database_instance' in resource_type:
                        # Only public if access_config or public_ip_address properties exist
                        # Filtered by property detection method instead
                        continue

                clean_type = _clean_resource_type(matched_type)
                exposed[resource_name] = ExposureDetail(
                    resource_name=resource_name,
                    resource_id=resource.get('id'),
                    exposure_type='heuristic',
                    confidence='medium',
                    reason=f'Resource type {clean_type} is inherently public-facing',
                    color=self.COLORS['heuristic'],
                    detection_methods=['Heuristic'],
                )

        return exposed

    @staticmethod
    def _contains_open_rules(rules_str: str) -> bool:
        """
        Check if a rules JSON string contains rules open to 0.0.0.0/0.
        
        Args:
            rules_str: JSON string or plain text containing rules
        
        Returns:
            True if any rule allows 0.0.0.0
        """
        if not rules_str:
            return False

        # Try parsing as JSON
        try:
            rules = json.loads(rules_str)
            if isinstance(rules, list):
                for rule in rules:
                    if isinstance(rule, dict):
                        # Check source_ip, source_address, cidr, etc.
                        for key in ['source_ip', 'source_address', 'source_cidr', 
                                   'cidr', 'start_ip_address', 'start_ip']:
                            if rule.get(key) == '0.0.0.0' or rule.get(key) == '0.0.0.0/0':
                                return True
            elif isinstance(rules, dict):
                # Single rule dict
                for key in ['source_ip', 'source_address', 'source_cidr', 
                           'cidr', 'start_ip_address', 'start_ip']:
                    if rules.get(key) == '0.0.0.0' or rules.get(key) == '0.0.0.0/0':
                        return True
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: check string contains 0.0.0.0
        return '0.0.0.0' in rules_str.upper()


def merge_exposure_detections(
    exposures: List[Dict[str, ExposureDetail]]
) -> Dict[str, ExposureDetail]:
    """
    Merge multiple exposure detection results, keeping highest-confidence detections.
    
    Args:
        exposures: List of exposure dicts from multiple detectors
    
    Returns:
        Merged dict with deduplicated resources (highest confidence wins)
    """
    merged = {}
    confidence_rank = {'high': 3, 'medium': 2, 'low': 1}

    for exposure_dict in exposures:
        for resource_name, detail in exposure_dict.items():
            if resource_name not in merged:
                merged[resource_name] = detail
            else:
                # Keep higher confidence detection
                existing = merged[resource_name]
                if confidence_rank.get(detail.confidence, 0) > confidence_rank.get(existing.confidence, 0):
                    merged[resource_name] = detail
                elif detail.confidence == existing.confidence:
                    # Same confidence, combine methods
                    if detail.detection_methods:
                        existing.detection_methods.extend(detail.detection_methods)

    return merged
