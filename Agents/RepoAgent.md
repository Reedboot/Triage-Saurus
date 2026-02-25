# 🟣 Repository Scanning Agent

## Role
Comprehensive guidance for scanning code repositories to identify security vulnerabilities, infrastructure misconfigurations, and dependency risks.

## Purpose
This agent provides the workflow, tools, and process for conducting security scans of source code repositories. For guidance on **scoring findings and attack path analysis**, see `Agents/SecurityAgent.md`.

## Repo Scan Workflow

### Step 1: Context Discovery (DEFAULT FIRST STEP)

**Purpose:** Understand the repository's purpose, technology stack, services, and architecture before running security scans. This provides essential context for interpreting findings.

**When to run:** ALWAYS run this as the first step for new repositories. It populates foundational knowledge that informs all subsequent analysis.

**Speed target:** < 1 minute using parallel exploration.

**How to run:** Use 4-6 parallel **explore agents** to discover:
1. **Purpose & README:** What does this repo do? Read README, RunBook, main docs
2. **Tech stack:** Languages, frameworks, dependencies (package.json, requirements.txt, *.csproj, go.mod, Dockerfile)
3. **IaC files:** Find all *.tf, *.bicep, K8s YAML, ARM templates
4. **Ingress points:** APIs, load balancers, APIM, public endpoints, Kubernetes Ingress
5. **Traffic flow (MANDATORY):** Complete request path from entry to backend (ports, middleware, routing logic)
6. **Databases:** Connection strings, schemas, migrations, Dacpac, EF, SQL scripts
7. **Egress:** External API calls, third-party services

See `Agents/ContextDiscoveryAgent.md` for detailed discovery patterns and grep commands.

**DO NOT:** Use general-purpose agent (too slow), run security scans yet, or do deep code analysis.

**Output files created:**
- `Output/Knowledge/Repos.md` - Adds entry for this repository
- `Output/Summary/Repos/<RepoName>.md` - Creates comprehensive repo summary with:
  - 🗺️ Architecture Diagram (Mermaid) at the top
  - Overall Score with progression (Security → Dev Skeptic → Platform Skeptic)
  - 📊 TL;DR Executive Summary
  - 🛡️ Security Observations (Confirmed Controls + Areas for Review)
  - 🧭 Overview
  - 🚦 Traffic Flow section (MANDATORY - Mermaid flowchart LR diagrams for sequential flows, colored borders)
  - 🛡️ Security Review
  - 🤔 Skeptic Reviews (Dev + Platform - MUST run even without deeper scans)
  - 🤝 Collaboration
  - 📚 Assumptions (ALL assumptions with evidence/impact/validation steps)
  - Tech stack, infrastructure, dependencies, CI/CD, observability

**After context discovery completes:** Ask user to select scan scope using ask_user tool:
- Choices: "Manual analysis only", "IaC scan", "All (SAST, SCA, Secrets, IaC)", "SAST only", "SCA only", "Secrets only", "IaC only", "Custom combination"
- **Default is "Manual analysis only"** for Phase 2 (Deeper Context Search) - code review without automated dependency scanning

**Phase 1 rule integration:** Phase 1 should use Rules/ (Rules/Summary.md) to derive grep patterns and guide IaC discovery when opengrep is not available.
- **SCA (CVE scanning)** runs ONLY when explicitly requested or as part of SAST
- SAST available but not default (more time-intensive, less actionable for initial triage)

---

### Step 2: Pre-Scan Remote Sync Check (REQUIRED BEFORE SCANNING)

**Purpose:** Ensure we're scanning the latest code by checking for remote updates before analysis begins.

**When to run:** ALWAYS run this before any scan type (full or incremental). This runs after context discovery.

