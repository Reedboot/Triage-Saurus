#!/usr/bin/env python3
"""Internet accessibility analyzer for cloud resources.

Computes and stores whether each resource is reachable from the Internet, either directly
or transitively through other resources. Uses graph traversal from Internet entry points
(e.g., public IPs, public API endpoints) to mark all downstream resources.

Usage:
    python internet_accessibility_analyzer.py --experiment-id <id>

Output:
    Populates resource_internet_accessibility table with computed accessibility info.
"""

import sqlite3
import sys
import argparse
import json
from typing import Set, Dict, List, Tuple, Optional
from collections import defaultdict, deque
from pathlib import Path
from dataclasses import dataclass

# Add Persist path(s) for imports regardless of invocation cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
for candidate in (SCRIPTS_DIR / "Persist", REPO_ROOT / "Scripts" / "Persist"):
    candidate_str = str(candidate)
    if candidate.exists() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from db_helpers import get_db_connection


@dataclass
class AccessibilityPath:
    """Represents a path from Internet to a resource."""
    resource_id: int
    resource_name: str
    resource_type: str
    distance: int  # Number of hops from Internet
    path_nodes: List[str]  # Resource names in path, starting with "Internet"
    entry_point: str  # The initial Internet-accessible resource
    via_public_ip: bool  # True if reachable via direct public IP
    via_endpoint: bool  # True if reachable via public endpoint (API, LB, etc.)
    via_managed_identity: bool  # True if reachable via identity/auth
    confirmed: bool  # True if explicitly configured or confirmed
    auth_level: str  # none, key, identity, network


