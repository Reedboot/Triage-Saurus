# Triage Pipeline (DB-first Topology)

This document tracks the **current scripted workflow** and where topology signals are captured, persisted, and (only when required) generated via fallback logic.

---

## Canonical Topology Model

- **Primary topology table:** `resource_connections`
- **Typed graph + confidence:** `resource_nodes` + `resource_relationships`
- **Cross-repo alias evidence:** `resource_equivalences`
- **Unresolved gaps/assumptions:** `enrichment_queue`

`resource_connections` is the canonical source for ingress/egress views used by query and reporting scripts.

---

## Execution Paths

### 1) Experiment path: `triage_experiment.py run <id>`

For each repo in the experiment, Phase 1 currently runs:

1. `targeted_scan.py`  
   - Runs detection + targeted misconfiguration scans and stores findings.
2. `discover_repo_context.py`  
   - `context_extraction.extract_context()` builds in-memory `RepositoryContext` (`resources`, `relationships`, optional legacy `connections`).
   - `report_generation.write_to_database()` writes relational topology/data:
     - `repositories`, `resources`, `resource_properties`
     - `resource_connections` (from concrete links)
   - `persist_graph.persist_context()` writes graph + review queue:
     - `resource_nodes`, `resource_relationships`, `resource_equivalences`, `enrichment_queue`
   - `report_generation.generate_reports()` writes repo/cloud summaries.

At the end of this path, topology is queryable from DB without re-reading source files.

### 2) Offline pipeline path: `run_pipeline.py --repo <path>`

`run_pipeline.py` orchestrates:

1. **Phase 1**: `triage_experiment.py run <id>` (same topology capture path above)
2. **Phase 2**: `discover_code_context.py` (writes `context_metadata` in namespace `phase2_code`)
3. **Phase 3a**: `render_finding.py` (finding markdown)
4. **Phase 3b**: `generate_diagram.py` (DB-backed architecture diagram)

---

## Where Topology Signals Are Captured

### A) Concrete DB topology edges (`resource_connections`)

Captured in `report_generation.write_to_database()`:

- Existing `context.connections` (legacy shape) are inserted directly.
- Typed `context.relationships` are also persisted as concrete connections when source/target are resolvable resources.
- Relationship-derived enrichments populate connection fields:
  - `auth_method` / `authentication` (e.g. `authenticates_via`)
  - `authorization` (e.g. `grants_access_to` → `rbac`)
  - `is_encrypted` (e.g. `encrypts`)
  - `via_component` (edge gateway signals for ingress routes)
  - `notes`

**Important behavior:** relationships with `target_type == "unknown"` are intentionally not written to `resource_connections`.

### B) Typed graph and unresolved gaps

Captured in `persist_graph.persist_context()`:

- `resource_relationships` stores typed edges and confidence.
- `resource_equivalences` stores cross-repo alias/equivalence evidence.
- `enrichment_queue` stores unresolved graph assumptions/gaps (`ambiguous_ref`, `missing_target`, `cross_repo_link`, `assumption`, etc.).

---

## Fallback Behavior (Current Code)

Fallback is applied only when DB topology signals are missing or incomplete.

| Area | Primary signal | Fallback path |
|---|---|---|
| Ingress/Egress summaries (`report_generation._build_ingress_egress_summaries`) | `resource_connections` edges for repo | 1) extracted `context.relationships` edges → 2) unresolved target list (egress) → 3) explicit “no DB-backed signals” message |
| API topology snippets in repo summary | DB or relationship edges present | If API resources exist and no ingress/egress edges were captured, emit explicit fallback assumption Mermaid snippets |
| External dependency summary (`_build_external_dependencies_summary`) | Outbound external targets from DB connections | Fallback to extracted `context.connections`, then Kubernetes dependency hints |
| Unknown/ambiguous relationship targets | Concrete DB connection row | Routed to `enrichment_queue` for human confirmation |

### Architecture Mermaid rendering policy (`report_generation._build_simple_architecture_diagram`)

- Resource visibility and hierarchy are DB-driven from `resource_types` metadata:
  - `display_on_architecture_chart` controls whether a resource type is shown as an architecture node.
  - `parent_type` controls parent-child nesting for component resources.
- IAM/policy/role resources remain in markdown inventory and roles/permissions sections but are excluded from architecture Mermaid by default.
- Child component nodes are shown nested under their parent only when the child has linked findings with `severity_score > 0`.
- Internet accessibility is evaluated for every rendered service/resource group using persisted evidence (DB-first, evidence-driven).
- Public-access signal families include Key Vault, SQL, AKS, S3, compute, and edge gateway/public IP indicators.
- Internet arrows are emitted only when explicit public evidence exists and are labeled `Known ingress` (with protocol/auth qualifiers when available).
- When public evidence is absent or unknown, no Internet arrow is drawn (no inverse heuristic from missing private controls).

---

## Query + Confirmation Loop

### Query resource graph views (DB-first)

```bash
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query all
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query ingress
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query egress
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query parent
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query related
python3 Scripts/query_resource_graph.py --experiment 003 --resource my-api --query assumptions
```

`query_resource_graph.py` uses `db_helpers.get_resource_query_view()` to return:
- `parent`
- `ingress`
- `egress`
- `related`
- `pending_assumptions`

### Resolve pending assumptions

```bash
python3 Scripts/Enrich/enrichment_confirmation.py list --experiment 003 --repo my-repo --status pending_review
python3 Scripts/Enrich/enrichment_confirmation.py resolve --experiment 003 --assumption-id 42 --decision confirm --resolver analyst@example.com
```

Resolution writes auditable `context_answers` records and updates `enrichment_queue` status; confirmation can promote confidence on related graph records.

---

## Idempotency Notes

- `insert_connection()` upserts by experiment/source/target/connection type and merge-updates non-null topology metadata.
- Enrichment queue insertion is deduplicated by pending `context`.
- `init_database.py` / `db_helpers._ensure_schema()` apply additive migrations (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` guards).