#### Step 1: Check Remote Status
```bash
# Fetch remote refs without pulling
cd /path/to/repo
git fetch --dry-run 2>&1 | grep -q "fatal: not a git repository" && echo "❌ Not a git repo" && exit 1

# Fetch latest remote refs
git fetch origin --quiet

# Get current branch
current_branch=$(git rev-parse --abbrev-ref HEAD)

# Check if branch has upstream
if ! git rev-parse --abbrev-ref @{upstream} &>/dev/null; then
  echo "⚠️ No upstream branch configured. Scanning local branch: $current_branch"
  # Proceed with scan (may be a local-only repo)
  exit 0
fi

# Get upstream branch
upstream_branch=$(git rev-parse --abbrev-ref @{upstream})

# Compare local vs remote
local_commit=$(git rev-parse HEAD)
remote_commit=$(git rev-parse @{upstream})

if [ "$local_commit" = "$remote_commit" ]; then
  echo "✅ Repository is up-to-date with $upstream_branch"
else
  # Check if we're ahead, behind, or diverged
  ahead=$(git rev-list --count @{upstream}..HEAD)
  behind=$(git rev-list --count HEAD..@{upstream})
  
  if [ "$behind" -gt 0 ] && [ "$ahead" -eq 0 ]; then
    echo "⚠️ Repository is BEHIND remote by $behind commit(s)"
    echo "Remote has newer changes that aren't in your local copy."
    # STOP and ask user via ask_user tool
  elif [ "$ahead" -gt 0 ] && [ "$behind" -eq 0 ]; then
    echo "ℹ️ Repository is AHEAD of remote by $ahead commit(s)"
    echo "You have local commits not pushed to remote."
    # Proceed with scan (we're scanning local work)
  else
    echo "⚠️ Repository has DIVERGED from remote"
    echo "Local is ahead by $ahead, behind by $behind commit(s)"
    # STOP and ask user via ask_user tool
  fi
fi
```

#### Step 2: Ask User Decision (if behind or diverged)

**Use ask_user tool with choices:**
- **"Pull latest and scan (Recommended)"** - Performs `git pull` then proceeds
- **"Scan current local version"** - Proceeds without pulling (may scan outdated code)
- **"Cancel scan"** - Stops the scan workflow

**Example ask_user prompt:**
```
🤔 The repository is 3 commit(s) behind origin/main. 

Would you like to pull the latest changes before scanning?

Choices:
- Pull latest and scan (Recommended)
- Scan current local version
- Cancel scan
```

#### Step 3: Execute Pull (if user chooses)
```bash
# If user selected "Pull latest and scan"
cd /path/to/repo

# Check for uncommitted changes first
if ! git diff-index --quiet HEAD --; then
  echo "⚠️ You have uncommitted changes. Stashing before pull..."
  git stash save "Auto-stash before security scan $(date +%Y-%m-%d_%H%M%S)"
  stashed=true
fi

# Pull latest
git pull origin $current_branch

# Restore stashed changes if any
if [ "$stashed" = true ]; then
  echo "Restoring stashed changes..."
  git stash pop
fi

echo "✅ Repository updated to latest. Proceeding with scan..."
```

#### Step 4: Record Sync Status in Audit Log
```markdown
### 09:01 - Pre-Scan Remote Sync Check
- **Repository:** my_api
- **Branch:** main
- **Local commit:** fe31e209
- **Remote commit:** a7b3c4d5
- **Status:** Behind by 3 commits
- **Action:** User chose to pull latest
- **New HEAD:** a7b3c4d5
- **Result:** ✅ Repository synced successfully
```

### Benefits of Remote Sync Check:
- ✅ **Prevents stale scans** - Always scan the latest code
- ✅ **Detects drift** - Alerts when local copy is outdated
- ✅ **User choice** - Allows intentional scanning of local branches
- ✅ **Audit trail** - Documents which commit was actually scanned

### Edge Cases Handled:
- **No upstream configured:** Proceeds (local-only repo)
- **Detached HEAD:** Warns but proceeds (scanning specific commit)
- **Uncommitted changes:** Auto-stashes before pull, restores after
- **Network unavailable:** Fails gracefully with clear error message
- **Local ahead of remote:** Proceeds (scanning unpushed work-in-progress)

---

### Git History Analysis (Context Phase)

**Purpose:** Extract historical context to understand architectural decisions, timeline of changes, and distinguish intentional architecture from technical debt.

**When to run:** Early in the scan process, after identifying the repo but before deep analysis of findings.

**Time limit:** 2-3 minutes maximum; focus on recent/relevant history only.

