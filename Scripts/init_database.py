#!/usr/bin/env python3
"""Initialize the CozoDB (SQLite engine) schema for Triage-Saurus learning database."""

import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "Output/Learning/triage_cozo.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relation_exists(db, name: str) -> bool:
    result = db.run("::relations")
    return any(row[0] == name for row in result["rows"])


def _create_if_absent(db, name: str, script: str) -> None:
    if not _relation_exists(db, name):
        db.run(script)


def init_schema(db) -> None:
    """Create all CozoDB relations (idempotent)."""

    # ── Counter table (used for auto-increment IDs) ───────────────────────────
    _create_if_absent(db, "counters", """
        :create counters {
            tbl: String
            =>
            val: Int default 0
        }
    """)

    # ── Experiments ───────────────────────────────────────────────────────────
    _create_if_absent(db, "experiments", """
        :create experiments {
            exp_id: String
            =>
            name: String default '',
            parent_experiment_id: String default '',
            agent_versions: String default '',
            script_versions: String default '',
            strategy_version: String default '',
            model: String default '',
            changes_description: String default '',
            changes_files: String default '',
            hypothesis: String default '',
            repos: String default '',
            status: String default 'running',
            started_at: String default '',
            completed_at: String default '',
            duration_sec: Int? default null,
            tokens_used: Int? default null,
            findings_count: Int? default null,
            high_value_count: Int? default null,
            avg_score: Float? default null,
            false_positives: Int? default null,
            false_negatives: Int? default null,
            accuracy_rate: Float? default null,
            precision_rate: Float? default null,
            recall_rate: Float? default null,
            human_reviewed: Bool default false,
            human_quality_rating: Int? default null,
            notes: String default '',
            created_by: String default '',
            tags: String default ''
        }
    """)

    # ── Repositories ──────────────────────────────────────────────────────────
    _create_if_absent(db, "repositories", """
        :create repositories {
            repo_id: Int
            =>
            experiment_id: String,
            repo_name: String,
            repo_url: String default '',
            repo_type: String default '',
            primary_language: String default '',
            files_scanned: Int default 0,
            iac_files_count: Int default 0,
            code_files_count: Int default 0,
            scanned_at: String default ''
        }
    """)

    # ── Resources (Asset Inventory) ───────────────────────────────────────────
    _create_if_absent(db, "resources", """
        :create resources {
            resource_id: Int
            =>
            experiment_id: String,
            repo_id: Int,
            resource_name: String,
            resource_type: String,
            provider: String default '',
            region: String default '',
            parent_resource_id: Int? default null,
            discovered_by: String default '',
            discovery_method: String default '',
            source_file: String default '',
            source_line_start: Int? default null,
            source_line_end: Int? default null,
            status: String default 'active',
            display_label: String default '',
            tags: String default '',
            first_seen: String default '',
            last_seen: String default ''
        }
    """)

    # ── Resource Properties ───────────────────────────────────────────────────
    _create_if_absent(db, "resource_properties", """
        :create resource_properties {
            property_id: Int
            =>
            resource_id: Int,
            property_key: String,
            property_value: String default '',
            property_type: String default '',
            is_security_relevant: Bool default false
        }
    """)

    # ── Resource Context ──────────────────────────────────────────────────────
    _create_if_absent(db, "resource_context", """
        :create resource_context {
            resource_id: Int
            =>
            business_criticality: String default '',
            data_classification: String default '',
            environment: String default '',
            purpose: String default '',
            owner_team: String default '',
            cost_per_month: Float? default null,
            usage_pattern: String default '',
            user_count: Int? default null,
            uptime_requirement: String default '',
            compliance_scope: String default '',
            last_updated: String default ''
        }
    """)

    # ── Resource Connections ──────────────────────────────────────────────────
    _create_if_absent(db, "resource_connections", """
        :create resource_connections {
            connection_id: Int
            =>
            experiment_id: String,
            source_resource_id: Int,
            target_resource_id: Int,
            source_repo_id: Int? default null,
            target_repo_id: Int? default null,
            is_cross_repo: Bool default false,
            connection_type: String default '',
            protocol: String default '',
            port: String default '',
            authentication: String default '',
            authorization: String default '',
            auth_method: String default '',
            is_encrypted: Bool? default null,
            via_component: String default '',
            notes: String default ''
        }
    """)

    # ── Findings ──────────────────────────────────────────────────────────────
    _create_if_absent(db, "findings", """
        :create findings {
            finding_id: Int
            =>
            experiment_id: String,
            repo_id: Int? default null,
            resource_id: Int? default null,
            title: String default '',
            description: String default '',
            category: String default '',
            severity_score: Int? default null,
            base_severity: String default '',
            overall_score: String default '',
            evidence_location: String default '',
            source_file: String default '',
            source_line_start: Int? default null,
            source_line_end: Int? default null,
            finding_path: String default '',
            detected_by: String default '',
            detection_method: String default '',
            rule_id: String default '',
            proposed_fix: String default '',
            code_snippet: String default '',
            reason: String default '',
            status: String default 'open',
            llm_enriched_at: String default '',
            created_at: String default '',
            updated_at: String default ''
        }
    """)

    # ── Enrichment Queue ──────────────────────────────────────────────────────
    _create_if_absent(db, "enrichment_queue", """
        :create enrichment_queue {
            queue_id: Int
            =>
            resource_node_id: Int? default null,
            relationship_id: Int? default null,
            gap_type: String default '',
            context: String default '',
            assumption_text: String default '',
            assumption_basis: String default '',
            confidence: String default 'medium',
            suggested_value: String default '',
            status: String default 'pending_review',
            resolved_by: String default '',
            resolved_at: String default '',
            rejection_reason: String default '',
            created_at: String default ''
        }
    """)

    # ── Learning Metrics ──────────────────────────────────────────────────────
    _create_if_absent(db, "learning_metrics", """
        :create learning_metrics {
            metric_id: Int
            =>
            experiment_id: String,
            metric_name: String,
            metric_value: Float,
            metric_type: String default '',
            recorded_at: String default ''
        }
    """)

    # ── Trust Boundaries ─────────────────────────────────────────────────────
    _create_if_absent(db, "trust_boundaries", """
        :create trust_boundaries {
            boundary_id: Int
            =>
            experiment_id: String,
            name: String,
            boundary_type: String default '',
            provider: String default '',
            region: String default '',
            description: String default '',
            notes: String default '',
            created_at: String default ''
        }
    """)

    _create_if_absent(db, "trust_boundary_members", """
        :create trust_boundary_members {
            boundary_id: Int,
            resource_id: Int
        }
    """)

    # ── Data Flows ───────────────────────────────────────────────────────────
    _create_if_absent(db, "data_flows", """
        :create data_flows {
            flow_id: Int
            =>
            experiment_id: String,
            name: String,
            flow_type: String default '',
            description: String default '',
            notes: String default '',
            created_at: String default ''
        }
    """)

    _create_if_absent(db, "data_flow_steps", """
        :create data_flow_steps {
            step_id: Int
            =>
            flow_id: Int,
            step_order: Int,
            resource_id: Int? default null,
            component_label: String default '',
            protocol: String default '',
            port: String default '',
            auth_method: String default '',
            is_encrypted: Bool? default null,
            notes: String default ''
        }
    """)

    # ── Context Questions & Answers ──────────────────────────────────────────
    _create_if_absent(db, "context_questions", """
        :create context_questions {
            question_id: Int
            =>
            question_key: String,
            question_text: String default '',
            question_category: String default 'General'
        }
    """)

    _create_if_absent(db, "context_answers", """
        :create context_answers {
            answer_id: Int
            =>
            experiment_id: String,
            question_id: Int,
            answer_value: String default '',
            answer_confidence: String default '',
            evidence_source: String default '',
            evidence_type: String default '',
            answered_by: String default '',
            answered_at: String default ''
        }
    """)

    # ── Skeptic Reviews ──────────────────────────────────────────────────────
    _create_if_absent(db, "skeptic_reviews", """
        :create skeptic_reviews {
            review_id: Int
            =>
            finding_id: Int,
            reviewer_type: String,
            score_adjustment: Float? default null,
            adjusted_score: Float? default null,
            confidence: Float? default null,
            reasoning: String default '',
            key_concerns: String default '',
            mitigating_factors: String default '',
            recommendation: String default 'confirm',
            reviewed_at: String default ''
        }
    """)

    # ── Risk Score History ───────────────────────────────────────────────────
    _create_if_absent(db, "risk_score_history", """
        :create risk_score_history {
            score_id: Int
            =>
            finding_id: Int,
            score: Float,
            scored_by: String default '',
            rationale: String default '',
            created_at: String default ''
        }
    """)

    # ── Context Metadata ─────────────────────────────────────────────────────
    _create_if_absent(db, "context_metadata", """
        :create context_metadata {
            meta_id: Int
            =>
            experiment_id: String,
            repo_id: Int? default null,
            namespace: String default 'phase2',
            key: String,
            value: String default '',
            source: String default '',
            created_at: String default ''
        }
    """)

    # ── Remediations ─────────────────────────────────────────────────────────
    _create_if_absent(db, "remediations", """
        :create remediations {
            remediation_id: Int
            =>
            finding_id: Int,
            title: String,
            description: String default '',
            remediation_type: String default 'config',
            effort: String default 'medium',
            priority: Int default 2,
            code_fix: String default '',
            reference_url: String default ''
        }
    """)

    # ── Knowledge Graph ───────────────────────────────────────────────────────
    _create_if_absent(db, "resource_nodes", """
        :create resource_nodes {
            node_id: Int
            =>
            resource_type: String,
            terraform_name: String,
            source_repo: String,
            canonical_name: String default '',
            friendly_name: String default '',
            display_label: String default '',
            provider: String default '',
            aliases: String default '[]',
            confidence: String default 'extracted',
            properties: String default '{}',
            created_at: String default '',
            updated_at: String default ''
        }
    """)

    _create_if_absent(db, "resource_relationships", """
        :create resource_relationships {
            rel_id: Int
            =>
            source_id: Int,
            target_id: Int,
            relationship_type: String,
            source_repo: String default '',
            confidence: String default 'extracted',
            notes: String default '',
            created_at: String default ''
        }
    """)

    _create_if_absent(db, "resource_equivalences", """
        :create resource_equivalences {
            equiv_id: Int
            =>
            resource_node_id: Int,
            candidate_resource_type: String,
            candidate_terraform_name: String,
            candidate_source_repo: String,
            equivalence_kind: String default 'cross_repo_alias',
            confidence: String default 'medium',
            evidence_level: String default 'inferred',
            provenance: String default '',
            context: String default '',
            created_at: String default '',
            updated_at: String default ''
        }
    """)

    import sys as _sys
    print("CozoDB schema initialized at", DB_PATH, file=_sys.stderr)


def main():
    try:
        from pycozo.client import Client
    except ImportError:
        print("ERROR: pycozo not installed. Run: pip install pycozo cozo-embedded", file=sys.stderr)
        sys.exit(1)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = Client("sqlite", str(DB_PATH), dataframe=False)
    try:
        init_schema(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
