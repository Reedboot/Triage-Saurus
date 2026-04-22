#!/usr/bin/env python3
"""
Enrich database resources with publicly_accessible property.

This script adds publicly_accessible properties to database resources based on:
1. Explicit terraform property values (publicly_accessible, public_network_access_enabled)
2. Presence of public_ip_address property
3. Database instance public endpoint configuration
"""

import sqlite3
import sys
import re
from pathlib import Path

def enrich_database_resources(db_path: str):
    """Enrich database and compute resources with publicly_accessible property."""
    
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        return False
    
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Define database resource types and their public access indicators
    db_types = {
        'aws_db_instance': {
            'properties': ['publicly_accessible', 'publicly_accessible_enabled'],
            'public_ip_props': ['endpoint'],
        },
        'aws_rds_cluster_instance': {
            'properties': ['publicly_accessible', 'publicly_accessible_enabled'],
            'public_ip_props': ['endpoint'],
        },
        'google_sql_database_instance': {
            'properties': ['public_ip_address', 'settings_ip_configuration_ipv4_enabled'],
        },
        'azurerm_mssql_server': {
            'properties': ['public_network_access_enabled'],
        },
        'azurerm_cosmosdb_account': {
            'properties': ['public_network_access_enabled'],
        },
        'alicloud_db_instance': {
            'properties': ['publicly_accessible'],
        },
        'huaweicloud_rds_instance': {
            'properties': ['publicly_accessible', 'publiclyaccessible'],
        },
        'tencentcloud_mysql_instance': {
            'properties': ['public_network_enable'],
        },
        # Compute instances with public IP
        'aws_instance': {
            'public_ip_props': ['public_ip', 'associate_public_ip_address'],
        },
        'google_compute_instance': {
            'public_ip_props': ['access_config'],
        },
        'azurerm_linux_virtual_machine': {
            'public_ip_props': ['public_ip_address', 'public_ip_address_id'],
        },
        'azurerm_windows_virtual_machine': {
            'public_ip_props': ['public_ip_address', 'public_ip_address_id'],
        },
        'alicloud_instance': {
            'public_ip_props': ['public_ip', 'eip_id'],
        },
        'huaweicloud_compute_instance': {
            'public_ip_props': ['access_ip_v4', 'security_groups'],
        },
        'tencentcloud_instance': {
            'public_ip_props': ['public_ip'],
        },
    }
    
    total_enriched = 0
    
    for db_type, config in db_types.items():
        # Get all resources of this type
        cursor.execute("""
            SELECT id, resource_name, resource_type
            FROM resources
            WHERE resource_type = ?
        """, (db_type,))
        
        resources = cursor.fetchall()
        
        for resource in resources:
            resource_id = resource['id']
            resource_name = resource['resource_name']
            
            # Get all properties for this resource
            cursor.execute("""
                SELECT property_key, property_value
                FROM resource_properties
                WHERE resource_id = ?
            """, (resource_id,))
            
            props = {row['property_key']: row['property_value'] for row in cursor.fetchall()}
            
            # Check for public access indicators
            is_public = False
            reason = None
            
            for prop_key in config.get('properties', []):
                if prop_key in props:
                    value = str(props[prop_key]).lower()
                    if value in ('true', '1', 'yes', 'enabled', 'public'):
                        is_public = True
                        reason = f"Property {prop_key}={value}"
                        break
            
            # Check for public IP properties
            if not is_public and 'public_ip_props' in config:
                for ip_prop in config['public_ip_props']:
                    if ip_prop in props and props[ip_prop]:
                        is_public = True
                        reason = f"Property {ip_prop} configured"
                        break
            
            # Enrich the resource with publicly_accessible property
            if is_public:
                # Check if property already exists
                cursor.execute("""
                    SELECT id FROM resource_properties
                    WHERE resource_id = ? AND property_key = 'publicly_accessible'
                """, (resource_id,))
                
                if not cursor.fetchone():
                    # Insert the property
                    cursor.execute("""
                        INSERT INTO resource_properties (resource_id, property_key, property_value, property_type, is_security_relevant)
                        VALUES (?, ?, ?, ?, ?)
                    """, (resource_id, 'publicly_accessible', 'true', 'boolean', 1))
                    
                    print(f"✓ {db_type}: {resource_name}")
                    print(f"  Reason: {reason}")
                    total_enriched += 1
    
    db.commit()
    print(f"\n{'='*60}")
    print(f"Total resources enriched: {total_enriched}")
    print(f"{'='*60}")
    
    db.close()
    return True

if __name__ == '__main__':
    db_path = 'Output/Data/cozo.db'
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    success = enrich_database_resources(db_path)
    sys.exit(0 if success else 1)
