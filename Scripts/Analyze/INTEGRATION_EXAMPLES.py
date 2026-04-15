#!/usr/bin/env python3
"""Quick integration guide and examples for internet accessibility framework.

This file shows how to integrate the new internet accessibility framework
into existing code (web UI, diagrams, etc.).
"""

# ============================================================================
# EXAMPLE 1: Integrate with Hierarchical Diagram Generator
# ============================================================================

def example_diagram_integration():
    """Show how to enrich diagram nodes with accessibility info."""
    
    # In generate_hierarchical_diagram.py, modify the generate() method:
    
    from Scripts.Generate.internet_accessibility_ui import (
        InternetAccessibilityHelper,
        enrich_resource_with_accessibility
    )
    
    experiment_id = "my-scan"
    helper = InternetAccessibilityHelper(experiment_id)
    helper.load()
    
    # Enrich each resource with accessibility info
    for resource in self.resources:
        resource = enrich_resource_with_accessibility(resource, helper)
    
    # Now resources have:
    # - resource['_is_internet_accessible']: bool
    # - resource['_accessibility_badge']: str (e.g., "📍 Public IP (0 hops)")
    # - resource['_internet_exposed_color']: str (e.g., "#ff0000")
    
    # Use in render_node():
    # if resource.get('_is_internet_accessible'):
    #     label = f"{resource['resource_name']}\n{resource['_accessibility_badge']}"


# ============================================================================
# EXAMPLE 2: Create Traffic Tab for Web UI
# ============================================================================

def example_web_ui_traffic_tab(experiment_id):
    """Show how to implement a Traffic tab for the web UI."""
    
    from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics
    from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper
    from db_helpers import get_db_connection
    import json
    
    # Query metrics
    metrics = query_accessibility_metrics(experiment_id)
    
    # Get detailed accessibility info
    helper = InternetAccessibilityHelper(experiment_id)
    helper.load()
    accessible = helper.get_internet_accessible_resources()
    
    # Build traffic data structure for template
    traffic_data = {
        # Overview metrics
        'summary': {
            'total_resources': metrics['total_resources'],
            'equipped_count': metrics['internet_accessible_count'],
            'exposed_percentage': metrics['internet_accessible_percentage'],
            'risk_score': _calculate_risk_score(metrics),
            'risk_level': _risk_level_from_score(_calculate_risk_score(metrics)),
        },
        
        # Access method breakdown
        'by_method': metrics['by_access_method'],
        
        # Resources by criticality
        'critical': {  # Direct public IP - 🔴
            'count': metrics['by_access_method']['via_public_ip'],
            'resources': [
                {
                    'name': info['resource_name'],
                    'type': info['resource_type'],
                    'auth': info['auth_level'],
                    'entry_point': info['entry_point'],
                    'distance': info['shortest_path_distance'],
                }
                for _, info in accessible
                if info.get('via_public_ip')
            ]
        },
        
        'high': {  # Public endpoint - 🟠
            'count': metrics['by_access_method']['via_public_endpoint'],
            'resources': [
                {
                    'name': info['resource_name'],
                    'type': info['resource_type'],
                    'auth': info['auth_level'],
                    'entry_point': info['entry_point'],
                    'distance': info['shortest_path_distance'],
                }
                for _, info in accessible
                if info.get('via_public_endpoint')
            ]
        },
        
        'medium': {  # Via managed identity - 🟡
            'count': metrics['by_access_method']['via_managed_identity'],
            'resources': [
                {
                    'name': info['resource_name'],
                    'type': info['resource_type'],
                    'auth': info['auth_level'],
                    'entry_point': info['entry_point'],
                    'distance': info['shortest_path_distance'],
                }
                for _, info in accessible
                if info.get('via_managed_identity')
            ]
        },
        
        # Detailed path information
        'paths': [
            {
                'resource': info['resource_name'],
                'entry_point': info['entry_point'],
                'distance': info['shortest_path_distance'],
                'auth_level': info['auth_level'],
                'path_nodes': info['path_data']['path_nodes'] if info['path_data'] else [],
                'badge': helper.get_accessibility_badge(rid),
                'color': helper.get_risk_color(rid),
            }
            for rid, info in accessible
        ]
    }
    
    return traffic_data


# ============================================================================
# EXAMPLE 3: Generate Audit Report
# ============================================================================

def example_generate_audit_report(experiment_id, output_file=None):
    """Show how to generate audit reports."""
    
    from Scripts.Analyze.audit_internet_accessibility import InternetAccessibilityAudit
    
    audit = InternetAccessibilityAudit(experiment_id)
    audit.run_full_analysis()
    
    # Generate Markdown report
    markdown_report = audit.generate_markdown_report()
    if output_file:
        with open(output_file, 'w') as f:
            f.write(markdown_report)
    
    # Or generate JSON for APIs
    json_report = audit.generate_json_report()
    return json_report


