#!/usr/bin/env python3
"""Database helper functions for Triage-Saurus (CozoDB backend)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from pycozo.client import Client

# Database location
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage.cozo"

ENRICHMENT_QUEUE_STATUSES = {"pending_review", "confirmed", "rejected"}
ENRICHMENT_DECISION_MAP = {
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "reject": "rejected",
    "rejected": "rejected",
}
ENRICHMENT_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

# apply_topology_backfills lives in init_database; re-export for callers
from init_database import apply_topology_backfills  # noqa: E402

# ---------------------------------------------------------------------------
# DB cache & connection helpers
# ---------------------------------------------------------------------------

_db_cache: dict[str, Client] = {}


def _get_db(path: Path = DB_PATH) -> Client:
    key = str(path)
    if key not in _db_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = Client('rocksdb', str(path), dataframe=False)
        _ensure_schema(db)
        _db_cache[key] = db
    return _db_cache[key]


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    """Context manager that yields a CozoDB Client."""
    db = _get_db(db_path or DB_PATH)
    yield db
    # CozoDB operations are atomic; no explicit commit/rollback needed


def _rows_to_dicts(result: dict) -> list[dict]:
    headers = result['headers']
    return [dict(zip(headers, row)) for row in result['rows']]


def _next_id(db: Client, seq_name: str) -> int:
    result = db.run('?[v] := *ts_seqs[$n, v]', {'n': seq_name})
    current = result['rows'][0][0] if result['rows'] else 0
    new_id = current + 1
    db.put('ts_seqs', [{'name': seq_name, 'value': new_id}])
    return new_id


def _now() -> str:
    return datetime.now().isoformat()


def _ensure_schema(db: Client) -> None:
    """Delegate schema initialisation to init_database."""
    from init_database import init_schema
    init_schema(db)


# ============================================================================
# REPOSITORY OPERATIONS
# ============================================================================

def insert_repository(
    experiment_id: str,
    repo_path: Path,
    repo_type: str = "Infrastructure",
) -> Tuple[int, str]:
    """Register repository - store only folder name (portable)."""
    repo_name = repo_path.name

    repo_url = None
    try:
        import git
        repo_obj = git.Repo(repo_path)
        if repo_obj.remotes:
            repo_url = repo_obj.remotes.origin.url
    except Exception:
        pass

    with get_db_connection() as db:
        result = db.run(
            '?[id] := *repositories{id, experiment_id, repo_name: rn},'
            ' experiment_id = $eid, rn = $name',
            {'eid': experiment_id, 'name': repo_name},
        )
        if result['rows']:
            return result['rows'][0][0], repo_name

        new_id = _next_id(db, 'repositories')
        db.put('repositories', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'repo_name': repo_name,
            'repo_url': repo_url,
            'repo_type': repo_type,
            'primary_language': None,
            'files_scanned': None,
            'iac_files_count': None,
            'code_files_count': None,
            'scanned_at': _now(),
        }])
        return new_id, repo_name


def update_repository_stats(
    experiment_id: str,
    repo_name: str,
    files_scanned: int,
    iac_files: int,
    code_files: int,
):
    """Update repository scan statistics."""
    with get_db_connection() as db:
        result = db.run(
            '?[id] := *repositories{id, experiment_id, repo_name: rn},'
            ' experiment_id = $eid, rn = $name',
            {'eid': experiment_id, 'name': repo_name},
        )
        if result['rows']:
            db.update('repositories', [{
                'id': result['rows'][0][0],
                'files_scanned': files_scanned,
                'iac_files_count': iac_files,
                'code_files_count': code_files,
            }])


def ensure_repository_entry(experiment_id: str, repo_name: str) -> int:
    """Ensure a repository record exists for the experiment."""
    with get_db_connection() as db:
        result = db.run(
            '?[id] := *repositories{id, experiment_id, repo_name: rn},'
            ' experiment_id = $eid, rn = $name',
            {'eid': experiment_id, 'name': repo_name},
        )
        if result['rows']:
            return result['rows'][0][0]

        new_id = _next_id(db, 'repositories')
        db.put('repositories', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'repo_name': repo_name,
            'repo_url': None,
            'repo_type': None,
            'primary_language': None,
            'files_scanned': None,
            'iac_files_count': None,
            'code_files_count': None,
            'scanned_at': _now(),
        }])
        return new_id


def get_repository_id(experiment_id: str, repo_name: str) -> Optional[int]:
    """Return repository ID if registered."""
    with get_db_connection() as db:
        result = db.run(
            '?[id] := *repositories{id, experiment_id, repo_name: rn},'
            ' experiment_id = $eid, rn = $name',
            {'eid': experiment_id, 'name': repo_name},
        )
        return result['rows'][0][0] if result['rows'] else None


def upsert_context_metadata(
    experiment_id: str,
    repo_name: str,
    key: str,
    value: str,
    *,
    namespace: str = "phase2",
    source: str = "phase2_context_summary",
):
    """Store structured context metadata for Phase 2 discoveries."""
    repo_id = ensure_repository_entry(experiment_id, repo_name)
    with get_db_connection() as db:
        existing = db.run(
            '?[id] := *context_metadata{id, experiment_id, repo_id, namespace: ns, key: k},'
            ' experiment_id = $eid, repo_id = $rid, ns = $ns, k = $k',
            {'eid': experiment_id, 'rid': repo_id, 'ns': namespace, 'k': key},
        )
        if existing['rows']:
            db.update('context_metadata', [{
                'id': existing['rows'][0][0],
                'value': value,
                'source': source,
                'created_at': _now(),
            }])
        else:
            new_id = _next_id(db, 'context_metadata')
            db.put('context_metadata', [{
                'id': new_id,
                'experiment_id': experiment_id,
                'repo_id': repo_id,
                'namespace': namespace,
                'key': key,
                'value': value,
                'source': source,
                'created_at': _now(),
            }])


# ============================================================================
# RESOURCE OPERATIONS
# ============================================================================

def insert_resource(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    resource_type: str,
    provider: str,
    source_file: str,
    source_line: Optional[int] = None,
    source_line_end: Optional[int] = None,
    parent_resource_id: Optional[int] = None,
    properties: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert resource with optional line numbers and parent relationship."""
    with get_db_connection() as db:
        repo_result = db.run(
            '?[id] := *repositories{id, experiment_id, repo_name: rn},'
            ' experiment_id = $eid, rn = $name',
            {'eid': experiment_id, 'name': repo_name},
        )
        if not repo_result['rows']:
            raise ValueError(
                f"Repository {repo_name} not registered in experiment {experiment_id}"
            )
        repo_id = repo_result['rows'][0][0]

        existing = db.run(
            '?[id] := *resources{id, experiment_id, repo_id: rid,'
            ' resource_type: rtype, resource_name: rname},'
            ' experiment_id = $eid, rid = $rid, rtype = $rtype, rname = $rname',
            {'eid': experiment_id, 'rid': repo_id,
             'rtype': resource_type, 'rname': resource_name},
        )
        if existing['rows']:
            resource_id = existing['rows'][0][0]
        else:
            resource_id = _next_id(db, 'resources')

        now = _now()
        db.put('resources', [{
            'id': resource_id,
            'experiment_id': experiment_id,
            'repo_id': repo_id,
            'resource_name': resource_name,
            'resource_type': resource_type,
            'provider': provider,
            'region': None,
            'parent_resource_id': parent_resource_id,
            'discovered_by': 'ContextDiscoveryAgent',
            'discovery_method': 'Terraform',
            'source_file': source_file,
            'source_line_start': source_line,
            'source_line_end': source_line_end,
            'status': 'active',
            'first_seen': now,
            'last_seen': now,
            'display_label': None,
            'tags': None,
        }])

        if properties:
            insert_resource_properties(resource_id, properties)

        return resource_id


