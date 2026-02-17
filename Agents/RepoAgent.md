# 🟣 Repository Scanning Agent

## Role
Comprehensive guidance for scanning code repositories to identify security vulnerabilities, infrastructure misconfigurations, and dependency risks.

## Purpose
This agent provides the workflow, tools, and process for conducting security scans of source code repositories. For guidance on **scoring findings and attack path analysis**, see `Agents/SecurityAgent.md`.

## Repo Scan Workflow

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
### fi_api

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

## Deliverables per Scan

- **Repo summary:** `Output/Summary/Repos/<RepoName>.md` (create FIRST, before findings)
- **Findings:** `Output/Findings/Code/<FindingName>.md` (one per security issue)
- **Knowledge updates:** `Output/Knowledge/Repos.md` (architectural context, tech stack, security observations)
- **Audit log entries:** `Output/Audit/Session_*.md` (scan timing, tools used, findings count)

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
