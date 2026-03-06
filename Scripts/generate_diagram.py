#!/usr/bin/env python3
"""Generate Mermaid diagrams from database queries."""

import sys
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db_helpers import get_db_connection, get_resources_for_diagram, get_connections_for_diagram
import resource_type_db as _rtdb

# Lazy DB connection for resource type lookups
_lookup_conn: sqlite3.Connection | None = None

def _get_lookup_db() -> sqlite3.Connection | None:
    global _lookup_conn
    if _lookup_conn is None:
        db_path = Path(__file__).resolve().parents[1] / "Output/Learning/triage.db"
        if db_path.exists():
            _lookup_conn = sqlite3.connect(str(db_path))
    return _lookup_conn


# Category → diagram stroke colour
_CATEGORY_COLOURS: dict[str, str] = {
    "Compute":   "#0066cc",
    "Container": "#0066cc",
    "Database":  "#00aa00",
    "Storage":   "#00aa00",
    "Identity":  "#f59f00",
    "Security":  "#ff6b6b",
    "Network":   "#7e57c2",
    "Monitoring":"#888888",
}


def get_node_style(resource: dict) -> Optional[str]:
    """Return Mermaid style string for a resource based on finding score or category."""
    node_id = sanitize_id(resource['resource_name'])
    score = resource.get('max_finding_score', 0)

    if score >= 9:
        return f"style {node_id} stroke:#ff0000, stroke-width:4px"
    if score >= 7:
        return f"style {node_id} stroke:#ff6b00, stroke-width:3px"

    conn = _get_lookup_db()
    if conn:
        category = _rtdb.get_category(conn, resource.get('resource_type', ''))
        colour = _CATEGORY_COLOURS.get(category)
        if colour:
            return f"style {node_id} stroke:{colour}, stroke-width:2px"

    return None


def _display_label(resource: dict) -> str:
    """Return diagram node label using real name + friendly type."""
    conn = _get_lookup_db()
    name = resource['resource_name']
    rtype = resource.get('resource_type', '')
    if conn and rtype:
        friendly = _rtdb.get_friendly_name(conn, rtype)
        return f"{name}<br/>{friendly}"
    return name


def _category(resource: dict) -> str:
    conn = _get_lookup_db()
    if conn:
        return _rtdb.get_category(conn, resource.get('resource_type', ''))
    return 'Other'


def sanitize_id(name: str) -> str:
    """Convert resource name to valid Mermaid node ID."""
    return name.replace('-', '_').replace('.', '_').replace(' ', '_')


