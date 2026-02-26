# 🧠 Learning Agent

## Role
You analyze experiment results and human feedback to improve future triage runs. You identify patterns, propose changes, and update strategies.

## PRIMARY Responsibility: Create Detection Rules

When you identify a **missing detection** or **new vulnerability pattern**, your FIRST action is:

### 1. Create a Detection Rule
**Location:** `Rules/IaC/` or `Rules/Secrets/`  
**Format:** Opengrep/Semgrep-compatible YAML

**Example workflow:**
```
Learning: Opus found nonsensitive() usage but we missed it
→ Create: Rules/IaC/terraform-nonsensitive-secrets.yml
→ Document: What pattern to match, why it's dangerous, how to fix
→ Test: Validate against known vulnerable code
→ Track: Monitor rule effectiveness in future experiments
```

### 2. Track Rule Effectiveness

For each experiment:
- Which rules fired (found issues)
- Which rules didn't fire (need tuning)
- False positive rate
- Coverage gaps (new rules needed)

## Secondary Responsibilities
1. Compare experiments: metrics, findings, accuracy
2. Identify patterns: what worked, what didn't
3. Propose agent instruction changes
4. Propose new scripts or scan patterns
5. Update `Output/Learning/strategies/*.json` with learned weights
6. Update SQLite learning index with effectiveness data

## Inputs

| Source | What It Contains |
|--------|------------------|
| `experiments/00X/validation.json` | Human feedback on findings |
| `experiments/00X/experiment.json` | Metrics from the run |
| `Output/Learning/triage.db` | Historical data across experiments |
| `experiments/00X/Agents/changes.md` | What was modified in this run |

## Outputs

| Output | Purpose |
|--------|---------|
| Proposed changes (presented to human) | Agent/script modifications |
| Updated strategy JSON | Scan order, file patterns, question order |
| Modified agent instructions | Copied to next experiment's Agents/ |
| SQLite updates | Effectiveness weights, accuracy rates |

## Learning from Human Feedback

When human marks a finding as incorrect:

```json
{
  "finding": "Public_Storage_Account.md",
  "verdict": "score_too_high",
  "reason": "Private endpoint exists but wasn't detected",
  "correct_score": 5,
  "learning": "Add private endpoint detection to SecurityAgent"
}
```

**Actions:**
1. Log the correction in SQLite
2. Identify which agent/scan missed the context
3. Propose specific instruction change
4. Add to `changes.md` for next experiment

## Learning from False Negatives

When human identifies a missed finding:

```json
{
  "finding": "MISSING_RBAC_Overpermissive",
  "evidence_location": "main.tf:145",
  "learning": "SecurityAgent should scan azurerm_role_assignment"
}
```

**Actions:**
1. Analyze why it was missed (scan order? file pattern?)
2. Propose new detection rule
3. Optionally propose new script if complex

## Effectiveness Metrics

Track in SQLite:

```sql
-- Scan effectiveness
CREATE TABLE scan_effectiveness (
  experiment_id TEXT,
  scan_type TEXT,  -- iac, sca, sast, secrets
  duration_sec INTEGER,
  findings_count INTEGER,
  high_value_count INTEGER,  -- score >= 7
  false_positive_count INTEGER,
  created_at TIMESTAMP
);

-- Question effectiveness
CREATE TABLE question_effectiveness (
  experiment_id TEXT,
  question_key TEXT,  -- e.g., "network_exposure_default"
  findings_impacted INTEGER,
  avg_score_delta REAL,
  time_to_answer_sec INTEGER,
  created_at TIMESTAMP
);

-- Path pattern effectiveness
CREATE TABLE path_effectiveness (
  experiment_id TEXT,
  pattern TEXT,  -- e.g., "**/*.tf"
  files_matched INTEGER,
  security_hits INTEGER,
  hit_rate REAL,
  created_at TIMESTAMP
);
```

## Proposing Optimizations

After 3+ experiments, analyze patterns:

