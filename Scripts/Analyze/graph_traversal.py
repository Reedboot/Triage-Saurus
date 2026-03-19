#!/usr/bin/env python3
"""
graph_traversal.py

Graph traversal for exposure analysis. Detects internet reachability paths
from Entry Points to Compute/Data resources via resource connections.

Classifies resources as:
- Direct Exposure: reachable from entry point without passing through countermeasure
- Mitigated: path includes countermeasure (WAF, App Gateway, NSG, Firewall)
- Isolated: not reachable from any entry point
"""

import sys
from pathlib import Path
from typing import Set, Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import deque
import json

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from resource_normalizer import ResourceNormalizer, UnifiedRole


@dataclass
class TraversalPath:
    """Represents a traversal path from entry point to target resource."""
    source_id: int  # Entry point resource ID
    target_id: int  # Target resource ID
    path_nodes: List[int] = field(default_factory=list)
    path_length: int = 0
    has_countermeasure: bool = False
    countermeasures: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "path_nodes": self.path_nodes,
            "path_length": self.path_length,
            "has_countermeasure": self.has_countermeasure,
            "countermeasures": self.countermeasures,
        }


@dataclass
class ExposureClassification:
    """Classification of a resource's exposure."""
    resource_id: int
    resource_name: str
    resource_type: str
    normalized_role: str
    exposure_level: str  # direct_exposure, mitigated, isolated
    has_internet_path: bool
    traversal_paths: List[TraversalPath] = field(default_factory=list)
    entry_points_reached: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "normalized_role": self.normalized_role,
            "exposure_level": self.exposure_level,
            "has_internet_path": self.has_internet_path,
            "traversal_paths": [p.to_dict() for p in self.traversal_paths],
            "entry_points_reached": self.entry_points_reached,
        }


