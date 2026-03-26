#!/usr/bin/env python3
"""Generate AI project overview summary and persist structured fields to DB metadata.

Usage:
    python Scripts/Enrich/generate_project_overview.py --experiment <id> --repo <name>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "Scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS / "Persist"))

from Scripts.Persist import db_helpers
from llm_resource_interpreter import _call_llm


def _parse_llm_json(raw: object) -> dict | None:
    if isinstance(raw, dict):
        candidate = raw.get("content") or raw.get("interpretation") or ""
        if isinstance(candidate, str):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        if "project_summary" in raw:
            return raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


def _fallback_overview(repo_name: str, facts: dict) -> dict:
    providers = ", ".join(facts.get("providers", [])) or "unknown providers"
    top_resources = ", ".join(facts.get("resource_types", [])) or "no resources identified"
    top_deps = ", ".join(facts.get("dependency_types", [])) or "no explicit dependency edges"
    auth_signals = ", ".join(facts.get("auth_signals", [])) or "limited explicit auth metadata"
    top_issues = ", ".join(facts.get("top_findings", [])) or "no findings"
    skeptic = facts.get("skeptic_summary") or "No skeptic reviews recorded yet"

    return {
        "project_summary": f"{repo_name} is an infrastructure-backed service repository with assets across {providers}.",
        "deployment_summary": f"Deployment footprint is primarily: {top_resources}.",
        "interactions_summary": f"Observed interaction patterns include: {', '.join(facts.get('interaction_types', [])) or 'no non-containment interactions yet' }.",
        "auth_summary": f"Access-control/auth signals: {auth_signals}.",
        "dependencies_summary": f"Dependencies are represented by: {top_deps}.",
        "issues_summary": f"Top issues include: {top_issues}.",
        "skeptic_summary": skeptic,
    }


def _fetch_facts(conn: sqlite3.Connection, experiment_id: str, repo_name: str) -> tuple[int, dict]:
    repo_row = conn.execute(
        """
        SELECT id FROM repositories
        WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
        LIMIT 1
        """,
        (experiment_id, repo_name),
    ).fetchone()
    if not repo_row:
        raise ValueError(f"Repository not found in experiment {experiment_id}: {repo_name}")
    repo_id = repo_row[0]

    providers = [
        r[0]
        for r in conn.execute(
            """
            SELECT COALESCE(provider, 'unknown') AS provider
            FROM resources
            WHERE experiment_id = ? AND repo_id = ?
            GROUP BY COALESCE(provider, 'unknown')
            ORDER BY COUNT(*) DESC
            LIMIT 5
            """,
            (experiment_id, repo_id),
        ).fetchall()
    ]

    resource_types = [
        r[0]
        for r in conn.execute(
            """
            SELECT resource_type
            FROM resources
            WHERE experiment_id = ? AND repo_id = ?
            GROUP BY resource_type
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """,
            (experiment_id, repo_id),
        ).fetchall()
    ]

    interaction_types = [
        r[0]
        for r in conn.execute(
            """
            SELECT connection_type
            FROM resource_connections
            WHERE experiment_id = ?
              AND (source_repo_id = ? OR target_repo_id = ?)
              AND connection_type IS NOT NULL
              AND LOWER(connection_type) NOT IN ('contains')
            GROUP BY connection_type
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """,
            (experiment_id, repo_id, repo_id),
        ).fetchall()
    ]

    dep_types = [
        r[0]
        for r in conn.execute(
            """
            SELECT connection_type
            FROM resource_connections
            WHERE experiment_id = ?
              AND source_repo_id = ?
              AND connection_type IS NOT NULL
              AND LOWER(connection_type) NOT IN ('contains')
            GROUP BY connection_type
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """,
            (experiment_id, repo_id),
        ).fetchall()
    ]

    auth_signals = [
        "; ".join([x for x in [r[0], r[1], r[2], r[3]] if x])
        for r in conn.execute(
            """
            SELECT connection_type, authentication, authorization, auth_method
            FROM resource_connections
            WHERE experiment_id = ?
              AND (source_repo_id = ? OR target_repo_id = ?)
              AND (
                (authentication IS NOT NULL AND TRIM(authentication) != '') OR
                (authorization IS NOT NULL AND TRIM(authorization) != '') OR
                (auth_method IS NOT NULL AND TRIM(auth_method) != '') OR
                LOWER(COALESCE(connection_type,'')) LIKE '%auth%' OR
                LOWER(COALESCE(connection_type,'')) LIKE '%grant%'
              )
            GROUP BY connection_type, authentication, authorization, auth_method
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """,
            (experiment_id, repo_id, repo_id),
        ).fetchall()
    ]

    top_findings = [
        (r[0] or r[1] or "Untitled")
        for r in conn.execute(
            """
            SELECT title, rule_id
            FROM findings
            WHERE experiment_id = ? AND repo_id = ?
            ORDER BY severity_score DESC, id ASC
            LIMIT 8
            """,
            (experiment_id, repo_id),
        ).fetchall()
    ]

    skeptic_rows = conn.execute(
        """
        SELECT sr.reviewer_type, ROUND(AVG(sr.adjusted_score), 2) AS avg_score, COUNT(*) AS reviews
        FROM skeptic_reviews sr
        JOIN findings f ON f.id = sr.finding_id
        WHERE f.experiment_id = ? AND f.repo_id = ?
        GROUP BY sr.reviewer_type
        ORDER BY sr.reviewer_type
        """,
        (experiment_id, repo_id),
    ).fetchall()
    skeptic_summary = ", ".join(
        f"{r[0]} avg={r[1]} ({r[2]} reviews)" for r in skeptic_rows
    )

    facts = {
        "providers": providers,
        "resource_types": resource_types,
        "interaction_types": interaction_types,
        "dependency_types": dep_types,
        "auth_signals": auth_signals,
        "top_findings": top_findings,
        "skeptic_summary": skeptic_summary,
    }
    return repo_id, facts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AI project overview metadata")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    with db_helpers.get_db_connection() as conn:
        repo_id, facts = _fetch_facts(conn, args.experiment, args.repo)

    prompt = (
        "You are creating an executive technical overview for a security triage portal. "
        "Use the supplied repository facts and respond with JSON only. Keep each field concise (1-2 sentences).\n\n"
        f"Repository: {args.repo}\n"
        f"Facts JSON: {json.dumps(facts)}\n\n"
        "Return JSON with keys: project_summary, deployment_summary, interactions_summary, auth_summary, dependencies_summary, issues_summary, skeptic_summary"
    )

    raw = _call_llm(prompt)
    parsed = _parse_llm_json(raw)
    overview = parsed if parsed and parsed.get("project_summary") else _fallback_overview(args.repo, facts)

    mappings = [
        ("ai_project_summary", overview.get("project_summary", "")),
        ("ai_deployment_summary", overview.get("deployment_summary", "")),
        ("ai_interactions_summary", overview.get("interactions_summary", "")),
        ("ai_auth_summary", overview.get("auth_summary", "")),
        ("ai_dependencies_summary", overview.get("dependencies_summary", "")),
        ("ai_issues_summary", overview.get("issues_summary", "")),
        ("ai_skeptic_summary", overview.get("skeptic_summary", "")),
    ]

    for key, value in mappings:
        text = (value or "").strip()
        if not text:
            continue
        db_helpers.upsert_context_metadata(
            args.experiment,
            args.repo,
            key,
            text,
            namespace="ai_overview",
            source="ai_project_overview",
        )

    print(f"Generated AI overview metadata for {args.repo} (experiment {args.experiment}, repo_id {repo_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
