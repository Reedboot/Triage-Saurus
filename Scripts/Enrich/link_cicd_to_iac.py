#!/usr/bin/env python3
"""
link_cicd_to_iac.py — Link CI/CD artifacts to IaC deployment targets.

Phase B: Artifact-to-IaC Linking
- Parse extracted CI/CD metadata from resource_properties
- Match artifact names to IaC resources (App Services, Functions, Containers, etc.)
- Create resource_connections: artifact → deployment_target (ci_cd_deploys_to)
- Update resources with artifact metadata (image name, base image, etc.)

Usage:
    python3 Scripts/Enrich/link_cicd_to_iac.py --experiment 001
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

from db_helpers import get_db_connection


class CICDtoIaCLinker:
    """Link CI/CD artifacts to IaC deployment targets."""
    
    def __init__(self, experiment_id: str):
        self.experiment_id = experiment_id
    
    def get_cicd_artifacts(self, conn) -> Dict[str, List[Dict]]:
        """Retrieve all extracted CI/CD artifacts from resource_properties."""
        artifacts_by_type = {}
        
        try:
            results = conn.execute(
                """SELECT property_key, property_value FROM resource_properties 
                   WHERE property_key LIKE 'cicd_artifact_%' 
                   ORDER BY property_key"""
            ).fetchall()
            
            for key, value in results:
                artifact_type = key.replace('cicd_artifact_', '')
                try:
                    artifact = json.loads(value)
                    if artifact_type not in artifacts_by_type:
                        artifacts_by_type[artifact_type] = []
                    artifacts_by_type[artifact_type].append(artifact)
                except json.JSONDecodeError:
                    print(f"[WARN] Could not parse artifact metadata for {key}")
            
            return artifacts_by_type
        
        except Exception as e:
            print(f"[ERROR] Could not retrieve CI/CD artifacts: {e}")
            return {}
    
    def get_iac_resources(self, conn) -> Dict[str, List[Dict]]:
        """Retrieve IaC resources that could be deployment targets."""
        resources_by_type = {}
        
        try:
            # Query for likely deployment targets
            target_types = [
                'app_service', 'function_app', 'container_app',
                'docker_container', 'ec2_instance', 'kubernetes_deployment',
                'kubernetes_pod', 'ecs_task', 'ecs_service'
            ]
            
            for res_type in target_types:
                results = conn.execute(
                    """SELECT id, resource_name, resource_type FROM resources 
                       WHERE resource_type = ? AND experiment_id = ?""",
                    (res_type, self.experiment_id)
                ).fetchall()
                
                if results:
                    resources_by_type[res_type] = [
                        {'id': r[0], 'name': r[1], 'type': r[2]} for r in results
                    ]
            
            return resources_by_type
        
        except Exception as e:
            print(f"[ERROR] Could not retrieve IaC resources: {e}")
            return {}
    
    def match_artifacts_to_resources(self, conn) -> List[dict]:
        """Match artifacts to resources based on naming and type hints."""
        matches = []
        
        artifacts = self.get_cicd_artifacts(conn)
        resources = self.get_iac_resources(conn)
        
        if not artifacts and not resources:
            print("[INFO] No artifacts or resources to link")
            return matches
        
        print(f"[Linking] Found {sum(len(v) for v in artifacts.values())} artifacts")
        print(f"[Linking] Found {sum(len(v) for v in resources.values())} IaC resources")
        
        # Match docker images to docker_container resources
        if 'docker_image' in artifacts:
            for artifact in artifacts['docker_image']:
                image_name = artifact.get('name', '').lower()
                
                # Extract base name (without tag or registry)
                # e.g., "gurubaba/jenkins:latest" → "jenkins"
                base_name = re.sub(r'.*/', '', image_name)  # Remove registry
                base_name = re.sub(r':.*', '', base_name)   # Remove tag
                
                if 'docker_container' in resources:
                    for resource in resources['docker_container']:
                        res_name = resource['name'].lower()
                        
                        # Check if names match
                        if base_name in res_name or res_name in base_name:
                            matches.append({
                                'source_id': None,  # Artifact has no resource_id yet
                                'source_name': image_name,
                                'source_type': 'docker_image',
                                'target_id': resource['id'],
                                'target_name': resource['name'],
                                'target_type': resource['type'],
                                'connection_type': 'ci_cd_deploys_to',
                                'metadata': {
                                    'artifact': artifact,
                                    'match_type': 'image_to_container'
                                }
                            })
                            print(f"[Match] {image_name} → {resource['name']}")
        
        # Match deployment targets (Function App, App Service)
        if 'docker_image' in artifacts:
            for artifact in artifacts['docker_image']:
                # Check if deployment sources mention app services/functions
                source = artifact.get('source', '')
                
                # Try to find matching resource
                if any(marker in source for marker in ['appservice', 'function', 'app', 'api']):
                    image_name = artifact.get('name', '').lower()
                    
                    for res_type in ['app_service', 'function_app']:
                        if res_type in resources:
                            for resource in resources[res_type]:
                                matches.append({
                                    'source_id': None,
                                    'source_name': image_name,
                                    'source_type': 'docker_image',
                                    'target_id': resource['id'],
                                    'target_name': resource['name'],
                                    'target_type': res_type,
                                    'connection_type': 'ci_cd_deploys_to',
                                    'metadata': {
                                        'artifact': artifact,
                                        'match_type': 'image_to_service'
                                    }
                                })
        
        return matches
    
    def store_links(self, conn, matches: List[dict]) -> int:
        """Store artifact-to-resource links in database."""
        stored = 0
        
        for i, match in enumerate(matches):
            try:
                # Store connection metadata for now (will expand schema in future)
                conn_metadata = {
                    'source_type': match['source_type'],
                    'source': match['source_name'],
                    'target_id': match['target_id'],
                    'target_name': match['target_name'],
                    'connection_type': match['connection_type'],
                    'metadata': match['metadata']
                }
                
                # Store in resource_properties with unique keys
                unique_key = f"ci_cd_link_{match['source_type']}_{i}"
                conn.execute(
                    """INSERT OR IGNORE INTO resource_properties 
                       (resource_id, property_key, property_value, is_security_relevant)
                       VALUES (?, ?, ?, 1)""",
                    (
                        match['target_id'],
                        unique_key,
                        json.dumps(conn_metadata)
                    )
                )
                
                # Update resource with artifact metadata
                conn.execute(
                    """INSERT OR IGNORE INTO resource_properties 
                       (resource_id, property_key, property_value, is_security_relevant)
                       VALUES (?, ?, ?, 1)""",
                    (
                        match['target_id'],
                        f'deployed_artifact_{i}',
                        match['source_name']
                    )
                )
                
                stored += 1
            
            except Exception as e:
                print(f"[WARN] Could not store link {match}: {e}")
        
        conn.commit()
        return stored
    
    def link(self, conn) -> int:
        """Run complete linking process."""
        print("[CI/CD→IaC Linking] Starting artifact-to-resource linking...")
        
        matches = self.match_artifacts_to_resources(conn)
        print(f"[Linking] Found {len(matches)} potential matches")
        
        stored = self.store_links(conn, matches)
        print(f"[Linking] Stored {stored} artifact-to-resource links")
        
        return stored


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Link CI/CD artifacts to IaC deployment targets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--experiment",
        required=True,
        help="Experiment ID to process",
    )
    
    args = parser.parse_args()
    
    try:
        with get_db_connection() as conn:
            linker = CICDtoIaCLinker(args.experiment)
            linked_count = linker.link(conn)
        
        print(f"[CI/CD→IaC Linking] Complete - linked {linked_count} artifact(s) to resource(s)")
        return 0
    
    except Exception as e:
        print(f"[ERROR] Failed to link CI/CD artifacts to IaC: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
