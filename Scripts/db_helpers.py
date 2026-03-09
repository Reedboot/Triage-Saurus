#!/usr/bin/env python3
"""Database helper functions for Triage-Saurus (CozoDB/SQLite engine backend)."""

import json
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Location ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage_cozo.db"

ENRICHMENT_QUEUE_STATUSES = {"pending_review", "confirmed", "rejected"}
ENRICHMENT_DECISION_MAP = {
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "reject": "rejected",
    "rejected": "rejected",
}
ENRICHMENT_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

_LINK_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_EVIDENCE_LEVEL_RANK = {"inferred": 1, "extracted": 2, "user_confirmed": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CozoSession ──────────────────────────────────────────────────────────────

class CozoSession:
    """Thin Cozo client wrapper yielded by get_db_connection()."""

    def __init__(self, client) -> None:
        self._client = client

    def run(self, script: str, params: Optional[dict] = None) -> dict:
        return self._client.run(script, params or {})

    def commit(self) -> None:
        pass  # CozoDB auto-commits each mutation

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


# ── Module-level cached client ───────────────────────────────────────────────
_CLIENT = None


def _get_client():
    """Return (and lazily initialise) the module-level Cozo client."""
    global _CLIENT
    if _CLIENT is None:
        try:
            from pycozo.client import Client
        except ImportError as exc:
            raise RuntimeError(
                "pycozo is required: pip install pycozo cozo-embedded"
            ) from exc
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CLIENT = Client("sqlite", str(DB_PATH), dataframe=False)
        from init_database import init_schema
        init_schema(_CLIENT)
    return _CLIENT


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    """Context manager that yields a CozoSession.

    db_path is accepted for backward compatibility but ignored;
    all data is written to DB_PATH (triage_cozo.db).
    """
    client = _get_client()
    session = CozoSession(client)
    try:
        yield session
    except Exception:
        raise


# ── Auto-increment helper ────────────────────────────────────────────────────

def _next_id(table_name: str) -> int:
    """Return the next auto-increment integer ID for *table_name*."""
    client = _get_client()
    result = client.run(
        "?[v] := *counters{tbl: $t, val: v}",
        {"t": table_name},
    )
    current = result["rows"][0][0] if result["rows"] else 0
    new_id = current + 1
    client.run(
        "?[tbl, val] <- [[$t, $v]] :put counters {tbl, val}",
        {"t": table_name, "v": new_id},
    )
    return new_id


# ── Topology backfills (declarative Datalog rules) ───────────────────────────

def apply_topology_backfills(conn=None) -> Dict[str, int]:
    """
    Apply topology backfills using Datalog :update rules (safe + idempotent).

    Replaces the previous procedural SQL-per-column approach with
    declarative Datalog queries.  conn is accepted for backward compatibility.
    """
    client = _get_client()
    updates: Dict[str, int] = {}

    def _run(label: str, script: str) -> None:
        result = client.run(script)
        rows = result.get("rows", [])
        updates[label] = 0 if (not rows or rows == [["OK"]]) else len(rows)

    # Backfill source_repo_id on resource_connections
    _run(
        "resource_connections_source_repo_id",
        """
        ?[connection_id, source_repo_id] :=
            *resource_connections{connection_id: connection_id,
                source_resource_id: src_id, source_repo_id: old_repo},
            is_null(old_repo),
            *resources{resource_id: src_id, repo_id: source_repo_id}
        :update resource_connections { connection_id, source_repo_id }
        """,
    )

    _run(
        "resource_connections_target_repo_id",
        """
        ?[connection_id, target_repo_id] :=
            *resource_connections{connection_id: connection_id,
                target_resource_id: tgt_id, target_repo_id: old_repo},
            is_null(old_repo),
            *resources{resource_id: tgt_id, repo_id: target_repo_id}
        :update resource_connections { connection_id, target_repo_id }
        """,
    )

    _run(
        "resource_connections_is_cross_repo",
        """
        ?[connection_id, is_cross_repo] :=
            *resource_connections{connection_id: connection_id,
                source_repo_id: src_repo, target_repo_id: tgt_repo},
            not is_null(src_repo), not is_null(tgt_repo),
            is_cross_repo = if(src_repo != tgt_repo, true, false)
        :update resource_connections { connection_id, is_cross_repo }
        """,
    )

    _run(
        "resource_connections_authentication",
        """
        needs_auth_update[cid] :=
            *resource_connections{connection_id: cid, authentication: old_auth},
            old_auth == ''
        needs_auth_update[cid] :=
            *resource_connections{connection_id: cid, authentication: old_auth},
            is_null(old_auth)

        ?[connection_id, authentication] :=
            needs_auth_update[connection_id],
            *resource_connections{connection_id: connection_id, auth_method: am},
            am != '', not is_null(am),
            authentication = am
        :update resource_connections { connection_id, authentication }
        """,
    )

    _run(
        "resource_connections_auth_method",
        """
        needs_am_update[cid] :=
            *resource_connections{connection_id: cid, auth_method: old_am},
            old_am == ''
        needs_am_update[cid] :=
            *resource_connections{connection_id: cid, auth_method: old_am},
            is_null(old_am)

        ?[connection_id, auth_method] :=
            needs_am_update[connection_id],
            *resource_connections{connection_id: connection_id, authentication: auth},
            auth != '', not is_null(auth),
            auth_method = auth
        :update resource_connections { connection_id, auth_method }
        """,
    )

    _run(
        "findings_repo_id",
        """
        ?[finding_id, repo_id] :=
            *findings{finding_id: finding_id, resource_id: res_id, repo_id: old_rid},
            is_null(old_rid), not is_null(res_id),
            *resources{resource_id: res_id, repo_id: repo_id}
        :update findings { finding_id, repo_id }
        """,
    )

    _run(
        "resource_nodes_aliases",
        """
        needs_aliases[nid] :=
            *resource_nodes{node_id: nid, aliases: a}, is_null(a)
        needs_aliases[nid] :=
            *resource_nodes{node_id: nid, aliases: a}, a == ''

        ?[node_id, aliases] :=
            needs_aliases[node_id], aliases = '[]'
        :update resource_nodes { node_id, aliases }
        """,
    )

    _run(
        "resource_nodes_confidence",
        """
        bad_conf[nid] :=
            *resource_nodes{node_id: nid, confidence: c}, is_null(c)
        bad_conf[nid] :=
            *resource_nodes{node_id: nid, confidence: c}, c == ''
        bad_conf[nid] :=
            *resource_nodes{node_id: nid, confidence: c},
            c != 'extracted', c != 'inferred', c != 'user_confirmed'

        ?[node_id, confidence] :=
            bad_conf[node_id], confidence = 'extracted'
        :update resource_nodes { node_id, confidence }
        """,
    )

    _run(
        "resource_nodes_properties",
        """
        needs_props[nid] :=
            *resource_nodes{node_id: nid, properties: p}, is_null(p)
        needs_props[nid] :=
            *resource_nodes{node_id: nid, properties: p}, p == ''

        ?[node_id, properties] :=
            needs_props[node_id], properties = '{}'
        :update resource_nodes { node_id, properties }
        """,
    )

    _run(
        "enrichment_queue_confidence",
        """
        bad_eq_conf[qid] :=
            *enrichment_queue{queue_id: qid, confidence: c}, is_null(c)
        bad_eq_conf[qid] :=
            *enrichment_queue{queue_id: qid, confidence: c}, c == ''
        bad_eq_conf[qid] :=
            *enrichment_queue{queue_id: qid, confidence: c},
            c != 'high', c != 'medium', c != 'low'

        ?[queue_id, confidence] :=
            bad_eq_conf[queue_id], confidence = 'medium'
        :update enrichment_queue { queue_id, confidence }
        """,
    )

    _run(
        "enrichment_queue_status",
        """
        bad_eq_status[qid] :=
            *enrichment_queue{queue_id: qid, status: s}, is_null(s)
        bad_eq_status[qid] :=
            *enrichment_queue{queue_id: qid, status: s}, s == ''
        bad_eq_status[qid] :=
            *enrichment_queue{queue_id: qid, status: s},
            s != 'pending_review', s != 'confirmed', s != 'rejected'

        ?[queue_id, status] :=
            bad_eq_status[queue_id], status = 'pending_review'
        :update enrichment_queue { queue_id, status }
        """,
    )

    return updates


# ============================================================================
# REPOSITORY OPERATIONS
# ============================================================================

def insert_repository(
    experiment_id: str,
    repo_path: Path,
    repo_type: str = "Infrastructure",
) -> Tuple[int, str]:
    """Register repository — store only folder name (portable)."""
    repo_name = repo_path.name

    repo_url = ""
    try:
        import git
        repo_obj = git.Repo(repo_path)
        if repo_obj.remotes:
            repo_url = repo_obj.remotes.origin.url
    except Exception:
        pass

    client = _get_client()
    existing = client.run(
        "?[repo_id] := *repositories{repo_id: repo_id, experiment_id: $eid, repo_name: $rn}",
        {"eid": experiment_id, "rn": repo_name},
    )
    if existing["rows"]:
        return existing["rows"][0][0], repo_name

    new_id = _next_id("repositories")
    client.run(
        """
        ?[repo_id, experiment_id, repo_name, repo_url, repo_type, scanned_at] <-
            [[$rid, $eid, $rn, $ru, $rt, $now]]
        :put repositories { repo_id, experiment_id, repo_name, repo_url, repo_type, scanned_at }
        """,
        {
            "rid": new_id, "eid": experiment_id, "rn": repo_name,
            "ru": repo_url, "rt": repo_type, "now": _now(),
        },
    )
    return new_id, repo_name


def update_repository_stats(
    experiment_id: str,
    repo_name: str,
    files_scanned: int,
    iac_files: int,
    code_files: int,
) -> None:
    """Update repository scan statistics."""
    client = _get_client()
    result = client.run(
        "?[repo_id] := *repositories{repo_id: repo_id, experiment_id: $eid, repo_name: $rn}",
        {"eid": experiment_id, "rn": repo_name},
    )
    if not result["rows"]:
        return
    repo_id = result["rows"][0][0]
    client.run(
        """
        ?[repo_id, files_scanned, iac_files_count, code_files_count] <- [[$rid, $fs, $iac, $code]]
        :update repositories { repo_id, files_scanned, iac_files_count, code_files_count }
        """,
        {"rid": repo_id, "fs": files_scanned, "iac": iac_files, "code": code_files},
    )


def ensure_repository_entry(experiment_id: str, repo_name: str) -> int:
    """Ensure a repository record exists; return its repo_id."""
    client = _get_client()
    existing = client.run(
        "?[repo_id] := *repositories{repo_id: repo_id, experiment_id: $eid, repo_name: $rn}",
        {"eid": experiment_id, "rn": repo_name},
    )
    if existing["rows"]:
        return existing["rows"][0][0]
    new_id = _next_id("repositories")
    client.run(
        "?[repo_id, experiment_id, repo_name, scanned_at] <- [[$rid, $eid, $rn, $now]] "
        ":put repositories { repo_id, experiment_id, repo_name, scanned_at }",
        {"rid": new_id, "eid": experiment_id, "rn": repo_name, "now": _now()},
    )
    return new_id


def get_repository_id(experiment_id: str, repo_name: str) -> Optional[int]:
    """Return repository ID if registered."""
    client = _get_client()
    result = client.run(
        "?[repo_id] := *repositories{repo_id: repo_id, experiment_id: $eid, repo_name: $rn}",
        {"eid": experiment_id, "rn": repo_name},
    )
    return result["rows"][0][0] if result["rows"] else None


def upsert_context_metadata(
    experiment_id: str,
    repo_name: str,
    key: str,
    value: str,
    *,
    namespace: str = "phase2",
    source: str = "phase2_context_summary",
) -> None:
    """Store structured context metadata for Phase 2 discoveries."""
    repo_id = ensure_repository_entry(experiment_id, repo_name)
    client = _get_client()

    existing = client.run(
        """
        ?[meta_id] :=
            *context_metadata{meta_id: meta_id, experiment_id: $eid,
                repo_id: $rid, namespace: $ns, key: $key}
        """,
        {"eid": experiment_id, "rid": repo_id, "ns": namespace, "key": key},
    )
    if existing["rows"]:
        mid = existing["rows"][0][0]
        client.run(
            "?[meta_id, value, source, created_at] <- [[$mid, $val, $src, $now]] "
            ":update context_metadata { meta_id, value, source, created_at }",
            {"mid": mid, "val": value, "src": source, "now": _now()},
        )
    else:
        mid = _next_id("context_metadata")
        client.run(
            """
            ?[meta_id, experiment_id, repo_id, namespace, key, value, source, created_at] <-
                [[$mid, $eid, $rid, $ns, $key, $val, $src, $now]]
            :put context_metadata {
                meta_id, experiment_id, repo_id, namespace, key, value, source, created_at
            }
            """,
            {
                "mid": mid, "eid": experiment_id, "rid": repo_id,
                "ns": namespace, "key": key, "val": value, "src": source, "now": _now(),
            },
        )


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
    client = _get_client()
    repo_id = get_repository_id(experiment_id, repo_name)
    if repo_id is None:
        raise ValueError(
            f"Repository {repo_name} not registered in experiment {experiment_id}"
        )

    existing = client.run(
        """
        ?[resource_id] :=
            *resources{resource_id: resource_id, experiment_id: $eid,
                repo_id: $rid, resource_name: $rn, resource_type: $rt}
        """,
        {"eid": experiment_id, "rid": repo_id, "rn": resource_name, "rt": resource_type},
    )
    if existing["rows"]:
        resource_id = existing["rows"][0][0]
    else:
        resource_id = _next_id("resources")
        now = _now()
        client.run(
            """
            ?[resource_id, experiment_id, repo_id, resource_name, resource_type,
              provider, source_file, source_line_start, source_line_end,
              parent_resource_id, discovered_by, discovery_method, first_seen, last_seen] <-
                [[$rid, $eid, $repoId, $rn, $rt, $prov, $sf, $sl, $sle, $parid,
                  'ContextDiscoveryAgent', 'Terraform', $now, $now]]
            :put resources {
                resource_id, experiment_id, repo_id, resource_name, resource_type,
                provider, source_file, source_line_start, source_line_end,
                parent_resource_id, discovered_by, discovery_method, first_seen, last_seen
            }
            """,
            {
                "rid": resource_id, "eid": experiment_id, "repoId": repo_id,
                "rn": resource_name, "rt": resource_type, "prov": provider or "",
                "sf": source_file or "", "sl": source_line, "sle": source_line_end,
                "parid": parent_resource_id, "now": now,
            },
        )

    if properties:
        for key, val in properties.items():
            _upsert_resource_property(resource_id, key, str(val))

    return resource_id


def _upsert_resource_property(resource_id: int, key: str, value: str) -> None:
    client = _get_client()
    existing = client.run(
        "?[property_id] := *resource_properties{property_id: property_id, resource_id: $rid, property_key: $k}",
        {"rid": resource_id, "k": key},
    )
    prop_type = _infer_property_type(key)
    is_sec = _is_security_relevant(key)
    if existing["rows"]:
        pid = existing["rows"][0][0]
        client.run(
            "?[property_id, property_value] <- [[$pid, $val]] "
            ":update resource_properties {property_id, property_value}",
            {"pid": pid, "val": value},
        )
    else:
        pid = _next_id("resource_properties")
        client.run(
            """
            ?[property_id, resource_id, property_key, property_value, property_type, is_security_relevant] <-
                [[$pid, $rid, $k, $val, $pt, $sec]]
            :put resource_properties {
                property_id, resource_id, property_key, property_value, property_type, is_security_relevant
            }
            """,
            {"pid": pid, "rid": resource_id, "k": key, "val": value, "pt": prop_type, "sec": is_sec},
        )


def get_resource_id(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    resource_type: Optional[str] = None,
) -> Optional[int]:
    """Get resource ID by name (and optionally type)."""
    client = _get_client()
    repo_id = get_repository_id(experiment_id, repo_name)
    if repo_id is None:
        return None

    if resource_type:
        result = client.run(
            """
            ?[resource_id] :=
                *resources{resource_id: resource_id, experiment_id: $eid,
                    repo_id: $rid, resource_name: $rn, resource_type: $rt}
            """,
            {"eid": experiment_id, "rid": repo_id, "rn": resource_name, "rt": resource_type},
        )
    else:
        result = client.run(
            """
            ?[resource_id] :=
                *resources{resource_id: resource_id, experiment_id: $eid,
                    repo_id: $rid, resource_name: $rn}
            """,
            {"eid": experiment_id, "rid": repo_id, "rn": resource_name},
        )
    return result["rows"][0][0] if result["rows"] else None


def update_resource_parent(
    experiment_id: str,
    repo_name: str,
    resource_name: str,
    parent_resource_id: int,
) -> None:
    """Update parent_resource_id for a resource."""
    resource_id = get_resource_id(experiment_id, repo_name, resource_name)
    if resource_id is None:
        return
    _get_client().run(
        "?[resource_id, parent_resource_id] <- [[$rid, $par]] "
        ":update resources { resource_id, parent_resource_id }",
        {"rid": resource_id, "par": parent_resource_id},
    )


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
) -> Optional[int]:
    """Insert or update a resource connection with cross-repo detection."""
    client = _get_client()

    def _find_resource(name: str, repo: Optional[str]) -> Optional[tuple]:
        if repo:
            repo_id = get_repository_id(experiment_id, repo)
            if repo_id is None:
                return None
            r = client.run(
                "?[resource_id, repo_id] := *resources{resource_id: resource_id, "
                "repo_id: repo_id, resource_name: $n, experiment_id: $eid}, repo_id = $rid",
                {"n": name, "eid": experiment_id, "rid": repo_id},
            )
        else:
            r = client.run(
                "?[resource_id, repo_id] := *resources{resource_id: resource_id, "
                "repo_id: repo_id, resource_name: $n, experiment_id: $eid}",
                {"n": name, "eid": experiment_id},
            )
        return tuple(r["rows"][0]) if r["rows"] else None

    src = _find_resource(source_name, source_repo)
    tgt = _find_resource(target_name, target_repo)
    if not src or not tgt:
        return None

    src_id, src_repo_id = src
    tgt_id, tgt_repo_id = tgt
    is_cross = src_repo_id != tgt_repo_id
    eff_auth = auth_method or authentication or ""
    eff_authentication = authentication or auth_method or ""

    existing = client.run(
        """
        ?[connection_id] :=
            *resource_connections{connection_id: connection_id,
                experiment_id: $eid, source_resource_id: $src,
                target_resource_id: $tgt, connection_type: $ct}
        """,
        {"eid": experiment_id, "src": src_id, "tgt": tgt_id, "ct": connection_type or ""},
    )

    if existing["rows"]:
        cid = existing["rows"][0][0]
        client.run(
            """
            ?[connection_id, source_repo_id, target_repo_id, is_cross_repo,
              protocol, port, authentication, authorization, auth_method,
              is_encrypted, via_component, notes] <-
              [[$cid, $sr, $tr, $cross, $proto, $prt, $auth, $authz, $am, $enc, $via, $notes]]
            :update resource_connections {
                connection_id, source_repo_id, target_repo_id, is_cross_repo,
                protocol, port, authentication, authorization, auth_method,
                is_encrypted, via_component, notes
            }
            """,
            {
                "cid": cid, "sr": src_repo_id, "tr": tgt_repo_id, "cross": is_cross,
                "proto": protocol or "", "prt": port or "", "auth": eff_authentication,
                "authz": authorization or "", "am": eff_auth, "enc": is_encrypted,
                "via": via_component or "", "notes": notes or "",
            },
        )
        return cid

    cid = _next_id("resource_connections")
    client.run(
        """
        ?[connection_id, experiment_id, source_resource_id, target_resource_id,
          source_repo_id, target_repo_id, is_cross_repo, connection_type,
          protocol, port, authentication, authorization, auth_method,
          is_encrypted, via_component, notes] <-
          [[$cid, $eid, $src, $tgt, $sr, $tr, $cross, $ct,
            $proto, $prt, $auth, $authz, $am, $enc, $via, $notes]]
        :put resource_connections {
            connection_id, experiment_id, source_resource_id, target_resource_id,
            source_repo_id, target_repo_id, is_cross_repo, connection_type,
            protocol, port, authentication, authorization, auth_method,
            is_encrypted, via_component, notes
        }
        """,
        {
            "cid": cid, "eid": experiment_id, "src": src_id, "tgt": tgt_id,
            "sr": src_repo_id, "tr": tgt_repo_id, "cross": is_cross,
            "ct": connection_type or "", "proto": protocol or "", "prt": port or "",
            "auth": eff_authentication, "authz": authorization or "", "am": eff_auth,
            "enc": is_encrypted, "via": via_component or "", "notes": notes or "",
        },
    )
    return cid


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
    """Insert finding and return finding_id."""
    effective_title = title if title is not None else finding_name
    effective_score = severity_score if severity_score is not None else score

    client = _get_client()
    resource_id: Optional[int] = None
    repo_id: Optional[int] = None

    if resource_name:
        res = client.run(
            """
            ?[resource_id, repo_id] :=
                *resources{resource_id: resource_id, repo_id: repo_id,
                    resource_name: $rn, experiment_id: $eid},
                *repositories{repo_id: repo_id, repo_name: $repo}
            """,
            {"rn": resource_name, "eid": experiment_id, "repo": repo_name},
        )
        if res["rows"]:
            resource_id, repo_id = res["rows"][0]
        else:
            warnings.warn(
                f"Resource '{resource_name}' not found in repo '{repo_name}' "
                f"experiment '{experiment_id}' — inserting finding without resource link."
            )

    if repo_id is None:
        repo_id = get_repository_id(experiment_id, repo_name)

    fid = _next_id("findings")
    now = _now()
    client.run(
        """
        ?[finding_id, experiment_id, repo_id, resource_id, title, description, category,
          severity_score, base_severity, evidence_location, source_file, source_line_start,
          source_line_end, detected_by, rule_id, proposed_fix, code_snippet, reason,
          created_at, updated_at] <-
          [[$fid, $eid, $rid, $resid, $title, $desc, $cat,
            $score, $sev, $evloc, $sf, $sl, $sle, $by, $rule, $fix, $snippet, $reason,
            $now, $now]]
        :put findings {
            finding_id, experiment_id, repo_id, resource_id, title, description, category,
            severity_score, base_severity, evidence_location, source_file, source_line_start,
            source_line_end, detected_by, rule_id, proposed_fix, code_snippet, reason,
            created_at, updated_at
        }
        """,
        {
            "fid": fid, "eid": experiment_id, "rid": repo_id, "resid": resource_id,
            "title": effective_title, "desc": description or "", "cat": category or "",
            "score": effective_score, "sev": severity or "", "evloc": evidence_location or "",
            "sf": source_file or "", "sl": source_line_start, "sle": source_line_end,
            "by": discovered_by or "", "rule": rule_id or "", "fix": proposed_fix or "",
            "snippet": code_snippet or "", "reason": reason or "", "now": now,
        },
    )
    return fid


