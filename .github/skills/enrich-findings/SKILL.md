---
name: enrich-findings
description: LLM-enrich raw scanner findings with titles, descriptions, proposed fixes, and severity scores (Phase 4 of the triage pipeline).
---

Enrich unenriched findings for an experiment using the SecurityAgent review logic.
Full agent guidance is in `Agents/SecurityAgent.md`.

## Prerequisites
Phases 1–3 must be complete (context discovery + opengrep scan). Verify:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```

## Running enrichment

```bash
python3 Scripts/Enrich/enrich_findings.py --experiment <id>
```

### Key flags
| Flag | Purpose |
|---|---|
| `--experiment <id>` | Experiment ID — **required** |
| `--dry-run` | Preview what would be enriched without writing |
| `--limit <n>` | Cap the number of findings to enrich in this run |

The script is **idempotent**: it skips any finding where `llm_enriched_at IS NOT NULL`. **Never force re-enrichment** — this overwrites reviewed scores.

## After enrichment — render finding MD files
```bash
python3 Scripts/Generate/render_finding.py --experiment <id> --all
python3 Scripts/Generate/generate_diagram.py <id>
```

## SecurityAgent enrichment rules
When enriching findings, follow these rules from `Agents/SecurityAgent.md`:

### Scoring environment rule
**NEVER reduce scoring for dev/lab/training/CTF environments** for inherently critical issues.

**Always CRITICAL (9–10/10) regardless of environment:**
- Anonymous storage with credentials
- Direct internet-exposed management interfaces (RDP/SSH 0.0.0.0/0)
- Hardcoded credentials in public repositories
- Database with no authentication exposed to internet
- Administrative credentials in plaintext on public site

### Attack-path tracing
For each finding, trace a realistic attack path:
1. **Entry point** — how does an attacker reach the vulnerable resource?
2. **Exploitation** — what do they do with access?
3. **Blast radius** — what can they pivot to from here?

### Data source classification (injection vulnerabilities)
- **User-controlled input** → directly exploitable (High/Critical)
- **Internal/trusted sources** → requires prior compromise (Medium, compounding)

## Output
Each finding gets written to DB with:
- `title`, `description`, `proposed_fix`
- `severity_score` (1–10)
- `llm_enriched_at` timestamp

And rendered to: `Output/Learning/experiments/<id>_<name>/Findings/<id>-<slug>.md`

## Next step
Run skeptic reviews: `skeptic-review` skill.

## Agent review logic
Full SecurityAgent scoring rules, environment handling, and attack-chain tracing:
`Agents/SecurityAgent.md`
