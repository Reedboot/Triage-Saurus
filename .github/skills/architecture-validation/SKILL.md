---
name: architecture-validation
description: Validate generated architecture diagrams for hierarchy issues, missing internet ingress, network segmentation gaps, and missing components.
---

Validate architecture diagrams produced by context discovery against the checklist in `Agents/ArchitectureValidationAgent.md`.

## Prerequisites
Before running, confirm an experiment exists and Phase 1 (context discovery) has completed:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```
Do not pass an experiment ID to `resume` — it reads active state automatically.
If the experiment status is `pending` or Phase 1 has not run, run context discovery first (see `context-discovery` skill).

## Running the validation

```bash
python3 Scripts/Validate/validate_architecture.py \
  --experiment <id> \
  --repo <repo_name>
```

### Key flags
| Flag | Purpose |
|---|---|
| `--experiment <id>` | Experiment ID (e.g. `001`) — **required** |
| `--repo <name>` | Repository name to validate — **required** |
| `--output <path>` | Override output path for `validation_report.md` |

To validate all repos in an experiment, run once per repo (loop over the repos listed in `experiment.json`).

## What is validated
The script checks six categories — read `Agents/ArchitectureValidationAgent.md` for the full checklist:

1. **Hierarchy** (CRITICAL) — child resources flat when parent exists (e.g. APIM APIs without parent APIM)
2. **Internet ingress** (CRITICAL) — publicly accessible resources with no `🌐 Internet` node in diagram
3. **Egress** — outbound connections to external services not documented
4. **Missing components** — expected resource types absent for the detected repo type (API / event-driven / data pipeline)
5. **Network segmentation** — public/app/data zones not separated; direct Internet → Database paths
6. **Cross-cloud consistency** — equivalent patterns missing across providers

## Decision gate
| Severity | Action |
|---|---|
| CRITICAL | Fix detection scripts before continuing to Phase 2+ |
| HIGH | Document in experiment notes; proceed with caution |
| MEDIUM / LOW | Continue; track for future improvement |

## Output artifacts
```
Output/Learning/experiments/<id>/
  validation_report.md   ← human-readable issue list with recommended fixes

Override with --output <path> if needed.
```

## After the validation
1. For CRITICAL issues — update `Scripts/Context/external_resource_hierarchy.py` or `Scripts/Context/discover_repo_context.py` as directed in the report, then re-run.
2. Log learnings to the Cozo DB (the script does this automatically).
3. Proceed to Phase 2+ or use the `experiment-run` skill to advance the pipeline.

## Agent review logic
Full validation checklist, provider-specific hierarchy patterns, and fix guidance:
`Agents/ArchitectureValidationAgent.md`
