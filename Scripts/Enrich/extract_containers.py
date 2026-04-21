#!/usr/bin/env python3
"""Extract container images and configurations from infrastructure code.

Discovers containers running on:
- EC2 instances (via user_data scripts)
- Lambda functions (environment configs)
- ECS task definitions
- Docker Compose files
- Kubernetes manifests (already handled separately)

Creates docker_container resources in database and links to parent resources.
"""

import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict


@dataclass
class Container:
    """Container resource extracted from infrastructure code."""
    name: str
    image: str
    image_tag: Optional[str]
    image_registry: Optional[str]
    ports: List[int]
    environment_vars: Dict[str, str]
    parent_resource_id: int
    parent_resource_name: str
    parent_resource_type: str
    source_file: str
    source_line: int
    command: Optional[str] = None
    volumes: Optional[List[str]] = None
    detach: bool = False
    pull_policy: Optional[str] = None


class ContainerExtractor:
    """Extract container configurations from infrastructure code."""

    def __init__(self, repo_path: Path, experiment_id: str, db_path: Path):
        """Initialize extractor."""
        self.repo_path = Path(repo_path)
        self.experiment_id = experiment_id
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
    
    PORT_MAPPING_PATTERN = re.compile(r'-p\s+(?:(\d+):)?(\d+)(?:/(\w+))?')
    ENV_PATTERN = re.compile(r'-e\s+(\w+)=([^\s]+)')
    VOLUME_PATTERN = re.compile(r'-v\s+([^\s]+)')
    IMAGE_PATTERN = re.compile(r'^(?:([^/]+)/)?([^/:]+)(?::(.+))?$')

    def extract_from_terraform(self) -> List[Container]:
        """Extract containers from Terraform files (user_data scripts)."""
        containers = []
        
        # Find all EC2 instances with user_data
        ec2_resources = self.conn.execute(
            """SELECT id, resource_name, source_file FROM resources 
               WHERE experiment_id=? AND resource_type='aws_instance'""",
            (self.experiment_id,)
        ).fetchall()
        
        for ec2 in ec2_resources:
            # Source file path may be relative to repo root or already include repo subdir
            source_path = ec2['source_file']
            if source_path.startswith('eks/'):
                # Path already includes the subdirectory
                tf_file = self.repo_path / source_path
            else:
                tf_file = self.repo_path / source_path
                
            if not tf_file.exists():
                # Try without the repo subdir (in case it's already included)
                parts = source_path.split('/')
                if len(parts) > 1 and parts[0] in ['eks', 'Terraform', 'scripts']:
                    tf_file = self.repo_path.parent / source_path
                    
            if not tf_file.exists():
                continue
                
            content = tf_file.read_text()
            
            # Extract user_data block
            user_data_match = re.search(
                r'user_data\s*=\s*<<-?EOF\s*(.*?)\s*EOF',
                content,
                re.DOTALL | re.IGNORECASE
            )
            
            if user_data_match:
                user_data = user_data_match.group(1)
                extracted = self._parse_docker_commands(
                    user_data,
                    parent_id=ec2['id'],
                    parent_name=ec2['resource_name'],
                    parent_type='aws_instance',
                    source_file=ec2['source_file'],
                    source_line=ec2['source_file']  # Approximate
                )
                containers.extend(extracted)
        
        return containers

    def _parse_docker_commands(
        self,
        script_content: str,
        parent_id: int,
        parent_name: str,
        parent_type: str,
        source_file: str,
        source_line: int
    ) -> List[Container]:
        """Parse docker run commands from a script."""
        containers = []
        
        # Simple approach: find "docker run" then extract image and flags
        for line in script_content.split('\n'):
            line = line.strip()
            if 'docker' not in line or 'run' not in line:
                continue
            
            # Extract everything after "docker run"
            match = re.search(r'(sudo\s+)?docker\s+run\s+(.+)$', line, re.IGNORECASE)
            if not match:
                continue
            
            args = match.group(2)
            
            # Parse flags and image from args
            # Strategy: Parse Docker flags properly
            # Single-letter flags like -p, -e, -v need value from next token
            # Long flags use = like --name=value
            tokens = args.split()
            
            image = None
            flags_str = ''
            i = 0
            
            while i < len(tokens):
                token = tokens[i]
                
                # Short flag that takes a value (-p 8080:8080, -e KEY=VAL, -v /path)
                if token.startswith('-') and len(token) == 2 and token[1] not in 'dD':
                    # Most single-letter flags take a value
                    flags_str += token + ' '
                    if i + 1 < len(tokens):
                        flags_str += tokens[i + 1] + ' '
                        i += 2
                    else:
                        i += 1
                # -d flag (detach, no value)
                elif token in ['-d', '--detach']:
                    flags_str += token + ' '
                    i += 1
                # Long flag
                elif token.startswith('--'):
                    flags_str += token + ' '
                    if '=' not in token and i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
                        flags_str += tokens[i + 1] + ' '
                        i += 2
                    else:
                        i += 1
                # Image or command
                else:
                    image = token
                    break
                
            # Everything after image is command/args (we don't need it for name)
            
            if not image:
                continue
            
            # Extract metadata
            ports = self._extract_ports(flags_str)
            envs = self._extract_env_vars(flags_str)
            volumes = self._extract_volumes(flags_str)
            registry, repo, tag = self._parse_image(image)
            
            # Generate container name from image
            container_name = repo if repo else image
            
            container = Container(
                name=f"{container_name}_{len(containers)}",
                image=image,
                image_tag=tag,
                image_registry=registry or 'docker.io',
                ports=ports,
                environment_vars=envs,
                parent_resource_id=parent_id,
                parent_resource_name=parent_name,
                parent_resource_type=parent_type,
                source_file=source_file,
                source_line=source_line,
                detach='-d' in flags_str,
                pull_policy='Always' if 'pull=always' in flags_str.lower() else None,
                volumes=volumes
            )
            containers.append(container)
        
        return containers

    def _extract_ports(self, flags: str) -> List[int]:
        """Extract port mappings from docker flags."""
        ports = []
        for match in self.PORT_MAPPING_PATTERN.finditer(flags):
            container_port = int(match.group(2))
            ports.append(container_port)
        return list(set(ports))  # Deduplicate

    def _extract_env_vars(self, flags: str) -> Dict[str, str]:
        """Extract environment variables from docker flags."""
        envs = {}
        for match in self.ENV_PATTERN.finditer(flags):
            key = match.group(1)
            value = match.group(2).strip('"\'')
            envs[key] = value
        return envs

    def _extract_volumes(self, flags: str) -> List[str]:
        """Extract volume mounts from docker flags."""
        volumes = []
        for match in self.VOLUME_PATTERN.finditer(flags):
            volumes.append(match.group(1))
        return volumes

    def _parse_image(self, image: str) -> Tuple[Optional[str], str, Optional[str]]:
        """Parse image string into registry, repo, and tag.
        
        Examples:
            - nginx:latest → (None, 'nginx', 'latest')
            - ubuntu → (None, 'ubuntu', None)
            - gcr.io/project/image:v1 → ('gcr.io', 'project/image', 'v1')
            - docker.io/library/nginx:1.19 → ('docker.io', 'library/nginx', '1.19')
        """
        match = self.IMAGE_PATTERN.match(image)
        if not match:
            return (None, image, None)
        
        registry = match.group(1)
        repo = match.group(2)
        tag = match.group(3)
        
        # If registry looks like a repo path (has /), reassemble
        if registry and '/' not in registry:
            # It's a registry
            pass
        elif registry and '/' in registry:
            # It's part of the repo path
            repo = f"{registry}/{repo}"
            registry = None
        
        return (registry, repo, tag)

    def store_containers(self, containers: List[Container]) -> int:
        """Store extracted containers in database."""
        if not containers:
            return 0
        
        cursor = self.conn.cursor()
        stored = 0
        
        for container in containers:
            try:
                # Check if already exists
                existing = cursor.execute(
                    """SELECT id FROM resources 
                       WHERE experiment_id=? AND resource_name=? AND resource_type='docker_container'""",
                    (self.experiment_id, container.name)
                ).fetchone()
                
                if existing:
                    continue
                
                # Insert container resource
                cursor.execute(
                    """INSERT INTO resources 
                       (experiment_id, repo_id, resource_name, resource_type, provider, 
                        discovered_by, discovery_method, source_file, source_line_start,
                        parent_resource_id, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.experiment_id,
                        self._get_repo_id(),
                        container.name,
                        'docker_container',
                        'docker',
                        'ContainerExtractor',
                        'UserData',
                        container.source_file,
                        container.source_line,
                        container.parent_resource_id,
                        'active'
                    )
                )
                
                resource_id = cursor.lastrowid
                
                # Store container properties
                properties = {
                    'image': container.image,
                    'image_tag': container.image_tag or 'latest',
                    'image_registry': container.image_registry or 'docker.io',
                    'ports': ','.join(map(str, container.ports)),
                    'parent_resource_type': container.parent_resource_type,
                    'parent_resource_name': container.parent_resource_name,
                    'detach': str(container.detach),
                    'pull_policy': container.pull_policy or 'default',
                }
                
                if container.command:
                    properties['command'] = container.command
                if container.volumes:
                    properties['volumes'] = ','.join(container.volumes)
                if container.environment_vars:
                    properties['environment_vars'] = str(container.environment_vars)
                
                for key, value in properties.items():
                    cursor.execute(
                        """INSERT INTO resource_properties 
                           (resource_id, property_key, property_value, is_security_relevant)
                           VALUES (?, ?, ?, ?)""",
                        (resource_id, key, value, 1 if key in ('image_tag', 'image') else 0)
                    )
                
                stored += 1
                
            except Exception as e:
                print(f"Warning: Failed to store container {container.name}: {e}")
                continue
        
        self.conn.commit()
        return stored

    def _get_repo_id(self) -> int:
        """Get first repo_id for this experiment."""
        result = self.conn.execute(
            "SELECT repo_id FROM resources WHERE experiment_id=? LIMIT 1",
            (self.experiment_id,)
        ).fetchone()
        return result['repo_id'] if result else 1

    def close(self):
        """Close database connection."""
        self.conn.close()


def extract_and_store_containers(repo_path: Path, experiment_id: str, db_path: Path) -> int:
    """Extract containers from infrastructure code and store in database."""
    extractor = ContainerExtractor(repo_path, experiment_id, db_path)
    
    try:
        print(f"Extracting containers from Terraform for experiment {experiment_id}...")
        containers = extractor.extract_from_terraform()
        
        if containers:
            print(f"Found {len(containers)} container(s):")
            for c in containers:
                print(f"  - {c.name}: {c.image} (ports: {c.ports})")
        
        stored = extractor.store_containers(containers)
        print(f"Stored {stored} container resource(s) in database")
        return stored
        
    finally:
        extractor.close()


if __name__ == '__main__':
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Extract container definitions from infrastructure code.",
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
    parser.add_argument(
        "--db",
        default=None,
        help="Database path (default: Output/Data/cozo.db)",
    )
    
    args = parser.parse_args()
    
    # Determine repo path from database or use provided
    if args.db:
        db_path = Path(args.db)
    else:
        db_path = Path.cwd() / 'Output/Data/cozo.db'
    
    if args.repo:
        repo_path = Path(args.repo)
    else:
        # Try to find repo from database experiment record
        import sqlite3
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            result = conn.execute(
                "SELECT target_repo FROM experiments WHERE id=? LIMIT 1",
                (args.experiment,)
            ).fetchone()
            conn.close()
            if result and result['target_repo']:
                repo_path = Path(result['target_repo'])
            else:
                print(f"Error: Could not determine repo path. Provide with --repo", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error: Could not read database: {e}", file=sys.stderr)
            sys.exit(1)
    
    extract_and_store_containers(repo_path, args.experiment, db_path)
