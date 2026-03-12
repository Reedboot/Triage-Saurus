#!/usr/bin/env python3
"""
persist_graph.py
Upserts resource nodes and typed relationships from a RepositoryContext into
the knowledge graph tables (resource_nodes, resource_relationships,
resource_equivalences, enrichment_queue).

Cross-repo identity: if a (resource_type, terraform_name) pair already exists from
a different repo, the new repo name is merged into the aliases JSON array and an
enrichment assumption is queued for user confirmation if canonical_name is still null.
Possible alias/equivalence matches are persisted in resource_equivalences so they are
explicitly queryable beyond queue text.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import RepositoryContext, Relationship, RelationshipType
from cozo_helpers import (
    insert_resource_node,
    insert_enrichment_node,
    insert_relationship,
    insert_equivalence,
    link_enrichment,
)


# Prefer cozo consolidated DB if present; fall back to legacy triage DB
import db_helpers as _db_helpers
DB_PATH = _db_helpers.DB_PATH


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_LINK_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_EVIDENCE_LEVEL_RANK = {"inferred": 1, "extracted": 2, "user_confirmed": 3}





# ---------------------------------------------------------------------------
# Node upsert
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Relationship upsert
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Enrichment queue
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_context(context: RepositoryContext, db_path: Path = DB_PATH, scan_id: str | None = None, actor_type: str | None = None, actor_id: str | None = None) -> None:
    """
    Main entry point. Upsert all resources and relationships from *context*
    into the Cozo knowledge graph, and queue enrichment items for gaps.
    """
    import resource_type_db as _rtdb

    repo_name = context.repository_name

    # 1. Upsert all resource nodes
    node_id_map: dict[str, str] = {}   # "resource_type.terraform_name" → node_id
    for resource in context.resources:
        friendly = _rtdb.get_friendly_name(None, resource.resource_type)
        canonical = resource.properties.get("canonical_name", "")
        aliases = resource.properties.get("aliases", "[]")
        insert_resource_node(
            resource_type=resource.resource_type,
            terraform_name=resource.name,
            source_repo=repo_name,
            aliases=aliases,
            canonical_name=canonical,
        )
        node_id_map[f"{resource.resource_type}.{resource.name}"] = f"{resource.resource_type}.{resource.name}"

    # 2. Upsert typed relationships
    for rel in context.relationships:
        src_key = f"{rel.source_type}.{rel.source_name}"
        tgt_key = f"{rel.target_type}.{rel.target_name}"
        is_gap = rel.target_type == "unknown" or rel.confidence == "inferred"

        # Ensure source node exists
        if src_key not in node_id_map:
            insert_resource_node(
                resource_type=rel.source_type,
                terraform_name=rel.source_name,
                source_repo=repo_name,
            )
            node_id_map[src_key] = src_key

        # Ensure target node exists
        if tgt_key not in node_id_map:
            insert_resource_node(
                resource_type=rel.target_type,
                terraform_name=rel.target_name,
                source_repo=repo_name,
            )
            node_id_map[tgt_key] = tgt_key

        if is_gap:
            # Queue as enrichment gap — target is a variable ref or connection string inference
            gap_type = "ambiguous_ref" if "var." in rel.target_name else "missing_target"
            assumption_text = (
                f"{rel.source_type.replace('azurerm_','').replace('aws_','').replace('_',' ').title()} "
                f"'{rel.source_name}' may {rel.relationship_type.value.replace('_',' ')} "
                f"an external resource referenced as '{rel.target_name}'"
            )
            if rel.notes:
                assumption_text = rel.notes
            insert_enrichment_node(
                context=rel.notes or f"{rel.source_type}.{rel.source_name} → {rel.target_name}",
                provenance="variable_reference" if "var." in rel.target_name else "connection_string",
                evidence_level="medium" if "var." not in rel.target_name else "low",
            )
            link_enrichment(node_id_map[src_key], f"enrichment_{src_key}_{tgt_key}", actor_type=actor_type, actor_id=actor_id, scan_id=scan_id)
            continue

        insert_relationship(
            from_id=node_id_map[src_key],
            to_id=node_id_map[tgt_key],
            relationship_type=rel.relationship_type.value,
            confidence=rel.confidence,
            evidence_level="extracted",
            actor_type=actor_type,
            actor_id=actor_id,
            scan_id=scan_id,
        )

    # TODO: Implement equivalence and enrichment linking as needed


def query_graph_for_repo(repo_name: str, db_path: Path = DB_PATH) -> dict:
    """
    Returns nodes and relationships for a given repo from the knowledge graph.
    Used by report_generation.py to build the inventory and diagram.
    """
    conn = _get_conn(db_path)
    try:
        nodes = conn.execute(
            "SELECT * FROM resource_nodes WHERE source_repo=? OR aliases LIKE ?",
            (repo_name, f'%"{repo_name}"%'),
        ).fetchall()

        node_ids = [n["id"] for n in nodes]
        if not node_ids:
            return {"nodes": [], "relationships": [], "assumptions": [], "equivalences": []}

        placeholders = ",".join("?" * len(node_ids))
        relationships = conn.execute(
            f"SELECT * FROM resource_relationships "
            f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            node_ids + node_ids,
        ).fetchall()

        assumptions = conn.execute(
            f"SELECT * FROM enrichment_queue "
            f"WHERE resource_node_id IN ({placeholders}) AND status='pending_review' "
            f"ORDER BY confidence DESC, created_at ASC",
            node_ids,
        ).fetchall()

        equivalences: list[sqlite3.Row] = []
        if _table_exists(conn, "resource_equivalences"):
            equivalences = conn.execute(
                f"SELECT * FROM resource_equivalences "
                f"WHERE resource_node_id IN ({placeholders}) OR candidate_source_repo=? "
                f"ORDER BY CASE confidence "
                f"    WHEN 'high' THEN 3 "
                f"    WHEN 'medium' THEN 2 "
                f"    WHEN 'low' THEN 1 "
                f"    ELSE 0 END DESC, created_at ASC",
                node_ids + [repo_name],
            ).fetchall()

        return {
            "nodes": [dict(n) for n in nodes],
            "relationships": [dict(r) for r in relationships],
            "assumptions": [dict(a) for a in assumptions],
            "equivalences": [dict(e) for e in equivalences],
        }
    finally:
        conn.close()
