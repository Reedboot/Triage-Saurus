#!/usr/bin/env python3
"""
infer_semantic_connections.py

Infer semantic resource connections from cloud topology heuristics.
Adds data_access / calls / depends_on edges to resource_connections table
so they appear in architecture diagrams and data flow analysis.

Rules:
1. LB/AppGateway/APIM → Compute/K8s in the same VPC (routes traffic)
2. Compute → DB in same VPC/provider
3. Compute/K8s → Storage in same provider
4. K8s → Container Registry (same provider)
5. Compute/K8s → Key Vault / Secrets (same provider, depends_on)
6. Compute/K8s → Messaging (ServiceBus, SQS, PubSub) — same provider
7. Compute/K8s → Cache (Redis) — same provider

Fan-out guard: when the cross-product of matching resources in the same provider
exceeds _MAX_FANOUT² and no VPC isolation is available, skip ALL-to-ALL inference
to avoid cluttering test-suite diagrams with N×M noise edges.  Architecture TF
files that use a VPC resource bypass this cap because VPC-scoped matching is precise.

Key design note: _APIM_TYPES / _K8S_TYPES / _LB_TYPES use EXACT match only (no
substring) to avoid child resources (azurerm_api_management_api) being counted
as top-level gateway resources and inflating cross-product counts.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
import db_helpers

# Maximum fanout per side before blocking unprecise (no-VPC) inference.
# Cross-products larger than _MAX_FANOUT² are suppressed without VPC scoping.
_MAX_FANOUT = 5

# Compute resource types
_COMPUTE_TYPES = {
    "aws_instance", "aws_ec2_instance",
    "aws_lambda_function",
    "aws_ecs_service", "aws_ecs_task",
    "azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine",
    "azurerm_app_service", "azurerm_function_app",
    "google_compute_instance", "google_cloud_run_service",
    "google_cloudfunctions_function",
    "oci_core_instance", "oci_functions_function",
    "alicloud_instance", "alicloud_fc_function",
}

_DB_TYPES = {
    "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance",
    "aws_neptune_cluster", "aws_neptune_cluster_instance",
    "aws_dynamodb_table",
    "azurerm_mssql_server", "azurerm_mysql_server", "azurerm_postgresql_server",
    "azurerm_cosmosdb_account",
    "google_sql_database_instance", "google_bigtable_instance",
    "oci_database", "oci_database_autonomous_database",
    "alicloud_db_instance",
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
    "google_compute_global_forwarding_rule", "google_compute_forwarding_rule",
    "oci_load_balancer_load_balancer",
    "alicloud_slb_load_balancer",
}

_APIM_TYPES = {
    "azurerm_api_management",
    "aws_api_gateway_rest_api", "aws_api_gateway_v2_api",
    "google_api_gateway_api",
    "oci_apigateway_gateway",
}

_K8S_TYPES = {
    "aws_eks_cluster",
    "azurerm_kubernetes_cluster",
    "google_container_cluster",
    "oci_containerengine_cluster",
    "alicloud_cs_managed_kubernetes",
}

_CONTAINER_REGISTRY_TYPES = {
    "aws_ecr_repository", "aws_ecrpublic_repository",
    "azurerm_container_registry",
    "google_container_registry", "google_artifact_registry_repository",
}

_KV_TYPES = {
    "azurerm_key_vault",
    "aws_secretsmanager_secret", "aws_kms_key",
    "google_secret_manager_secret",
}

_MESSAGING_TYPES = {
    "azurerm_servicebus_namespace", "azurerm_eventhub_namespace",
    "aws_sqs_queue", "aws_sns_topic",
    "google_pubsub_topic",
    "alicloud_message_service_queue",
}

_CACHE_TYPES = {
    "azurerm_redis_cache",
    "aws_elasticache_cluster", "aws_elasticache_replication_group",
    "alicloud_kvstore_instance",
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
        vpc_types = ("aws_vpc", "azurerm_virtual_network", "google_compute_network",
                     "oci_core_vcn", "alicloud_vpc")
        vpc_ids = {
            r["id"] for r in resources
            if (r.get("resource_type") or "").lower() in vpc_types
        }
        # Build: resource_id → set of ancestor VPC ids (via recursive contains)
        children_map: dict[int, list[int]] = {}
        for rc in conn.execute(
            "SELECT source_resource_id, target_resource_id FROM resource_connections "
            "WHERE experiment_id = ? AND connection_type = 'contains'",
            (experiment_id,),
        ).fetchall():
            # Ignore self-contains
            if rc[0] != rc[1]:
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

        resource_vpc = {r["id"]: get_vpc_for(r["id"]) for r in resources}

        def same_provider(r1: dict, r2: dict) -> bool:
            p1 = (r1.get("provider") or "").lower().strip()
            p2 = (r2.get("provider") or "").lower().strip()
            return bool(p1 and p2 and p1 == p2)

        def plausible_connection(src: dict, tgt: dict) -> bool:
            """True if a connection is plausible.

            Requires same provider.  If both resources have VPC membership, they
            must share the same VPC (precise).  If neither has VPC info, returns
            True but callers should apply the fan-out guard.
            """
            if not same_provider(src, tgt):
                return False
            v1 = resource_vpc.get(src["id"])
            v2 = resource_vpc.get(tgt["id"])
            if v1 and v2:
                return v1 == v2
            # One or both lack VPC info — plausible but imprecise
            return True

        def has_vpc_isolation(r: dict) -> bool:
            return resource_vpc.get(r["id"]) is not None

        def fanout_ok(srcs: list, tgts: list) -> bool:
            """Return False when the cross-product would be too large with no VPC scoping.

            srcs and tgts must already be filtered to a single provider.
            If any resource has VPC membership, we trust the VPC check to filter
            correctly.  Only suppress when both sides are VPC-less and the
            cross-product exceeds _MAX_FANOUT².
            """
            if not srcs or not tgts:
                return False
            if len(srcs) * len(tgts) > _MAX_FANOUT * _MAX_FANOUT:
                # Large cross-product: only allow if VPC can scope it
                any_vpc = any(has_vpc_isolation(r) for r in srcs + tgts)
                return any_vpc
            return True

        # All categories use exact match only (see _categorise docstring).
        # Doing so prevents child resources from being miscounted as top-level
        # service instances and triggering spurious cross-product connections.

        def _categorise(resource_type: str) -> dict:
            """Return a dict of booleans for category membership.

            All categories use exact match only to prevent child resources
            (e.g. google_cloud_run_service_iam_member) from matching parent types.
            """
            rt = (resource_type or "").lower()
            return {
                "compute": rt in _COMPUTE_TYPES,
                "db": rt in _DB_TYPES,
                "storage": rt in _STORAGE_TYPES,
                "lb": rt in _LB_TYPES,
                "apim": rt in _APIM_TYPES,
                "k8s": rt in _K8S_TYPES,
                "registry": rt in _CONTAINER_REGISTRY_TYPES,
                "kv": rt in _KV_TYPES,
                "messaging": rt in _MESSAGING_TYPES,
                "cache": rt in _CACHE_TYPES,
            }

        # Pre-categorise all resources
        for r in resources:
            r["_cats"] = _categorise(r["resource_type"])

        def by_cat(cat: str) -> list:
            return [r for r in resources if r["_cats"].get(cat)]

        compute = by_cat("compute")
        databases = by_cat("db")
        storage = by_cat("storage")
        lbs = by_cat("lb")
        apims = by_cat("apim")
        k8s = by_cat("k8s")
        registries = by_cat("registry")
        kvs = by_cat("kv")
        messaging = by_cat("messaging")
        caches = by_cat("cache")

        new_connections = []

        def add_conn(src_id: int, tgt_id: int, conn_type: str, label: str):
            if src_id != tgt_id and (src_id, tgt_id) not in existing_pairs:
                new_connections.append((src_id, tgt_id, conn_type, label))
                existing_pairs.add((src_id, tgt_id))

        # Collect all providers in this experiment
        providers = {(r.get("provider") or "").lower() for r in resources if r.get("provider")}

        for provider in providers:
            # Pre-filter every category to this provider only.
            # IMPORTANT: inner loops must use these filtered lists so that the
            # fanout guard and the cross-product iteration are always scoped to
            # one provider — prevents cross-provider ghost connections.
            p = provider  # alias

            def pf(lst):
                return [r for r in lst if r.get("provider", "").lower() == p]

            p_lbs = pf(lbs)
            p_apims = pf(apims)
            p_compute = pf(compute)
            p_k8s = pf(k8s)
            p_databases = pf(databases)
            p_storage = pf(storage)
            p_registries = pf(registries)
            p_kvs = pf(kvs)
            p_messaging = pf(messaging)
            p_caches = pf(caches)
            p_ck = p_compute + p_k8s

            # Rule 1: LB → Compute/K8s (traffic routing)
            if fanout_ok(p_lbs, p_ck):
                for lb in p_lbs:
                    for tgt in p_ck:
                        if plausible_connection(lb, tgt):
                            add_conn(lb["id"], tgt["id"], "data_access", "routes to")

            # Rule 1b: LB → APIM → Compute (APIM sits in the routing chain)
            if fanout_ok(p_lbs, p_apims):
                for lb in p_lbs:
                    for apim in p_apims:
                        if plausible_connection(lb, apim):
                            add_conn(lb["id"], apim["id"], "data_access", "routes to")

            if fanout_ok(p_apims, p_ck):
                for apim in p_apims:
                    for tgt in p_ck:
                        if plausible_connection(apim, tgt):
                            add_conn(apim["id"], tgt["id"], "data_access", "routes to")

            # Rule 2: Compute/K8s → DB
            if fanout_ok(p_ck, p_databases):
                for src in p_ck:
                    for db in p_databases:
                        if plausible_connection(src, db):
                            add_conn(src["id"], db["id"], "data_access", "reads/writes")

            # Rule 3: Compute/K8s → Storage
            if fanout_ok(p_ck, p_storage):
                for src in p_ck:
                    for stor in p_storage:
                        if plausible_connection(src, stor):
                            add_conn(src["id"], stor["id"], "data_access", "reads/writes")

            # Rule 4: K8s/Compute → Container Registry
            if fanout_ok(p_ck, p_registries):
                for src in p_ck:
                    for reg in p_registries:
                        if plausible_connection(src, reg):
                            add_conn(src["id"], reg["id"], "data_access", "pulls images")

            # Rule 5: Compute/K8s → Key Vault / Secrets (depends_on)
            if fanout_ok(p_ck, p_kvs):
                for src in p_ck:
                    for kv in p_kvs:
                        if plausible_connection(src, kv):
                            add_conn(src["id"], kv["id"], "depends_on", "reads secrets")

            # Rule 6: Compute/K8s → Messaging
            if fanout_ok(p_ck, p_messaging):
                for src in p_ck:
                    for msg in p_messaging:
                        if plausible_connection(src, msg):
                            add_conn(src["id"], msg["id"], "data_access", "publishes/subscribes")

            # Rule 7: Compute/K8s → Cache
            if fanout_ok(p_ck, p_caches):
                for src in p_ck:
                    for cache in p_caches:
                        if plausible_connection(src, cache):
                            add_conn(src["id"], cache["id"], "data_access", "reads/writes cache")

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
               WHERE rc.experiment_id = ? AND rc.connection_type IN ('data_access', 'depends_on')""",
            (experiment_id,),
        ).fetchall()

        cursor = conn.cursor()
        flow_count = 0
        for row in rows:
            r = dict(row)
            flow_name = f"{r['src_name']} → {r['tgt_name']}"
            flow_type = "app_to_db" if _type_matches(r["tgt_type"], _DB_TYPES) else (
                "app_to_storage" if _type_matches(r["tgt_type"], _STORAGE_TYPES) else (
                "app_to_secrets" if _type_matches(r["tgt_type"], _KV_TYPES) else "data_access"
            ))
            cursor.execute(
                "INSERT INTO data_flows (experiment_id, name, flow_type, description, notes) VALUES (?,?,?,?,?)",
                (experiment_id, flow_name, flow_type, f"Inferred: {r['src_type']} accesses {r['tgt_type']}", "inferred"),
            )
            flow_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO data_flow_steps (flow_id, step_order, resource_id, component_label) VALUES (?,?,?,?)",
                (flow_id, 1, r["source_resource_id"], r["src_name"]),
            )
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