def insert_resource_properties(resource_id: int, properties: Dict[str, str]) -> None:
    """Upsert a batch of resource properties."""
    with get_db_connection() as db:
        for key, value in properties.items():
            existing = db.run(
                '?[id] := *resource_properties{id, resource_id, property_key: k},'
                ' resource_id = $rid, k = $k',
                {'rid': resource_id, 'k': key},
            )
            if existing['rows']:
                db.update('resource_properties', [{
                    'id': existing['rows'][0][0],
                    'property_value': str(value),
                }])
            else:
                new_id = _next_id(db, 'resource_properties')
                db.put('resource_properties', [{
                    'id': new_id,
                    'resource_id': resource_id,
                    'property_key': key,
                    'property_value': str(value),
                    'property_type': _infer_property_type(key),
                    'is_security_relevant': _is_security_relevant(key),
                }])


def get_resource_id(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    resource_type: Optional[str] = None,
) -> Optional[int]:
    """Get resource ID by name (and optionally type) for parent relationship resolution."""
    with get_db_connection() as db:
        if resource_type:
            result = db.run(
                """
                ?[id] :=
                    *resources{id, experiment_id, resource_name: rname,
                               resource_type: rtype, repo_id},
                    experiment_id = $eid, rname = $rname, rtype = $rtype,
                    *repositories{id: repo_id, repo_name: rn},
                    rn = $repo
                """,
                {'eid': experiment_id, 'repo': repo_name,
                 'rname': resource_name, 'rtype': resource_type},
            )
        else:
            result = db.run(
                """
                ?[id] :=
                    *resources{id, experiment_id, resource_name: rname, repo_id},
                    experiment_id = $eid, rname = $rname,
                    *repositories{id: repo_id, repo_name: rn},
                    rn = $repo
                """,
                {'eid': experiment_id, 'repo': repo_name, 'rname': resource_name},
            )
        return result['rows'][0][0] if result['rows'] else None


def update_resource_parent(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    parent_resource_id: int,
):
    """Update parent_resource_id for a resource (used in second pass)."""
    with get_db_connection() as db:
        result = db.run(
            """
            ?[id] :=
                *resources{id, experiment_id, resource_name: rname, repo_id},
                experiment_id = $eid, rname = $rname,
                *repositories{id: repo_id, repo_name: rn},
                rn = $repo
            """,
            {'eid': experiment_id, 'repo': repo_name, 'rname': resource_name},
        )
        for (resource_id,) in result['rows']:
            db.update('resources', [{'id': resource_id,
                                     'parent_resource_id': parent_resource_id}])


# ============================================================================
# CONNECTION OPERATIONS
# ============================================================================

def insert_connection(
    experiment_id: str,
    source_name: str,
    target_name: str,
    connection_type: str,
    protocol: Optional[str] = None,
    port: Optional[str] = None,
    authentication: Optional[str] = None,
    source_repo: Optional[str] = None,
    target_repo: Optional[str] = None,
    authorization: Optional[str] = None,
    auth_method: Optional[str] = None,
    is_encrypted: Optional[bool] = None,
    via_component: Optional[str] = None,
    notes: Optional[str] = None,
):
    """Insert or update a resource connection with cross-repo detection."""
    with get_db_connection() as db:
        if source_repo:
            src_result = db.run(
                """
                ?[src_id, src_repo_id] :=
                    *resources{id: src_id, resource_name: sname,
                               experiment_id: eid, repo_id: src_repo_id},
                    sname = $sname, eid = $eid,
                    *repositories{id: src_repo_id, repo_name: srepo},
                    srepo = $srepo
                """,
                {'sname': source_name, 'eid': experiment_id, 'srepo': source_repo},
            )
        else:
            src_result = db.run(
                '?[src_id, src_repo_id] :='
                ' *resources{id: src_id, resource_name: sname,'
                ' experiment_id: eid, repo_id: src_repo_id},'
                ' sname = $sname, eid = $eid',
                {'sname': source_name, 'eid': experiment_id},
            )

        if target_repo:
            tgt_result = db.run(
                """
                ?[tgt_id, tgt_repo_id] :=
                    *resources{id: tgt_id, resource_name: tname,
                               experiment_id: eid, repo_id: tgt_repo_id},
                    tname = $tname, eid = $eid,
                    *repositories{id: tgt_repo_id, repo_name: trepo},
                    trepo = $trepo
                """,
                {'tname': target_name, 'eid': experiment_id, 'trepo': target_repo},
            )
        else:
            tgt_result = db.run(
                '?[tgt_id, tgt_repo_id] :='
                ' *resources{id: tgt_id, resource_name: tname,'
                ' experiment_id: eid, repo_id: tgt_repo_id},'
                ' tname = $tname, eid = $eid',
                {'tname': target_name, 'eid': experiment_id},
            )

        if not src_result['rows'] or not tgt_result['rows']:
            return None

        src_id, src_repo_id = src_result['rows'][0]
        tgt_id, tgt_repo_id = tgt_result['rows'][0]
        is_cross_repo = (src_repo_id != tgt_repo_id)
        effective_auth_method = auth_method or authentication
        effective_authentication = authentication or auth_method

        # Check for an existing connection with the same type
        existing_result = db.run(
            '?[conn_id, ct] :='
            ' *resource_connections{id: conn_id, experiment_id: eid,'
            ' source_resource_id: sid, target_resource_id: tid, connection_type: ct},'
            ' eid = $eid, sid = $src, tid = $tgt',
            {'eid': experiment_id, 'src': src_id, 'tgt': tgt_id},
        )
        ct_norm = connection_type or ''
        existing_id = None
        for row in _rows_to_dicts(existing_result):
            if (row['ct'] or '') == ct_norm:
                existing_id = row['conn_id']
                break

        if existing_id is not None:
            ef_result = db.run(
                '?[p, po, aut, auz, am, ie, vc, n] :='
                ' *resource_connections{id, protocol: p, port: po,'
                ' authentication: aut, authorization: auz, auth_method: am,'
                ' is_encrypted: ie, via_component: vc, notes: n},'
                ' id = $id',
                {'id': existing_id},
            )
            if ef_result['rows']:
                ep, epo, eaut, eauz, eam, eie, evc, en = ef_result['rows'][0]
                db.update('resource_connections', [{
                    'id': existing_id,
                    'source_repo_id': src_repo_id,
                    'target_repo_id': tgt_repo_id,
                    'is_cross_repo': is_cross_repo,
                    'protocol': protocol if protocol is not None else ep,
                    'port': port if port is not None else epo,
                    'authentication': (effective_authentication
                                       if effective_authentication is not None else eaut),
                    'authorization': authorization if authorization is not None else eauz,
                    'auth_method': (effective_auth_method
                                    if effective_auth_method is not None else eam),
                    'is_encrypted': is_encrypted if is_encrypted is not None else eie,
                    'via_component': via_component if via_component is not None else evc,
                    'notes': notes if notes is not None else en,
                }])
            return existing_id

        new_id = _next_id(db, 'resource_connections')
        db.put('resource_connections', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'source_resource_id': src_id,
            'target_resource_id': tgt_id,
            'source_repo_id': src_repo_id,
            'target_repo_id': tgt_repo_id,
            'is_cross_repo': is_cross_repo,
            'connection_type': connection_type,
            'protocol': protocol,
            'port': port,
            'authentication': effective_authentication,
            'authorization': authorization,
            'auth_method': effective_auth_method,
            'is_encrypted': is_encrypted,
            'via_component': via_component,
            'notes': notes,
        }])
        return new_id
    return None


