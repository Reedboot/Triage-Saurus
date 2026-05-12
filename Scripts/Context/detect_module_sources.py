#!/usr/bin/env python3
"""
detect_module_sources.py

Detect external module sources in Terraform/HCL files.
Extracts module names and sources for user confirmation.

Returns JSON with structure:
{
  "modules": [
    {
      "name": "aks_accounts_api",
      "source": "git::https://dev.azure.com/cbinfrastructure/cbi/_git/terraform-aks//modules/azure",
      "source_file": "terraform/aks.tf",
      "source_line": 1,
      "inferred_type": "aks_module"
    },
    ...
  ],
  "inferred_architectures": {
    "aks_module": ["aks_accounts_api", "aks_event_ingestion", ...],
    "helm": [],
    "serverless": []
  }
}
"""

import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any

# Module source patterns to recognize
MODULE_PATTERNS = {
    "aks_module": {
        "patterns": [
            r"terraform-aks//modules",
            r"cbi/_git/terraform-aks"
        ],
        "label": "Azure Kubernetes Service (AKS) modules"
    },
    "helm": {
        "patterns": [
            r"source.*=.*['\"]helm",
            r"helm_release",
            r"artifacthub\.io"
        ],
        "label": "Helm charts"
    },
    "serverless": {
        "patterns": [
            r"aws.*lambda",
            r"google.*cloud.*function",
            r"azure.*function"
        ],
        "label": "Serverless/Function modules"
    },
    "kubernetes": {
        "patterns": [
            r"kubernetes-provider",
            r"helm-provider"
        ],
        "label": "Kubernetes provider modules"
    },
    "remote_git": {
        "patterns": [
            r"source.*=.*['\"]git::"
        ],
        "label": "External Git repositories"
    }
}


def detect_modules(repo_path: str, max_files: int = 500) -> Dict[str, Any]:
    """Detect all module sources in Terraform files within a repository.
    
    Args:
        repo_path: Path to repository root
        max_files: Maximum number of .tf files to scan (safety limit)
    
    Returns:
        Dict with 'modules' list and 'inferred_architectures' mapping
    """
    repo = Path(repo_path)
    if not repo.exists():
        return {"error": f"Path does not exist: {repo_path}", "modules": [], "inferred_architectures": {}}
    
    modules = []
    inferred_architectures = {arch: [] for arch in MODULE_PATTERNS.keys()}
    
    # Find all .tf files - look in common locations first
    tf_files = []
    common_paths = [repo / "terraform", repo / "terraform-aks", repo]
    
    for search_path in common_paths:
        if search_path.exists():
            try:
                for tf_file in search_path.glob("*.tf"):
                    if len(tf_files) < max_files:
                        tf_files.append(tf_file)
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
                    source_line_offset = 0
                    
                    for i in range(current_line, min(current_line + 10, len(lines))):
                        source_match = source_pattern.search(lines[i])
                        if source_match:
                            source = source_match.group(1)
                            source_line_offset = i - current_line
                            break
                    
                    if source:
                        # Infer architecture type from source
                        inferred_type = "unknown"
                        for arch_type, arch_def in MODULE_PATTERNS.items():
                            for pattern in arch_def["patterns"]:
                                if re.search(pattern, source, re.IGNORECASE):
                                    inferred_type = arch_type
                                    break
                            if inferred_type != "unknown":
                                break
                        
                        # Record module
                        module_info = {
                            "name": module_name,
                            "source": source,
                            "source_file": str(tf_file.relative_to(repo)),
                            "source_line": current_line + 1,
                            "inferred_type": inferred_type
                        }
                        modules.append(module_info)
                        
                        # Add to inferred architectures
                        if inferred_type != "unknown":
                            inferred_architectures[inferred_type].append(module_name)
                
                current_line += 1
        
        except Exception as e:
            print(f"Warning: Error reading {tf_file}: {e}")
    
    return {
        "modules": modules,
        "inferred_architectures": {k: v for k, v in inferred_architectures.items() if v}
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
        label = MODULE_PATTERNS.get(mtype, {}).get("label", mtype)
        lines.append(f"\n{label} ({len(mods)}):")
        
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
