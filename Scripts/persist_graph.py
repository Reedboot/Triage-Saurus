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

from pycozo.client import Client

from models import RepositoryContext, Relationship, RelationshipType


DB_PATH = Path(__file__).resolve().parents[1] / "Output/Learning/triage.cozo"


def _get_db(db_path: Path = DB_PATH) -> Client:
    """Get CozoDB client, initialising schema if needed."""
    from db_helpers import _db_cache
    key = str(db_path)
    if key in _db_cache:
        return _db_cache[key]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Client('rocksdb', str(db_path), dataframe=False)
    from init_database import init_schema
    init_schema(db)
    _db_cache[key] = db
    return db


def _rows_to_dicts(result: dict) -> list[dict]:
    headers = result['headers']
    return [dict(zip(headers, row)) for row in result['rows']]


def _next_id(db: Client, seq_name: str) -> int:
    result = db.run('?[v] := *ts_seqs{name: $n, value: v}', {'n': seq_name})
    current = result['rows'][0][0] if result['rows'] else 0
    new_id = current + 1
    db.put('ts_seqs', [{'name': seq_name, 'value': new_id}])
    return new_id


def _now() -> str:
    return datetime.utcnow().isoformat()


_LINK_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_EVIDENCE_LEVEL_RANK = {"inferred": 1, "extracted": 2, "user_confirmed": 3}


def _relation_exists(db: Client, relation_name: str) -> bool:
    rels = db.relations()
    rel_names = [row[0] for row in rels['rows']]
    return relation_name in rel_names