# ============================================================================
# FINDING OPERATIONS
# ============================================================================

def insert_finding(
    experiment_id: str,
    repo_name: str,
    finding_name: str,
    resource_name: Optional[str],
    score: int,
    severity: str,
    category: str,
    evidence_location: str,
    discovered_by: str = "SecurityAgent",
    title: Optional[str] = None,
    description: Optional[str] = None,
    severity_score: Optional[int] = None,
    source_file: Optional[str] = None,
    source_line_start: Optional[int] = None,
    source_line_end: Optional[int] = None,
    code_snippet: Optional[str] = None,
    reason: Optional[str] = None,
    rule_id: Optional[str] = None,
    proposed_fix: Optional[str] = None,
) -> int:
    """Insert finding and return finding_id.

    Backward-compatible: old callers pass finding_name/score; new callers can
    also supply the enriched columns.  title falls back to finding_name;
    severity_score falls back to score.
    """
    effective_title = title if title is not None else finding_name
    effective_severity_score = severity_score if severity_score is not None else score

    with get_db_connection() as db:
        resource_id = None
        repo_id = None

        if resource_name:
            res_result = db.run(
                """
                ?[res_id, rid] :=
                    *resources{id: res_id, experiment_id: eid,
                               resource_name: rname, repo_id: rid},
                    eid = $eid, rname = $rname,
                    *repositories{id: rid, repo_name: rn},
                    rn = $repo
                """,
                {'eid': experiment_id, 'repo': repo_name, 'rname': resource_name},
            )
            if res_result['rows']:
                resource_id = res_result['rows'][0][0]
                repo_id = res_result['rows'][0][1]
            else:
                import warnings
                warnings.warn(
                    f"Resource '{resource_name}' not found in repo '{repo_name}' "
                    f"experiment '{experiment_id}' — inserting finding without resource link."
                )

        if repo_id is None:
            repo_result = db.run(
                '?[id] := *repositories{id, experiment_id: eid, repo_name: rn},'
                ' eid = $eid, rn = $repo',
                {'eid': experiment_id, 'repo': repo_name},
            )
            if repo_result['rows']:
                repo_id = repo_result['rows'][0][0]

        new_id = _next_id(db, 'findings')
        now = _now()
        db.put('findings', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'repo_id': repo_id,
            'resource_id': resource_id,
            'title': effective_title,
            'description': description,
            'category': category,
            'severity_score': effective_severity_score,
            'base_severity': severity,
            'overall_score': None,
            'evidence_location': evidence_location,
            'source_file': source_file,
            'source_line_start': source_line_start,
            'source_line_end': source_line_end,
            'finding_path': None,
            'detected_by': discovered_by,
            'detection_method': None,
            'status': 'open',
            'code_snippet': code_snippet,
            'reason': reason,
            'llm_enriched_at': None,
            'rule_id': rule_id,
            'proposed_fix': proposed_fix,
            'created_at': now,
            'updated_at': now,
        }])
        return new_id


def store_skeptic_review(
    finding_id: int,
    reviewer_type: str,
    score_adjustment: float,
    adjusted_score: float,
    confidence: float,
    reasoning: str,
    key_concerns: str = None,
    mitigating_factors: str = None,
    recommendation: str = 'confirm',
) -> int:
    """Insert or update a skeptic review for a finding. Returns review id."""
    with get_db_connection() as db:
        existing = db.run(
            '?[id] := *skeptic_reviews{id, finding_id, reviewer_type: rt},'
            ' finding_id = $fid, rt = $rt',
            {'fid': finding_id, 'rt': reviewer_type},
        )
        if existing['rows']:
            rid = existing['rows'][0][0]
            db.update('skeptic_reviews', [{
                'id': rid,
                'score_adjustment': score_adjustment,
                'adjusted_score': adjusted_score,
                'confidence': confidence,
                'reasoning': reasoning,
                'key_concerns': key_concerns,
                'mitigating_factors': mitigating_factors,
                'recommendation': recommendation,
                'reviewed_at': _now(),
            }])
            return rid

        new_id = _next_id(db, 'skeptic_reviews')
        db.put('skeptic_reviews', [{
            'id': new_id,
            'finding_id': finding_id,
            'reviewer_type': reviewer_type,
            'score_adjustment': score_adjustment,
            'adjusted_score': adjusted_score,
            'confidence': confidence,
            'reasoning': reasoning,
            'key_concerns': key_concerns,
            'mitigating_factors': mitigating_factors,
            'recommendation': recommendation,
            'reviewed_at': _now(),
        }])
        return new_id


def record_risk_score(
    finding_id: int,
    score: float,
    scored_by: str,
    rationale: str = None,
) -> int:
    """Append a risk score snapshot to risk_score_history. Returns history row id."""
    with get_db_connection() as db:
        new_id = _next_id(db, 'risk_score_history')
        db.put('risk_score_history', [{
            'id': new_id,
            'finding_id': finding_id,
            'score': score,
            'scored_by': scored_by,
            'rationale': rationale,
            'created_at': _now(),
        }])
        return new_id


