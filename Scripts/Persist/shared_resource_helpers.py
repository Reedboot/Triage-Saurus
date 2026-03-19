#!/usr/bin/env python3
"""
shared_resource_helpers.py — Track and query shared/external resources across repos.

Shared resources are cloud resources referenced by multiple repos but defined elsewhere:
- API Management instances
- Container Registries
- Log Analytics workspaces
- Service Bus namespaces
- Key Vaults
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"


def register_shared_resource(
    resource_type: str,
    resource_identifier: str,
    provider: str,
    *,
    friendly_name: str = None,
    category: str = None,
    discovered_from_repo: str = None,
    variable_name: str = None,
    data_source_name: str = None,
    properties: dict = None,
) -> int:
    """
    Register or update a shared resource in the database.
    
    Args:
        resource_type: Terraform resource type (e.g., "azurerm_api_management")
        resource_identifier: Unique identifier (e.g., "var.api_management_name", actual name if known)
        provider: Cloud provider ("azure", "aws", "gcp", etc.)
        friendly_name: Human-readable name
        category: Resource category ("API", "Storage", etc.)
        discovered_from_repo: First repo that referenced this
        variable_name: Variable name if it's a var reference (e.g., "api_management_name")
        data_source_name: Data source name if it's a data reference
        properties: Additional metadata as dict
    
    Returns:
        Database ID of the shared resource
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        # Check if already exists
        existing = conn.execute("""
            SELECT id, reference_count FROM shared_resources 
            WHERE provider = ? AND resource_type = ? AND resource_identifier = ?
        """, (provider, resource_type, resource_identifier)).fetchone()
        
        if existing:
            # Update reference count
            shared_id, count = existing
            conn.execute("""
                UPDATE shared_resources 
                SET reference_count = reference_count + 1
                WHERE id = ?
            """, (shared_id,))
            conn.commit()
            return shared_id
        else:
            # Insert new shared resource
            props_json = json.dumps(properties) if properties else None
            cursor = conn.execute("""
                INSERT INTO shared_resources 
                (resource_type, resource_identifier, friendly_name, provider, category,
                 discovered_from_repo, variable_name, data_source_name, properties)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (resource_type, resource_identifier, friendly_name, provider, category,
                  discovered_from_repo, variable_name, data_source_name, props_json))
            conn.commit()
            return cursor.lastrowid


def link_repo_to_shared_resource(
    shared_resource_id: int,
    repo_name: str,
    experiment_id: str = None,
    local_resource_id: int = None,
    reference_type: str = "references",
) -> None:
    """
    Record that a repo references a shared resource.
    
    Args:
        shared_resource_id: ID of the shared resource
        repo_name: Name of the repo making the reference
        experiment_id: Experiment/scan ID
        local_resource_id: ID of the local resource that references the shared one
        reference_type: Type of reference ("references", "depends_on", "uses_auth_from", etc.)
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO shared_resource_references
            (shared_resource_id, repo_name, experiment_id, local_resource_id, reference_type)
            VALUES (?, ?, ?, ?, ?)
        """, (shared_resource_id, repo_name, experiment_id, local_resource_id, reference_type))
        conn.commit()


def get_shared_resource(provider: str, resource_type: str, resource_identifier: str) -> Optional[dict]:
    """
    Get shared resource details if it exists.
    
    Returns dict with keys: id, resource_type, resource_identifier, friendly_name, provider,
                            category, reference_count, variable_name, properties
    Returns None if not found.
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT * FROM shared_resources
            WHERE provider = ? AND resource_type = ? AND resource_identifier = ?
        """, (provider, resource_type, resource_identifier)).fetchone()
        
        if row:
            result = dict(row)
            if result.get("properties"):
                result["properties"] = json.loads(result["properties"])
            return result
        return None


def get_repos_using_shared_resource(shared_resource_id: int) -> list[dict]:
    """
    Get all repos that reference a shared resource.
    
    Returns list of dicts with keys: repo_name, experiment_id, reference_type, discovered_at
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT repo_name, experiment_id, reference_type, discovered_at
            FROM shared_resource_references
            WHERE shared_resource_id = ?
            ORDER BY discovered_at DESC
        """, (shared_resource_id,)).fetchall()
        
        return [dict(row) for row in rows]


def get_shared_resources_for_repo(repo_name: str) -> list[dict]:
    """
    Get all shared resources referenced by a repo.
    
    Returns list of dicts combining shared_resources and reference details.
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT 
                sr.*,
                srr.reference_type,
                srr.experiment_id as ref_experiment_id,
                srr.local_resource_id
            FROM shared_resources sr
            JOIN shared_resource_references srr ON sr.id = srr.shared_resource_id
            WHERE srr.repo_name = ?
            ORDER BY sr.resource_type, sr.resource_identifier
        """, (repo_name,)).fetchall()
        
        results = []
        for row in rows:
            result = dict(row)
            if result.get("properties"):
                result["properties"] = json.loads(result["properties"])
            results.append(result)
        return results


def get_all_shared_resources(provider: str = None) -> list[dict]:
    """
    Get all shared resources, optionally filtered by provider.
    
    Returns list of dicts with shared resource details and reference counts.
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        
        if provider:
            rows = conn.execute("""
                SELECT * FROM shared_resources
                WHERE provider = ?
                ORDER BY reference_count DESC, resource_type
            """, (provider,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM shared_resources
                ORDER BY provider, reference_count DESC, resource_type
            """).fetchall()
        
        results = []
        for row in rows:
            result = dict(row)
            if result.get("properties"):
                result["properties"] = json.loads(result["properties"])
            results.append(result)
        return results


if __name__ == "__main__":
    # Test/demo
    print("📊 Shared Resources Database\n")
    
    # Example: Register shared APIM
    apim_id = register_shared_resource(
        resource_type="azurerm_api_management",
        resource_identifier="var.api_management_name",
        provider="azure",
        friendly_name="Platform APIM",
        category="API",
        discovered_from_repo="account-viewing-permissions",
        variable_name="api_management_name",
    )
    print(f"✓ Registered shared APIM (ID: {apim_id})")
    
    # Link repos to it
    link_repo_to_shared_resource(apim_id, "account-viewing-permissions", "experiment-001")
    link_repo_to_shared_resource(apim_id, "accounts-api", "experiment-002")
    link_repo_to_shared_resource(apim_id, "users-api", "experiment-003")
    
    print(f"\n✓ Linked 3 repos to shared APIM")
    
    # Query
    repos = get_repos_using_shared_resource(apim_id)
    print(f"\nRepos using this APIM:")
    for repo in repos:
        print(f"  • {repo['repo_name']} ({repo['experiment_id']})")
    
    # Show all shared resources
    all_shared = get_all_shared_resources("azure")
    print(f"\nAll shared Azure resources:")
    for sr in all_shared:
        print(f"  • {sr['resource_type']}: {sr['resource_identifier']} (used by {sr['reference_count']} repos)")