def get_finding_ids_for_experiment(experiment_id: str) -> List[int]:
    """Return all finding IDs for an experiment."""
    client = _get_client()
    result = client.run(
        "?[finding_id] := *findings{finding_id: finding_id, experiment_id: $eid}",
        {"eid": experiment_id},
    )
    return sorted(row[0] for row in result["rows"])


def store_skeptic_review(
    finding_id: int,
    reviewer_type: str,
    score_adjustment: float,
    adjusted_score: float,
    confidence: float,
    reasoning: str,
    key_concerns: str = None,
    mitigating_factors: str = None,
    recommendation: str = "confirm",
) -> int:
    """Insert or update a skeptic review. Returns review id."""
    client = _get_client()
    existing = client.run(
        "?[review_id] := *skeptic_reviews{review_id: review_id, finding_id: $fid, reviewer_type: $rt}",
        {"fid": finding_id, "rt": reviewer_type},
    )
    now = _now()
    if existing["rows"]:
        rid = existing["rows"][0][0]
        client.run(
            """
            ?[review_id, score_adjustment, adjusted_score, confidence, reasoning,
              key_concerns, mitigating_factors, recommendation, reviewed_at] <-
              [[$rid, $sa, $as, $conf, $reasoning, $kc, $mf, $rec, $now]]
            :update skeptic_reviews {
                review_id, score_adjustment, adjusted_score, confidence, reasoning,
                key_concerns, mitigating_factors, recommendation, reviewed_at
            }
            """,
            {
                "rid": rid, "sa": score_adjustment, "as": adjusted_score,
                "conf": confidence, "reasoning": reasoning or "",
                "kc": key_concerns or "", "mf": mitigating_factors or "",
                "rec": recommendation, "now": now,
            },
        )
        return rid

    rid = _next_id("skeptic_reviews")
    client.run(
        """
        ?[review_id, finding_id, reviewer_type, score_adjustment, adjusted_score, confidence,
          reasoning, key_concerns, mitigating_factors, recommendation, reviewed_at] <-
          [[$rid, $fid, $rt, $sa, $as, $conf, $reasoning, $kc, $mf, $rec, $now]]
        :put skeptic_reviews {
            review_id, finding_id, reviewer_type, score_adjustment, adjusted_score, confidence,
            reasoning, key_concerns, mitigating_factors, recommendation, reviewed_at
        }
        """,
        {
            "rid": rid, "fid": finding_id, "rt": reviewer_type, "sa": score_adjustment,
            "as": adjusted_score, "conf": confidence, "reasoning": reasoning or "",
            "kc": key_concerns or "", "mf": mitigating_factors or "",
            "rec": recommendation, "now": now,
        },
    )
    return rid