class GraphTraversal:
    """Traverse resource graph to detect internet exposure paths."""

    def __init__(self, normalizer: Optional[ResourceNormalizer] = None):
        """Initialize with optional resource normalizer."""
        self.normalizer = normalizer or ResourceNormalizer()
        # Build adjacency list during traversal setup
        self.adjacency: Dict[int, List[int]] = {}
        self.resource_map: Dict[int, dict] = {}
        self.entry_points: Set[int] = set()
        self.countermeasures: Set[int] = set()
        self.compute_data: Set[int] = set()

    def setup(self, resources: List[dict], connections: List[dict]) -> None:
        """
        Setup graph from resources and connections.

        Args:
            resources: List of dicts with id, resource_name, resource_type, provider
            connections: List of dicts with source_resource_id, target_resource_id
        """
        self.resource_map = {r["id"]: r for r in resources}
        self.adjacency = {r["id"]: [] for r in resources}

        # Add edges from connections
        for conn in connections:
            src_id = conn.get("source_resource_id")
            tgt_id = conn.get("target_resource_id")
            if src_id and tgt_id and src_id in self.adjacency:
                self.adjacency[src_id].append(tgt_id)

        # Classify resources by role
        for resource_id, resource in self.resource_map.items():
            normalized = self.normalizer.normalize(
                resource["resource_name"],
                resource["resource_type"],
                resource.get("provider"),
            )
            resource["normalized_role"] = normalized.normalized_role.value

            if normalized.normalized_role == UnifiedRole.ENTRY_POINT:
                self.entry_points.add(resource_id)
            elif normalized.normalized_role == UnifiedRole.COUNTERMEASURE:
                self.countermeasures.add(resource_id)
            elif normalized.normalized_role in (UnifiedRole.COMPUTE, UnifiedRole.DATA):
                self.compute_data.add(resource_id)

    def _bfs_from_entry_point(self, entry_point_id: int) -> Dict[int, TraversalPath]:
        """
        BFS from an entry point, tracking paths and whether they pass through countermeasures.

        Returns dict of {target_id: TraversalPath}
        """
        paths = {}
        queue = deque([(entry_point_id, [entry_point_id], False, [])])

        while queue:
            current_id, path_nodes, has_cm, cms = queue.popleft()

            # Explore neighbors
            for neighbor_id in self.adjacency.get(current_id, []):
                if neighbor_id not in path_nodes:  # Avoid cycles
                    new_path = path_nodes + [neighbor_id]
                    new_has_cm = has_cm or (neighbor_id in self.countermeasures)
                    new_cms = cms + ([neighbor_id] if neighbor_id in self.countermeasures else [])

                    # Record path to this resource
                    if neighbor_id not in paths or len(new_path) < paths[neighbor_id].path_length:
                        paths[neighbor_id] = TraversalPath(
                            source_id=entry_point_id,
                            target_id=neighbor_id,
                            path_nodes=new_path,
                            path_length=len(new_path),
                            has_countermeasure=new_has_cm,
                            countermeasures=new_cms,
                        )

                    # Continue BFS
                    queue.append((neighbor_id, new_path, new_has_cm, new_cms))

        return paths

    def classify_exposure(self) -> Dict[int, ExposureClassification]:
        """
        Classify all resources as exposed, mitigated, or isolated.

        Returns dict of {resource_id: ExposureClassification}
        """
        classifications: Dict[int, ExposureClassification] = {}

        # Traverse from each entry point
        all_paths: Dict[int, List[TraversalPath]] = {}

        for entry_point_id in self.entry_points:
            paths = self._bfs_from_entry_point(entry_point_id)
            for target_id, path in paths.items():
                if target_id not in all_paths:
                    all_paths[target_id] = []
                all_paths[target_id].append(path)

        # Classify each compute/data resource
        for resource_id in self.compute_data:
            resource = self.resource_map[resource_id]
            paths = all_paths.get(resource_id, [])
            entry_points_reached = list(set(p.source_id for p in paths))

            if not paths:
                # Not reachable from any entry point
                classification = ExposureClassification(
                    resource_id=resource_id,
                    resource_name=resource["resource_name"],
                    resource_type=resource["resource_type"],
                    normalized_role=resource.get("normalized_role", "unknown"),
                    exposure_level="isolated",
                    has_internet_path=False,
                    traversal_paths=[],
                    entry_points_reached=[],
                )
            elif all(p.has_countermeasure for p in paths):
                # All paths have countermeasures
                classification = ExposureClassification(
                    resource_id=resource_id,
                    resource_name=resource["resource_name"],
                    resource_type=resource["resource_type"],
                    normalized_role=resource.get("normalized_role", "unknown"),
                    exposure_level="mitigated",
                    has_internet_path=True,
                    traversal_paths=paths,
                    entry_points_reached=entry_points_reached,
                )
            else:
                # At least one path without countermeasure
                classification = ExposureClassification(
                    resource_id=resource_id,
                    resource_name=resource["resource_name"],
                    resource_type=resource["resource_type"],
                    normalized_role=resource.get("normalized_role", "unknown"),
                    exposure_level="direct_exposure",
                    has_internet_path=True,
                    traversal_paths=paths,
                    entry_points_reached=entry_points_reached,
                )

            classifications[resource_id] = classification
        
        # Also classify entry points themselves as directly exposed
        for entry_point_id in self.entry_points:
            resource = self.resource_map[entry_point_id]
            classifications[entry_point_id] = ExposureClassification(
                resource_id=entry_point_id,
                resource_name=resource["resource_name"],
                resource_type=resource["resource_type"],
                normalized_role=resource.get("normalized_role", "unknown"),
                exposure_level="direct_exposure",
                has_internet_path=True,
                traversal_paths=[],  # Entry points are the source, not a destination
                entry_points_reached=[entry_point_id],  # Self-reference
            )

        return classifications

    def get_exposed_resources(self) -> List[ExposureClassification]:
        """Get all resources with internet exposure (direct or mitigated)."""
        classifications = self.classify_exposure()
        return [c for c in classifications.values() if c.has_internet_path]

    def get_directly_exposed_resources(self) -> List[ExposureClassification]:
        """Get resources with direct internet exposure (no countermeasures)."""
        classifications = self.classify_exposure()
        return [c for c in classifications.values() if c.exposure_level == "direct_exposure"]


if __name__ == "__main__":
    # Test with simple graph
    resources = [
        {"id": 1, "resource_name": "igw", "resource_type": "aws_internet_gateway", "provider": "aws"},
        {"id": 2, "resource_name": "waf", "resource_type": "aws_waf_web_acl", "provider": "aws"},
        {"id": 3, "resource_name": "s3", "resource_type": "aws_s3_bucket", "provider": "aws"},
        {"id": 4, "resource_name": "rds", "resource_type": "aws_db_instance", "provider": "aws"},
    ]

    connections = [
        {"source_resource_id": 1, "target_resource_id": 2},  # IGW -> WAF
        {"source_resource_id": 2, "target_resource_id": 3},  # WAF -> S3
        {"source_resource_id": 1, "target_resource_id": 4},  # IGW -> RDS (no WAF!)
    ]

    traversal = GraphTraversal()
    traversal.setup(resources, connections)

    classifications = traversal.classify_exposure()
    for r_id, classification in classifications.items():
        print(f"{classification.resource_name}: {classification.exposure_level}")

    print(f"\nDirectly exposed: {len(traversal.get_directly_exposed_resources())}")
    print(f"All exposed: {len(traversal.get_exposed_resources())}")
