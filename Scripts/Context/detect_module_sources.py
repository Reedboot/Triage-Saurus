#!/usr/bin/env python3
"""
detect_module_sources.py

Detect external module sources in Terraform/HCL files.
Extracts module names and sources that use REMOTE sources (git::, http://, etc).
Skips local module references.

Returns JSON with structure:
{
  "modules": [
    {
      "name": "my_module",
      "source": "git::https://github.com/myorg/terraform-module//path",
      "source_file": "terraform/main.tf",
      "source_line": 1,
      "inferred_type": "external_git"
    },
    ...
  ]
}
"""

import re
import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Any

# Simple classification for module types based on source
def classify_module_type(source: str) -> str:
    """Classify module type based on source URL."""
    source_lower = source.lower()
    
    # Check for remote sources
    if 'git::' in source_lower or source_lower.startswith('git@'):
        return 'external_git'
    elif 'http://' in source_lower or 'https://' in source_lower:
        return 'external_http'
    elif source_lower.startswith('registry.terraform.io'):
        return 'terraform_registry'
    else:
        # Local paths like "./modules/vpc" or "../other-module"
        return 'local'


def detect_modules(repo_path: str, max_files: int = 500) -> Dict[str, Any]:
    """Detect all EXTERNAL module sources in Terraform files within a repository.
    
    Looks for ANY module "name" { source = "..." } block where the source is
    a remote reference (git::, http://, https://, etc.), not local paths.
    
    Args:
        repo_path: Path to repository root
        max_files: Maximum number of .tf files to scan (safety limit)
    
    Returns:
        Dict with 'modules' list (external modules only)
    """
    repo = Path(repo_path)
    if not repo.exists():
        return {"error": f"Path does not exist: {repo_path}", "modules": []}
    
    modules = []
    
    # Fast by design: only parse *.tf files with a file cap.
    # Recursively search all .tf files in the repo
    tf_files = []
    seen_files = set()
    
    try:
        # Recursively find all .tf files
        for tf_file in repo.glob("**/*.tf"):
            file_key = tf_file.resolve()
            if file_key not in seen_files and len(tf_files) < max_files:
                tf_files.append(tf_file)
                seen_files.add(file_key)
    except Exception:
        pass
    
    for tf_file in tf_files:
        try:
            content = tf_file.read_text(encoding='utf-8')
            lines = content.split('\n')
            
            # Find module blocks: module "name" { ... }
            module_pattern = re.compile(r'module\s+"([^"]+)"\s*\{')
            source_pattern = re.compile(r'source\s*=\s*["\']([^"\']+)["\']')
            
            current_line = 0
            while current_line < len(lines):
                line = lines[current_line]
                match = module_pattern.search(line)
                
                if match:
                    module_name = match.group(1)
                    # Look for source in following lines (usually within next 10 lines)
                    source = None
                    
                    for i in range(current_line, min(current_line + 10, len(lines))):
                        source_match = source_pattern.search(lines[i])
                        if source_match:
                            source = source_match.group(1)
                            break
                    
                    if source:
                        # Only record EXTERNAL modules (remote sources), skip local paths
                        module_type = classify_module_type(source)
                        
                        if module_type != 'local':
                            # Record external module
                            module_info = {
                                "name": module_name,
                                "source": source,
                                "source_file": str(tf_file.relative_to(repo)),
                                "source_line": current_line + 1,
                                "inferred_type": module_type
                            }
                            modules.append(module_info)
                
                current_line += 1
        
        except Exception as e:
            print(f"Warning: Error reading {tf_file}: {e}", file=sys.stderr)
    
    return {
        "modules": modules
    }


def format_output(detection: Dict[str, Any]) -> str:
    """Format detection results as human-readable text."""
    if "error" in detection:
        return f"Error: {detection['error']}"
    
    if not detection["modules"]:
        return "No external modules detected."
    
    lines = [f"Found {len(detection['modules'])} external module(s):\n"]
    
    # Group by inferred type
    by_type = {}
    for module in detection["modules"]:
        mtype = module["inferred_type"]
        if mtype not in by_type:
            by_type[mtype] = []
        by_type[mtype].append(module)
    
    for mtype, mods in sorted(by_type.items()):
        lines.append(f"\n{mtype.replace('_', ' ').title()} ({len(mods)}):")
        
        for mod in mods:
            lines.append(f"  • {mod['name']}")
            lines.append(f"    Source: {mod['source'][:80]}...")
            lines.append(f"    File: {mod['source_file']}:{mod['source_line']}")
    
    lines.append("\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Detect external module sources in Terraform")
    parser.add_argument("repo_path", help="Path to repository")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--json-file", help="Write JSON output to file")
    
    args = parser.parse_args()
    
    result = detect_modules(args.repo_path)
    
    if args.json or args.json_file:
        json_output = json.dumps(result, indent=2)
        if args.json_file:
            Path(args.json_file).write_text(json_output)
            print(f"Wrote results to {args.json_file}")
        else:
            print(json_output)
    else:
        print(format_output(result))
        if result["modules"]:
            print("Use --json to see structured output")


if __name__ == "__main__":
    main()
