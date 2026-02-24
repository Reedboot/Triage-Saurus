#!/usr/bin/env python3
"""Generate Mermaid diagrams from database queries."""

import sys
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db_helpers import get_db_connection


def sanitize_id(name: str) -> str:
    """Convert resource name to valid Mermaid node ID."""
    return name.replace('-', '_').replace('.', '_').replace(' ', '_')


def generate_architecture_diagram(experiment_id: str) -> str:
    """Generate full architecture diagram from database."""
    
    with get_db_connection() as conn:
        # Get all resources
        resources = conn.execute("""
            SELECT 
              r.resource_name,
              r.resource_type,
              r.provider,
              repo.repo_name,
              COALESCE(MAX(f.score), 0) as max_finding_score
            FROM resources r
            JOIN repositories repo ON r.repo_id = repo.id
            LEFT JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
            GROUP BY r.id
            ORDER BY r.resource_type, r.resource_name
        """, [experiment_id]).fetchall()
        
        # Get connections
        connections = conn.execute("""
            SELECT 
              r_src.resource_name as source,
              r_tgt.resource_name as target,
              rc.protocol,
              rc.port,
              rc.is_cross_repo
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id]).fetchall()
    
    if not resources:
        return "flowchart TB\n  empty[No resources found]"
    
    lines = ["flowchart TB"]
    
    # Group resources by type for subgraphs
    vms = [r for r in resources if r['resource_type'] == 'VM']
    aks = [r for r in resources if r['resource_type'] == 'AKS']
    nsgs = [r for r in resources if r['resource_type'] == 'NSG']
    paas = [r for r in resources if r['resource_type'] in ('SQL', 'KeyVault', 'Storage', 'AppService')]
    other = [r for r in resources if r not in vms + aks + nsgs + paas]
    
    # Add Internet node if we have internet connections
    has_internet_connections = any(c['source'] == 'Internet' or c['target'] == 'Internet' for c in connections)
    if has_internet_connections:
        lines.append("  internet[Internet]")
    
    # VNet subgraph (VMs, AKS, NSG)
    if vms or aks or nsgs:
        lines.append("  subgraph vnet[VNet]")
        
        for vm in vms:
            node_id = sanitize_id(vm['resource_name'])
            lines.append(f"    {node_id}[{vm['resource_name']} VM]")
        
        for aks_cluster in aks:
            node_id = sanitize_id(aks_cluster['resource_name'])
            lines.append(f"    {node_id}[{aks_cluster['resource_name']} AKS]")
        
        for nsg in nsgs:
            node_id = sanitize_id(nsg['resource_name'])
            lines.append(f"    {node_id}[Network Security Group]")
        
        lines.append("  end")
    
    # PaaS subgraph
    if paas:
        lines.append("  subgraph paas[Azure PaaS]")
        for p in paas:
            node_id = sanitize_id(p['resource_name'])
            type_label = p['resource_type'].replace('KeyVault', 'Key Vault').replace('AppService', 'App Service')
            lines.append(f"    {node_id}[{p['resource_name']}<br/>{type_label}]")
        lines.append("  end")
    
    # Other resources (outside subgraphs)
    for res in other:
        if res['resource_name'] not in ('Internet', 'NSG'):
            node_id = sanitize_id(res['resource_name'])
            lines.append(f"  {node_id}[{res['resource_name']}<br/>{res['resource_type']}]")
    
    lines.append("")
    
    # Add connections
    for conn in connections:
        src = sanitize_id(conn['source'])
        tgt = sanitize_id(conn['target'])
        
        # Add protocol/port label if available
        label = ""
        if conn['protocol'] and conn['port']:
            label = f"|{conn['protocol']}:{conn['port']}|"
        elif conn['protocol']:
            label = f"|{conn['protocol']}|"
        
        # Cross-repo connections use dashed line
        if conn['is_cross_repo']:
            lines.append(f"  {src} -.{label}.> {tgt}")
        else:
            lines.append(f"  {src} -->{label} {tgt}")
    
    lines.append("")
    
    # Styling based on findings
    for r in resources:
        node_id = sanitize_id(r['resource_name'])
        score = r['max_finding_score']
        
        if score >= 9:
            lines.append(f"  style {node_id} stroke:#ff0000,stroke-width:4px,fill:#ffe6e6")
        elif score >= 7:
            lines.append(f"  style {node_id} stroke:#ff6b00,stroke-width:3px,fill:#fff4e6")
        elif r['resource_type'] == 'VM':
            lines.append(f"  style {node_id} stroke:#0066cc,stroke-width:2px")
        elif r['resource_type'] == 'SQL':
            lines.append(f"  style {node_id} stroke:#00aa00,stroke-width:2px")
        elif r['resource_type'] == 'KeyVault':
            lines.append(f"  style {node_id} stroke:#f59f00,stroke-width:2px")
        elif r['resource_type'] == 'Storage':
            lines.append(f"  style {node_id} stroke:#00aa00,stroke-width:2px")
        elif r['resource_type'] == 'NSG':
            lines.append(f"  style {node_id} stroke:#ff6b6b,stroke-width:2px")
    
    if has_internet_connections:
        lines.append("  style internet stroke:#ff0000,stroke-width:3px")
    
    return "\n".join(lines)


def generate_security_view(experiment_id: str, min_score: int = 7) -> str:
    """Generate diagram showing only resources with findings."""
    
    with get_db_connection() as conn:
        # Get vulnerable resources
        vulnerable = conn.execute("""
            SELECT DISTINCT
              r.resource_name,
              r.resource_type,
              MAX(f.score) as max_score
            FROM resources r
            JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ? AND f.score >= ?
            GROUP BY r.id
            ORDER BY max_score DESC
        """, [experiment_id, min_score]).fetchall()
        
        if not vulnerable:
            return f"flowchart TB\n  empty[No resources with score >= {min_score}]"
        
        # Get connections between vulnerable resources
        vulnerable_names = {r['resource_name'] for r in vulnerable}
        
        connections = conn.execute("""
            SELECT 
              r_src.resource_name as source,
              r_tgt.resource_name as target,
              rc.protocol
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id]).fetchall()
        
        # Filter connections to only those involving vulnerable resources
        relevant_connections = [
            c for c in connections 
            if c['source'] in vulnerable_names or c['target'] in vulnerable_names
        ]
    
    lines = ["flowchart TB"]
    
    # Add vulnerable resources
    for r in vulnerable:
        node_id = sanitize_id(r['resource_name'])
        score = r['max_score']
        severity = "🔴 Critical" if score >= 9 else "🟠 High"
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{r['resource_type']}<br/>{severity} {score}/10]")
    
    lines.append("")
    
    # Add connections
    for conn in relevant_connections:
        src = sanitize_id(conn['source'])
        tgt = sanitize_id(conn['target'])
        label = f"|{conn['protocol']}|" if conn['protocol'] else ""
        lines.append(f"  {src} -->{label} {tgt}")
    
    lines.append("")
    
    # Styling
    for r in vulnerable:
        node_id = sanitize_id(r['resource_name'])
        if r['max_score'] >= 9:
            lines.append(f"  style {node_id} stroke:#ff0000,stroke-width:4px,fill:#ffe6e6")
        else:
            lines.append(f"  style {node_id} stroke:#ff6b00,stroke-width:3px,fill:#fff4e6")
    
    return "\n".join(lines)


