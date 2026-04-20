#!/usr/bin/env python3
"""
Test suite for internet exposure detector.

Tests:
- Individual detection methods (findings, firewall, properties, heuristics)
- Edge cases (private endpoints, NAT Gateway, duplicates)
- Color coding and confidence levels
- Provider-specific detection rules
"""

import json
from internet_exposure_detector import InternetExposureDetector, ExposureDetail, merge_exposure_detections


class TestInternetExposureDetector:
    """Test InternetExposureDetector class."""
    
    def test_detector_init(self):
        """Test detector initialization with different providers."""
        for provider in ['aws', 'azure', 'gcp', 'oci']:
            detector = InternetExposureDetector(provider)
            assert detector.provider == provider.lower()
    
    def test_detect_by_explicit_findings(self):
        """Test detection via explicit internet_exposure findings."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'webapp-001', 'resource_type': 'azurerm_app_service'},
            {'id': 2, 'resource_name': 'vm-001', 'resource_type': 'azurerm_virtual_machine'},
        ]
        
        findings = [
            {
                'resource_id': 1,
                'context': [
                    {'context_key': 'internet_exposure', 'context_value': 'true'}
                ]
            }
        ]
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=findings, properties=None
        )
        
        assert 'webapp-001' in exposed
        assert exposed['webapp-001'].exposure_type == 'finding'
        assert exposed['webapp-001'].confidence == 'high'
        assert exposed['webapp-001'].color == '#ff0000'  # Red
    
    def test_detect_by_firewall_rules(self):
        """Test detection via firewall rules open to 0.0.0.0."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'sql-001', 'resource_type': 'azurerm_mssql_server'},
        ]
        
        properties = {
            1: {'start_ip_address': '0.0.0.0', 'end_ip_address': '255.255.255.255'}
        }
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=properties
        )
        
        assert 'sql-001' in exposed
        assert exposed['sql-001'].exposure_type == 'firewall_rule'
        assert exposed['sql-001'].confidence == 'high'
        assert exposed['sql-001'].color == '#ff9900'  # Orange
    
    def test_detect_by_resource_properties(self):
        """Test detection via public access properties."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'storage-001', 'resource_type': 'azurerm_storage_account'},
            {'id': 2, 'resource_name': 'sql-002', 'resource_type': 'azurerm_mssql_server'},
        ]
        
        properties = {
            1: {'public_access_enabled': 'true'},
            2: {'publicly_accessible': 'true'},
        }
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=properties
        )
        
        assert 'storage-001' in exposed
        assert exposed['storage-001'].exposure_type == 'property'
        assert exposed['storage-001'].confidence == 'medium'
        assert exposed['storage-001'].color == '#ffff00'  # Yellow
        
        assert 'sql-002' in exposed
        assert exposed['sql-002'].exposure_type == 'property'
    
    def test_detect_by_heuristics_aws(self):
        """Test detection via resource type heuristics for AWS."""
        detector = InternetExposureDetector('aws')
        
        resources = [
            {'id': 1, 'resource_name': 'alb-001', 'resource_type': 'aws_lb'},
            {'id': 2, 'resource_name': 'api-gateway-001', 'resource_type': 'aws_apigateway'},
            {'id': 3, 'resource_name': 'db-001', 'resource_type': 'aws_db_instance'},
        ]
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=None
        )
        
        # ALB and API Gateway are public by design
        assert 'alb-001' in exposed
        assert exposed['alb-001'].exposure_type == 'heuristic'
        
        assert 'api-gateway-001' in exposed
        assert exposed['api-gateway-001'].exposure_type == 'heuristic'
        
        # Database is NOT public by default
        assert 'db-001' not in exposed
    
    def test_detect_by_heuristics_azure(self):
        """Test detection via resource type heuristics for Azure."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'app-gateway-001', 'resource_type': 'azurerm_application_gateway'},
            {'id': 2, 'resource_name': 'app-service-001', 'resource_type': 'azurerm_app_service'},
            {'id': 3, 'resource_name': 'cosmos-001', 'resource_type': 'azurerm_cosmosdb_account'},
            {'id': 4, 'resource_name': 'vm-001', 'resource_type': 'azurerm_virtual_machine'},
        ]
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=None
        )
        
        # App Gateway and App Service are public by design
        assert 'app-gateway-001' in exposed
        assert 'app-service-001' in exposed
        assert 'cosmos-001' in exposed
        
        # VM is NOT public by default
        assert 'vm-001' not in exposed
    
    def test_private_name_override(self):
        """Test that resources with 'private' in name are not marked public."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'private-app-service', 'resource_type': 'azurerm_app_service'},
        ]
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=None
        )
        
        # Should NOT be exposed despite being an App Service
        assert 'private-app-service' not in exposed
    
    def test_confidence_ranking(self):
        """Test that multiple detection methods select highest confidence."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'app-001', 'resource_type': 'azurerm_app_service'},
        ]
        
        findings = [
            {
                'resource_id': 1,
                'context': [
                    {'context_key': 'internet_exposure', 'context_value': 'true'}
                ]
            }
        ]
        
        properties = {
            1: {'public_access_enabled': 'true'}  # Medium confidence
        }
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=findings, properties=properties
        )
        
        # Finding (high) wins over property (medium) and heuristic
        assert exposed['app-001'].exposure_type == 'finding'
        assert exposed['app-001'].confidence == 'high'
        assert exposed['app-001'].color == '#ff0000'  # Red
    
    def test_firewall_rules_json_parsing(self):
        """Test parsing of complex firewall rules JSON."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'sql-001', 'resource_type': 'azurerm_mssql_server'},
        ]
        
        # Firewall rules as JSON
        rules = json.dumps([
            {'source_ip': '10.0.0.0', 'port': 1433},
            {'source_ip': '0.0.0.0', 'port': 1433},  # Open rule
        ])
        
        properties = {
            1: {'firewall_rules': rules}
        }
        
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=properties
        )
        
        assert 'sql-001' in exposed
        assert exposed['sql-001'].exposure_type == 'firewall_rule'
    
    def test_no_false_positives(self):
        """Test that private resources are not marked as exposed."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'vm-internal', 'resource_type': 'azurerm_virtual_machine'},
            {'id': 2, 'resource_name': 'db-internal', 'resource_type': 'azurerm_mssql_server'},
        ]
        
        # No findings, properties, or public indicators
        exposed = detector.detect_exposed_resources(
            resources, [], findings=None, properties=None
        )
        
        # Should not detect any exposure
        assert len(exposed) == 0
    
    def test_color_codes_correct(self):
        """Test that color codes are assigned correctly."""
        assert InternetExposureDetector.COLORS['finding'] == '#ff0000'       # Red
        assert InternetExposureDetector.COLORS['firewall_rule'] == '#ff9900' # Orange
        assert InternetExposureDetector.COLORS['property'] == '#ffff00'      # Yellow
        assert InternetExposureDetector.COLORS['heuristic'] == '#ffff00'     # Yellow
    
    def test_multiple_providers_independent(self):
        """Test that detectors are independent per provider."""
        aws_detector = InternetExposureDetector('aws')
        azure_detector = InternetExposureDetector('azure')
        gcp_detector = InternetExposureDetector('gcp')
        alicloud_detector = InternetExposureDetector('alicloud')
        
        resources_aws = [
            {'id': 1, 'resource_name': 'alb', 'resource_type': 'aws_lb'}
        ]
        
        resources_azure = [
            {'id': 1, 'resource_name': 'alb', 'resource_type': 'azurerm_application_gateway'}
        ]
        resources_gcp = [
            {'id': 1, 'resource_name': 'gateway', 'resource_type': 'google_api_gateway_api'}
        ]
        resources_alicloud = [
            {'id': 1, 'resource_name': 'cdn', 'resource_type': 'alicloud_cdn_domain'}
        ]
        
        exposed_aws = aws_detector.detect_exposed_resources(resources_aws, [])
        exposed_azure = azure_detector.detect_exposed_resources(resources_azure, [])
        exposed_gcp = gcp_detector.detect_exposed_resources(resources_gcp, [])
        exposed_alicloud = alicloud_detector.detect_exposed_resources(resources_alicloud, [])
        
        # Both should detect their resource as public (different type names, same public design)
        assert 'alb' in exposed_aws
        assert 'alb' in exposed_azure
        assert 'gateway' in exposed_gcp
        assert 'cdn' in exposed_alicloud

    def test_public_entry_type_inventory_includes_missing_providers(self):
        public_types = InternetExposureDetector.get_public_entry_types()
        assert 'aws_eip' in public_types
        assert 'google_cloud_run_service' in public_types
        assert 'alicloud_api_gateway_api' in public_types
        assert 'alicloud_cdn_domain' in public_types


