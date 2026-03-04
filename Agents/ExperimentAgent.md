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

### Pipeline Overview (6 phases — minimize token usage)

```
Phase 1 (scripts, no LLM)        Phase 2 (scripts, no LLM)         Phase 3 (opengrep rules scan)
   discover_repo_context.py  ──►  discover_code_context.py    ──►   opengrep scan --config Rules/
   targeted_scan.py               reads Phase 1 output              store_findings.py
   writes: resources, arch        writes: Summary/Repos/*.md        writes: raw findings → DB
   diagrams to DB & MD            updates DB (context_metadata)     render_finding.py (MD per finding)
   → render_finding.py (MD)       generates repo summary MD         generate_diagram.py (update charts)
   → generate_diagram.py          generate_diagram.py (update charts)

Phase 4 (LLM enrichment)         Phase 5 (skeptic reviews)         Phase 6 (reports)
   enrich_findings.py        ──►  run_skeptics.py            ──►   report_generation.py
   writes: title, description,    writes: skeptic_reviews          risk_register.py
   proposed_fix, severity_score   risk_score_history               render_finding.py (final MD)
   sets: llm_enriched_at          final score = avg(3 skeptics)     generate_diagram.py (final charts)
   render_finding.py (MD)         render_finding.py (MD update)
   generate_diagram.py (charts)
```

**Key design principles:**
- **After each phase**, run `render_finding.py` (for all findings) and `generate_diagram.py` so MD files and diagrams are always in sync with the DB.
- **After Phase 4 runs once, never re-enrich**. Check `llm_enriched_at IS NOT NULL`.
- **After Phase 5 runs, never re-review** — check `(finding_id, reviewer_type)` in `skeptic_reviews`. Only re-run if new findings are added or context changes.
- **Phase 2 before Phase 3**: script-based code context discovery runs before opengrep so detected languages, frameworks, containers, and architecture inform which misconfig rule folders to target.

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