def record_risk_score(finding_id: int, score: float, scored_by: str, rationale: str = None) -> int:
    client = _get_client()
    sid = _next_id("risk_score_history")
    client.run(
        "?[score_id, finding_id, score, scored_by, rationale, created_at] <- [[$sid, $fid, $score, $by, $rat, $now]] "
        ":put risk_score_history { score_id, finding_id, score, scored_by, rationale, created_at }",
        {"sid": sid, "fid": finding_id, "score": score, "by": scored_by or "",
         "rat": rationale or "", "now": _now()},
    )
    return sid


def store_remediation(
    finding_id: int, title: str, description: str = None, remediation_type: str = "config",
    effort: str = "medium", priority: int = 2, code_fix: str = None, reference_url: str = None,
) -> int:
    client = _get_client()
    existing = client.run(
        "?[remediation_id] := *remediations{remediation_id: remediation_id, finding_id: $fid, title: $t}",
        {"fid": finding_id, "t": title},
    )
    if existing["rows"]:
        rid = existing["rows"][0][0]
        client.run(
            "?[remediation_id, description, remediation_type, effort, priority, code_fix, reference_url] <- "
            "[[$rid, $desc, $rt, $effort, $pri, $fix, $url]] "
            ":update remediations {remediation_id, description, remediation_type, effort, priority, code_fix, reference_url}",
            {"rid": rid, "desc": description or "", "rt": remediation_type, "effort": effort,
             "pri": priority, "fix": code_fix or "", "url": reference_url or ""},
        )
        return rid

    rid = _next_id("remediations")
    client.run(
        """
        ?[remediation_id, finding_id, title, description, remediation_type, effort, priority, code_fix, reference_url] <-
            [[$rid, $fid, $t, $desc, $rt, $effort, $pri, $fix, $url]]
        :put remediations {remediation_id, finding_id, title, description, remediation_type, effort, priority, code_fix, reference_url}
        """,
        {"rid": rid, "fid": finding_id, "t": title, "desc": description or "", "rt": remediation_type,
         "effort": effort, "pri": priority, "fix": code_fix or "", "url": reference_url or ""},
    )
    return rid


