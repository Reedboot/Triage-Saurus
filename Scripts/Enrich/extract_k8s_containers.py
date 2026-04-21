#!/usr/bin/env python3
"""
extract_k8s_containers.py — Extract container images from Kubernetes manifests.

Parses Kubernetes YAML files to extract:
- Deployment containers
- StatefulSet containers
- DaemonSet containers
- CronJob containers
- Pod containers
- Init containers
- Sidecar containers

Links containers to their parent K8s resources.

Usage:
    python3 Scripts/Enrich/extract_k8s_containers.py --experiment 001 --repo /path/to/repo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

from db_helpers import get_db_connection


class K8sContainerExtractor:
    """Extract containers from Kubernetes manifests."""
    
    def __init__(self, repo_path: Path, experiment_id: str):
        self.repo_path = Path(repo_path)
        self.experiment_id = experiment_id
        self.containers: List[Dict] = []
        self.container_links: List[Dict] = []
    
    def extract_all(self) -> tuple:
        """Extract containers from all K8s manifests."""
        # Find all YAML files recursively
        yaml_files = list(self.repo_path.glob('**/*.yaml')) + list(self.repo_path.glob('**/*.yml'))
        
        # Filter to likely K8s manifests (skip config files, workflows)
        yaml_files = [
            f for f in yaml_files 
            if 'workflow' not in str(f).lower() 
            and 'config' not in str(f).lower()
            and 'funding' not in str(f).lower()
            and 'dependabot' not in str(f).lower()
        ]
        
        print(f"[K8s Containers] Found {len(yaml_files)} potential K8s YAML files")
        
        for yaml_file in yaml_files:
            try:
                self.parse_yaml_file(yaml_file)
            except Exception as e:
                print(f"[WARN] Error parsing {yaml_file}: {e}")
        
        return self.containers, self.container_links
    
    def parse_yaml_file(self, yaml_file: Path) -> None:
        """Parse a single Kubernetes YAML file."""
        with open(yaml_file) as f:
            # Handle YAML files with multiple documents (---)
            docs = yaml.safe_load_all(f)
            
            for doc in docs:
                if not doc or not isinstance(doc, dict):
                    continue
                
                kind = doc.get('kind', '')
                metadata = doc.get('metadata', {})
                name = metadata.get('name', 'unknown')
                namespace = metadata.get('namespace', 'default')
                spec = doc.get('spec', {})
                
                # Extract based on resource type
                if kind == 'Deployment':
                    self.extract_from_deployment(doc, yaml_file)
                elif kind == 'StatefulSet':
                    self.extract_from_statefulset(doc, yaml_file)
                elif kind == 'DaemonSet':
                    self.extract_from_daemonset(doc, yaml_file)
                elif kind == 'CronJob':
                    self.extract_from_cronjob(doc, yaml_file)
                elif kind == 'Pod':
                    self.extract_from_pod(doc, yaml_file)
    
    def extract_containers_from_spec(self, pod_spec: Dict, parent_info: Dict) -> None:
        """Extract containers from a pod spec."""
        if not pod_spec:
            return
        
        # Regular containers
        containers = pod_spec.get('containers', [])
        for container in containers:
            if isinstance(container, dict):
                image = container.get('image', 'unknown:latest')
                name = container.get('name', 'unnamed')
                
                self.containers.append({
                    'type': 'k8s_container',
                    'name': name,
                    'image': image,
                    'namespace': parent_info.get('namespace', 'default'),
                    'parent_kind': parent_info.get('kind'),
                    'parent_name': parent_info.get('name'),
                    'source': str(parent_info.get('file', 'unknown')),
                    'init': False
                })
                
                # Link container to parent
                self.container_links.append({
                    'container': name,
                    'image': image,
                    'parent_kind': parent_info.get('kind'),
                    'parent_name': parent_info.get('name'),
                    'namespace': parent_info.get('namespace', 'default')
                })
        
        # Init containers
        init_containers = pod_spec.get('initContainers', [])
        for container in init_containers:
            if isinstance(container, dict):
                image = container.get('image', 'unknown:latest')
                name = container.get('name', 'unnamed')
                
                self.containers.append({
                    'type': 'k8s_init_container',
                    'name': name,
                    'image': image,
                    'namespace': parent_info.get('namespace', 'default'),
                    'parent_kind': parent_info.get('kind'),
                    'parent_name': parent_info.get('name'),
                    'source': str(parent_info.get('file', 'unknown')),
                    'init': True
                })
    
    def extract_from_deployment(self, doc: Dict, yaml_file: Path) -> None:
        """Extract from Deployment resource."""
        metadata = doc.get('metadata', {})
        spec = doc.get('spec', {})
        template = spec.get('template', {})
        pod_spec = template.get('spec', {})
        
        parent_info = {
            'kind': 'Deployment',
            'name': metadata.get('name', 'unknown'),
            'namespace': metadata.get('namespace', 'default'),
            'file': yaml_file
        }
        
        self.extract_containers_from_spec(pod_spec, parent_info)
    
    def extract_from_statefulset(self, doc: Dict, yaml_file: Path) -> None:
        """Extract from StatefulSet resource."""
        metadata = doc.get('metadata', {})
        spec = doc.get('spec', {})
        template = spec.get('template', {})
        pod_spec = template.get('spec', {})
        
        parent_info = {
            'kind': 'StatefulSet',
            'name': metadata.get('name', 'unknown'),
            'namespace': metadata.get('namespace', 'default'),
            'file': yaml_file
        }
        
        self.extract_containers_from_spec(pod_spec, parent_info)
    
    def extract_from_daemonset(self, doc: Dict, yaml_file: Path) -> None:
        """Extract from DaemonSet resource."""
        metadata = doc.get('metadata', {})
        spec = doc.get('spec', {})
        template = spec.get('template', {})
        pod_spec = template.get('spec', {})
        
        parent_info = {
            'kind': 'DaemonSet',
            'name': metadata.get('name', 'unknown'),
            'namespace': metadata.get('namespace', 'default'),
            'file': yaml_file
        }
        
        self.extract_containers_from_spec(pod_spec, parent_info)
    
    def extract_from_cronjob(self, doc: Dict, yaml_file: Path) -> None:
        """Extract from CronJob resource."""
        metadata = doc.get('metadata', {})
        spec = doc.get('spec', {})
        job_template = spec.get('jobTemplate', {})
        job_spec = job_template.get('spec', {})
        pod_template = job_spec.get('template', {})
        pod_spec = pod_template.get('spec', {})
        
        parent_info = {
            'kind': 'CronJob',
            'name': metadata.get('name', 'unknown'),
            'namespace': metadata.get('namespace', 'default'),
            'file': yaml_file
        }
        
        self.extract_containers_from_spec(pod_spec, parent_info)
    
    def extract_from_pod(self, doc: Dict, yaml_file: Path) -> None:
        """Extract from Pod resource."""
        metadata = doc.get('metadata', {})
        pod_spec = doc.get('spec', {})
        
        parent_info = {
            'kind': 'Pod',
            'name': metadata.get('name', 'unknown'),
            'namespace': metadata.get('namespace', 'default'),
            'file': yaml_file
        }
        
        self.extract_containers_from_spec(pod_spec, parent_info)


def store_k8s_containers(conn, experiment_id: str, containers: List[Dict], links: List[Dict]) -> int:
    """Store K8s containers in database."""
    stored = 0
    
    for i, container in enumerate(containers):
        try:
            unique_key = f"k8s_container_{i}"
            conn.execute(
                """INSERT OR IGNORE INTO resource_properties 
                   (resource_id, property_key, property_value, is_security_relevant)
                   VALUES (0, ?, ?, 1)""",
                (unique_key, json.dumps(container))
            )
            
            print(f"[K8s Container] {container.get('parent_kind')}: {container.get('parent_name')} → {container.get('name')} ({container.get('image')})")
            stored += 1
        
        except Exception as e:
            print(f"[WARN] Could not store K8s container: {e}")
    
    conn.commit()
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract containers from Kubernetes manifests.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--repo", default=None, help="Repository path")
    
    args = parser.parse_args()
    
    # Determine repo path
    if args.repo:
        repo_path = Path(args.repo)
    else:
        with get_db_connection() as conn:
            result = conn.execute(
                "SELECT repos FROM experiments WHERE id = ?",
                (args.experiment,)
            ).fetchone()
            
            if not result or not result[0]:
                print("[ERROR] Could not determine repo path", file=sys.stderr)
                return 1
            
            try:
                repos = json.loads(result[0]) if isinstance(result[0], str) else result[0]
                repo_path = Path(repos[0]) if isinstance(repos, list) else Path(repos)
            except (json.JSONDecodeError, TypeError, IndexError):
                print("[ERROR] Could not parse repos", file=sys.stderr)
                return 1
    
    if not repo_path.exists():
        print(f"[ERROR] Repository not found: {repo_path}", file=sys.stderr)
        return 1
    
    print(f"[K8s Containers] Scanning {repo_path}")
    
    try:
        extractor = K8sContainerExtractor(repo_path, args.experiment)
        containers, links = extractor.extract_all()
        
        print(f"[K8s Containers] Found {len(containers)} containers in K8s manifests")
        
        with get_db_connection() as conn:
            stored = store_k8s_containers(conn, args.experiment, containers, links)
            print(f"[K8s Containers] Stored {stored} K8s container(s)")
        
        return 0
    
    except Exception as e:
        print(f"[ERROR] Failed to extract K8s containers: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