# ============================================================================
# EXAMPLE 4: Query in Python/API
# ============================================================================

def example_query_accessible_resources(experiment_id):
    """Show how to query accessible resources programmatically."""
    
    from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper
    
    helper = InternetAccessibilityHelper(experiment_id)
    helper.load()
    
    # Get all accessible resources
    accessible = helper.get_internet_accessible_resources()
    
    print(f"Found {len(accessible)} internet-accessible resources:\n")
    
    for resource_id, info in sorted(
        accessible,
        key=lambda x: x[1].get('shortest_path_distance', 999)
    ):
        badge = helper.get_accessibility_badge(resource_id)
        print(f"  {info['resource_name']:30} | {badge}")
        
        # Print path details
        if info.get('path_data'):
            path_nodes = info['path_data'].get('path_nodes', [])
            if path_nodes:
                path_str = " → ".join(path_nodes)
                print(f"    Path: {path_str}")
                print(f"    Auth: {info['auth_level']}")
        print()


# ============================================================================
# EXAMPLE 5: CLI Commands for Operators
# ============================================================================

def example_cli_commands():
    """Show CLI commands that operators would run."""
    
    # After completing a scan:
    
    # 1. Run the analyzer
    commands = [
        # Analyze a specific experiment
        "python Scripts/Analyze/internet_accessibility_analyzer.py --experiment-id my-scan-1",
        
        # Generate markdown audit report
        "python Scripts/Analyze/audit_internet_accessibility.py --experiment-id my-scan-1 --format markdown --output audit.md",
        
        # Generate JSON audit report
        "python Scripts/Analyze/audit_internet_accessibility.py --experiment-id my-scan-1 --format json --output audit.json",
        
        # Query in Python
        "python -c \"from Scripts.Generate.internet_accessibility_ui import query_accessibility_metrics; import json; print(json.dumps(query_accessibility_metrics('my-scan-1'), indent=2))\"",
    ]
    
    for cmd in commands:
        print(f"$ {cmd}\n")


# ============================================================================
# EXAMPLE 6: REST API Endpoint
# ============================================================================

def example_rest_api_endpoint():
    """Show how to create a REST API endpoint for accessibility data."""
    
    # Example Flask/FastAPI endpoint
    
    # @app.get("/api/experiments/{experiment_id}/internet-accessibility")
    def get_internet_accessibility(experiment_id: str):
        """Get internet accessibility data for an experiment."""
        
        from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics
        from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper
        
        try:
            # Get metrics
            metrics = query_accessibility_metrics(experiment_id)
            
            # Get resources
            helper = InternetAccessibilityHelper(experiment_id)
            helper.load()
            accessible = helper.get_internet_accessible_resources()
            
            resource_list = [
                {
                    'id': rid,
                    'name': info['resource_name'],
                    'type': info['resource_type'],
                    'isInternetAccessible': info['is_internet_accessible'],
                    'viaPublicIp': info['via_public_ip'],
                    'viaPublicEndpoint': info['via_public_endpoint'],
                    'viaManagedIdentity': info['via_managed_identity'],
                    'shortestPathDistance': info['shortest_path_distance'],
                    'entryPoint': info['entry_point'],
                    'authLevel': info['auth_level'],
                    'path': info['path_data'].get('path_nodes', []) if info['path_data'] else [],
                }
                for rid, info in accessible
            ]
            
            return {
                'status': 'ok',
                'metrics': metrics,
                'resources': resource_list,
            }
        
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }, 500


# ============================================================================
# EXAMPLE 7: Continuous Monitoring / Change Detection
# ============================================================================

