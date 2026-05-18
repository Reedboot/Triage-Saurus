---
name: learning-promote
description: Promote validated learnings from a completed experiment — create detection rules, update strategy JSON, and update agent instructions for future experiments.
---

Promote learnings from a completed experiment into the shared knowledge base.
Full agent guidance is in `Agents/LearningAgent.md`.

## Prerequisites
The experiment must be in `completed` or `awaiting_review` status:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```
Do not promote from an experiment with unreviewed findings — validate human feedback first.

## Running promotion

```bash
python3 Scripts/Experiments/triage_experiment.py promote <experiment_id>
```

This command **does not write files itself** — it prints a structured recommended prompt for the agent to execute. The agent then performs the actual file changes: creating detection rules, updating strategy JSON, and appending to the learning log.

Read the printed prompt carefully and follow it step-by-step.

## What gets promoted — and when

### 1. Detection rules (PRIMARY responsibility)
For every missed detection or new vulnerability pattern found in the experiment:

```bash
# Create rule in Rules/Misconfigurations/ (or Rules/Misconfigurations/Secrets/)
# Format: opengrep/Semgrep-compatible YAML
```

Rule must include: what pattern to match, why it's dangerous, how to fix it.
Validate before promoting:
```bash
opengrep scan --config Rules/Misconfigurations/<new-rule.yml> <target-repo>
```

### 2. Strategy JSON
Update `Output/Learning/strategies/<strategy-name>.json` with learned scan order, question order, file patterns, and agent weight overrides.

### 3. Agent instruction changes
Propose specific, evidence-backed changes to agent `.md` files (e.g., "SecurityAgent — add `azurerm_role_assignment` RBAC scanning, missed in 2/3 runs"). Apply only after human approval.

## Promotion thresholds (from `LearningAgent.md`)
| Rule | Threshold | Rationale |
|---|---|---|
| Minimum data points | 3 experiments | Avoid overfitting to single run |
| Confidence for adoption | >20% improvement | Significant gain required |
| Regression threshold | >10% decline | Revert changes if worse |
| Weight decay | 5% per month unused | Prevent stale learning |

## Regression detection
Before promoting any strategy change, verify it doesn't degrade prior experiment metrics.
If promotion causes >10% decline on a tracked metric, revert and document why.

## Inputs consumed
| Source | Purpose |
|---|---|
| `experiments/<id>/validation.json` | Human feedback on findings |
| `experiments/<id>/experiment.json` | Run metrics |
| Cozo knowledge graph | Historical accuracy data |
| `experiments/<id>/Agents/changes.md` | What was modified this run |

## Output artifacts
```
Rules/Misconfigurations/                        ← new/updated detection rules
Output/Learning/strategies/                     ← updated strategy JSON
Output/Learning/EXPERIMENT_<id>_LEARNINGS.md   ← append-only learning log for this experiment
SessionKickoff.md                               ← updated session guidance (after agent acts on prompt)
Agents/Instructions.md                          ← updated agent rules (after human approval)
```

## Agent review logic
Full learning workflow, effectiveness metrics, strategy JSON format:
`Agents/LearningAgent.md`
