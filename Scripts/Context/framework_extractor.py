"""
Framework and tech stack extraction for repository context.
Extracts version numbers, framework names, and IaC types from various config files.
"""
import json
import re
from pathlib import Path
from typing import Optional, Tuple


def detect_dotnet_version(repo_path: Path) -> Optional[str]:
    """
    Extract .NET version from global.json or *.csproj files.
    Returns: ".NET 10", ".NET 8", etc. or None.
    """
    # Priority 1: global.json SDK version
    global_json = repo_path / "global.json"
    if global_json.exists():
        try:
            data = json.loads(global_json.read_text())
            sdk_version = data.get("sdk", {}).get("version")
            if sdk_version:
                major = sdk_version.split(".")[0]
                return f".NET {major}"
        except Exception:
            pass

    # Priority 2: *.csproj TargetFramework (net10.0 → .NET 10)
    for csproj in repo_path.rglob("*.csproj"):
        try:
            content = csproj.read_text()
            match = re.search(r"<TargetFramework>net(\d+\.\d+)", content)
            if match:
                major = match.group(1).split(".")[0]
                return f".NET {major}"
        except Exception:
            pass

    return None


def detect_framework_name(repo_path: Path) -> Optional[str]:
    """
    Detect application framework type (ASP.NET Core, Console, etc).
    Returns: "ASP.NET Core", "ASP.NET Framework", "Console App", etc. or None.
    """
    for csproj in repo_path.rglob("*.csproj"):
        try:
            content = csproj.read_text()
            if "Microsoft.NET.Sdk.Web" in content:
                return "ASP.NET Core"
            elif "AspNetCore" in content or "Kestrel" in content:
                return "ASP.NET Core"
            elif "Microsoft.NET.Sdk.WindowsDesktop" in content:
                return "Windows Desktop"
            elif "WindowsFormsApp" in content or "WinForms" in content:
                return "Windows Forms"
            elif "WPFApp" in content or "PresentationFramework" in content:
                return "WPF"
        except Exception:
            pass

    # Check Dockerfile for hints
    for dockerfile in repo_path.rglob("Dockerfile*"):
        try:
            content = dockerfile.read_text()
            if "aspnet" in content.lower():
                return "ASP.NET Core"
            elif "mcr.microsoft.com" in content.lower():
                return ".NET"
        except Exception:
            pass

    return ".NET"  # Default if .NET is detected but type unknown


def detect_iac_type(repo_path: Path) -> Optional[str]:
    """
    Detect Infrastructure-as-Code type.
    Returns: "Terraform", "ARM Templates", "Bicep", "CloudFormation", etc. or None.
    """
    # Terraform
    if list(repo_path.rglob("*.tf")):
        return "Terraform"

    # Bicep
    if list(repo_path.rglob("*.bicep")):
        return "Bicep"

    # ARM Templates (check for $schema)
    for json_file in repo_path.rglob("*.json"):
        try:
            content = json_file.read_text()
            if '"$schema"' in content and ("arm" in content.lower() or "template" in content.lower()):
                return "ARM Templates"
        except Exception:
            pass

    # CloudFormation
    if list(repo_path.rglob("*.yaml")) or list(repo_path.rglob("*.yml")):
        for yaml_file in list(repo_path.rglob("*.yaml")) + list(repo_path.rglob("*.yml")):
            try:
                content = yaml_file.read_text()
                if "AWSTemplateFormatVersion" in content or "AWSCertificateManagerCertificate" in content:
                    return "CloudFormation"
            except Exception:
                pass

    # Kubernetes manifests
    if list(repo_path.rglob("*.yaml")) or list(repo_path.rglob("*.yml")):
        for yaml_file in list(repo_path.rglob("*.yaml")) + list(repo_path.rglob("*.yml")):
            try:
                content = yaml_file.read_text()
                if "kind:" in content and ("Deployment" in content or "Service" in content or "Pod" in content):
                    return "Kubernetes"
            except Exception:
                pass

    return None


def detect_python_version(repo_path: Path) -> Optional[str]:
    """Extract Python version from pyproject.toml or setup.py."""
    # pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            match = re.search(r'python\s*=\s*["\']([0-9.]+)', content, re.IGNORECASE)
            if match:
                return f"Python {match.group(1)}"
        except Exception:
            pass

    # setup.py
    setup_py = repo_path / "setup.py"
    if setup_py.exists():
        try:
            content = setup_py.read_text()
            match = re.search(r'python_requires\s*=\s*["\']>=?\s*([0-9.]+)', content)
            if match:
                return f"Python {match.group(1)}"
        except Exception:
            pass

    return None


def detect_node_version(repo_path: Path) -> Optional[str]:
    """Extract Node.js version from .nvmrc or package.json."""
    # .nvmrc
    nvmrc = repo_path / ".nvmrc"
    if nvmrc.exists():
        try:
            version = nvmrc.read_text().strip()
            if version.startswith("v"):
                version = version[1:]
            return f"Node.js {version}"
        except Exception:
            pass

    # .node-version
    node_version = repo_path / ".node-version"
    if node_version.exists():
        try:
            version = node_version.read_text().strip()
            return f"Node.js {version}"
        except Exception:
            pass

    # package.json "engines"
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text())
            node_req = data.get("engines", {}).get("node", "")
            if node_req:
                # Extract major version from ">=16.0.0" → "16"
                match = re.search(r"(\d+)", node_req)
                if match:
                    return f"Node.js {match.group(1)}"
        except Exception:
            pass

    return None


def detect_go_version(repo_path: Path) -> Optional[str]:
    """Extract Go version from go.mod."""
    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        try:
            content = go_mod.read_text()
            match = re.search(r"^go\s+(\d+\.\d+)", content, re.MULTILINE)
            if match:
                return f"Go {match.group(1)}"
        except Exception:
            pass

    return None


def detect_tech_stack(repo_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Detect the primary language/framework version, framework name, and IaC type.
    
    Returns:
        (framework_version, framework_name, iac_type)
        E.g., (".NET 10", "ASP.NET Core", "Terraform")
    """
    framework_version = None
    framework_name = None
    iac_type = detect_iac_type(repo_path)

    # Try to detect language-specific version
    framework_version = (
        detect_dotnet_version(repo_path)
        or detect_python_version(repo_path)
        or detect_node_version(repo_path)
        or detect_go_version(repo_path)
    )

    # Detect framework name if we have a .NET version
    if framework_version and ".NET" in framework_version:
        framework_name = detect_framework_name(repo_path)

    return framework_version, framework_name, iac_type
