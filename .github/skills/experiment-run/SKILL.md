---
name: experiment-run
description: Orchestrate a full 6-phase triage experiment — context discovery, code context, opengrep scan, LLM enrichment, skeptic reviews, and report generation.
---

Run or resume a triage experiment. Full agent guidance is in `Agents/ExperimentAgent.md`.

## Always start here — check current state

```bash
python3 Scripts/Experiments/triage_experiment.py resume
```

This returns: current experiment ID, status, next action, and repos in scope. **Follow the `next_action` instruction before doing anything else.**

## Subcommands

| Subcommand | Purpose |
|---|---|
| `resume` | Check current experiment state and get next action |
| `run <id>` | Mark experiment as running and begin the pipeline |
| `status <id>` | Show experiment status and phase progress |
| `list` | List all experiments with status |
| `promote <id>` | Promote learnings from a completed experiment (see `learning-promote` skill) |

## 6-Phase pipeline

Run phases in order. **After each phase**, always execute:
```bash
python3 Scripts/Generate/render_finding.py --experiment <id> --all
python3 Scripts/Generate/generate_diagram.py <id>
```
This keeps MD files and diagrams in sync with the DB at all times.

---

### Phase 1 — Automated context discovery (no LLM, ~10s per repo)
```bash
python3 Scripts/Context/discover_repo_context.py <repo_path> \
  --repos-root /mnt/c/Repos \
  --output-dir Output/Learning/experiments/<id>_<name>
```
→ Then run `architecture-validation` skill to catch hierarchy/ingress issues before proceeding.

### Phase 2 — Deeper code context (no LLM, ~30–60s per repo)
```bash
python3 Scripts/Context/discover_code_context.py \
  --experiment <id> --repo <repo_name> --target <repo_path> \
  --output-dir Output/Learning/experiments/<id>_<name>
```

### Phase 3 — Rules-based infrastructure scan (~5–10 min)
`triage_experiment.py run` handles this automatically. Manual run if needed:
```bash
opengrep scan --config Rules/Misconfigurations/ <repo_path> \
  --json --quiet | \
  python3 Scripts/Persist/store_findings.py --stdin-json \
  --experiment <id> --repo <repo_name>
```
**CRITICAL:** Apply ALL detection rules (`Rules/Misconfigurations/` — every subfolder). Partial rule sets produce incomplete coverage.

### Phase 4 — LLM enrichment (run ONCE, idempotent)
```bash
python3 Scripts/Enrich/enrich_findings.py --experiment <id>
```
**Rule:** After Phase 4 runs once, **never re-enrich**. The script checks `llm_enriched_at IS NOT NULL` and skips already-enriched findings automatically.

### Phase 5 — Skeptic reviews (idempotent per finding)
```bash
python3 Scripts/Utils/run_skeptics.py --experiment <id>
```
Runs Dev Skeptic + Platform Skeptic. Skips findings already reviewed (checks `skeptic_reviews` table for existing `(finding_id, reviewer_type)` rows). Only re-run if new findings were added.

Final score = average of Security + Dev + Platform scores.

### Phase 6 — Report generation
```bash
python3 Scripts/Generate/report_generation.py --experiment <id>
python3 Scripts/Utils/risk_register.py
```

## Key invariants
- **Phase 2 before Phase 3** — code context informs which misconfig rule folders to target.
- **Never re-enrich after Phase 4** — check `llm_enriched_at IS NOT NULL`.
- **Never re-review after Phase 5** — check `(finding_id, reviewer_type)` in `skeptic_reviews`.
- Use the **experiment's own agent copies** (`Output/Learning/experiments/<id>_<name>/Agents/`) not root `Agents/`.

## Output artifacts
```
Output/Learning/experiments/<id>_<name>/
  experiment.json                    ← experiment config, status, metrics
  Summary/Repos/<RepoName>.md        ← per-repo architecture + context
  Findings/<id>-<slug>.md            ← one file per finding
  Summary/Cloud/<Provider>/          ← cloud resource type summaries
  Risk Register.xlsx                 ← executive risk register
```

## After the experiment
Promote learnings with the `learning-promote` skill.

## Agent review logic
Full 6-phase pipeline, idempotency rules, render-after-each-phase requirement:
`Agents/ExperimentAgent.md`
