#!/usr/bin/env python3
"""
render_exposure_summary.py

Generate Markdown summaries with embedded Mermaid diagrams for exposure analysis results.
Creates per-provider diagrams (AWS, Azure, GCP) colored by exposure level.

Usage:
  python3 render_exposure_summary.py --experiment <exp_id> [--output-dir <dir>]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
import db_helpers


class ExposureMermaidRenderer:
    """Render Mermaid diagrams from exposure analysis results."""

    # Color scheme for exposure levels
    COLOR_SCHEME = {
        "direct_exposure": "#ff0000",  # Red
        "mitigated": "#ff9900",  # Orange
        "isolated": "#00cc00",  # Green
        "entry_point": "#0066cc",  # Blue
        "countermeasure": "#6600cc",  # Purple
    }

    @staticmethod
    def sanitize_id(name: str) -> str:
        """Convert resource name to valid Mermaid node ID."""
        return name.replace("-", "_").replace(".", "_").replace(" ", "_").replace("/", "_")

    @staticmethod
    def get_node_style(resource_name: str, exposure_level: str, has_violation: bool) -> str:
        """Get Mermaid style for a node based on exposure and violations."""
        node_id = ExposureMermaidRenderer.sanitize_id(resource_name)
        color = ExposureMermaidRenderer.COLOR_SCHEME.get(exposure_level, "#999999")
        
        if exposure_level == "direct_exposure" and has_violation:
            # Red + pulse for direct exposure + vulnerability
            return f"style {node_id} stroke:{color},stroke-width:3px,animation:pulse"
        elif exposure_level == "direct_exposure":
            # Orange for direct exposure without violation
            return f"style {node_id} stroke:#ff9900,stroke-width:3px"
        else:
            return f"style {node_id} stroke:{color},stroke-width:2px"

    @staticmethod
    def render_provider_diagram(
        provider: str,
        resources: List[dict],
        id_to_name: Optional[dict] = None,
    ) -> str:
        """
        Render Mermaid diagram for a specific cloud provider.

        Args:
            provider: Cloud provider (aws, azure, gcp)
            resources: List of exposure_analysis rows for this provider
            id_to_name: optional mapping of resource_id -> resource_name for path rendering

        Returns:
            Mermaid diagram code
        """
        if not resources:
            return f"graph TD\n    N[\"No {provider.upper()} resources analyzed\"]"

        lines = [f"graph TD", f"    subgraph {provider.upper()}[\"{provider.upper()} Environment\"]"]

        # Group by normalized role
        by_role = defaultdict(list)
        for r in resources:
            role = r.get("normalized_role", "unknown")
            by_role[role].append(r)

        # Render subgraphs by role
        role_order = ["entry_point", "countermeasure", "load_balancer", "compute", "data"]
        role_labels = {
            "entry_point": "🌐 Entry Points",
            "countermeasure": "🛡️ Countermeasures",
            "load_balancer": "⚖️ Load Balancers",
            "compute": "⚙️ Compute",
            "data": "💾 Data",
        }

        for role in role_order:
            if role not in by_role:
                continue
            role_resources = by_role[role]
            if role_resources:
                role_label = role_labels.get(role, role)
                lines.append(f"        subgraph {role}[\"{role_label}\"]")
                for r in role_resources:
                    node_id = ExposureMermaidRenderer.sanitize_id(r["resource_name"])
                    exposure = r.get("exposure_level", "unknown")
                    has_violation = bool(r.get("opengrep_violations") and json.loads(r["opengrep_violations"]))
                    
                    # Node label with resource type
                    label = f"{r['resource_name']}<br/>({r['resource_type']})"
                    lines.append(f"            {node_id}[\"{label}\"]")
                lines.append(f"        end")

        lines.append(f"    end")

        # Build id->name mapping fallback
        id_to_name = id_to_name or {}

        # Edges: collect edges from exposure paths and annotate styles
        edges = []  # list of (edge_line, style)
        edge_index = 0
        for r in resources:
            # exposure_path may be JSON string of paths
            raw = r.get("exposure_path")
            if not raw:
                continue
            try:
                paths = json.loads(raw)
            except Exception:
                continue

            for p in paths:
                # source_id, target_id, path_nodes, path_length, has_countermeasure
                src_id = p.get("source_id")
                tgt_id = p.get("target_id")
                path_nodes = p.get("path_nodes", [])
                path_length = p.get("path_length", len(path_nodes))
                has_cm = p.get("has_countermeasure", False)

                # Resolve names
                src_name = id_to_name.get(src_id) or f"res_{src_id}"
                tgt_name = id_to_name.get(tgt_id) or f"res_{tgt_id}"
                src_node = ExposureMermaidRenderer.sanitize_id(src_name)
                tgt_node = ExposureMermaidRenderer.sanitize_id(tgt_name)

                # Edge label: include path length and countermeasure info
                cm_label = "with CM" if has_cm else "no CM"
                label = f"len={path_length}, {cm_label}"

                # Append the edge line
                edges.append({"line": f"    {src_node} -->|{label}| {tgt_node}", "red": False})

                # If this resource has OpenGrep violations and is direct_exposure, mark edge red
                has_violation = bool(r.get("opengrep_violations") and json.loads(r["opengrep_violations"]))
                if r.get("exposure_level") == "direct_exposure" and has_violation:
                    edges[-1]["red"] = True

        # Append edge lines to diagram
        for e in edges:
            lines.append(e["line"])

        # Add linkStyle entries for red/bold edges
        for i, e in enumerate(edges):
            if e.get("red"):
                # Mermaid linkStyle uses index-based styling
                lines.append(f"    linkStyle {i} stroke:#ff0000,stroke-width:3px")
            else:
                lines.append(f"    linkStyle {i} stroke:#888888,stroke-width:1px")

        # Add styling for nodes
        for r in resources:
            node_id = ExposureMermaidRenderer.sanitize_id(r["resource_name"])
            exposure = r.get("exposure_level", "unknown")
            has_violation = bool(r.get("opengrep_violations") and json.loads(r["opengrep_violations"]))
            style = ExposureMermaidRenderer.get_node_style(r["resource_name"], exposure, has_violation)
            lines.append(f"    {style}")

        return "\n".join(lines)

    @staticmethod
    def render_risk_table(resources: List[dict]) -> str:
        """Render a Markdown table of resources sorted by risk score."""
        if not resources:
            return "No resources analyzed"

        lines = [
            "| Resource | Type | Exposure Level | Risk Score | OpenGrep Violations |",
            "|---|---|---|---|---|",
        ]

        for r in sorted(resources, key=lambda x: x.get("risk_score", 0), reverse=True):
            violations = json.loads(r.get("opengrep_violations", "[]")) if r.get("opengrep_violations") else []
            violation_text = ", ".join([f"`{v['rule_id']}`" for v in violations[:3]]) if violations else "None"
            
            lines.append(
                f"| {r['resource_name']} | {r['resource_type']} | "
                f"{r.get('exposure_level', 'unknown')} | "
                f"{r.get('risk_score', 0):.1f}/10 | {violation_text} |"
            )

        return "\n".join(lines)


class ExposureSummaryGenerator:
    """Generate Markdown summaries from exposure analysis data."""

    def __init__(self, experiment_id: str, output_dir: Optional[Path] = None):
        """Initialize generator."""
        self.experiment_id = experiment_id
        self.output_dir = output_dir or Path("Output/Summary/Cloud")
        self.db_path = db_helpers.DB_PATH
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.db_path), timeout=30)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self) -> None:
        """Close connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def load_exposure_analysis(self) -> List[dict]:
        """Load exposure analysis results."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT * FROM exposure_analysis
            WHERE experiment_id = ?
            ORDER BY provider, risk_score DESC
            """,
            (self.experiment_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def generate_summaries(self) -> Dict[str, Path]:
        """
        Generate per-provider summaries.

        Returns:
            Dict mapping provider → output file path
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results = self.load_exposure_analysis()

        if not results:
            print(f"[!] No exposure analysis results found for experiment {self.experiment_id}")
            return {}

        # Group by provider
        by_provider = defaultdict(list)
        for r in results:
            provider = r.get("provider", "unknown")
            by_provider[provider].append(r)

        output_files = {}

        for provider, resources in by_provider.items():
            # Build id->name mapping by collecting IDs from resources and their exposure paths
            ids = set()
            for r in resources:
                rid = r.get("resource_id")
                if rid:
                    ids.add(rid)
                raw = r.get("exposure_path")
                if raw:
                    try:
                        paths = json.loads(raw)
                        for p in paths:
                            for node in p.get("path_nodes", []):
                                ids.add(node)
                            # also include source/target ids
                            if p.get("source_id"):
                                ids.add(p.get("source_id"))
                            if p.get("target_id"):
                                ids.add(p.get("target_id"))
                    except Exception:
                        pass

            id_to_name = {}
            if ids:
                conn = self.connect()
                placeholders = ",".join(["?" for _ in ids])
                query = f"SELECT id, resource_name FROM resources WHERE id IN ({placeholders})"
                try:
                    rows = conn.execute(query, tuple(ids)).fetchall()
                    for row in rows:
                        id_to_name[row[0]] = row[1]
                except Exception:
                    # fallback: empty mapping
                    id_to_name = {}

            # Generate diagram (pass id mapping for edge annotations)
            diagram = ExposureMermaidRenderer.render_provider_diagram(provider, resources, id_to_name)
            risk_table = ExposureMermaidRenderer.render_risk_table(resources)

            # Count stats
            direct = sum(1 for r in resources if r.get("exposure_level") == "direct_exposure")
            mitigated = sum(1 for r in resources if r.get("exposure_level") == "mitigated")
            isolated = sum(1 for r in resources if r.get("exposure_level") == "isolated")
            with_violations = sum(1 for r in resources if r.get("opengrep_violations") and json.loads(r["opengrep_violations"]))

            # Build markdown
            provider_upper = provider.upper()
            markdown = f"""# Internet Exposure Analysis: {provider_upper}

Generated: {datetime.now().isoformat()}

## Summary

| Metric | Count |
|--------|-------|
| Directly Exposed | {direct} |
| Mitigated | {mitigated} |
| Isolated | {isolated} |
| With OpenGrep Violations | {with_violations} |

## Architecture Diagram

```mermaid
{diagram}
```

## Risk Assessment

{risk_table}

## Legend

- 🔴 **Red** (direct_exposure): Resource is directly reachable from the internet with no intervening security controls
- 🟠 **Orange** (mitigated): Resource is reachable from internet but passes through security controls (WAF, App Gateway, NSG, Firewall)
- 🟢 **Green** (isolated): Resource is isolated from internet (private VPC/subnet, no public routing)
- 🌐 **Entry Points**: Resources providing internet access (IGW, Public IP, endpoints)
- 🛡️ **Countermeasures**: Security controls (WAF, NSG, Firewall, App Gateway)

## Next Steps

- Review directly exposed resources: prioritize patching OpenGrep violations
- Validate countermeasures: ensure WAF/NSG rules are correctly configured
- Document isolated resources: confirm they have no unintended public paths
"""

            # Write file
            output_file = self.output_dir / f"Internet_Exposure_{provider_upper}.md"
            output_file.write_text(markdown)
            output_files[provider] = output_file
            print(f"[+] Generated {provider.upper()} summary: {output_file}")

        return output_files

    def run(self) -> int:
        """Run summary generation."""
        try:
            output_files = self.generate_summaries()
            if output_files:
                print(f"\n[✓] Generated {len(output_files)} summary files")
                return 0
            else:
                print("[!] No summaries generated")
                return 1
        except Exception as e:
            print(f"[✗] Error generating summaries: {e}")
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self.close()


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Render exposure analysis summaries")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--output-dir", type=Path, help="Output directory (default: Output/Summary/Cloud)")
    args = parser.parse_args()

    generator = ExposureSummaryGenerator(args.experiment, args.output_dir)
    return generator.run()


if __name__ == "__main__":
    sys.exit(main())