def store_remediation(
    finding_id: int,
    title: str,
    description: str = None,
    remediation_type: str = 'config',
    effort: str = 'medium',
    priority: int = 2,
    code_fix: str = None,
    reference_url: str = None,
) -> int:
    """Insert or update a remediation for a finding. Returns remediation id."""
    with get_db_connection() as db:
        existing = db.run(
            '?[id] := *remediations{id, finding_id, title: t},'
            ' finding_id = $fid, t = $title',
            {'fid': finding_id, 'title': title},
        )
        if existing['rows']:
            rid = existing['rows'][0][0]
            db.update('remediations', [{
                'id': rid,
                'description': description,
                'remediation_type': remediation_type,
                'effort': effort,
                'priority': priority,
                'code_fix': code_fix,
                'reference_url': reference_url,
            }])
            return rid

        new_id = _next_id(db, 'remediations')
        db.put('remediations', [{
            'id': new_id,
            'finding_id': finding_id,
            'title': title,
            'description': description,
            'remediation_type': remediation_type,
            'effort': effort,
            'priority': priority,
            'code_fix': code_fix,
            'reference_url': reference_url,
        }])
        return new_id


def insert_trust_boundary(
    experiment_id: str,
    name: str,
    boundary_type: str,
    provider: str = None,
    region: str = None,
    description: str = None,
) -> int:
    """Insert or return existing trust boundary id."""
    with get_db_connection() as db:
        existing = db.run(
            '?[id] := *trust_boundaries{id, experiment_id, name: n},'
            ' experiment_id = $eid, n = $name',
            {'eid': experiment_id, 'name': name},
        )
        if existing['rows']:
            return existing['rows'][0][0]

        new_id = _next_id(db, 'trust_boundaries')
        db.put('trust_boundaries', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'name': name,
            'boundary_type': boundary_type,
            'provider': provider,
            'region': region,
            'description': description,
            'notes': None,
            'created_at': _now(),
        }])
        return new_id


def add_resource_to_trust_boundary(trust_boundary_id: int, resource_id: int):
    """Add a resource to a trust boundary (idempotent)."""
    with get_db_connection() as db:
        existing = db.run(
            '?[tb_id, r_id] :='
            ' *trust_boundary_members{trust_boundary_id: tb_id, resource_id: r_id},'
            ' tb_id = $tbid, r_id = $rid',
            {'tbid': trust_boundary_id, 'rid': resource_id},
        )
        if not existing['rows']:
            db.put('trust_boundary_members', [{
                'trust_boundary_id': trust_boundary_id,
                'resource_id': resource_id,
            }])


def insert_data_flow(
    experiment_id: str,
    name: str,
    flow_type: str,
    description: str = None,
) -> int:
    """Insert a data flow and return its id."""
    with get_db_connection() as db:
        new_id = _next_id(db, 'data_flows')
        db.put('data_flows', [{
            'id': new_id,
            'experiment_id': experiment_id,
            'name': name,
            'flow_type': flow_type,
            'description': description,
            'notes': None,
            'created_at': _now(),
        }])
        return new_id


def add_data_flow_step(
    flow_id: int,
    step_order: int,
    component_label: str,
    resource_id: int = None,
    protocol: str = None,
    port: str = None,
    auth_method: str = None,
    is_encrypted: bool = None,
    notes: str = None,
) -> int:
    """Add a step to a data flow. Returns step id."""
    with get_db_connection() as db:
        new_id = _next_id(db, 'data_flow_steps')
        db.put('data_flow_steps', [{
            'id': new_id,
            'flow_id': flow_id,
            'step_order': step_order,
            'component_label': component_label,
            'resource_id': resource_id,
            'protocol': protocol,
            'port': port,
            'auth_method': auth_method,
            'is_encrypted': is_encrypted,
            'notes': notes,
        }])
        return new_id


# ============================================================================
# CONTEXT OPERATIONS
# ============================================================================

def insert_context_answer(
    experiment_id: str,
    question_key: str,
    answer_value: str,
    evidence_source: str,
    confidence: str = 'confirmed',
    answered_by: str = 'ContextDiscoveryAgent',
    question_text: Optional[str] = None,
    question_category: str = 'General',
    evidence_type: str = 'code',
) -> int:
    """Record context answer and return inserted context_answers.id."""
    with get_db_connection() as db:
        return _insert_context_answer_with_conn(
            db,
            experiment_id=experiment_id,
            question_key=question_key,
            answer_value=answer_value,
            evidence_source=evidence_source,
            confidence=confidence,
            answered_by=answered_by,
            question_text=question_text,
            question_category=question_category,
            evidence_type=evidence_type,
        )


def _upsert_context_question(
    db: Client,
    *,
    question_key: str,
    question_text: Optional[str] = None,
    question_category: str = 'General',
) -> int:
    """Return context_questions.id, creating the question if it does not exist."""
    existing = db.run(
        '?[id] := *context_questions{id, question_key: k}, k = $k',
        {'k': question_key},
    )
    if existing['rows']:
        return int(existing['rows'][0][0])

    resolved_text = question_text or question_key.replace('_', ' ').title()
    new_id = _next_id(db, 'context_questions')
    db.put('context_questions', [{
        'id': new_id,
        'question_key': question_key,
        'question_text': resolved_text,
        'question_category': question_category,
    }])
    return new_id


def _insert_context_answer_with_conn(
    db: Client,
    *,
    experiment_id: str,
    question_key: str,
    answer_value: str,
    evidence_source: str,
    confidence: str = 'confirmed',
    answered_by: str = 'ContextDiscoveryAgent',
    question_text: Optional[str] = None,
    question_category: str = 'General',
    evidence_type: str = 'code',
) -> int:
    """Insert a context answer using an existing Client."""
    question_id = _upsert_context_question(
        db,
        question_key=question_key,
        question_text=question_text,
        question_category=question_category,
    )
    new_id = _next_id(db, 'context_answers')
    db.put('context_answers', [{
        'id': new_id,
        'experiment_id': experiment_id,
        'question_id': question_id,
        'answer_value': answer_value,
        'answer_confidence': confidence,
        'evidence_source': evidence_source,
        'evidence_type': evidence_type,
        'answered_by': answered_by,
        'answered_at': _now(),
    }])
    return new_id


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _infer_property_type(key: str) -> str:
    """Infer property type from key name."""
    security_keywords = ['public', 'firewall', 'encryption', 'tls', 'auth', 'access', 'rbac']
    network_keywords = ['subnet', 'vnet', 'ip', 'port', 'protocol']
    identity_keywords = ['identity', 'principal', 'role', 'permission']

    key_lower = key.lower()

    if any(k in key_lower for k in security_keywords):
        return 'security'
    elif any(k in key_lower for k in network_keywords):
        return 'network'
    elif any(k in key_lower for k in identity_keywords):
        return 'identity'
    else:
        return 'configuration'


