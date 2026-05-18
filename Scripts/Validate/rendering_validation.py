#!/usr/bin/env python3
"""Rendering validation module for diagram review skill.

Provides four validation checks:
1. Icon Availability - checks if SVG files exist for resource types
2. Icon Mapping Semantics - validates resource type → icon mappings
3. Orphan Root Cause Analysis - classifies orphans by root cause
4. Asset Validation Report - generates comprehensive asset status

These validations catch rendering gaps, semantic errors, and provide
root cause analysis for orphaned nodes.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ICONS_ROOT = REPO_ROOT / "web" / "static" / "assets" / "icons"

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    issue_type: str
    severity: str
    resource_type: str
    details: dict[str, Any]


@dataclass
class IconGap:
    """Missing icon file."""
    resource_type: str
    provider: str
    category: str
    icon_name: str
    icon_path: str


@dataclass
class MappingError:
    """Semantic error in icon mapping."""
    resource_type: str
    wrong_icon: str
    correct_icon: str
    reason: str


@dataclass
class OrphanDiagnosis:
    """Root cause analysis for an orphan node."""
    node_id: str
    resource_type: str
    root_cause: str  # RENDERING_GAP, MAPPING_ERROR, REAL_ORPHAN
    icon_exists: bool
    mapping_ok: bool
    severity: str


# Load AWS icon mappings from icon_resolver.py
def _load_aws_mappings_from_source() -> dict[str, tuple[str, str]]:
    """Load AWS mappings from icon_resolver.py source file."""
    mappings = {}
    icon_resolver_path = REPO_ROOT / "Scripts" / "Generate" / "icon_resolver.py"
    
    if not icon_resolver_path.exists():
        logger.warning(f"icon_resolver.py not found at {icon_resolver_path}")
        return mappings
    
    try:
        content = icon_resolver_path.read_text()
        
        # Find AWS_RESOURCE_TYPE_TO_ICON section
        start = content.find("AWS_RESOURCE_TYPE_TO_ICON = {")
        if start == -1:
            return mappings
        
        # Find the closing brace
        start = content.find("{", start) + 1
        depth = 1
        pos = start
        while depth > 0 and pos < len(content):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
            pos += 1
        
        mapping_text = content[start:pos-1]
        
        # Parse entries: 'resource_type': ('category', 'icon_name'),
        import re
        pattern = r"'([^']+)':\s*\(\s*'([^']+)',\s*'([^']+)'\s*\)"
        for match in re.finditer(pattern, mapping_text):
            resource_type, category, icon_name = match.groups()
            mappings[resource_type] = (category, icon_name)
        
        logger.info(f"Loaded {len(mappings)} AWS resource mappings from icon_resolver.py")
        
    except Exception as e:
        logger.warning(f"Failed to load mappings from icon_resolver.py: {e}")
    
    return mappings


def _check_icon_file_exists(category: str, icon_name: str, provider: str = "aws") -> bool:
    """Check if icon file exists, checking both base and 64/ subdirectories."""
    # Check base directory
    base_path = ICONS_ROOT / provider / category / f"{icon_name}.svg"
    if base_path.exists():
        return True
    
    # Check 64/ subdirectory
    subdir_path = ICONS_ROOT / provider / category / "64" / f"{icon_name}.svg"
    if subdir_path.exists():
        return True
    
    return False


AWS_RESOURCE_TYPE_TO_ICON = _load_aws_mappings_from_source()


# Semantic validation rules for icon mappings
SERVICE_BUS_ICON = "service-bus"
SERVICE_BUS_NAMESPACE_TYPE = "azurerm_service_bus"
SERVICE_BUS_CHILD_TYPES = {
    "azurerm_servicebus_queue": "queue",
    "azurerm_servicebus_topic": "topic",
    "azurerm_servicebus_subscription": "subscription",
}

SEMANTIC_MAPPING_CHECKS = [
    # (resource_type, wrong_icon_name, correct_icon_name, reason)
    ("aws_route_table", "route53", "route-table", "route53 is DNS service, not routing"),
    ("aws_security_group", "network-firewall", "security-group", "network-firewall is different service"),
    (SERVICE_BUS_NAMESPACE_TYPE, "system-topic", SERVICE_BUS_ICON, "Service Bus namespace should use the Service Bus icon, not a topic icon"),
]


def validate_icon_availability(
    provider: str = "aws",
    mappings: dict[str, tuple[str, str]] | None = None,
) -> list[IconGap]:
    """Check if SVG icon files exist for all resource types.

    Args:
        provider: Cloud provider ('aws', 'azure', 'gcp')
        mappings: Resource type → (category, icon_name) mapping dict

    Returns:
        List of missing icon files per resource type
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    gaps = []
    for resource_type, (category, icon_name) in mappings.items():
        # Check both base and 64/ subdirectories
        if not _check_icon_file_exists(category, icon_name, provider):
            icon_path = ICONS_ROOT / provider / category / f"{icon_name}.svg"
            gaps.append(
                IconGap(
                    resource_type=resource_type,
                    provider=provider,
                    category=category,
                    icon_name=icon_name,
                    icon_path=str(icon_path),
                )
            )

    return sorted(gaps, key=lambda g: g.resource_type)