class TestMergeExposureDetections:
    """Test merging of multiple exposure detection results."""
    
    def test_merge_no_duplicates(self):
        """Test merging results with no duplicate resources."""
        dict1 = {
            'resource-1': ExposureDetail('resource-1', 1, 'finding', 'high', 'Test 1', '#ff0000')
        }
        dict2 = {
            'resource-2': ExposureDetail('resource-2', 2, 'property', 'medium', 'Test 2', '#ffff00')
        }
        
        merged = merge_exposure_detections([dict1, dict2])
        
        assert len(merged) == 2
        assert 'resource-1' in merged
        assert 'resource-2' in merged
    
    def test_merge_with_duplicates_keeps_highest_confidence(self):
        """Test that duplicate resources keep highest confidence."""
        dict1 = {
            'resource-1': ExposureDetail('resource-1', 1, 'heuristic', 'low', 'Test', '#ffff00')
        }
        dict2 = {
            'resource-1': ExposureDetail('resource-1', 1, 'finding', 'high', 'Test', '#ff0000')
        }
        
        merged = merge_exposure_detections([dict1, dict2])
        
        assert len(merged) == 1
        assert merged['resource-1'].exposure_type == 'finding'
        assert merged['resource-1'].confidence == 'high'
    
    def test_merge_combines_detection_methods(self):
        """Test that multiple detections of same confidence combine methods."""
        detail1 = ExposureDetail('resource-1', 1, 'finding', 'high', 'Test', '#ff0000')
        detail1.detection_methods = ['Finding']
        
        detail2 = ExposureDetail('resource-1', 1, 'finding', 'high', 'Test', '#ff0000')
        detail2.detection_methods = ['Direct Finding']
        
        dict1 = {'resource-1': detail1}
        dict2 = {'resource-1': detail2}
        
        merged = merge_exposure_detections([dict1, dict2])
        
        assert len(merged['resource-1'].detection_methods) >= 2


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_resources(self):
        """Test detector with empty resource list."""
        detector = InternetExposureDetector('azure')
        exposed = detector.detect_exposed_resources([], [])
        assert len(exposed) == 0
    
    def test_invalid_finding_context(self):
        """Test handling of invalid finding context JSON."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1, 'resource_name': 'resource-1', 'resource_type': 'azurerm_app_service'},
        ]
        
        findings = [
            {
                'resource_id': 1,
                'context': 'invalid json'  # Not a list
            }
        ]
        
        # Should not crash
        exposed = detector.detect_exposed_resources(
            resources, [], findings=findings, properties=None
        )
        
        # Should still detect heuristic
        assert 'resource-1' in exposed
    
    def test_missing_resource_fields(self):
        """Test handling of resources with missing fields."""
        detector = InternetExposureDetector('azure')
        
        resources = [
            {'id': 1},  # Missing resource_name and resource_type
        ]
        
        # Should not crash
        exposed = detector.detect_exposed_resources(resources, [])
        assert len(exposed) == 0


if __name__ == '__main__':
    # Run basic smoke tests
    detector = InternetExposureDetector('azure')
    
    # Test 1: Findings-based detection
    resources = [
        {'id': 1, 'resource_name': 'webapp-001', 'resource_type': 'azurerm_app_service'},
    ]
    findings = [
        {
            'resource_id': 1,
            'context': [
                {'context_key': 'internet_exposure', 'context_value': 'true'}
            ]
        }
    ]
    exposed = detector.detect_exposed_resources(resources, [], findings=findings)
    assert 'webapp-001' in exposed
    print("✓ Findings-based detection works")
    
    # Test 2: Firewall detection
    resources = [
        {'id': 1, 'resource_name': 'sql-001', 'resource_type': 'azurerm_mssql_server'},
    ]
    properties = {1: {'start_ip_address': '0.0.0.0'}}
    exposed = detector.detect_exposed_resources(resources, [], properties=properties)
    assert 'sql-001' in exposed
    print("✓ Firewall-based detection works")
    
    # Test 3: Property detection
    resources = [
        {'id': 1, 'resource_name': 'storage-001', 'resource_type': 'azurerm_storage_account'},
    ]
    properties = {1: {'public_access_enabled': 'true'}}
    exposed = detector.detect_exposed_resources(resources, [], properties=properties)
    assert 'storage-001' in exposed
    print("✓ Property-based detection works")
    
    # Test 4: Heuristic detection
    detector_aws = InternetExposureDetector('aws')
    resources = [
        {'id': 1, 'resource_name': 'alb-001', 'resource_type': 'aws_lb'},
    ]
    exposed = detector_aws.detect_exposed_resources(resources, [])
    assert 'alb-001' in exposed
    print("✓ Heuristic-based detection works")
    
    print("\n✅ All basic tests passed!")
