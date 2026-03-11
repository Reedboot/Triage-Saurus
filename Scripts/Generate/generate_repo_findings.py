#!/usr/bin/env python3
"""Generate Repo finding Markdown files from database context."""

import argparse
import sys
from pathlib import Path

from db_helpers import get_db_connection
from template_renderer import render_template
from output_paths import OUTPUT_FINDINGS_DIR

def get_repo_context(repo_name: str) -> dict:
    """Get repository context from the database."""
    with get_db_connection() as conn:
        repo = conn.execute("SELECT * FROM repositories WHERE repo_name = ?", [repo_name]).fetchone()
        if not repo:
            raise ValueError(f"Repository not found in database: {repo_name}")
        
        resources = conn.execute("SELECT * FROM resources WHERE repo_id = ?", [repo['id']]).fetchall()
        connections = conn.execute("SELECT * FROM resource_connections WHERE source_repo_id = ? OR target_repo_id = ?", [repo['id'], repo['id']]).fetchall()

    return {
        "repo": dict(repo),
        "resources": [dict(r) for r in resources],
        "connections": [dict(c) for c in connections],
    }

def generate_finding_model(repo_context: dict) -> dict:
    """Generate the finding model from the repository context."""
    repo = repo_context["repo"]
    # This is a simplified model. A real implementation would have more logic.
    return {
        "title": repo["repo_name"],
        "architecture_mermaid": "graph TB\n  A --> B",
        "description": "Repo scan summary.",
        "overall_score": {"severity": "Low", "score": 3},
        "overview_bullets": [f"**Repo name:** {repo['repo_name']}"],
        "security_review": {
            "summary": "This repo appears to manage cloud infrastructure via IaC.",
            "applicability": {"status": "Yes", "evidence": "IaC files detected."},
            "assumptions": ["Terraform state is stored securely."],
            "exploitability": "If an attacker can read Terraform state files, they may recover credentials.",
            "risks": ["State/outputs/logs may expose secret material."],
            "key_evidence_deep": ["- ✅ IaC provider/module usage"],
            "recommendations": [{"text": "Confirm Terraform state backend", "score_from": 3, "score_to": 2}],
            "rationale": "Score is driven by the presence of IaC.",
        },
        "skeptic": {
            "dev": {"missing": "...", "score_recommendation": "Keep", "how_it_could_be_worse": "...", "countermeasure_effectiveness": "...", "assumptions_to_validate": "..."},
            "platform": {"missing": "...", "score_recommendation": "Keep", "operational_constraints": "...", "countermeasure_effectiveness": "...", "assumptions_to_validate": "..."},
        },
        "collaboration": {"outcome": "Repo scan completed.", "next_step": "Confirm state backend."},
        "compounding_findings": ["None identified"],
        "meta": {"last_updated": "now"},
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Repo finding Markdown files from database context.")
    parser.add_argument("repo_name", help="Name of the repository to generate a finding for.")
    args = parser.parse_args()

    try:
        repo_context = get_repo_context(args.repo_name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    finding_model = generate_finding_model(repo_context)
    
    template_path = Path(__file__).resolve().parents[1] / "Templates" / "RepoFinding.md"
    
    output_dir = OUTPUT_FINDINGS_DIR / "Repo"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"Repo_{args.repo_name}.md"

    rendered_finding = render_template(str(template_path), finding_model)
    output_path.write_text(rendered_finding, encoding="utf-8")

    print(f"Finding written to {output_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
