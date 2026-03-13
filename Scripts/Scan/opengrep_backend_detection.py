#!/usr/bin/env python3
"""opengrep_backend_detection.py - Extract backend routing using opengrep rules."""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


def run_opengrep_rules(repo_path: Path, rules_dir: Path) -> Dict:
    """Run opengrep on repo with all backend detection rules, return parsed findings."""
    if not repo_path.exists():
        return {"backends": [], "deployments": [], "databases": []}
    
    opengrep_path = Path.home() / ".local/bin/opengrep"
    if not opengrep_path.exists():
        return {"backends": [], "deployments": [], "databases": []}
    
    # Run opengrep with backend detection rules
    try:
        result = subprocess.run(
            [str(opengrep_path), "scan", "--config", str(rules_dir), "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120  # 2 minute timeout
        )
        
        if result.returncode != 0:
            return {"backends": [], "deployments": [], "databases": []}
        
        # Parse JSON output (last line after progress output)
        lines = result.stdout.strip().split("\n")
        json_line = lines[-1]
        data = json.loads(json_line)
        
        return _parse_opengrep_findings(data)
        
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return {"backends": [], "deployments": [], "databases": []}


def _parse_opengrep_findings(data: Dict) -> Dict:
    """Parse opengrep JSON findings into structured backend info."""
    backends = []
    deployments = []
    databases = []
    
    for finding in data.get("results", []):
        check_id = finding.get("check_id", "")
        metavars = finding.get("extra", {}).get("metavars", {})
        metadata = finding.get("extra", {}).get("metadata", {})
        
        # APIM/API Gateway backend routing
        if "backend-routing" in check_id or "backend-integration" in check_id:
            service_url = metavars.get("$SERVICE_URL", {}).get("abstract_content", "")
            integration_uri = metavars.get("$URI", {}).get("abstract_content", "")
            api_name = metavars.get("$API_NAME", {}).get("abstract_content", "")
            
            url = service_url or integration_uri
            if url:
                # Clean up the URL (remove quotes and template syntax artifacts)
                url = url.strip('"').replace('""', '"')
                backends.append({
                    "api_name": api_name.strip('"'),
                    "backend_url": url,
                    "provider": _detect_provider(check_id)
                })
        
        # Kubernetes/EKS/AKS/GKE deployments
        elif "backend-deployment" in check_id or "kubernetes" in check_id or "eks" in check_id or "aks" in check_id or "gke" in check_id:
            module_name = metavars.get("$MODULE_NAME", {}).get("abstract_content", "") or \
                         metavars.get("$RELEASE_NAME", {}).get("abstract_content", "") or \
                         metavars.get("$DEPLOYMENT_NAME", {}).get("abstract_content", "")
            
            # Try to find app_name in various metavar names
            app_name = None
            for key in ["$APP_NAME", "$NAME"]:
                if key in metavars:
                    app_name = metavars[key].get("abstract_content", "")
                    break
            
            namespace = metavars.get("$NAMESPACE", {}).get("abstract_content", "")
            
            if app_name:
                deployments.append({
                    "module_name": module_name.strip('"'),
                    "app_name": app_name.strip('"'),
                    "namespace": namespace.strip('"') if namespace else None,
                    "provider": _detect_provider(check_id),
                    "type": "helm" if "helm" in check_id.lower() else "kubernetes"
                })
        
        # Database connections (if we add rules for these)
        elif "database" in check_id.lower() or "sql" in check_id.lower():
            db_name = metavars.get("$DATABASE", {}).get("abstract_content", "") or \
                     metavars.get("$DB_NAME", {}).get("abstract_content", "")
            server = metavars.get("$SERVER", {}).get("abstract_content", "")
            
            if db_name:
                databases.append({
                    "database": db_name.strip('"'),
                    "server": server.strip('"') if server else "unknown",
                    "provider": _detect_provider(check_id)
                })
    
    return {
        "backends": backends,
        "deployments": deployments,
        "databases": databases
    }


def _detect_provider(check_id: str) -> str:
    """Detect cloud provider from rule check_id."""
    check_id_lower = check_id.lower()
    if "azure" in check_id_lower or "azurerm" in check_id_lower:
        return "azure"
    elif "aws" in check_id_lower:
        return "aws"
    elif "gcp" in check_id_lower or "google" in check_id_lower:
        return "gcp"
    return "unknown"


def extract_backend_for_api(repo_path: Path, api_name: str, rules_dir: Optional[Path] = None) -> Optional[str]:
    """Extract backend URL for a specific API using opengrep."""
    if rules_dir is None:
        rules_dir = Path(__file__).resolve().parents[2] / "Rules/Detection"
    
    findings = run_opengrep_rules(repo_path, rules_dir)
    
    for backend in findings.get("backends", []):
        if backend["api_name"] == api_name:
            return backend["backend_url"]
    
    return None


def extract_kubernetes_deployments(repo_path: Path, rules_dir: Optional[Path] = None) -> List[Dict]:
    """Extract Kubernetes deployments using opengrep."""
    if rules_dir is None:
        rules_dir = Path(__file__).resolve().parents[2] / "Rules/Detection"
    
    findings = run_opengrep_rules(repo_path, rules_dir)
    return findings.get("deployments", [])


def extract_database_connections(repo_path: Path, rules_dir: Optional[Path] = None) -> List[Dict]:
    """Extract database connections using opengrep."""
    if rules_dir is None:
        rules_dir = Path(__file__).resolve().parents[2] / "Rules/Detection"
    
    findings = run_opengrep_rules(repo_path, rules_dir)
    return findings.get("databases", [])
