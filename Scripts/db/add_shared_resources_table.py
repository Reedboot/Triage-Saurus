#!/usr/bin/env python3
"""
Add shared_resources table to track external/shared cloud resources referenced across multiple repos.

Examples:
- Shared API Management instance used by multiple APIs
- Central Log Analytics workspace
- Shared Container Registry
- Platform-wide Service Bus namespace
"""

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"

def migrate():
    print("Adding shared_resources table...")
    
    with sqlite3.connect(str(DB_PATH)) as conn:
        # Create shared_resources table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_type TEXT NOT NULL,
                resource_identifier TEXT NOT NULL,
                friendly_name TEXT,
                provider TEXT NOT NULL,
                category TEXT,
                discovered_from_repo TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reference_count INTEGER DEFAULT 1,
                variable_name TEXT,
                data_source_name TEXT,
                properties TEXT,
                UNIQUE(provider, resource_type, resource_identifier)
            )
        """)
        
        # Create index for fast lookups
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_resources_lookup 
            ON shared_resources(provider, resource_type, resource_identifier)
        """)
        
        # Create table to track which repos reference which shared resources
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_resource_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shared_resource_id INTEGER NOT NULL,
                repo_name TEXT NOT NULL,
                experiment_id TEXT,
                local_resource_id INTEGER,
                reference_type TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shared_resource_id) REFERENCES shared_resources(id),
                FOREIGN KEY (local_resource_id) REFERENCES assets(id),
                UNIQUE(shared_resource_id, repo_name, local_resource_id)
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_resource_references_repo
            ON shared_resource_references(repo_name)
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_resource_references_shared
            ON shared_resource_references(shared_resource_id)
        """)
        
        conn.commit()
        print("✓ Tables created successfully")
        
        # Show examples of shared resources we might find
        print("\n📋 Shared Resource Examples:")
        examples = [
            ("API Management", "Azure APIM instance used by multiple microservice repos"),
            ("Container Registry", "Docker registry shared across all services"),
            ("Log Analytics", "Central logging workspace for entire platform"),
            ("Service Bus", "Platform-wide messaging infrastructure"),
            ("Key Vault", "Shared secrets/certificates vault"),
            ("Application Insights", "Shared monitoring/telemetry"),
        ]
        for name, desc in examples:
            print(f"  • {name}: {desc}")

if __name__ == "__main__":
    migrate()
