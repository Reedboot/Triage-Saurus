#!/usr/bin/env python3
"""Store opengrep scan output inside a Cozo DB for later enrichment."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import sqlite3
try:
    from pycozo import Client
    _HAS_PYCOZO = True
except Exception:
    Client = None
    _HAS_PYCOZO = False

DEFAULT_COZO_DB = Path("Output/Data/cozo.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Utils"))
from shared_utils import _severity_score


def _canonical_provider(provider: str) -> str:
    value = (provider or "").strip().lower()
    if value == "oracle":
        return "oci"
    return value or "unknown"


def _detect_provider(check_id: str, metadata: Mapping[str, Any] | None) -> str:
    metadata = metadata or {}
    # Prefer explicit asset_provider metadata if rule provides it
    asset_provider = metadata.get("asset_provider") or metadata.get("provider")
    if isinstance(asset_provider, str) and asset_provider.strip():
        return _canonical_provider(asset_provider)

    tech = metadata.get("technology") or metadata.get("technologies")
    if isinstance(tech, str):
        tech_values = [tech.lower()]
    elif isinstance(tech, Iterable):
        tech_values = [str(item).lower() for item in tech if item is not None]
    else:
        tech_values = []

    for token in tech_values:
        if "azure" in token or "azurerm" in token:
            return "azure"
        if "aws" in token or "amazon" in token:
            return "aws"
        if "gcp" in token or "google" in token:
            return "gcp"
        if "alicloud" in token or "alibaba" in token:
            return "alicloud"
        if "oci" in token or "oracle" in token:
            return "oci"
        if "tencentcloud" in token or "tencent" in token:
            return "tencentcloud"
        if "huaweicloud" in token or "huawei" in token:
            return "huaweicloud"
        if "terraform" in token:
            return "terraform"

    lower_id = check_id.lower()
    if "azure" in lower_id or "azurerm" in lower_id:
        return "azure"
    if "aws" in lower_id:
        return "aws"
    if "gcp" in lower_id or "google" in lower_id:
        return "gcp"
    if "alicloud" in lower_id or "alibaba" in lower_id:
        return "alicloud"
    if "oci" in lower_id or "oracle" in lower_id:
        return "oci"
    if "tencentcloud" in lower_id or "tencent" in lower_id:
        return "tencentcloud"
    if "huaweicloud" in lower_id or "huawei" in lower_id:
        return "huaweicloud"
    if "terraform" in lower_id:
        return "terraform"
    return "unknown"


def _make_finding_id(repo_name: str, rule_id: str, path: str, start_line: int) -> str:
    payload = f"{repo_name}|{rule_id}|{path}|{start_line}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_context_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _extract_metavar(entry: Mapping[str, Any]) -> str:
    for attr in ("abstract_content", "value", "content"):
        val = entry.get(attr)
        if val:
            return _normalize_context_value(val).strip('"')
    return _normalize_context_value(entry)


def _context_entries(finding_id: str, extra: Mapping[str, Any]) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    metadata = extra.get("metadata") or {}
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            context.append(
                {
                    "finding_id": finding_id,
                    "context_key": f"metadata.{key}",
                    "context_value": _normalize_context_value(value),
                    "context_type": "metadata",
                }
            )

    metavars = extra.get("metavars") or {}
    if isinstance(metavars, Mapping):
        for key, datum in metavars.items():
            if isinstance(datum, Mapping):
                value = _extract_metavar(datum)
            else:
                value = _normalize_context_value(datum)
            context.append(
                {
                    "finding_id": finding_id,
                    "context_key": key,
                    "context_value": value,
                    "context_type": "metavar",
                }
            )

    return context


def _ensure_relations(db: Client) -> None:
    existing = {row[0] for row in db.relations()["rows"]}
    schema = {
        "repo_scans": ["scan_id", "repo_name", "repo_path", "scan_time"],
        "findings": [
            "finding_id",
            "scan_id",
            "repo_name",
            "repo_path",
            "rule_id",
            "check_id",
            "category",
            "severity",
            "severity_score",
            "message",
            "code_snippet",
            "metadata_json",
            "provider",
            "source_file",
            "start_line",
            "end_line",
            "created_at",
        ],
        "finding_context": [
            "finding_id",
            "context_key",
            "context_value",
            "context_type",
        ],
    }

    for name, columns in schema.items():
        if name not in existing:
            db.create(name, *columns)


# Maps (rule_id_substring, context_key, child_resource_type, parent_resource_type)
PARENT_REF_MAP = [
    # Azure
    ("storage-container", "storage_account_name", "azurerm_storage_container", "azurerm_storage_account"),
    ("storage-container", "$ACCOUNT", "azurerm_storage_container", "azurerm_storage_account"),
    ("storage-container", "$ACCOUNT_ID", "azurerm_storage_container", "azurerm_storage_account"),
    ("sql-database", "server_id", "azurerm_mssql_database", "azurerm_mssql_server"),
    ("sql-database", "$SERVER", "azurerm_mssql_database", "azurerm_mssql_server"),
    ("keyvault-secret", "key_vault_id", "azurerm_key_vault_secret", "azurerm_key_vault"),
    ("keyvault-secret", "$KV", "azurerm_key_vault_secret", "azurerm_key_vault"),
    ("keyvault-key", "key_vault_id", "azurerm_key_vault_key", "azurerm_key_vault"),
    ("keyvault-key", "$KV", "azurerm_key_vault_key", "azurerm_key_vault"),
    ("keyvault-certificate", "key_vault_id", "azurerm_key_vault_certificate", "azurerm_key_vault"),
    ("keyvault-certificate", "$KV", "azurerm_key_vault_certificate", "azurerm_key_vault"),
    ("vm-extension", "virtual_machine_id", "azurerm_virtual_machine_extension", "azurerm_linux_virtual_machine"),
    ("vm-extension", "$VM_ID", "azurerm_virtual_machine_extension", "azurerm_linux_virtual_machine"),
    ("servicebus-queue", "namespace_id", "azurerm_servicebus_queue", "azurerm_servicebus_namespace"),
    ("servicebus-queue", "$NAMESPACE_ID", "azurerm_servicebus_queue", "azurerm_servicebus_namespace"),
    ("servicebus-topic", "namespace_id", "azurerm_servicebus_topic", "azurerm_servicebus_namespace"),
    ("servicebus-topic", "$NAMESPACE_ID", "azurerm_servicebus_topic", "azurerm_servicebus_namespace"),
    ("servicebus-subscription", "topic_id", "azurerm_servicebus_subscription", "azurerm_servicebus_topic"),
    ("servicebus-subscription", "$TOPIC_ID", "azurerm_servicebus_subscription", "azurerm_servicebus_topic"),
    ("eventhub-consumer-group", "eventhub_name", "azurerm_eventhub_consumer_group", "azurerm_eventhub"),
    ("eventhub-consumer-group", "$EH_NAME", "azurerm_eventhub_consumer_group", "azurerm_eventhub"),
    ("eventhub", "namespace_name", "azurerm_eventhub", "azurerm_eventhub_namespace"),
    ("eventhub", "$NAMESPACE_NAME", "azurerm_eventhub", "azurerm_eventhub_namespace"),
    ("aks-node-pool", "kubernetes_cluster_id", "azurerm_kubernetes_cluster_node_pool", "azurerm_kubernetes_cluster"),
    ("aks-node-pool", "$CLUSTER_ID", "azurerm_kubernetes_cluster_node_pool", "azurerm_kubernetes_cluster"),
    ("storage-blob", "storage_account_name", "azurerm_storage_blob", "azurerm_storage_account"),
    ("storage-blob", "$ACCOUNT_NAME", "azurerm_storage_blob", "azurerm_storage_account"),
    ("storage-queue", "storage_account_name", "azurerm_storage_queue", "azurerm_storage_account"),
    ("storage-queue", "$ACCOUNT_NAME", "azurerm_storage_queue", "azurerm_storage_account"),
    ("storage-share", "storage_account_name", "azurerm_storage_share", "azurerm_storage_account"),
    ("storage-share", "$ACCOUNT_NAME", "azurerm_storage_share", "azurerm_storage_account"),
    ("cosmosdb-sql-container", "account_name", "azurerm_cosmosdb_sql_container", "azurerm_cosmosdb_account"),
    ("cosmosdb-sql-database", "account_name", "azurerm_cosmosdb_sql_database", "azurerm_cosmosdb_account"),
    ("mysql-database", "server_name", "azurerm_mysql_database", "azurerm_mysql_server"),
    ("mysql-database", "$SERVER_NAME", "azurerm_mysql_database", "azurerm_mysql_server"),
    ("postgresql-database", "server_name", "azurerm_postgresql_database", "azurerm_postgresql_server"),
    ("postgresql-database", "$SERVER_NAME", "azurerm_postgresql_database", "azurerm_postgresql_server"),
    ("mssql-firewall-rule", "server_id", "azurerm_mssql_firewall_rule", "azurerm_mssql_server"),
    ("mssql-firewall-rule", "$SERVER_ID", "azurerm_mssql_firewall_rule", "azurerm_mssql_server"),
    ("nsg-rule", "network_security_group_name", "azurerm_network_security_rule", "azurerm_network_security_group"),
    ("nsg-rule", "$NSG_NAME", "azurerm_network_security_rule", "azurerm_network_security_group"),
    ("api-management-api", "api_management_name", "azurerm_api_management_api", "azurerm_api_management"),
    ("api-management-api", "$APIM_NAME", "azurerm_api_management_api", "azurerm_api_management"),
    # Alicloud
    ("ack-node-pool", "cluster_id", "alicloud_cs_kubernetes_node_pool", "alicloud_cs_managed_kubernetes"),
    ("ack-node-pool", "$CLUSTER_ID", "alicloud_cs_kubernetes_node_pool", "alicloud_cs_managed_kubernetes"),
    ("kms-secret", "encryption_key_id", "alicloud_kms_secret", "alicloud_kms_key"),
    ("kms-secret", "$KEY_ID", "alicloud_kms_secret", "alicloud_kms_key"),
    ("vswitch", "vpc_id", "alicloud_vswitch", "alicloud_vpc"),
    ("vswitch", "$VPC_ID", "alicloud_vswitch", "alicloud_vpc"),
    ("security-group-rule", "security_group_id", "alicloud_security_group_rule", "alicloud_security_group"),
    ("security-group-rule", "$SG_ID", "alicloud_security_group_rule", "alicloud_security_group"),
    ("log-store", "project", "alicloud_log_store", "alicloud_log_project"),
    ("log-store", "$PROJECT", "alicloud_log_store", "alicloud_log_project"),
    ("fc-function", "service_name", "alicloud_fc_function", "alicloud_fc_service"),
    ("fc-function", "$SERVICE_NAME", "alicloud_fc_function", "alicloud_fc_service"),
    # OCI
    ("oke-node-pool", "cluster_id", "oci_containerengine_node_pool", "oci_containerengine_cluster"),
    ("oke-node-pool", "$CLUSTER_ID", "oci_containerengine_node_pool", "oci_containerengine_cluster"),
    ("kms-key", "management_endpoint", "oci_kms_key", "oci_kms_vault"),
    ("vault-secret", "vault_id", "oci_vault_secret", "oci_kms_vault"),
    ("vault-secret", "$VAULT_ID", "oci_vault_secret", "oci_kms_vault"),
    ("oci-subnet", "vcn_id", "oci_core_subnet", "oci_core_vcn"),
    ("oci-subnet", "$VCN_ID", "oci_core_subnet", "oci_core_vcn"),
    ("functions", "application_id", "oci_functions_function", "oci_functions_application"),
    ("functions", "$APP_ID", "oci_functions_function", "oci_functions_application"),
    ("apigateway", "gateway_id", "oci_apigateway_deployment", "oci_apigateway_gateway"),
    ("apigateway", "$GATEWAY_ID", "oci_apigateway_deployment", "oci_apigateway_gateway"),
    ("logging", "log_group_id", "oci_logging_log", "oci_logging_log_group"),
    ("logging", "$LOG_GROUP_ID", "oci_logging_log", "oci_logging_log_group"),
]


def _normalize_parent_ref(value: str) -> str:
    """Extract the resource name from a Terraform reference.

    Examples:
      azurerm_storage_account.main.id  -> main
      data.azurerm_storage_account.sa.id -> sa
      myaccount                         -> myaccount
      var.servicebus_namespace_id       -> None (cannot resolve; caller should fuzzy-match)
    """
    if not value:
        return value
    if value.startswith("data."):
        parts = value.split(".")
        return parts[2] if len(parts) >= 3 else value
    # var./local. references cannot be resolved to a literal resource name here
    if value.startswith("var.") or value.startswith("local."):
        return ""
    parts = value.split(".")
    if len(parts) >= 2:
        return parts[1]
    return value


def _fuzzy_parent_lookup(sq: "sqlite3.Connection", experiment_id: str,
                         parent_type: str, context_value: str) -> "int | None":
    """Try a token-based lookup for parent resources when exact name matching failed.

    Used when context_value is a var./local. reference whose literal value is unknown.
    Extracts tokens from the reference string and searches for a parent resource whose
    name shares at least one significant (>3-char) token.

    Returns the parent resource id, or None if no unambiguous match is found.
    """
    if not context_value:
        return None
    # Extract word tokens from the reference (e.g. "var.myapp_servicebus_ns_id" → ["myapp", "servicebus", "ns"])
    tokens = [t for t in re.split(r'[^a-zA-Z0-9]+', context_value) if len(t) > 3
              and t.lower() not in ('true', 'false', 'null', 'none', 'data', 'local', 'module')]
    if not tokens:
        return None
    candidates = sq.execute(
        "SELECT id, resource_name FROM resources WHERE experiment_id = ? AND resource_type = ?",
        (experiment_id, parent_type),
    ).fetchall()
    if not candidates:
        return None
    matches = []
    for cid, cname in candidates:
        name_lower = (cname or "").lower()
        if any(tok.lower() in name_lower or name_lower in tok.lower() for tok in tokens):
            matches.append(cid)
    return matches[0] if len(matches) == 1 else None


def _create_parent_child_relationships(db: "Client", experiment_id: str) -> None:
    """Post-process finding_context rows to wire parent_resource_id and resource_relationships.

    Queries pycozo for finding_context entries associated with this scan, then uses a
    separate sqlite3 connection to update the resources and resource_relationships tables
    in the same database file.

    db: open pycozo Client (must still be open when this function is called)
    experiment_id: the scan_id used when storing findings
    """
    # findings columns (positional): finding_id(0), scan_id(1), repo_name(2), repo_path(3),
    # rule_id(4), check_id(5), category(6), severity(7), severity_score(8), message(9),
    # code_snippet(10), metadata_json(11), provider(12), source_file(13), start_line(14),
    # end_line(15), created_at(16)
    # finding_context columns: finding_id(0), context_key(1), context_value(2), context_type(3)
    try:
        result = db.run(
            """
            ?[finding_id, rule_id, source_file, start_line, context_key, context_value] :=
              *findings[finding_id, $scan_id, _, _, rule_id, _, _, _, _, _, _, _, _, source_file, start_line, _, _],
              *finding_context[finding_id, context_key, context_value, _]
            """,
            {"scan_id": experiment_id},
        )
    except Exception as exc:
        print(f"WARNING: parent-child post-process: pycozo query failed: {exc}", file=sys.stderr)
        return

    file_line_ctx: dict = defaultdict(list)
    for row in result.get("rows", []):
        _fid, rule_id, source_file, start_line, context_key, context_value = row
        key = (source_file or "", int(start_line) if start_line else 0)
        file_line_ctx[key].append((rule_id, context_key, context_value))

    if not file_line_ctx:
        return

    try:
        sq = sqlite3.connect(str(DEFAULT_COZO_DB), timeout=10)
        sq.execute("PRAGMA journal_mode=WAL")
    except Exception as exc:
        print(f"WARNING: parent-child post-process: sqlite connect failed: {exc}", file=sys.stderr)
        return

    try:
        res_rows = sq.execute(
            "SELECT id, resource_name, resource_type, source_file, source_line_start "
            "FROM resources WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchall()

        linked = 0
        rel_inserted = 0
        for res_id, _res_name, res_type, src_file, src_line in res_rows:
            key = (src_file or "", int(src_line) if src_line else 0)
            for rule_id, context_key, context_value in file_line_ctx.get(key, []):
                for rule_substr, map_key, child_type, parent_type in PARENT_REF_MAP:
                    if rule_substr not in rule_id.lower():
                        continue
                    if map_key != context_key:
                        continue
                    if res_type != child_type:
                        continue

                    norm = _normalize_parent_ref(context_value or "")
                    parent_row = None
                    if norm:
                        parent_row = sq.execute(
                            "SELECT id FROM resources "
                            "WHERE experiment_id = ? AND resource_type = ? AND resource_name = ?",
                            (experiment_id, parent_type, norm),
                        ).fetchone()
                    if not parent_row and not norm:
                        # context_value was a var./local. reference — try token-based fuzzy match
                        fuzzy_id = _fuzzy_parent_lookup(sq, experiment_id, parent_type, context_value or "")
                        if fuzzy_id:
                            parent_row = (fuzzy_id,)
                            print(f"  Fuzzy parent match: {context_value!r} → parent id {fuzzy_id} ({parent_type})")
                        else:
                            print(
                                f"WARNING: cannot resolve parent ref {context_value!r} to {parent_type} "
                                f"(resource {res_id})",
                                file=sys.stderr,
                            )
                    parent_id = parent_row[0] if parent_row else None

                    if parent_id is not None:
                        sq.execute(
                            "UPDATE resources SET parent_resource_id = ? WHERE id = ?",
                            (parent_id, res_id),
                        )
                        linked += 1

                    # Always record the relationship; use 0 as sentinel target when
                    # parent is not yet in the resources table (cross-repo or deferred).
                    target_id = parent_id if parent_id is not None else 0
                    sq.execute(
                        "INSERT OR IGNORE INTO resource_relationships "
                        "(source_id, target_id, relationship_type, source_repo, confidence, notes) "
                        "VALUES (?, ?, 'child_of', ?, 'extracted', ?)",
                        (
                            res_id,
                            target_id,
                            experiment_id,
                            f"context_key={context_key}; ref={context_value}; parent_type={parent_type}",
                        ),
                    )
                    rel_inserted += 1
                    break  # First matching PARENT_REF_MAP entry wins per context row

        sq.commit()
        if linked or rel_inserted:
            print(f"  Parent-child: {linked} resources linked, {rel_inserted} relationships created.")
    except Exception as exc:
        try:
            sq.rollback()
        except Exception:
            pass
        print(f"WARNING: parent-child post-process: update failed: {exc}", file=sys.stderr)
    finally:
        sq.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import opengrep JSON scans into Cozo.")
    parser.add_argument("scan_json", type=Path, help="Path to opengrep JSON output.")
    parser.add_argument("--repo", default="unknown", help="Repository name under scan.")
    parser.add_argument(
        "--repo-path",
        type=Path,
        help="Repository path (optional).",
    )
    parser.add_argument(
        "--scan-id",
        help="Optional scan identifier (defaults to a random UUID).",
    )
    args = parser.parse_args()

    if not args.scan_json.exists():
        print(f"ERROR: scan file not found: {args.scan_json}", file=sys.stderr)
        sys.exit(1)

    DEFAULT_COZO_DB.parent.mkdir(parents=True, exist_ok=True)
    with args.scan_json.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    scan_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    scan_id = args.scan_id or hashlib.sha1(scan_time.encode("utf-8")).hexdigest()

    if not (_HAS_PYCOZO and Client is not None):
        print("ERROR: pycozo with cozo_embedded is required to persist findings. Install a compatible pycozo wheel in CI.", file=sys.stderr)
        sys.exit(2)

    # Preferred path: use pycozo Client to create relations and store.
    db = Client(engine="sqlite", path=str(DEFAULT_COZO_DB), dataframe=False)
    try:
        _ensure_relations(db)

        db.insert(
            "repo_scans",
            {
                "scan_id": scan_id,
                "repo_name": args.repo,
                "repo_path": str(args.repo_path) if args.repo_path else "",
                "scan_time": scan_time,
            },
        )

        stored = 0
        contexts = 0
        for result in data.get("results", []):
            extra = result.get("extra", {}) or {}
            metadata = extra.get("metadata") or {}
            rule_id = result.get("check_id", "")
            path = result.get("path", "")
            start_line = result.get("start", {}).get("line", 0) or 0
            end_line = result.get("end", {}).get("line", start_line) or start_line
            severity = extra.get("severity", "WARNING")
            finding_id = _make_finding_id(args.repo, rule_id, path, start_line)
            provider = _detect_provider(rule_id, metadata)

            message = (extra.get("message") or "").strip()
            code_snippet = extra.get("lines") or ""
            category = metadata.get("category") if isinstance(metadata, Mapping) else None
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)

            db.insert(
                "findings",
                {
                    "finding_id": finding_id,
                    "scan_id": scan_id,
                    "repo_name": args.repo,
                    "repo_path": str(args.repo_path) if args.repo_path else "",
                    "rule_id": rule_id,
                    "check_id": rule_id,
                    "category": category or "",
                    "severity": severity,
                    "severity_score": _severity_score(severity),
                    "message": message,
                    "code_snippet": code_snippet,
                    "metadata_json": metadata_json,
                    "provider": provider,
                    "source_file": path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "created_at": scan_time,
                },
            )

            entries = _context_entries(finding_id, extra)
            for entry in entries:
                db.put("finding_context", entry)

            stored += 1
            contexts += len(entries)

        _create_parent_child_relationships(db, scan_id)

    finally:
        try:
            db.close()
        except Exception:
            pass

    print(f"Stored {stored} findings with {contexts} context rows into {DEFAULT_COZO_DB}")


if __name__ == "__main__":
    main()
