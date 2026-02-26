#!/usr/bin/env python3
"""Template rendering for Triage-Saurus document generation.

Uses the existing Templates/ folder for all document templates.
Supports both findings templates and report generation templates.
"""

from pathlib import Path
from string import Template


# Repository root and templates directory
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "Templates"


def render_template(template_name: str, context: dict) -> str:
    """Render a template from the Templates/ directory.
    
    Args:
        template_name: Template filename (e.g., "RepoSummary.md", "CloudFinding.md")
        context: Dictionary of variables to substitute
    
    Returns:
        Rendered template string
    
    Example:
        context = {"repo_name": "MyRepo", "services": "AKS, SQL"}
        output = render_template("RepoSummary.md", context)
    """
    template_path = TEMPLATES_DIR / template_name
    
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    
    template_content = template_path.read_text(encoding='utf-8')
    
    # Use string.Template for simple ${variable} substitution
    template = Template(template_content)
    
    try:
        return template.safe_substitute(context)
    except Exception as e:
        raise ValueError(f"Error rendering template {template_name}: {e}")
