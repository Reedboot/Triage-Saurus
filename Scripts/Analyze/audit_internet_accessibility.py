#!/usr/bin/env python3
"""Comprehensive internet accessibility audit script.

This script can be run on any experiment/scan to:
1. Compute internet accessibility for all resources
2. Generate a detailed audit report
3. Compare accessibility to previous scans
4. Identify changes and new exposures

Usage:
    python audit_internet_accessibility.py [--experiment-id <id>] [--compare-to <prev-id>] [--output report.md]
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Add imports
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(ROOT / "Persist"))
sys.path.insert(0, str(ROOT / "Generate"))
sys.path.insert(0, str(ROOT / "Analyze"))

from db_helpers import get_db_connection
from internet_accessibility_analyzer import InternetAccessibilityAnalyzer, ensure_schema
from internet_accessibility_ui import InternetAccessibilityHelper, query_accessibility_metrics


class InternetAccessibilityAudit:
    """Comprehensive audit of internet accessibility."""

    def __init__(self, experiment_id: str):
        """Initialize audit."""
        self.experiment_id = experiment_id
        self.report_lines: list = []
        self.metrics: dict = {}
        self.issues: list = []

    def run_full_analysis(self) -> None:
        """Run the full internet accessibility analysis."""
        print(f"[*] Starting internet accessibility audit for {self.experiment_id}")

        # Ensure schema
        with get_db_connection() as conn:
            ensure_schema(conn)

        # Run analyzer
        analyzer = InternetAccessibilityAnalyzer(self.experiment_id)
        analyzer.run()

        # Query metrics
        self.metrics = query_accessibility_metrics(self.experiment_id)
        print(f"[+] Analysis complete")

    def generate_markdown_report(self) -> str:
        """Generate markdown report."""
        lines = []

        # Header
        lines.append("# Internet Accessibility Audit Report")
        lines.append("")
        lines.append(f"**Experiment ID:** {self.experiment_id}")
        lines.append(f"**Generated:** {datetime.now().isoformat()}")
        lines.append("")

        # Executive Summary
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(
            f"- **Total Resources:** {self.metrics.get('total_resources', 0)}"
        )
        lines.append(
            f"- **Internet-Accessible:** {self.metrics.get('internet_accessible_count', 0)} "
            f"({self.metrics.get('internet_accessible_percentage', 0):.1f}%)"
        )
        lines.append("")

        # Access Methods
        lines.append("## Access Methods")
        lines.append("")
        methods = self.metrics.get("by_access_method", {})
        lines.append(
            f"- **Via Public IP:** {methods.get('via_public_ip', 0)} resources 🔴 (CRITICAL)"
        )
        lines.append(
            f"- **Via Public Endpoint:** {methods.get('via_public_endpoint', 0)} resources 🟠 (HIGH)"
        )
        lines.append(
            f"- **Via Managed Identity:** {methods.get('via_managed_identity', 0)} resources 🟡 (MEDIUM)"
        )
        lines.append("")

        # Detailed Asset List
        lines.append("## Detailed Internet-Accessible Resources")
        lines.append("")

        helper = InternetAccessibilityHelper(self.experiment_id)
        helper.load()
        accessible = helper.get_internet_accessible_resources()

        if accessible:
            lines.append("### By Risk Level")
            lines.append("")

            # Critical (Public IP)
            critical = [
                (rid, info)
                for rid, info in accessible
                if info.get("via_public_ip")
            ]
            if critical:
                lines.append("#### 🔴 CRITICAL - Direct Public IP Access")
                lines.append("")
                for rid, info in critical:
                    lines.extend(_format_resource_detail(info))
                lines.append("")

            # High (Public Endpoint)
            high = [
                (rid, info)
                for rid, info in accessible
                if info.get("via_public_endpoint")
            ]
            if high:
                lines.append("#### 🟠 HIGH - Public Endpoint Access")
                lines.append("")
                for rid, info in high:
                    lines.extend(_format_resource_detail(info))
                lines.append("")

            # Medium (Managed Identity)
            medium = [
                (rid, info)
                for rid, info in accessible
                if info.get("via_managed_identity")
            ]
            if medium:
                lines.append("#### 🟡 MEDIUM - Managed Identity Access")
                lines.append("")
                for rid, info in medium:
                    lines.extend(_format_resource_detail(info))
                lines.append("")

        else:
            lines.append("✅ **No internet-accessible resources detected**")
            lines.append("")

        # Assessment & Recommendations
        lines.append("## Security Assessment")
        lines.append("")

        risk_score = _calculate_risk_score(self.metrics)
        risk_level = _risk_level_from_score(risk_score)

        lines.append(f"**Overall Risk Level:** {risk_level}")
        lines.append(f"**Risk Score:** {risk_score:.1f}/100")
        lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        lines.append("")
        lines.extend(_generate_recommendations(self.metrics))
        lines.append("")

        # Technical Details
        lines.append("## Technical Analysis")
        lines.append("")
        lines.append("### Asset Paths")
        lines.append("")

        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT 
                    resource_name, shortest_path_distance, entry_point,
                    path_data, auth_level
                FROM resource_internet_accessibility
                WHERE experiment_id = ? AND is_internet_accessible = 1
                ORDER BY shortest_path_distance
                """,
                [self.experiment_id],
            ).fetchall()

            for row in rows:
                path_data = json.loads(row["path_data"]) if row["path_data"] else {}
                path_nodes = path_data.get("path_nodes", [])
                distance = row["shortest_path_distance"]
                auth = row["auth_level"]

                if path_nodes:
                    path_str = " → ".join(path_nodes)
                    lines.append(f"- **{row['resource_name']}**: {path_str}")
                    lines.append(f"  - Distance: {distance} hops")
                    lines.append(f"  - Auth Level: {auth}")
                    lines.append(f"  - Entry Point: {row['entry_point']}")
                    lines.append("")

        return "\n".join(lines)

    def generate_json_report(self) -> str:
        """Generate JSON report."""
        helper = InternetAccessibilityHelper(self.experiment_id)
        helper.load()

        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM resource_internet_accessibility
                WHERE experiment_id = ?
                """,
                [self.experiment_id],
            ).fetchall()

            resources = []
            for row in rows:
                try:
                    path_data = json.loads(row["path_data"]) if row["path_data"] else None
                except Exception:
                    path_data = None

                resources.append(
                    {
                        "resource_id": row["resource_id"],
                        "resource_name": row["resource_name"],
                        "resource_type": row["resource_type"],
                        "is_internet_accessible": row["is_internet_accessible"] == 1,
                        "via_public_ip": row["via_public_ip"] == 1,
                        "via_public_endpoint": row["via_public_endpoint"] == 1,
                        "via_managed_identity": row["via_managed_identity"] == 1,
                        "shortest_path_distance": row["shortest_path_distance"],
                        "entry_point": row["entry_point"],
                        "auth_level": row["auth_level"],
                        "path": path_data,
                    }
                )

        report = {
            "experiment_id": self.experiment_id,
            "generated_at": datetime.now().isoformat(),
            "metrics": self.metrics,
            "resources": resources,
        }

        return json.dumps(report, indent=2)


def _format_resource_detail(info: dict) -> list:
    """Format a single resource detail for report."""
    lines = []
    name = info.get("resource_name", "unknown")
    rtype = info.get("resource_type", "unknown")
    distance = info.get("shortest_path_distance", "?")
    entry = info.get("entry_point", "?")
    auth = info.get("auth_level", "?")

    lines.append(f"- **{name}** ({rtype})")
    lines.append(f"  - Entry Point: {entry}")
    lines.append(f"  - Distance: {distance} hops")
    lines.append(f"  - Auth Level: {auth}")

    path_data = info.get("path_data")
    if path_data and isinstance(path_data, dict):
        path_nodes = path_data.get("path_nodes", [])
        if path_nodes:
            path_str = " → ".join(path_nodes)
            lines.append(f"  - Path: {path_str}")

    lines.append("")
    return lines


def _calculate_risk_score(metrics: dict) -> float:
    """Calculate overall risk score (0-100)."""
    score = 0.0

    total = metrics.get("total_resources", 1)
    accessible = metrics.get("internet_accessible_count", 0)

    if total == 0:
        return 0.0

    # Base score from percentage accessible
    accessible_pct = (accessible / total) * 100
    score += accessible_pct * 0.4  # 40% weight

    # Additional weight for critical access methods
    methods = metrics.get("by_access_method", {})
    public_ip_count = methods.get("via_public_ip", 0)
    public_ep_count = methods.get("via_public_endpoint", 0)

    # Each public IP adds significant risk
    score += public_ip_count * 15
    score += public_ep_count * 5

    # Cap at 100
    return min(score, 100.0)


def _risk_level_from_score(score: float) -> str:
    """Map risk score to level."""
    if score >= 80:
        return "🔴 CRITICAL"
    elif score >= 60:
        return "🟠 HIGH"
    elif score >= 40:
        return "🟡 MEDIUM"
    elif score >= 20:
        return "🟢 LOW"
    else:
        return "✅ MINIMAL"


def _generate_recommendations(metrics: dict) -> list:
    """Generate recommendations based on metrics."""
    lines = []

    methods = metrics.get("by_access_method", {})
    public_ip = methods.get("via_public_ip", 0)
    public_ep = methods.get("via_public_endpoint", 0)
    identity = methods.get("via_managed_identity", 0)

    if public_ip > 0:
        lines.append(
            f"1. **URGENT:** Remove or restrict {public_ip} public IP(s)"
        )
        lines.append(
            "   - Consider using private endpoints or VPN access instead"
        )
        lines.append("   - Implement strict NSG/Security Group rules")
        lines.append("")

    if public_ep > 0:
        lines.append(
            f"2. **HIGH:** Secure {public_ep} public endpoint(s)"
        )
        lines.append("   - Enforce authentication on all public APIs")
        lines.append("   - Implement rate limiting and WAF rules")
        lines.append("   - Use TLS/HTTPS exclusively")
        lines.append("")

    if identity > 0:
        lines.append(f"3. **MEDIUM:** Review {identity} managed identity access(es)")
        lines.append("   - Implement least-privilege RBAC roles")
        lines.append("   - Add audit logging for identity usage")
        lines.append("")

    if public_ip == 0 and public_ep == 0 and identity == 0:
        lines.append("✅ **No obvious internet exposure detected**")
        lines.append("   - Continue to monitor for misconfigurations")
        lines.append("   - Regularly run this audit on new scans")

    return lines


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Comprehensive internet accessibility audit"
    )
    parser.add_argument(
        "--experiment-id",
        required=True,
        help="Experiment ID to audit",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file for report (default: stdout)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Report format",
    )

    args = parser.parse_args()

    audit = InternetAccessibilityAudit(args.experiment_id)
    audit.run_full_analysis()

    if args.format == "markdown":
        report = audit.generate_markdown_report()
    else:
        report = audit.generate_json_report()

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report)
        print(f"[+] Report written to {output_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
