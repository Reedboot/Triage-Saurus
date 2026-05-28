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

# Run dev + platform first, then security (security should incorporate the other skeptics).
REVIEWER_TYPES = ["dev", "platform", "security"]

ROLE_INTROS = {
    "security": "You are a Security Engineer reviewing a finding. Be concise.",
    "dev": "You are a Developer Skeptic reviewing a finding. Be concise.",
    "platform": "You are a Platform/Cloud Engineer reviewing a finding. Be concise.",
}


def _load_cloud_posture(conn, experiment_id: str) -> dict | None:
    """Load cloud infrastructure posture from context_metadata."""
    try:
        row = conn.execute(
            """
            SELECT value FROM context_metadata
            WHERE experiment_id = ? AND namespace = 'cloud_posture' AND key = 'posture_json'
            ORDER BY id DESC LIMIT 1
            """,
            (experiment_id,),
        ).fetchone()
        if row and row["value"]:
            return json.loads(row["value"])
    except Exception:
        pass
    return None


def _cloud_posture_block(posture: dict) -> str:
    """Format cloud posture as a concise context block for skeptic prompts."""
    if not posture:
        return ""
    parts = []
    if posture.get("behind_waf"):
        parts.append(f"WAF via App Gateway '{posture.get('app_gateway_name', '?')}': YES")
    elif posture.get("behind_app_gateway"):
        parts.append(f"App Gateway (no WAF) '{posture.get('app_gateway_name', '?')}': YES")
    else:
        parts.append("Direct internet exposure (no App Gateway/WAF): YES")
    if posture.get("behind_apim"):
        parts.append(f"APIM '{posture.get('apim_name', '?')}': YES")
    if posture.get("aks_cluster"):
        secured = "IP-restricted" if posture.get("aks_secured") else "public API server"
        parts.append(f"AKS '{posture['aks_cluster']}' ({secured})")
    parts.append(f"Endpoint exposure: {posture.get('endpoint_exposure', 'unknown')}")
    return "Cloud infrastructure context: " + "; ".join(parts) + "\n"


def _build_prompt(row: dict, reviewer: str, peer_reviews: list[dict] | None = None, cloud_posture: dict | None = None) -> str:
    snippet = (row.get("code_snippet") or "").strip()
    intro = ROLE_INTROS[reviewer]
    finding_title = row.get("title") or row.get("rule_id") or "Finding"

    peer_block = ""
    if reviewer == "security" and peer_reviews:
        # Security should explicitly incorporate dev/platform skeptical views when setting final severity.
        peer_lines = []
        for pr in peer_reviews:
            rt = pr.get("reviewer_type")
            adj = pr.get("adjusted_score")
            conf = pr.get("confidence")
            rec = pr.get("recommendation")
            reason = (pr.get("reasoning") or "").strip()
            peer_lines.append(f"- {rt}: adjusted_score={adj}/10, confidence={conf}, recommendation={rec}; reasoning={reason}")
        peer_block = "Peer skeptic reviews (incorporate these):\n" + "\n".join(peer_lines) + "\n\n"

    cred_block = ""
    cred_class = row.get("credential_classification")
    cred_note = row.get("credential_note") or ""
    if cred_class:
        cred_block = (
            f"AI credential classification: {cred_class}"
            + (f" — {cred_note}" if cred_note else "")
            + "\n"
        )
        if cred_class in ("placeholder", "variable_reference", "example_value"):
            cred_block += (
                "The enrichment AI determined this is NOT a real credential value. "
                "Unless the snippet clearly shows a real secret, recommend 'dismiss' or 'downgrade' "
                "and note this as a false positive from the scanner.\n"
            )

    posture_block = _cloud_posture_block(cloud_posture) if cloud_posture else ""

    return (
        f"{intro}\n\n"
        f"Title: {finding_title}\n"
        f"Description: {row.get('description') or ''}\n"
        f"Base/current severity score: {row.get('severity_score')}/10\n"
        f"File: {row.get('source_file')}\n"
        f"Code snippet:\n```\n{snippet}\n```\n"
        f"Proposed fix: {row.get('proposed_fix') or ''}\n"
        f"{cred_block}\n"
        f"{posture_block}"
        f"{peer_block}"
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
               source_file, code_snippet, proposed_fix, reason,
               credential_classification, credential_note
        FROM findings
        WHERE experiment_id = ? AND llm_enriched_at IS NOT NULL
        ORDER BY severity_score DESC
    """
    params = [args.experiment]
    if args.limit:
        query += f" LIMIT {args.limit}"

    with db_helpers.get_db_connection() as conn:
        findings = [dict(r) for r in conn.execute(query, params).fetchall()]
        cloud_posture = _load_cloud_posture(conn, args.experiment)

    if cloud_posture:
        print(f"[cloud-posture] waf={cloud_posture.get('behind_waf')}, apim={cloud_posture.get('behind_apim')}, exposure={cloud_posture.get('endpoint_exposure')}")

    print(f"Found {len(findings)} enriched findings for experiment '{args.experiment}'")

    for row in findings:
        fid = row["id"]
        adjusted_scores = []
        reviewer_scores = {}

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

            prompt = _build_prompt(row, reviewer, cloud_posture=cloud_posture)

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
            reviewer_scores[reviewer] = adjusted_score
            print(f"  [reviewed] finding {fid} by {reviewer}: {adjusted_score}/10 ({recommendation})")

        # If all 3 reviewers completed, set final score.
        # Security is expected to incorporate dev+platform views, so use Security's adjusted score as final.
        if not args.dry_run and len(reviewer_scores) == 3:
            final_score = reviewer_scores.get("security")
            if final_score is None:
                final_score = round(sum(reviewer_scores.values()) / len(reviewer_scores))
            with db_helpers.get_db_connection() as conn:
                conn.execute(
                    "UPDATE findings SET severity_score = ? WHERE id = ?",
                    (final_score, fid),
                )
            try:
                db_helpers.record_risk_score(
                    fid,
                    final_score,
                    scored_by="skeptic_final",
                    rationale="Final severity set from security skeptic (after dev+platform review).",
                )
            except Exception:
                pass
            print(f"Final score for finding {fid}: {final_score}")


if __name__ == "__main__":
    main()
