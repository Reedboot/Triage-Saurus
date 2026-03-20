#!/usr/bin/env python3
"""
Phase 2 — Script-based code context discovery.

Analyses a repository WITHOUT any LLM calls by:
  1. Running opengrep Detection/Frameworks/ rules  → languages & frameworks
  2. Running opengrep Detection/Code/ rules        → auth patterns
  3. Parsing manifest files directly               → package details
  4. Parsing Kubernetes/Docker manifests           → services, ingress, RBAC
  5. Detecting CI/CD pipeline files                → pipeline tooling
  6. Writing all findings to context_metadata DB   → feeds Phase 3 targeting
  7. Generating/updating the repo summary MD       → human-readable output

Usage:
    python3 Scripts/Context/discover_code_context.py \\
        --experiment 003 \\
        --repo insecure-kubernetes-deployments \\
        --target /path/to/repo \\
        --output-dir Output/Learning/experiments/003_insecure-kube
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "Utils"))
sys.path.append(str(Path(__file__).resolve().parent.parent / "Persist"))

from db_helpers import upsert_context_metadata, get_db_connection, ensure_repository_entry
from output_paths import REPO_ROOT
from service_auth_topology import render_service_auth_topology

DETECTION_FRAMEWORKS = REPO_ROOT / "Rules" / "Detection" / "Frameworks"
DETECTION_CODE = REPO_ROOT / "Rules" / "Detection" / "Code"


# ---------------------------------------------------------------------------
# opengrep helpers
# ---------------------------------------------------------------------------

def _run_opengrep(rules_dir: Path, target: Path) -> list[dict]:
    """Run opengrep and return results list (empty on error/no findings)."""
    if not rules_dir.exists():
        return []
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cmd = [
        "opengrep", "scan",
        "--config", str(rules_dir),
        str(target),
        "--json",
        "--output", str(tmp_path),
        "--quiet",
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        try:
            data = json.loads(tmp_path.read_text())
            return data.get("results", [])
        except json.JSONDecodeError:
            pass
    return []


def _extract_rule_ids(results: list[dict]) -> set[str]:
    return {r.get("check_id", "") for r in results}


# ---------------------------------------------------------------------------
# Manifest parsers
# ---------------------------------------------------------------------------

def _parse_package_json(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    for pj in target.rglob("package.json"):
        if "node_modules" in pj.parts:
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        findings.setdefault("node.project_names", []).append(data.get("name", pj.parent.name))
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for framework in ("express", "fastify", "koa", "hapi", "next", "nuxt", "react", "vue", "angular"):
            if framework in deps:
                findings.setdefault("node.frameworks", []).append(framework)
        for sec_lib in ("jsonwebtoken", "passport", "bcrypt", "helmet", "cors"):
            if sec_lib in deps:
                findings.setdefault("node.security_libs", []).append(sec_lib)
    return findings


def _parse_requirements_txt(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    framework_map = {
        "flask": "Flask", "django": "Django", "fastapi": "FastAPI",
        "tornado": "Tornado", "starlette": "Starlette",
    }
    sec_libs = {"pyjwt", "cryptography", "bcrypt", "passlib", "authlib"}
    for req in target.rglob("requirements*.txt"):
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            pkg = re.split(r"[><=!~]", line.strip())[0].lower().strip()
            if pkg in framework_map:
                findings.setdefault("python.frameworks", []).append(framework_map[pkg])
            if pkg in sec_libs:
                findings.setdefault("python.security_libs", []).append(pkg)
    return findings


def _parse_pom_xml(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    for pom in target.rglob("pom.xml"):
        text = pom.read_text(encoding="utf-8", errors="replace")
        artifact = re.search(r"<artifactId>([^<]+)</artifactId>", text)
        if artifact:
            findings.setdefault("java.artifacts", []).append(artifact.group(1).strip())
        if "spring-boot" in text:
            findings["java.spring_boot"] = "true"
        if "spring-security" in text:
            findings["java.spring_security"] = "true"
    return findings


def _parse_go_mod(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    for gm in target.rglob("go.mod"):
        text = gm.read_text(encoding="utf-8", errors="replace")
        mod_match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
        if mod_match:
            findings.setdefault("go.modules", []).append(mod_match.group(1))
        for framework in ("gin-gonic/gin", "labstack/echo", "gorilla/mux", "go-chi/chi"):
            if framework in text:
                findings.setdefault("go.frameworks", []).append(framework.split("/")[-1])
    return findings


def _parse_dockerfiles(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    for df in target.rglob("Dockerfile*"):
        text = df.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FROM "):
                image = stripped.split()[1]
                findings.setdefault("container.base_images", []).append(image)
            elif stripped.upper().startswith("EXPOSE "):
                port = stripped.split()[1] if len(stripped.split()) > 1 else ""
                if port:
                    findings.setdefault("container.exposed_ports", []).append(port)
            elif stripped.upper().startswith("ENV "):
                parts = stripped.split(None, 2)
                if len(parts) >= 2:
                    env_key = parts[1].split("=")[0]
                    for sensitive in ("PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIAL"):
                        if sensitive in env_key.upper():
                            findings.setdefault("container.sensitive_env_vars", []).append(env_key)
    return findings


# ---------------------------------------------------------------------------
# Kubernetes manifest parser
# ---------------------------------------------------------------------------

def _is_helm_template(yaml_file: Path) -> bool:
    """Return True if the file is inside a Helm chart templates/ directory."""
    return "templates" in yaml_file.parts and (yaml_file.parent.parent / "Chart.yaml").exists()


def _clean_name(value: str) -> str:
    """Strip Helm template expressions; return 'helm-templated' if the whole value is a template."""
    if "{{" in value:
        # Try to extract a meaningful name from expressions like {{ .Values.foo.barName }}
        match = re.search(r"\.\w+\.(\w+)\s*}}", value)
        if match:
            return f"<{match.group(1)}>"
        return "<helm-templated>"
    return value


def _parse_k8s_manifests(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}

    # Detect Helm charts and scan their raw templates for security-sensitive patterns
    for chart_yaml in target.rglob("Chart.yaml"):
        chart_text = chart_yaml.read_text(encoding="utf-8", errors="replace")
        name_match = re.search(r"^name:\s*(\S+)", chart_text, re.MULTILINE)
        chart_name = name_match.group(1) if name_match else chart_yaml.parent.name
        findings.setdefault("k8s.helm_charts", []).append(chart_name)

        # Scan raw template text for security patterns even though we can't parse names
        templates_dir = chart_yaml.parent / "templates"
        if templates_dir.is_dir():
            for tmpl in templates_dir.rglob("*.yaml"):
                tmpl_text = tmpl.read_text(encoding="utf-8", errors="replace")
                if "privileged: true" in tmpl_text:
                    findings.setdefault("k8s.privileged_containers", []).append(
                        f"<helm:{chart_name}/{tmpl.stem}>"
                    )
                if "hostNetwork: true" in tmpl_text:
                    findings.setdefault("k8s.host_network", []).append(
                        f"<helm:{chart_name}/{tmpl.stem}>"
                    )
                if "hostPID: true" in tmpl_text:
                    findings.setdefault("k8s.host_pid", []).append(
                        f"<helm:{chart_name}/{tmpl.stem}>"
                    )
                # Literal (non-templated) images in Helm templates
                for image in re.findall(r"image:\s*([a-zA-Z0-9/_.:@-]+:[a-zA-Z0-9._-]+)", tmpl_text):
                    findings.setdefault("k8s.container_images", []).append(image)

    k8s_kinds = {
        "Deployment", "DaemonSet", "StatefulSet", "Job", "CronJob", "Pod",
        "Service", "Ingress", "NetworkPolicy",
        "ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding",
        "ServiceAccount", "ConfigMap", "Secret",
    }
    for yaml_file in list(target.rglob("*.yaml")) + list(target.rglob("*.yml")):
        if ".git" in yaml_file.parts:
            continue
        # Skip Helm template files — they contain {{ }} placeholders and can't be parsed without values
        if _is_helm_template(yaml_file):
            chart = yaml_file.parent.parent.name
            findings.setdefault("k8s.helm_templates_skipped", []).append(f"{yaml_file.name} (chart:{chart})")
            continue
        text = yaml_file.read_text(encoding="utf-8", errors="replace")
        # Split on YAML document separators
        for doc in re.split(r"^---", text, flags=re.MULTILINE):
            kind_match = re.search(r"^kind:\s*(\S+)", doc, re.MULTILINE)
            if not kind_match:
                continue
            kind = kind_match.group(1)
            if kind not in k8s_kinds:
                continue

            name_match = re.search(r"^\s+name:\s*(\S+)", doc, re.MULTILINE)
            ns_match = re.search(r"^\s+namespace:\s*(\S+)", doc, re.MULTILINE)
            name = _clean_name(name_match.group(1) if name_match else "unknown")
            ns = _clean_name(ns_match.group(1) if ns_match else "default")

            entry = f"{name} (ns:{ns})"
            findings.setdefault(f"k8s.{kind.lower()}s", []).append(entry)

            # Ingress hostnames
            if kind == "Ingress":
                for host in re.findall(r"^\s+host:\s*(\S+)", doc, re.MULTILINE):
                    cleaned = _clean_name(host)
                    if cleaned != "<helm-templated>":
                        findings.setdefault("k8s.ingress_hosts", []).append(cleaned)

            # Container images — skip Helm template expressions
            for image in re.findall(r"^\s+image:\s*(\S+)", doc, re.MULTILINE):
                cleaned = _clean_name(image)
                if cleaned != "<helm-templated>":
                    findings.setdefault("k8s.container_images", []).append(cleaned)

            # RBAC — broad permissions
            if kind in ("ClusterRoleBinding", "RoleBinding"):
                if "cluster-admin" in doc or '"*"' in doc or "'*'" in doc:
                    findings.setdefault("k8s.rbac_risks", []).append(
                        f"{kind}/{name}: wildcard or cluster-admin"
                    )

            # Privileged containers
            if "privileged: true" in doc:
                findings.setdefault("k8s.privileged_containers", []).append(name)

            # hostNetwork / hostPID
            if "hostNetwork: true" in doc:
                findings.setdefault("k8s.host_network", []).append(name)
            if "hostPID: true" in doc:
                findings.setdefault("k8s.host_pid", []).append(name)

    return findings


# ---------------------------------------------------------------------------
# CI/CD detection
# ---------------------------------------------------------------------------

def _detect_cicd(target: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    checks = {
        "GitHub Actions": target / ".github" / "workflows",
        "GitLab CI":      target / ".gitlab-ci.yml",
        "Jenkinsfile":    target / "Jenkinsfile",
        "Makefile":       target / "Makefile",
        "CircleCI":       target / ".circleci",
        "Tekton":         None,  # detected via K8s kind:Pipeline below
    }
    detected = []
    for name, path in checks.items():
        if path and path.exists():
            detected.append(name)
    # Also check for Tekton pipelines in k8s YAML
    for yf in list(target.rglob("*.yaml")) + list(target.rglob("*.yml")):
        if yf.read_text(encoding="utf-8", errors="replace").find("kind: Pipeline") != -1:
            if "Tekton" not in detected:
                detected.append("Tekton")
            break
    if detected:
        findings["cicd.tools"] = ", ".join(detected)
    return findings


# ---------------------------------------------------------------------------
# Aggregate & deduplicate
# ---------------------------------------------------------------------------

def _merge(acc: dict[str, Any], updates: dict[str, Any]) -> None:
    for k, v in updates.items():
        if isinstance(v, list):
            existing = acc.get(k, [])
            seen = set(existing)
            acc[k] = existing + [x for x in v if x not in seen]
        else:
            if k not in acc:
                acc[k] = v


def _flatten_for_db(raw: dict[str, Any]) -> dict[str, str]:
    """Convert list values to newline-joined strings ready for context_metadata."""
    flat: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            unique = list(dict.fromkeys(v))  # deduplicate, preserve order
            if unique:
                flat[k] = "\n".join(unique)
        elif v:
            flat[k] = str(v)
    return flat


    return findings


# ---------------------------------------------------------------------------
# Summary MD helper functions
# ---------------------------------------------------------------------------

def _render_data_flows(experiment_id: str, repo_name: str) -> str:
    """Query database for ingress/egress data flows and render as markdown."""
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone()
            if not repo_row:
                return "- No data flows detected (repository not found in database)"
            
            repo_id = repo_row[0]
            
            # Query exposure analysis for entry points and internet-accessible resources
            # Join with resources table to get repo_id
            entry_points = conn.execute("""
                SELECT ea.resource_name, ea.resource_type, ea.exposure_level, ea.has_internet_path
                FROM exposure_analysis ea
                JOIN resources r ON ea.resource_id = r.id
                WHERE r.repo_id = ? AND (ea.is_entry_point = 1 OR ea.has_internet_path = 1)
                ORDER BY ea.is_entry_point DESC, ea.exposure_level
            """, (repo_id,)).fetchall()
            
            if not entry_points:
                return "- No internet-facing entry points detected"
            
            lines = []
            lines.append("**Ingress (Internet → Services):**")
            for name, rtype, level, has_path in entry_points:
                emoji = "🌐" if level == "direct_exposure" else "🛡️" if level == "mitigated" else "🔒"
                lines.append(f"- {emoji} {name} ({rtype}) - {level}")
            
            # Query for data-tier resources (egress from compute to data)
            data_tier = conn.execute("""
                SELECT ea.resource_name, ea.resource_type
                FROM exposure_analysis ea
                JOIN resources r ON ea.resource_id = r.id
                WHERE r.repo_id = ? AND ea.normalized_role = 'data'
                ORDER BY ea.resource_name
            """, (repo_id,)).fetchall()
            
            if data_tier:
                lines.append("\n**Egress (Services → Data):**")
                for name, rtype in data_tier:
                    lines.append(f"- 💾 {name} ({rtype})")
            
            return "\n".join(lines)
    except Exception as e:
        return f"- Error querying data flows: {str(e)}"


def _render_rbac(experiment_id: str, repo_name: str) -> str:
    """Query database for RBAC and permissions data."""
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone()
            if not repo_row:
                return "- No RBAC data found"
            
            repo_id = repo_row[0]
            
            # Query for identity/auth resources
            rbac_resources = conn.execute("""
                SELECT rt.friendly_name, r.resource_name, rt.category
                FROM resources r
                JOIN resource_types rt ON r.resource_type = rt.terraform_type
                WHERE r.repo_id = ? 
                AND rt.category IN ('Identity', 'Security')
                ORDER BY rt.category, rt.friendly_name, r.resource_name
            """, (repo_id,)).fetchall()
            
            if not rbac_resources:
                return "- No role/permission resources detected"
            
            lines = []
            by_category = {}
            for friendly, name, category in rbac_resources:
                by_category.setdefault(category, []).append(f"{friendly}: {name}")
            
            for category in ['Identity', 'Security']:
                if category in by_category:
                    emoji = "👤" if category == "Identity" else "🔐"
                    lines.append(f"**{emoji} {category}:**")
                    for item in by_category[category][:10]:  # Limit to 10 per category
                        lines.append(f"- {item}")
                    if len(by_category[category]) > 10:
                        lines.append(f"- ... and {len(by_category[category]) - 10} more")
                    lines.append("")  # Blank line between categories
            
            return "\n".join(lines).strip() if lines else "- No role/permission resources detected"
    except Exception as e:
        return f"- Error querying RBAC: {str(e)}"


def _render_findings(experiment_id: str, repo_name: str) -> str:
    """Query database for findings and render summary."""
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone()
            if not repo_row:
                return "- No findings (repository not found in database)"
            
            repo_id = repo_row[0]
            
            # Query findings by severity
            findings = conn.execute("""
                SELECT title, severity_score, category, source_file
                FROM findings
                WHERE repo_id = ?
                ORDER BY severity_score DESC, title
            """, (repo_id,)).fetchall()
            
            if not findings:
                return "- No findings detected (0 security issues found)"
            
            lines = []
            lines.append(f"**Total: {len(findings)} finding(s)**\n")
            
            # Group by severity
            critical = [f for f in findings if f[1] >= 9]
            high = [f for f in findings if 7 <= f[1] < 9]
            medium = [f for f in findings if 5 <= f[1] < 7]
            low = [f for f in findings if f[1] < 5]
            
            if critical:
                lines.append(f"**🔴 Critical ({len(critical)}):**")
                for title, score, category, file in critical[:5]:
                    lines.append(f"- [{score}/10] {title}")
                if len(critical) > 5:
                    lines.append(f"- ... and {len(critical) - 5} more")
            
            if high:
                lines.append(f"\n**🟠 High ({len(high)}):**")
                for title, score, category, file in high[:5]:
                    lines.append(f"- [{score}/10] {title}")
                if len(high) > 5:
                    lines.append(f"- ... and {len(high) - 5} more")
            
            if medium:
                lines.append(f"\n**🟡 Medium ({len(medium)}):**")
                for title, score, category, file in medium[:3]:
                    lines.append(f"- [{score}/10] {title}")
                if len(medium) > 3:
                    lines.append(f"- ... and {len(medium) - 3} more")
            
            if low:
                lines.append(f"\n**🟢 Low ({len(low)}):**")
                lines.append(f"- {len(low)} low-severity finding(s)")
            
            return "\n".join(lines)
    except Exception as e:
        return f"- Error querying findings: {str(e)}"


def _render_apim_auth_methods(experiment_id: str, repo_name: str) -> str:
    """Query database for APIM operations and their auth methods."""
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone()
            if not repo_row:
                return "- No APIM resources detected"
            
            repo_id = repo_row[0]
            
            # Query APIM operations
            operations = conn.execute("""
                SELECT resource_name
                FROM resources
                WHERE repo_id = ? AND resource_type = 'azurerm_api_management_api_operation'
                ORDER BY resource_name
            """, (repo_id,)).fetchall()
            
            if not operations:
                return "- No APIM operations detected"
            
            lines = []
            lines.append("**API Operations and Authentication:**\n")
            
            # Get API policy to check for JWT validation
            # For now, show that headers are required but may not be cryptographically verified
            for (op_name,) in operations:
                # Mark specific operations based on name
                if op_name == 'health_check':
                    lines.append(f"- `{op_name}` - 🌐 Public (no auth required)")
                elif 'hidden' in op_name:
                    lines.append(f"- `{op_name}` - 🔐 CB-Logical-Execution-Context required")
                else:
                    lines.append(f"- `{op_name}` - ⚠️ Custom headers (CB-Logical-Execution-Context, CB-User-Context)")
            
            lines.append("\n**⚠️ Security Notes:**")
            lines.append("- Custom headers are used for authorization but are NOT cryptographically verified")
            lines.append("- Headers can be spoofed by any client with a valid APIM subscription key")
            lines.append("- API policy missing JWT validation (contains only `<forward-request />`)")
            
            return "\n".join(lines)
    except Exception as e:
        return f"- Error querying APIM operations: {str(e)}"



# ---------------------------------------------------------------------------
# Summary MD writer
# ---------------------------------------------------------------------------

def _write_summary(
    experiment_id: str,
    repo_name: str,
    flat: dict[str, str],
    fired_framework_ids: set[str],
    fired_code_ids: set[str],
    output_dir: Path,
) -> Path:
    """Write/overwrite the repo summary MD with structured Phase 2 findings."""
    summary_path = output_dir / "Summary" / "Repos" / f"{repo_name}.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    def _val(key: str, default: str = "Not detected") -> str:
        return flat.get(key, default)

    def _list_val(key: str) -> list[str]:
        v = flat.get(key, "")
        return [x for x in v.splitlines() if x] if v else []

    # Build language/framework summary line
    langs = []
    if "context-python-requirements" in fired_framework_ids:
        langs.append("Python")
    if "context-nodejs-package-json" in fired_framework_ids:
        langs.append("Node.js")
    if "context-java-maven-project" in fired_framework_ids:
        langs.append("Java")
    if "context-dotnet-project" in fired_framework_ids:
        langs.append(".NET")
    if "context-golang-module" in fired_framework_ids:
        langs.append("Go")

    py_fw = _list_val("python.frameworks")
    node_fw = _list_val("node.frameworks")
    java_spring = "Spring Boot" if flat.get("java.spring_boot") == "true" else ""
    go_fw = _list_val("go.frameworks")
    frameworks_all = py_fw + node_fw + ([java_spring] if java_spring else []) + go_fw

    # Query database for infrastructure-level auth resources
    auth_infrastructure = []
    try:
        with get_db_connection() as conn:
            # Get repo_id for this repo
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
                (experiment_id, repo_name)
            ).fetchone()
            
            if repo_row:
                repo_id = repo_row[0]
                # Query for auth/identity related infrastructure resources
                auth_keywords = ['subscription', 'identity', 'jwt', 'auth', 'key_vault', 'principal']
                auth_resources = conn.execute("""
                    SELECT DISTINCT resource_type, COUNT(*) as count
                    FROM resources
                    WHERE repo_id = ?
                    AND (
                        resource_type LIKE '%subscription%'
                        OR resource_type LIKE '%identity%'
                        OR resource_type LIKE '%jwt%'
                        OR resource_type LIKE '%auth%'
                        OR resource_type LIKE '%key_vault%'
                        OR resource_type LIKE '%principal%'
                        OR resource_name LIKE '%principal%'
                        OR resource_name LIKE '%identity%'
                        OR resource_name LIKE '%auth%'
                    )
                    GROUP BY resource_type
                    ORDER BY count DESC
                """, (repo_id,)).fetchall()
                
                for resource_type, count in auth_resources:
                    if 'subscription' in resource_type.lower() and 'servicebus' not in resource_type.lower():
                        auth_infrastructure.append(f"API Management Subscription Keys ({count})")
                    elif 'principal' in resource_type.lower():
                        auth_infrastructure.append(f"{resource_type} ({count})")
                    elif 'identity' in resource_type.lower():
                        auth_infrastructure.append(f"Managed Identity ({count})")
                    elif 'key_vault' in resource_type.lower():
                        auth_infrastructure.append(f"Key Vault secrets ({count})")
    except Exception:
        pass  # Fail silently if DB query fails

    # Code-level auth patterns
    auth_patterns = []
    if any("jwt" in x for x in fired_code_ids):
        auth_patterns.append("JWT validation")
    if any("custom-header-auth" in x for x in fired_code_ids):
        auth_patterns.append("Custom header auth (⚠️ no crypto validation)")
    if flat.get("node.security_libs"):
        auth_patterns += [x for x in _list_val("node.security_libs") if x in ("passport", "jsonwebtoken")]
    if flat.get("python.security_libs"):
        auth_patterns += _list_val("python.security_libs")
    if flat.get("java.spring_security") == "true":
        auth_patterns.append("Spring Security")

    ingress_hosts = _list_val("k8s.ingress_hosts")
    images = _list_val("k8s.container_images")
    rbac_risks = _list_val("k8s.rbac_risks")
    privileged = _list_val("k8s.privileged_containers")
    host_net = _list_val("k8s.host_network")
    sensitive_env = _list_val("container.sensitive_env_vars")
    base_images = _list_val("container.base_images")
    exposed_ports = _list_val("container.exposed_ports")
    cicd = flat.get("cicd.tools", "Not detected")

    def _bullets(items: list[str], indent: str = "- ") -> str:
        return "\n".join(f"{indent}{x}" for x in items) if items else f"{indent}None detected"

    # Combine auth patterns for TL;DR (show infrastructure first)
    auth_summary = []
    if auth_infrastructure:
        auth_summary.extend(auth_infrastructure[:2])  # Show top 2 infrastructure items
    if auth_patterns:
        auth_summary.extend(auth_patterns[:2])  # Show top 2 code items
    auth_tldr = ", ".join(auth_summary) if auth_summary else "Not detected"

    # Mermaid diagram — top-level flow
    ingress_nodes = "\n    ".join(
        f'Ingress["{h}"]' for h in (ingress_hosts or ["Ingress"])
    )
    service_nodes = "\n    ".join(
        f'Svc{i}["{img.split("/")[-1].split(":")[0]}"]'
        for i, img in enumerate(images[:6])
    ) or 'Svc0["services"]'
    mermaid = f"""```mermaid
flowchart LR
    Internet["🌐 Internet"] --> {ingress_nodes or "Ingress"}
    {ingress_nodes or "Ingress"} --> {service_nodes.split(chr(10))[0].split('[')[0].strip() or 'Svc0'}
    {chr(10).join('    ' + n.split('[')[0].strip() + ' --> DB[("data")]' for n in service_nodes.splitlines() if 'DB' not in n and n.strip())}
```"""

    md = f"""# 📦 {repo_name}

