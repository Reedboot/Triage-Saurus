# report_generation.py

import os
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple

from models import RepositoryContext
from context_extraction import (
    iter_files,
    detect_terraform_resources,
    detect_hosting_from_terraform,
    detect_ingress_from_code,
    detect_apim_backend_services,
    detect_external_dependencies,
    detect_authentication_methods,
    detect_network_topology,
    has_terraform_module_source,
    classify_terraform_resources,
    extract_kubernetes_topology_signals,
    extract_vm_names_with_os,
    extract_nsg_associations,
    detect_terraform_backend,
    extract_resource_names,
    extract_resource_names_with_property,
    _extract_paas_resources,
    _detect_vm_paas_connections,
    _analyze_nsg_rules,
    _extract_nsg_allowed_protocols,
    _extract_service_accounts,
)
from template_renderer import render_template
from markdown_validator import validate_markdown_file

def now_uk():
    from datetime import datetime
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def write_cloud_resource_summaries(
    *,
    repo: Path,
    provider: str,
    summary_dir: Path,
) -> list[Path]:
    """Generate detailed per-resource summaries for key compute resources (VMs, AKS clusters)."""
    # This is a placeholder for the original write_cloud_resource_summaries
    return []

def write_experiment_cloud_architecture_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    summary_dir: Path,
    findings_dir: Path | None = None,
    repo_summary_path: Path | None = None,
) -> Path | None:
    """Write experiment-scoped provider architecture summaries for ALL detected providers."""
    # This is a placeholder for the original write_experiment_cloud_architecture_summary
    return None

def _build_simple_architecture_diagram(
    repo_name: str,
    hosting_info: dict,
    tf_resource_types: set[str],
    ingress_info: dict,
    auth_service: str,
    backend_services: list[str],
) -> str:
    """Build simplified architecture diagram for template."""
    # This is a placeholder for the original _build_simple_architecture_diagram
    return ""

def _group_evidence(evidence_list: list) -> list[str]:
    """Group evidence items by label, showing count if multiple."""
    # This is a placeholder for the original _group_evidence
    return []

def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    repo_type: str,
    purpose: str,
    langs: list[tuple[str, str]],
    ci: str,
    providers: list[str],
    ingress: list,
    egress: list,
    extra_evidence: list,
    scan_scope: str,
    summary_dir: Path | None = None,
) -> Path:
    """Write repository summary using template_renderer."""
    # This is a placeholder for the original write_repo_summary
    out_path = summary_dir / "Repos" / f"{repo_name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.touch()
    return out_path

def generate_reports(context: RepositoryContext, summary_dir_str: str):
    """
    Generates reports from the context model.
    """
    summary_dir = Path(summary_dir_str)
    repo_path = Path(summary_dir.parent, "Intake", "Code", context.repository_name) # simplified

    # A real implementation would pass more arguments to these functions
    write_repo_summary(
        repo=repo_path,
        repo_name=context.repository_name,
        repo_type="Unknown",
        purpose="Unknown",
        langs=[],
        ci="Unknown",
        providers=[],
        ingress=[],
        egress=[],
        extra_evidence=[],
        scan_scope="full",
        summary_dir=summary_dir
    )

    write_experiment_cloud_architecture_summary(
        repo=repo_path,
        repo_name=context.repository_name,
        providers=[],
        summary_dir=summary_dir
    )

def write_to_database(context: RepositoryContext):
    """
    Writes the context model to the database.
    """
    # This is where the database writing logic would go.
    # For now, it's just a placeholder.
    print(f"Writing {len(context.resources)} resources for {context.repository_name} to the database.")
