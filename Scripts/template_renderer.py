#!/usr/bin/env python3
"""Template rendering for Triage-Saurus document generation.

Uses the existing Templates/ folder for all document templates.
Supports both findings templates and report generation templates.
"""

from pathlib import Path
from string import Template
from typing import Any, Dict


# Repository root and templates directory
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "Templates"


def render_template(template_name: str, context: Dict[str, Any]) -> str:
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
    
    # Add helper functions to context
    context = dict(context)  # Copy to avoid mutating input
    context['_helpers'] = TemplateHelpers()
    
    try:
        return template.safe_substitute(context)
    except Exception as e:
        raise ValueError(f"Error rendering template {template_name}: {e}")


class TemplateHelpers:
    """Helper functions available in templates."""
    
    @staticmethod
    def join_list(items: list, sep: str = ", ") -> str:
        """Join list items with separator."""
        return sep.join(str(item) for item in items)
    
    @staticmethod
    def conditional(condition: bool, true_val: str, false_val: str = "") -> str:
        """Return true_val if condition else false_val."""
        return true_val if condition else false_val
    
    @staticmethod
    def format_count(count: int, singular: str, plural: str = None) -> str:
        """Format count with singular/plural noun."""
        if plural is None:
            plural = singular + "s"
        return f"{count} {singular if count == 1 else plural}"


def render_template_string(template_str: str, context: Dict[str, Any]) -> str:
    """Render a template string directly (not from file).
    
    Useful for inline templates or testing.
    """
    template = Template(template_str)
    context = dict(context)
    context['_helpers'] = TemplateHelpers()
    return template.safe_substitute(context)


# For more complex templates, we can add Jinja2 support later
def render_jinja_template(template_path: Path, context: Dict[str, Any]) -> str:
    """Render a Jinja2 template (if jinja2 is installed).
    
    This is a placeholder for future Jinja2 support.
    """
    try:
        from jinja2 import Environment, FileSystemLoader
        
        script_dir = Path(__file__).parent
        repo_root = script_dir.parent
        templates_dir = repo_root / "Templates"
        
        env = Environment(loader=FileSystemLoader(templates_dir))
        template = env.get_template(str(template_path))
        return template.render(context)
    except ImportError:
        raise ImportError("Jinja2 not installed. Use render_template() for simple templates.")


if __name__ == "__main__":
    # Test the template renderer with existing templates
    print("Template Renderer - Uses existing Templates/ folder")
    print(f"Templates directory: {TEMPLATES_DIR}")
    print()
    print("Available templates:")
    if TEMPLATES_DIR.exists():
        for template in sorted(TEMPLATES_DIR.glob("*.md")):
            print(f"  - {template.name}")
    print()
    print("Usage:")
    print('  from template_renderer import render_template')
    print('  context = {"repo_name": "Test", "timestamp": "01/01/2026"}')
    print('  output = render_template("RepoSummary.md", context)')
