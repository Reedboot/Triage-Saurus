#!/usr/bin/env python3
"""
skaffold_parser.py — Parse Skaffold YAML files to extract AKS workload definitions.

Detects:
- Container images and their Dockerfiles
- Helm releases (AKS services/deployments)
- Workload configuration (replicas, ports, health checks)
- Namespace assignments
"""

import yaml
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class SkaffoldWorkload:
    """Represents a workload defined in Skaffold."""
    name: str
    image_name: str
    dockerfile: Optional[str] = None
    helm_chart: Optional[str] = None
    helm_version: Optional[str] = None
    namespace: Optional[str] = None
    replicas: int = 1
    ports: List[Dict[str, any]] = field(default_factory=list)
    health_check_path: Optional[str] = None
    health_check_port: int = 8080
    resource_type: str = "kubernetes_deployment"  # or kubernetes_service, etc.
    environment_vars: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "image_name": self.image_name,
            "dockerfile": self.dockerfile,
            "helm_chart": self.helm_chart,
            "helm_version": self.helm_version,
            "namespace": self.namespace,
            "replicas": self.replicas,
            "ports": self.ports,
            "health_check_path": self.health_check_path,
            "health_check_port": self.health_check_port,
            "resource_type": self.resource_type,
            "environment_vars": self.environment_vars,
        }


class SkaffoldParser:
    """Parse Skaffold configuration files."""
    
    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)
        self.skaffold_file = self.repo_path / "skaffold.yaml"
        
    def parse(self) -> List[SkaffoldWorkload]:
        """Parse skaffold.yaml and return list of workloads."""
        if not self.skaffold_file.exists():
            return []
        
        try:
            with open(self.skaffold_file, 'r') as f:
                config = yaml.safe_load(f)
            
            if not config:
                return []
            
            workloads = []
            
            # Parse build artifacts (container images)
            artifacts = self._extract_artifacts(config)
            
            # Parse manifests (Helm releases)
            helm_releases = self._extract_helm_releases(config)
            
            # Match artifacts to releases
            workloads = self._match_artifacts_to_releases(artifacts, helm_releases)
            
            return workloads
            
        except Exception as e:
            print(f"[WARN] Failed to parse skaffold.yaml: {e}")
            return []
    
    def _extract_artifacts(self, config: dict) -> Dict[str, dict]:
        """Extract build artifacts (container images)."""
        artifacts = {}
        
        # Check profiles first (common pattern)
        profiles = config.get('profiles', [])
        for profile in profiles:
            build = profile.get('build', {})
            for artifact in build.get('artifacts', []):
                image_name = artifact.get('image')
                if image_name:
                    artifacts[image_name] = {
                        'image': image_name,
                        'dockerfile': artifact.get('docker', {}).get('dockerfile'),
                    }
        
        # Also check top-level build
        build = config.get('build', {})
        for artifact in build.get('artifacts', []):
            image_name = artifact.get('image')
            if image_name:
                artifacts[image_name] = {
                    'image': image_name,
                    'dockerfile': artifact.get('docker', {}).get('dockerfile'),
                }
        
        return artifacts
    
    def _extract_helm_releases(self, config: dict) -> List[dict]:
        """Extract Helm releases (AKS deployments)."""
        releases = []
        
        manifests = config.get('manifests', {})
        helm = manifests.get('helm', {})
        
        for release in helm.get('releases', []):
            release_data = {
                'name': release.get('name'),
                'chart': release.get('remoteChart'),
                'version': release.get('version'),
                'values': release.get('setValues', {}),
                'value_templates': release.get('setValueTemplates', {}),
            }
            releases.append(release_data)
        
        return releases
    
    def _match_artifacts_to_releases(
        self, 
        artifacts: Dict[str, dict], 
        releases: List[dict]
    ) -> List[SkaffoldWorkload]:
        """Match container artifacts to Helm releases to create workload definitions."""
        workloads = []
        
        for release in releases:
            name = release['name']
            values = release['values']
            value_templates = release['value_templates']
            
            # Extract configuration
            namespace = value_templates.get('namespace', '').replace('{{.ENVIRONMENT_NAME}}-', '')
            replicas = int(values.get('replicas', 1))
            
            # Extract health check info
            health_path = values.get('readinessProbe.httpGet.path', '/health')
            health_port = int(values.get('readinessProbe.httpGet.port', 8080))
            
            # Extract ports
            ports = []
            container_ports = values.get('containerPorts', [])
            if isinstance(container_ports, list):
                ports = container_ports
            
            # Match to artifact by name pattern
            dockerfile = None
            image_name = None
            for artifact_name, artifact_data in artifacts.items():
                if artifact_name in name or name in artifact_name:
                    dockerfile = artifact_data.get('dockerfile')
                    image_name = artifact_name
                    break
            
            # Determine resource type from chart and workload name
            chart = release.get('chart', '')
            
            # API/Service workloads
            if any(keyword in chart for keyword in ['api', 'service', 'web']) or \
               any(keyword in name for keyword in ['api', 'service', 'web', 'frontend', 'backend']):
                if 'worker' not in name and 'queue' not in name and 'consumer' not in name:
                    resource_type = 'kubernetes_service'  # API/Web workload
                else:
                    resource_type = 'kubernetes_deployment'  # Background worker
            # Worker/Consumer workloads
            elif any(keyword in chart for keyword in ['worker', 'consumer', 'background']) or \
                 any(keyword in name for keyword in ['worker', 'queue', 'consumer', 'subscriber', 'listener']):
                resource_type = 'kubernetes_deployment'  # Background worker
            # Default
            else:
                resource_type = 'kubernetes_deployment'
            
            workload = SkaffoldWorkload(
                name=name,
                image_name=image_name or name,
                dockerfile=dockerfile,
                helm_chart=chart,
                helm_version=release.get('version'),
                namespace=namespace or 'default',
                replicas=replicas,
                ports=ports,
                health_check_path=health_path,
                health_check_port=health_port,
                resource_type=resource_type,
            )
            
            workloads.append(workload)
        
        return workloads


def extract_skaffold_workloads(repo_path: Path) -> List[SkaffoldWorkload]:
    """
    Convenience function to extract workloads from a repo.
    
    Returns list of SkaffoldWorkload objects.
    """
    parser = SkaffoldParser(repo_path)
    return parser.parse()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 skaffold_parser.py <repo_path>")
        sys.exit(1)
    
    repo = Path(sys.argv[1])
    workloads = extract_skaffold_workloads(repo)
    
    if not workloads:
        print("No Skaffold workloads found")
    else:
        print(f"Found {len(workloads)} Skaffold workloads:\n")
        for wl in workloads:
            print(f"📦 {wl.name}")
            print(f"   Type: {wl.resource_type}")
            print(f"   Image: {wl.image_name}")
            if wl.dockerfile:
                print(f"   Dockerfile: {wl.dockerfile}")
            if wl.helm_chart:
                print(f"   Helm: {wl.helm_chart} ({wl.helm_version})")
            print(f"   Namespace: {wl.namespace}")
            print(f"   Replicas: {wl.replicas}")
            print(f"   Health: {wl.health_check_path} on port {wl.health_check_port}")
            print()