def example_change_detection(current_experiment_id, previous_experiment_id):
    """Show how to detect new exposures between scans."""
    
    from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics
    from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper
    
    # Current state
    current_metrics = query_accessibility_metrics(current_experiment_id)
    current_helper = InternetAccessibilityHelper(current_experiment_id)
    current_helper.load()
    current_accessible = {
        name: info
        for _, (rid, info) in enumerate(current_helper.get_internet_accessible_resources())
        for name in [info['resource_name']]
    }
    
    # Previous state
    previous_metrics = query_accessibility_metrics(previous_experiment_id)
    previous_helper = InternetAccessibilityHelper(previous_experiment_id)
    previous_helper.load()
    previous_accessible = {
        name: info
        for _, (rid, info) in enumerate(previous_helper.get_internet_accessible_resources())
        for name in [info['resource_name']]
    }
    
    # Detect changes
    new_exposures = set(current_accessible.keys()) - set(previous_accessible.keys())
    remediated = set(previous_accessible.keys()) - set(current_accessible.keys())
    changed_risk_level = [
        name
        for name in set(current_accessible.keys()) & set(previous_accessible.keys())
        if (current_accessible[name]['auth_level'] != previous_accessible[name]['auth_level'])
    ]
    
    # Report changes
    if new_exposures:
        print(f"🔴 NEW EXPOSURES: {', '.join(new_exposures)}")
        # Send alert
    
    if remediated:
        print(f"✅ FIXED: {', '.join(remediated)}")
    
    if changed_risk_level:
        print(f"⚠️  RISK LEVEL CHANGED: {', '.join(changed_risk_level)}")
    
    # Overall trend
    print(f"\nExposed resources trend:")
    print(f"  Previous: {previous_metrics['internet_accessible_count']}")
    print(f"  Current:  {current_metrics['internet_accessible_count']}")
    print(f"  Delta:    {current_metrics['internet_accessible_count'] - previous_metrics['internet_accessible_count']:+d}")


# ============================================================================
# EXAMPLE 8: Dashboard Widget Data
# ============================================================================

def example_dashboard_widget_data(experiment_id):
    """Show how to get data for dashboard widgets."""
    
    from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics
    from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper
    
    metrics = query_accessibility_metrics(experiment_id)
    helper = InternetAccessibilityHelper(experiment_id)
    helper.load()
    
    # Widget 1: Exposure Gauge
    widget_exposure = {
        'total': metrics['total_resources'],
        'exposed': metrics['internet_accessible_count'],
        'percentage': metrics['internet_accessible_percentage'],
        'status': 'good' if metrics['internet_accessible_percentage'] < 20 else 'warning' if metrics['internet_accessible_percentage'] < 50 else 'critical',
    }
    
    # Widget 2: Risk Level
    risk_score = _calculate_risk_score(metrics)
    widget_risk = {
        'score': risk_score,
        'level': _risk_level_from_score(risk_score),
        'critical_count': metrics['by_access_method']['via_public_ip'],
        'high_count': metrics['by_access_method']['via_public_endpoint'],
    }
    
    # Widget 3: Top Risks
    accessible = helper.get_internet_accessible_resources()
    top_risks = sorted(
        [
            {
                'name': info['resource_name'],
                'severity': 'critical' if info['via_public_ip'] else 'high' if info['via_public_endpoint'] else 'medium',
                'distance': info['shortest_path_distance'],
            }
            for _, info in accessible
        ],
        key=lambda x: {'critical': 0, 'high': 1, 'medium': 2}[x['severity']]
    )[:5]
    
    widget_top_risks = {
        'resources': top_risks,
        'show_more': len(accessible) > 5,
        'more_count': len(accessible) - 5 if len(accessible) > 5 else 0,
    }
    
    return {
        'exposure': widget_exposure,
        'risk': widget_risk,
        'top_risks': widget_top_risks,
    }


# ============================================================================
# Helper Functions Used in Examples
# ============================================================================

def _calculate_risk_score(metrics):
    """Calculate risk score (0-100)."""
    total = metrics.get('total_resources', 1)
    accessible = metrics.get('internet_accessible_count', 0)
    accessible_pct = (accessible / total) * 100 if total > 0 else 0
    
    score = (accessible_pct * 0.4) + \
            (metrics['by_access_method'].get('via_public_ip', 0) * 15) + \
            (metrics['by_access_method'].get('via_public_endpoint', 0) * 5)
    
    return min(score, 100.0)


def _risk_level_from_score(score):
    """Map score to risk level."""
    if score >= 80:
        return "CRITICAL"
    elif score >= 60:
        return "HIGH"
    elif score >= 40:
        return "MEDIUM"
    elif score >= 20:
        return "LOW"
    else:
        return "MINIMAL"


# ============================================================================
# Module Info
# ============================================================================

if __name__ == "__main__":
    print("""
Internet Accessibility Framework - Integration Examples
========================================================

This file provides copy-paste examples for integrating the framework into:
1. Hierarchical diagram generator
2. Web UI traffic tab
3. Audit report generation
4. Python/API queries
5. CLI commands
6. REST API endpoints
7. Change detection (trending)
8. Dashboard widgets

See each example_* function for specific use cases.

To run examples:
    python google_examples_internet_accessibility.py
    """)
    
    # Demonstrate CLI usage
    print("\n[CLI Examples]")
    example_cli_commands()