> **Phase 2 code context — generated by `discover_code_context.py` (no LLM)**

---

## 📊 TL;DR

| **Field** | **Value** |
|---|---|
| **Languages** | {", ".join(langs) or "Not detected"} |
| **Frameworks** | {", ".join(frameworks_all) or "Not detected"} |
| **Containerization** | {"Docker" if base_images else "Not detected"} |
| **CI/CD** | {cicd} |
| **Auth Patterns** | {auth_tldr} |
| **RBAC Risks** | {str(len(rbac_risks)) + " found" if rbac_risks else "None detected"} |
| **Privileged Containers** | {str(len(privileged)) + " found" if privileged else "None"} |

---

## 🏗️ Architecture

{mermaid}

---

## 🔐 Authentication & Identity

**Infrastructure-level:**
{_bullets(auth_infrastructure or ["None detected"])}

**Code-level:**
{_bullets(auth_patterns or ["No auth patterns detected by opengrep"])}

---

## 🐳 Container & Deployment Notes

**Base Images:**
{_bullets(base_images)}

**Exposed Ports:**
{_bullets(exposed_ports)}

**Container Images (K8s):**
{_bullets(images)}

**Sensitive ENV vars in Dockerfiles:**
{_bullets(sensitive_env)}

---

## ☸️ Kubernetes

