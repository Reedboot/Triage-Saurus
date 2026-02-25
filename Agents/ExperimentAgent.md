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
       --output-dir Output/Learning/experiments/001_baseline
   ```
   - Creates baseline summary with architecture diagram at `experiments/<id>/Summary/Repos/<RepoName>.md`
   - Creates/updates knowledge at `experiments/<id>/Knowledge/Repos.md`
   - Detects: languages, hosting, auth patterns, ingress/egress, IaC resources
   - **Captures parent-child hierarchies:** SQL Server → Databases, Storage Account → Containers, AKS → Namespaces
   - **Populates parent_resource_id in database** for downstream attack path analysis
   
   **Validate hierarchies detected:**
   ```bash
   python3 -c "
   import sqlite3
   conn = sqlite3.connect('Output/Learning/triage.db')
   cursor = conn.cursor()
   cursor.execute('''
     SELECT parent.resource_name, parent.resource_type, 
            COUNT(child.id) AS child_count
     FROM resources parent
     LEFT JOIN resources child ON child.parent_resource_id = parent.id
     WHERE parent.experiment_id = '001'
     GROUP BY parent.id
     HAVING child_count > 0
   ''')
   for row in cursor.fetchall():
       print(f'{row[0]} ({row[1]}): {row[2]} children')
   "
   ```
   - Example output: "tycho (SQLServer): 2 children", "storage_labpallas (StorageAccount): 3 children"
   
   **Phase 2: Deeper Context Search (explore agent) - ~30-60 seconds**
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

   **Phase 3: Rules-Based Infrastructure Scanning (~5-10 minutes)**
   
   **CRITICAL:** Apply ALL detection rules to achieve complete coverage.
   
   **For IaC scanning (Terraform, Bicep, CloudFormation):**
   ```bash
   # Run all IaC rules (currently ~42 rules)
   semgrep --config Rules/IaC/ <repo_path> --json -o findings_iac.json
   
   # Verify all rules ran
   ls -1 Rules/IaC/*.yml | wc -l
   ```
   
   **For each finding from semgrep:**
   1. Create individual finding document in `experiments/001_baseline/Findings/Cloud/`
   2. Follow `Templates/CloudFinding.md` template
   3. Include:
      - Resource name and type
      - Rule ID that detected it
      - Evidence location (file, line number)
      - Technical severity (from rule metadata)
      - Architecture diagram showing resource context
   4. **Use hierarchies for targeted rule application:**
      - Apply SQL TLS rules to all databases with SQL Server parent
      - Apply blob public access rules to all containers with Storage Account parent
      - Apply pod security rules to all namespaces with AKS cluster parent
      - Query database: `SELECT * FROM resources WHERE parent_resource_id = (SELECT id FROM resources WHERE resource_name = 'tycho')`
   4. Leave Skeptic sections BLANK (filled in Phase 5)
   
   **Expected Results:**
   - Detection rate: ~80-90% of ground truth vulnerabilities
   - Time: 5-10 minutes for 40+ rules
   - Output: Individual finding files ready for skeptic review
   
   **Validation checkpoint:**
   ```bash
   # Verify findings were created
   ls experiments/001_baseline/Findings/Cloud/*.md | wc -l
   
   # Verify rule coverage
   grep "Rule:" experiments/001_baseline/Findings/Cloud/*.md | sort -u
   ```
   
   **Key Learning from Experiment 015:**
   - Selective rule application achieves only 50% detection
   - Applying ALL rules achieves 86%+ detection
   - Missing rules for: terraform-hardcoded-keyvault-secret, terraform-nonsensitive-secrets
   - These gaps are now filled (rules created 2026-02-25)

   **Phase 4: Security Review (using gathered context)**
   Follow `experiments/001_baseline/Agents/SecurityAgent.md`:
   
   **Phase 4: Security Review (using gathered context)**
   Follow `experiments/001_baseline/Agents/SecurityAgent.md`:
   
   **Step 4a: Review repo summary security observations**
   - Phase 2 has documented security findings in `Summary/Repos/<RepoName>.md`
   - Review the "Security Observations" section for all identified issues
   
   **Step 4b: Extract findings as individual files** (MANDATORY)
   - For **MEDIUM+ severity** findings documented in repo summary:
     - Create individual finding file: `experiments/001_baseline/Findings/Code/<RepoName>_<Issue>_<Number>.md`
     - Include architecture diagram showing attack path
     - Copy finding details (location, issue, attack vector, mitigations)
     - Add POC script section (if exploitable - use guidance from SecurityAgent.md)
     - Leave Skeptic sections blank (filled in Phase 5)
   - For **LOW/INFO** findings: leave in repo summary only
   
   **Example extraction:**
   ```
   Repo summary finding:
   "Pre-Validation Logging Side Effect (MEDIUM) - Request paths logged BEFORE validation"
   
   →  Extract to: experiments/001_baseline/Findings/Code/FI_API_001_Pre_Validation_Logging.md
   →  Include: POC showing path enumeration attack
   →  Leave blank: Dev Skeptic and Platform Skeptic sections
   ```
   
   **Step 4c: Additional logic review** (if time permits):
   Focus on **logic review**, not just documented findings:
   
   **Authentication Flow Analysis:**
   - Trace the complete auth flow from request entry to final validation
   - Identify all auth decision points (middleware, services, APIs)
   - Look for bypass paths (health checks, webhooks, callbacks that skip auth)
   - Check fail-open vs fail-closed behaviour
   
   **Authorization Logic:**
   - How are permissions checked?
   - Can users access resources they shouldn't?
   - Are role checks consistent across endpoints?
   
   **Input Validation:**
   - Where does user input enter the system?
   - What validation exists at each layer?
   - Are there paths where input bypasses validation?
   
   **Trust Boundary Analysis:**
   - What data is trusted vs untrusted?
   - Are internal service calls properly validated?
   - Does the system trust headers (X-Forwarded-For, X-User-Id) without verification?
   
   Generate **individual finding files** for any NEW issues discovered:
   - `experiments/001_baseline/Findings/Code/` for code logic issues
   - `experiments/001_baseline/Findings/Cloud/` for infrastructure issues
   - Each finding follows template in `Templates/CodeFinding.md` or `Templates/CloudFinding.md`
   - **DO NOT** run skeptic reviews yet - findings must exist first
   
   **Validation:** At end of Phase 4:
   - `ls experiments/001_baseline/Findings/Code/*.md` → at least 1 file per MEDIUM+ finding
   - `ls experiments/001_baseline/Findings/Cloud/*.md` → findings from Phase 3 rules
   - Each file has blank Skeptic sections (not yet filled)
   
   **Phase 5: Skeptic Reviews (review each finding)**
   
   **IMPORTANT:** Skeptics review the FINDINGS created in Phases 3-4, not the code directly.
   The workflow is:
   1. Read finding from `experiments/001_baseline/Findings/`
   2. Understand the security engineer's claim
   3. Access source code/IaC to verify or challenge the claim
   4. Update the finding file with skeptic section
   
   For EACH finding generated in Phases 3-4:
   
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

   **Phase 6: Capture Metrics**
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

6. **Capture ALL user feedback immediately:**
   - Every piece of feedback → add to `experiments/<id>/feedback.md`
   - Don't wait to be prompted - capture as you receive it
   - Include: what was wrong, what was corrected, what files were updated
   - This drives learning for future experiments

7. **When done, mark complete:**
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
| `completed` | Scans finished, findings generated | **Post-scan validation** |
| `awaiting_review` | Waiting for human feedback | `triage experiment review <id>` |
| `reviewed` | Human feedback recorded | `triage experiment learn <id>` |
| `learned` | Learning applied, ready for next experiment | Create next experiment |

## Post-Scan Validation (After 'completed')

**🚨 FOR EXPANSEAZURELAB ONLY:** When scanning ExpanseAzureLab, perform validation BEFORE marking as `awaiting_review`.

### Step 1: Check for Ground Truth (ExpanseAzureLab specific)
ExpanseAzureLab contains validation resources:
- `attacks/` folder with vulnerability documentation (`AzureLabFull.pdf`)
- `images/` folder with reference architecture diagrams

**DO NOT look at these during the scan phase** - only check them now for validation.

### Step 2: Create Validation Matrix
If ground truth exists, create validation comparison:

**File:** `Output/Learning/experiments/00X/validation_comparison.md`

```markdown
# Experiment {ID} Validation Comparison

## Ground Truth Source
- **Location:** /path/to/repo/attacks/file.pdf (or attacks.md)
- **Architecture Reference:** /path/to/repo/images/

## Vulnerability Comparison

| # | Ground Truth Vulnerability | Severity (Actual) | Found? | Finding ID | Notes |
|---|---------------------------|-------------------|--------|------------|-------|
| 1 | Public storage with credentials | CRITICAL | ✅ | STORAGE_001 | Correct |
| 2 | SQL firewall 0.0.0.0 | HIGH | ✅ | SQL_001 | Correct |
| 3 | Missing DDoS protection | MEDIUM | ❌ | - | **FALSE NEGATIVE** |
| 4 | ... | ... | ... | ... | ... |

## Architecture Comparison

Compare generated architecture diagrams (`Summary/Repos/`) to reference diagrams (`images/`):
- ✅ Components identified correctly
- ❌ Missed: [list components not in diagram]
- ❌ Incorrect relationships: [list errors]

## Metrics

- **True Positives:** N findings correctly identified
- **False Negatives:** M vulnerabilities missed
- **False Positives:** P findings not in ground truth
- **Accuracy:** TP / (TP + FN + FP)

## False Negatives Analysis
[Why did we miss these? What needs improvement?]

## False Positives Analysis
[Were these actually valid findings not in ground truth, or overcautious?]
```

### Step 3: Update experiment.json
Add validation metrics to experiment.json:
```json
{
  "validation": {
    "ground_truth_source": "attacks/AzureLabFull.pdf",
    "true_positives": 6,
    "false_negatives": 4,
    "false_positives": 1,
    "accuracy": 0.67
  }
}
```

### Step 4: Mark as awaiting_review
After validation comparison is complete, proceed to human review.

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

## 📚 Learning Capture During Experiments

**CRITICAL:** During experiment execution, capture learnings immediately when discovered.

### When to Capture Learnings

Capture a `LEARNING_*.md` file when you discover:

1. **Script/Agent Bugs**
   - Detection logic failures (e.g., provider misidentification)
   - False positives/negatives in automated scans
   - Performance issues or inefficiencies

2. **Skeptic Disagreement Patterns**
   - DevSkeptic vs PlatformSkeptic score differences >2 points
   - Repeated disagreements on specific issue types (e.g., network controls)
   - New compensating control patterns identified

3. **Detection Gaps**
   - Security issues missed by automated scans but found manually
   - IaC patterns not covered by existing detection rules
   - New attack vectors not in SecurityAgent guidance

4. **Process Improvements**
   - Phase ordering issues (should Phase X come before Phase Y?)
   - User workflow friction points
   - Documentation gaps causing confusion

### Learning File Format

Create: `experiments/<id>/LEARNING_<Topic>.md`

```markdown
# LEARNING: [Brief Title]

## Context
- **Experiment:** <id>_<name>
- **Date:** YYYY-MM-DD
- **Phase:** [Context Discovery / Security Review / Skeptic Reviews]
- **Trigger:** [What caused this discovery]

## Issue Discovered
[Detailed description with evidence]

## Root Cause
[Technical analysis - why did this happen?]

## Recommended Fix
[Specific implementation steps with code examples]

## Impact
- **Affects:** [Which agents/scripts/future scans]
- **Priority:** P0 (Critical) / P1 (High) / P2 (Medium)
- **Effort:** [Estimated implementation time]

## Verification
[How to test the fix works - test cases]

## Related
- [Link to affected finding if applicable]
- [Link to agent instruction file]
```

### Examples from Experiment 006

**Good example:** `LEARNING_PROVIDER_DETECTION_BUG.md`
- Documented AWS misidentification issue
- Root cause analysis (regex too broad + alphabetical sort)
- Specific fix with code snippets
- Test cases for verification
- Multi-cloud support recommendation

**What to avoid:**
- ❌ Storing in `~/.copilot/session-state/*/files/` (not persistent)
- ❌ Only mentioning in checkpoint/audit log (hard to find later)
- ❌ Relying on memory across sessions (gets lost)

### Handoff to LearningAgent

After experiment completion and human validation:
1. LearningAgent reads all `LEARNING_*.md` files in experiment folder
2. Analyzes `validation.json` for human feedback patterns
3. Proposes consolidated changes to agent instructions
4. Updates `triage.db` SQLite with effectiveness metrics
5. Applies approved changes to next experiment's Agents/

**Your responsibility:** Capture the raw learning during experiment
**LearningAgent responsibility:** Analyze patterns and propose systematic fixes

---

## See Also
- `Agents/LearningAgent.md` — Applies learnings from experiments
- `Agents/Instructions.md` — Canonical operating rules
- `SessionKickoff.md` — Session initialization

## ⛔ CRITICAL: No Copying Between Experiments

**NEVER copy findings, summaries, or analyses from previous experiments when running a new experiment.**

**Prohibited actions:**
- ❌ `cp experiments/001_*/Findings/* experiments/004_*/Findings/`
- ❌ `cp experiments/001_*/Summary/* experiments/004_*/Summary/`
- ❌ Copy-pasting content from previous experiment files into new ones
- ❌ Using previous experiment output as a "template" for current experiment

**Why this matters:**
- Experiments test if improved agents produce better results **independently**
- Copying defeats the purpose of comparing agent effectiveness
- Learnings can only be validated if experiments are truly independent

**Correct approach:**
- ✅ Run each experiment from scratch using only the code/IaC being scanned
- ✅ Use the experiment's own agent instructions (experiments/<id>/Agents/)
- ✅ Let agents produce findings independently
- ✅ Compare results AFTER both experiments complete

**If an experiment needs reference material:**
- ✅ Reference the actual source code being scanned
- ✅ Check experiment-specific Knowledge/ for context
- ❌ Do NOT reference other experiment outputs

**Enforcement:**
When tasked with completing an experiment phase, if you find yourself about to copy from a previous experiment, **STOP** and complete the analysis from scratch using the source repository.
