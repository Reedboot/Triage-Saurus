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


DB_PATH = Path(__file__).resolve().parents[1] / "Output/Learning/triage.db"


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_LINK_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_EVIDENCE_LEVEL_RANK = {"inferred": 1, "extracted": 2, "user_confirmed": 3}




def _upsert_equivalence(
    conn: sqlite3.Connection,
    *,
    resource_node_id: int,
    candidate_resource_type: str,
    candidate_terraform_name: str,
    candidate_source_repo: str,
    equivalence_kind: str,
    confidence: str,
    evidence_level: str,
    provenance: str,
    context_text: str = "",
) -> Optional[int]:
    if not _table_exists(conn, "resource_equivalences"):
        return None

    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        """
        SELECT id, confidence, evidence_level, provenance, context
        FROM resource_equivalences
        WHERE resource_node_id=?
          AND candidate_resource_type=?
          AND candidate_terraform_name=?
          AND candidate_source_repo=?
          AND equivalence_kind=?
        """,
        (
            resource_node_id,
            candidate_resource_type,
            candidate_terraform_name,
            candidate_source_repo,
            equivalence_kind,
        ),
    ).fetchone()

    if existing:
        best_confidence = existing["confidence"] or "low"
        if _LINK_CONFIDENCE_RANK.get(confidence, 0) > _LINK_CONFIDENCE_RANK.get(best_confidence, 0):
            best_confidence = confidence

        best_evidence = existing["evidence_level"] or "inferred"
        if _EVIDENCE_LEVEL_RANK.get(evidence_level, 0) > _EVIDENCE_LEVEL_RANK.get(best_evidence, 0):
            best_evidence = evidence_level

        provenance_chain = [p.strip() for p in (existing["provenance"] or "").split(",") if p.strip()]
        if provenance and provenance not in provenance_chain:
            provenance_chain.append(provenance)
        merged_provenance = ", ".join(provenance_chain) if provenance_chain else provenance
        merged_context = context_text or existing["context"] or ""

        if (
            best_confidence != existing["confidence"]
            or best_evidence != existing["evidence_level"]
            or merged_provenance != (existing["provenance"] or "")
            or merged_context != (existing["context"] or "")
        ):
            conn.execute(
                """
                UPDATE resource_equivalences
                SET confidence=?, evidence_level=?, provenance=?, context=?, updated_at=?
                WHERE id=?
                """,
                (best_confidence, best_evidence, merged_provenance, merged_context, now, existing["id"]),
            )
        return existing["id"]

    cur = conn.execute(
        """
        INSERT INTO resource_equivalences
        (
          resource_node_id,
          candidate_resource_type,
          candidate_terraform_name,
          candidate_source_repo,
          equivalence_kind,
          confidence,
          evidence_level,
          provenance,
          context,
          created_at,
          updated_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            resource_node_id,
            candidate_resource_type,
            candidate_terraform_name,
            candidate_source_repo,
            equivalence_kind,
            confidence,
            evidence_level,
            provenance,
            context_text,
            now,
            now,
        ),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Node upsert
# ---------------------------------------------------------------------------

def _upsert_node(
    conn: sqlite3.Connection,
    resource_type: str,
    terraform_name: str,
    source_repo: str,
    friendly_name: str = "",
    provider: str = "",
    canonical_name: str = "",
) -> int:
    """
    Insert or update a resource node.
    Returns the row id.
    On conflict (same type+name+repo) updates friendly_name if provided.
    On cross-repo match (same type+name, different repo) merges alias.
    """
    now = datetime.utcnow().isoformat()

    # Check for existing node in same repo
    row = conn.execute(
        "SELECT id, aliases, canonical_name FROM resource_nodes "
        "WHERE resource_type=? AND terraform_name=? AND source_repo=?",
        (resource_type, terraform_name, source_repo),
    ).fetchone()

    if row:
        if friendly_name:
            conn.execute(
                "UPDATE resource_nodes SET friendly_name=?, updated_at=? WHERE id=?",
                (friendly_name, now, row["id"]),
            )
        return row["id"]

    # Check for cross-repo match (same type+name, different repo)
    cross = conn.execute(
        "SELECT id, aliases, source_repo, canonical_name FROM resource_nodes "
        "WHERE resource_type=? AND terraform_name=? AND source_repo!=?",
        (resource_type, terraform_name, source_repo),
    ).fetchone()

    if not cross and canonical_name:
        # Also check for inferred placeholder nodes that reference this canonical name
        cross = conn.execute(
            "SELECT id, aliases, source_repo, canonical_name FROM resource_nodes "
            "WHERE resource_type=? AND terraform_name=? AND source_repo!=?",
            (resource_type, f"__inferred__{canonical_name}", source_repo),
        ).fetchone()
        if cross:
            inferred_name = f"__inferred__{canonical_name}"
            # Promote the placeholder to a real extracted node
            conn.execute(
                "UPDATE resource_nodes SET terraform_name=?, canonical_name=?, "
                "confidence='extracted', friendly_name=?, provider=?, updated_at=? WHERE id=?",
                (terraform_name, canonical_name, friendly_name or "", provider, now, cross["id"]),
            )
            _upsert_equivalence(
                conn,
                resource_node_id=cross["id"],
                candidate_resource_type=resource_type,
                candidate_terraform_name=terraform_name,
                candidate_source_repo=source_repo,
                equivalence_kind="cross_repo_alias",
                confidence="high",
                evidence_level="extracted",
                provenance="placeholder_promotion",
                context_text=(
                    f"Promoted inferred placeholder '{inferred_name}' in repo '{cross['source_repo']}' "
                    f"using extracted resource '{resource_type}.{terraform_name}' from repo '{source_repo}'"
                ),
            )
            _upsert_equivalence(
                conn,
                resource_node_id=cross["id"],
                candidate_resource_type=resource_type,
                candidate_terraform_name=inferred_name,
                candidate_source_repo=cross["source_repo"],
                equivalence_kind="placeholder_promotion",
                confidence="high",
                evidence_level="extracted",
                provenance="inferred_placeholder",
                context_text=(
                    f"Placeholder '{resource_type}.{inferred_name}' was promoted after cross-repo match "
                    f"with '{resource_type}.{terraform_name}'"
                ),
            )
            # Auto-resolve any enrichment queue items for this node
            conn.execute(
                "UPDATE enrichment_queue SET status='confirmed', resolved_by='scan', "
                "resolved_at=? WHERE resource_node_id=? AND status='pending_review'",
                (now, cross["id"]),
            )
            return cross["id"]

    if cross:
        # Merge this repo into the aliases list
        aliases = json.loads(cross["aliases"] or "[]")
        if source_repo not in aliases:
            aliases.append(source_repo)
        conn.execute(
            "UPDATE resource_nodes SET aliases=?, updated_at=? WHERE id=?",
            (json.dumps(aliases), now, cross["id"]),
        )
        _upsert_equivalence(
            conn,
            resource_node_id=cross["id"],
            candidate_resource_type=resource_type,
            candidate_terraform_name=terraform_name,
            candidate_source_repo=source_repo,
            equivalence_kind="cross_repo_alias",
            confidence="high" if cross["canonical_name"] else "medium",
            evidence_level="extracted" if cross["canonical_name"] else "inferred",
            provenance="cross_repo_reference",
            context_text=(
                f"Resource {resource_type}.{terraform_name} appears in both "
                f"'{cross['source_repo']}' and '{source_repo}'"
            ),
        )
        # Queue an enrichment assumption if canonical name is still unknown
        if not cross["canonical_name"]:
            _queue_assumption(
                conn,
                resource_node_id=cross["id"],
                gap_type="cross_repo_link",
                context=(
                    f"Resource {resource_type}.{terraform_name} appears in both "
                    f"'{cross['source_repo']}' and '{source_repo}'"
                ),
                assumption_text=(
                    f"These are likely the same {resource_type.replace('azurerm_', '').replace('_', ' ').title()} "
                    f"instance referenced across repos"
                ),
                assumption_basis="cross_repo_reference",
                confidence="medium",
                suggested_value=f"Check both repos to confirm canonical name",
            )
        return cross["id"]

    # Insert new node
    cur = conn.execute(
        """INSERT INTO resource_nodes
           (resource_type, terraform_name, canonical_name, friendly_name,
            provider, source_repo, aliases, confidence, created_at, updated_at)
           VALUES (?,?,?,?,?,?, '[]','extracted',?,?)""",
        (resource_type, terraform_name, canonical_name or None,
         friendly_name or "", provider, source_repo, now, now),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Relationship upsert
# ---------------------------------------------------------------------------

def _upsert_relationship(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    rel_type: str,
    source_repo: str,
    confidence: str,
    notes: str = "",
) -> Optional[int]:
    """Insert relationship, ignore if already exists at same or higher confidence."""
    _CONFIDENCE_RANK = {"extracted": 1, "inferred": 2, "user_confirmed": 3}
    existing = conn.execute(
        "SELECT id, confidence FROM resource_relationships "
        "WHERE source_id=? AND target_id=? AND relationship_type=?",
        (source_id, target_id, rel_type),
    ).fetchone()
    if existing:
        # Upgrade confidence if new observation is more certain
        if _CONFIDENCE_RANK.get(confidence, 0) > _CONFIDENCE_RANK.get(existing["confidence"], 0):
            conn.execute(
                "UPDATE resource_relationships SET confidence=?, notes=? WHERE id=?",
                (confidence, notes or existing["notes"] or "", existing["id"]),
            )
        return existing["id"]

    cur = conn.execute(
        """INSERT INTO resource_relationships
           (source_id, target_id, relationship_type, source_repo, confidence, notes)
           VALUES (?,?,?,?,?,?)""",
        (source_id, target_id, rel_type, source_repo, confidence, notes or ""),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Enrichment queue
# ---------------------------------------------------------------------------

def _queue_assumption(
    conn: sqlite3.Connection,
    *,
    resource_node_id: Optional[int] = None,
    relationship_id: Optional[int] = None,
    gap_type: str,
    context: str,
    assumption_text: str = "",
    assumption_basis: str = "",
    confidence: str = "medium",
    suggested_value: str = "",
) -> None:
    """Log a gap or assumption to the enrichment queue (deduplicated by context)."""
    exists = conn.execute(
        "SELECT id FROM enrichment_queue WHERE context=? AND status='pending_review'",
        (context,),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """INSERT INTO enrichment_queue
           (resource_node_id, relationship_id, gap_type, context,
            assumption_text, assumption_basis, confidence, suggested_value)
           VALUES (?,?,?,?,?,?,?,?)""",
        (resource_node_id, relationship_id, gap_type, context,
         assumption_text, assumption_basis, confidence, suggested_value),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_context(context: RepositoryContext, db_path: Path = DB_PATH) -> None:
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
            link_enrichment(node_id_map[src_key], f"enrichment_{src_key}_{tgt_key}")
            continue

        insert_relationship(
            from_id=node_id_map[src_key],
            to_id=node_id_map[tgt_key],
            relationship_type=rel.relationship_type.value,
            confidence=rel.confidence,
            evidence_level="extracted",
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