def _is_security_relevant(key: str) -> bool:
    """Determine if property is security-relevant."""
    security_keywords = [
        'public', 'firewall', 'encryption', 'tls', 'ssl', 'auth', 'access',
        'rbac', 'identity', 'role', 'permission', 'security', 'audit',
        'logging', 'monitoring', 'vulnerability', 'exposed', 'open'
    ]
    key_lower = key.lower()
    return any(k in key_lower for k in security_keywords)


def format_source_location(
    source_file: str,
    start_line: Optional[int],
    end_line: Optional[int],
) -> str:
    """Format source location for display."""
    if start_line:
        if end_line and end_line != start_line:
            return f"{source_file}:{start_line}-{end_line}"
        else:
            return f"{source_file}:{start_line}"
    else:
        return source_file


# ============================================================================
# QUERY HELPERS
# ============================================================================

def _maybe_parse_json(val: Optional[str]):
    if val is None:
        return None
    try:
        return json.loads(val)
    except Exception:
        return val


def _prop_bool(val):
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).lower()
    return s in ('1', 'true', 'yes', 'y', 't')


def get_resources_for_diagram(experiment_id: str) -> List[Dict]:
    """Get all resources with properties merged into a canonical dict."""
    with get_db_connection() as db:
        res_result = db.run(
            """
            ?[id, resource_name, resource_type, provider, repo_name] :=
                *resources{id, experiment_id, repo_id, resource_name, resource_type, provider},
                experiment_id = $eid,
                *repositories{id: repo_id, repo_name}
            """,
            {'eid': experiment_id},
        )

        resources = []
        for row in _rows_to_dicts(res_result):
            rid = row['id']

            score_result = db.run(
                '?[max(s)] := *findings{resource_id, severity_score: s},'
                ' resource_id = $rid',
                {'rid': rid},
            )
            max_score = 0
            if score_result['rows'] and score_result['rows'][0][0] is not None:
                max_score = score_result['rows'][0][0]

            props_result = db.run(
                '?[k, v] :='
                ' *resource_properties{resource_id, property_key: k, property_value: v},'
                ' resource_id = $rid',
                {'rid': rid},
            )
            prop_dict = {r[0]: _maybe_parse_json(r[1]) for r in props_result['rows']}

            resources.append({
                'id': rid,
                'resource_name': row['resource_name'],
                'resource_type': row['resource_type'],
                'provider': row['provider'],
                'repo_name': row['repo_name'],
                'max_finding_score': max_score,
                'properties': prop_dict,
                'public': _prop_bool(
                    prop_dict.get('public')
                    or prop_dict.get('public_access')
                    or prop_dict.get('public', False)
                ),
                'public_reason': prop_dict.get('public_reason') or prop_dict.get('notes') or '',
                'network_acls': _maybe_parse_json(prop_dict.get('network_acls')),
                'firewall_rules': _maybe_parse_json(prop_dict.get('firewall_rules')) or [],
            })

        resources.sort(key=lambda r: (r.get('resource_type', ''), r.get('resource_name', '')))
        return resources


def get_connections_for_diagram(
    experiment_id: str,
    repo_name: Optional[str] = None,
) -> List[Dict]:
    """Get connections for diagram generation, optionally scoped to a repository."""
    with get_db_connection() as db:
        result = db.run(
            """
            ?[conn_id, source, source_type, target, target_type,
              connection_type, protocol, port, am, auth,
              is_encrypted, via_component, notes, is_cross_repo,
              source_repo, target_repo] :=
                *resource_connections{
                    id: conn_id, experiment_id: eid,
                    source_resource_id: src_id, target_resource_id: tgt_id,
                    connection_type, protocol, port,
                    authentication: auth, auth_method: am,
                    is_encrypted, via_component, notes, is_cross_repo
                },
                eid = $eid,
                *resources{id: src_id, resource_name: source,
                           resource_type: source_type, repo_id: src_repo_id},
                *resources{id: tgt_id, resource_name: target,
                           resource_type: target_type, repo_id: tgt_repo_id},
                *repositories{id: src_repo_id, repo_name: source_repo},
                *repositories{id: tgt_repo_id, repo_name: target_repo}
            """,
            {'eid': experiment_id},
        )

        conn_rows = []
        for r in _rows_to_dicts(result):
            am = r.pop('am')
            auth = r.pop('auth')
            r['auth_method'] = am if am is not None else auth
            conn_id = r.pop('conn_id')
            if repo_name and r['source_repo'] != repo_name and r['target_repo'] != repo_name:
                continue
            conn_rows.append((conn_id, r))

        conn_rows.sort(key=lambda x: x[0])
        return [r for _, r in conn_rows]


