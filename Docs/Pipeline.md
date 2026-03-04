# Triage Pipeline

Describes the four-phase scan pipeline, which scripts run at each phase, and idempotency rules.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 1 — Scripts only (zero LLM)                                  │
│                                                                     │
│  opengrep scan ──────────────────► findings (raw)                   │
│  store_findings.py               (rule_id, source_file, snippet,    │
│                                   reason, severity_score)           │
│  discover_repo_context.py ───────► resources                        │
│                                    resource_properties              │
│                                    resource_connections             │
│                                    trust_boundaries                 │
│                                    repositories                     │
│                                                                     │
│  generate_diagram.py ────────────► Architecture diagram (Mermaid)  │
│  risk_register.py ───────────────► Finding list (raw scores)        │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 2 — LLM enrichment (once per finding)                        │
│                                                                     │
│  enrich_findings.py queries: findings WHERE llm_enriched_at IS NULL │
│  LLM writes back:                                                   │
│    findings     ── title, description, proposed_fix, severity_score │
│    remediations ── fix description, effort, priority                │
│    data_flows   ── named flows (auth, API request, ingress)         │
│    data_flow_steps ─ ordered hops with auth/encryption per step     │
│    resource_connections ─ auth_method, is_encrypted updates         │
│    risk_score_history   ─ initial score snapshot (scored_by='llm')  │
│                                                                     │
│  Sets llm_enriched_at timestamp — never re-processed after this.   │
│                                                                     │
│  render_finding.py ──────────────► Enriched finding MD files        │
│  generate_diagram.py ────────────► Architecture diagram (annotated) │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 3 — Skeptic reviews (one row per reviewer per finding)       │
│                                                                     │
│  run_skeptics.py --reviewer dev      → skeptic_reviews (role=dev)   │
│  run_skeptics.py --reviewer platform → skeptic_reviews (role=plat.) │
│  run_skeptics.py --reviewer security → skeptic_reviews (role=sec.)  │
│  Each review also writes: risk_score_history snapshot               │
│  When all 3 done: findings.severity_score = avg(adjusted scores)    │
│                                                                     │
│  render_finding.py ──────────────► Final scored finding MD files    │
│  risk_register.py ───────────────► Risk register (final scores)     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 4 — Reporting (scripts only, zero LLM)                       │
│                                                                     │
│  generate_diagram.py ────────────► Mermaid architecture diagrams    │
│  risk_register.py ───────────────► Risk register spreadsheet        │
│  render_finding.py ──────────────► Individual finding MD files      │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SUBSEQUENT RUNS — only re-enrich if invalidated                    │
│                                                                     │
│  New opengrep hit    → new findings row → LLM enriches that row     │
│  New context answer  → re-score affected findings only              │
│  New countermeasure  → adjust score, append risk_score_history row  │
│  No changes          → all reports from DB, zero LLM calls          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Scripts Reference

| Script | Phase | Reads | Writes |
|--------|-------|-------|--------|
| `triage_experiment.py run` | 1 | experiment config | orchestrates phases 1–4 |
| `targeted_scan.py` | 1 | repo files | calls detection scan → targeted misconfig scan → `store_findings.py` |
| `discover_repo_context.py` | 1 | repo files (IaC, code) | `resources`, `resource_properties`, `resource_connections`, `trust_boundaries`, `repositories` |
| `store_findings.py` | 1 | opengrep JSON | `findings` (raw), `risk_score_history` (scored_by='script') |
| `enrich_findings.py` | 2 | `findings WHERE llm_enriched_at IS NULL` | `findings` enrichment cols, `remediations`, `data_flows`, `data_flow_steps`, `risk_score_history` |
| `run_skeptics.py` | 3 | enriched `findings` | `skeptic_reviews`, `risk_score_history`; updates `findings.severity_score` |
| `render_finding.py` | 4 | `findings` + DB | individual finding MD files |
| `generate_diagram.py` | 4 | `resources`, `resource_connections`, `findings` | Mermaid diagram MD files |
| `risk_register.py` | 4 | `findings`, `resources` | risk register report |
| `init_database.py` | Setup | — | creates/migrates all tables, seeds `providers` + `resource_types` |

---

## Idempotency Rules

All write scripts are safe to re-run:

| Script | Skip condition |
|--------|---------------|
| `store_findings.py` | `(experiment_id, rule_id, source_file, source_line_start)` already exists |
| `enrich_findings.py` | `findings.llm_enriched_at IS NOT NULL` |
| `run_skeptics.py` | `skeptic_reviews` row for `(finding_id, reviewer_type)` already exists |
| `discover_repo_context.py` | Uses `INSERT OR REPLACE` — safe to re-run, updates in place |

---

## Running a Full Scan

```bash
# Recommended — targeted_scan.py handles both detection and misconfigurations automatically
python3 Scripts/targeted_scan.py /path/to/repo --experiment 003 --repo terragoat

# Or via the experiment orchestrator (calls targeted_scan.py internally)
python3 Scripts/triage_experiment.py run 003

# Dry-run to preview which misconfig folders would be targeted
python3 Scripts/targeted_scan.py /path/to/repo --experiment 003 --repo terragoat --dry-run

# If targeted_scan was already run but store_findings wasn't called:
python3 Scripts/store_findings.py \
    Output/Learning/experiments/003_a_new_dawn/scan_terragoat.json \
    --experiment 003 --repo terragoat

# Phase 2 — LLM enrichment (run once)
python3 Scripts/enrich_findings.py --experiment 003

# Phase 3 — Skeptic reviews (run once)
python3 Scripts/run_skeptics.py --experiment 003 --reviewer all

# Phase 4 — Reports from DB (instant, no LLM)
python3 Scripts/generate_diagram.py --experiment-id 003
python3 Scripts/risk_register.py

# Dry-run any phase to preview without writing
python3 Scripts/enrich_findings.py --experiment 003 --dry-run
python3 Scripts/run_skeptics.py --experiment 003 --reviewer dev --dry-run
```

---

## Maintenance

```bash
# Initialise or migrate schema (safe on existing DB — CREATE IF NOT EXISTS + ALTER TABLE)
python3 Scripts/init_database.py

# Backup before migrations
cp Output/Learning/triage.db Output/Learning/triage_backup_$(date +%Y%m%d).db

# Check finding counts per experiment
python3 -c "
import sqlite3
conn = sqlite3.connect('Output/Learning/triage.db')
for row in conn.execute('SELECT experiment_id, COUNT(*) FROM findings GROUP BY experiment_id').fetchall():
    print(row)
"

# Check enrichment status
python3 -c "
import sqlite3
conn = sqlite3.connect('Output/Learning/triage.db')
for row in conn.execute('''
    SELECT experiment_id,
           COUNT(*) total,
           SUM(CASE WHEN llm_enriched_at IS NOT NULL THEN 1 ELSE 0 END) enriched
    FROM findings GROUP BY experiment_id
''').fetchall():
    print(row)
"
```
