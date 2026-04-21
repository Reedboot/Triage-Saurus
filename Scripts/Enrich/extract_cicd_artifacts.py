#!/usr/bin/env python3
"""
extract_cicd_artifacts.py — Extract build artifacts and deployment targets from CI/CD pipelines.

Detects and parses:
- GitHub Workflows (.github/workflows/*.yml)
- GitLab CI (.gitlab-ci.yml)
- Azure Pipelines (azure-pipelines.yml)
- Jenkins (Jenkinsfile)

Extracts:
- Build artifacts (docker images, npm packages, compiled binaries)
- Deployment targets (App Services, Functions, container registries)
- Build identities (service principals, managed identities)

Creates connections:
- artifact → deployment_target (ci_cd_deploys_to)
- ci_cd_pipeline → artifact (ci_cd_builds)

Usage:
    python3 Scripts/Enrich/extract_cicd_artifacts.py --experiment 001
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Optional, Tuple

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

from db_helpers import get_db_connection


class CICDArtifactExtractor:
    """Extract CI/CD artifacts and deployment targets from pipeline files."""
    
    def __init__(self, repo_path: Path, experiment_id: str):
        self.repo_path = Path(repo_path)
        self.experiment_id = experiment_id
        self.artifacts: List[Dict] = []
        self.deployments: List[Dict] = []
    
    def extract_all(self) -> Tuple[List[Dict], List[Dict]]:
        """Extract artifacts and deployments from all CI/CD files."""
        self.extract_github_workflows()
        self.extract_gitlab_ci()
        self.extract_azure_pipelines()
        self.extract_dockerfile_info()
        return self.artifacts, self.deployments
    
    def extract_github_workflows(self) -> None:
        """Extract from .github/workflows/*.yml files."""
        workflows_dir = self.repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return
        
        print(f"[CI/CD] Parsing GitHub Workflows from {workflows_dir}")
        
        for workflow_file in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")):
            try:
                with open(workflow_file) as f:
                    workflow = yaml.safe_load(f)
                
                if not workflow or 'jobs' not in workflow:
                    continue
                
                workflow_name = workflow.get('name', workflow_file.stem)
                
                for job_name, job in workflow.get('jobs', {}).items():
                    if not isinstance(job, dict):
                        continue
                    
                    # Extract docker build commands
                    steps = job.get('steps', [])
                    for step in steps:
                        if not isinstance(step, dict):
                            continue
                        
                        run_cmd = step.get('run', '') or ''
                        
                        # Docker build detection
                        docker_build = re.search(
                            r'docker\s+build.*?-t\s+([^\s]+)',
                            run_cmd,
                            re.IGNORECASE | re.DOTALL
                        )
                        if docker_build:
                            image = docker_build.group(1).strip()
                            self.artifacts.append({
                                'type': 'docker_image',
                                'name': image,
                                'source': f'{workflow_file.name}#{job_name}',
                                'platform': 'GitHub Actions',
                                'file': str(workflow_file.relative_to(self.repo_path) if self.repo_path in workflow_file.parents else workflow_file)
                            })
                        
                        # Docker push detection (indicates deployment)
                        docker_push = re.search(
                            r'docker\s+push\s+([^\s]+)',
                            run_cmd,
                            re.IGNORECASE
                        )
                        if docker_push:
                            registry = docker_push.group(1).strip()
                            self.deployments.append({
                                'type': 'docker_registry',
                                'target': registry,
                                'source': f'{workflow_file.name}#{job_name}',
                                'platform': 'GitHub Actions'
                            })
                        
                        # Azure deploy detection (webapp, function, etc.)
                        azure_deploy = re.search(
                            r'az\s+\w*app.*?--name\s+([^\s]+)',
                            run_cmd,
                            re.IGNORECASE
                        )
                        if azure_deploy:
                            service_name = azure_deploy.group(1)
                            self.deployments.append({
                                'type': 'azure_service',
                                'target': service_name,
                                'source': f'{workflow_file.name}#{job_name}',
                                'platform': 'GitHub Actions',
                                'command': run_cmd[:100]
                            })
                        
                        # npm publish detection
                        if 'npm publish' in run_cmd.lower():
                            self.artifacts.append({
                                'type': 'npm_package',
                                'name': re.search(r'npm\s+publish\s+([^\s]+)', run_cmd),
                                'source': f'{workflow_file.name}#{job_name}',
                                'platform': 'GitHub Actions'
                            })
                        
                        # dotnet publish detection
                        dotnet_publish = re.search(
                            r'dotnet\s+publish.*?-o\s+([^\s]+)',
                            run_cmd,
                            re.IGNORECASE
                        )
                        if dotnet_publish:
                            output_path = dotnet_publish.group(1)
                            self.artifacts.append({
                                'type': 'dotnet_build',
                                'name': output_path,
                                'source': f'{workflow_file.name}#{job_name}',
                                'platform': 'GitHub Actions'
                            })
            
            except Exception as e:
                print(f"[WARN] Error parsing GitHub Workflow {workflow_file.name}: {e}")
    
    def extract_gitlab_ci(self) -> None:
        """Extract from .gitlab-ci.yml file."""
        gitlab_file = self.repo_path / ".gitlab-ci.yml"
        if not gitlab_file.exists():
            return
        
        print(f"[CI/CD] Parsing GitLab CI from {gitlab_file}")
        
        try:
            with open(gitlab_file) as f:
                gitlab = yaml.safe_load(f)
            
            if not gitlab:
                return
            
            # Parse stages
            for stage_name, stage_config in gitlab.items():
                if stage_name.startswith('.') or not isinstance(stage_config, dict):
                    continue
                
                script = stage_config.get('script', [])
                if isinstance(script, str):
                    script = [script]
                
                script_text = '\n'.join(script)
                
                # Docker build
                docker_build = re.search(
                    r'docker\s+build.*?-t\s+([^\s]+)',
                    script_text,
                    re.IGNORECASE
                )
                if docker_build:
                    image = docker_build.group(1).strip()
                    self.artifacts.append({
                        'type': 'docker_image',
                        'name': image,
                        'source': f'.gitlab-ci.yml#{stage_name}',
                        'platform': 'GitLab CI'
                    })
                
                # Docker push
                docker_push = re.search(
                    r'docker\s+push\s+([^\s]+)',
                    script_text,
                    re.IGNORECASE
                )
                if docker_push:
                    registry = docker_push.group(1).strip()
                    self.deployments.append({
                        'type': 'docker_registry',
                        'target': registry,
                        'source': f'.gitlab-ci.yml#{stage_name}',
                        'platform': 'GitLab CI'
                    })
                
                # Kubernetes deploy
                if 'kubectl' in script_text.lower():
                    self.deployments.append({
                        'type': 'kubernetes',
                        'target': 'kubernetes_cluster',
                        'source': f'.gitlab-ci.yml#{stage_name}',
                        'platform': 'GitLab CI'
                    })
        
        except Exception as e:
            print(f"[WARN] Error parsing GitLab CI: {e}")
    
    def extract_azure_pipelines(self) -> None:
        """Extract from azure-pipelines.yml file."""
        azure_file = self.repo_path / "azure-pipelines.yml"
        if not azure_file.exists():
            return
        
        print(f"[CI/CD] Parsing Azure Pipelines from {azure_file}")
        
        try:
            with open(azure_file) as f:
                pipelines = yaml.safe_load(f)
            
            if not pipelines or 'stages' not in pipelines:
                return
            
            for stage in pipelines.get('stages', []):
                if not isinstance(stage, dict):
                    continue
                
                stage_name = stage.get('stage', 'unknown')
                jobs = stage.get('jobs', [])
                
                for job in jobs:
                    if not isinstance(job, dict):
                        continue
                    
                    job_name = job.get('job', 'unknown')
                    steps = job.get('steps', [])
                    
                    for step in steps:
                        if not isinstance(step, dict):
                            continue
                        
                        script = step.get('script', '') or ''
                        
                        # Docker build
                        docker_build = re.search(
                            r'docker\s+build.*?-t\s+([^\s]+)',
                            script,
                            re.IGNORECASE | re.DOTALL
                        )
                        if docker_build:
                            image = docker_build.group(1).strip()
                            self.artifacts.append({
                                'type': 'docker_image',
                                'name': image,
                                'source': f'azure-pipelines.yml#{stage_name}#{job_name}',
                                'platform': 'Azure Pipelines'
                            })
                        
                        # App Service deploy
                        app_deploy = re.search(
                            r'az\s+webapp\s+.*?--name\s+([^\s]+)',
                            script,
                            re.IGNORECASE
                        )
                        if app_deploy:
                            app_name = app_deploy.group(1)
                            self.deployments.append({
                                'type': 'app_service',
                                'target': app_name,
                                'source': f'azure-pipelines.yml#{stage_name}#{job_name}',
                                'platform': 'Azure Pipelines'
                            })
                        
                        # Function app deploy
                        func_deploy = re.search(
                            r'az\s+functionapp\s+.*?--name\s+([^\s]+)',
                            script,
                            re.IGNORECASE
                        )
                        if func_deploy:
                            func_name = func_deploy.group(1)
                            self.deployments.append({
                                'type': 'function_app',
                                'target': func_name,
                                'source': f'azure-pipelines.yml#{stage_name}#{job_name}',
                                'platform': 'Azure Pipelines'
                            })
        
        except Exception as e:
            print(f"[WARN] Error parsing Azure Pipelines: {e}")
    
    def extract_dockerfile_info(self) -> None:
        """Extract base image info from Dockerfiles."""
        dockerfiles = list(self.repo_path.glob("Dockerfile")) + list(self.repo_path.glob("**/Dockerfile"))
        
        for dockerfile in dockerfiles[:5]:  # Limit to first 5
            try:
                with open(dockerfile) as f:
                    content = f.read()
                
                # Find FROM statement (base image)
                from_match = re.search(
                    r'FROM\s+([^\s]+)(?:\s+AS\s+)?',
                    content,
                    re.IGNORECASE | re.MULTILINE
                )
                
                if from_match:
                    base_image = from_match.group(1).strip()
                    self.artifacts.append({
                        'type': 'base_image',
                        'name': base_image,
                        'source': f'{dockerfile.relative_to(self.repo_path) if self.repo_path in dockerfile.parents else dockerfile}',
                        'platform': 'Docker',
                        'file': str(dockerfile.relative_to(self.repo_path) if self.repo_path in dockerfile.parents else dockerfile)
                    })
            
            except Exception as e:
                print(f"[WARN] Error parsing Dockerfile {dockerfile}: {e}")