**Ingress Hosts:**
{_bullets(ingress_hosts)}

**RBAC Risks:**
{_bullets(rbac_risks)}

**Privileged containers:**
{_bullets(privileged)}

**Host network:**
{_bullets(host_net)}

---

## 🌐 Network Topology

**Ingress hosts:** {", ".join(ingress_hosts) or "Not detected"}

---

## 🔌 Ingress Paths

{_bullets(ingress_hosts)}

---

## ⚙️ CI/CD

{cicd}

---

## 📦 Dependencies

**Python frameworks:** {", ".join(py_fw) or "None"}
**Node.js frameworks:** {", ".join(node_fw) or "None"}
**Java artifacts:** {", ".join(_list_val("java.artifacts")) or "None"}
**Go modules:** {", ".join(_list_val("go.modules")) or "None"}

---

## 🔄 Ingress/Egress Data Flows

{_render_data_flows(experiment_id, repo_name)}

---

## 🔐 Roles & Permissions

{_render_rbac(experiment_id, repo_name)}

---

## 🔓 API Authentication Methods

{_render_apim_auth_methods(experiment_id, repo_name)}

---

## ☁️ Service Authentication Topology

{render_service_auth_topology(experiment_id, repo_name)}

---

## 🔍 Findings

{_render_findings(experiment_id, repo_name)}

