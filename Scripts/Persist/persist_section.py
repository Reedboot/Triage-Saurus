#!/usr/bin/env python3
"""Persist an AI-generated HTML section or Mermaid diagram to the Triage-Saurus database.

Usage — HTML section (e.g. TLDR, Risks narrative):
    python3 Scripts/Persist/persist_section.py \
        --repo MyRepo \
        --experiment 042 \
        --key tldr \
        --title "📊 TL;DR" \
        --html-file /tmp/tldr.html

    # or inline HTML string
    python3 Scripts/Persist/persist_section.py \
        --repo MyRepo --experiment 042 --key risks --title "⚠️ Risks" \
        --html "<p>High-severity findings detected…</p>"

Usage — Mermaid architecture diagram:
    python3 Scripts/Persist/persist_section.py \
        --diagram-provider azure \
        --diagram-title "Azure Architecture" \
        --experiment 042 \
        --mermaid-file /tmp/architecture.mmd

Section key reference
---------------------
tldr          → 📊 TL;DR / Executive Summary
architecture  → 🏗 Architecture
assets        → 📦 Assets
findings      → 🔍 Findings
risks         → ⚠️ Risks
roles         → 🔐 Roles & Permissions
ingress       → 🔌 Ingress
egress        → 🚪 Egress
auth          → 🔑 Authentication & Identity
containers    → 🐳 Containers
kubernetes    → ☸️ Kubernetes
network       → 🌐 Network Topology
cicd          → ⚙️ CI/CD
dependencies  → 📦 Dependencies
detection     → 🔎 Detection Rules Fired
meta          → 🏷️ Meta Data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve Scripts/Persist on the path so db_helpers can be imported directly.
# Also add the repo root so the no-op cozo_helpers shim is found first.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))   # repo root (no-op cozo_helpers shim)
sys.path.insert(0, str(_HERE))   # Scripts/Persist

from db_helpers import upsert_ai_section, upsert_cloud_diagram


def _read_content(html_file: str | None, html_inline: str | None, label: str) -> str:
    if html_file:
        path = Path(html_file)
        if not path.exists():
            print(f"ERROR: {label} file not found: {html_file}", file=sys.stderr)
            sys.exit(1)
        return path.read_text(encoding="utf-8")
    if html_inline is not None:
        return html_inline
    print(f"ERROR: Provide --html-file or --html for {label}.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persist an HTML section or Mermaid diagram to the Triage-Saurus DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Common
    parser.add_argument("--experiment", required=True, help="Experiment ID (e.g. 042)")
    parser.add_argument("--generated-by", default="agent", help="Name of the agent/script writing this")

    # HTML section mode
    section_grp = parser.add_argument_group("HTML section mode")
    section_grp.add_argument("--repo", help="Repository name")
    section_grp.add_argument("--key", help="Section key slug (e.g. tldr, risks, roles)")
    section_grp.add_argument("--title", help="Human-readable section title")
    section_grp.add_argument("--html-file", metavar="PATH", help="Path to .html file to store")
    section_grp.add_argument("--html", metavar="STRING", help="Inline HTML string to store")

    # Diagram mode
    diagram_grp = parser.add_argument_group("Mermaid diagram mode")
    diagram_grp.add_argument("--diagram-provider", metavar="PROVIDER", help="Cloud provider (e.g. azure, aws)")
    diagram_grp.add_argument("--diagram-title", metavar="TITLE", help="Diagram title")
    diagram_grp.add_argument("--mermaid-file", metavar="PATH", help="Path to .mmd or .md file with Mermaid code")
    diagram_grp.add_argument("--mermaid", metavar="STRING", help="Inline Mermaid code string")
    diagram_grp.add_argument("--diagram-order", type=int, default=0, help="Display order for diagram tab (default 0)")

    args = parser.parse_args()

    is_diagram_mode = bool(args.diagram_provider or args.diagram_title or args.mermaid_file or args.mermaid)

    if is_diagram_mode:
        if not args.diagram_provider:
            parser.error("--diagram-provider is required in diagram mode")
        if not args.diagram_title:
            parser.error("--diagram-title is required in diagram mode")
        mermaid_code = _read_content(args.mermaid_file, args.mermaid, "mermaid")
        upsert_cloud_diagram(
            experiment_id=args.experiment,
            provider=args.diagram_provider.lower(),
            diagram_title=args.diagram_title,
            mermaid_code=mermaid_code,
            display_order=args.diagram_order,
        )
        print(
            f"✅ Diagram stored: experiment={args.experiment} "
            f"provider={args.diagram_provider} title={args.diagram_title!r}"
        )
    else:
        if not args.repo:
            parser.error("--repo is required in section mode")
        if not args.key:
            parser.error("--key is required in section mode")
        if not args.title:
            parser.error("--title is required in section mode")
        content_html = _read_content(args.html_file, args.html, "HTML section")
        upsert_ai_section(
            experiment_id=args.experiment,
            repo_name=args.repo,
            section_key=args.key,
            title=args.title,
            content_html=content_html,
            generated_by=args.generated_by,
        )
        print(
            f"✅ Section stored: experiment={args.experiment} "
            f"repo={args.repo!r} key={args.key!r} title={args.title!r}"
        )


if __name__ == "__main__":
    main()