def generate_blast_radius_diagram(experiment_id: str, compromised_resource: str) -> str:
    """Show what attacker can reach from compromised resource."""
    
    with get_db_connection() as conn:
        # Recursive CTE to find all reachable resources
        reachable = conn.execute("""
            WITH RECURSIVE blast_radius AS (
              SELECT 
                rc.target_resource_id as resource_id,
                1 as hop_count,
                r_src.resource_name || ' → ' || r_tgt.resource_name as path
              FROM resource_connections rc
              JOIN resources r_src ON rc.source_resource_id = r_src.id
              JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
              WHERE r_src.resource_name = ?
                AND r_src.experiment_id = ?
              
              UNION ALL
              
              SELECT 
                rc.target_resource_id,
                br.hop_count + 1,
                br.path || ' → ' || r.resource_name
              FROM blast_radius br
              JOIN resource_connections rc ON br.resource_id = rc.source_resource_id
              JOIN resources r ON rc.target_resource_id = r.id
              WHERE br.hop_count < 5
            )
            SELECT DISTINCT 
              r.resource_name, 
              r.resource_type,
              br.hop_count, 
              br.path
            FROM blast_radius br
            JOIN resources r ON br.resource_id = r.id
            ORDER BY br.hop_count
        """, [compromised_resource, experiment_id]).fetchall()
    
    if not reachable:
        return f"flowchart LR\n  compromised[{compromised_resource}]\n  empty[No connections found]"
    
    lines = ["flowchart LR"]
    lines.append(f"  compromised[🔴 {compromised_resource}<br/>COMPROMISED]:::compromised")
    
    # Add reachable resources
    for r in reachable:
        node_id = sanitize_id(r['resource_name'])
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{r['resource_type']}<br/>Hop {r['hop_count']}]")
    
    lines.append("")
    
    # Show paths
    seen_edges = set()
    for r in reachable:
        path_parts = r['path'].split(' → ')
        for i in range(len(path_parts) - 1):
            src = sanitize_id(path_parts[i])
            tgt = sanitize_id(path_parts[i + 1])
            edge = (src, tgt)
            if edge not in seen_edges:
                lines.append(f"  {src} ==> {tgt}")
                seen_edges.add(edge)
    
    lines.append("")
    lines.append("  classDef compromised stroke:#ff0000,stroke-width:6px,fill:#ffcccc")
    
    return "\n".join(lines)