def insert_trust_boundary(
    experiment_id: str, name: str, boundary_type: str,
    provider: str = None, region: str = None, description: str = None,
) -> int:
    client = _get_client()
    existing = client.run(
        "?[boundary_id] := *trust_boundaries{boundary_id: boundary_id, experiment_id: $eid, name: $n}",
        {"eid": experiment_id, "n": name},
    )
    if existing["rows"]:
        return existing["rows"][0][0]
    bid = _next_id("trust_boundaries")
    client.run(
        """
        ?[boundary_id, experiment_id, name, boundary_type, provider, region, description, created_at] <-
            [[$bid, $eid, $name, $bt, $prov, $reg, $desc, $now]]
        :put trust_boundaries {boundary_id, experiment_id, name, boundary_type, provider, region, description, created_at}
        """,
        {"bid": bid, "eid": experiment_id, "name": name, "bt": boundary_type or "",
         "prov": provider or "", "reg": region or "", "desc": description or "", "now": _now()},
    )
    return bid


def add_resource_to_trust_boundary(trust_boundary_id: int, resource_id: int) -> None:
    _get_client().run(
        "?[boundary_id, resource_id] <- [[$bid, $rid]] :put trust_boundary_members {boundary_id, resource_id}",
        {"bid": trust_boundary_id, "rid": resource_id},
    )


