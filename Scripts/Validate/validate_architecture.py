#!/usr/bin/env python3
"""Architecture Validation Script

Validates generated architecture diagrams for:
1. Flat hierarchies (resources without proper parent nesting)
2. Missing internet ingress nodes
3. Missing egress documentation
4. Incomplete component detection

Generates validation reports and recommendations for script improvements.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# Add Scripts paths
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Persist"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Context"))

import db_helpers
from external_resource_hierarchy import get_parent_mapping, HIERARCHY_CONFIG
from Scripts.Generate.internet_exposure_detector import InternetExposureDetector


class ArchitectureValidator:
    def __init__(self, experiment_id: str, repo_name: str):
        self.experiment_id = experiment_id
        self.repo_name = repo_name
        self.issues = []
        self.warnings = []
        self.passed_checks = []
        
    def validate_all(self, resources: List[Dict]) -> Dict:
        """Run all validation checks."""
        print(f"🔍 Validating architecture for {self.repo_name}...")
        
        # Check 1: Hierarchy validation
        self.validate_hierarchy(resources)
        
        # Check 2: Internet ingress detection
        self.validate_internet_ingress(resources)
        
        # Check 3: Component completeness
        self.validate_completeness(resources)
        
        # Check 4: Egress detection
        self.validate_egress(resources)
        
        return {
            "critical_issues": [i for i in self.issues if i["severity"] == "CRITICAL"],
            "high_issues": [i for i in self.issues if i["severity"] == "HIGH"],
            "warnings": self.warnings,
            "passed_checks": self.passed_checks,
            "total_resources": len(resources)
        }
    
    def validate_hierarchy(self, resources: List[Dict]):
        """Check for flat hierarchies that should be nested."""
        print("  ├─ Checking hierarchy...")
        
        flat_resources = []
        
        for resource in resources:
            resource_type = resource.get("resource_type")
            parent_id = resource.get("parent_resource_id")
            
            # Check if this resource type should have a parent
            parent_mapping = get_parent_mapping(resource_type)
            
            if parent_mapping and not parent_id:
                # Resource should have parent but doesn't
                flat_resources.append({
                    "resource": resource,
                    "expected_parent": parent_mapping["parent_type"],
                    "parent_field": parent_mapping["parent_field"]
                })
        
        if flat_resources:
            self.issues.append({
                "type": "flat_hierarchy",
                "severity": "HIGH",
                "count": len(flat_resources),
                "resources": flat_resources,
                "title": f"Flat Hierarchy: {len(flat_resources)} resources missing parent",
                "description": "Resources exist without proper parent-child nesting in diagram",
                "recommended_fix": "Update external_resource_hierarchy.py to detect variable references"
            })
            print(f"    ❌ Found {len(flat_resources)} flat resources")
        else:
            self.passed_checks.append("All resources properly nested in hierarchy")
            print("    ✅ Hierarchy looks good")
    
    def validate_internet_ingress(self, resources: List[Dict]):
        """Check if public resources have Internet ingress documented."""
        print("  ├─ Checking internet ingress...")
        
        public_types = InternetExposureDetector.get_public_entry_types()
        property_based_checks = {
            "azurerm_api_management": lambda r: r.get("virtual_network_type") != "Internal",
            "azurerm_application_gateway": lambda r: not r.get("private"),
            "azurerm_app_service": lambda r: not r.get("vnet_integration"),
            "aws_api_gateway_rest_api": lambda r: r.get("endpoint_configuration", {}).get("types") != ["PRIVATE"],
            "aws_instance": lambda r: r.get("associate_public_ip_address"),
        }

        public_resources = []
        has_internet_node = False
        
        for resource in resources:
            resource_type = resource.get("resource_type")
            
            # Check for Internet node
            if resource_type == "internet" or resource.get("resource_name", "").lower() == "internet":
                has_internet_node = True
            
            # Check if resource is public
            if resource_type in property_based_checks and property_based_checks[resource_type](resource):
                public_resources.append(resource)
            elif resource_type in public_types:
                public_resources.append(resource)
        
        if public_resources and not has_internet_node:
            self.issues.append({
                "type": "missing_internet_ingress",
                "severity": "CRITICAL",
                "count": len(public_resources),
                "public_resources": [r.get("resource_name") for r in public_resources],
                "title": "Missing Internet Ingress Node",
                "description": f"{len(public_resources)} publicly accessible resources found but no Internet node in diagram",
                "recommended_fix": "Add Internet node and connections to public resources in generate_diagram.py"
            })
            print(f"    ❌ {len(public_resources)} public resources, no Internet node")
        elif public_resources and has_internet_node:
            self.passed_checks.append(f"Internet ingress properly documented ({len(public_resources)} public resources)")
            print(f"    ✅ Internet ingress documented")
        else:
            self.passed_checks.append("No public internet exposure detected")
            print("    ✅ No public resources (internal only)")
    
    def validate_completeness(self, resources: List[Dict]):
        """Check for expected components based on resource patterns."""
        print("  ├─ Checking component completeness...")
        
        resource_types = {r.get("resource_type") for r in resources}
        
        # Detect repo type
        has_api = any("api" in rt.lower() for rt in resource_types)
        has_queue = any("queue" in rt.lower() or "topic" in rt.lower() for rt in resource_types)
        has_database = any("database" in rt.lower() or "sql" in rt.lower() for rt in resource_types)
        has_compute = any("app_service" in rt.lower() or "lambda" in rt.lower() or "cloud_run" in rt.lower() for rt in resource_types)
        
        missing_components = []
        
        if has_api:
            # API repo should have auth
            has_auth = any("auth" in rt.lower() or "identity" in rt.lower() for rt in resource_types)
            if not has_auth:
                missing_components.append("authentication mechanism")
            
            # Should have monitoring
            has_monitoring = any("insights" in rt.lower() or "cloudwatch" in rt.lower() or "logging" in rt.lower() for rt in resource_types)
            if not has_monitoring:
                missing_components.append("monitoring/logging")
        
        if has_queue:
            # Event-driven should have dead-letter
            has_dlq = any("dead" in r.get("resource_name", "").lower() for r in resources)
            if not has_dlq:
                self.warnings.append({
                    "type": "missing_dlq",
                    "title": "No dead-letter queue detected",
                    "description": "Consider adding dead-letter handling for failed messages"
                })
        
        if missing_components:
            self.warnings.append({
                "type": "incomplete_components",
                "title": f"Missing components: {', '.join(missing_components)}",
                "description": "Expected components not found in resources"
            })
            print(f"    ⚠️  Missing: {', '.join(missing_components)}")
        else:
            self.passed_checks.append("All expected components present")
            print("    ✅ Components look complete")
    
    def validate_egress(self, resources: List[Dict]):
        """Check if external dependencies are documented."""
        print("  ├─ Checking egress documentation...")
        
        # Check for external resource markers
        external_resources = [r for r in resources if r.get("status") == "external"]
        
        if external_resources:
            self.passed_checks.append(f"External dependencies documented ({len(external_resources)} resources)")
            print(f"    ✅ {len(external_resources)} external resources documented")
        else:
            self.warnings.append({
                "type": "no_egress",
                "title": "No external dependencies documented",
                "description": "Repository likely has external dependencies (databases, APIs) that aren't shown"
            })
            print("    ⚠️  No external dependencies found")
    
    def generate_report(self, results: Dict) -> str:
        """Generate markdown validation report."""
        lines = [
            "# Architecture Validation Report",
            f"**Repo:** {self.repo_name}",
            f"**Experiment:** {self.experiment_id}",
            f"**Date:** {datetime.utcnow().isoformat()}",
            f"**Total Resources:** {results['total_resources']}",
            ""
        ]
        
        # Critical issues
        critical = results["critical_issues"]
        if critical:
            lines.append(f"## ❌ Critical Issues ({len(critical)})\n")
            for i, issue in enumerate(critical, 1):
                lines.append(f"### Issue {i}: {issue['title']}")
                lines.append(f"**Severity:** {issue['severity']}")
                lines.append(f"**Description:** {issue['description']}")
                lines.append(f"\n**Recommended Fix:** {issue.get('recommended_fix', 'See details above')}\n")
        
        # High issues
        high = results["high_issues"]
        if high:
            lines.append(f"## ⚠️  High Priority Issues ({len(high)})\n")
            for i, issue in enumerate(high, 1):
                lines.append(f"### Issue {i}: {issue['title']}")
                lines.append(f"**Description:** {issue['description']}")
                lines.append(f"\n**Recommended Fix:** {issue.get('recommended_fix', 'See details above')}\n")
        
        # Warnings
        warnings = results["warnings"]
        if warnings:
            lines.append(f"## ⚠️  Warnings ({len(warnings)})\n")
            for warning in warnings:
                lines.append(f"- **{warning['title']}:** {warning['description']}")
            lines.append("")
        
        # Passed checks
        passed = results["passed_checks"]
        if passed:
            lines.append(f"## ✅ Passed Checks ({len(passed)})\n")
            for check in passed:
                lines.append(f"- {check}")
            lines.append("")
        
        # Summary
        lines.append("## Summary\n")
        lines.append(f"- ❌ Critical: {len(critical)}")
        lines.append(f"- ⚠️  High: {len(high)}")
        lines.append(f"- ⚠️  Warnings: {len(warnings)}")
        lines.append(f"- ✅ Passed: {len(passed)}")
        
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Validate architecture diagram")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument("--output", help="Output file for report (default: validation_report.md)")
    args = parser.parse_args()
    
    output_file = args.output or f"Output/Learning/experiments/{args.experiment}/validation_report.md"
    
    # Load resources from database
    with db_helpers.get_db_connection() as conn:
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
            (args.experiment, args.repo)
        ).fetchone()
        
        if not repo_row:
            print(f"ERROR: Repository {args.repo} not found in experiment {args.experiment}")
            sys.exit(1)
        
        repo_id = repo_row[0]
        
        # Get all resources for this repo
        cursor = conn.execute("""
            SELECT id, resource_type, resource_name, parent_resource_id, 
                   source_file, status, provider
            FROM resources
            WHERE experiment_id = ? AND repo_id = ?
        """, (args.experiment, repo_id))
        
        columns = [d[0] for d in cursor.description]
        resources = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    if not resources:
        print(f"WARNING: No resources found for {args.repo}")
        sys.exit(0)
    
    # Run validation
    validator = ArchitectureValidator(args.experiment, args.repo)
    results = validator.validate_all(resources)
    
    # Generate report
    report = validator.generate_report(results)
    
    # Write to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    
    print(f"\n📄 Validation report written to: {output_path}")
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"Summary: {len(results['critical_issues'])} critical, {len(results['high_issues'])} high, {len(results['warnings'])} warnings")
    print(f"{'='*60}")
    
    # Exit code based on severity
    if results["critical_issues"]:
        sys.exit(2)  # Critical issues found
    elif results["high_issues"]:
        sys.exit(1)  # High priority issues found
    else:
        sys.exit(0)  # All good


if __name__ == "__main__":
    main()
