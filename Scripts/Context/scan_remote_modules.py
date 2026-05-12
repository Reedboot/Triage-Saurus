#!/usr/bin/env python3
"""
scan_remote_modules.py

Optionally scan remote module source code to understand what resources they create.
Fetches remote modules (with user permission) and analyzes them.
"""

import re
import json
import tempfile
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

# Resource type patterns to detect what modules create
RESOURCE_PATTERNS = {
    "azurerm_kubernetes_cluster": r'resource\s+"azurerm_kubernetes_cluster"',
    "kubernetes_deployment": r'resource\s+"kubernetes_deployment"',
    "helm_release": r'resource\s+"helm_release"',
    "azurerm_app_service": r'resource\s+"azurerm_app_service"',
    "aws_lambda_function": r'resource\s+"aws_lambda_function"',
    "google_cloud_run_service": r'resource\s+"google_cloud_run_service"',
    "azurerm_container_registry": r'resource\s+"azurerm_container_registry"',
    "aws_eks_cluster": r'resource\s+"aws_eks_cluster"',
    "google_container_cluster": r'resource\s+"google_container_cluster"',
}


def extract_git_url(source: str) -> Optional[str]:
    """Extract git URL from Terraform module source.
    
    Examples:
        git::https://github.com/user/repo//modules/azure
        -> https://github.com/user/repo
    """
    if not source.startswith("git::"):
        return None
    
    # Remove git:: prefix
    url_part = source[5:]
    
    # Remove module path (after //)
    if "//" in url_part:
        url_part = url_part.split("//")[0]
    
    return url_part


def clone_git_repo(git_url: str, temp_dir: str, timeout: int = 30) -> Optional[Path]:
    """Clone a git repository.
    
    Returns path to cloned repo or None if failed.
    """
    try:
        repo_path = Path(temp_dir) / "repo"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", git_url, str(repo_path)],
            timeout=timeout,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return repo_path
        else:
            print(f"  ⚠ Failed to clone {git_url}: {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  ⚠ Clone timeout for {git_url}")
        return None
    except Exception as e:
        print(f"  ⚠ Error cloning {git_url}: {e}")
        return None


def analyze_module(module_path: Path) -> Dict[str, Any]:
    """Analyze a module's Terraform code to identify what resources it creates.
    
    Returns dict with 'resource_types' list and 'metadata'.
    """
    resource_types = {}
    
    # Find all .tf files in module
    try:
        for tf_file in module_path.glob("**/*.tf"):
            content = tf_file.read_text(encoding='utf-8', errors='ignore')
            
            for resource_type, pattern in RESOURCE_PATTERNS.items():
                matches = len(re.findall(pattern, content))
                if matches > 0:
                    if resource_type not in resource_types:
                        resource_types[resource_type] = 0
                    resource_types[resource_type] += matches
    except Exception as e:
        print(f"  ⚠ Error analyzing module: {e}")
    
    return {
        "resource_types": resource_types,
        "resource_count": sum(resource_types.values()),
        "files_analyzed": len(list(module_path.glob("**/*.tf")))
    }


def scan_remote_module(module_source: str, module_name: str = None) -> Dict[str, Any]:
    """Scan a remote module to understand what it creates.
    
    Args:
        module_source: Terraform module source (e.g., git::https://...)
        module_name: Friendly name for the module
    
    Returns:
        Dict with 'source', 'git_url', 'resources', 'status'
    """
    result = {
        "source": module_source,
        "module_name": module_name or "unknown",
        "git_url": None,
        "resources": {},
        "status": "unknown",
        "error": None
    }
    
    # Extract git URL
    git_url = extract_git_url(module_source)
    if not git_url:
        result["status"] = "not_git"
        result["error"] = "Module source is not a git URL"
        return result
    
    result["git_url"] = git_url
    
    # Clone and analyze
    print(f"  Cloning {git_url}...")
    with tempfile.TemporaryDirectory() as temp_dir:
        repo_path = clone_git_repo(git_url, temp_dir)
        
        if not repo_path:
            result["status"] = "clone_failed"
            return result
        
        # Extract module path if specified (e.g., //modules/azure)
        module_subpath = None
        if "//" in module_source:
            module_subpath = module_source.split("//", 1)[1]
        
        # Search for Terraform files
        scan_paths = [repo_path]
        if module_subpath:
            candidate = repo_path / module_subpath
            if candidate.exists():
                scan_paths = [candidate]
        
        print(f"  Analyzing module code...")
        analysis = None
        for scan_path in scan_paths:
            if list(scan_path.glob("**/*.tf")):
                analysis = analyze_module(scan_path)
                break
        
        if analysis:
            result["resources"] = analysis["resource_types"]
            result["files_analyzed"] = analysis["files_analyzed"]
            result["resource_count"] = analysis["resource_count"]
            result["status"] = "success"
        else:
            result["status"] = "no_terraform"
            result["error"] = "No Terraform files found in module"
    
    return result


def format_scan_results(scans: List[Dict[str, Any]]) -> str:
    """Format scan results as human-readable text."""
    lines = [f"Scanned {len(scans)} module(s):\n"]
    
    for scan in scans:
        lines.append(f"\n{scan['module_name']}")
        lines.append(f"  Source: {scan['source'][:70]}...")
        
        if scan["status"] == "success":
            lines.append(f"  ✓ Cloned and analyzed")
            if scan["resources"]:
                lines.append(f"  Resources created:")
                for rtype, count in sorted(scan["resources"].items(), key=lambda x: -x[1]):
                    lines.append(f"    - {rtype}: {count}")
            else:
                lines.append(f"  No recognized resources found")
        else:
            lines.append(f"  ✗ {scan['status']}: {scan['error']}")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Scan remote Terraform modules")
    parser.add_argument("modules", nargs="+", help="Module sources to scan (git::https://...)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--json-file", help="Write JSON output to file")
    
    args = parser.parse_args()
    
    print(f"Scanning {len(args.modules)} module source(s)...")
    results = []
    
    for module_source in args.modules:
        # Extract friendly name from source
        module_name = module_source.split("/")[-1].replace("//", "_")
        result = scan_remote_module(module_source, module_name)
        results.append(result)
    
    if args.json or args.json_file:
        json_output = json.dumps(results, indent=2)
        if args.json_file:
            Path(args.json_file).write_text(json_output)
            print(f"\nWrote results to {args.json_file}")
        else:
            print(json_output)
    else:
        print("\n" + format_scan_results(results))


if __name__ == "__main__":
    main()
