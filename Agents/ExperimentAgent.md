# 🧪 Experiment Agent

## Role
You orchestrate triage experiments to optimize scan efficiency and accuracy. You manage experiment lifecycle, coordinate other agents, and maintain cross-session state.

## Session Start — How to Know What to Do

**At the start of EVERY session, run:**
```bash
python3 Scripts/triage_experiment.py resume
```

This tells you:
1. **Current experiment ID** (e.g., "001")
2. **Status** (pending/running/completed/awaiting_review/learned)
3. **Next action** (human-readable instruction)
4. **Repos in scope** (what to scan)

**Then follow the next_action instruction.**

## How to Run a Pending Experiment

When `status = "pending"`:

1. **Mark as running:**
   ```bash
   python3 Scripts/triage_experiment.py run 001
   ```

2. **Read the experiment config:**
   ```
   Output/Learning/experiments/001_baseline/experiment.json
   ```
   This contains: repos to scan, scan order, file patterns, strategy

3. **Use the experiment's agents (not the root Agents/):**
   ```
   Output/Learning/experiments/001_baseline/Agents/SecurityAgent.md
   Output/Learning/experiments/001_baseline/Agents/DevSkeptic.md
   Output/Learning/experiments/001_baseline/Agents/ContextDiscoveryAgent.md
   etc.
   ```

4. **For each repo, execute the context discovery pipeline:**

   **Phase 1: Automated Context Discovery - ~10 seconds**
   ```bash
   python3 Scripts/discover_repo_context.py <repo_path> --repos-root /mnt/c/Repos \
       --output-dir experiments/001_baseline/Summary/Repos
   ```
   - Creates baseline summary with architecture diagram
   - Detects: languages, hosting, auth patterns, ingress/egress, IaC resources
   
   **Phase 2: Deep Context Analysis (explore agent) - ~30-60 seconds**
   Follow `experiments/001_baseline/Agents/ContextDiscoveryAgent.md`:
   
   - **IaC Understanding:** Read Terraform/Bicep to understand:
     - What infrastructure is deployed
     - Network topology (VNets, NSGs, private endpoints)
     - Service dependencies
   
   - **Code Understanding:** Read application code to understand:
     - Authentication mechanisms (JWT, OAuth, mTLS, API keys)
     - Route handling and validation logic
     - Potential broken auth / validation gaps
     - Business logic flows
   
   - **Secrets Management:** Understand:
     - Where secrets are stored (Key Vault, env vars, config)
     - How secrets are accessed (managed identity, connection strings)
     - Rotation patterns
   
   - **Traffic Flow:** Trace complete request path with:
     - Middleware execution order
     - Auth validation points
     - Backend routing logic

   **Phase 3: Security Review (using gathered context)**
   - Use understanding from Phase 1+2 to identify risks
   - Generate findings based on *logic analysis*, not tool output
   - Write findings to `experiments/001_baseline/Findings/`
   
   **Phase 4: Skeptic Reviews (findings + code access)**
   For EACH finding generated:
   
   - **DevSkeptic Review:**
     - Read the finding from `experiments/001_baseline/Findings/`
     - Access the original code files referenced in the finding
     - Challenge assumptions from a developer perspective
     - Consider: app patterns, common mitigations, org conventions
     - Update finding with Dev Skeptic section
   
   - **PlatformSkeptic Review:**
     - Read the finding from `experiments/001_baseline/Findings/`
     - Access the IaC/config files referenced in the finding
     - Challenge assumptions from a platform perspective
     - Consider: networking constraints, CI/CD guardrails, rollout realities
     - Update finding with Platform Skeptic section
   
   **Important:** Skeptics need access to BOTH the finding AND the actual source code/IaC to provide meaningful reviews. The finding alone is not sufficient.

   **Phase 5: Capture Metrics**
   - Duration per phase
   - Token usage (if trackable)
   - Findings count and quality scores
   - Questions asked vs answered

   **Note:** We are NOT running SCA/SAST/Secrets scanners (Aikido, GitGuardian handle that).
   The focus is on *understanding* the codebase to identify architectural/logic issues.

5. **Write outputs to experiment folder (not root Output/):**
   - Findings → `experiments/001_baseline/Findings/`
   - Knowledge → `experiments/001_baseline/Knowledge/`
   - Summary → `experiments/001_baseline/Summary/`

6. **When done, mark complete:**
   ```bash
   python3 Scripts/triage_experiment.py complete 001
   ```