def get_resource_query_view(
    experiment_id: str,
    resource_name: str,
    repo_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return parent/child/ingress/egress/related view for a resource."""
    with get_db_connection() as db:
        if repo_name:
            res_result = db.run(
                """
                ?[id, resource_name, resource_type, repo_name, parent_resource_id] :=
                    *resources{id, experiment_id, resource_name,
                               resource_type, repo_id, parent_resource_id},
                    experiment_id = $eid, resource_name = $rname,
                    *repositories{id: repo_id, repo_name},
                    repo_name = $repo
                """,
                {'eid': experiment_id, 'rname': resource_name, 'repo': repo_name},
            )
        else:
            res_result = db.run(
                """
                ?[id, resource_name, resource_type, repo_name, parent_resource_id] :=
                    *resources{id, experiment_id, resource_name,
                               resource_type, repo_id, parent_resource_id},
                    experiment_id = $eid, resource_name = $rname,
                    *repositories{id: repo_id, repo_name}
                """,
                {'eid': experiment_id, 'rname': resource_name},
            )

        if not res_result['rows']:
            return None

        res_rows = sorted(_rows_to_dicts(res_result), key=lambda x: x['id'])
        resource_row = res_rows[0]
        resource_id = resource_row['id']
        owning_repo = resource_row['repo_name']
        parent_resource_id = resource_row['parent_resource_id']

        # Resolve optional parent
        parent = None
        if parent_resource_id is not None:
            p_result = db.run(
                '?[pname, ptype] :='
                ' *resources{id, resource_name: pname, resource_type: ptype},'
                ' id = $pid',
                {'pid': parent_resource_id},
            )
            if p_result['rows']:
                parent = {
                    'name': p_result['rows'][0][0],
                    'type': p_result['rows'][0][1],
                }

        # Children
        children_result = db.run(
            '?[resource_name, resource_type] :='
            ' *resources{id, resource_name, resource_type, parent_resource_id},'
            ' parent_resource_id = $pid',
            {'pid': resource_id},
        )
        children = sorted(
            _rows_to_dicts(children_result),
            key=lambda x: x.get('resource_name', ''),
        )

        # Ingress connections
        ingress_result = db.run(
            """
            ?[from_resource, from_type, from_repo, connection_type,
              protocol, port, am, auth, is_encrypted, via_component, notes] :=
                *resource_connections{
                    id, experiment_id: eid,
                    source_resource_id: src_id, target_resource_id: tgt_id,
                    connection_type, protocol, port,
                    authentication: auth, auth_method: am,
                    is_encrypted, via_component, notes
                },
                eid = $eid, tgt_id = $rid,
                *resources{id: src_id, resource_name: from_resource,
                           resource_type: from_type, repo_id: src_repo_id},
                *repositories{id: src_repo_id, repo_name: from_repo}
            """,
            {'eid': experiment_id, 'rid': resource_id},
        )
        ingress = []
        for r in sorted(_rows_to_dicts(ingress_result),
                        key=lambda x: x.get('from_resource', '')):
            am = r.pop('am')
            auth = r.pop('auth')
            r['auth_method'] = am if am is not None else auth
            ingress.append(r)

        # Egress connections
        egress_result = db.run(
            """
            ?[to_resource, to_type, to_repo, connection_type,
              protocol, port, am, auth, is_encrypted, via_component, notes] :=
                *resource_connections{
                    id, experiment_id: eid,
                    source_resource_id: src_id, target_resource_id: tgt_id,
                    connection_type, protocol, port,
                    authentication: auth, auth_method: am,
                    is_encrypted, via_component, notes
                },
                eid = $eid, src_id = $rid,
                *resources{id: tgt_id, resource_name: to_resource,
                           resource_type: to_type, repo_id: tgt_repo_id},
                *repositories{id: tgt_repo_id, repo_name: to_repo}
            """,
            {'eid': experiment_id, 'rid': resource_id},
        )
        egress = []
        for r in sorted(_rows_to_dicts(egress_result),
                        key=lambda x: x.get('to_resource', '')):
            am = r.pop('am')
            auth = r.pop('auth')
            r['auth_method'] = am if am is not None else auth
            egress.append(r)

        # Pending assumptions linked to this resource's nodes
        target_names = [resource_name, f'__inferred__{resource_name.lower()}']
        pending_assumptions: list[dict] = []
        seen_eq_ids: set[int] = set()
        conf_rank = {'high': 3, 'medium': 2, 'low': 1}

        for tname in target_names:
            node_result = db.run(
                '?[node_id, node_source_repo, node_aliases] :='
                ' *resource_nodes{id: node_id, terraform_name,'
                ' source_repo: node_source_repo, aliases: node_aliases},'
                ' terraform_name = $tn',
                {'tn': tname},
            )
            for nrow in _rows_to_dicts(node_result):
                node_id = nrow['node_id']
                node_source_repo = nrow['node_source_repo']
                node_aliases_raw = nrow['node_aliases']
                try:
                    aliases = json.loads(node_aliases_raw) if node_aliases_raw else []
                except Exception:
                    aliases = []
                if node_source_repo != owning_repo and owning_repo not in aliases:
                    continue

                eq_result = db.run(
                    """
                    ?[eq_id, gap_type, context, assumption_text,
                      confidence, suggested_value, created_at] :=
                        *enrichment_queue{
                            id: eq_id, resource_node_id, gap_type, context,
                            assumption_text, confidence, suggested_value,
                            status, created_at
                        },
                        resource_node_id = $nid, status = "pending_review"
                    """,
                    {'nid': node_id},
                )
                for eq in _rows_to_dicts(eq_result):
                    if eq['eq_id'] not in seen_eq_ids:
                        seen_eq_ids.add(eq['eq_id'])
                        pending_assumptions.append(eq)

        pending_assumptions.sort(key=lambda a: (
            -conf_rank.get(a.get('confidence', ''), 0),
            a.get('created_at') or '',
        ))
        for a in pending_assumptions:
            a['id'] = a.pop('eq_id')
            a.pop('created_at', None)

        # Build related list
        related: list[dict[str, Any]] = []
        for row in ingress:
            related.append({
                'resource': row['from_resource'],
                'resource_type': row['from_type'],
                'repo': row['from_repo'],
                'direction': 'ingress',
                'connection_type': row['connection_type'],
            })
        for row in egress:
            related.append({
                'resource': row['to_resource'],
                'resource_type': row['to_type'],
                'repo': row['to_repo'],
                'direction': 'egress',
                'connection_type': row['connection_type'],
            })

        return {
            'resource': {
                'name': resource_row['resource_name'],
                'type': resource_row['resource_type'],
                'repo': owning_repo,
            },
            'parent': parent,
            'children': children,
            'ingress': ingress,
            'egress': egress,
            'related': related,
            'pending_assumptions': pending_assumptions,
        }


# ============================================================================
# ENRICHMENT QUEUE HELPERS
# ============================================================================

def _normalize_queue_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "all":
        return normalized
    if normalized not in ENRICHMENT_QUEUE_STATUSES:
        valid = ", ".join(sorted(ENRICHMENT_QUEUE_STATUSES | {"all"}))
        raise ValueError(
            f"Invalid enrichment_queue status '{status}'. Expected one of: {valid}"
        )
    return normalized


def _normalize_enrichment_decision(decision: str) -> str:
    normalized = (decision or "").strip().lower()
    resolved = ENRICHMENT_DECISION_MAP.get(normalized)
    if not resolved:
        valid = ", ".join(sorted(ENRICHMENT_DECISION_MAP))
        raise ValueError(
            f"Invalid decision '{decision}'. Expected one of: {valid}"
        )
    return resolved


def _load_repo_aliases(raw_aliases: Optional[str], *, field_name: str) -> list[str]:
    if not raw_aliases:
        return []
    try:
        parsed = json.loads(raw_aliases)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid aliases JSON in {field_name}: {raw_aliases}"
        ) from exc
    if not isinstance(parsed, list):
        raise ValueError(
            f"Expected aliases list in {field_name}, got: {type(parsed).__name__}"
        )
    aliases: list[str] = []
    for alias in parsed:
        if alias is None:
            continue
        alias_text = str(alias).strip()
        if alias_text:
            aliases.append(alias_text)
    return aliases


def _list_experiment_repos(db: Client, experiment_id: str) -> list[str]:
    result = db.run(
        '?[repo_name] := *repositories{repo_name, experiment_id},'
        ' experiment_id = $eid',
        {'eid': experiment_id},
    )
    repo_names = sorted([str(row[0]) for row in result['rows'] if row[0]])
    if not repo_names:
        raise ValueError(f"No repositories found for experiment '{experiment_id}'.")
    return repo_names


def _fetch_enrichment_rows(
    db: Client,
    *,
    status: str = "pending_review",
    assumption_id: Optional[int] = None,
) -> list[dict]:
    """Fetch enrichment_queue rows with joined node/relationship data."""
    normalized_status = _normalize_queue_status(status)
    params: dict[str, Any] = {}
    clauses: list[str] = []

    if normalized_status != "all":
        clauses.append("status = $status")
        params["status"] = normalized_status
    if assumption_id is not None:
        clauses.append("id = $aid")
        params["aid"] = assumption_id

    where_clause = (", " + ", ".join(clauses)) if clauses else ""

    eq_result = _rows_to_dicts(db.run(
        f"""
        ?[id, resource_node_id, relationship_id, gap_type, context,
          assumption_text, assumption_basis, confidence, suggested_value,
          status, resolved_by, resolved_at, rejection_reason, created_at] :=
            *enrichment_queue{{
                id, resource_node_id, relationship_id, gap_type, context,
                assumption_text, assumption_basis, confidence, suggested_value,
                status, resolved_by, resolved_at, rejection_reason, created_at
            }}{where_clause}
        """,
        params,
    ))

    rows: list[dict] = []
    for eq in eq_result:
        row = dict(eq)

        row.update({
            'node_resource_type': None,
            'node_terraform_name': None,
            'node_source_repo': None,
            'node_aliases': None,
            'node_confidence': None,
        })
        if eq['resource_node_id'] is not None:
            node_r = db.run(
                '?[rt, tn, sr, al, conf] :='
                ' *resource_nodes{id, resource_type: rt, terraform_name: tn,'
                ' source_repo: sr, aliases: al, confidence: conf},'
                ' id = $id',
                {'id': eq['resource_node_id']},
            )
            if node_r['rows']:
                n = node_r['rows'][0]
                row.update({
                    'node_resource_type': n[0],
                    'node_terraform_name': n[1],
                    'node_source_repo': n[2],
                    'node_aliases': n[3],
                    'node_confidence': n[4],
                })

        row.update({
            'relationship_type': None,
            'relationship_confidence': None,
            'rel_source_resource_type': None,
            'rel_source_terraform_name': None,
            'rel_source_repo': None,
            'rel_source_aliases': None,
            'rel_target_resource_type': None,
            'rel_target_terraform_name': None,
            'rel_target_repo': None,
            'rel_target_aliases': None,
        })
        if eq['relationship_id'] is not None:
            rel_r = db.run(
                '?[rt, conf, src_id, tgt_id] :='
                ' *resource_relationships{id, relationship_type: rt,'
                ' confidence: conf, source_id: src_id, target_id: tgt_id},'
                ' id = $id',
                {'id': eq['relationship_id']},
            )
            if rel_r['rows']:
                rr = rel_r['rows'][0]
                row['relationship_type'] = rr[0]
                row['relationship_confidence'] = rr[1]
                for prefix, node_id in [('rel_source', rr[2]), ('rel_target', rr[3])]:
                    nr = db.run(
                        '?[rt, tn, sr, al] :='
                        ' *resource_nodes{id, resource_type: rt,'
                        ' terraform_name: tn, source_repo: sr, aliases: al},'
                        ' id = $id',
                        {'id': node_id},
                    )
                    if nr['rows']:
                        row[f'{prefix}_resource_type'] = nr['rows'][0][0]
                        row[f'{prefix}_terraform_name'] = nr['rows'][0][1]
                        row[f'{prefix}_repo'] = nr['rows'][0][2]
                        row[f'{prefix}_aliases'] = nr['rows'][0][3]

        rows.append(row)

    conf_rank = {'high': 3, 'medium': 2, 'low': 1}
    rows.sort(key=lambda r: (
        -conf_rank.get(r.get('confidence', ''), 0),
        r.get('created_at') or '',
    ))
    return rows


def _enrichment_assumption_question_key(assumption_id: int) -> str:
    return f"enrichment_queue_assumption_{assumption_id}_decision"


def _assumption_repo_scope(row: dict) -> set[str]:
    repos: set[str] = set()
    for key in ("node_source_repo", "rel_source_repo", "rel_target_repo"):
        value = row.get(key)
        if value:
            repos.add(str(value))
    for key in ("node_aliases", "rel_source_aliases", "rel_target_aliases"):
        repos.update(_load_repo_aliases(row.get(key), field_name=key))
    return repos


def _serialize_assumption_row(row: dict, repo_scope: set[str]) -> Dict[str, Any]:
    relationship_summary: Optional[str] = None
    if (row["relationship_type"]
            and row["rel_source_resource_type"]
            and row["rel_target_resource_type"]):
        relationship_summary = (
            f"{row['rel_source_resource_type']}.{row['rel_source_terraform_name']} "
            f"--[{row['relationship_type']}]--> "
            f"{row['rel_target_resource_type']}.{row['rel_target_terraform_name']}"
        )

    return {
        "id": row["id"],
        "resource_node_id": row["resource_node_id"],
        "relationship_id": row["relationship_id"],
        "gap_type": row["gap_type"],
        "context": row["context"],
        "assumption_text": row["assumption_text"],
        "assumption_basis": row["assumption_basis"],
        "confidence": row["confidence"],
        "suggested_value": row["suggested_value"],
        "status": row["status"],
        "resolved_by": row["resolved_by"],
        "resolved_at": row["resolved_at"],
        "rejection_reason": row["rejection_reason"],
        "created_at": row["created_at"],
        "node": {
            "resource_type": row["node_resource_type"],
            "terraform_name": row["node_terraform_name"],
            "source_repo": row["node_source_repo"],
            "confidence": row["node_confidence"],
        },
        "relationship": {
            "type": row["relationship_type"],
            "confidence": row["relationship_confidence"],
            "source": {
                "resource_type": row["rel_source_resource_type"],
                "terraform_name": row["rel_source_terraform_name"],
                "source_repo": row["rel_source_repo"],
            },
            "target": {
                "resource_type": row["rel_target_resource_type"],
                "terraform_name": row["rel_target_terraform_name"],
                "source_repo": row["rel_target_repo"],
            },
            "summary": relationship_summary,
        },
        "repo_scope": sorted(repo_scope),
        "question_key": _enrichment_assumption_question_key(int(row["id"])),
    }


def list_enrichment_assumptions(
    experiment_id: str,
    repo_name: Optional[str] = None,
    status: str = "pending_review",
) -> List[Dict[str, Any]]:
    """
    List enrichment queue assumptions scoped to an experiment and optional repo.

    Scope is derived from repositories registered to the experiment and matched
    against node source_repo + aliases.
    """
    with get_db_connection() as db:
        experiment_repos = _list_experiment_repos(db, experiment_id)
        repo_scope = set(experiment_repos)
        if repo_name and repo_name not in repo_scope:
            raise ValueError(
                f"Repository '{repo_name}' is not registered under"
                f" experiment '{experiment_id}'."
            )

        rows = _fetch_enrichment_rows(db, status=status)
        records: list[Dict[str, Any]] = []
        for row in rows:
            assumption_scope = _assumption_repo_scope(row)
            if not assumption_scope.intersection(repo_scope):
                continue
            if repo_name and repo_name not in assumption_scope:
                continue
            records.append(_serialize_assumption_row(row, assumption_scope))
        return records


def _apply_confirmation_confidence_updates(db: Client, row: dict) -> list[str]:
    updates: list[str] = []
    relationship_id = row.get("relationship_id")
    resource_node_id = row.get("resource_node_id")
    gap_type = (row.get("gap_type") or "").strip().lower()

    if relationship_id is not None:
        check = db.run(
            '?[conf] := *resource_relationships{id, confidence: conf}, id = $id',
            {'id': relationship_id},
        )
        if check['rows'] and check['rows'][0][0] != 'user_confirmed':
            db.update('resource_relationships', [{
                'id': relationship_id,
                'confidence': 'user_confirmed',
            }])
            updates.append(
                f"resource_relationships[{relationship_id}] confidence=user_confirmed"
            )

    if resource_node_id is not None and gap_type in {"cross_repo_link", "unknown_name"}:
        check = db.run(
            '?[conf] := *resource_nodes{id, confidence: conf}, id = $id',
            {'id': resource_node_id},
        )
        if check['rows'] and check['rows'][0][0] != 'user_confirmed':
            db.update('resource_nodes', [{
                'id': resource_node_id,
                'confidence': 'user_confirmed',
                'updated_at': _now(),
            }])
            updates.append(
                f"resource_nodes[{resource_node_id}] confidence=user_confirmed"
            )

        equiv_check = db.run(
            '?[id] := *resource_equivalences{id, resource_node_id: rni, evidence_level: el},'
            ' rni = $rid, el != "user_confirmed"',
            {'rid': resource_node_id},
        )
        count = len(equiv_check['rows'])
        if count:
            now = _now()
            for (eq_id,) in equiv_check['rows']:
                db.update('resource_equivalences', [{
                    'id': eq_id,
                    'evidence_level': 'user_confirmed',
                    'updated_at': now,
                }])
            updates.append(
                f"resource_equivalences[resource_node_id={resource_node_id}]"
                f" evidence_level=user_confirmed ({count} rows)"
            )

    return updates


def resolve_enrichment_assumption(
    experiment_id: str,
    assumption_id: int,
    decision: str,
    resolved_by: str,
    *,
    repo_name: Optional[str] = None,
    resolution_note: Optional[str] = None,
    evidence_source: str = "user_confirmation_cli",
) -> Dict[str, Any]:
    """
    Resolve a pending enrichment assumption and persist an auditable context answer.

    Confirmation upgrades graph confidence where an explicit rule exists.
    Rejections preserve existing graph confidence and record rejection reason.
    """
    normalized_decision = _normalize_enrichment_decision(decision)
    resolver = (resolved_by or "").strip()
    if not resolver:
        raise ValueError("resolved_by must be provided.")
    note = (resolution_note or "").strip()
    if normalized_decision == "rejected" and not note:
        raise ValueError(
            "A rejection requires --note explaining why the assumption was rejected."
        )
    if not evidence_source.strip():
        raise ValueError("evidence_source must be provided.")

    with get_db_connection() as db:
        experiment_repos = _list_experiment_repos(db, experiment_id)
        experiment_scope = set(experiment_repos)
        if repo_name and repo_name not in experiment_scope:
            raise ValueError(
                f"Repository '{repo_name}' is not registered under"
                f" experiment '{experiment_id}'."
            )

        rows = _fetch_enrichment_rows(db, status="all", assumption_id=assumption_id)
        if not rows:
            raise ValueError(
                f"Assumption id {assumption_id} was not found in enrichment_queue."
            )
        row = rows[0]

        assumption_scope = _assumption_repo_scope(row)
        if not assumption_scope.intersection(experiment_scope):
            raise ValueError(
                f"Assumption id {assumption_id} is not associated with"
                f" experiment '{experiment_id}'."
            )
        if repo_name and repo_name not in assumption_scope:
            raise ValueError(
                f"Assumption id {assumption_id} is outside"
                f" repository scope '{repo_name}'."
            )

        if row["status"] != "pending_review":
            raise ValueError(
                f"Assumption id {assumption_id} is already resolved"
                f" with status '{row['status']}'."
            )

        question_key = _enrichment_assumption_question_key(assumption_id)
        assumption_text = (
            row["assumption_text"] or row["context"] or f"Assumption #{assumption_id}"
        )
        answer_payload = json.dumps(
            {
                "assumption_id": assumption_id,
                "decision": normalized_decision,
                "note": note or None,
                "assumption_text": assumption_text,
                "resolver": resolver,
            },
            sort_keys=True,
        )
        answer_id = _insert_context_answer_with_conn(
            db,
            experiment_id=experiment_id,
            question_key=question_key,
            question_text=(
                f"Resolve enrichment assumption #{assumption_id}: {assumption_text}"
            ),
            question_category="EnrichmentQueue",
            answer_value=answer_payload,
            evidence_source=evidence_source,
            evidence_type="user_confirmation",
            confidence="confirmed",
            answered_by=resolver,
        )

        rejection_reason = note if normalized_decision == "rejected" else None
        db.update('enrichment_queue', [{
            'id': assumption_id,
            'status': normalized_decision,
            'resolved_by': resolver,
            'resolved_at': _now(),
            'rejection_reason': rejection_reason,
        }])

        # Verify the update applied correctly
        verify = db.run(
            '?[status] := *enrichment_queue{id, status}, id = $id',
            {'id': assumption_id},
        )
        if not verify['rows'] or verify['rows'][0][0] != normalized_decision:
            raise RuntimeError(
                f"Failed to resolve assumption id {assumption_id};"
                " status changed during update."
            )

        confidence_updates: list[str] = []
        if normalized_decision == "confirmed":
            confidence_updates = _apply_confirmation_confidence_updates(db, row)

        return {
            "assumption_id": assumption_id,
            "experiment_id": experiment_id,
            "repo_name": repo_name,
            "status": normalized_decision,
            "resolved_by": resolver,
            "resolution_note": note or None,
            "question_key": question_key,
            "context_answer_id": answer_id,
            "confidence_updates": confidence_updates,
        }


if __name__ == "__main__":
    print(f"Database path: {DB_PATH}")
    print(f"Database exists: {DB_PATH.exists()}")