class InternetAccessibilityAnalyzer:
    """Analyzes and computes internet accessibility for resources."""

    def __init__(self, experiment_id: str):
        """Initialize analyzer for an experiment."""
        self.experiment_id = experiment_id
        self.resources_by_id: Dict[int, dict] = {}
        self.resources_by_name: Dict[str, dict] = {}
        self.connections_list: List[dict] = []
        self.properties_by_resource: Dict[int, Dict[str, str]] = defaultdict(dict)
        
        # Results tracking
        self.accessible_resources: Set[int] = set()
        self.accessibility_paths: Dict[int, List[AccessibilityPath]] = defaultdict(list)
        self.internet_entry_points: List[dict] = []  # Resources directly exposed to Internet

    def load_data(self) -> None:
        """Load resources, connections, and properties from database."""
        with get_db_connection() as conn:
            # Load all resources for this experiment
            rows = conn.execute(
                """
                SELECT id, resource_name, resource_type, provider, parent_resource_id
                FROM resources
                WHERE experiment_id = ?
                ORDER BY id
                """,
                [self.experiment_id],
            ).fetchall()

            for row in rows:
                res_dict = {
                    "id": row["id"],
                    "resource_name": row["resource_name"],
                    "resource_type": row["resource_type"],
                    "provider": row["provider"],
                    "parent_resource_id": row["parent_resource_id"],
                }
                self.resources_by_id[row["id"]] = res_dict
                self.resources_by_name[row["resource_name"]] = res_dict

            # Load all connections
            conn_rows = conn.execute(
                """
                SELECT 
                    id, source_resource_id, target_resource_id,
                    connection_type, protocol, port, authentication,
                    auth_method, inferred_internet, target_external
                FROM resource_connections
                WHERE experiment_id = ?
                ORDER BY id
                """,
                [self.experiment_id],
            ).fetchall()

            for row in conn_rows:
                conn_dict = {
                    "id": row["id"],
                    "source_resource_id": row["source_resource_id"],
                    "target_resource_id": row["target_resource_id"],
                    "connection_type": (row["connection_type"] or "").lower(),
                    "protocol": row["protocol"] or "",
                    "port": row["port"] or "",
                    "authentication": row["authentication"] or "",
                    "auth_method": row["auth_method"] or "",
                    "inferred_internet": row["inferred_internet"] == 1 if row["inferred_internet"] is not None else False,
                    "target_external": row["target_external"] or "",
                }
                self.connections_list.append(conn_dict)

            # Load resource properties
            prop_rows = conn.execute(
                """
                SELECT resource_id, property_key, property_value
                FROM resource_properties
                WHERE resource_id IN (
                    SELECT id FROM resources WHERE experiment_id = ?
                )
                """,
                [self.experiment_id],
            ).fetchall()

            for row in prop_rows:
                self.properties_by_resource[row["resource_id"]][row["property_key"]] = row["property_value"]

    def _is_public_ip_resource(self, resource_id: int) -> bool:
        """Check if resource is a public IP address."""
        res = self.resources_by_id.get(resource_id)
        if not res:
            return False
        rtype = (res.get("resource_type") or "").lower()
        name = (res.get("resource_name") or "").lower()
        
        # Check if it's a public IP resource type
        if any(t in rtype for t in ["public_ip", "publicip", "elastic_ip", "eip", "public_address"]):
            return True
        
        # Check properties for explicit internet access
        props = self.properties_by_resource.get(resource_id, {})
        if props.get("internet_access", "").lower() == "true":
            return True
        if props.get("is_internet_accessible", "").lower() == "true":
            return True
            
        return False

    def _is_public_endpoint_resource(self, resource_id: int) -> bool:
        """Check if resource is a public endpoint (API, LB, gateway)."""
        res = self.resources_by_id.get(resource_id)
        if not res:
            return False
            
        rtype = (res.get("resource_type") or "").lower()
        
        # Check for endpoint-like resource types
        endpoint_keywords = [
            "api_management", "apim", "api_gateway", "application_gateway",
            "load_balancer", "alb", "nlb", "elb", "cdn", "cloudfront",
            "front_door", "api_endpoint", "public_endpoint", "app_service",
            "function_app"
        ]
        
        if any(kw in rtype for kw in endpoint_keywords):
            # Verify it's actually public
            props = self.properties_by_resource.get(resource_id, {})
            
            # Check explicit public access settings
            if props.get("public_access_enabled", "").lower() == "false":
                return False
            if props.get("is_private", "").lower() == "true":
                return False
            if props.get("private_endpoint_only", "").lower() == "true":
                return False
            
            # App Services and Function Apps are public by default
            return True
        
        return False

    def find_internet_entry_points(self) -> None:
        """Identify resources that are directly accessible from Internet."""
        self.internet_entry_points = []
        
        for res_id, res in self.resources_by_id.items():
            is_entry = False
            via_type = None
            
            # Check if it's a direct public IP
            if self._is_public_ip_resource(res_id):
                is_entry = True
                via_type = "public_ip"
            
            # Check if it's a public endpoint
            elif self._is_public_endpoint_resource(res_id):
                is_entry = True
                via_type = "public_endpoint"
            
            # Check for explicit Internet → Resource connections
            for conn in self.connections_list:
                if conn.get("source_resource_id") is None and \
                   conn.get("target_resource_id") == res_id and \
                   conn.get("inferred_internet"):
                    is_entry = True
                    via_type = "inferred_connection"
                    break
            
            if is_entry:
                self.internet_entry_points.append({
                    "resource_id": res_id,
                    "resource_name": res.get("resource_name"),
                    "resource_type": res.get("resource_type"),
                    "via_type": via_type,
                })
                self.accessible_resources.add(res_id)

    def _build_adjacency_list(self) -> Dict[int, List[Tuple[int, dict]]]:
        """Build adjacency list for graph traversal."""
        graph: Dict[int, List[Tuple[int, dict]]] = defaultdict(list)
        
        for conn in self.connections_list:
            src_id = conn.get("source_resource_id")
            tgt_id = conn.get("target_resource_id")
            
            # Skip self-loops and missing resources
            if src_id is None or tgt_id is None or src_id == tgt_id:
                continue
            if src_id not in self.resources_by_id or tgt_id not in self.resources_by_id:
                continue
            
            # Skip administrative edge types that don't enable actual traffic flow
            skip_types = {
                "contains", "grants_access_to", "parent_of", "child_of",
                "resource_group_member", "has_role", "depends_on",
            }
            if conn.get("connection_type") in skip_types:
                continue
            
            graph[src_id].append((tgt_id, conn))
        
        return graph

    def traverse_from_internet(self) -> None:
        """BFS traversal from Internet entry points to find all reachable resources."""
        graph = self._build_adjacency_list()
        visited: Set[int] = set()
        queue: deque = deque()
        parent_map: Dict[int, Tuple[int, dict]] = {}  # Maps resource_id → (parent_id, connection)
        
        # Start BFS from each Internet entry point
        for entry in self.internet_entry_points:
            entry_id = entry["resource_id"]
            queue.append((entry_id, 0, [entry["resource_name"]]))  # (resource_id, distance, path)
            visited.add(entry_id)
        
        while queue:
            curr_id, distance, path = queue.popleft()
            
            # Traverse to all neighbors
            for next_id, conn in graph.get(curr_id, []):
                if next_id not in visited:
                    visited.add(next_id)
                    self.accessible_resources.add(next_id)
                    
                    next_res = self.resources_by_id.get(next_id)
                    new_path = path + [next_res.get("resource_name", f"resource_{next_id}")]
                    
                    # Store accessibility path info
                    path_obj = AccessibilityPath(
                        resource_id=next_id,
                        resource_name=next_res.get("resource_name"),
                        resource_type=next_res.get("resource_type"),
                        distance=distance + 1,
                        path_nodes=new_path,
                        entry_point=path[0],
                        via_public_ip=any("public_ip" in self.resources_by_id.get(pid, {}).get("resource_type", "").lower() for pid in {curr_id}),
                        via_endpoint=any("api_management" in self.resources_by_id.get(pid, {}).get("resource_type", "").lower() or "app_service" in self.resources_by_id.get(pid, {}).get("resource_type", "").lower() for pid in {curr_id}),
                        via_managed_identity=any("managed_identity" in conn.get("authentication", "").lower() for conn in [conn]),
                        confirmed=conn.get("confirmed", True) if "confirmed" in conn else True,
                        auth_level=self._determine_auth_level(conn),
                    )
                    self.accessibility_paths[next_id].append(path_obj)
                    
                    queue.append((next_id, distance + 1, new_path))

    def _determine_auth_level(self, connection: dict) -> str:
        """Determine authentication level for a connection."""
        auth = (connection.get("authentication") or "").lower()
        auth_method = (connection.get("auth_method") or "").lower()
        
        if not auth and not auth_method:
            return "none"
        if "key" in auth or "sas" in auth or "key" in auth_method:
            return "key"
        if "identity" in auth or "managed" in auth or "identity" in auth_method:
            return "identity"
        if "certificate" in auth or "cert" in auth or "tls" in auth:
            return "certificate"
        
        return "other"

    def store_results(self) -> None:
        """Store accessibility analysis results in database."""
        with get_db_connection() as conn:
            # Clear any previous results for this experiment
            conn.execute(
                "DELETE FROM resource_internet_accessibility WHERE experiment_id = ?",
                [self.experiment_id],
            )
            
            for res_id in self.resources_by_id:
                is_accessible = res_id in self.accessible_resources
                paths = self.accessibility_paths.get(res_id, [])
                
                if paths:
                    # Use the shortest path
                    shortest_path = min(paths, key=lambda p: p.distance)
                    path_data = {
                        "entry_point": shortest_path.entry_point,
                        "distance": shortest_path.distance,
                        "path_nodes": shortest_path.path_nodes,
                        "via_public_ip": shortest_path.via_public_ip,
                        "via_endpoint": shortest_path.via_endpoint,
                        "via_managed_identity": shortest_path.via_managed_identity,
                        "auth_level": shortest_path.auth_level,
                    }
                else:
                    path_data = None
                
                conn.execute(
                    """
                    INSERT INTO resource_internet_accessibility
                    (experiment_id, resource_id, resource_name, resource_type, 
                     is_internet_accessible, shortest_path_distance, path_data, 
                     via_public_ip, via_public_endpoint, via_managed_identity,
                     entry_point, auth_level, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [
                        self.experiment_id,
                        res_id,
                        self.resources_by_id[res_id].get("resource_name"),
                        self.resources_by_id[res_id].get("resource_type"),
                        1 if is_accessible else 0,
                        paths[0].distance if paths else None,
                        json.dumps(path_data) if path_data else None,
                        1 if any(p.via_public_ip for p in paths) else 0,
                        1 if any(p.via_endpoint for p in paths) else 0,
                        1 if any(p.via_managed_identity for p in paths) else 0,
                        next((p.entry_point for p in paths), None),
                        next((p.auth_level for p in paths), None),
                    ],
                )
            
            conn.commit()

    def run(self) -> None:
        """Execute the full analysis."""
        print(f"[*] Analyzing internet accessibility for experiment: {self.experiment_id}")
        
        self.load_data()
        print(f"[*] Loaded {len(self.resources_by_id)} resources and {len(self.connections_list)} connections")
        
        self.find_internet_entry_points()
        print(f"[*] Found {len(self.internet_entry_points)} Internet entry points")
        for entry in self.internet_entry_points:
            print(f"    - {entry['resource_name']} ({entry['via_type']})")
        
        self.traverse_from_internet()
        print(f"[*] Found {len(self.accessible_resources)} Internet-accessible resources")
        
        self.store_results()
        print(f"[+] Results stored in resource_internet_accessibility table")

        # Print summary
        print("\n[*] Internet Accessibility Summary:")
        direct_count = len([e for e in self.internet_entry_points if e["via_type"] == "public_ip"])
        endpoint_count = len([e for e in self.internet_entry_points if e["via_type"] == "public_endpoint"])
        print(f"    - Direct public IPs: {direct_count}")
        print(f"    - Public endpoints: {endpoint_count}")
        
        # Count resources by distance
        distance_counts = defaultdict(int)
        for paths_list in self.accessibility_paths.values():
            if paths_list:
                min_dist = min(p.distance for p in paths_list)
                distance_counts[min_dist] += 1
        
        for dist in sorted(distance_counts.keys()):
            print(f"    - {distance_counts[dist]} resources at distance {dist}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure resource_internet_accessibility table exists."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS resource_internet_accessibility (
        id INTEGER PRIMARY KEY,
        experiment_id TEXT NOT NULL,
        resource_id INTEGER NOT NULL,
        resource_name TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        is_internet_accessible BOOLEAN DEFAULT 0,
        shortest_path_distance INTEGER,
        path_data TEXT,
        via_public_ip BOOLEAN DEFAULT 0,
        via_public_endpoint BOOLEAN DEFAULT 0,
        via_managed_identity BOOLEAN DEFAULT 0,
        entry_point TEXT,
        auth_level TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(experiment_id, resource_id),
        FOREIGN KEY (experiment_id) REFERENCES repositories(experiment_id)
    );

    CREATE INDEX IF NOT EXISTS idx_internet_accessibility_experiment
        ON resource_internet_accessibility(experiment_id, is_internet_accessible);
    CREATE INDEX IF NOT EXISTS idx_internet_accessibility_distance
        ON resource_internet_accessibility(experiment_id, shortest_path_distance);
    """)
    conn.commit()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze internet accessibility for resources in an experiment"
    )
    parser.add_argument(
        "--experiment-id",
        required=True,
        help="Experiment ID to analyze",
    )
    
    args = parser.parse_args()
    
    # Ensure schema
    with get_db_connection() as conn:
        ensure_schema(conn)
    
    # Run analysis
    analyzer = InternetAccessibilityAnalyzer(args.experiment_id)
    analyzer.run()


if __name__ == "__main__":
    main()
