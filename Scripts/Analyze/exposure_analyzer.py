#!/usr/bin/env python3
"""
exposure_analyzer.py

Main orchestrator for internet exposure analysis.
Combines resource normalization, graph traversal, and risk scoring to detect
and assess internet exposure across multi-cloud infrastructure.

Usage:
  python3 exposure_analyzer.py --experiment <exp_id> [--db-path <path>]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))

from resource_normalizer import ResourceNormalizer, UnifiedRole
from graph_traversal import GraphTraversal
from risk_scorer import RiskScorer

try:
    import db_helpers
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
    import db_helpers


class InternetExposureAnalyzer:
    """Orchestrate exposure analysis for an experiment."""

    def __init__(self, experiment_id: str, db_path: Optional[Path] = None):
        """Initialize analyzer with database connection."""
        self.experiment_id = experiment_id
        self.db_path = db_path or db_helpers.DB_PATH
        self.normalizer = ResourceNormalizer()
        self.traversal: Optional[GraphTraversal] = None
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.db_path), timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout = 30000")
            # Ensure schema
            db_helpers._ensure_schema(self.conn)
        return self.conn

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def load_resources(self) -> List[dict]:
        """Load all resources for this experiment."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT id, resource_name, resource_type, provider, repo_id
            FROM resources
            WHERE experiment_id = ?
            """,
            (self.experiment_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def load_connections(self) -> List[dict]:
        """Load all resource connections for this experiment."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT id, source_resource_id, target_resource_id, connection_type
            FROM resource_connections
            WHERE experiment_id = ?
            """,
            (self.experiment_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def load_opengrep_findings(self) -> Dict[int, dict]:
        """Load OpenGrep findings per resource."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT DISTINCT
              f.resource_id,
              f.rule_id,
              f.base_severity
            FROM findings f
            WHERE f.experiment_id = ? AND f.rule_id IS NOT NULL
            """,
            (self.experiment_id,),
        )
        findings_by_resource = {}
        for row in cursor.fetchall():
            resource_id = row["resource_id"]
            if resource_id not in findings_by_resource:
                findings_by_resource[resource_id] = []
            findings_by_resource[resource_id].append({
                "rule_id": row["rule_id"],
                "severity": row["base_severity"],
            })
        return findings_by_resource

    def run(self) -> Dict[str, any]:
        """
        Run full exposure analysis.

        Returns:
            Dict with analysis results and summary stats
        """
        print(f"[*] Starting exposure analysis for experiment {self.experiment_id}")

        # Load resources and connections
        print("[*] Loading resources and connections...")
        resources = self.load_resources()
        connections = self.load_connections()
        findings = self.load_opengrep_findings()

        if not resources:
            print("[!] No resources found for this experiment")
            return {"status": "error", "message": "No resources found"}

        print(f"[+] Loaded {len(resources)} resources, {len(connections)} connections")

        # Setup graph traversal
        print("[*] Setting up graph traversal...")
        self.traversal = GraphTraversal(self.normalizer)
        self.traversal.setup(resources, connections)

        # Classify exposure
        print("[*] Classifying exposure...")
        classifications = self.traversal.classify_exposure()

        # Persist exposure analysis
        print("[*] Persisting exposure analysis to database...")
        self._persist_exposure_analysis(resources, classifications, findings)

        # Property-based exposure: mark resources with public-access findings
        # as direct_exposure even if BFS didn't reach them (e.g. public buckets,
        # public databases in clouds where we have no entry-point topology yet).
        print("[*] Applying property-based exposure overrides...")
        self._apply_property_based_exposure(resources)

        # Propagate exposure from children to parents (e.g., VM with public IP child)
        print("[*] Propagating exposure from children to parents...")
        self._propagate_exposure_from_children()

        # Persist internet exposure paths (B2)
        print("[*] Persisting internet exposure paths...")
        self._persist_internet_exposure_paths(classifications)

        # Populate trust boundaries (B3)
        print("[*] Populating trust boundaries...")
        self._populate_trust_boundaries(resources, classifications)

        # Generate summary
        directly_exposed = sum(1 for c in classifications.values() if c.exposure_level == "direct_exposure")
        mitigated = sum(1 for c in classifications.values() if c.exposure_level == "mitigated")
        isolated = sum(1 for c in classifications.values() if c.exposure_level == "isolated")

        print(f"\n[+] Analysis complete:")
        print(f"    - Directly exposed: {directly_exposed}")
        print(f"    - Mitigated: {mitigated}")
        print(f"    - Isolated: {isolated}")

        return {
            "status": "success",
            "experiment_id": self.experiment_id,
            "resources_analyzed": len(resources),
            "directly_exposed": directly_exposed,
            "mitigated": mitigated,
            "isolated": isolated,
            "total_exposed": directly_exposed + mitigated,
        }

    def _apply_property_based_exposure(self, resources: List[dict]) -> None:
        """Override exposure_level to direct_exposure for resources with public-access findings.

        Some resources are directly internet-accessible via misconfigured properties
        (public bucket ACLs, public database endpoints, open firewall rules) without
        needing a network gateway in the topology. This is common in Alicloud, OCI, and GCP.

        Rule ID patterns that indicate direct internet exposure:
          - *-public-access* (OSS buckets, GCS buckets, OCI buckets, Azure mysql)
          - *-public-ip*     (Cloud SQL with public IP)
          - *-allow-all*     (GCP firewall allowing all ports / Azure NSG allowing all)
          - *-open-all-ports (GCP compute firewall open to world)
          - *-bigquery-dataset-public-access (GCP BigQuery public dataset)
        """
        # Patterns in rule_id that signal direct public internet access
        _PUBLIC_ACCESS_PATTERNS = (
            "-public-access",
            "-public-ip",
            "-network-allow-all",
            "-open-all-ports",
            "-allow-all-ports",
            "-bigquery-dataset-public",
        )

        conn = self.connect()
        try:
            # Find findings with public-access rule IDs for this experiment
            rows = conn.execute("""
                SELECT DISTINCT f.resource_id, r.resource_name, r.resource_type, r.provider
                FROM findings f
                JOIN resources r ON r.id = f.resource_id
                WHERE f.experiment_id = ?
                  AND f.resource_id IS NOT NULL
            """, (self.experiment_id,)).fetchall()

            upgraded = 0
            for row in rows:
                resource_id = row[0]
                rule_ids_rows = conn.execute(
                    "SELECT rule_id FROM findings WHERE experiment_id = ? AND resource_id = ?",
                    (self.experiment_id, resource_id),
                ).fetchall()
                rule_ids = [r[0] or "" for r in rule_ids_rows]

                is_public = any(
                    pat in rule_id.lower()
                    for rule_id in rule_ids
                    for pat in _PUBLIC_ACCESS_PATTERNS
                )
                if not is_public:
                    continue

                # Only upgrade if currently isolated (don't downgrade direct/mitigated)
                existing = conn.execute(
                    "SELECT exposure_level FROM exposure_analysis WHERE experiment_id = ? AND resource_id = ?",
                    (self.experiment_id, resource_id),
                ).fetchone()

                if existing and existing[0] == "isolated":
                    conn.execute("""
                        UPDATE exposure_analysis
                        SET exposure_level = 'direct_exposure',
                            has_internet_path = 1,
                            notes = COALESCE(notes || ' | ', '') || 'property-based: public access finding'
                        WHERE experiment_id = ? AND resource_id = ?
                    """, (self.experiment_id, resource_id))
                    upgraded += 1
                elif not existing:
                    # No existing row — insert a minimal one
                    normalized = self.normalizer.normalize(row[1], row[2], row[3])
                    conn.execute("""
                        INSERT INTO exposure_analysis
                          (experiment_id, resource_id, resource_name, resource_type, provider,
                           normalized_role, is_entry_point, is_countermeasure, is_compute_or_data,
                           exposure_level, has_internet_path, notes)
                        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1, 'direct_exposure', 1,
                                'property-based: public access finding')
                    """, (self.experiment_id, resource_id, row[1], row[2], row[3],
                          normalized.normalized_role.value))
                    upgraded += 1

            if upgraded:
                conn.commit()
                print(f"[+] Property-based exposure: upgraded {upgraded} resource(s) to direct_exposure")
        except Exception as e:
            print(f"[WARN] Property-based exposure detection failed: {e}", file=sys.stderr)

    def _propagate_exposure_from_children(self) -> None:
        """Propagate exposure from children to parents.
        
        If a child resource has direct_exposure or mitigated exposure,
        its parent should inherit at least the same exposure level.
        """
        conn = self.connect()
        try:
            # Build child → parent map
            child_parent_rows = conn.execute("""
                SELECT child.id, child.parent_resource_id, 
                       parent.resource_name, parent.resource_type
                FROM resources child
                JOIN resources parent ON child.parent_resource_id = parent.id
                WHERE child.experiment_id = ? AND child.parent_resource_id IS NOT NULL
            """, (self.experiment_id,)).fetchall()
            
            if not child_parent_rows:
                return
            
            upgraded = 0
            for child_id, parent_id, parent_name, parent_type in child_parent_rows:
                # Get child's exposure level
                child_exp = conn.execute("""
                    SELECT exposure_level FROM exposure_analysis
                    WHERE experiment_id = ? AND resource_id = ?
                """, (self.experiment_id, child_id)).fetchone()
                
                if not child_exp or child_exp[0] == 'isolated':
                    continue  # Skip if child has no exposure or is isolated
                
                child_exposure = child_exp[0]  # 'direct_exposure' or 'mitigated'
                
                # Check parent's current exposure
                parent_exp = conn.execute("""
                    SELECT exposure_level FROM exposure_analysis
                    WHERE experiment_id = ? AND resource_id = ?
                """, (self.experiment_id, parent_id)).fetchone()
                
                if not parent_exp:
                    # Parent has no exposure_analysis entry — create one
                    parent_resource = conn.execute("""
                        SELECT resource_name, resource_type, provider FROM resources
                        WHERE id = ?
                    """, (parent_id,)).fetchone()
                    if parent_resource:
                        normalized = self.normalizer.normalize(parent_resource[0], parent_resource[1], parent_resource[2])
                        conn.execute("""
                            INSERT INTO exposure_analysis
                              (experiment_id, resource_id, resource_name, resource_type, provider,
                               normalized_role, is_entry_point, is_countermeasure, is_compute_or_data,
                               exposure_level, has_internet_path, notes)
                            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1, ?, 1,
                                    'inherited-from-child')
                        """, (self.experiment_id, parent_id, parent_resource[0], parent_resource[1],
                              parent_resource[2], normalized.normalized_role.value, child_exposure))
                        upgraded += 1
                elif parent_exp[0] == 'isolated' and child_exposure in ('direct_exposure', 'mitigated'):
                    # Parent is isolated but child is exposed — upgrade parent
                    conn.execute("""
                        UPDATE exposure_analysis
                        SET exposure_level = ?,
                            has_internet_path = 1,
                            notes = COALESCE(notes || ' | ', '') || 'inherited-from-child'
                        WHERE experiment_id = ? AND resource_id = ?
                    """, (child_exposure, self.experiment_id, parent_id))
                    upgraded += 1
            
            if upgraded:
                conn.commit()
                print(f"[+] Exposure propagation: upgraded {upgraded} parent resource(s)")
        except Exception as e:
            print(f"[WARN] Exposure propagation failed: {e}", file=sys.stderr)

    def _persist_exposure_analysis(
        self,
        resources: List[dict],
        classifications: Dict[int, any],
        findings: Dict[int, List[dict]],
    ) -> None:
        """Persist exposure analysis results to database."""
        conn = self.connect()
        cursor = conn.cursor()

        # Clear existing records for this experiment so re-runs get fresh scores
        cursor.execute("DELETE FROM exposure_analysis WHERE experiment_id = ?", (self.experiment_id,))
        cursor.execute("DELETE FROM exposure_risk_scoring WHERE experiment_id = ?", (self.experiment_id,))

        # Build resource map for quick lookup
        resource_map = {r["id"]: r for r in resources}

        for resource_id, classification in classifications.items():
            resource = resource_map.get(resource_id, {})

            # Get OpenGrep findings for this resource
            resource_findings = findings.get(resource_id, [])
            most_severe = None
            if resource_findings:
                most_severe = max(resource_findings, key=lambda f: RiskScorer.severity_to_score(f.get("severity")))

            # Compute risk score
            severity = most_severe.get("severity") if most_severe else None
            risk_score = RiskScorer.compute_score(
                RiskScorer.severity_to_score(severity),
                classification.exposure_level,
                any(p.has_countermeasure for p in classification.traversal_paths),
            )

            # Insert into exposure_analysis
            cursor.execute(
                """
                INSERT OR REPLACE INTO exposure_analysis
                (experiment_id, resource_id, resource_name, resource_type, provider,
                 normalized_role, is_entry_point, is_countermeasure, is_compute_or_data,
                 exposure_level, exposure_path, has_internet_path, opengrep_violations,
                 base_severity, risk_score, confidence, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.experiment_id,
                    resource_id,
                    classification.resource_name,
                    classification.resource_type,
                    resource.get("provider", "unknown"),
                    classification.normalized_role,
                    1 if resource_id in self.traversal.entry_points else 0,
                    1 if resource_id in self.traversal.countermeasures else 0,
                    1 if resource_id in self.traversal.compute_data else 0,
                    classification.exposure_level,
                    json.dumps([p.to_dict() for p in classification.traversal_paths]),
                    1 if classification.has_internet_path else 0,
                    json.dumps([{"rule_id": f["rule_id"], "severity": f["severity"]}
                               for f in resource_findings]),
                    severity,
                    risk_score,
                    "medium",
                    datetime.now().isoformat(),
                ),
            )

            # Insert into exposure_risk_scoring
            if resource_findings:
                for finding in resource_findings:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO exposure_risk_scoring
                        (experiment_id, resource_id, opengrep_rule_id, rule_severity,
                         severity_score, exposure_multiplier, final_risk_score,
                         exposure_factor, vulnerability_factor, scoring_method, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.experiment_id,
                            resource_id,
                            finding["rule_id"],
                            finding["severity"],
                            RiskScorer.severity_to_score(finding["severity"]),
                            RiskScorer.get_exposure_multiplier(
                                classification.exposure_level,
                                any(p.has_countermeasure for p in classification.traversal_paths),
                            ),
                            risk_score,
                            {
                                "direct_exposure": "Directly exposed to internet",
                                "mitigated": "Behind security controls",
                                "isolated": "Isolated from internet",
                            }.get(classification.exposure_level, "Unknown"),
                            finding["severity"],
                            "exposure_plus_vuln",
                            datetime.now().isoformat(),
                        ),
                    )

        conn.commit()

    def _persist_internet_exposure_paths(
        self,
        classifications: Dict[int, any],
    ) -> None:
        """Persist internet exposure paths to the internet_exposure_paths table (B2)."""
        conn = self.connect()
        cursor = conn.cursor()

        # Clear existing paths for this experiment
        cursor.execute(
            "DELETE FROM internet_exposure_paths WHERE experiment_id = ?",
            (self.experiment_id,),
        )

        path_counter = 0
        for resource_id, classification in classifications.items():
            if not classification.has_internet_path:
                continue
            for entry_point_id in (classification.entry_points_reached or []):
                if entry_point_id == resource_id:
                    continue  # Skip self-references (entry points)
                path_counter += 1
                path_id = f"{self.experiment_id}-{entry_point_id}-{resource_id}-{path_counter}"
                paths = [p for p in classification.traversal_paths if p.source_id == entry_point_id]
                best_path = paths[0] if paths else None
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO internet_exposure_paths
                    (experiment_id, path_id, source_resource_id, target_resource_id,
                     path_length, path_nodes, has_countermeasure, countermeasures_in_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.experiment_id,
                        path_id,
                        entry_point_id,
                        resource_id,
                        best_path.path_length if best_path else 1,
                        json.dumps(best_path.path_nodes if best_path else [entry_point_id, resource_id]),
                        1 if (best_path and best_path.has_countermeasure) else 0,
                        json.dumps(best_path.countermeasures if best_path else []),
                    ),
                )

        conn.commit()
        print(f"[+] Persisted {path_counter} internet exposure paths")

    def _populate_trust_boundaries(
        self,
        resources: List[dict],
        classifications: Dict[int, any],
    ) -> None:
        """Populate trust boundaries from VPC/subnet/exposure topology (B3)."""
        conn = self.connect()
        cursor = conn.cursor()

        # Clear existing trust boundaries for this experiment (members first, no FK cascade)
        cursor.execute(
            """DELETE FROM trust_boundary_members
               WHERE trust_boundary_id IN (
                   SELECT id FROM trust_boundaries WHERE experiment_id = ?
               )""",
            (self.experiment_id,),
        )
        cursor.execute(
            "DELETE FROM trust_boundaries WHERE experiment_id = ?",
            (self.experiment_id,),
        )

        # Fetch VPC/VNet containers from resource_connections (parent containers)
        vpc_types = ("aws_vpc", "azurerm_virtual_network", "google_compute_network", "oci_core_vcn", "alicloud_vpc")
        vpc_rows = conn.execute(
            "SELECT id, resource_name, resource_type, provider FROM resources WHERE experiment_id = ? AND resource_type IN ({})".format(
                ",".join("?" * len(vpc_types))
            ),
            (self.experiment_id, *vpc_types),
        ).fetchall()

        boundary_count = 0

        # Internet-Facing boundary
        internet_facing_ids = [
            r_id for r_id, c in classifications.items()
            if c.exposure_level in ("direct_exposure",) and c.has_internet_path
        ]
        if internet_facing_ids:
            cursor.execute(
                """INSERT INTO trust_boundaries
                   (experiment_id, name, boundary_type, description)
                   VALUES (?, ?, ?, ?)""",
                (self.experiment_id, "Internet-Facing", "internet",
                 "Resources directly accessible from the public internet"),
            )
            tb_id = cursor.lastrowid
            for r_id in internet_facing_ids:
                cursor.execute(
                    "INSERT OR IGNORE INTO trust_boundary_members (trust_boundary_id, resource_id) VALUES (?,?)",
                    (tb_id, r_id),
                )
            boundary_count += 1

        # Per-VPC boundary
        for vpc in vpc_rows:
            vpc_dict = dict(vpc)
            # Get resources contained within this VPC (direct and indirect children)
            children = conn.execute(
                """WITH RECURSIVE contained(rid) AS (
                     SELECT target_resource_id FROM resource_connections
                     WHERE source_resource_id = ? AND experiment_id = ?
                     UNION
                     SELECT rc.target_resource_id FROM resource_connections rc
                     JOIN contained c ON rc.source_resource_id = c.rid
                     WHERE rc.experiment_id = ?
                   )
                   SELECT rid FROM contained""",
                (vpc_dict["id"], self.experiment_id, self.experiment_id),
            ).fetchall()
            child_ids = [row[0] for row in children]
            if not child_ids:
                continue

            provider = vpc_dict.get("provider", "unknown")
            friendly_type = {
                "aws": "VPC",
                "azure": "VNet",
                "gcp": "VPC Network",
                "oci": "VCN",
                "oracle": "VCN",
                "alicloud": "VPC",
                "tencentcloud": "VPC",
                "huaweicloud": "VPC",
            }.get(provider, "VPC")
            cursor.execute(
                """INSERT INTO trust_boundaries
                   (experiment_id, name, boundary_type, provider, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (self.experiment_id, f"{friendly_type}: {vpc_dict['resource_name']}", "network_boundary",
                 provider, f"Network isolation boundary: {vpc_dict['resource_name']}"),
            )
            tb_id = cursor.lastrowid
            for child_id in child_ids:
                cursor.execute(
                    "INSERT OR IGNORE INTO trust_boundary_members (trust_boundary_id, resource_id) VALUES (?,?)",
                    (tb_id, child_id),
                )
            boundary_count += 1

        # Data Tier boundary (isolated databases/storage)
        data_tier_ids = [
            r_id for r_id, c in classifications.items()
            if c.exposure_level == "isolated"
            and c.normalized_role in ("data", "DataRole.DATA", "DATA", "data_store")
        ]
        if data_tier_ids:
            cursor.execute(
                """INSERT INTO trust_boundaries
                   (experiment_id, name, boundary_type, description)
                   VALUES (?, ?, ?, ?)""",
                (self.experiment_id, "Data Tier", "data_tier",
                 "Isolated data stores (databases, storage) with no direct internet path"),
            )
            tb_id = cursor.lastrowid
            for r_id in data_tier_ids:
                cursor.execute(
                    "INSERT OR IGNORE INTO trust_boundary_members (trust_boundary_id, resource_id) VALUES (?,?)",
                    (tb_id, r_id),
                )
            boundary_count += 1

        conn.commit()
        print(f"[+] Populated {boundary_count} trust boundaries")


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze internet exposure across multi-cloud resources"
    )
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--db-path", type=Path, help="Database path (default: cozo.db)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    analyzer = InternetExposureAnalyzer(args.experiment, args.db_path)
    try:
        result = analyzer.run()
        if result["status"] == "success":
            print("\n[✓] Exposure analysis completed successfully")
            return 0
        else:
            print(f"\n[✗] Analysis failed: {result.get('message')}")
            return 1
    except Exception as e:
        print(f"[✗] Error during analysis: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    finally:
        analyzer.close()


if __name__ == "__main__":
    sys.exit(main())
