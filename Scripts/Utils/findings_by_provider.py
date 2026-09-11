"""
Helper module to organize findings by cloud provider.
"""

from typing import Dict, List, Any, Optional
from collections import defaultdict


def get_provider_for_finding(finding: Dict[str, Any]) -> str:
    """
    Extract cloud provider from finding.
    
    Tries multiple approaches:
    1. Direct 'provider' field from resource join
    2. Infer from resource_type (e.g., 'azurerm_*' -> azure)
    3. Infer from finding context/metadata
    4. Default to 'unknown'
    """
    # Direct provider from resource (if joined)
    if 'provider' in finding and finding['provider']:
        return finding['provider'].lower()
    
    # Infer from resource type
    if 'resource_type' in finding and finding['resource_type']:
        rtype = finding['resource_type'].lower()
        if rtype.startswith('azurerm_'):
            return 'azure'
        elif rtype.startswith('aws_'):
            return 'aws'
        elif rtype.startswith('google_'):
            return 'gcp'
        elif rtype.startswith('oci_'):
            return 'oci'
        elif rtype.startswith('alicloud_'):
            return 'alicloud'
    
    # Infer from finding context (if available)
    if 'finding_context' in finding and finding['finding_context']:
        ctx = str(finding['finding_context']).lower()
        if 'azure' in ctx or 'azurerm' in ctx:
            return 'azure'
        elif 'aws' in ctx:
            return 'aws'
        elif 'gcp' in ctx or 'google' in ctx:
            return 'gcp'
        elif 'oci' in ctx:
            return 'oci'
    
    # Infer from source file path
    if 'source_file' in finding and finding['source_file']:
        src = str(finding['source_file']).lower()
        if 'azure' in src or 'azurerm' in src:
            return 'azure'
        elif 'aws' in src:
            return 'aws'
        elif 'gcp' in src or 'google' in src:
            return 'gcp'
        elif 'oci' in src:
            return 'oci'
    
    return 'unknown'


def provider_display_name(provider: str) -> str:
    """Get friendly display name for provider."""
    names = {
        'aws': '☁️ AWS',
        'azure': '☁️ Azure',
        'gcp': '☁️ Google Cloud',
        'oci': '☁️ Oracle Cloud',
        'alicloud': '☁️ Alibaba Cloud',
        'unknown': '❓ Unknown Provider',
    }
    return names.get(provider.lower(), f'☁️ {provider}')


def provider_color(provider: str) -> str:
    """Get color code for provider."""
    colors = {
        'aws': '#FF9900',      # AWS orange
        'azure': '#0078D4',    # Azure blue
        'gcp': '#EA4335',      # GCP red
        'oci': '#F80000',      # Oracle red
        'alicloud': '#FF6700', # Alibaba orange
        'unknown': '#999999',  # Gray
    }
    return colors.get(provider.lower(), '#999999')


def group_findings_by_provider(
    findings: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group findings by cloud provider.
    
    Returns dict: {provider_name: [finding, finding, ...]}
    Sorted by provider name, findings within each provider keep original order.
    """
    groups = defaultdict(list)
    
    for finding in findings:
        provider = get_provider_for_finding(finding)
        groups[provider].append(finding)
    
    # Sort by provider (known providers first, unknown last)
    provider_order = ['aws', 'azure', 'gcp', 'oci', 'alicloud']
    sorted_groups = {}
    
    for provider in provider_order:
        if provider in groups:
            sorted_groups[provider] = groups[provider]
    
    # Add any remaining providers
    for provider in sorted(groups.keys()):
        if provider not in sorted_groups:
            sorted_groups[provider] = groups[provider]
    
    return sorted_groups


def count_findings_by_provider_and_severity(
    findings: List[Dict[str, Any]]
) -> Dict[str, Dict[str, int]]:
    """
    Count findings by provider and severity.
    
    Returns: {provider: {'CRITICAL': 5, 'HIGH': 3, ...}}
    """
    counts = defaultdict(lambda: defaultdict(int))
    
    for finding in findings:
        provider = get_provider_for_finding(finding)
        severity = (finding.get('base_severity') or finding.get('severity') or 'INFO').upper()
        counts[provider][severity] += 1
    
    return dict(counts)


def get_provider_summary(findings: List[Dict[str, Any]]) -> str:
    """
    Generate text summary of findings by provider.
    
    Example: "AWS: 5 Critical, 12 High | Azure: 3 Critical, 2 Medium"
    """
    counts = count_findings_by_provider_and_severity(findings)
    summaries = []
    
    severity_order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
    
    for provider in ['aws', 'azure', 'gcp', 'oci', 'alicloud']:
        if provider not in counts:
            continue
        
        provider_counts = counts[provider]
        parts = []
        for severity in severity_order:
            if severity in provider_counts:
                count = provider_counts[severity]
                parts.append(f"{count} {severity.capitalize()}")
        
        if parts:
            summaries.append(f"{provider_display_name(provider).split()[-1]}: {', '.join(parts)}")
    
    # Add unknown if present
    if 'unknown' in counts:
        provider_counts = counts['unknown']
        parts = []
        for severity in severity_order:
            if severity in provider_counts:
                count = provider_counts[severity]
                parts.append(f"{count} {severity.capitalize()}")
        if parts:
            summaries.append(f"Unknown: {', '.join(parts)}")
    
    return " | ".join(summaries) if summaries else "No findings"
