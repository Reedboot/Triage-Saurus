#!/usr/bin/env python3
"""
infer_semantic_connections.py

Infer semantic resource connections from cloud topology heuristics.
Adds data_access / calls / depends_on edges to resource_connections table
so they appear in architecture diagrams and data flow analysis.

Rules:
1. ELB/ALB → EC2 instances in the same VPC (routes traffic)
2. EC2/App Service/Lambda → RDS/SQL Server in same VPC/region (likely DB client)
3. EKS/AKS/GKE cluster → ECR/ACR/container registry in same account
4. Lambda → S3 buckets in same account (common serverless pattern)
5. EC2 instances → S3 buckets in same account (storage client)

These are stored as 'data_access' connections in resource_connections.
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
import db_helpers


# Compute resource types → DB/storage resource types they likely connect to
_COMPUTE_TYPES = {
    "aws_instance", "aws_ec2_instance",
    "aws_lambda_function",
    "aws_ecs_service", "aws_ecs_task",
    "azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine",
    "azurerm_app_service", "azurerm_function_app",
    "google_compute_instance", "google_container_cluster",
    "oci_core_instance",
}

_DB_TYPES = {
    "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance",
    "aws_neptune_cluster", "aws_neptune_cluster_instance",
    "aws_dynamodb_table",
    "azurerm_mssql_server", "azurerm_mysql_server", "azurerm_postgresql_server",
    "azurerm_cosmosdb_account",
    "google_sql_database_instance", "google_bigtable_instance",
    "oci_database",
}

_STORAGE_TYPES = {
    "aws_s3_bucket",
    "azurerm_storage_account",
    "google_storage_bucket",
    "oci_objectstorage_bucket", "alicloud_oss_bucket",
}

_LB_TYPES = {
    "aws_elb", "aws_alb", "aws_lb", "aws_nlb",
    "azurerm_application_gateway", "azurerm_lb",
    "google_compute_global_forwarding_rule",
}

_CONTAINER_REGISTRY_TYPES = {
    "aws_ecr_repository",
    "azurerm_container_registry",
    "google_container_registry",
}

_K8S_TYPES = {
    "aws_eks_cluster",
    "azurerm_kubernetes_cluster",
    "google_container_cluster",
}


def _type_matches(resource_type: str, type_set: set) -> bool:
    rt = (resource_type or "").lower()
    return rt in type_set or any(t in rt for t in type_set)


def infer_connections(experiment_id: str, db_path=None) -> int:
    """
    Infer and persist semantic connections for an experiment.
    Returns count of connections added.
    """
    db = db_path or db_helpers.DB_PATH
    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")

    try:
        # Load resources
        resources = conn.execute(
            "SELECT id, resource_name, resource_type, provider, repo_id FROM resources WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchall()
        resources = [dict(r) for r in resources]

        # Get existing connections to avoid duplicates
        existing = conn.execute(
            "SELECT source_resource_id, target_resource_id FROM resource_connections WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchall()
        existing_pairs = {(r[0], r[1]) for r in existing}

        # Get VPC containment: resource_id → parent VPC id
        vpc_types = ("aws_vpc", "azurerm_virtual_network", "google_compute_network")
        vpc_ids = {
            r["id"] for r in resources
            if (r.get("resource_type") or "").lower() in vpc_types
        }
        # Build: resource_id → set of ancestor VPC ids (via recursive contains)
        children_map: dict[int, list[int]] = {}
        for rc in conn.execute(
            "SELECT source_resource_id, target_resource_id FROM resource_connections WHERE experiment_id = ? AND connection_type = 'contains'",
            (experiment_id,),
        ).fetchall():
            children_map.setdefault(rc[0], []).append(rc[1])

        def get_vpc_for(resource_id: int) -> int | None:
            """Walk up containment tree to find VPC parent."""
            visited = set()
            queue = [resource_id]
            while queue:
                rid = queue.pop()
                if rid in visited:
                    continue
                visited.add(rid)
                for parent_id, children in children_map.items():
                    if rid in children:
                        if parent_id in vpc_ids:
                            return parent_id
                        queue.append(parent_id)
            return None

        resource_map = {r["id"]: r for r in resources}
        resource_vpc = {r["id"]: get_vpc_for(r["id"]) for r in resources}

        def same_vpc(id1: int, id2: int) -> bool:
            v1 = resource_vpc.get(id1)
            v2 = resource_vpc.get(id2)
            return v1 is not None and v1 == v2

        # Categorise resources
        compute = [r for r in resources if _type_matches(r["resource_type"], _COMPUTE_TYPES)]
        databases = [r for r in resources if _type_matches(r["resource_type"], _DB_TYPES)]
        storage = [r for r in resources if _type_matches(r["resource_type"], _STORAGE_TYPES)]
        lbs = [r for r in resources if _type_matches(r["resource_type"], _LB_TYPES)]
        k8s = [r for r in resources if _type_matches(r["resource_type"], _K8S_TYPES)]
        registries = [r for r in resources if _type_matches(r["resource_type"], _CONTAINER_REGISTRY_TYPES)]

        new_connections = []

        def add_conn(src_id: int, tgt_id: int, conn_type: str, label: str):
            if (src_id, tgt_id) not in existing_pairs:
                new_connections.append((src_id, tgt_id, conn_type, label))
                existing_pairs.add((src_id, tgt_id))

        def same_provider(r1: dict, r2: dict) -> bool:
            p1 = (r1.get("provider") or "").lower().strip()
            p2 = (r2.get("provider") or "").lower().strip()
            return bool(p1 and p2 and p1 == p2)

        def plausible_connection(src: dict, tgt: dict) -> bool:
            """True if a connection between src and tgt is plausible (same provider + VPC or same provider)."""
            if not same_provider(src, tgt):
                return False
            # If both in VPCs, must be same VPC
            v1 = resource_vpc.get(src["id"])
            v2 = resource_vpc.get(tgt["id"])
            if v1 and v2:
                return v1 == v2
            return True  # No VPC info; assume possible within same provider

        # Rule 1: LB → Compute in same VPC/provider
        for lb in lbs:
            for comp in compute:
                if plausible_connection(lb, comp):
                    add_conn(lb["id"], comp["id"], "data_access", "routes to")

        # Rule 2: Compute → DB in same VPC/provider
        for comp in compute:
            for db in databases:
                if plausible_connection(comp, db):
                    add_conn(comp["id"], db["id"], "data_access", "reads/writes")

        # Rule 3: Compute → Storage in same provider
        for comp in compute:
            for stor in storage:
                if same_provider(comp, stor):
                    add_conn(comp["id"], stor["id"], "data_access", "reads/writes")

        # Rule 4: K8s → Container registries (same provider)
        for k in k8s:
            for reg in registries:
                if same_provider(k, reg):
                    add_conn(k["id"], reg["id"], "data_access", "pulls images")

        # Persist new connections
        if new_connections:
            cursor = conn.cursor()
            cursor.executemany(
                """INSERT OR IGNORE INTO resource_connections
                   (source_resource_id, target_resource_id, connection_type, experiment_id)
                   VALUES (?, ?, ?, ?)""",
                [(src, tgt, ctype, experiment_id) for src, tgt, ctype, _ in new_connections],
            )
            conn.commit()

        return len(new_connections)

    finally:
        conn.close()


def infer_data_flows(experiment_id: str, db_path=None) -> int:
    """
    Store inferred connections as named data flows in data_flows/data_flow_steps.
    Returns count of data flows added.
    """
    db = db_path or db_helpers.DB_PATH
    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        # Clear existing inferred data flows
        conn.execute(
            "DELETE FROM data_flows WHERE experiment_id = ? AND (notes = 'inferred' OR notes IS NULL)",
            (experiment_id,),
        )

        # Fetch data_access connections
        rows = conn.execute(
            """SELECT rc.id, rc.source_resource_id, rc.target_resource_id,
                      r1.resource_name as src_name, r1.resource_type as src_type,
                      r2.resource_name as tgt_name, r2.resource_type as tgt_type
               FROM resource_connections rc
               JOIN resources r1 ON rc.source_resource_id = r1.id
               JOIN resources r2 ON rc.target_resource_id = r2.id
               WHERE rc.experiment_id = ? AND rc.connection_type = 'data_access'""",
            (experiment_id,),
        ).fetchall()

        cursor = conn.cursor()
        flow_count = 0
        for row in rows:
            r = dict(row)
            flow_name = f"{r['src_name']} → {r['tgt_name']}"
            flow_type = "app_to_db" if _type_matches(r["tgt_type"], _DB_TYPES) else (
                "app_to_storage" if _type_matches(r["tgt_type"], _STORAGE_TYPES) else "data_access"
            )
            cursor.execute(
                "INSERT INTO data_flows (experiment_id, name, flow_type, description, notes) VALUES (?,?,?,?,?)",
                (experiment_id, flow_name, flow_type, f"Inferred: {r['src_type']} accesses {r['tgt_type']}", "inferred"),
            )
            flow_id = cursor.lastrowid
            # Add source step
            cursor.execute(
                "INSERT INTO data_flow_steps (flow_id, step_order, resource_id, component_label) VALUES (?,?,?,?)",
                (flow_id, 1, r["source_resource_id"], r["src_name"]),
            )
            # Add target step
            cursor.execute(
                "INSERT INTO data_flow_steps (flow_id, step_order, resource_id, component_label) VALUES (?,?,?,?)",
                (flow_id, 2, r["target_resource_id"], r["tgt_name"]),
            )
            flow_count += 1

        conn.commit()
        return flow_count

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Infer semantic resource connections")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()

    print(f"[*] Inferring semantic connections for experiment {args.experiment}")
    count = infer_connections(args.experiment, args.db_path)
    print(f"[+] Added {count} semantic connections")

    print("[*] Storing data flows...")
    flow_count = infer_data_flows(args.experiment, args.db_path)
    print(f"[+] Stored {flow_count} data flows")
    print("[✓] Done")


if __name__ == "__main__":
    main()