def _upsert_equivalence(
    db: Client,
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
    if not _relation_exists(db, 'resource_equivalences'):
        return None

    now = _now()
    existing = _rows_to_dicts(db.run('''
        ?[id, confidence, evidence_level, provenance, context] :=
            *resource_equivalences{id, resource_node_id, candidate_resource_type,
                                   candidate_terraform_name, candidate_source_repo,
                                   equivalence_kind, confidence, evidence_level, provenance, context},
            resource_node_id = $rnid,
            candidate_resource_type = $crt,
            candidate_terraform_name = $ctn,
            candidate_source_repo = $csr,
            equivalence_kind = $ek
    ''', {'rnid': resource_node_id, 'crt': candidate_resource_type,
          'ctn': candidate_terraform_name, 'csr': candidate_source_repo, 'ek': equivalence_kind}))

    if existing:
        row = existing[0]
        best_confidence = row['confidence'] or 'low'
        if _LINK_CONFIDENCE_RANK.get(confidence, 0) > _LINK_CONFIDENCE_RANK.get(best_confidence, 0):
            best_confidence = confidence

        best_evidence = row['evidence_level'] or 'inferred'
        if _EVIDENCE_LEVEL_RANK.get(evidence_level, 0) > _EVIDENCE_LEVEL_RANK.get(best_evidence, 0):
            best_evidence = evidence_level

        provenance_chain = [p.strip() for p in (row['provenance'] or '').split(',') if p.strip()]
        if provenance and provenance not in provenance_chain:
            provenance_chain.append(provenance)
        merged_provenance = ', '.join(provenance_chain) if provenance_chain else provenance
        merged_context = context_text or row['context'] or ''

        if (
            best_confidence != row['confidence']
            or best_evidence != row['evidence_level']
            or merged_provenance != (row['provenance'] or '')
            or merged_context != (row['context'] or '')
        ):
            db.update('resource_equivalences', [{
                'id': row['id'],
                'confidence': best_confidence,
                'evidence_level': best_evidence,
                'provenance': merged_provenance,
                'context': merged_context,
                'updated_at': now,
            }])
        return row['id']

    new_id = _next_id(db, 'resource_equivalences')
    db.put('resource_equivalences', [{
        'id': new_id,
        'resource_node_id': resource_node_id,
        'candidate_resource_type': candidate_resource_type,
        'candidate_terraform_name': candidate_terraform_name,
        'candidate_source_repo': candidate_source_repo,
        'equivalence_kind': equivalence_kind,
        'confidence': confidence,
        'evidence_level': evidence_level,
        'provenance': provenance,
        'context': context_text,
        'created_at': now,
        'updated_at': now,
    }])
    return new_id


def _upsert_node(
    db: Client,
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
    now = _now()

    # Check for existing node in same repo
    same_repo = _rows_to_dicts(db.run('''
        ?[id, aliases, canonical_name] :=
            *resource_nodes{id, resource_type, terraform_name, source_repo, aliases, canonical_name},
            resource_type = $rt, terraform_name = $tn, source_repo = $sr
    ''', {'rt': resource_type, 'tn': terraform_name, 'sr': source_repo}))

    if same_repo:
        if friendly_name:
            db.update('resource_nodes', [{'id': same_repo[0]['id'], 'friendly_name': friendly_name, 'updated_at': now}])
        return same_repo[0]['id']

    # Check for cross-repo match (same type+name, different repo)
    cross = _rows_to_dicts(db.run('''
        ?[id, aliases, source_repo, canonical_name] :=
            *resource_nodes{id, resource_type, terraform_name, source_repo, aliases, canonical_name},
            resource_type = $rt, terraform_name = $tn, source_repo != $sr
    ''', {'rt': resource_type, 'tn': terraform_name, 'sr': source_repo}))

    if not cross and canonical_name:
        inferred_name = f"__inferred__{canonical_name}"
        inferred_cross = _rows_to_dicts(db.run('''
            ?[id, aliases, source_repo, canonical_name] :=
                *resource_nodes{id, resource_type, terraform_name, source_repo, aliases, canonical_name},
                resource_type = $rt, terraform_name = $tn, source_repo != $sr
        ''', {'rt': resource_type, 'tn': inferred_name, 'sr': source_repo}))
        if inferred_cross:
            cross_row = inferred_cross[0]
            # Promote the placeholder to a real extracted node
            db.update('resource_nodes', [{
                'id': cross_row['id'],
                'terraform_name': terraform_name,
                'canonical_name': canonical_name,
                'confidence': 'extracted',
                'friendly_name': friendly_name or '',
                'provider': provider,
                'updated_at': now,
            }])
            _upsert_equivalence(
                db,
                resource_node_id=cross_row['id'],
                candidate_resource_type=resource_type,
                candidate_terraform_name=terraform_name,
                candidate_source_repo=source_repo,
                equivalence_kind="cross_repo_alias",
                confidence="high",
                evidence_level="extracted",
                provenance="placeholder_promotion",
                context_text=(
                    f"Promoted inferred placeholder '{inferred_name}' in repo '{cross_row['source_repo']}' "
                    f"using extracted resource '{resource_type}.{terraform_name}' from repo '{source_repo}'"
                ),
            )
            _upsert_equivalence(
                db,
                resource_node_id=cross_row['id'],
                candidate_resource_type=resource_type,
                candidate_terraform_name=inferred_name,
                candidate_source_repo=cross_row['source_repo'],
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
            pending = _rows_to_dicts(db.run(
                '?[id] := *enrichment_queue{id, resource_node_id, status}, resource_node_id = $nid, status = "pending_review"',
                {'nid': cross_row['id']}
            ))
            for p in pending:
                db.update('enrichment_queue', [{'id': p['id'], 'status': 'confirmed', 'resolved_by': 'scan', 'resolved_at': now}])
            return cross_row['id']

    if cross:
        cross_row = cross[0]
        # Merge this repo into the aliases list
        aliases = json.loads(cross_row['aliases'] or '[]')
        if source_repo not in aliases:
            aliases.append(source_repo)
        db.update('resource_nodes', [{'id': cross_row['id'], 'aliases': json.dumps(aliases), 'updated_at': now}])
        _upsert_equivalence(
            db,
            resource_node_id=cross_row['id'],
            candidate_resource_type=resource_type,
            candidate_terraform_name=terraform_name,
            candidate_source_repo=source_repo,
            equivalence_kind="cross_repo_alias",
            confidence="high" if cross_row['canonical_name'] else "medium",
            evidence_level="extracted" if cross_row['canonical_name'] else "inferred",
            provenance="cross_repo_reference",
            context_text=(
                f"Resource {resource_type}.{terraform_name} appears in both "
                f"'{cross_row['source_repo']}' and '{source_repo}'"
            ),
        )
        if not cross_row['canonical_name']:
            _queue_assumption(
                db,
                resource_node_id=cross_row['id'],
                gap_type="cross_repo_link",
                context=(
                    f"Resource {resource_type}.{terraform_name} appears in both "
                    f"'{cross_row['source_repo']}' and '{source_repo}'"
                ),
                assumption_text=(
                    f"These are likely the same {resource_type.replace('azurerm_', '').replace('aws_','').replace('_', ' ').title()} "
                    f"instance referenced across repos"
                ),
                assumption_basis="cross_repo_reference",
                confidence="medium",
                suggested_value="Check both repos to confirm canonical name",
            )
        return cross_row['id']

    # Insert new node
    new_id = _next_id(db, 'resource_nodes')
    db.put('resource_nodes', [{
        'id': new_id,
        'resource_type': resource_type,
        'terraform_name': terraform_name,
        'canonical_name': canonical_name or None,
        'friendly_name': friendly_name or '',
        'display_label': None,
        'provider': provider,
        'source_repo': source_repo,
        'aliases': '[]',
        'confidence': 'extracted',
        'properties': '{}',
        'created_at': now,
        'updated_at': now,
    }])
    return new_id


def _upsert_relationship(
    db: Client,
    source_id: int,
    target_id: int,
    rel_type: str,
    source_repo: str,
    confidence: str,
    notes: str = "",
) -> Optional[int]:
    """Insert relationship, ignore if already exists at same or higher confidence."""
    _CONFIDENCE_RANK = {"extracted": 1, "inferred": 2, "user_confirmed": 3}

    existing = _rows_to_dicts(db.run('''
        ?[id, confidence, notes] :=
            *resource_relationships{id, source_id, target_id, relationship_type, confidence, notes},
            source_id = $sid, target_id = $tid, relationship_type = $rt
    ''', {'sid': source_id, 'tid': target_id, 'rt': rel_type}))

    if existing:
        row = existing[0]
        if _CONFIDENCE_RANK.get(confidence, 0) > _CONFIDENCE_RANK.get(row['confidence'], 0):
            db.update('resource_relationships', [{
                'id': row['id'],
                'confidence': confidence,
                'notes': notes or row.get('notes') or '',
            }])
        return row['id']

    new_id = _next_id(db, 'resource_relationships')
    db.put('resource_relationships', [{
        'id': new_id,
        'source_id': source_id,
        'target_id': target_id,
        'relationship_type': rel_type,
        'source_repo': source_repo,
        'confidence': confidence,
        'notes': notes or '',
        'created_at': _now(),
    }])
    return new_id


def _queue_assumption(
    db: Client,
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
    exists = db.run(
        '?[id] := *enrichment_queue{id, context, status}, context = $ctx, status = "pending_review"',
        {'ctx': context}
    )
    if exists['rows']:
        return

    new_id = _next_id(db, 'enrichment_queue')
    db.put('enrichment_queue', [{
        'id': new_id,
        'resource_node_id': resource_node_id,
        'relationship_id': relationship_id,
        'gap_type': gap_type,
        'context': context,
        'assumption_text': assumption_text,
        'assumption_basis': assumption_basis,
        'confidence': confidence,
        'suggested_value': suggested_value,
        'status': 'pending_review',
        'resolved_by': None,
        'resolved_at': None,
        'rejection_reason': None,
        'created_at': _now(),
    }])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_context(context: RepositoryContext, db_path: Path = DB_PATH) -> None:
    """
    Main entry point. Upsert all resources and relationships from *context*
    into the knowledge graph tables, and queue enrichment items for gaps.
    """
    import resource_type_db as _rtdb

    db = _get_db(db_path)
    _rt_db: Optional[Client] = db

    repo_name = context.repository_name

    # 1. Upsert all resource nodes
    node_id_map: dict[str, int] = {}   # "resource_type.terraform_name" → node_id
    for resource in context.resources:
        friendly = _rtdb.get_friendly_name(_rt_db, resource.resource_type)
        provider = _rtdb.get_provider_key(_rt_db, resource.resource_type) or ""
        canonical = resource.properties.get("canonical_name", "")
        confidence_override = "inferred" if resource.properties.get("inferred") == "true" else "extracted"
        node_id = _upsert_node(
            db,
            resource_type=resource.resource_type,
            terraform_name=resource.name,
            source_repo=repo_name,
            friendly_name=friendly,
            provider=provider,
            canonical_name=canonical,
        )
        if confidence_override == "inferred":
            # Downgrade confidence for inferred placeholder nodes if still extracted
            current = _rows_to_dicts(db.run(
                '?[confidence] := *resource_nodes{id, confidence}, id = $nid',
                {'nid': node_id}
            ))
            if current and current[0]['confidence'] == 'extracted':
                db.update('resource_nodes', [{'id': node_id, 'confidence': 'inferred'}])
        node_id_map[f"{resource.resource_type}.{resource.name}"] = node_id

    # 2. Upsert typed relationships
    for rel in context.relationships:
        src_key = f"{rel.source_type}.{rel.source_name}"
        tgt_key = f"{rel.target_type}.{rel.target_name}"
        is_gap = rel.target_type == "unknown" or rel.confidence == "inferred"

        if src_key not in node_id_map:
            node_id_map[src_key] = _upsert_node(db, rel.source_type, rel.source_name, repo_name)
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
                db,
                resource_node_id=src_id,
                gap_type=gap_type,
                context=rel.notes or f"{rel.source_type}.{rel.source_name} → {rel.target_name}",
                assumption_text=assumption_text,
                assumption_basis="variable_reference" if "var." in rel.target_name else "connection_string",
                confidence="medium" if "var." not in rel.target_name else "low",
                suggested_value="Scan the IaC repo that provisions this resource to confirm",
            )
            continue

        if tgt_key not in node_id_map:
            node_id_map[tgt_key] = _upsert_node(db, rel.target_type, rel.target_name, repo_name)
        tgt_id = node_id_map[tgt_key]

        rel_id = _upsert_relationship(
            db, src_id, tgt_id,
            rel_type=rel.relationship_type.value,
            source_repo=repo_name,
            confidence=rel.confidence,
            notes=rel.notes,
        )

        # Also persist as a resource_connections entry when both source/target resources
        # exist in the resources table so diagrams can draw arrows.
        try:
            repo_rows = _rows_to_dicts(db.run(
                '?[experiment_id, id] := *repositories{id, repo_name, experiment_id}, repo_name = $rn',
                {'rn': repo_name}
            ))
            if repo_rows:
                experiment_id = repo_rows[0]['experiment_id']
                repo_id = repo_rows[0]['id']

                src_res_rows = _rows_to_dicts(db.run(
                    '?[id, repo_id] := *resources{id, repo_id, resource_name}, resource_name = $rn, repo_id = $rid',
                    {'rn': rel.source_name, 'rid': repo_id}
                ))
                tgt_res_rows = _rows_to_dicts(db.run(
                    '?[id, repo_id] := *resources{id, repo_id, resource_name}, resource_name = $rn, repo_id = $rid',
                    {'rn': rel.target_name, 'rid': repo_id}
                ))

                if src_res_rows and tgt_res_rows and experiment_id:
                    src_res = src_res_rows[0]
                    tgt_res = tgt_res_rows[0]
                    exists = db.run('''
                        ?[id] := *resource_connections{id, experiment_id, source_resource_id, target_resource_id},
                        experiment_id = $eid, source_resource_id = $src, target_resource_id = $tgt
                    ''', {'eid': experiment_id, 'src': src_res['id'], 'tgt': tgt_res['id']})
                    if not exists['rows']:
                        conn_id = _next_id(db, 'resource_connections')
                        db.put('resource_connections', [{
                            'id': conn_id,
                            'experiment_id': experiment_id,
                            'source_resource_id': src_res['id'],
                            'target_resource_id': tgt_res['id'],
                            'source_repo_id': src_res['repo_id'],
                            'target_repo_id': tgt_res['repo_id'],
                            'is_cross_repo': src_res['repo_id'] != tgt_res['repo_id'],
                            'connection_type': rel.relationship_type.value,
                            'protocol': None, 'port': None, 'authentication': None,
                            'authorization': None, 'auth_method': None, 'is_encrypted': None,
                            'via_component': None, 'notes': None,
                        }])
        except Exception:
            # Non-fatal: diagram connections are best-effort
            pass

        # Queue low-confidence inferred relationships for confirmation
        if rel.confidence == "inferred" and rel_id:
            src_friendly = _rtdb.get_friendly_name(_rt_db, rel.source_type)
            tgt_friendly = _rtdb.get_friendly_name(_rt_db, rel.target_type)
            _queue_assumption(
                db,
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
    Returns nodes and relationships for a given repo from the knowledge graph.
    Used by report_generation.py to build the inventory and diagram.
    """
    db = _get_db(db_path)

    all_nodes = _rows_to_dicts(db.run('''
        ?[id, resource_type, terraform_name, canonical_name, friendly_name, source_repo,
          aliases, confidence, properties, created_at, updated_at] :=
            *resource_nodes{id, resource_type, terraform_name, canonical_name, friendly_name,
                           source_repo, aliases, confidence, properties, created_at, updated_at}
    '''))
    nodes = [
        n for n in all_nodes
        if n['source_repo'] == repo_name or repo_name in json.loads(n.get('aliases') or '[]')
    ]

    node_ids = [n['id'] for n in nodes]
    if not node_ids:
        return {'nodes': [], 'relationships': [], 'assumptions': [], 'equivalences': []}

    node_id_set = set(node_ids)

    all_rels = _rows_to_dicts(db.run('''
        ?[id, source_id, target_id, rel_type, source_repo, confidence, notes, created_at] :=
            *resource_relationships{id, source_id, target_id, relationship_type: rel_type,
                                    source_repo, confidence, notes, created_at}
    '''))
    relationships = [r for r in all_rels if r['source_id'] in node_id_set or r['target_id'] in node_id_set]

    all_eq = _rows_to_dicts(db.run('''
        ?[id, rnid, gap_type, context, assumption_text, confidence, status, created_at] :=
            *enrichment_queue{id, resource_node_id: rnid, gap_type, context,
                              assumption_text, confidence, status, created_at},
            status = "pending_review"
    '''))
    assumptions = [e for e in all_eq if e['rnid'] in node_id_set]

    equivalences: list[dict] = []
    if _relation_exists(db, 'resource_equivalences'):
        all_equiv = _rows_to_dicts(db.run('''
            ?[id, rnid, crt, ctn, csr, ek, conf, el, prov, ctx, ca] :=
                *resource_equivalences{id, resource_node_id: rnid, candidate_resource_type: crt,
                                       candidate_terraform_name: ctn, candidate_source_repo: csr,
                                       equivalence_kind: ek, confidence: conf, evidence_level: el,
                                       provenance: prov, context: ctx, created_at: ca}
        '''))
        equivalences = [e for e in all_equiv if e['rnid'] in node_id_set or e['csr'] == repo_name]

    return {
        'nodes': nodes,
        'relationships': relationships,
        'assumptions': assumptions,
        'equivalences': equivalences,
    }
