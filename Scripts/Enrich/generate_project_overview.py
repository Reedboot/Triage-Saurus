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
    
    # Build deployment footprint summary
    footprint = facts.get("deployment_footprint", {})
    categories_summary = ", ".join(
        f"{cat}: {count}" for cat, count in footprint.get("categories", {}).items()
    ) if footprint.get("categories") else "no resources identified"
    providers_summary = ", ".join(
        f"{prov}: {count}" for prov, count in footprint.get("providers", {}).items()
    ) if footprint.get("providers") else "unknown"

    return {
        "project_summary": f"{repo_name} is an infrastructure-backed service repository with assets across {providers}.",
        "deployment_summary": f"Deployment footprint ({footprint.get('total_resources', 0)} total): {categories_summary}. Providers: {providers_summary}.",
        "deployment_footprint": footprint,
        "interactions_summary": f"Observed interaction patterns include: {', '.join(facts.get('interaction_types', [])) or 'no non-containment interactions yet' }.",
        "auth_summary": f"Access-control/auth signals: {auth_signals}.",
        "dependencies_summary": f"Dependencies are represented by: {top_deps}.",
        "issues_summary": f"Top issues include: {top_issues}.",
        "skeptic_summary": skeptic,
    }


def _generate_open_questions(conn: sqlite3.Connection, repo_id: int, facts: dict) -> list[dict]:
    """Generate open questions based on repository facts and findings.
    
    Returns list of question objects with: question, file, line, asset
    """
    questions = []
    
    # Question 1: Unclear deployment strategy
    if facts.get("interaction_types"):
        questions.append({
            "question": "What is the primary deployment mechanism for this service?",
            "file": "terraform/kubernetes.tf",
            "line": 1,
            "asset": "deployment"
        })
    
    # Question 2: Authentication strategy
    if not facts.get("auth_signals") or len(facts.get("auth_signals", [])) < 3:
        questions.append({
            "question": "What authentication method should be used for inter-service communication?",
            "file": "terraform/api_management.tf",
            "line": 1,
            "asset": "api_management"
        })
    
    # Question 3: Data sensitivity classification
    if "sql" in str(facts.get("resource_types", [])).lower():
        questions.append({
            "question": "How is sensitive data classified and protected in the database layer?",
            "file": "terraform/database.tf",
            "line": 1,
            "asset": "database"
        })
    
    # Question 4: Disaster recovery strategy
    if len(facts.get("providers", [])) > 1 or "dr" in str(facts.get("resource_types", [])).lower():
        questions.append({
            "question": "What is the disaster recovery and failover strategy for this service?",
            "file": "README.md",
            "line": 1,
            "asset": "infrastructure"
        })
    
    # Question 5: Monitoring and alerting
    if not facts.get("skeptic_summary"):
        questions.append({
            "question": "Are all critical service dependencies monitored with alerting configured?",
            "file": "terraform/monitoring.tf",
            "line": 1,
            "asset": "monitoring"
        })
    
    return questions[:5]  # Keep at most 5 questions


def _fetch_deployment_footprint(conn: sqlite3.Connection, experiment_id: str, repo_id: int) -> dict:
    """Generate structured deployment footprint breakdown by category and provider."""
    
    # Query categories and counts
    category_rows = conn.execute(
        """
        SELECT render_category, COUNT(*) as count
        FROM resources
        WHERE experiment_id = ? AND repo_id = ?
        GROUP BY render_category
        ORDER BY count DESC
        """,
        (experiment_id, repo_id),
    ).fetchall()
    
    categories = {}
    for category, count in category_rows:
        cat_name = category or "Other"
        categories[cat_name] = int(count)
    
    # Query providers and counts
    provider_rows = conn.execute(
        """
        SELECT COALESCE(provider, 'Unknown') as provider, COUNT(*) as count
        FROM resources
        WHERE experiment_id = ? AND repo_id = ?
        GROUP BY COALESCE(provider, 'Unknown')
        ORDER BY count DESC
        """,
        (experiment_id, repo_id),
    ).fetchall()
    
    providers = {}
    for provider, count in provider_rows:
        providers[provider] = int(count)
    
    # Query total resource count
    total_count = conn.execute(
        """
        SELECT COUNT(*) FROM resources
        WHERE experiment_id = ? AND repo_id = ?
        """,
        (experiment_id, repo_id),
    ).fetchone()[0]
    
    return {
        "categories": categories,
        "providers": providers,
        "total_resources": int(total_count),
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
    
    # Fetch deployment footprint breakdown
    deployment_footprint = _fetch_deployment_footprint(conn, experiment_id, repo_id)

    facts = {
        "providers": providers,
        "resource_types": resource_types,
        "interaction_types": interaction_types,
        "dependency_types": dep_types,
        "auth_signals": auth_signals,
        "top_findings": top_findings,
        "skeptic_summary": skeptic_summary,
        "deployment_footprint": deployment_footprint,
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
        ("ai_deployment_footprint", json.dumps(overview.get("deployment_footprint", {}))),
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

    # Generate and store open questions via LLM
    questions_prompt = (
        "You are generating open questions for an executive security triage review. "
        "Based on these repository facts, generate 3-5 critical questions the reviewer should address. "
        "Return ONLY a JSON array where each element has: {\"question\": \"...\", \"file\": \"...\", \"line\": <int>, \"asset\": \"...\"}\n\n"
        f"Repository: {args.repo}\n"
        f"Facts JSON: {json.dumps(facts)}\n\n"
        "Example format:\n"
        '[{"question": "What is the primary auth mechanism?", "file": "terraform/auth.tf", "line": 1, "asset": "authentication"}]'
    )

    try:
        questions_raw = _call_llm(questions_prompt)
        questions_list = []
        
        if isinstance(questions_raw, str):
            questions_list = json.loads(questions_raw)
        elif isinstance(questions_raw, dict):
            content = questions_raw.get("content") or questions_raw.get("interpretation", "")
            if isinstance(content, str):
                questions_list = json.loads(content)
            elif isinstance(content, list):
                questions_list = content
        elif isinstance(questions_raw, list):
            questions_list = questions_raw
        
        # Validate and filter questions
        valid_questions = []
        for q in questions_list:
            if isinstance(q, dict) and q.get("question"):
                q_obj = {
                    "question": str(q.get("question", "")).strip()[:200],
                    "file": str(q.get("file", "README.md"))[:100],
                    "line": max(1, int(q.get("line", 1))),
                    "asset": str(q.get("asset", "infrastructure"))[:100],
                }
                if q_obj["question"]:
                    valid_questions.append(q_obj)
        
        # Store questions
        if valid_questions:
            db_helpers.upsert_context_metadata(
                args.experiment,
                args.repo,
                "ai_open_questions",
                json.dumps(valid_questions[:5]),  # Keep at most 5
                namespace="ai_overview",
                source="ai_project_overview",
            )
    except Exception as e:
        # Fallback: generate questions heuristically if LLM fails
        try:
            with db_helpers.get_db_connection() as conn:
                fallback_questions = _generate_open_questions(conn, repo_id, facts)
            if fallback_questions:
                db_helpers.upsert_context_metadata(
                    args.experiment,
                    args.repo,
                    "ai_open_questions",
                    json.dumps(fallback_questions),
                    namespace="ai_overview",
                    source="ai_project_overview",
                )
        except Exception:
            pass

    print(f"Generated AI overview metadata for {args.repo} (experiment {args.experiment}, repo_id {repo_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
