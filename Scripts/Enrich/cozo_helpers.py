from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Tuple
import sqlite3

COZO_DB_PATH = Path(__file__).resolve().parents[2] / "Output/Data/cozo.db"


def _execute_sql(sql: str, params: tuple = ()) -> None:
    """Execute SQL directly against the Cozo sqlite DB for simple graph ops."""
    conn = sqlite3.connect(str(COZO_DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def insert_resource_node(resource_type: str, terraform_name: str, source_repo: str, aliases: str = "[]", canonical_name: str = "") -> None:
    """Insert or update a resource node in the lightweight nodes table."""
    sql = (
        "INSERT INTO nodes (type, resource_type, terraform_name, source_repo, aliases, canonical_name) VALUES (?,?,?,?,?,?)"
    )
    try:
        _execute_sql(sql, ("resource", resource_type, terraform_name, source_repo, aliases, canonical_name))
    except Exception as e:
        raise RuntimeError(f"Cozo insert_resource_node failed: {e}; SQL: {sql}; params: {(resource_type, terraform_name, source_repo, aliases, canonical_name)!r}")

def insert_enrichment_node(context: str, provenance: str, evidence_level: str) -> None:
    """Insert an enrichment node in the lightweight nodes table."""
    sql = (
        "INSERT INTO nodes (type, context, provenance, evidence_level) VALUES (?,?,?,?)"
    )
    try:
        _execute_sql(sql, ("enrichment", context, provenance, evidence_level))
    except Exception as e:
        raise RuntimeError(f"Cozo insert_enrichment_node failed: {e}; SQL: {sql}; params: {(context, provenance, evidence_level)!r}")

def insert_relationship(from_id: str, to_id: str, relationship_type: str, confidence: str, evidence_level: str) -> None:
    """Insert a relationship edge between nodes in the lightweight edges table."""
    sql = (
        "INSERT INTO edges (from_id, to_id, type, confidence, evidence_level) VALUES (?,?,?,?,?)"
    )
    try:
        _execute_sql(sql, (from_id, to_id, relationship_type, confidence, evidence_level))
    except Exception as e:
        raise RuntimeError(f"Cozo insert_relationship failed: {e}; SQL: {sql}; params: {(from_id, to_id, relationship_type, confidence, evidence_level)!r}")

def insert_equivalence(resource_id: str, candidate_id: str, equivalence_kind: str) -> None:
    """Insert an equivalence edge between resource nodes in Cozo."""
    sql = (
        "INSERT INTO edges (from_id, to_id, type, equivalence_kind) VALUES (?,?,?,?)"
    )
    try:
        _execute_sql(sql, (resource_id, candidate_id, 'equivalence', equivalence_kind))
    except Exception as e:
        raise RuntimeError(f"Cozo insert_equivalence failed: {e}; SQL: {sql}; params: {(resource_id, candidate_id, equivalence_kind)!r}")

def link_enrichment(resource_id: str, enrichment_id: str) -> None:
    """Link a resource node to an enrichment node in Cozo (as an edge)."""
    sql = (
        "INSERT INTO edges (from_id, to_id, type) VALUES (?,?,?)"
    )
    try:
        _execute_sql(sql, (resource_id, enrichment_id, 'enriched_by'))
    except Exception as e:
        raise RuntimeError(f"Cozo link_enrichment failed: {e}; SQL: {sql}; params: {(resource_id, enrichment_id)!r}")

def insert_task_dependency(task_id: str, depends_on_id: str) -> None:
    """Insert a dependency edge between task nodes in Cozo."""
    sql = (
        "INSERT INTO edges (from_id, to_id, type) VALUES (?,?,?)"
    )
    try:
        _execute_sql(sql, (task_id, depends_on_id, 'depends_on'))
    except Exception as e:
        raise RuntimeError(f"Cozo insert_task_dependency failed: {e}; SQL: {sql}; params: {(task_id, depends_on_id)!r}")
_SEVERITY_PROFILE: Dict[str, Tuple[str, int]] = {
    "error": ("High", 9),
    "warning": ("Medium", 6),
    "info": ("Low", 4),
}

DEFAULT_SEVERITY_LABEL = "Medium"
DEFAULT_SEVERITY_SCORE = 6


def _ensure_db_exists() -> None:
    if not COZO_DB_PATH.exists():
        raise FileNotFoundError(f"Cozo DB not found at {COZO_DB_PATH}")


def _open_client(dataframe: bool = False) -> Client:
    _ensure_db_exists()
    return Client(engine="sqlite", path=str(COZO_DB_PATH), dataframe=dataframe)


def export_relations(*relation_names: str) -> Dict[str, Dict[str, Any]]:
    if not relation_names:
        raise ValueError("At least one relation must be requested")
    with closing(_open_client(dataframe=False)) as client:
        return client.export_relations(list(relation_names))


def _rows_from_relation(relation: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers = relation.get("headers") or []
    rows = relation.get("rows") or []
    return [{headers[idx]: row[idx] for idx in range(len(headers))} for row in rows]


def fetch_findings_with_context(
    scan_id: str | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    exported = export_relations("findings", "finding_context")
    finding_rows = _rows_from_relation(exported.get("findings", {}))
    context_rows = _rows_from_relation(exported.get("finding_context", {}))
    context_map: Dict[str, List[Dict[str, Any]]] = {}
    for ctx in context_rows:
        fid = ctx.get("finding_id")
        if not fid:
            continue
        context_map.setdefault(fid, []).append(ctx)
    if scan_id:
        finding_rows = [row for row in finding_rows if row.get("scan_id") == scan_id]
    return finding_rows, context_map


def get_finding_with_context(finding_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    findings, context_map = fetch_findings_with_context()
    for row in findings:
        if row.get("finding_id") == finding_id:
            return row, context_map.get(finding_id, [])
    raise KeyError(f"Finding not found in Cozo DB: {finding_id}")


def severity_summary(raw_severity: str | None) -> Tuple[str, int]:
    key = (raw_severity or "").strip().lower()
    return _SEVERITY_PROFILE.get(key, (DEFAULT_SEVERITY_LABEL, DEFAULT_SEVERITY_SCORE))


def clamp_score(score: int) -> int:
    return max(1, min(10, score))