4. **For each repo, execute the pipeline:**

   **Phase 1: Automated Context Discovery - ~10 seconds**
   ```bash
   python3 Scripts/discover_repo_context.py <repo_path> --repos-root /mnt/c/Repos \
       --output-dir Output/Learning/experiments/001_baseline
   ```
   - Creates baseline summary with architecture diagram at `experiments/<id>/Summary/Repos/<RepoName>.md`
   - Detects: IaC resource types, hosting platform, basic auth patterns
   - Populates `resources` table and `repositories` table in DB
   - **After Phase 1:** render findings and diagrams
     ```bash
     python3 Scripts/generate_diagram.py <id>
     ```

   **Phase 2: Deeper Context Discovery (scripts, no LLM) - ~5-15 seconds**
   ```bash
   python3 Scripts/discover_code_context.py \
       --experiment <id> \
       --repo <repo_name> \
       --target <repo_path> \
       --output-dir Output/Learning/experiments/<id>_<name>
   ```

   Detects without any LLM calls:
   - **Languages & Frameworks:** opengrep Detection/Frameworks/ rules + manifest parsing (requirements.txt, package.json, pom.xml, go.mod)
   - **Auth patterns:** opengrep Detection/Code/ rules (JWT, Spring Security, Passport, etc.)
   - **Containers:** Dockerfile base images, exposed ports, sensitive ENV vars
   - **Kubernetes:** Deployments, Services, Ingress hosts, RBAC roles/bindings, privileged containers, host network
   - **CI/CD:** GitHub Actions, GitLab CI, Jenkinsfile, Tekton

   Writes all findings to `context_metadata` table (namespace: `phase2_code`) and generates `Summary/Repos/<repo>.md`.

   ```bash
   python3 Scripts/generate_diagram.py <id>
   ```

   **Phase 3: Rules-Based Infrastructure Scanning (~5-10 minutes)**

   **CRITICAL:** Apply ALL detection rules to achieve complete coverage.

   **`triage_experiment.py run` handles this automatically:**
   - Runs `opengrep scan --config Rules/ <repo> --json --output scan_<repo>.json`
   - Immediately calls `store_findings.py scan_<repo>.json --experiment <id>` → raw findings in DB

   **Manual run if needed:**
   ```bash
   opengrep scan --config /home/neil/code/Triage-Saurus/Rules/ <repo_path> \
       --json --output Output/Learning/experiments/<id>/scan_<repo>.json
   python3 Scripts/store_findings.py Output/Learning/experiments/<id>/scan_<repo>.json \
       --experiment <id> --repo <repo_name>
   ```

   **After Phase 3, render all finding MDs and update diagrams:**
   ```bash
   python3 -c "
   import sqlite3; conn = sqlite3.connect('Output/Learning/triage.db')
   ids = [r[0] for r in conn.execute(\"SELECT id FROM findings WHERE experiment_id=?\", ['<id>']).fetchall()]
   print(' '.join(map(str, ids)))
   "
   # Then for each ID:
   for id in <ids>; do python3 Scripts/render_finding.py --id "$id"; done
   python3 Scripts/generate_diagram.py <id>
   ```

   **Verify DB findings stored:**
   ```bash
   python3 -c "
   import sqlite3; conn = sqlite3.connect('Output/Learning/triage.db')
   rows = conn.execute(\"SELECT rule_id, COUNT(*) FROM findings WHERE experiment_id=? GROUP BY rule_id\", ['<id>']).fetchall()
   for r in rows: print(r)
   "
   ```

   **Key Learning from Experiment 015:**
   - Selective rule application achieves only 50% detection
   - Applying ALL rules achieves 86%+ detection
   - Missing rules for: terraform-hardcoded-keyvault-secret, terraform-nonsensitive-secrets
   - These gaps are now filled (rules created 2026-02-25)

   **Phase 4: LLM Enrichment — run ONCE, findings stored in DB**
   Follow `experiments/<id>/Agents/SecurityAgent.md` → `## DB-First Enrichment Workflow`:

   ```bash
   python3 Scripts/enrich_findings.py --experiment <id>
   ```
   - Queries `findings WHERE llm_enriched_at IS NULL`
   - Calls LLM once per finding; writes `title`, `description`, `proposed_fix`, `severity_score`
   - Sets `llm_enriched_at` — subsequent runs skip already-enriched findings
   - Also writes a `risk_score_history` row (`scored_by='llm'`)

   **After enrichment, regenerate finding MDs and diagrams:**
   ```bash
   for id in <ids>; do python3 Scripts/render_finding.py --id "$id"; done
   python3 Scripts/generate_diagram.py <id>
   ```
   
   **Validation:** At end of Phase 4:


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
   
   **Phase 5: Skeptic Reviews — run ONCE, stored in DB**

   ```bash
   python3 Scripts/run_skeptics.py --experiment <id> --reviewer all
   ```
   
   - Queries `findings WHERE llm_enriched_at IS NOT NULL`
   - Skips findings already reviewed (checks `skeptic_reviews` table)
   - Calls each reviewer persona (security, dev, platform) in sequence
   - Writes to `skeptic_reviews` + `risk_score_history`
   - When all 3 reviewers complete: averages adjusted scores → updates `findings.severity_score`
   
   **Run individual reviewers if preferred:**
   ```bash
   python3 Scripts/run_skeptics.py --experiment <id> --reviewer dev
   python3 Scripts/run_skeptics.py --experiment <id> --reviewer platform
   python3 Scripts/run_skeptics.py --experiment <id> --reviewer security
   ```
   
   **After skeptic reviews, regenerate diagrams and reports:**
   ```bash
   python3 Scripts/generate_diagram.py <id>
   for id in <ids>; do python3 Scripts/render_finding.py --id "$id"; done
   python3 Scripts/risk_register.py
   ```

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
