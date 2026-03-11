#!/usr/bin/env python3
"""
Analyze Terraform module dependencies and classify them.

Usage: python3 Scripts/analyze_terraform_modules.py <repo-path>

Scans *.tf files for module blocks and classifies each source as:
- Local path
- Known local repo (requires Output/Knowledge/Repos.md)
- Unknown local repo
- Remote git URL
- Terraform Registry module
"""

import os
import re
import sys
from pathlib import Path

def get_known_repo_root():
    """Read the known repo root from Output/Knowledge/Repos.md if it exists."""
    repos_md = Path("Output/Knowledge/Repos.md")
    if not repos_md.exists():
        return None
    
    content = repos_md.read_text()
    # Look for "Repo root directory:" line
    match = re.search(r'\*\*Repo root directory:\*\*\s*`([^`]+)`', content)
    if match:
        return match.group(1)
    return None

def classify_module_source(source, repo_path, known_repo_root):
    """Classify a Terraform module source."""
    # Registry module: namespace/name/provider
    if re.match(r'^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$', source):
        return "registry", f"https://registry.terraform.io/modules/{source}"
    
    # Git URLs
    if source.startswith(('git::', 'git@', 'https://github.com', 'https://gitlab.com', 'https://dev.azure.com')):
        return "remote_git", source
    
    # Local path (relative or absolute)
    if source.startswith(('./', '../', '/')):
        abs_path = Path(repo_path) / source if not source.startswith('/') else Path(source)
        abs_path = abs_path.resolve()
        return "local_path", str(abs_path)
    
    # Possible local repo reference (e.g., "../terraform-modules")
    if known_repo_root and ('/' in source or '\\' in source):
        # Check if it could be a sibling repo
        potential_repo = Path(repo_path).parent / source
        if potential_repo.exists():
            return "known_local_repo", str(potential_repo.resolve())
        return "unknown_local_repo", source
    
    return "unknown", source

def extract_modules(repo_path):
    """Extract all Terraform module blocks from *.tf files."""
    modules = []
    
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden directories and .terraform
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '.terraform']
        
        for file in files:
            if file.endswith('.tf'):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    # Find module blocks with source
                    # Pattern: module "name" { ... source = "..." ... }
                    pattern = r'module\s+"([^"]+)"\s*\{[^}]*?source\s*=\s*"([^"]+)"'
                    matches = re.finditer(pattern, content, re.DOTALL)
                    
                    for match in matches:
                        module_name = match.group(1)
                        source = match.group(2)
                        rel_path = os.path.relpath(file_path, repo_path)
                        modules.append({
                            'name': module_name,
                            'source': source,
                            'file': rel_path
                        })
                except Exception as e:
                    print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
    
    return modules

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 Scripts/analyze_terraform_modules.py <repo-path>", file=sys.stderr)
        sys.exit(1)
    
    repo_path = sys.argv[1]
    if not os.path.isdir(repo_path):
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    known_repo_root = get_known_repo_root()
    
    print(f"== Terraform Module Analysis ==")
    print(f"Repo: {repo_path}")
    if known_repo_root:
        print(f"Known repo root: {known_repo_root}")
    print()
    
    modules = extract_modules(repo_path)
    
    if not modules:
        print("No Terraform modules found")
        return
    
    print(f"Found {len(modules)} module references\n")
    
    # Classify and group
    by_type = {
        'local_path': [],
        'known_local_repo': [],
        'unknown_local_repo': [],
        'remote_git': [],
        'registry': [],
        'unknown': []
    }
    
    for mod in modules:
        mod_type, classified_source = classify_module_source(
            mod['source'], 
            repo_path, 
            known_repo_root
        )
        by_type[mod_type].append({
            'name': mod['name'],
            'source': mod['source'],
            'classified': classified_source,
            'file': mod['file']
        })
    
    # Print by category
    if by_type['local_path']:
        print(f"== Local Path Modules ({len(by_type['local_path'])}) ==")
        for mod in by_type['local_path']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    Resolved: {mod['classified']}")
            print(f"    File: {mod['file']}")
            print()
    
    if by_type['known_local_repo']:
        print(f"== Known Local Repo Modules ({len(by_type['known_local_repo'])}) ==")
        for mod in by_type['known_local_repo']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    Resolved: {mod['classified']}")
            print(f"    File: {mod['file']}")
            print()
    
    if by_type['unknown_local_repo']:
        print(f"== Unknown Local Repo Modules ({len(by_type['unknown_local_repo'])}) ==")
        for mod in by_type['unknown_local_repo']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    File: {mod['file']}")
            print()
    
    if by_type['remote_git']:
        print(f"== Remote Git Modules ({len(by_type['remote_git'])}) ==")
        for mod in by_type['remote_git']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    File: {mod['file']}")
            print()
    
    if by_type['registry']:
        print(f"== Terraform Registry Modules ({len(by_type['registry'])}) ==")
        for mod in by_type['registry']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    URL: {mod['classified']}")
            print(f"    File: {mod['file']}")
            print()
    
    if by_type['unknown']:
        print(f"== Unknown Module Types ({len(by_type['unknown'])}) ==")
        for mod in by_type['unknown']:
            print(f"  {mod['name']}")
            print(f"    Source: {mod['source']}")
            print(f"    File: {mod['file']}")
            print()

if __name__ == '__main__':
    main()