#### Step 1: Identify Significant Changes
```bash
# Recent commits with security/upgrade relevance
git --no-pager log --oneline --all -100 | grep -iE "(upgrade|migration|security|framework|vulnerability|CVE|dotnet|node|python|java|terraform|breaking)"
```

#### Step 2: Framework/Runtime Changes
Look for commits indicating major version upgrades:
- **Language/Runtime:** ".NET 8", "Node 20", "Python 3.11", "Go 1.21"
- **Frameworks:** "Angular 17", "React 18", "Spring Boot 3"
- **IaC Versions:** "Terraform 1.5", "Pulumi 3.x"

**Record in repo summary:**
```markdown
### Key Historical Context
- **Latest commit:** <hash> (<date>)
- **Framework upgrades:** Upgraded to .NET 8.0 in Oct 2025 (commit 81401b1)
- **Security patches:** Applied CVE-2024-12345 fix in Sep 2025 (commit abc123d)
```

#### Step 3: Major Dependency Updates
```bash
# Check for significant package updates
git --no-pager log --oneline -50 -- package.json packages.lock.json requirements.txt go.mod Gemfile.lock
```

#### Step 4: Infrastructure Changes
```bash
# IaC and deployment changes
git --no-pager log --oneline -50 -- terraform/ infra/ .github/ .azure-pipelines.yml
```

#### What to Capture:
- **Commit hash** for reference (use in findings/Dev Skeptic context)
- **Date** (relative timing: "months ago", "last week")
- **Title/summary** of the change
- **Why it matters** for current security assessment

#### Example Use Cases:
1. **False Positive Context:** "We upgraded to .NET 8.0 months ago (commit 81401b1). The .NET 2.1.x packages are zombie dependencies, not runtime vulnerabilities."
2. **Remediation Timeline:** "This vulnerability was patched 3 months ago (commit def456) but the finding still appears - may be stale scan data."
3. **Architectural Intent:** "Service was intentionally moved to private endpoint in June 2025 (commit 789abc), contradicting the 'public endpoint' finding."

#### Recording in Knowledge Base:
Update `Output/Knowledge/Repos.md`:
```markdown
### my_api

#### Basic Information
- **Latest Commit:** fe31e209 (2026-01-22)

#### Key Historical Context
- **Framework:** Upgraded to .NET 8.0 (commit 81401b1, Oct 2025)
- **Dependencies:** Newtonsoft.Json remained at 13.0.4 (not updated in recent history)
- **Infrastructure:** Added VNet integration (commit c3b912f, Sep 2025)
```

### Scan Timing & Tools Tracking
**CRITICAL:** For every repo scan, track duration and tools used for each scan type:

1. **Record start time** before each scan type (IaC, SCA, SAST, Secrets)
2. **Record end time** after completion
3. **Calculate duration** in MM:SS or HH:MM:SS format
4. **Log tools used** with specific commands/versions
5. **Update audit log** in `Output/Audit/Session_*.md` under `## Scan Timing & Tools` section

**Example logging:**
```markdown
### Scan Type: SCA
- **Duration:** 02:34
- **Tools used:** dotnet list package --vulnerable, grep packages.lock.json
- **Findings count:** 2
- **Status:** Completed
```

**Common tools by scan type:**
- **IaC:** grep (Terraform/Pulumi/CloudFormation patterns), terraform validate
- **SCA:** dotnet list package --vulnerable, npm audit, pip-audit, go list -m all
- **SAST:** semgrep, custom grep patterns, language-specific linters
- **Secrets:** git-secrets, grep for patterns, truffleHog

**Timing benchmarks (for reference):**
- IaC scan: ~30-90 seconds (depends on file count)
- SCA scan: ~1-5 minutes (depends on dependency count + network)
- SAST scan: ~5-30 minutes (depends on codebase size + complexity)
- Secrets scan: ~1-3 minutes (depends on git history depth)

### Separate Task Agents per Scan Type

**When multiple scan types are selected (e.g., IaC + SCA + SAST), split into separate task agents:**

