---
name: skeptic-review
description: Run Dev Skeptic and Platform Skeptic reviews on enriched findings to challenge scores, verify exploitability, and populate TL;DR executive summaries (Phase 5).
---

Run both skeptic reviewers over enriched findings for an experiment.
Full agent guidance is in `Agents/DevSkeptic.md` and `Agents/PlatformSkeptic.md`.

## Prerequisites
Phase 4 (LLM enrichment) must be complete. Verify:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```
All findings to review must have `llm_enriched_at IS NOT NULL`.

## Running skeptic reviews

```bash
python3 Scripts/Utils/run_skeptics.py --experiment <id>
```

### Key flags
| Flag | Purpose |
|---|---|
| `--experiment <id>` | Experiment ID — **required** |
| `--repo <name>` | Limit to a single repository (optional) |
| `--reviewer dev\|platform\|security\|all` | Run specific reviewer(s) (default: `all`) |

The script is **idempotent**: it checks `skeptic_reviews` for existing `(finding_id, reviewer_type)` rows and skips already-reviewed findings. Only re-run if new findings were added or context changed.

## What each reviewer checks

### Dev Skeptic (`reviewer_type = 'dev'`)
Focuses on code correctness and realistic exploit paths:
- **Data source tracing** — is the vulnerable input user-controlled or internal-only?
- **Actual mitigations present** — ORM, parameterized queries, template auto-escaping, input validators
- **Test-only dependency scope** — does the vulnerable package only deploy to test environments?
- **Framework-level controls** — does the framework neutralise the finding?

### Platform Skeptic (`reviewer_type = 'platform'`)
Focuses on cloud/IaC controls and platform constraints:
- **Compensating controls in IaC** — WAF, APIM JWT validation, private endpoints, VNet integration
- **SKU/tier requirements** — does the recommended fix require a paid tier or redeploy?
- **Network constraints** — VPN, jump hosts, internal-only routes that affect exploitability
- **Only credit defenses with evidence** — cite the IaC file and line, not assumptions

### Security Skeptic (`reviewer_type = 'security'`)
Runs last. Synthesises the Dev and Platform reviews into a final adjusted score:
- Reads the Dev and Platform reviews already written to DB
- Sets the authoritative `adjusted_score` that becomes the finding's final score
- Writes the TL;DR executive summary when all three reviews are present
- **Run `--reviewer security` alone** if Dev/Platform are already done and only the final synthesis is needed

## Scoring rules
- **Final score = Security skeptic's `adjusted_score`** (incorporates Dev and Platform inputs). Falls back to average of all three scores if the Security reviewer did not run.
- `score_adjustment` = float (negative = downgrade, positive = escalate)
- `recommendation` = `confirm` | `downgrade` | `dismiss` | `escalate`
- **CRITICAL:** Score based on actual exploitable damage with proven defenses, not principle violations alone

### Correct example
> "Down to 5/10 — APIM JWT validation confirmed in `terraform/apim_policies.tf` blocks exploitation"

### Wrong example
> "Keep 9/10 — violates defence-in-depth even though APIM validates JWTs"

## TL;DR population trigger
After **both** Dev and Platform reviews are complete for a finding, populate `## 📊 TL;DR — Executive Summary` in the finding MD:
- Final score with adjustment tracking (Security → Dev → Platform)
- Top 3 priority actions with effort estimates
- Material risks summary (2–3 sentences)
- Reason for any score change

## DB output per review
```
reviewer_type:        'dev' or 'platform'
score_adjustment:     float
adjusted_score:       int 1–10
confidence:           float 0.0–1.0
reasoning:            full analysis
key_concerns:         comma-separated
mitigating_factors:   comma-separated
recommendation:       confirm | downgrade | dismiss | escalate
```
Score history row also written to `risk_score_history` automatically.

## After skeptic reviews
```bash
python3 Scripts/Generate/render_finding.py --experiment <id> --all
python3 Scripts/Generate/generate_diagram.py <id>
```
Then proceed to report generation: `experiment-run` Phase 6, or `cloud-summary` skill.

## Agent review logic
Full reviewer checklists, data source classification, evidence citation rules:
`Agents/DevSkeptic.md` · `Agents/PlatformSkeptic.md`