def insert_data_flow(experiment_id: str, name: str, flow_type: str, description: str = None) -> int:
    client = _get_client()
    fid = _next_id("data_flows")
    client.run(
        "?[flow_id, experiment_id, name, flow_type, description, created_at] <- "
        "[[$fid, $eid, $name, $ft, $desc, $now]] "
        ":put data_flows { flow_id, experiment_id, name, flow_type, description, created_at }",
        {"fid": fid, "eid": experiment_id, "name": name, "ft": flow_type or "",
         "desc": description or "", "now": _now()},
    )
    return fid


def add_data_flow_step(
    flow_id: int, step_order: int, component_label: str, resource_id: int = None,
    protocol: str = None, port: str = None, auth_method: str = None,
    is_encrypted: bool = None, notes: str = None,
) -> int:
    client = _get_client()
    sid = _next_id("data_flow_steps")
    client.run(
        """
        ?[step_id, flow_id, step_order, component_label, resource_id,
          protocol, port, auth_method, is_encrypted, notes] <-
          [[$sid, $fid, $so, $cl, $rid, $proto, $prt, $am, $enc, $notes]]
        :put data_flow_steps {
            step_id, flow_id, step_order, component_label, resource_id,
            protocol, port, auth_method, is_encrypted, notes
        }
        """,
        {"sid": sid, "fid": flow_id, "so": step_order, "cl": component_label,
         "rid": resource_id, "proto": protocol or "", "prt": port or "",
         "am": auth_method or "", "enc": is_encrypted, "notes": notes or ""},
    )
    return sid


# ============================================================================
# CONTEXT OPERATIONS
# ============================================================================

def insert_context_answer(
    experiment_id: str, question_key: str, answer_value: str, evidence_source: str,
    confidence: str = "confirmed", answered_by: str = "ContextDiscoveryAgent",
    question_text: Optional[str] = None, question_category: str = "General",
    evidence_type: str = "code",
) -> int:
    client = _get_client()
    return _insert_context_answer_impl(
        client, experiment_id=experiment_id, question_key=question_key,
        answer_value=answer_value, evidence_source=evidence_source,
        confidence=confidence, answered_by=answered_by,
        question_text=question_text, question_category=question_category,
        evidence_type=evidence_type,
    )


def _upsert_context_question(
    client, *, question_key: str, question_text: Optional[str] = None,
    question_category: str = "General",
) -> int:
    existing = client.run(
        "?[question_id] := *context_questions{question_id: question_id, question_key: $qk}",
        {"qk": question_key},
    )
    if existing["rows"]:
        return existing["rows"][0][0]
    resolved_text = question_text or question_key.replace("_", " ").title()
    qid = _next_id("context_questions")
    client.run(
        "?[question_id, question_key, question_text, question_category] <- [[$qid, $qk, $qt, $qcat]] "
        ":put context_questions { question_id, question_key, question_text, question_category }",
        {"qid": qid, "qk": question_key, "qt": resolved_text, "qcat": question_category},
    )
    return qid


def _insert_context_answer_impl(
    client, *, experiment_id: str, question_key: str, answer_value: str,
    evidence_source: str, confidence: str = "confirmed",
    answered_by: str = "ContextDiscoveryAgent",
    question_text: Optional[str] = None, question_category: str = "General",
    evidence_type: str = "code",
) -> int:
    question_id = _upsert_context_question(
        client, question_key=question_key,
        question_text=question_text, question_category=question_category,
    )
    aid = _next_id("context_answers")
    client.run(
        """
        ?[answer_id, experiment_id, question_id, answer_value, answer_confidence,
          evidence_source, evidence_type, answered_by, answered_at] <-
          [[$aid, $eid, $qid, $av, $conf, $esrc, $etype, $by, $now]]
        :put context_answers {
            answer_id, experiment_id, question_id, answer_value, answer_confidence,
            evidence_source, evidence_type, answered_by, answered_at
        }
        """,
        {"aid": aid, "eid": experiment_id, "qid": question_id, "av": answer_value,
         "conf": confidence, "esrc": evidence_source, "etype": evidence_type,
         "by": answered_by, "now": _now()},
    )
    return aid


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _infer_property_type(key: str) -> str:
    security_kw = ["public", "firewall", "encryption", "tls", "auth", "access", "rbac"]
    network_kw = ["subnet", "vnet", "ip", "port", "protocol"]
    identity_kw = ["identity", "principal", "role", "permission"]
    k = key.lower()
    if any(w in k for w in security_kw):
        return "security"
    if any(w in k for w in network_kw):
        return "network"
    if any(w in k for w in identity_kw):
        return "identity"
    return "configuration"


def _is_security_relevant(key: str) -> bool:
    security_kw = [
        "public", "firewall", "encryption", "tls", "ssl", "auth", "access",
        "rbac", "identity", "role", "permission", "security", "audit",
        "logging", "monitoring", "vulnerability", "exposed", "open",
    ]
    return any(w in key.lower() for w in security_kw)


