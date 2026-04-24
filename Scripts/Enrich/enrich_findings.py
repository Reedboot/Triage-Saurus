#!/usr/bin/env python3
"""Phase 2 pipeline: LLM enrichment for unenriched findings.

Usage:
    python3 Scripts/Enrich/enrich_findings.py --experiment <id> [--dry-run] [--limit N]
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db_helpers
from llm_resource_interpreter import _call_llm


_CREDENTIAL_KEYWORDS = frozenset([
    "hardcoded", "secret", "password", "passwd", "credential", "api_key", "apikey",
    "token", "connection_string", "connectionstring", "private_key", "privatekey",
    "access_key", "accesskey", "auth", "jwt",
])


def _is_credential_finding(row: dict) -> bool:
    """Return True if this finding is likely about hardcoded / exposed credentials."""
    haystack = " ".join(filter(None, [
        row.get("rule_id") or "",
        row.get("title") or "",
        row.get("reason") or "",
    ])).lower()
    return any(kw in haystack for kw in _CREDENTIAL_KEYWORDS)


def _build_prompt(row: dict) -> str:
    snippet = (row.get("code_snippet") or "").strip()
    rule_or_title = row.get("rule_id") or row.get("title") or "Finding"
    is_cred = _is_credential_finding(row)

    # Attack intelligence from rule message (pre-authored, don't override)
    attack_context = ""
    attack_parts = [
        ("Attack chain", row.get("attack_chain")),
        ("Tools",        row.get("attack_tools")),
        ("Kill chain",   row.get("attack_chain_steps")),
        ("Impact",       row.get("attack_impact")),
    ]
    attack_lines = [f"  {label}: {val}" for label, val in attack_parts if val]
    if attack_lines:
        attack_context = "Pre-authored attack intelligence (use as context for description):\n" + "\n".join(attack_lines) + "\n"

    credential_block = ""
    if is_cred:
        credential_block = (
            "\nThis finding relates to a potential hardcoded credential. "
            "Carefully inspect the code snippet and classify the detected value:\n"
            '  - "real_credential"    : an actual secret value (password, key, token, connection string with credentials)\n'
            '  - "placeholder"        : a template/placeholder that is not a real secret (e.g. CHANGE_ME, <PASSWORD>, YOUR_SECRET_HERE, TODO, xxx, ****)\n'
            '  - "variable_reference" : references an external variable/env var/config (e.g. ${PASSWORD}, process.env.SECRET, var.password)\n'
            '  - "example_value"      : obviously fake / test / demo / sample value (e.g. "example", "test123", "demo_pass")\n'
            '  - "unknown"            : cannot determine from the snippet alone\n'
            "Include \"credential_classification\" and a brief \"credential_note\" in the JSON.\n"
            "If the value is a placeholder, variable reference, or example value, reduce severity_score accordingly "
            "(1–3 for placeholder/variable_reference, 3–4 for example_value).\n"
        )

    extra_fields = ""
    if is_cred:
        extra_fields = (
            ',\n  "credential_classification": "<real_credential|placeholder|variable_reference|example_value|unknown>"'
            ',\n  "credential_note": "<brief explanation of classification>"'
        )

    return (
        "You are a security analyst. Enrich this finding with concise, accurate output.\n\n"
        f"Rule: {rule_or_title}\n"
        f"File: {row.get('source_file')}:{row.get('source_line_start')}\n"
        f"Severity: {row.get('base_severity')} ({row.get('severity_score')}/10)\n"
        f"Scanner message: {row.get('reason') or ''}\n"
        f"{attack_context}"
        f"Code snippet:\n```\n{snippet}\n```\n"
        f"{credential_block}\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "title": "short human-readable title (no underscores, ≤60 chars)",\n'
        '  "description": "2–3 sentence explanation of why this is a security risk",\n'
        '  "proposed_fix": "concrete 1–3 sentence remediation",\n'
        '  "severity_score": <integer 1-10, adjust if needed>,\n'
        '  "confidence": <float 0.0-1.0>'
        f"{extra_fields}\n"
        "}"
    )


def main():
    parser = argparse.ArgumentParser(description="LLM-enrich unenriched findings")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts only, no DB writes")
    parser.add_argument("--limit", type=int, default=None, help="Max findings to process")
    args = parser.parse_args()

    query = """
        SELECT id, title, rule_id, source_file, source_line_start,
               base_severity, severity_score, reason, code_snippet,
               attack_chain, attack_tools, attack_chain_steps, attack_impact
        FROM findings
        WHERE experiment_id = ? AND llm_enriched_at IS NULL
        ORDER BY severity_score DESC
    """
    params = [args.experiment]
    if args.limit:
        query += f" LIMIT {args.limit}"

    with db_helpers.get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    print(f"Found {len(rows)} unenriched findings for experiment '{args.experiment}'")

    for row in rows:
        fid = row["id"]
        prompt = _build_prompt(row)

        if args.dry_run:
            print(f"\n--- Prompt for finding {fid} ---\n{prompt}\n")
            continue

        raw = _call_llm(prompt)

        # _call_llm returns a dict (placeholder) — try to extract JSON string if
        # the real integration returns one via 'content' or 'interpretation'.
        enriched = None
        if isinstance(raw, dict):
            # If there's a 'content' key with a JSON string, use it
            candidate = raw.get("content") or raw.get("interpretation") or ""
            try:
                enriched = json.loads(candidate) if isinstance(candidate, str) and candidate.strip().startswith("{") else None
            except (json.JSONDecodeError, TypeError):
                enriched = None
            # Fallback: if raw itself looks like the expected schema, use it directly
            if enriched is None and "title" in raw:
                enriched = raw
        elif isinstance(raw, str):
            try:
                enriched = json.loads(raw)
            except json.JSONDecodeError:
                pass

        if not enriched:
            warnings.warn(f"Finding {fid}: could not parse LLM JSON response, skipping enrichment.")
            continue

        title = enriched.get("title", row.get("title") or row.get("rule_id") or "Finding")
        description = enriched.get("description")
        proposed_fix = enriched.get("proposed_fix")
        new_score = enriched.get("severity_score", row.get("severity_score"))
        credential_classification = enriched.get("credential_classification")
        credential_note = enriched.get("credential_note")

        with db_helpers.get_db_connection() as conn:
            conn.execute("""
                UPDATE findings
                SET title = ?, description = ?, proposed_fix = ?,
                    severity_score = ?, llm_enriched_at = CURRENT_TIMESTAMP,
                    credential_classification = ?, credential_note = ?
                WHERE id = ?
            """, (title, description, proposed_fix, new_score,
                  credential_classification, credential_note, fid))

        # Record provenance: LLM enrichment event for this finding (non-fatal)
        try:
            from cozo_helpers import _insert_relationship_audit
            _insert_relationship_audit(f"finding:{fid}", f"finding:{fid}", "llm_enrichment", action='created', actor_type='llm', actor_id='llm_enrich_findings', scan_id=args.experiment, details_json=json.dumps({"confidence": enriched.get("confidence", None)}))
        except Exception:
            pass

        db_helpers.record_risk_score(fid, new_score, scored_by="llm", rationale=description)
        cred_suffix = f" [{credential_classification}]" if credential_classification else ""
        print(f"Enriched finding {fid}: {title} [{new_score}/10]{cred_suffix}")


if __name__ == "__main__":
    main()