#### Why Split?
- **Context isolation:** Each scan type needs different focus (IaC = infrastructure, SAST = code logic, SCA = dependencies)
- **Parallel execution:** Scans can run simultaneously, reducing total time
- **Specialized analysis:** IaC agent references architecture diagrams, SAST agent traces code execution paths
- **Prevents context overflow:** Large repos with full scan (IaC + SCA + SAST + Secrets) would overflow a single context window

#### Task Agent Instructions by Type:

**IaC Scan Task:**
```
Scan repository <repo_path> for Infrastructure-as-Code security issues.

Follow Agents/Instructions.md and Agents/SecurityAgent.md for scoring and attack path analysis.

Focus:
- Terraform/Pulumi/CloudFormation/ARM template security misconfigurations
- Network exposure (public IPs, open security groups, missing private endpoints)
- Identity/permissions (overly permissive IAM roles, missing RBAC)
- Encryption gaps (unencrypted storage, disabled TLS)
- Resource naming patterns and tagging conventions

Tools: grep for IaC patterns, terraform validate (if available)

Create findings using Templates/CodeFinding.md under Output/Findings/Code/
Update Output/Summary/Repos/<RepoName>.md with IaC findings section
Record timing in audit log under "Scan Type: IaC"
```

**SCA Scan Task:**
```
Scan repository <repo_path> for Software Composition Analysis (dependency vulnerabilities).

⚠️ **IMPORTANT:** SCA scans (including CVE checking) should ONLY run when explicitly requested by the user or as part of a SAST scan. Do NOT run during Phase 2 manual analysis unless user specifically requests it.

Follow Agents/Instructions.md and Agents/SecurityAgent.md for scoring and attack path analysis.

Focus:
- Known CVEs in direct and transitive dependencies
- End-of-life packages/frameworks
- Version mismatches and dependency conflicts
- License compliance issues

Tools: dotnet list package --vulnerable, npm audit, pip-audit, go list -m all

Create findings using Templates/CodeFinding.md under Output/Findings/Code/
Update Output/Summary/Repos/<RepoName>.md with SCA findings section
Record timing in audit log under "Scan Type: SCA"
```

**SAST Scan Task:**
```
Scan repository <repo_path> for Static Application Security Testing (code vulnerabilities).

Follow Agents/Instructions.md and Agents/SecurityAgent.md for scoring and attack path analysis.

Focus:
- Injection vulnerabilities (SQL, command, XSS, LDAP)
- Authentication/authorization flaws
- Insecure cryptography usage
- Hard-coded secrets/credentials
- Business logic vulnerabilities
- Race conditions and concurrency issues

Tools: semgrep, custom grep patterns, language-specific linters

Create findings using Templates/CodeFinding.md under Output/Findings/Code/
Update Output/Summary/Repos/<RepoName>.md with SAST findings section
Record timing in audit log under "Scan Type: SAST"
```

**Secrets Scan Task:**
```
Scan repository <repo_path> for exposed secrets and credentials.

Follow Agents/Instructions.md and Agents/SecurityAgent.md for scoring and attack path analysis.

Focus:
- Hard-coded API keys, passwords, tokens
- Private keys and certificates
- Connection strings with embedded credentials
- Git history for accidentally committed secrets

Tools: git-secrets, grep for secret patterns, truffleHog

Create findings using Templates/CodeFinding.md under Output/Findings/Code/
Update Output/Summary/Repos/<RepoName>.md with Secrets findings section
Record timing in audit log under "Scan Type: Secrets"
```

#### Consolidation After Parallel Scans:
After all task agents complete:
1. **Review all findings** across scan types
2. **Identify cross-cutting issues:** IaC exposes public endpoint + SAST finds auth bypass = compounded risk
3. **Update finding scores** based on blast radius from architecture context (see SecurityAgent.md for scoring guidance)
4. **Add cross-references** in `## Compounding Findings` sections using markdown links
5. **Update repo summary** with consolidated security posture
6. **Regenerate risk register:** `python3 Scripts/risk_register.py`

## Incremental Scan Optimization (Recommended for Re-scans)

**Purpose:** Speed up subsequent scans by only analyzing what changed since last scan.