def format_source_location(source_file: str, start_line: Optional[int], end_line: Optional[int]) -> str:
    if start_line:
        if end_line and end_line != start_line:
            return f"{source_file}:{start_line}-{end_line}"
        return f"{source_file}:{start_line}"
    return source_file


# ============================================================================
# QUERY HELPERS
# ============================================================================

def get_resources_for_diagram(experiment_id: str) -> List[Dict]:
    """
    Return all resources with their max finding score merged into canonical dicts.

    Uses a join across resources, repositories, findings, and properties,
    replacing the previous multi-round-trip Python loop.
    """
    client = _get_client()

    res_result = client.run(
        """
        max_score[resource_id, max(severity_score)] :=
            *findings{resource_id: resource_id, severity_score: severity_score,
                experiment_id: $eid},
            not is_null(severity_score)

        ?[resource_id, resource_name, resource_type, provider, repo_name, max_finding_score] :=
            *resources{resource_id: resource_id, experiment_id: $eid, repo_id: repo_id,
                resource_name: resource_name, resource_type: resource_type, provider: provider},
            *repositories{repo_id: repo_id, repo_name: repo_name},
            max_score[resource_id, max_finding_score]

        ?[resource_id, resource_name, resource_type, provider, repo_name, max_finding_score] :=
            *resources{resource_id: resource_id, experiment_id: $eid, repo_id: repo_id,
                resource_name: resource_name, resource_type: resource_type, provider: provider},
            *repositories{repo_id: repo_id, repo_name: repo_name},
            not max_score[resource_id, _],
            max_finding_score = 0
        """,
        {"eid": experiment_id},
    )

    resources = []
    for row in res_result["rows"]:
        resource_id, resource_name, resource_type, provider, repo_name, max_score = row
        props_result = client.run(
            "?[pk, pv] := *resource_properties{resource_id: $rid, property_key: pk, property_value: pv}",
            {"rid": resource_id},
        )
        prop_dict = {r[0]: _maybe_parse_json(r[1]) for r in props_result["rows"]}
        resources.append({
            "id": resource_id,
            "resource_name": resource_name,
            "resource_type": resource_type,
            "provider": provider,
            "repo_name": repo_name,
            "max_finding_score": max_score or 0,
            "properties": prop_dict,
            "public": _prop_bool(prop_dict.get("public") or prop_dict.get("public_access") or False),
            "public_reason": prop_dict.get("public_reason") or prop_dict.get("notes") or "",
            "network_acls": _maybe_parse_json(prop_dict.get("network_acls")),
            "firewall_rules": _maybe_parse_json(prop_dict.get("firewall_rules")) or [],
        })
    return resources


def get_connections_for_diagram(experiment_id: str, repo_name: Optional[str] = None) -> List[Dict]:
    """Return connections for diagram generation, optionally scoped to a repository."""
    client = _get_client()

    _BASE_CONN_QUERY = """
    ?[source, source_type, target, target_type, connection_type, protocol, port,
      auth_method, is_encrypted, via_component, notes, is_cross_repo, source_repo, target_repo] :=
        *resource_connections{connection_id: _, experiment_id: $eid,
            source_resource_id: src_id, target_resource_id: tgt_id,
            connection_type: connection_type, protocol: protocol, port: port,
            auth_method: am, authentication: auth,
            is_encrypted: is_encrypted, via_component: via_component, notes: notes,
            is_cross_repo: is_cross_repo, source_repo_id: src_repo_id, target_repo_id: tgt_repo_id},
        *resources{resource_id: src_id, resource_name: source, resource_type: source_type},
        *resources{resource_id: tgt_id, resource_name: target, resource_type: target_type},
        *repositories{repo_id: src_repo_id, repo_name: source_repo},
        *repositories{repo_id: tgt_repo_id, repo_name: target_repo},
        auth_method = if(am != '', am, auth)
    """

    if repo_name:
        # Two heads for OR (source_repo = rn OR target_repo = rn)
        scoped_query = _BASE_CONN_QUERY + ", source_repo = $rn"
        scoped_query += "\n" + _BASE_CONN_QUERY + ", target_repo = $rn"
        result = client.run(scoped_query, {"eid": experiment_id, "rn": repo_name})
    else:
        result = client.run(_BASE_CONN_QUERY, {"eid": experiment_id})

    keys = [
        "source", "source_type", "target", "target_type", "connection_type",
        "protocol", "port", "auth_method", "is_encrypted", "via_component", "notes",
        "is_cross_repo", "source_repo", "target_repo",
    ]
    # Deduplicate rows that appear in both source and target filters
    seen = set()
    rows = []
    for row in result["rows"]:
        key = tuple(row[:4] + row[4:5])  # source, source_type, target, target_type, conn_type
        if key not in seen:
            seen.add(key)
            rows.append(dict(zip(keys, row)))
    return rows