def generate_multi_repo_diagram(experiment_id: str) -> str:
    """Generate diagram showing all repos and cross-repo connections."""
    
    with get_db_connection() as conn:
        repos = conn.execute("""
            SELECT id, repo_name 
            FROM repositories 
            WHERE experiment_id = ?
        """, [experiment_id]).fetchall()
        
        if not repos:
            return "flowchart TB\n  empty[No repositories found]"
        
        lines = ["flowchart TB"]
        
        # Subgraph per repo
        for repo in repos:
            repo_id = repo['id']
            repo_name = repo['repo_name']
            repo_node = sanitize_id(repo_name)
            
            lines.append(f"  subgraph {repo_node}[Repository: {repo_name}]")
            
            # Get resources in this repo
            resources = conn.execute("""
                SELECT resource_name, resource_type
                FROM resources
                WHERE repo_id = ?
            """, [repo_id]).fetchall()
            
            for r in resources:
                node_id = f"{repo_node}_{sanitize_id(r['resource_name'])}"
                lines.append(f"    {node_id}[{r['resource_name']}<br/>{r['resource_type']}]")
            
            lines.append("  end")
        
        lines.append("")
        
        # Add connections
        connections = conn.execute("""
            SELECT 
              repo_src.repo_name as src_repo,
              r_src.resource_name as src_name,
              repo_tgt.repo_name as tgt_repo,
              r_tgt.resource_name as tgt_name,
              rc.is_cross_repo,
              rc.protocol
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            JOIN repositories repo_src ON r_src.repo_id = repo_src.id
            JOIN repositories repo_tgt ON r_tgt.repo_id = repo_tgt.id
            WHERE rc.experiment_id = ?
        """, [experiment_id]).fetchall()
        
        for c in connections:
            src_repo_node = sanitize_id(c['src_repo'])
            tgt_repo_node = sanitize_id(c['tgt_repo'])
            src = f"{src_repo_node}_{sanitize_id(c['src_name'])}"
            tgt = f"{tgt_repo_node}_{sanitize_id(c['tgt_name'])}"
            
            label = f"|{c['protocol']}|" if c['protocol'] else ""
            
            # Style cross-repo connections differently
            if c['is_cross_repo']:
                lines.append(f"  {src} -.{label}.>|cross-repo| {tgt}")
            else:
                lines.append(f"  {src} -->{label} {tgt}")
    
    return "\n".join(lines)


def main():
    """CLI for diagram generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate Mermaid diagrams from database")
    parser.add_argument("experiment_id", help="Experiment ID (e.g., '008')")
    parser.add_argument("--type", choices=['architecture', 'security', 'blast-radius', 'multi-repo'],
                        default='architecture', help="Diagram type")
    parser.add_argument("--min-score", type=int, default=7, 
                        help="Minimum score for security view")
    parser.add_argument("--compromised", help="Compromised resource for blast radius")
    parser.add_argument("--output", type=Path, help="Output file (default: stdout)")
    
    args = parser.parse_args()
    
    if args.type == 'architecture':
        diagram = generate_architecture_diagram(args.experiment_id)
    elif args.type == 'security':
        diagram = generate_security_view(args.experiment_id, args.min_score)
    elif args.type == 'blast-radius':
        if not args.compromised:
            print("Error: --compromised required for blast-radius diagram", file=sys.stderr)
            sys.exit(1)
        diagram = generate_blast_radius_diagram(args.experiment_id, args.compromised)
    elif args.type == 'multi-repo':
        diagram = generate_multi_repo_diagram(args.experiment_id)
    
    if args.output:
        args.output.write_text(diagram)
        print(f"Diagram written to {args.output}")
    else:
        print(diagram)


if __name__ == "__main__":
    main()