**When to use:** 
- Second+ scan of a repository
- CI/CD pipeline integration
- Regular monitoring/re-triage

### Implementation Steps:

#### 1. Record Last Scanned Commit (REQUIRED)
In repo summary (`Output/Summary/Repos/<RepoName>.md`), add metadata section at the top:
```markdown
## 🔍 Scan History

**Track each scan type independently to enable smart re-scanning:**

- **Last IaC Scan:** 2026-02-17 09:08 GMT (Commit: fe31e209) ✅
- **Last SCA Scan:** 2026-02-17 09:15 GMT (Commit: fe31e209) ✅  
- **Last SAST Scan:** Never ⏭️
- **Last Secrets Scan:** Never ⏭️

**Legend:** ✅ = Completed | ⏭️ = Not yet run | ⚠️ = Failed/Incomplete
```

**IMPORTANT:** Always update this section:
- On first scan of a repo, initialize all 4 scan types as "Never"
- After completing a scan type, update its timestamp + commit
- This enables gap detection: "You scanned IaC+SCA before, now adding Secrets scan"

#### 2. Detect Scan Gaps (Which scan types haven't been run yet?)

**Before checking for file changes, first identify missing scan types:**

```bash
# Read scan history from repo summary
# Example: grep "Last.*Scan:" Output/Summary/Repos/my_api.md

# Determine what user requested vs what's been done
requested_scans=("IaC" "SCA" "SAST" "Secrets")  # User selected "All"
completed_scans=("IaC" "SCA")  # From scan history
missing_scans=("SAST" "Secrets")  # Never run before

if [ ${#missing_scans[@]} -gt 0 ]; then
  echo "📋 New scan types requested: ${missing_scans[*]}"
  echo "These will be run regardless of commit changes."
fi
```

**Example scenarios:**

| Previous Scan | New Request | What to Run |
|--------------|-------------|-------------|
| IaC + SCA | IaC + SCA + Secrets | **Secrets only** (IaC/SCA already done on this commit) |
| IaC + SCA | All (IaC+SCA+SAST+Secrets) | **SAST + Secrets** (skip IaC/SCA if no changes) |
| Never scanned | IaC + SCA | **IaC + SCA** (full scan of requested types) |
| IaC + SCA (commit abc) | IaC + SCA (commit xyz) | **Check file changes** (may skip if no IaC/dependency changes) |

#### 3. Check for Changes on Re-scan (For already-completed scan types)

**Only check file changes for scan types that were previously completed:**

```bash
# Get current HEAD
current_commit=$(git -C /path/to/repo rev-parse HEAD)
last_iac_commit="fe31e209"  # Read from "Last IaC Scan" in repo summary
last_sca_commit="fe31e209"  # Read from "Last SCA Scan" in repo summary

# If user requested IaC scan and it was already done on this commit
if [ "$current_commit" = "$last_iac_commit" ]; then
  echo "⏭️ IaC scan already completed on this commit. Skipping."
  skip_iac=true
fi

# If commits differ, check what changed
if [ "$current_commit" != "$last_iac_commit" ]; then
  git -C /path/to/repo diff --name-only $last_iac_commit..$current_commit
fi
```

#### 4. Selective Scan by File Type (Only for previously-completed scan types)

**IaC Scan - Skip if no IaC changes AND already scanned this commit:**
```bash
# Check for IaC file changes
iac_changes=$(git diff --name-only $last_scan_commit HEAD | grep -E '\.(tf|tfvars|yaml|yml|json)$|terraform/|infra/')

if [ -z "$iac_changes" ]; then
  echo "✓ No IaC changes detected. Skipping IaC scan."
else
  echo "📋 IaC files changed: $(echo $iac_changes | wc -l) files"
  # Run IaC scan on changed files only
fi
```

**SCA Scan - Skip if no dependency changes AND already scanned this commit:**
```bash
# Check for dependency manifest changes
dep_changes=$(git diff --name-only $last_scan_commit HEAD | grep -E 'package\.json|package-lock\.json|requirements\.txt|Pipfile\.lock|go\.mod|go\.sum|\.csproj|packages\.lock\.json|Gemfile\.lock')

if [ -z "$dep_changes" ]; then
  echo "✓ No dependency changes detected. Skipping SCA scan."
else
  echo "📦 Dependency files changed. Running SCA scan."
fi
```