def get_resource_query_view(
    experiment_id: str,
    resource_name: str,
    repo_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return parent/child/ingress/egress/related view for a resource.

    Uses a single Datalog query to fetch the complete neighbourhood,
    replacing the previous five separate SQL queries and manual Python merging.
    """
    client = _get_client()

    params: Dict[str, Any] = {"eid": experiment_id, "rn": resource_name}
    repo_clause = ", repo_name = $repo" if repo_name else ""
    if repo_name:
        params["repo"] = repo_name

    base = client.run(
        f"""
        ?[resource_id, resource_name, resource_type, repo_name, parent_resource_id] :=
            *resources{{resource_id: resource_id, experiment_id: $eid, repo_id: repo_id,
                resource_name: resource_name, resource_type: resource_type,
                parent_resource_id: parent_resource_id}},
            *repositories{{repo_id: repo_id, repo_name: repo_name}},
            resource_name = $rn{repo_clause}
        :limit 1
        """,
        params,
    )
    if not base["rows"]:
        return None

    res_id, res_name, res_type, owning_repo, parent_id = base["rows"][0]

    # Single Datalog query for complete neighbourhood
    nbr = client.run(
        """
        children[cid, cname, ctype] :=
            *resources{resource_id: cid, resource_name: cname, resource_type: ctype,
                parent_resource_id: $rid}

        parent_info[par_name, par_type] :=
            *resources{resource_id: $par_id, resource_name: par_name, resource_type: par_type}

        ingress_rows[sid, sname, stype, srepo, ct, proto, port, am, enc, via, nts] :=
            *resource_connections{experiment_id: $eid, source_resource_id: sid,
                target_resource_id: $rid, connection_type: ct, protocol: proto,
                port: port, auth_method: am_col, authentication: auth_col,
                is_encrypted: enc, via_component: via, notes: nts},
            *resources{resource_id: sid, resource_name: sname, resource_type: stype,
                repo_id: src_repo_id},
            *repositories{repo_id: src_repo_id, repo_name: srepo},
            am = if(am_col != '', am_col, auth_col)

        egress_rows[tid, tname, ttype, trepo, ct, proto, port, am, enc, via, nts] :=
            *resource_connections{experiment_id: $eid, source_resource_id: $rid,
                target_resource_id: tid, connection_type: ct, protocol: proto,
                port: port, auth_method: am_col, authentication: auth_col,
                is_encrypted: enc, via_component: via, notes: nts},
            *resources{resource_id: tid, resource_name: tname, resource_type: ttype,
                repo_id: tgt_repo_id},
            *repositories{repo_id: tgt_repo_id, repo_name: trepo},
            am = if(am_col != '', am_col, auth_col)

        pending[qid, gap_type, ctx, atext, conf, sval] :=
            *enrichment_queue{queue_id: qid, resource_node_id: nid,
                gap_type: gap_type, context: ctx, assumption_text: atext,
                confidence: conf, suggested_value: sval, status: 'pending_review'},
            not is_null(nid),
            *resource_nodes{node_id: nid, terraform_name: $rn}
        pending[qid, gap_type, ctx, atext, conf, sval] :=
            *enrichment_queue{queue_id: qid, resource_node_id: nid,
                gap_type: gap_type, context: ctx, assumption_text: atext,
                confidence: conf, suggested_value: sval, status: 'pending_review'},
            not is_null(nid),
            *resource_nodes{node_id: nid, terraform_name: $inferred_rn}

        ?[kind, id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
          par_name, par_type, gap_type, ctx, atext, conf, sval] :=
            children[id, name, rtype], kind = 'child', repo = '', ct = '',
            proto = '', port = '', am = '', enc = null, via = '', nts = '',
            par_name = '', par_type = '', gap_type = '', ctx = '',
            atext = '', conf = '', sval = ''

        ?[kind, id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
          par_name, par_type, gap_type, ctx, atext, conf, sval] :=
            ingress_rows[id, name, rtype, repo, ct, proto, port, am, enc, via, nts],
            kind = 'ingress', par_name = '', par_type = '',
            gap_type = '', ctx = '', atext = '', conf = '', sval = ''

        ?[kind, id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
          par_name, par_type, gap_type, ctx, atext, conf, sval] :=
            egress_rows[id, name, rtype, repo, ct, proto, port, am, enc, via, nts],
            kind = 'egress', par_name = '', par_type = '',
            gap_type = '', ctx = '', atext = '', conf = '', sval = ''

        ?[kind, id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
          par_name, par_type, gap_type, ctx, atext, conf, sval] :=
            parent_info[par_name, par_type],
            kind = 'parent', id = $par_id, name = $rn, rtype = '', repo = $repo,
            ct = '', proto = '', port = '', am = '', enc = null, via = '', nts = '',
            gap_type = '', ctx = '', atext = '', conf = '', sval = ''

        ?[kind, id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
          par_name, par_type, gap_type, ctx, atext, conf, sval] :=
            pending[id, gap_type, ctx, atext, conf, sval],
            kind = 'assumption', name = '', rtype = '', repo = '', ct = '',
            proto = '', port = '', am = '', enc = null, via = '', nts = '',
            par_name = '', par_type = ''
        """,
        {
            "eid": experiment_id, "rid": res_id, "rn": resource_name,
            "repo": owning_repo,
            "par_id": parent_id if parent_id is not None else -1,
            "inferred_rn": f"__inferred__{resource_name.lower()}",
        },
    )

    children, ingress, egress, related, assumptions = [], [], [], [], []
    parent = None

    for row in nbr["rows"]:
        (kind, r_id, name, rtype, repo, ct, proto, port, am, enc, via, nts,
         par_name, par_type, gap_type, ctx, atext, conf, sval) = row

        if kind == "child":
            children.append({"resource_name": name, "resource_type": rtype})
        elif kind == "ingress":
            ingress.append({
                "from_resource": name, "from_type": rtype, "from_repo": repo,
                "connection_type": ct, "protocol": proto, "port": port,
                "auth_method": am, "is_encrypted": enc, "via_component": via, "notes": nts,
            })
            related.append({"resource": name, "resource_type": rtype, "repo": repo,
                            "direction": "ingress", "connection_type": ct})
        elif kind == "egress":
            egress.append({
                "to_resource": name, "to_type": rtype, "to_repo": repo,
                "connection_type": ct, "protocol": proto, "port": port,
                "auth_method": am, "is_encrypted": enc, "via_component": via, "notes": nts,
            })
            related.append({"resource": name, "resource_type": rtype, "repo": repo,
                            "direction": "egress", "connection_type": ct})
        elif kind == "parent" and par_name:
            parent = {"name": par_name, "type": par_type}
        elif kind == "assumption":
            assumptions.append({
                "id": r_id, "gap_type": gap_type, "context": ctx,
                "assumption_text": atext, "confidence": conf, "suggested_value": sval,
            })

    return {
        "resource": {"name": res_name, "type": res_type, "repo": owning_repo},
        "parent": parent,
        "children": children,
        "ingress": ingress,
        "egress": egress,
        "related": related,
        "pending_assumptions": assumptions,
    }


def _maybe_parse_json(val: Optional[str]):
    if val is None:
        return None
    try:
        return json.loads(val)
    except Exception:
        return val


def _prop_bool(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).lower() in ("1", "true", "yes", "y", "t")


# ============================================================================
# ENRICHMENT QUEUE HELPERS
# ============================================================================

def _normalize_queue_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "all":
        return normalized
    if normalized not in ENRICHMENT_QUEUE_STATUSES:
        valid = ", ".join(sorted(ENRICHMENT_QUEUE_STATUSES | {"all"}))
        raise ValueError(f"Invalid enrichment_queue status '{status}'. Expected one of: {valid}")
    return normalized


def _normalize_enrichment_decision(decision: str) -> str:
    normalized = (decision or "").strip().lower()
    resolved = ENRICHMENT_DECISION_MAP.get(normalized)
    if not resolved:
        valid = ", ".join(sorted(ENRICHMENT_DECISION_MAP))
        raise ValueError(f"Invalid decision '{decision}'. Expected one of: {valid}")
    return resolved


def _load_repo_aliases(raw_aliases: Optional[str], *, field_name: str) -> list:
    if not raw_aliases:
        return []
    try:
        parsed = json.loads(raw_aliases)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid aliases JSON in {field_name}: {raw_aliases}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"Expected aliases list in {field_name}, got: {type(parsed).__name__}")
    return [stripped for a in parsed if a is not None for stripped in (str(a).strip(),) if stripped]


def _list_experiment_repos(client, experiment_id: str) -> list:
    result = client.run(
        "?[repo_name] := *repositories{repo_name: repo_name, experiment_id: $eid}",
        {"eid": experiment_id},
    )
    names = [row[0] for row in result["rows"] if row[0]]
    if not names:
        raise ValueError(f"No repositories found for experiment '{experiment_id}'.")
    return names


def list_enrichment_assumptions(
    experiment_id: str,
    repo_name: Optional[str] = None,
    status: str = "pending_review",
) -> List[Dict[str, Any]]:
    """List enrichment queue assumptions scoped to an experiment and optional repo."""
    client = _get_client()
    experiment_repos = _list_experiment_repos(client, experiment_id)
    repo_scope = set(experiment_repos)
    if repo_name and repo_name not in repo_scope:
        raise ValueError(
            f"Repository '{repo_name}' is not registered under experiment '{experiment_id}'."
        )

    normalized = _normalize_queue_status(status)
    params: Dict[str, Any] = {}
    status_clause = ""
    if normalized != "all":
        status_clause = ", status = $status"
        params["status"] = normalized

    result = client.run(
        f"""
        ?[queue_id, resource_node_id, relationship_id, gap_type, context,
          assumption_text, assumption_basis, confidence, suggested_value, status,
          resolved_by, resolved_at, rejection_reason, created_at] :=
            *enrichment_queue{{queue_id: queue_id, resource_node_id: resource_node_id,
                relationship_id: relationship_id, gap_type: gap_type, context: context,
                assumption_text: assumption_text, assumption_basis: assumption_basis,
                confidence: confidence, suggested_value: suggested_value, status: status,
                resolved_by: resolved_by, resolved_at: resolved_at,
                rejection_reason: rejection_reason, created_at: created_at}}{status_clause}
        """,
        params,
    )

    records: List[Dict[str, Any]] = []
    for row in result["rows"]:
        (queue_id, resource_node_id, relationship_id, gap_type, context,
         assumption_text, assumption_basis, confidence, suggested_value, status_val,
         resolved_by, resolved_at, rejection_reason, created_at) = row

        # Gather repo scope from node/relationship
        node_scope: set = set()
        if resource_node_id:
            nr = client.run(
                "?[sr, al] := *resource_nodes{node_id: $nid, source_repo: sr, aliases: al}",
                {"nid": resource_node_id},
            )
            for nrow in nr["rows"]:
                if nrow[0]:
                    node_scope.add(nrow[0])
                node_scope.update(_load_repo_aliases(nrow[1], field_name="node_aliases"))
        if relationship_id:
            rr = client.run(
                """
                ?[sr, sra, tra] :=
                    *resource_relationships{rel_id: $rid, source_repo: sr, source_id: sid, target_id: tid},
                    *resource_nodes{node_id: sid, source_repo: sra},
                    *resource_nodes{node_id: tid, source_repo: tra}
                """,
                {"rid": relationship_id},
            )
            for rrow in rr["rows"]:
                node_scope.update(r for r in rrow if r)

        if not node_scope.intersection(repo_scope):
            continue
        if repo_name and repo_name not in node_scope:
            continue

        records.append({
            "id": queue_id,
            "resource_node_id": resource_node_id,
            "relationship_id": relationship_id,
            "gap_type": gap_type,
            "context": context,
            "assumption_text": assumption_text,
            "assumption_basis": assumption_basis,
            "confidence": confidence,
            "suggested_value": suggested_value,
            "status": status_val,
            "resolved_by": resolved_by,
            "resolved_at": resolved_at,
            "rejection_reason": rejection_reason,
            "created_at": created_at,
            "repo_scope": sorted(node_scope),
            "question_key": f"enrichment_queue_assumption_{queue_id}_decision",
        })
    return records


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
    """Resolve a pending enrichment assumption."""
    normalized = _normalize_enrichment_decision(decision)
    resolver = (resolved_by or "").strip()
    if not resolver:
        raise ValueError("resolved_by must be provided.")
    note = (resolution_note or "").strip()
    if normalized == "rejected" and not note:
        raise ValueError("A rejection requires --note explaining why.")
    if not evidence_source.strip():
        raise ValueError("evidence_source must be provided.")

    client = _get_client()
    experiment_repos = _list_experiment_repos(client, experiment_id)
    if repo_name and repo_name not in set(experiment_repos):
        raise ValueError(
            f"Repository '{repo_name}' is not registered under experiment '{experiment_id}'."
        )

    row = client.run(
        "?[queue_id, status, assumption_text, context] := *enrichment_queue{queue_id: queue_id, "
        "status: status, assumption_text: assumption_text, context: context}, queue_id = $aid",
        {"aid": assumption_id},
    )
    if not row["rows"]:
        raise ValueError(f"Assumption id {assumption_id} was not found in enrichment_queue.")

    _, status_val, assumption_text, context = row["rows"][0]
    if status_val != "pending_review":
        raise ValueError(
            f"Assumption id {assumption_id} is already resolved with status '{status_val}'."
        )

    question_key = f"enrichment_queue_assumption_{assumption_id}_decision"
    label = assumption_text or context or f"Assumption #{assumption_id}"
    answer_payload = json.dumps({
        "assumption_id": assumption_id, "decision": normalized,
        "note": note or None, "assumption_text": label, "resolver": resolver,
    }, sort_keys=True)

    answer_id = _insert_context_answer_impl(
        client, experiment_id=experiment_id, question_key=question_key,
        question_text=f"Resolve enrichment assumption #{assumption_id}: {label}",
        question_category="EnrichmentQueue", answer_value=answer_payload,
        evidence_source=evidence_source, evidence_type="user_confirmation",
        confidence="confirmed", answered_by=resolver,
    )

    rejection_reason = note if normalized == "rejected" else ""
    client.run(
        "?[queue_id, status, resolved_by, resolved_at, rejection_reason] <- "
        "[[$aid, $decision, $resolver, $now, $rejection]] "
        ":update enrichment_queue { queue_id, status, resolved_by, resolved_at, rejection_reason }",
        {"aid": assumption_id, "decision": normalized, "resolver": resolver,
         "now": _now(), "rejection": rejection_reason},
    )

    confidence_updates: List[str] = []
    if normalized == "confirmed":
        rel_row = client.run(
            "?[relationship_id] := *enrichment_queue{queue_id: $aid, relationship_id: relationship_id}",
            {"aid": assumption_id},
        )
        if rel_row["rows"] and rel_row["rows"][0][0] is not None:
            rel_id = rel_row["rows"][0][0]
            client.run(
                "?[rel_id, confidence] <- [[$rid, 'user_confirmed']] "
                ":update resource_relationships {rel_id, confidence}",
                {"rid": rel_id},
            )
            confidence_updates.append(f"resource_relationships[{rel_id}] confidence=user_confirmed")

    return {
        "assumption_id": assumption_id, "experiment_id": experiment_id,
        "repo_name": repo_name, "status": normalized, "resolved_by": resolver,
        "resolution_note": note or None, "question_key": question_key,
        "context_answer_id": answer_id, "confidence_updates": confidence_updates,
    }


if __name__ == "__main__":
    print(f"Database path: {DB_PATH}")
    print(f"Database exists: {DB_PATH.exists()}")
