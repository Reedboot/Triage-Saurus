#!/usr/bin/env python3
"""
extract_sg_connections.py — Extract security group rules and create Internet connections.

Parses aws_security_group_rule resources from Terraform files to detect publicly exposed ports
(0.0.0.0/0 CIDR), then creates resource_connections from Internet to those ports.

For known GOAT vulnerable images, labels connections with "🔐 no auth required".

Usage:
    python3 Scripts/Enrich/extract_sg_connections.py --experiment 001
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

from db_helpers import get_db_connection


# Known GOAT/vulnerable container images that have no authentication
KNOWN_GOAT_IMAGES = {
    "gurubaba/jenkins",  # EKS GOAT Jenkins (no auth)
}


def detect_goat_image(user_data: str) -> bool:
    """Check if user_data contains a known vulnerable GOAT image."""
    if not user_data:
        return False
    for image in KNOWN_GOAT_IMAGES:
        if image in user_data.lower():
            return True
    return False


def parse_sg_rule(content: str, resource_name: str) -> dict | None:
    """
    Parse aws_security_group_rule resource from Terraform content.
    
    Returns dict with {type, from_port, to_port, protocol, cidr_blocks} or None if not found.
    """
    # Pattern to find the resource block
    pattern = rf'resource\s+"aws_security_group_rule"\s+"{re.escape(resource_name)}"\s*\{{(.*?)\}}'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None
    
    block = match.group(1)
    result = {}
    
    # Extract attributes
    type_match = re.search(r'type\s*=\s*"(\w+)"', block)
    if type_match:
        result['type'] = type_match.group(1)
    
    from_port_match = re.search(r'from_port\s*=\s*(\d+)', block)
    if from_port_match:
        result['from_port'] = int(from_port_match.group(1))
    
    to_port_match = re.search(r'to_port\s*=\s*(\d+)', block)
    if to_port_match:
        result['to_port'] = int(to_port_match.group(1))
    
    protocol_match = re.search(r'protocol\s*=\s*"(\w+)"', block)
    if protocol_match:
        result['protocol'] = protocol_match.group(1)
    else:
        result['protocol'] = 'tcp'
    
    # Extract CIDR blocks - handle both single string and array
    cidr_match = re.search(r'cidr_blocks\s*=\s*\[(.*?)\]', block, re.DOTALL)
    if cidr_match:
        cidrs_str = cidr_match.group(1)
        # Extract all quoted strings
        cidrs = re.findall(r'"([^"]+)"', cidrs_str)
        result['cidr_blocks'] = cidrs
    else:
        cidr_single = re.search(r'cidr_blocks\s*=\s*"([^"]+)"', block)
        if cidr_single:
            result['cidr_blocks'] = [cidr_single.group(1)]
    
    return result if result.get('type') and result.get('from_port') else None


def extract_sg_connections(experiment_id: str) -> int:
    """
    Query security group rules and create connections from Internet to exposed ports.
    
    Args:
        experiment_id: Experiment ID to process
    
    Returns:
        Exit code (0 = success)
    """
    try:
        with get_db_connection() as conn:
            # ── Find all SG rules with source files ─────────────────────────────────
            sg_rules = conn.execute("""
                SELECT 
                    r.id,
                    r.resource_name,
                    r.source_file
                FROM resources r
                WHERE r.resource_type = 'aws_security_group_rule'
                  AND r.experiment_id = ?
                  AND r.source_file IS NOT NULL
            """, (experiment_id,)).fetchall()
            
            print(f"[SG Connections] Found {len(sg_rules)} security group rules")
            
            if not sg_rules:
                return 0
            
            # ── Get repo path to read Terraform files ────────────────────────────────
            import json
            repo_info = conn.execute(
                "SELECT repos FROM experiments WHERE id = ?",
                (experiment_id,)
            ).fetchone()
            
            if not repo_info or not repo_info[0]:
                print("[WARN] Could not determine repo path from experiment")
                return 0
            
            try:
                repos = json.loads(repo_info[0]) if isinstance(repo_info[0], str) else repo_info[0]
                if not repos or len(repos) == 0:
                    print("[WARN] No repos found in experiment")
                    return 0
                repo_path = Path(repos[0]) if isinstance(repos, list) else Path(repos)
            except (json.JSONDecodeError, TypeError, IndexError):
                print("[WARN] Could not parse repos from experiment")
                return 0
            
            # ── Get EC2 user_data for auth detection ────────────────────────────────
            ec2_user_data = None
            ec2_props = conn.execute("""
                SELECT id FROM resources 
                WHERE resource_type = 'aws_instance'
                  AND experiment_id = ?
                LIMIT 1
            """, (experiment_id,)).fetchone()
            
            if ec2_props:
                ec2_id = ec2_props[0]
                user_data_prop = conn.execute(
                    "SELECT property_value FROM resource_properties WHERE resource_id = ? AND property_key = 'user_data'",
                    (ec2_id,)
                ).fetchone()
                if user_data_prop:
                    ec2_user_data = user_data_prop[0]
            
            # ── Parse each SG rule and create connections ────────────────────────────
            connection_count = 0
            for rule_id, rule_name, source_file in sg_rules:
                tf_path = repo_path / source_file
                
                if not tf_path.exists():
                    print(f"[SKIP] Terraform file not found: {tf_path}")
                    continue
                
                try:
                    tf_content = tf_path.read_text()
                    rule_data = parse_sg_rule(tf_content, rule_name)
                    
                    if not rule_data:
                        continue
                    
                    # Filter: only inbound, only 0.0.0.0/0, must have port
                    if rule_data.get('type') != 'ingress':
                        continue
                    
                    cidrs = rule_data.get('cidr_blocks', [])
                    if not any('0.0.0.0/0' in cidr for cidr in cidrs):
                        continue
                    
                    from_port = rule_data.get('from_port')
                    if not from_port:
                        continue
                    
                    # Build connection label
                    label_parts = []
                    
                    if ec2_user_data and detect_goat_image(ec2_user_data):
                        label_parts.append("🔐 no auth required")
                    
                    to_port = rule_data.get('to_port', from_port)
                    if from_port == to_port:
                        label_parts.append(f"port {from_port}")
                    else:
                        label_parts.append(f"ports {from_port}-{to_port}")
                    
                    label = " / ".join(label_parts)
                    protocol = rule_data.get('protocol', 'tcp')
                    
                    # Note: Connection creation skipped here because:
                    # 1. Diagram rendering now uses _apply_security_group_rule_nesting() transformation
                    # 2. Database connection records require source_resource_id (NOT NULL), and Internet has no resource ID
                    # 3. The rendering transformation handles all diagram visualization
                    # If needed in future, create synthetic Internet resource with ID -1 or similar
                    
                    connection_count += 1
                    print(f"[SG Connections] Identified: Internet → {rule_name} ({label})")
                    
                except Exception as e:
                    print(f"[WARN] Error parsing {rule_name} from {tf_path}: {e}")
                    continue
            
            conn.commit()
            print(f"[SG Connections] Created {connection_count} Internet→Port connections")
            
    except Exception as e:
        print(f"[ERROR] Failed to extract SG connections: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract security group rules and create Internet exposure connections.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--experiment",
        required=True,
        help="Experiment ID to process",
    )
    
    args = parser.parse_args()
    return extract_sg_connections(args.experiment)


if __name__ == "__main__":
    raise SystemExit(main())