**SAST Scan - Analyze only changed code (if previously scanned):**
```bash
# Get changed source code files
code_changes=$(git diff --name-only $last_scan_commit HEAD | grep -E '\.(cs|js|ts|py|go|java|rb)$')

if [ -z "$code_changes" ]; then
  echo "✓ No code changes detected. Skipping SAST scan."
else
  echo "🔍 Code files changed: $(echo $code_changes | wc -l) files"
  # Run SAST only on changed files
  while read -r file; do
    semgrep scan "$file"
  done <<< "$code_changes"
fi
```

**Secrets Scan - Only new commits (if previously scanned):**
```bash
# Scan only new commits since last scan
git log --all --pretty=format:"%H" $last_scan_commit..HEAD | while read commit; do
  git show $commit | grep -iE '(password|api.?key|secret|token|credentials)'
done
```

#### 5. Update Scan History Metadata (CRITICAL - Always do this)
After completing any scan, **update the specific scan type(s) completed:**
```markdown
## 🔍 Scan History

- **Last IaC Scan:** 2026-02-17 09:08 GMT (Commit: fe31e209) ✅
- **Last SCA Scan:** 2026-02-17 09:15 GMT (Commit: fe31e209) ✅  
- **Last SAST Scan:** 2026-02-20 14:30 GMT (Commit: a7b3c4d5) ✅ ← UPDATED
- **Last Secrets Scan:** 2026-02-20 14:35 GMT (Commit: a7b3c4d5) ✅ ← UPDATED

**Latest Scan Details:**
- **Date:** 2026-02-20 14:30 GMT
- **Commit:** a7b3c4d5 (12 commits ahead of last scan)
- **Scan types:** SAST, Secrets (IaC/SCA skipped - no relevant changes)
- **Changed files:** 8 (6 .cs files, 2 .tf files)
- **New findings:** 1 (SQL injection in UserController.cs)
```

**Rule:** Each scan type gets its own timestamp + commit. Never use "Last Full Scan" - track individually!

#### 6. Smart Re-scan Decision Tree
```
Step 1: Check scan history for each requested scan type
│
├─ Scan type NEVER run before?
│  └─ ALWAYS RUN (gap-filling scan)
│
└─ Scan type previously completed?
   │
   ├─ Current commit == last scan commit for this type?
   │  └─ Ask "No changes. Force re-scan anyway?" → Yes/No
   │
   └─ Current commit != last scan commit?
      │
      ├─ IaC scan: Check for .tf/.yaml/.json changes → Run if changed
      ├─ SCA scan: Check for manifest changes → Run if changed  
      ├─ SAST scan: Check for source code changes → Run if changed
      └─ Secrets scan: Check for new commits → Run if new commits exist
```

**Example walkthrough:**

**Scenario:** User previously ran "IaC + SCA" on commit `abc123`, now wants "All" scans on commit `xyz789`

1. **Check scan history:**
   - IaC: Done on `abc123` ✅
   - SCA: Done on `abc123` ✅
   - SAST: Never ⏭️
   - Secrets: Never ⏭️

2. **Identify gaps:**
   - Must run: SAST, Secrets (never done before)
   - Check changes: IaC, SCA (already done, but on old commit)

3. **Check file changes between `abc123` and `xyz789`:**
   - `.tf` files changed? → Re-run IaC
   - `package.json` changed? → Re-run SCA
   - `.cs` files changed? → Already running SAST
   - New commits? → Already running Secrets

4. **Final decision:**
   - Run: SAST ✅ (new scan type)
   - Run: Secrets ✅ (new scan type)
   - Run: SCA ✅ (manifest changed)
   - Skip: IaC ⏭️ (no .tf file changes detected)

**Output to user:**
```
📋 Scan Plan for my_api:
✅ SAST scan (never run before)
✅ Secrets scan (never run before)  
✅ SCA scan (dependencies changed since last scan)
⏭️ IaC scan (no changes to infrastructure files)

Total scans to run: 3/4 (saving ~2 minutes)
```

