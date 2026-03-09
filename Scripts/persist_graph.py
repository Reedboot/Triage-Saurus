#!/usr/bin/env python3
"""
persist_graph.py
Upserts resource nodes and typed relationships from a RepositoryContext into
the knowledge graph tables (resource_nodes, resource_relationships,
resource_equivalences, enrichment_queue) using the CozoDB backend.

Cross-repo identity: if a (resource_type, terraform_name) pair already exists from
a different repo, the new repo name is merged into the aliases JSON array and an
enrichment assumption is queued for user confirmation if canonical_name is still null.
Possible alias/equivalence matches are persisted in resource_equivalences so they are
explicitly queryable beyond queue text.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import RepositoryContext, Relationship, RelationshipType


DB_PATH = Path(__file__).resolve().parents[1] / "Output/Learning/triage_cozo.db"

_LINK_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_EVIDENCE_LEVEL_RANK = {"inferred": 1, "extracted": 2, "user_confirmed": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_client(db_path: Path = DB_PATH):
    """Return a CozoDB client (opens/creates the database)."""
    try:
        from pycozo.client import Client
    except ImportError as exc:
        raise RuntimeError("pycozo is required: pip install pycozo cozo-embedded") from exc
    import sys
    import os  # noqa: F401 – available for submodule init_schema if needed
    sys.path.insert(0, str(Path(__file__).parent))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    client = Client("sqlite", str(db_path), dataframe=False)
    from init_database import init_schema
    init_schema(client)
    return client


def _next_id(client, table_name: str) -> int:
    result = client.run("?[v] := *counters{tbl: $t, val: v}", {"t": table_name})
    current = result["rows"][0][0] if result["rows"] else 0
    new_id = current + 1
    client.run("?[tbl, val] <- [[$t, $v]] :put counters {tbl, val}", {"t": table_name, "v": new_id})
    return new_id


# ---------------------------------------------------------------------------
# Equivalence upsert
# ---------------------------------------------------------------------------

def _upsert_equivalence(
    client,
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
    now = _now()
    existing = client.run(
        """
        ?[equiv_id, confidence, evidence_level, provenance, context] :=
            *resource_equivalences{
                equiv_id: equiv_id,
                resource_node_id: $nid,
                candidate_resource_type: $crt,
                candidate_terraform_name: $ctn,
                candidate_source_repo: $csr,
                equivalence_kind: $ek,
                confidence: confidence,
                evidence_level: evidence_level,
                provenance: provenance,
                context: context
            }
        """,
        {
            "nid": resource_node_id,
            "crt": candidate_resource_type,
            "ctn": candidate_terraform_name,
            "csr": candidate_source_repo,
            "ek": equivalence_kind,
        },
    )

    if existing["rows"]:
        eid, old_conf, old_ev, old_prov, old_ctx = existing["rows"][0]

        best_conf = old_conf or "low"
        if _LINK_CONFIDENCE_RANK.get(confidence, 0) > _LINK_CONFIDENCE_RANK.get(best_conf, 0):
            best_conf = confidence

        best_ev = old_ev or "inferred"
        if _EVIDENCE_LEVEL_RANK.get(evidence_level, 0) > _EVIDENCE_LEVEL_RANK.get(best_ev, 0):
            best_ev = evidence_level

        chain = [p.strip() for p in (old_prov or "").split(",") if p.strip()]
        if provenance and provenance not in chain:
            chain.append(provenance)
        merged_prov = ", ".join(chain) if chain else provenance
        merged_ctx = context_text or old_ctx or ""

        if (best_conf != old_conf or best_ev != old_ev or
                merged_prov != (old_prov or "") or merged_ctx != (old_ctx or "")):
            client.run(
                """
                ?[equiv_id, confidence, evidence_level, provenance, context, updated_at] <-
                    [[$eid, $conf, $ev, $prov, $ctx, $now]]
                :update resource_equivalences {
                    equiv_id, confidence, evidence_level, provenance, context, updated_at
                }
                """,
                {"eid": eid, "conf": best_conf, "ev": best_ev,
                 "prov": merged_prov, "ctx": merged_ctx, "now": now},
            )
        return eid

    eid = _next_id(client, "resource_equivalences")
    client.run(
        """
        ?[equiv_id, resource_node_id, candidate_resource_type, candidate_terraform_name,
          candidate_source_repo, equivalence_kind, confidence, evidence_level,
          provenance, context, created_at, updated_at] <-
          [[$eid, $nid, $crt, $ctn, $csr, $ek, $conf, $ev, $prov, $ctx, $now, $now]]
        :put resource_equivalences {
            equiv_id, resource_node_id, candidate_resource_type, candidate_terraform_name,
            candidate_source_repo, equivalence_kind, confidence, evidence_level,
            provenance, context, created_at, updated_at
        }
        """,
        {
            "eid": eid, "nid": resource_node_id, "crt": candidate_resource_type,
            "ctn": candidate_terraform_name, "csr": candidate_source_repo,
            "ek": equivalence_kind, "conf": confidence, "ev": evidence_level,
            "prov": provenance, "ctx": context_text, "now": now,
        },
    )
    return eid


# ---------------------------------------------------------------------------
# Node upsert
# ---------------------------------------------------------------------------

def _upsert_node(
    client,
    resource_type: str,
    terraform_name: str,
    source_repo: str,
    friendly_name: str = "",
    provider: str = "",
    canonical_name: str = "",
) -> int:
    """
    Insert or update a resource node. Returns the node_id.
    On conflict (same type+name+repo) updates friendly_name if provided.
    On cross-repo match (same type+name, different repo) merges alias.
    Uses Datalog identity resolution instead of procedural SQL joins.
    """
    now = _now()

    # Check for existing node in same repo
    row = client.run(
        """
        ?[node_id, aliases, canonical_name] :=
            *resource_nodes{node_id: node_id, resource_type: $rt,
                terraform_name: $tn, source_repo: $sr,
                aliases: aliases, canonical_name: canonical_name}
        """,
        {"rt": resource_type, "tn": terraform_name, "sr": source_repo},
    )
    if row["rows"]:
        nid, _, _ = row["rows"][0]
        if friendly_name:
            client.run(
                "?[node_id, friendly_name, updated_at] <- [[$nid, $fn, $now]] "
                ":update resource_nodes { node_id, friendly_name, updated_at }",
                {"nid": nid, "fn": friendly_name, "now": now},
            )
        return nid

    # Check for cross-repo match (same type+name, different repo) — Datalog identity resolution
    cross = client.run(
        """
        ?[node_id, aliases, source_repo, canonical_name] :=
            *resource_nodes{node_id: node_id, resource_type: $rt,
                terraform_name: $tn, source_repo: source_repo,
                aliases: aliases, canonical_name: canonical_name},
            source_repo != $sr
        :limit 1
        """,
        {"rt": resource_type, "tn": terraform_name, "sr": source_repo},
    )

    if not cross["rows"] and canonical_name:
        # Check for inferred placeholder nodes referencing this canonical name
        inferred_name = f"__inferred__{canonical_name}"
        cross = client.run(
            """
            ?[node_id, aliases, source_repo, canonical_name] :=
                *resource_nodes{node_id: node_id, resource_type: $rt,
                    terraform_name: $tn, source_repo: source_repo,
                    aliases: aliases, canonical_name: canonical_name},
                source_repo != $sr
            :limit 1
            """,
            {"rt": resource_type, "tn": inferred_name, "sr": source_repo},
        )
        if cross["rows"]:
            cid, _, csrc, _ = cross["rows"][0]
            # Promote placeholder to real extracted node
            client.run(
                """
                ?[node_id, terraform_name, canonical_name, confidence, friendly_name, provider, updated_at] <-
                    [[$cid, $tn, $cn, 'extracted', $fn, $prov, $now]]
                :update resource_nodes {
                    node_id, terraform_name, canonical_name, confidence, friendly_name, provider, updated_at
                }
                """,
                {"cid": cid, "tn": terraform_name, "cn": canonical_name or "",
                 "fn": friendly_name or "", "prov": provider, "now": now},
            )
            _upsert_equivalence(client, resource_node_id=cid,
                candidate_resource_type=resource_type,
                candidate_terraform_name=terraform_name,
                candidate_source_repo=source_repo,
                equivalence_kind="cross_repo_alias",
                confidence="high", evidence_level="extracted",
                provenance="placeholder_promotion",
                context_text=(
                    f"Promoted inferred placeholder '{inferred_name}' in repo '{csrc}' "
                    f"using extracted resource '{resource_type}.{terraform_name}' from repo '{source_repo}'"
                ))
            _upsert_equivalence(client, resource_node_id=cid,
                candidate_resource_type=resource_type,
                candidate_terraform_name=inferred_name,
                candidate_source_repo=csrc,
                equivalence_kind="placeholder_promotion",
                confidence="high", evidence_level="extracted",
                provenance="inferred_placeholder",
                context_text=(
                    f"Placeholder '{resource_type}.{inferred_name}' was promoted after cross-repo match "
                    f"with '{resource_type}.{terraform_name}'"
                ))
            # Auto-resolve enrichment queue items for this node
            client.run(
                """
                pending_ids[qid] :=
                    *enrichment_queue{queue_id: qid, resource_node_id: $nid, status: 'pending_review'}
                ?[queue_id, status, resolved_by, resolved_at] :=
                    pending_ids[queue_id],
                    status = 'confirmed', resolved_by = 'scan', resolved_at = $now
                :update enrichment_queue { queue_id, status, resolved_by, resolved_at }
                """,
                {"nid": cid, "now": now},
            )
            return cid

    if cross["rows"]:
        cid, raw_aliases, csrc, ccan = cross["rows"][0]
        aliases = json.loads(raw_aliases or "[]")
        if source_repo not in aliases:
            aliases.append(source_repo)
        client.run(
            "?[node_id, aliases, updated_at] <- [[$cid, $al, $now]] "
            ":update resource_nodes { node_id, aliases, updated_at }",
            {"cid": cid, "al": json.dumps(aliases), "now": now},
        )
        _upsert_equivalence(client, resource_node_id=cid,
            candidate_resource_type=resource_type,
            candidate_terraform_name=terraform_name,
            candidate_source_repo=source_repo,
            equivalence_kind="cross_repo_alias",
            confidence="high" if ccan else "medium",
            evidence_level="extracted" if ccan else "inferred",
            provenance="cross_repo_reference",
            context_text=(
                f"Resource {resource_type}.{terraform_name} appears in both "
                f"'{csrc}' and '{source_repo}'"
            ))
        if not ccan:
            _queue_assumption(client, resource_node_id=cid,
                gap_type="cross_repo_link",
                context=(
                    f"Resource {resource_type}.{terraform_name} appears in both "
                    f"'{csrc}' and '{source_repo}'"
                ),
                assumption_text=(
                    f"These are likely the same "
                    f"{resource_type.replace('azurerm_', '').replace('_', ' ').title()} "
                    f"instance referenced across repos"
                ),
                assumption_basis="cross_repo_reference",
                confidence="medium",
                suggested_value="Check both repos to confirm canonical name")
        return cid

    # Insert new node
    nid = _next_id(client, "resource_nodes")
    client.run(
        """
        ?[node_id, resource_type, terraform_name, source_repo, canonical_name,
          friendly_name, provider, aliases, confidence, created_at, updated_at] <-
          [[$nid, $rt, $tn, $sr, $cn, $fn, $prov, '[]', 'extracted', $now, $now]]
        :put resource_nodes {
            node_id, resource_type, terraform_name, source_repo, canonical_name,
            friendly_name, provider, aliases, confidence, created_at, updated_at
        }
        """,
        {"nid": nid, "rt": resource_type, "tn": terraform_name, "sr": source_repo,
         "cn": canonical_name or "", "fn": friendly_name or "", "prov": provider, "now": now},
    )
    return nid


# ---------------------------------------------------------------------------
# Relationship upsert
# ---------------------------------------------------------------------------

def _upsert_relationship(
    client,
    source_id: int,
    target_id: int,
    rel_type: str,
    source_repo: str,
    confidence: str,
    notes: str = "",
) -> Optional[int]:
    """Insert relationship, upgrade confidence if new observation is more certain."""
    _CONF_RANK = {"extracted": 1, "inferred": 2, "user_confirmed": 3}
    existing = client.run(
        """
        ?[rel_id, confidence] :=
            *resource_relationships{rel_id: rel_id, source_id: $src,
                target_id: $tgt, relationship_type: $rt, confidence: confidence}
        """,
        {"src": source_id, "tgt": target_id, "rt": rel_type},
    )
    if existing["rows"]:
        rid, old_conf = existing["rows"][0]
        if _CONF_RANK.get(confidence, 0) > _CONF_RANK.get(old_conf, 0):
            client.run(
                "?[rel_id, confidence, notes] <- [[$rid, $conf, $notes]] "
                ":update resource_relationships { rel_id, confidence, notes }",
                {"rid": rid, "conf": confidence, "notes": notes or ""},
            )
        return rid

    rid = _next_id(client, "resource_relationships")
    client.run(
        """
        ?[rel_id, source_id, target_id, relationship_type, source_repo, confidence, notes, created_at] <-
            [[$rid, $src, $tgt, $rt, $sr, $conf, $notes, $now]]
        :put resource_relationships {
            rel_id, source_id, target_id, relationship_type, source_repo, confidence, notes, created_at
        }
        """,
        {"rid": rid, "src": source_id, "tgt": target_id, "rt": rel_type,
         "sr": source_repo, "conf": confidence, "notes": notes or "", "now": _now()},
    )
    return rid


# ---------------------------------------------------------------------------
# Enrichment queue
# ---------------------------------------------------------------------------

def _queue_assumption(
    client,
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
    # Deduplicate by context + status
    exists = client.run(
        "?[queue_id] := *enrichment_queue{queue_id: queue_id, context: $ctx, status: 'pending_review'}",
        {"ctx": context},
    )
    if exists["rows"]:
        return

    qid = _next_id(client, "enrichment_queue")
    client.run(
        """
        ?[queue_id, resource_node_id, relationship_id, gap_type, context,
          assumption_text, assumption_basis, confidence, suggested_value, created_at] <-
          [[$qid, $nid, $rid, $gt, $ctx, $atext, $abasis, $conf, $sval, $now]]
        :put enrichment_queue {
            queue_id, resource_node_id, relationship_id, gap_type, context,
            assumption_text, assumption_basis, confidence, suggested_value, created_at
        }
        """,
        {"qid": qid, "nid": resource_node_id, "rid": relationship_id,
         "gt": gap_type, "ctx": context, "atext": assumption_text,
         "abasis": assumption_basis, "conf": confidence, "sval": suggested_value,
         "now": _now()},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_context(context: RepositoryContext, db_path: Path = DB_PATH) -> None:
    """
    Main entry point. Upsert all resources and relationships from *context*
    into the knowledge graph tables, and queue enrichment items for gaps.
    Uses Datalog identity resolution via resource_equivalences for cross-repo aliases.
    """
    import resource_type_db as _rtdb

    client = _get_client(db_path)
    repo_name = context.repository_name

    # 1. Upsert all resource nodes
    node_id_map: dict[str, int] = {}
    for resource in context.resources:
        friendly = _rtdb.get_friendly_name(None, resource.resource_type)
        provider = _rtdb.get_provider_key(None, resource.resource_type) or ""
        canonical = resource.properties.get("canonical_name", "")
        confidence_override = "inferred" if resource.properties.get("inferred") == "true" else "extracted"
        node_id = _upsert_node(
            client,
            resource_type=resource.resource_type,
            terraform_name=resource.name,
            source_repo=repo_name,
            friendly_name=friendly,
            provider=provider,
            canonical_name=canonical,
        )
        if confidence_override == "inferred":
            # Downgrade confidence for inferred placeholder nodes
            client.run(
                """
                already_extracted[nid] :=
                    *resource_nodes{node_id: nid, confidence: c}, c = 'extracted', nid = $nid
                ?[node_id, confidence] :=
                    already_extracted[node_id], confidence = 'inferred'
                :update resource_nodes { node_id, confidence }
                """,
                {"nid": node_id},
            )
        node_id_map[f"{resource.resource_type}.{resource.name}"] = node_id

    # 2. Upsert typed relationships
    for rel in context.relationships:
        src_key = f"{rel.source_type}.{rel.source_name}"
        tgt_key = f"{rel.target_type}.{rel.target_name}"
        is_gap = rel.target_type == "unknown" or rel.confidence == "inferred"

        if src_key not in node_id_map:
            node_id_map[src_key] = _upsert_node(client, rel.source_type, rel.source_name, repo_name)
        src_id = node_id_map[src_key]

        if is_gap:
            gap_type = "ambiguous_ref" if "var." in rel.target_name else "missing_target"
            assumption_text = (
                f"{rel.source_type.replace('azurerm_','').replace('aws_','').replace('_',' ').title()} "
                f"'{rel.source_name}' may {rel.relationship_type.value.replace('_',' ')} "
                f"an external resource referenced as '{rel.target_name}'"
            )
            if rel.notes:
                assumption_text = rel.notes
            _queue_assumption(
                client,
                resource_node_id=src_id,
                gap_type=gap_type,
                context=rel.notes or f"{rel.source_type}.{rel.source_name} -> {rel.target_name}",
                assumption_text=assumption_text,
                assumption_basis="variable_reference" if "var." in rel.target_name else "connection_string",
                confidence="medium" if "var." not in rel.target_name else "low",
                suggested_value="Scan the IaC repo that provisions this resource to confirm",
            )
            continue

        if tgt_key not in node_id_map:
            node_id_map[tgt_key] = _upsert_node(client, rel.target_type, rel.target_name, repo_name)
        tgt_id = node_id_map[tgt_key]

        rel_id = _upsert_relationship(
            client, src_id, tgt_id,
            rel_type=rel.relationship_type.value,
            source_repo=repo_name,
            confidence=rel.confidence,
            notes=rel.notes,
        )

        # Also persist as a resource_connections entry so diagrams can draw arrows
        try:
            from db_helpers import _get_client as _get_db_client
            db_client = _get_db_client()
            repo_row = db_client.run(
                "?[experiment_id, repo_id] := *repositories{experiment_id: experiment_id, "
                "repo_id: repo_id, repo_name: $rn}",
                {"rn": repo_name},
            )
            if repo_row["rows"]:
                experiment_id = repo_row["rows"][0][0]
                src_res = db_client.run(
                    "?[resource_id, repo_id] := *resources{resource_id: resource_id, "
                    "repo_id: repo_id, resource_name: $rn, experiment_id: $eid}",
                    {"rn": rel.source_name, "eid": experiment_id},
                )
                tgt_res = db_client.run(
                    "?[resource_id, repo_id] := *resources{resource_id: resource_id, "
                    "repo_id: repo_id, resource_name: $rn, experiment_id: $eid}",
                    {"rn": rel.target_name, "eid": experiment_id},
                )
                if src_res["rows"] and tgt_res["rows"]:
                    src_rid, src_repo_id = src_res["rows"][0]
                    tgt_rid, tgt_repo_id = tgt_res["rows"][0]
                    exists = db_client.run(
                        "?[cid] := *resource_connections{connection_id: cid, "
                        "experiment_id: $eid, source_resource_id: $src, target_resource_id: $tgt}",
                        {"eid": experiment_id, "src": src_rid, "tgt": tgt_rid},
                    )
                    if not exists["rows"]:
                        from db_helpers import _next_id as _db_next_id
                        cid = _db_next_id("resource_connections")
                        db_client.run(
                            """
                            ?[connection_id, experiment_id, source_resource_id, target_resource_id,
                              source_repo_id, target_repo_id, is_cross_repo, connection_type] <-
                              [[$cid, $eid, $src, $tgt, $srep, $trep, $cross, $ct]]
                            :put resource_connections {
                                connection_id, experiment_id, source_resource_id, target_resource_id,
                                source_repo_id, target_repo_id, is_cross_repo, connection_type
                            }
                            """,
                            {
                                "cid": cid, "eid": experiment_id,
                                "src": src_rid, "tgt": tgt_rid,
                                "srep": src_repo_id, "trep": tgt_repo_id,
                                "cross": src_repo_id != tgt_repo_id,
                                "ct": rel.relationship_type.value,
                            },
                        )
        except Exception:
            pass  # Non-fatal: diagram connections are best-effort

        # Queue low-confidence inferred relationships for confirmation
        if rel.confidence == "inferred" and rel_id:
            src_friendly = _rtdb.get_friendly_name(None, rel.source_type)
            tgt_friendly = _rtdb.get_friendly_name(None, rel.target_type)
            _queue_assumption(
                client,
                resource_node_id=src_id,
                relationship_id=rel_id,
                gap_type="assumption",
                context=(
                    f"{rel.source_type}.{rel.source_name} "
                    f"--[{rel.relationship_type.value}]--> "
                    f"{rel.target_type}.{rel.target_name}"
                ),
                assumption_text=(
                    f"{src_friendly} '{rel.source_name}' "
                    f"{rel.relationship_type.value.replace('_', ' ')} "
                    f"{tgt_friendly} '{rel.target_name}'"
                ),
                assumption_basis="attribute_pattern_match",
                confidence="medium",
                suggested_value="Confirm by inspecting the Terraform attribute",
            )


def query_graph_for_repo(repo_name: str, db_path: Path = DB_PATH) -> dict:
    """
    Returns nodes, relationships, assumptions, and equivalences for a given repo.
    Uses Datalog to resolve aliases and cross-repo identities.
    """
    client = _get_client(db_path)

    # Fetch nodes for the repo (source_repo match OR repo in aliases JSON)
    nodes_result = client.run(
        """
        ?[node_id, resource_type, terraform_name, source_repo, canonical_name,
          friendly_name, display_label, provider, aliases, confidence, properties,
          created_at, updated_at] :=
            *resource_nodes{node_id: node_id, resource_type: resource_type,
                terraform_name: terraform_name, source_repo: source_repo,
                canonical_name: canonical_name, friendly_name: friendly_name,
                display_label: display_label, provider: provider,
                aliases: aliases, confidence: confidence, properties: properties,
                created_at: created_at, updated_at: updated_at},
            source_repo = $rn
        """,
        {"rn": repo_name},
    )

    # Also fetch nodes where repo_name appears in aliases
    aliased_result = client.run(
        """
        ?[node_id, resource_type, terraform_name, source_repo, canonical_name,
          friendly_name, display_label, provider, aliases, confidence, properties,
          created_at, updated_at] :=
            *resource_nodes{node_id: node_id, resource_type: resource_type,
                terraform_name: terraform_name, source_repo: source_repo,
                canonical_name: canonical_name, friendly_name: friendly_name,
                display_label: display_label, provider: provider,
                aliases: aliases, confidence: confidence, properties: properties,
                created_at: created_at, updated_at: updated_at},
            source_repo != $rn,
            str_includes(aliases, $rn_fragment)
        """,
        {"rn": repo_name, "rn_fragment": f'"{repo_name}"'},
    )

    node_cols = [
        "node_id", "resource_type", "terraform_name", "source_repo", "canonical_name",
        "friendly_name", "display_label", "provider", "aliases", "confidence",
        "properties", "created_at", "updated_at",
    ]

    nodes = []
    seen_ids: set = set()
    for row in nodes_result["rows"] + aliased_result["rows"]:
        d = dict(zip(node_cols, row))
        if d["node_id"] not in seen_ids:
            seen_ids.add(d["node_id"])
            nodes.append(d)

    if not nodes:
        return {"nodes": [], "relationships": [], "assumptions": [], "equivalences": []}

    node_ids = [n["node_id"] for n in nodes]

    # Relationships involving these nodes
    rels: list[dict] = []
    if node_ids:
        for nid in node_ids:
            r = client.run(
                """
                ?[rel_id, source_id, target_id, relationship_type, source_repo, confidence, notes, created_at] :=
                    *resource_relationships{rel_id: rel_id, source_id: source_id, target_id: target_id,
                        relationship_type: relationship_type, source_repo: source_repo,
                        confidence: confidence, notes: notes, created_at: created_at},
                    (source_id = $nid || target_id = $nid)
                """,
                {"nid": nid},
            )
            for row in r["rows"]:
                rels.append(dict(zip(
                    ["rel_id", "source_id", "target_id", "relationship_type",
                     "source_repo", "confidence", "notes", "created_at"],
                    row,
                )))

    # Pending assumptions for these nodes
    assumptions: list[dict] = []
    for nid in node_ids:
        a = client.run(
            """
            ?[queue_id, resource_node_id, gap_type, context, assumption_text,
              assumption_basis, confidence, suggested_value, status, created_at] :=
                *enrichment_queue{queue_id: queue_id, resource_node_id: resource_node_id,
                    gap_type: gap_type, context: context, assumption_text: assumption_text,
                    assumption_basis: assumption_basis, confidence: confidence,
                    suggested_value: suggested_value, status: status, created_at: created_at},
                resource_node_id = $nid, status = 'pending_review'
            """,
            {"nid": nid},
        )
        for row in a["rows"]:
            assumptions.append(dict(zip(
                ["queue_id", "resource_node_id", "gap_type", "context", "assumption_text",
                 "assumption_basis", "confidence", "suggested_value", "status", "created_at"],
                row,
            )))

    # Equivalences involving these nodes or the repo
    equivalences: list[dict] = []
    eq_cols = [
        "equiv_id", "resource_node_id", "candidate_resource_type", "candidate_terraform_name",
        "candidate_source_repo", "equivalence_kind", "confidence", "evidence_level",
        "provenance", "context", "created_at", "updated_at",
    ]
    for nid in node_ids:
        e = client.run(
            """
            ?[equiv_id, resource_node_id, candidate_resource_type, candidate_terraform_name,
              candidate_source_repo, equivalence_kind, confidence, evidence_level,
              provenance, context, created_at, updated_at] :=
                *resource_equivalences{equiv_id: equiv_id, resource_node_id: resource_node_id,
                    candidate_resource_type: candidate_resource_type,
                    candidate_terraform_name: candidate_terraform_name,
                    candidate_source_repo: candidate_source_repo,
                    equivalence_kind: equivalence_kind, confidence: confidence,
                    evidence_level: evidence_level, provenance: provenance,
                    context: context, created_at: created_at, updated_at: updated_at},
                resource_node_id = $nid
            """,
            {"nid": nid},
        )
        for row in e["rows"]:
            equivalences.append(dict(zip(eq_cols, row)))

    # Also fetch equivalences by candidate_source_repo
    e_by_repo = client.run(
        """
        ?[equiv_id, resource_node_id, candidate_resource_type, candidate_terraform_name,
          candidate_source_repo, equivalence_kind, confidence, evidence_level,
          provenance, context, created_at, updated_at] :=
            *resource_equivalences{equiv_id: equiv_id, resource_node_id: resource_node_id,
                candidate_resource_type: candidate_resource_type,
                candidate_terraform_name: candidate_terraform_name,
                candidate_source_repo: candidate_source_repo,
                equivalence_kind: equivalence_kind, confidence: confidence,
                evidence_level: evidence_level, provenance: provenance,
                context: context, created_at: created_at, updated_at: updated_at},
            candidate_source_repo = $rn
        """,
        {"rn": repo_name},
    )
    eq_ids = {eq["equiv_id"] for eq in equivalences}
    for row in e_by_repo["rows"]:
        d = dict(zip(eq_cols, row))
        if d["equiv_id"] not in eq_ids:
            eq_ids.add(d["equiv_id"])
            equivalences.append(d)

    # Deduplicate relationships
    seen_rels: set = set()
    unique_rels = []
    for r in rels:
        key = r["rel_id"]
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(r)

    return {
        "nodes": nodes,
        "relationships": unique_rels,
        "assumptions": assumptions,
        "equivalences": equivalences,
    }
