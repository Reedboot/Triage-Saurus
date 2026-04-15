#!/usr/bin/env python3
"""Integration module for internet accessibility data in diagram generation.

Provides helper functions to:
1. Load computed internet accessibility from the database
2. Enrich resource nodes with accessibility information
3. Create visibility indicators in diagrams
"""

import json
from typing import Dict, Optional, List, Tuple
from pathlib import Path, pathlib
import sys

# Add parent directory to path
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(ROOT / "Persist"))

from db_helpers import get_db_connection


class InternetAccessibilityHelper:
    """Helper to load and access internet accessibility data."""

    def __init__(self, experiment_id: str):
        """Initialize with experiment ID."""
        self.experiment_id = experiment_id
        self._cache: Dict[int, dict] = {}  # resource_id -> accessibility info
        self.loaded = False

    def load(self) -> None:
        """Load internet accessibility data from database."""
        if self.loaded:
            return

        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT 
                    id, resource_id, resource_name, resource_type,
                    is_internet_accessible, shortest_path_distance,
                    path_data, via_public_ip, via_public_endpoint,
                    via_managed_identity, entry_point, auth_level
                FROM resource_internet_accessibility
                WHERE experiment_id = ?
                """,
                [self.experiment_id],
            ).fetchall()

            for row in rows:
                try:
                    path_data = json.loads(row["path_data"]) if row["path_data"] else None
                except Exception:
                    path_data = None

                self._cache[row["resource_id"]] = {
                    "resource_name": row["resource_name"],
                    "resource_type": row["resource_type"],
                    "is_internet_accessible": row["is_internet_accessible"] == 1,
                    "via_public_ip": row["via_public_ip"] == 1,
                    "via_public_endpoint": row["via_public_endpoint"] == 1,
                    "via_managed_identity": row["via_managed_identity"] == 1,
                    "shortest_path_distance": row["shortest_path_distance"],
                    "entry_point": row["entry_point"],
                    "auth_level": row["auth_level"],
                    "path_data": path_data,
                }

        self.loaded = True

    def is_internet_accessible(self, resource_id: int) -> bool:
        """Check if resource is internet accessible."""
        if not self.loaded:
            self.load()
        return self._cache.get(resource_id, {}).get("is_internet_accessible", False)

    def get_accessibility_info(self, resource_id: int) -> Optional[dict]:
        """Get full accessibility information for a resource."""
        if not self.loaded:
            self.load()
        return self._cache.get(resource_id)

    def get_internet_accessible_resources(self) -> List[Tuple[int, dict]]:
        """Get all internet-accessible resources."""
        if not self.loaded:
            self.load()
        return [
            (res_id, info)
            for res_id, info in self._cache.items()
            if info.get("is_internet_accessible")
        ]

    def get_accessibility_badge(self, resource_id: int) -> str:
        """Get a textual badge/indicator for display."""
        info = self.get_accessibility_info(resource_id)
        if not info:
            return ""

        if not info.get("is_internet_accessible"):
            return ""

        # Build badge based on access method and distance
        badges = []

        if info.get("via_public_ip"):
            badges.append("📍 Public IP")
        elif info.get("via_public_endpoint"):
            badges.append("🟡 Public Endpoint")
        elif info.get("via_managed_identity"):
            badges.append("🔐 Via Identity")
        else:
            badges.append("🌐 Internet Accessible")

        distance = info.get("shortest_path_distance")
        if distance is not None:
            badges.append(f"({distance} hops)")

        return " ".join(badges)

    def get_risk_color(self, resource_id: int) -> Optional[str]:
        """Get color code for internet accessibility risk."""
        info = self.get_accessibility_info(resource_id)
        if not info:
            return None

        if not info.get("is_internet_accessible"):
            return None

        # Direct public IP exposure = red
        if info.get("via_public_ip"):
            return "#ff0000"  # Red

        # Public endpoint = orange
        if info.get("via_public_endpoint"):
            return "#ff8c00"  # Orange

        # Via managed identity = yellow
        if info.get("via_managed_identity"):
            return "#ffcc00"  # Yellow

        # Generic internet accessible = amber
        return "#ffa500"  # Amber

    def create_accessibility_table(self) -> str:
        """Create an HTML/Markdown table of internet-accessible resources."""
        if not self.loaded:
            self.load()

        accessible = self.get_internet_accessible_resources()
        if not accessible:
            return _no_internet_accessible_detected()

        rows = []
        for res_id, info in sorted(accessible, key=lambda x: x[1].get("shortest_path_distance", 999)):
            resource_name = info.get("resource_name", f"resource_{res_id}")
            resource_type = info.get("resource_type", "unknown")
            distance = info.get("shortest_path_distance", "?")
            entry_point = info.get("entry_point", "?")
            auth_level = info.get("auth_level", "unknown")
            via_method = _get_via_method(info)

            rows.append(
                f"| {resource_name} | {resource_type} | {distance} | {via_method} | {auth_level} | {entry_point} |"
            )

        header = (
            "| Resource Name | Resource Type | Hops | Access Method | Auth Level | Entry Point |\n"
            "|---|---|---|---|---|---|\n"
        )

        return "### Internet-Accessible Resources\n\n" + header + "\n".join(rows) + "\n"


def _get_via_method(info: dict) -> str:
    """Extract access method from info."""
    if info.get("via_public_ip"):
        return "Public IP"
    if info.get("via_public_endpoint"):
        return "Public Endpoint"
    if info.get("via_managed_identity"):
        return "Managed Identity"
    return "Unknown"


def _no_internet_accessible_detected() -> str:
    """Return message when no internet-accessible resources found."""
    return (
        "### Internet-Accessible Resources\n\n"
        "✅ **No Internet-accessible resources detected**\n\n"
        "This is the most secure posture. All resources are internal or protected.\n"
    )


def enrich_resource_with_accessibility(
    resource: dict, helper: InternetAccessibilityHelper
) -> dict:
    """Add accessibility information to a resource dict."""
    resource_id = resource.get("id")
    if not resource_id:
        return resource

    # Add accessibility flag
    resource["_is_internet_accessible"] = helper.is_internet_accessible(resource_id)

    # Add badge for display
    badge = helper.get_accessibility_badge(resource_id)
    if badge:
        resource["_accessibility_badge"] = badge
        # Add to label if not already present
        existing_label = resource.get("_label", resource.get("resource_name", ""))
        if badge not in existing_label:
            resource["_label"] = f"{existing_label}\n{badge}"

    # Add color override for high-risk resources
    color = helper.get_risk_color(resource_id)
    if color:
        resource["_internet_exposed_color"] = color

    return resource


def query_accessibility_metrics(experiment_id: str) -> dict:
    """Query and return accessibility metrics for summary."""
    helper = InternetAccessibilityHelper(experiment_id)
    helper.load()

    accessible_resources = helper.get_internet_accessible_resources()
    total = len(helper._cache)

    by_method = {
        "via_public_ip": 0,
        "via_public_endpoint": 0,
        "via_managed_identity": 0,
    }

    distances = []
    for _, info in accessible_resources:
        if info.get("via_public_ip"):
            by_method["via_public_ip"] += 1
        if info.get("via_public_endpoint"):
            by_method["via_public_endpoint"] += 1
        if info.get("via_managed_identity"):
            by_method["via_managed_identity"] += 1

        dist = info.get("shortest_path_distance")
        if dist is not None:
            distances.append(dist)

    return {
        "total_resources": total,
        "internet_accessible_count": len(accessible_resources),
        "internet_accessible_percentage": (
            len(accessible_resources) / total * 100 if total > 0 else 0
        ),
        "by_access_method": by_method,
        "shortest_path_distance_avg": (
            sum(distances) / len(distances) if distances else None
        ),
        "shortest_path_distance_max": max(distances) if distances else None,
    }