def store_artifacts_and_deployments(conn, experiment_id: str, artifacts: List[Dict], deployments: List[Dict]) -> int:
    """Store artifacts and deployments in database as resources and connections."""
    stored = 0
    
    # Store artifacts as resources
    for i, artifact in enumerate(artifacts):
        try:
            artifact_type = artifact.get('type', 'unknown')
            artifact_name = artifact.get('name', '').strip()
            
            if not artifact_name or artifact_name == 'None':
                continue
            
            # Try to match to existing resource or create metadata connection
            existing = conn.execute(
                "SELECT id FROM resources WHERE resource_name = ? AND experiment_id = ?",
                (artifact_name, experiment_id)
            ).fetchone()
            
            if not existing:
                # For now, just create connection metadata with unique keys
                # Later phase will link to actual IaC resources
                # Use a unique key combining type and index to avoid constraint violations
                unique_key = f'cicd_artifact_{artifact_type}_{i}'
                conn.execute(
                    """INSERT OR IGNORE INTO resource_properties 
                       (resource_id, property_key, property_value, is_security_relevant)
                       VALUES (0, ?, ?, 1)""",
                    (unique_key, json.dumps(artifact))
                )
                print(f"[Artifact] {artifact_type}: {artifact_name} (from {artifact.get('source')})")
                stored += 1
        
        except Exception as e:
            print(f"[WARN] Could not store artifact {artifact}: {e}")
    
    # Store deployment information
    for i, deployment in enumerate(deployments):
        try:
            deployment_type = deployment.get('type', 'unknown')
            target_name = deployment.get('target', '').strip()
            
            if not target_name:
                continue
            
            # Create connection metadata for deployment with unique keys
            unique_key = f'cicd_deployment_{deployment_type}_{i}'
            conn.execute(
                """INSERT OR IGNORE INTO resource_properties 
                   (resource_id, property_key, property_value, is_security_relevant)
                   VALUES (0, ?, ?, 1)""",
                (unique_key, json.dumps(deployment))
            )
            print(f"[Deployment] {deployment_type}: {target_name} (from {deployment.get('source')})")
            stored += 1
        
        except Exception as e:
            print(f"[WARN] Could not store deployment {deployment}: {e}")
    
    conn.commit()
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract CI/CD artifacts and deployment targets from pipeline files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--experiment",
        required=True,
        help="Experiment ID to process",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Repository path (auto-detected if not provided)",
    )
    
    args = parser.parse_args()
    
    # Determine repo path
    if args.repo:
        repo_path = Path(args.repo)
    else:
        # Try to find repo from database
        with get_db_connection() as conn:
            result = conn.execute(
                "SELECT repos FROM experiments WHERE id = ?",
                (args.experiment,)
            ).fetchone()
            
            if not result or not result[0]:
                print("[ERROR] Could not determine repo path. Provide with --repo", file=sys.stderr)
                return 1
            
            try:
                repos = json.loads(result[0]) if isinstance(result[0], str) else result[0]
                repo_path = Path(repos[0]) if isinstance(repos, list) else Path(repos)
            except (json.JSONDecodeError, TypeError, IndexError):
                print("[ERROR] Could not parse repos from experiment", file=sys.stderr)
                return 1
    
    if not repo_path.exists():
        print(f"[ERROR] Repository not found: {repo_path}", file=sys.stderr)
        return 1
    
    print(f"[CI/CD Artifacts] Scanning {repo_path}")
    
    try:
        extractor = CICDArtifactExtractor(repo_path, args.experiment)
        artifacts, deployments = extractor.extract_all()
        
        print(f"[CI/CD Artifacts] Found {len(artifacts)} artifacts, {len(deployments)} deployments")
        
        with get_db_connection() as conn:
            stored = store_artifacts_and_deployments(conn, args.experiment, artifacts, deployments)
            print(f"[CI/CD Artifacts] Stored {stored} artifact/deployment records")
        
        return 0
    
    except Exception as e:
        print(f"[ERROR] Failed to extract CI/CD artifacts: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
