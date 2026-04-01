#!/usr/bin/env python3
"""Extract third-party dependencies from manifest files using direct parsing."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
from db_helpers import get_db_connection



def _extract_nuget_dependencies(csproj_file: Path) -> List[Dict]:
    """Extract NuGet dependencies from a .csproj file."""
    deps = []
    try:
        tree = ET.parse(csproj_file)
        root = tree.getroot()
        
        # Handle XML namespaces
        ns = {'': 'http://schemas.microsoft.com/developer/msbuild/2003'}
        
        # Find all PackageReference elements
        for pkg_ref in root.findall('.//PackageReference', ns) or root.findall('.//PackageReference'):
            include = pkg_ref.get('Include')
            version = pkg_ref.get('Version')
            if include and version:
                deps.append({
                    'package_name': include,
                    'version': version,
                    'package_manager': 'nuget',
                    'language': '.NET',
                    'source_file': str(csproj_file),
                    'project_path': str(csproj_file.parent),
                })
    except Exception as e:
        print(f"  Warning: Failed to parse {csproj_file}: {e}")
    
    return deps


def _extract_npm_dependencies(package_json: Path) -> List[Dict]:
    """Extract npm dependencies from package.json."""
    deps = []
    try:
        with open(package_json) as f:
            data = json.load(f)
        
        for dep_type in ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']:
            for package_name, version in (data.get(dep_type) or {}).items():
                deps.append({
                    'package_name': package_name,
                    'version': version,
                    'package_manager': 'npm',
                    'language': 'Node.js',
                    'source_file': str(package_json),
                    'project_path': str(package_json.parent),
                })
    except Exception as e:
        print(f"  Warning: Failed to parse {package_json}: {e}")
    
    return deps


def _extract_python_dependencies(req_file: Path) -> List[Dict]:
    """Extract pip dependencies from requirements.txt."""
    deps = []
    try:
        with open(req_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Parse version specifiers: package==1.0, package>=1.0, etc.
                match = re.match(r'^([a-zA-Z0-9_\-\.]+)\s*([><=!~]+)\s*(.+)$', line)
                if match:
                    package_name, op, version = match.groups()
                    deps.append({
                        'package_name': package_name.strip(),
                        'version': f"{op}{version.strip()}",
                        'package_manager': 'pip',
                        'language': 'Python',
                        'source_file': str(req_file),
                        'project_path': str(req_file.parent),
                    })
    except Exception as e:
        print(f"  Warning: Failed to parse {req_file}: {e}")
    
    return deps


def _extract_go_dependencies(go_mod: Path) -> List[Dict]:
    """Extract Go module dependencies from go.mod."""
    deps = []
    try:
        with open(go_mod) as f:
            in_require = False
            for line in f:
                line = line.strip()
                
                if line == 'require (':
                    in_require = True
                    continue
                elif line == ')' and in_require:
                    in_require = False
                    continue
                elif line.startswith('require ') and not in_require:
                    # Single-line require
                    parts = line.split()
                    if len(parts) >= 3:
                        package_name = parts[1]
                        version = parts[2]
                        deps.append({
                            'package_name': package_name,
                            'version': version,
                            'package_manager': 'go',
                            'language': 'Go',
                            'source_file': str(go_mod),
                            'project_path': str(go_mod.parent),
                        })
                elif in_require and line and not line.startswith('//'):
                    # Multi-line require
                    parts = line.split()
                    if len(parts) >= 2:
                        package_name = parts[0]
                        version = parts[1]
                        deps.append({
                            'package_name': package_name,
                            'version': version,
                            'package_manager': 'go',
                            'language': 'Go',
                            'source_file': str(go_mod),
                            'project_path': str(go_mod.parent),
                        })
    except Exception as e:
        print(f"  Warning: Failed to parse {go_mod}: {e}")
    
    return deps


def _extract_maven_dependencies(pom_xml: Path) -> List[Dict]:
    """Extract Maven dependencies from pom.xml."""
    deps = []
    try:
        tree = ET.parse(pom_xml)
        root = tree.getroot()
        
        # Handle namespaces
        ns = {'maven': 'http://maven.apache.org/POM/4.0.0'}
        
        for dep in root.findall('.//maven:dependency', ns) or root.findall('.//dependency'):
            group = dep.find('maven:groupId', ns) or dep.find('groupId')
            artifact = dep.find('maven:artifactId', ns) or dep.find('artifactId')
            version = dep.find('maven:version', ns) or dep.find('version')
            
            if group is not None and artifact is not None and version is not None:
                package_name = f"{group.text}:{artifact.text}"
                deps.append({
                    'package_name': package_name,
                    'version': version.text,
                    'package_manager': 'maven',
                    'language': 'Java',
                    'source_file': str(pom_xml),
                    'project_path': str(pom_xml.parent),
                })
    except Exception as e:
        print(f"  Warning: Failed to parse {pom_xml}: {e}")
    
    return deps


def extract_dependencies(
    repo_path: Path, 
    experiment_id: str, 
    repo_id: str
) -> Tuple[int, int]:
    """Extract dependencies from repo and persist to database.
    
    Args:
        repo_path: Path to repository root
        experiment_id: Experiment ID for tracking
        repo_id: Repository record ID in DB
    
    Returns:
        (num_extracted, num_inserted)
    """
    print(f"\n[Phase 2] Extracting dependencies from {repo_path}...")
    
    all_dependencies = []
    
    # Find and parse manifest files
    for csproj in repo_path.rglob('*.csproj'):
        all_dependencies.extend(_extract_nuget_dependencies(csproj))
    
    for pkg_json in repo_path.rglob('package.json'):
        all_dependencies.extend(_extract_npm_dependencies(pkg_json))
    
    for req_file in repo_path.rglob('requirements*.txt'):
        all_dependencies.extend(_extract_python_dependencies(req_file))
    
    for go_mod in repo_path.rglob('go.mod'):
        all_dependencies.extend(_extract_go_dependencies(go_mod))
    
    for pom_xml in repo_path.rglob('pom.xml'):
        all_dependencies.extend(_extract_maven_dependencies(pom_xml))
    
    if not all_dependencies:
        print(f"  No dependencies detected")
        return (0, 0)
    
    # Group by project path for logging
    by_project = {}
    for dep in all_dependencies:
        project = dep["project_path"]
        if project not in by_project:
            by_project[project] = []
        by_project[project].append(dep)
    
    print(f"  Detected {len(all_dependencies)} dependencies across {len(by_project)} projects:")
    for project, deps in sorted(by_project.items()):
        print(f"    [{Path(project).relative_to(repo_path)}] {len(deps)} packages")
    
    # Persist to database
    with get_db_connection() as conn:
        inserted = 0
        try:
            for dep in all_dependencies:
                dep_id = f"{experiment_id}:{repo_id}:{dep['package_manager']}:{dep['package_name'].lower()}:{dep['version']}"
                
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dependencies
                    (id, repo_id, experiment_id, project_path, package_name, version,
                     package_manager, language, source_file, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dep_id,
                        repo_id,
                        experiment_id,
                        dep["project_path"],
                        dep["package_name"],
                        dep["version"],
                        dep["package_manager"],
                        dep["language"],
                        dep["source_file"],
                        datetime.utcnow().isoformat(),
                    ),
                )
                inserted += 1
            
            print(f"  ✓ Persisted {inserted} dependencies to database")
        except Exception as e:
            print(f"  ✗ Error persisting dependencies: {e}")
            inserted = 0
            raise
    
    return (len(all_dependencies), inserted)


if __name__ == "__main__":
    # Test invocation
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <repo_path> <experiment_id> <repo_id>")
        sys.exit(1)
    
    repo_path = Path(sys.argv[1])
    experiment_id = sys.argv[2]
    repo_id = sys.argv[3]
    
    num_extracted, num_inserted = extract_dependencies(repo_path, experiment_id, repo_id)
    print(f"\nSummary: {num_extracted} extracted, {num_inserted} inserted")
