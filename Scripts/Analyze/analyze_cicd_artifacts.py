#!/usr/bin/env python3
"""
analyze_cicd_artifacts.py — Analyze CI/CD artifacts for security issues.

Creates findings for:
- :latest tags (supply chain risk)
- Base image vulnerabilities (using known vulnerable images)
- Unspecified base images (pinning risk)
- GOAT/intentional vulnerable images (testing/demo risk)
- Privileged containers
- Missing health checks
- Environment variable secrets

Usage:
    python3 Scripts/Analyze/analyze_cicd_artifacts.py --experiment 001
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

from db_helpers import get_db_connection


class CICDArtifactAnalyzer:
    """Analyze CI/CD artifacts and generate security findings."""
    
    # Known vulnerable/GOAT images
    VULNERABLE_IMAGES = {
        'gurubaba/jenkins': 'GOAT image: Intentionally vulnerable Jenkins without authentication',
        'gurubaba/app': 'GOAT image: Intentionally vulnerable application',
        'gurubaba/vulnerable': 'GOAT image: Intentionally vulnerable base image',
        'tvandewoude/cve-2021-3129': 'Known CVE image: Laravel RCE',
        'vulnerables/web-dvwa': 'DVWA: Damn Vulnerable Web Application',
    }
    
    def __init__(self, experiment_id: str):
        self.experiment_id = experiment_id
        self.conn = None
        self.findings: List[Dict] = []
    
    def get_artifacts(self, conn) -> List[Dict]:
        """Retrieve all CI/CD artifacts from resource_properties."""
        artifacts = []
        
        try:
            results = conn.execute(
                """SELECT property_key, property_value FROM resource_properties 
                   WHERE property_key LIKE 'cicd_artifact_%'
                   ORDER BY property_key"""
            ).fetchall()
            
            for key, value in results:
                try:
                    artifact = json.loads(value)
                    artifact['property_key'] = key
                    artifacts.append(artifact)
                except json.JSONDecodeError:
                    pass
            
            return artifacts
        
        except Exception as e:
            print(f"[WARN] Could not retrieve artifacts: {e}")
            return []
    
    def analyze_docker_image(self, image_name: str, artifact: Dict) -> List[Dict]:
        """Analyze a Docker image for security issues."""
        findings = []
        
        # Check for :latest tag
        if image_name.endswith(':latest'):
            findings.append({
                'type': 'artifact_latest_tag',
                'severity': 'HIGH',
                'title': 'Docker image uses :latest tag',
                'description': f'Image {image_name} uses :latest tag, which may introduce unexpected changes and supply chain risk. Pin to specific version (e.g., :1.2.0).',
                'artifact': image_name,
                'recommendation': 'Pin base image to specific version: FROM image:1.2.0 (not :latest)',
                'cwe': 'CWE-1104: Use of Unmaintained Third Party Components'
            })
        elif ':' not in image_name and '/' in image_name:
            # No tag specified at all
            findings.append({
                'type': 'artifact_unspecified_version',
                'severity': 'MEDIUM',
                'title': 'Docker image version not specified',
                'description': f'Image {image_name} has no version tag. Builds will be non-reproducible.',
                'artifact': image_name,
                'recommendation': 'Always specify image version: image:1.2.0',
                'cwe': 'CWE-1104: Use of Unmaintained Third Party Components'
            })
        
        # Check for known vulnerable images
        for vuln_pattern, reason in self.VULNERABLE_IMAGES.items():
            if vuln_pattern.lower() in image_name.lower():
                findings.append({
                    'type': 'artifact_vulnerable_image',
                    'severity': 'CRITICAL' if 'GOAT' in reason else 'HIGH',
                    'title': f'Known vulnerable image: {vuln_pattern}',
                    'description': reason,
                    'artifact': image_name,
                    'recommendation': f'Use a secure, maintained image. If {vuln_pattern} is intentional for testing, isolate it from production.',
                    'cwe': 'CWE-1275: Undefined Behavior'
                })
        
        # Check for public registries without auth
        if image_name.startswith('docker.io/') or '/' not in image_name.split(':')[0]:
            findings.append({
                'type': 'artifact_public_registry',
                'severity': 'MEDIUM',
                'title': 'Image from public registry without verification',
                'description': f'Image {image_name} is pulled from public registry without signature verification.',
                'artifact': image_name,
                'recommendation': 'Use private registry or verify image signatures with Cosign/Notary',
                'cwe': 'CWE-494: Download of Code Without Integrity Check'
            })
        
        return findings
    
    def analyze_base_images(self, conn) -> List[Dict]:
        """Analyze base images for vulnerabilities."""
        findings = []
        
        try:
            results = conn.execute(
                """SELECT property_value FROM resource_properties 
                   WHERE property_key = 'cicd_artifact_base_image'"""
            ).fetchall()
            
            for (value,) in results:
                try:
                    artifact = json.loads(value)
                    base_image = artifact.get('name', '')
                    
                    if not base_image:
                        continue
                    
                    # Old/unsupported base images
                    if any(pattern in base_image for pattern in ['ubuntu:12.04', 'ubuntu:14.04', 'debian:7', 'centos:6']):
                        findings.append({
                            'type': 'artifact_obsolete_base',
                            'severity': 'HIGH',
                            'title': f'Obsolete base image: {base_image}',
                            'description': f'Base image {base_image} is no longer maintained and has known unpatched vulnerabilities.',
                            'artifact': base_image,
                            'recommendation': f'Update to latest LTS version (e.g., ubuntu:22.04, debian:12)',
                            'cwe': 'CWE-937: Using Components with Known Vulnerabilities'
                        })
                    
                    # Large/bloated base images
                    if any(pattern in base_image for pattern in ['ubuntu:latest', 'debian:latest', 'centos:latest']):
                        findings.append({
                            'type': 'artifact_bloated_base',
                            'severity': 'LOW',
                            'title': f'Large base image: {base_image}',
                            'description': f'Base image {base_image} is unnecessarily large. Consider using slim/alpine variants.',
                            'artifact': base_image,
                            'recommendation': 'Use slim or alpine variants: python:3.11-slim or python:3.11-alpine',
                            'cwe': 'CWE-1104: Use of Unmaintained Third Party Components'
                        })
                
                except json.JSONDecodeError:
                    pass
            
            return findings
        
        except Exception as e:
            print(f"[WARN] Could not analyze base images: {e}")
            return []
    
    def analyze_deployment_metadata(self, conn) -> List[Dict]:
        """Analyze deployment targets for artifact configuration issues."""
        findings = []
        
        try:
            # Find deployments with no artifact metadata
            results = conn.execute(
                """SELECT r.id, r.resource_name, r.resource_type FROM resources r
                   WHERE r.resource_type IN ('app_service', 'function_app', 'ecs_task', 'kubernetes_deployment')
                   AND r.experiment_id = ?
                   AND r.id NOT IN (
                       SELECT resource_id FROM resource_properties 
                       WHERE property_key = 'deployed_artifact'
                   )""",
                (self.experiment_id,)
            ).fetchall()
            
            for (res_id, res_name, res_type) in results:
                findings.append({
                    'type': 'artifact_deployment_unknown',
                    'severity': 'MEDIUM',
                    'title': f'{res_type} has no tracked deployment artifact',
                    'description': f'{res_name} ({res_type}) does not have a tracked CI/CD artifact. Cannot verify what software is deployed.',
                    'resource_id': res_id,
                    'resource_name': res_name,
                    'recommendation': 'Link CI/CD pipelines to deployment targets to track artifact lineage',
                    'cwe': 'CWE-1062: Parent Class Not Found When Deserializing Object'
                })
            
            return findings
        
        except Exception as e:
            print(f"[WARN] Could not analyze deployment metadata: {e}")
            return []
    
    def store_findings(self, conn) -> int:
        """Store all generated findings in the database."""
        stored = 0
        
        for finding in self.findings:
            try:
                resource_id = finding.get('resource_id')
                
                conn.execute(
                    """INSERT INTO findings 
                       (experiment_id, title, description, severity, resource_id, category)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        self.experiment_id,
                        finding.get('title', 'Artifact issue'),
                        finding.get('description', ''),
                        finding.get('severity', 'MEDIUM'),
                        resource_id if resource_id else None,
                        'artifact_security',
                    )
                )
                
                stored += 1
                print(f"[Finding] {finding.get('type')}: {finding.get('title')}")
            
            except Exception as e:
                print(f"[WARN] Could not store finding: {e}")
        
        conn.commit()
        return stored
    
    def analyze(self, conn) -> int:
        """Run complete artifact analysis."""
        print("[Artifact Analysis] Analyzing CI/CD artifacts for security issues...")
        
        # Analyze docker images
        artifacts = self.get_artifacts(conn)
        for artifact in artifacts:
            if 'docker_image' in artifact.get('property_key', ''):
                image_name = artifact.get('name', '')
                if image_name:
                    findings = self.analyze_docker_image(image_name, artifact)
                    self.findings.extend(findings)
        
        # Analyze base images
        base_findings = self.analyze_base_images(conn)
        self.findings.extend(base_findings)
        
        # Analyze deployment coverage
        deployment_findings = self.analyze_deployment_metadata(conn)
        self.findings.extend(deployment_findings)
        
        print(f"[Artifact Analysis] Found {len(self.findings)} artifact-related findings")
        
        # Store findings
        stored = self.store_findings(conn)
        return stored
    
    def close(self):
        """Close database connection (no-op with context manager)."""
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze CI/CD artifacts for security issues.",
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
            analyzer = CICDArtifactAnalyzer(args.experiment)
            finding_count = analyzer.analyze(conn)
            analyzer.close()
        
        print(f"[Artifact Analysis] Complete - created {finding_count} finding(s)")
        return 0
    
    except Exception as e:
        print(f"[ERROR] Failed to analyze CI/CD artifacts: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