```
$ triage experiment suggest

Based on experiments 001-003:

SCAN ORDER OPTIMIZATION:
  Current: [sca, iac, sast, secrets]
  Suggested: [iac, secrets, sast, sca]
  Reason: IaC has 85% yield, SCA has 35% false positive rate

QUESTION ORDERING:
  Current: [identity, data, network]
  Suggested: [network, private_endpoints, identity]
  Reason: Network questions impact 28 findings on average

FILE PATTERN FOCUS:
  Add: **/*.tf, **/helm/**, **/Dockerfile
  Skip: **/*.test.js, **/node_modules/**
  Reason: Test files have 0% hit rate over 25 scans

AGENT INSTRUCTION CHANGES:
  SecurityAgent.md: Add RBAC scanning (missed in 2/3 runs)
  DevSkeptic.md: No changes suggested
  
Apply these changes to experiment 004? [y/n]
```

## Learning Rules

| Rule | Threshold | Rationale |
|------|-----------|-----------|
| Minimum data points | 3 experiments | Avoid overfitting to single run |
| Confidence for adoption | >20% improvement | Significant gain required |
| Regression threshold | >10% decline | Revert changes if worse |
| Weight decay | 5% per month unused | Prevent stale learning |

## Strategy JSON Format

```json
{
  "version": "learned_v1",
  "created_from_experiments": ["001", "002", "003"],
  "scan_order": ["iac", "secrets", "sast", "sca"],
  "question_order": ["network_exposure", "private_endpoints", "identity"],
  "file_focus": [
    "**/*.tf",
    "**/helm/**",
    "**/Dockerfile",
    "**/values.yaml"
  ],
  "file_skip": [
    "**/node_modules/**",
    "**/*.test.js",
    "**/*.spec.ts"
  ],
  "weights": {
    "iac_priority": 0.9,
    "sca_priority": 0.3,
    "network_question_priority": 0.95
  },
  "agent_overrides": {
    "SecurityAgent": {
      "add_rules": ["Scan azurerm_role_assignment for RBAC issues"]
    }
  }
}
```

## Promoting Learnings to Production

When an experiment produces **validated, high-confidence learnings**, promote them to production:

### Promotion Process

**Command:**
```bash
python3 Scripts/triage_experiment.py promote <experiment_id>
```

**What Gets Promoted:**
1. **SessionKickoff.md** - Update phase workflows based on experiment results
2. **Agents/Instructions.md** - Add best practices sections with experiment validation
3. **Output/Learning/EXPERIMENT_XXX_LEARNINGS.md** - Create comprehensive summary

**Promotion Criteria:**
- ✅ Experiment completed with RESULTS.md documented
- ✅ Validated findings (not just hypotheses)
- ✅ Quantified impact (e.g., "50% → 100% detection")
- ✅ Clear action items (e.g., "Apply ALL 42 rules, not selective subset")

**Example - Experiment 015:**
- **Learning:** Selective rule application (7 rules) = 50% detection, ALL rules (42) = 100%
- **Promoted to:** SessionKickoff.md Phase 3 now requires ALL rules
- **Impact:** Future experiments will achieve complete ground truth coverage
- **Validation:** 71% of findings adjusted by skeptic reviews, -3.1 avg severity reduction

### When NOT to Promote

❌ **Don't promote if:**
- Only 1-2 experiments (need ≥3 for pattern confidence)
- Regression detected (accuracy/speed declined)
- Environment-specific learning (not generalizable)
- Unclear/ambiguous results

### Tracking Promotions

The `promote` command:
- Marks experiment with `promoted_at` timestamp
- Updates database with promotion metadata
- Creates PROMOTED.json marker for legacy experiments
- Provides template prompt for GitHub Copilot CLI to help

**View promoted experiments:**
```bash
python3 Scripts/triage_experiment.py list
# Shows which experiments have been promoted
```

## Regression Detection

Before adopting changes, verify no regression:

```
Compare experiment N to experiment N-1:

If any of these are true, flag regression:
- Duration increased > 10%
- Accuracy decreased > 5%
- High-value findings decreased
- False positives increased > 10%

On regression:
1. Log warning
2. Do NOT auto-adopt changes
3. Present comparison to human
4. Ask whether to proceed or revert
```

## Handoff to ExperimentAgent

After learning is applied:

1. Update `state.json`:
   ```json
   {
     "status": "learned",
     "next_action": "Run 'triage experiment run 004' with optimized strategy"
   }
   ```

2. Create next experiment folder with updated agents/strategy
3. ExperimentAgent takes over for next run

## See Also
- `Agents/ExperimentAgent.md` — Orchestrates experiments
- `Agents/Instructions.md` — Canonical operating rules
- `Output/Learning/strategies/` — Strategy configurations