### Benefits:
- **10-50x faster** for repos with minimal changes
- **Reduced token usage** (only analyze deltas)
- **CI/CD friendly** (fast feedback on PRs)
- **Audit trail preserved** (scan history tracks incremental scans)

### Fallback to Full Scan When:
- First scan of a repository
- More than 30 days since last scan (drift detection)
- User explicitly requests full re-scan
- Last scan was incomplete/failed
- Major framework/runtime upgrade detected in commits

## Deliverables per Scan

- **Repo summary:** `Output/Summary/Repos/<RepoName>.md` (create FIRST, before findings)
- **Findings:** `Output/Findings/Code/<FindingName>.md` (one per security issue)
- **Knowledge updates:** `Output/Knowledge/Repos.md` (architectural context, tech stack, security observations)
- **Audit log entries:** `Output/Audit/Session_*.md` (scan timing, tools used, findings count)

## Repo Scan Checklist (Complete All Phases)

When scanning a repository with IaC:

☐ **Phase 1: Automated context discovery** (discover_repo_context.py)
  - Run script: `python3 Scripts/discover_repo_context.py <repo_path> --repos-root <repos_root_path>`
  - Creates skeleton `Output/Summary/Repos/<RepoName>.md` with [PHASE 2 TODO] markers
  - Updates `Output/Knowledge/Repos.md` with basic repo info

☐ **Phase 2: Manual context analysis** (explore agent)
  - Complete TODO markers in repo summary
  - Add middleware execution order, authentication flow, route mappings
  - Add security controls matrix
  - Add Dev Skeptic and Platform Skeptic reviews
  - **Validation:** Ensure TL;DR has numeric score (not 0/10 INFO)
  - **Validation:** Run `grep -c "PHASE 2 TODO" Output/Summary/Repos/*.md` → should return 0

☐ **Phase 3: Security review** (create findings, invoke skeptics)
  - **Step 3a: Extract findings from repo summary**
    - Review "Security Observations" section in repo summary
    - Extract MEDIUM+ severity findings as individual files under `Output/Findings/Code/`
    - Each finding file should include:
      - Architecture diagram showing attack path
      - TL;DR section
      - Security Review section (from repo summary)
      - POC script (if exploitable)
      - Blank Skeptic sections (to be filled in Step 3b)
  - **Step 3b: Invoke skeptics on extracted findings**
    - Run Dev Skeptic review on each finding file
    - Run Platform Skeptic review on each finding file
    - Update scores based on skeptic feedback
  - **Step 3c: Link findings**
    - Link findings to repo summary under Compounding Findings
    - Update repo summary TL;DR with finding links
  - **Validation:** 
    - `ls Output/Findings/Code/*.md` → at least 1 finding file for MEDIUM+ issues
    - Each finding has completed Skeptic sections
    - Repo summary links to extracted findings

☐ **Phase 4: Cloud architecture update** (ArchitectureAgent) **[MANDATORY if IaC detected]**
  - Update or create `Output/Summary/Cloud/Architecture_<Provider>.md`
  - Show where this service fits in overall estate
  - Include TL;DR section with services scanned/referenced
  - Link security findings to architecture components
  - **DO NOT SKIP THIS PHASE** - stakeholders need estate-wide view

**Warning:** Do NOT mark experiment/scan complete until all phases validated.

## Integration with Other Agents

- **SecurityAgent.md:** Use for attack path analysis, exploitability assessment, and scoring
- **DevSkeptic.md:** Run after findings created to get developer perspective
- **PlatformSkeptic.md:** Run after findings created to get platform/operations perspective
- **Instructions.md:** Follow for overall workflow, question formatting, bulk processing rules

## Quick Reference

**Preferred scan scope:** IaC + SCA (default for initial triage)
**Full scan:** IaC + SCA + SAST + Secrets (time-intensive, use for comprehensive assessment)
**Parallel execution:** Yes - split scan types into separate task agents
**Git history:** Yes - analyze early in scan for context
**Findings template:** Templates/CodeFinding.md
**Skeptic reviews:** Run Dev and Platform skeptics after findings created