def validate_icon_mapping_semantics(mappings: dict[str, tuple[str, str]] | None = None) -> list[MappingError]:
    """Check if icon mappings are semantically correct.

    Args:
        mappings: Resource type → (category, icon_name) mapping dict

    Returns:
        List of semantic mapping errors
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    errors = []
    for resource_type, wrong_icon, correct_icon, reason in SEMANTIC_MAPPING_CHECKS:
        if resource_type in mappings:
            _, mapped_icon = mappings[resource_type]
            if mapped_icon == wrong_icon:
                errors.append(
                    MappingError(
                        resource_type=resource_type,
                        wrong_icon=wrong_icon,
                        correct_icon=correct_icon,
                        reason=reason,
                    )
                )

    # Service Bus child resources should not reuse the generic namespace icon.
    for child_type, child_label in SERVICE_BUS_CHILD_TYPES.items():
        if child_type not in mappings:
            continue
        _, mapped_icon = mappings[child_type]
        if mapped_icon == SERVICE_BUS_ICON:
            errors.append(
                MappingError(
                    resource_type=child_type,
                    wrong_icon=SERVICE_BUS_ICON,
                    correct_icon=f"{child_label}-specific icon",
                    reason=f"Service Bus {child_label}s should not use the generic Service Bus namespace icon",
                )
            )

    return sorted(errors, key=lambda e: e.resource_type)


def infer_resource_type_from_node_id(node_id: str, code_context: str = "") -> str | None:
    """Infer resource type from node ID and diagram code context.

    Args:
        node_id: Node identifier from diagram
        code_context: Relevant Terraform/CloudFormation code

    Returns:
        Inferred resource type or None if cannot determine
    """
    node_lower = node_id.lower()

    # Try exact matches in known mappings
    for resource_type in AWS_RESOURCE_TYPE_TO_ICON.keys():
        resource_short = resource_type.replace("aws_", "").lower()
        if resource_short == node_lower or resource_short in node_lower:
            return resource_type

    # Try pattern matching with heuristics
    patterns = [
        ("lambda", "aws_lambda_function"),
        ("function", "aws_lambda_function"),
        ("s3|bucket", "aws_s3_bucket"),
        ("dynamo|table", "aws_dynamodb_table"),
        ("instance|ec2|compute", "aws_instance"),
        ("route.*table|rt", "aws_route_table"),
        ("security.*group|sg", "aws_security_group"),
        ("api.*gateway|gateway", "aws_api_gateway_rest_api"),
        ("load.*balancer|alb|elb|lb", "aws_lb"),
        ("rds|database|db", "aws_db_instance"),
        ("sqs|queue", "aws_sqs_queue"),
        ("sns|topic", "aws_sns_topic"),
        ("vpc", "aws_vpc"),
        ("subnet", "aws_subnet"),
        ("iam|role|policy", "aws_iam_role"),
    ]

    for pattern, resource_type in patterns:
        if re.search(pattern, node_lower, re.IGNORECASE):
            return resource_type

    return None


def check_icon_exists(
    resource_type: str,
    provider: str = "aws",
    mappings: dict[str, tuple[str, str]] | None = None,
) -> bool:
    """Check if icon file exists for resource type.

    Args:
        resource_type: AWS resource type (e.g., 'aws_instance')
        provider: Cloud provider
        mappings: Resource type mapping dict

    Returns:
        True if icon exists, False otherwise
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    if resource_type not in mappings:
        return False

    category, icon_name = mappings[resource_type]
    return _check_icon_file_exists(category, icon_name, provider)