def generate_architecture_diagram(experiment_id: str) -> str:
    # Support experiment folder names like '001_001' by falling back to numeric prefix if no rows
    from db_helpers import get_db_connection as _get_db_conn
    with _get_db_conn() as _conn:
        check = _conn.execute("SELECT COUNT(1) as c FROM repositories WHERE experiment_id = ?", [experiment_id]).fetchone()
        if check and check['c'] == 0 and '_' in experiment_id:
            alt = experiment_id.split('_')[0]
            # If numeric prefix exists in DB, use it
            alt_check = _conn.execute("SELECT COUNT(1) as c FROM repositories WHERE experiment_id = ?", [alt]).fetchone()
            if alt_check and alt_check['c'] > 0:
                experiment_id = alt

    """Generate full architecture diagram from database with parent-child hierarchies."""
    
    # Prefer canonical helpers that return merged properties
    resources = get_resources_for_diagram(experiment_id)
    hierarchies = []
    connections = get_connections_for_diagram(experiment_id)
    # hierarchies can be built by joining resources with parent relationships if needed
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT parent.id as parent_id, parent.resource_name as parent_name, parent.resource_type as parent_type,
                   child.id as child_id, child.resource_name as child_name, child.resource_type as child_type
            FROM resources parent
            JOIN resources child ON child.parent_resource_id = parent.id
            WHERE parent.experiment_id = ?
            ORDER BY parent.resource_name, child.resource_name
        """, [experiment_id]).fetchall()
        hierarchies = rows

    
    if not resources:
        return "flowchart TB\n  empty[No resources found]"
    
    lines = ["flowchart TB"]
    
    # Build parent-child mapping
    parent_children: Dict[int, List] = {}
    child_ids = set()
    for h in hierarchies:
        parent_id = h['parent_id']
        if parent_id not in parent_children:
            parent_children[parent_id] = []
        parent_children[parent_id].append(h)
        child_ids.add(h['child_id'])
    
    # Group resources by type (excluding children as they'll be in parent subgraphs)
    root_resources = [r for r in resources if r['id'] not in child_ids]

    # Group by DB category instead of hardcoded type strings
    def _in_cat(r: dict, *cats: str) -> bool:
        return _category(r) in cats

    vms            = [r for r in root_resources if _in_cat(r, 'Compute')]
    aks            = [r for r in root_resources if _in_cat(r, 'Container')]
    sql_servers    = [r for r in root_resources if _in_cat(r, 'Database')]
    storage_accounts = [r for r in root_resources if _in_cat(r, 'Storage')]
    nsgs           = [r for r in root_resources if _in_cat(r, 'Security') and 'nsg' in r.get('resource_type','').lower()]
    paas           = [r for r in root_resources if _in_cat(r, 'Identity') and r['id'] not in child_ids]
    other          = [r for r in root_resources if r not in vms + aks + sql_servers + storage_accounts + nsgs + paas]
    
    # Add Internet node if we have internet connections
    has_internet_connections = any(c['source'] == 'Internet' or c['target'] == 'Internet' for c in connections)
    if has_internet_connections:
        lines.append("  internet[Internet]")
    
    # VNet subgraph (VMs, AKS, NSG)
    if vms or aks or nsgs:
        lines.append("  subgraph vnet[VNet]")
        
        for vm in vms:
            node_id = sanitize_id(vm['resource_name'])
            lines.append(f"    {node_id}[{_display_label(vm)}]")
        
        for aks_cluster in aks:
            node_id = sanitize_id(aks_cluster['resource_name'])
            lines.append(f"    {node_id}[{_display_label(aks_cluster)}]")
        
        for nsg in nsgs:
            node_id = sanitize_id(nsg['resource_name'])
            lines.append(f"    {node_id}[{_display_label(nsg)}]")
        
        lines.append("  end")
    
    # SQL Server hierarchies (parent -> databases)
    for sql_server in sql_servers:
        node_id = sanitize_id(sql_server['resource_name'])
        lines.append(f"  subgraph {node_id}_sg[SQL Server: {sql_server['resource_name']}]")
        
        if sql_server['id'] in parent_children:
            for child in parent_children[sql_server['id']]:
                child_id = sanitize_id(child['child_name'])
                lines.append(f"    {child_id}[Database: {child['child_name']}]")
        else:
            lines.append(f"    {node_id}[{sql_server['resource_name']}]")
        
        lines.append("  end")
    
    # Storage Account hierarchies (parent -> containers -> blobs)
    for storage_account in storage_accounts:
        node_id = sanitize_id(storage_account['resource_name'])
        lines.append(f"  subgraph {node_id}_sg[Storage Account: {storage_account['resource_name']}]")
        
        if storage_account['id'] in parent_children:
            for container in parent_children[storage_account['id']]:
                container_id = sanitize_id(container['child_name'])
                
                # Check if container has blob children
                has_blob_children = container['child_id'] in parent_children
                
                if has_blob_children:
                    lines.append(f"    subgraph {container_id}_sg[Container: {container['child_name']}]")
                    for blob in parent_children[container['child_id']]:
                        blob_id = sanitize_id(blob['child_name'])
                        lines.append(f"      {blob_id}[Blob: {blob['child_name']}]")
                    lines.append("    end")
                else:
                    lines.append(f"    {container_id}[Container: {container['child_name']}]")
        else:
            lines.append(f"    {node_id}[{storage_account['resource_name']}]")
        
        lines.append("  end")
    
    # PaaS subgraph
    if paas:
        lines.append(f"  subgraph paas[PaaS / Identity]")
        for p in paas:
            node_id = sanitize_id(p['resource_name'])
            lines.append(f"    {node_id}[{_display_label(p)}]")
        lines.append("  end")
    
    # Other resources (outside subgraphs)
    for res in other:
        if res['resource_name'] not in ('Internet', 'NSG'):
            node_id = sanitize_id(res['resource_name'])
            lines.append(f"  {node_id}[{_display_label(res)}]")
    
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
        style = get_node_style(r)
        if style:
            lines.append(f"  {style}")
    
    if has_internet_connections:
        lines.append("  style internet stroke:#ff0000, stroke-width:3px")
    
    return "\n".join(lines)


def generate_security_view(experiment_id: str, min_score: int = 7) -> str:
    """Generate diagram showing only resources with findings."""
    
    with get_db_connection() as conn:
        # Get vulnerable resources
        vulnerable = conn.execute("""
            SELECT DISTINCT
              r.resource_name,
              r.resource_type,
              MAX(COALESCE(f.severity_score, f.score, 0)) as max_score
            FROM resources r
            JOIN findings f ON r.id = f.resource_id
            WHERE r.experiment_id = ? AND COALESCE(f.severity_score, f.score, 0) >= ?
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
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}<br/>{severity} {score}/10]")
    
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
        style = get_node_style(r)
        if style:
            lines.append(f"  {style}")
    
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
        lines.append(f"  {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}<br/>Hop {r['hop_count']}]")
    
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
    lines.append("  classDef compromised stroke:#ff0000, stroke-width:6px")
    
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
                lines.append(f"    {node_id}[{r['resource_name']}<br/>{_rtdb.get_friendly_name(_get_lookup_db(), r['resource_type']) if _get_lookup_db() else r['resource_type']}]")
            
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
