#!/usr/bin/env python3
"""Phase 3 pipeline: run skeptic reviews on enriched findings.

Usage:
    python3 Scripts/run_skeptics.py --experiment <id>
        [--reviewer security|dev|platform|all]
        [--dry-run] [--limit N]
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Enrich"))
import db_helpers
from llm_resource_interpreter import _call_llm

REVIEWER_TYPES = ["security", "dev", "platform"]

ROLE_INTROS = {
    "security": "You are a Security Engineer reviewing a finding. Be concise.",
    "dev": "You are a Developer Skeptic reviewing a finding. Be concise.",
    "platform": "You are a Platform/Cloud Engineer reviewing a finding. Be concise.",
}


def _build_prompt(row: dict, reviewer: str) -> str:
    snippet = (row.get("code_snippet") or "").strip()
    intro = ROLE_INTROS[reviewer]
    finding_title = row.get("title") or row.get("rule_id") or "Finding"
    return (
        f"{intro}\n\n"
        f"Title: {finding_title}\n"
        f"Description: {row.get('description') or ''}\n"
        f"Severity score: {row.get('severity_score')}/10\n"
        f"File: {row.get('source_file')}\n"
        f"Code snippet:\n```\n{snippet}\n```\n"
        f"Proposed fix: {row.get('proposed_fix') or ''}\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "score_adjustment": <float, positive=escalate / negative=downgrade>,\n'
        '  "adjusted_score": <integer 1-10>,\n'
        '  "confidence": <float 0.0-1.0>,\n'
        '  "reasoning": "<string>",\n'
        '  "key_concerns": "<string>",\n'
        '  "mitigating_factors": "<string>",\n'
        '  "recommendation": "confirm"|"downgrade"|"dismiss"|"escalate"\n'
        "}"
    )


def _parse_llm_response(raw) -> dict | None:
    """Extract a dict from the (possibly placeholder) LLM response."""
    if isinstance(raw, dict):
        candidate = raw.get("content") or raw.get("interpretation") or ""
        try:
            parsed = json.loads(candidate) if isinstance(candidate, str) and candidate.strip().startswith("{") else None
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if parsed is None and "adjusted_score" in raw:
            parsed = raw
        return parsed
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Run skeptic reviews on enriched findings")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument(
        "--reviewer", default="all",
        choices=REVIEWER_TYPES + ["all"],
        help="Which reviewer(s) to run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print prompts only")
    parser.add_argument("--limit", type=int, default=None, help="Max findings to process")
    args = parser.parse_args()

    reviewers = REVIEWER_TYPES if args.reviewer == "all" else [args.reviewer]

    # Fetch enriched findings
    query = """
        SELECT id, rule_id, title, description, severity_score,
               source_file, code_snippet, proposed_fix, reason
        FROM findings
        WHERE experiment_id = ? AND llm_enriched_at IS NOT NULL
        ORDER BY severity_score DESC
    """
    params = [args.experiment]
    if args.limit:
        query += f" LIMIT {args.limit}"

    with db_helpers.get_db_connection() as conn:
        findings = [dict(r) for r in conn.execute(query, params).fetchall()]

    print(f"Found {len(findings)} enriched findings for experiment '{args.experiment}'")

    for row in findings:
        fid = row["id"]
        adjusted_scores = []

        for reviewer in reviewers:
            # Skip if review already exists
            with db_helpers.get_db_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM skeptic_reviews WHERE finding_id = ? AND reviewer_type = ?",
                    (fid, reviewer),
                ).fetchone()
            if existing:
                print(f"  [skip] finding {fid} already reviewed by {reviewer}")
                # Still collect score for final average
                with db_helpers.get_db_connection() as conn:
                    score_row = conn.execute(
                        "SELECT adjusted_score FROM skeptic_reviews WHERE finding_id = ? AND reviewer_type = ?",
                        (fid, reviewer),
                    ).fetchone()
                if score_row:
                    adjusted_scores.append(score_row[0])
                continue

            prompt = _build_prompt(row, reviewer)

            if args.dry_run:
                print(f"\n--- {reviewer} prompt for finding {fid} ---\n{prompt}\n")
                continue

            raw = _call_llm(prompt)
            parsed = _parse_llm_response(raw)

            if not parsed:
                warnings.warn(f"Finding {fid} / {reviewer}: could not parse LLM response, skipping.")
                continue

            score_adjustment = float(parsed.get("score_adjustment", 0.0))
            adjusted_score = parsed.get("adjusted_score", row["severity_score"])
            confidence = float(parsed.get("confidence", 0.5))
            reasoning = parsed.get("reasoning", "")
            key_concerns = parsed.get("key_concerns")
            mitigating_factors = parsed.get("mitigating_factors")
            recommendation = parsed.get("recommendation", "confirm")

            db_helpers.store_skeptic_review(
                finding_id=fid,
                reviewer_type=reviewer,
                score_adjustment=score_adjustment,
                adjusted_score=adjusted_score,
                confidence=confidence,
                reasoning=reasoning,
                key_concerns=key_concerns,
                mitigating_factors=mitigating_factors,
                recommendation=recommendation,
            )
            db_helpers.record_risk_score(
                fid, adjusted_score,
                scored_by=f"{reviewer}_skeptic",
                rationale=reasoning,
            )
            adjusted_scores.append(adjusted_score)
            print(f"  [reviewed] finding {fid} by {reviewer}: {adjusted_score}/10 ({recommendation})")

        # If all 3 reviewers completed, set final averaged score
        if not args.dry_run and len(adjusted_scores) == 3:
            final_score = round(sum(adjusted_scores) / len(adjusted_scores))
            with db_helpers.get_db_connection() as conn:
                conn.execute(
                    "UPDATE findings SET severity_score = ? WHERE id = ?",
                    (final_score, fid),
                )
            print(f"Final score for finding {fid}: {final_score}")


if __name__ == "__main__":
    main()