def check_mapping_semantics(
    resource_type: str,
    mappings: dict[str, tuple[str, str]] | None = None,
) -> bool:
    """Check if resource type mapping is semantically correct.

    Args:
        resource_type: AWS resource type
        mappings: Resource type mapping dict

    Returns:
        True if mapping is correct, False if semantic error
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    for rt, wrong_icon, correct_icon, _ in SEMANTIC_MAPPING_CHECKS:
        if resource_type == rt:
            _, mapped_icon = mappings.get(rt, ("", ""))
            if mapped_icon == wrong_icon:
                return False

    return True


def validate_rendering_pipeline(
    orphan_nodes: list[str],
    code_context: str = "",
    provider: str = "aws",
    mappings: dict[str, tuple[str, str]] | None = None,
) -> dict[str, OrphanDiagnosis]:
    """Classify orphan nodes by root cause.

    For each orphan, determines if it's caused by:
    - RENDERING_GAP: Icon file missing
    - MAPPING_ERROR: Semantic mapping error
    - REAL_ORPHAN: Actual connectivity issue

    Args:
        orphan_nodes: List of orphan node IDs
        code_context: Relevant Terraform/CloudFormation code
        provider: Cloud provider
        mappings: Resource type mapping dict

    Returns:
        Dict mapping node_id → OrphanDiagnosis
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    diagnosis = {}

    for node_id in orphan_nodes:
        # Infer resource type
        resource_type = infer_resource_type_from_node_id(node_id, code_context)

        if resource_type is None:
            # Cannot determine type - mark as real orphan
            diagnosis[node_id] = OrphanDiagnosis(
                node_id=node_id,
                resource_type="unknown",
                root_cause="REAL_ORPHAN",
                icon_exists=False,
                mapping_ok=True,
                severity="INFO",
            )
            continue

        # Check 1: Icon file exists?
        icon_exists = check_icon_exists(resource_type, provider, mappings)

        # Check 2: Mapping is correct?
        mapping_ok = check_mapping_semantics(resource_type, mappings)

        # Classify root cause
        if not icon_exists:
            root_cause = "RENDERING_GAP"
            severity = "HIGH"
        elif not mapping_ok:
            root_cause = "MAPPING_ERROR"
            severity = "CRITICAL"
        else:
            root_cause = "REAL_ORPHAN"
            severity = "INFO"

        diagnosis[node_id] = OrphanDiagnosis(
            node_id=node_id,
            resource_type=resource_type,
            root_cause=root_cause,
            icon_exists=icon_exists,
            mapping_ok=mapping_ok,
            severity=severity,
        )

    return diagnosis


def generate_asset_validation_report(
    provider: str = "aws",
    mappings: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Generate comprehensive asset validation report.

    Args:
        provider: Cloud provider
        mappings: Resource type mapping dict

    Returns:
        Formatted report text
    """
    if mappings is None:
        mappings = AWS_RESOURCE_TYPE_TO_ICON

    report_lines = []

    # Check icon availability
    gaps = validate_icon_availability(provider, mappings)
    total_resources = len(mappings)
    available_icons = total_resources - len(gaps)
    coverage_pct = (available_icons / total_resources * 100) if total_resources > 0 else 0

    report_lines.append("\n" + "=" * 80)
    report_lines.append("ASSET VALIDATION REPORT")
    report_lines.append("=" * 80)

    report_lines.append(f"\n📦 Icon Coverage: {available_icons}/{total_resources} ({coverage_pct:.1f}%)")

    if gaps:
        report_lines.append(f"\n⚠️  Missing {len(gaps)} icons:")
        by_category = {}
        for gap in gaps:
            by_category.setdefault(gap.category, []).append(gap.resource_type)
        for category in sorted(by_category.keys()):
            report_lines.append(f"\n   {category}:")
            for rt in sorted(by_category[category])[:5]:
                report_lines.append(f"     - {rt}")
            if len(by_category[category]) > 5:
                report_lines.append(f"     ... and {len(by_category[category]) - 5} more")

    # Check icon mapping semantics
    errors = validate_icon_mapping_semantics(mappings)
    if errors:
        report_lines.append(f"\n🔴 Mapping Errors: {len(errors)} semantic mismatches")
        for error in errors:
            report_lines.append(f"\n   {error.resource_type}:")
            report_lines.append(f"     Wrong:  {error.wrong_icon}")
            report_lines.append(f"     Correct: {error.correct_icon}")
            report_lines.append(f"     Reason: {error.reason}")

    # Overall status
    report_lines.append("\n" + "-" * 80)
    if gaps or errors:
        report_lines.append("⚠️  FAILED - Rendering issues present")
    else:
        report_lines.append("✅ PASSED - All assets validated")
    report_lines.append("=" * 80 + "\n")

    return "\n".join(report_lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Demo
    print("Icon Availability Validation:")
    gaps = validate_icon_availability()
    print(f"  Found {len(gaps)} missing icons")

    print("\nIcon Mapping Semantics Validation:")
    errors = validate_icon_mapping_semantics()
    print(f"  Found {len(errors)} semantic errors")

    print("\nAsset Validation Report:")
    print(generate_asset_validation_report())

    print("Orphan Root Cause Analysis (demo):")
    demo_orphans = ["goat_instance", "goat_rt", "AWS_Goat_sg"]
    diagnosis = validate_rendering_pipeline(demo_orphans)
    for node_id, diag in diagnosis.items():
        print(f"  {node_id}: {diag.root_cause}")
