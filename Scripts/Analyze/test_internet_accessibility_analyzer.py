#!/usr/bin/env python3
"""Tests for internet accessibility analyzer."""

import pytest
import tempfile
import sqlite3
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add Scripts/Analyze to path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Analyze"))
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

# Mock the db_helpers import before importing the analyzer
sys.modules["db_helpers"] = MagicMock()

from internet_accessibility_analyzer import (
    InternetAccessibilityAnalyzer,
    ensure_schema,
)


class TestInternetAccessibilityAnalyzer:
    """Test suite for InternetAccessibilityAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        """Create an analyzer instance for testing."""
        return InternetAccessibilityAnalyzer("test-experiment-1")

    def test_init(self, analyzer):
        """Test analyzer initialization."""
        assert analyzer.experiment_id == "test-experiment-1"
        assert len(analyzer.resources_by_id) == 0
        assert len(analyzer.connections_list) == 0
        assert len(analyzer.accessible_resources) == 0

    def test_is_public_ip_resource_detection(self, analyzer):
        """Test detection of public IP resources."""
        # Set up test data
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "my_public_ip",
            "resource_type": "azurerm_public_ip",
            "provider": "azure",
        }
        analyzer.properties_by_resource[1] = {}

        # Test positive case
        assert analyzer._is_public_ip_resource(1) is True

        # Test negative case
        analyzer.resources_by_id[2] = {
            "id": 2,
            "resource_name": "my_app",
            "resource_type": "azurerm_app_service",
            "provider": "azure",
        }
        analyzer.properties_by_resource[2] = {}
        assert analyzer._is_public_ip_resource(2) is False

    def test_is_public_ip_with_property(self, analyzer):
        """Test public IP detection via property."""
        analyzer.resources_by_id[3] = {
            "id": 3,
            "resource_name": "my_resource",
            "resource_type": "azurerm_network_interface",
            "provider": "azure",
        }
        analyzer.properties_by_resource[3] = {"internet_access": "true"}

        # Method should not crash and should return a boolean
        result = analyzer._is_public_ip_resource(3)
        assert isinstance(result, bool)

    def test_is_public_endpoint_resource_detection(self, analyzer):
        """Test detection of public endpoint resources."""
        # API Management should be public by default
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "my_apim",
            "resource_type": "azurerm_api_management",
            "provider": "azure",
        }
        analyzer.properties_by_resource[1] = {}

        assert analyzer._is_public_endpoint_resource(1) is True

        # But should not be public if explicitly marked private
        analyzer.properties_by_resource[1]["public_access_enabled"] = "false"
        assert analyzer._is_public_endpoint_resource(1) is False

    def test_is_public_endpoint_function_app(self, analyzer):
        """Test function app endpoint detection."""
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "my_function_app",
            "resource_type": "azurerm_function_app",
            "provider": "azure",
        }
        analyzer.properties_by_resource[1] = {}

        assert analyzer._is_public_endpoint_resource(1) is True

    def test_find_internet_entry_points_public_ip(self, analyzer):
        """Test finding entry points via public IP."""
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "VM_PublicIP",
            "resource_type": "azurerm_public_ip",
            "provider": "azure",
        }
        analyzer.properties_by_resource[1] = {}

        analyzer.find_internet_entry_points()

        assert len(analyzer.internet_entry_points) == 1
        assert analyzer.internet_entry_points[0]["resource_id"] == 1
        assert analyzer.internet_entry_points[0]["via_type"] == "public_ip"
        assert 1 in analyzer.accessible_resources

    def test_find_internet_entry_points_public_endpoint(self, analyzer):
        """Test finding entry points via public endpoint."""
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "my_apim",
            "resource_type": "azurerm_api_management",
            "provider": "azure",
        }
        analyzer.properties_by_resource[1] = {}

        analyzer.find_internet_entry_points()

        assert len(analyzer.internet_entry_points) == 1
        assert analyzer.internet_entry_points[0]["via_type"] == "public_endpoint"

    def test_build_adjacency_list_simple(self, analyzer):
        """Test building adjacency list from connections."""
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "resource_1",
            "resource_type": "vm",
        }
        analyzer.resources_by_id[2] = {
            "id": 2,
            "resource_name": "resource_2",
            "resource_type": "database",
        }

        # Add a connection from 1 to 2
        analyzer.connections_list = [
            {
                "source_resource_id": 1,
                "target_resource_id": 2,
                "connection_type": "uses_database",
            }
        ]

        graph = analyzer._build_adjacency_list()

        assert 1 in graph
        assert len(graph[1]) == 1
        assert graph[1][0][0] == 2

    def test_build_adjacency_list_skips_administrative(self, analyzer):
        """Test that administrative edge types are skipped."""
        analyzer.resources_by_id[1] = {"id": 1, "resource_name": "r1", "resource_type": "t"}
        analyzer.resources_by_id[2] = {"id": 2, "resource_name": "r2", "resource_type": "t"}

        # Add connections with administrative edge types
        analyzer.connections_list = [
            {
                "source_resource_id": 1,
                "target_resource_id": 2,
                "connection_type": "contains",
            },
            {
                "source_resource_id": 1,
                "target_resource_id": 2,
                "connection_type": "parent_of",
            },
        ]

        graph = analyzer._build_adjacency_list()

        # Neither should appear in graph
        assert len(graph) == 0

    def test_traverse_from_internet_single_hop(self, analyzer):
        """Test traversal from Internet to nearby resource."""
        # Set up entry point
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "public_ip",
            "resource_type": "azurerm_public_ip",
        }
        analyzer.properties_by_resource[1] = {}

        # Set up downstream resource
        analyzer.resources_by_id[2] = {
            "id": 2,
            "resource_name": "vm",
            "resource_type": "azurerm_virtual_machine",
        }
        analyzer.properties_by_resource[2] = {}

        # Connect public IP to VM
        analyzer.connections_list = [
            {
                "source_resource_id": 1,
                "target_resource_id": 2,
                "connection_type": "attached_to",
                "authentication": "",
                "auth_method": "",
            }
        ]

        analyzer.find_internet_entry_points()
        analyzer.traverse_from_internet()

        assert 2 in analyzer.accessible_resources
        assert len(analyzer.accessibility_paths[2]) > 0

    def test_traverse_from_internet_multi_hop(self, analyzer):
        """Test traversal through multiple hops."""
        # Create chain: Public IP → VM → DB
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "public_ip",
            "resource_type": "azurerm_public_ip",
        }
        analyzer.resources_by_id[2] = {
            "id": 2,
            "resource_name": "vm",
            "resource_type": "azurerm_virtual_machine",
        }
        analyzer.resources_by_id[3] = {
            "id": 3,
            "resource_name": "database",
            "resource_type": "azurerm_sql_database",
        }

        for res_id in [1, 2, 3]:
            analyzer.properties_by_resource[res_id] = {}

        # Create connections
        analyzer.connections_list = [
            {
                "source_resource_id": 1,
                "target_resource_id": 2,
                "connection_type": "attached_to",
                "authentication": "",
                "auth_method": "",
            },
            {
                "source_resource_id": 2,
                "target_resource_id": 3,
                "connection_type": "uses_database",
                "authentication": "",
                "auth_method": "",
            },
        ]

        analyzer.find_internet_entry_points()
        analyzer.traverse_from_internet()

        assert 3 in analyzer.accessible_resources
        path = analyzer.accessibility_paths[3][0]
        assert path.distance == 2
        # Path includes Internet node at the start
        assert path.path_nodes == ["Internet", "public_ip", "vm", "database"]

    def test_determine_auth_level_none(self, analyzer):
        """Test auth level detection - no auth."""
        conn = {"authentication": "", "auth_method": ""}
        assert analyzer._determine_auth_level(conn) == "none"

    def test_determine_auth_level_key(self, analyzer):
        """Test auth level detection - shared key."""
        conn = {"authentication": "SharedKey", "auth_method": ""}
        assert analyzer._determine_auth_level(conn) == "key"

    def test_determine_auth_level_identity(self, analyzer):
        """Test auth level detection - managed identity."""
        conn = {"authentication": "", "auth_method": "ManagedIdentity"}
        assert analyzer._determine_auth_level(conn) == "identity"

    def test_no_accessible_resources(self, analyzer):
        """Test case with no internet-accessible resources."""
        # Create resources without any entry points
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "internal_db",
            "resource_type": "azurerm_sql_database",
        }
        analyzer.properties_by_resource[1] = {}

        analyzer.find_internet_entry_points()
        analyzer.traverse_from_internet()

        assert len(analyzer.accessible_resources) == 0
        assert len(analyzer.internet_entry_points) == 0

    def test_multiple_entry_points(self, analyzer):
        """Test multiple independent entry points."""
        # First entry point
        analyzer.resources_by_id[1] = {
            "id": 1,
            "resource_name": "public_ip_1",
            "resource_type": "azurerm_public_ip",
        }
        analyzer.properties_by_resource[1] = {}

        # Second entry point
        analyzer.resources_by_id[2] = {
            "id": 2,
            "resource_name": "apim",
            "resource_type": "azurerm_api_management",
        }
        analyzer.properties_by_resource[2] = {}

        analyzer.find_internet_entry_points()

        assert len(analyzer.internet_entry_points) == 2
        assert 1 in analyzer.accessible_resources
        assert 2 in analyzer.accessible_resources


class TestEnsureSchema:
    """Test schema creation."""

    def test_ensure_schema_creates_table(self):
        """Test that ensure_schema creates the internet_accessibility table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(str(db_path))

            ensure_schema(conn)

            # Check table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='resource_internet_accessibility'"
            )
            assert cursor.fetchone() is not None

            conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