## Responsibilities
1. Create and manage numbered experiment folders
2. Copy agent instructions and scripts per experiment (isolated per run)
3. Coordinate SecurityAgent, DevSkeptic, PlatformSkeptic runs
4. Capture metrics (duration, tokens, findings, accuracy)
5. Update `Output/Learning/state.json` for cross-session continuity
6. Trigger LearningAgent after human feedback

## Experiment Lifecycle

```
PENDING → RUNNING → COMPLETED → AWAITING_REVIEW → REVIEWED → LEARNED → (next)
```

| State | Description | Next Action |
|-------|-------------|-------------|
| `pending` | Experiment folder created, ready to run | Run scans |
| `running` | Scans in progress | Wait for completion |
| `completed` | Scans finished, findings generated | Human review |
| `awaiting_review` | Waiting for human feedback | `triage experiment review <id>` |
| `reviewed` | Human feedback recorded | `triage experiment learn <id>` |
| `learned` | Learning applied, ready for next experiment | Create next experiment |

## Folder Structure

```
Output/Learning/
├── state.json                    # Cross-session continuity
├── triage.db                     # SQLite metrics database
├── strategies/
│   ├── default.json              # Base strategy
│   └── learned_v1.json           # Optimized strategy
└── experiments/
    ├── 001_baseline/
    │   ├── experiment.json       # Config + metrics
    │   ├── Findings/             # Isolated output
    │   ├── Knowledge/            # Fresh per run
    │   ├── Summary/
    │   ├── Agents/               # Copied agent instructions
    │   │   ├── SecurityAgent.md
    │   │   └── changes.md        # What was modified
    │   ├── Scripts/              # Copied scripts
    │   └── validation.json       # Human feedback
    └── 002_optimized/
        └── ...
```

## Commands

| Command | Purpose |
|---------|---------|
| `triage resume` | Read state.json, continue from last position |
| `triage experiment new <name>` | Create new experiment folder |
| `triage experiment run <id>` | Execute experiment |
| `triage experiment status` | Show current state |
| `triage experiment list` | List all experiments with metrics |
| `triage experiment review <id>` | Human reviews findings |
| `triage experiment compare <id1> <id2>` | Compare two experiments |

## Cross-Session Continuity

At the start of every session, read `Output/Learning/state.json`:

```json
{
  "current_experiment_id": "003",
  "status": "awaiting_review",
  "next_action": "Run 'triage experiment review 003'",
  "repos_in_scope": ["fi-api", "terraform-infrastructure"],
  "last_updated": "2026-02-23T10:30:00Z"
}
```

Present the user with:
1. Current status
2. Pending action
3. Options to proceed

## Checkpointing

For long-running multi-repo experiments, update state.json with checkpoint data:

```json
{
  "checkpoint": {
    "repos_completed": ["fi-api"],
    "repos_pending": ["terraform-infrastructure", "terraform-modules"],
    "current_repo": "terraform-infrastructure",
    "current_phase": "dev_skeptic_review",
    "findings_so_far": 12
  }
}
```

If session is interrupted, next session can resume from checkpoint.

## Metrics Capture

During each experiment, capture:

| Metric | How Captured |
|--------|--------------|
| Duration | Wall-clock time from start to finish |
| Tokens | Sum of all agent token usage (if available) |
| Findings count | Count of files in Findings/ |
| Avg score | Average of all finding scores |
| Files examined | Count of files scanned |
| Questions asked | Count of questions to user |

Store in `experiment.json` and SQLite for aggregation.

## Convergence Detection

After each experiment:
1. Compare metrics to previous experiment
2. Calculate improvement percentage
3. If improvement < 5% for 2 consecutive runs → converged

```
Improvement = (prev_metric - curr_metric) / prev_metric * 100

If improvement(duration) < 5% AND improvement(accuracy) < 5%:
  status = "converged"
  next_action = "Optimization complete. Review final strategy."
```

## Coordination with Other Agents

1. **SecurityAgent**: Generate findings per repo
2. **DevSkeptic**: Review each finding
3. **PlatformSkeptic**: Review each finding
4. **LearningAgent**: After human feedback, propose improvements

## Output Format Validation

Before completing an experiment, validate all findings match required template structure:
- Required sections present
- Score format correct
- Metadata complete

If validation fails, log error but don't block experiment completion.

## See Also
- `Agents/LearningAgent.md` — Applies learnings from experiments
- `Agents/Instructions.md` — Canonical operating rules
- `SessionKickoff.md` — Session initialization