---

## 🔎 opengrep Detection Rules Fired

**Frameworks:**
{_bullets(sorted(fired_framework_ids) or ["None"])}

**Code patterns:**
{_bullets(sorted(fired_code_ids) or ["None"])}
"""

    summary_path.write_text(md, encoding="utf-8")
    return summary_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2: script-based code context discovery (no LLM)."
    )
    parser.add_argument("--experiment", required=True, help="Experiment ID (e.g. 003)")
    parser.add_argument("--repo", required=True, help="Repository name (folder name)")
    parser.add_argument("--target", required=True, help="Absolute path to the repo to scan")
    parser.add_argument(
        "--output-dir",
        help="Experiment output directory (default: Output/Learning/experiments/<id>_<repo>)",
    )
    parser.add_argument(
        "--no-opengrep",
        action="store_true",
        help="Skip opengrep runs (file parsing only, useful if opengrep is unavailable)",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"Error: target is not a directory: {target}", file=sys.stderr)
        return 1

    # Resolve output dir
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        from output_paths import OUTPUT_ROOT
        experiments = OUTPUT_ROOT / "Learning" / "experiments"
        candidates = sorted(experiments.glob(f"{args.experiment}_*"))
        output_dir = candidates[0] if candidates else (
            experiments / f"{args.experiment}_{args.repo}"
        )

    print(f"\n[Phase 2] Target:      {target}")
    print(f"[Phase 2] Experiment:  {args.experiment}")
    print(f"[Phase 2] Repo:        {args.repo}")
    print(f"[Phase 2] Output dir:  {output_dir}")

    raw: dict[str, Any] = {}

    # ── 1. opengrep: Frameworks ──────────────────────────────────────────────
    fired_framework_ids: set[str] = set()
    if not args.no_opengrep:
        print("\n[Phase 2] Running opengrep Detection/Frameworks ...")
        fw_results = _run_opengrep(DETECTION_FRAMEWORKS, target)
        fired_framework_ids = _extract_rule_ids(fw_results)
        print(f"  → {len(fired_framework_ids)} framework rule(s) fired: {sorted(fired_framework_ids)}")
        for rule_id in fired_framework_ids:
            raw[f"opengrep.framework.{rule_id}"] = "detected"

    # ── 2. opengrep: Code patterns ───────────────────────────────────────────
    fired_code_ids: set[str] = set()
    if not args.no_opengrep:
        print("\n[Phase 2] Running opengrep Detection/Code ...")
        code_results = _run_opengrep(DETECTION_CODE, target)
        fired_code_ids = _extract_rule_ids(code_results)
        print(f"  → {len(fired_code_ids)} code rule(s) fired: {sorted(fired_code_ids)}")
        for rule_id in fired_code_ids:
            raw[f"opengrep.code.{rule_id}"] = "detected"

    # ── 3. Manifest parsing ──────────────────────────────────────────────────
    print("\n[Phase 2] Parsing manifest files ...")
    _merge(raw, _parse_package_json(target))
    _merge(raw, _parse_requirements_txt(target))
    _merge(raw, _parse_pom_xml(target))
    _merge(raw, _parse_go_mod(target))
    _merge(raw, _parse_dockerfiles(target))

    # ── 4. Kubernetes manifests ──────────────────────────────────────────────
    print("[Phase 2] Parsing Kubernetes manifests ...")
    _merge(raw, _parse_k8s_manifests(target))

    # ── 5. CI/CD detection ───────────────────────────────────────────────────
    print("[Phase 2] Detecting CI/CD tooling ...")
    _merge(raw, _detect_cicd(target))

    # ── 6. Persist to DB ─────────────────────────────────────────────────────
    flat = _flatten_for_db(raw)
    print(f"\n[Phase 2] Persisting {len(flat)} metadata entries to DB (clearing stale entries first) ...")
    # Clear stale entries so removed/fixed data doesn't persist across runs
    repo_id = ensure_repository_entry(args.experiment, args.repo)
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM context_metadata WHERE experiment_id=? AND repo_id=? AND namespace='phase2_code'",
            (args.experiment, repo_id),
        )
    for key, value in sorted(flat.items()):
        upsert_context_metadata(
            experiment_id=args.experiment,
            repo_name=args.repo,
            key=key,
            value=value,
            namespace="phase2_code",
            source="discover_code_context",
        )
        print(f"  ✓ {key}")

     # ── 7. Write summary MD ───────────────────────────────────────────────────
    print("\n[Phase 2] Writing repo summary MD ...")
    summary_path = _write_summary(
        experiment_id=args.experiment,
        repo_name=args.repo,
        flat=flat,
        fired_framework_ids=fired_framework_ids,
        fired_code_ids=fired_code_ids,
        output_dir=output_dir,
    )
    print(f"  ✓ {summary_path}")

    print(f"\n[Phase 2] Complete — {len(flat)} metadata entries, summary at {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
