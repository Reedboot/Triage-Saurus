# 🟣 Triage-Saurus Workflows

## Purpose
This document defines the navigation flows and menu structures for Triage-Saurus sessions. It ensures consistent user experience across different operations.

---

## Main Menu

**Presented at:** Session start, or when user selects "Return to main menu"

**Options:**
1. Copy/paste a single issue to triage
2. Process bulk intake from Intake/Cloud (N files)
3. Scan a repo
4. Scan a sample repo
5. Import and triage sample cloud findings (N files)
6. Import and triage sample code findings (N files)

**Allow freeform:** Yes (for custom paths or special requests)

**Navigation:**
- Option 1 → Single Issue Triage Flow
- Option 2 → Bulk Cloud Triage Flow
- Option 3 → Repository Scan Flow
- Option 4 → Repository Scan Flow (sample repo)
- Option 5 → Sample Cloud Triage Flow
- Option 6 → Sample Code Triage Flow

---

## Repository Scan Flow

### Step 1: Confirm Repos Root Path
**Prompt:** "Confirm the repos root directory path where your repositories are located:"

**Options:**
- `/mnt/c/Repos (Recommended)` (or detected path)
- Allow freeform for custom paths

### Step 2: Select Repository
**Prompt:** "Which repository would you like to scan?"

**Options:**
- List all individual repositories found in repos root
- Special patterns: "Scan all terraform-* repos", "Scan multiple repos (specify pattern)"
- Allow freeform for custom repo names or patterns

### Step 3: Pre-Discovery Folder Access
**Prompt:** "Ready to scan {repo_name}. This will access {repo_path}. Grant permission to proceed?"

**Purpose:** Request folder access permission ONCE before launching parallel agents to avoid multiple permission prompts.

**Options:**
- Continue with context discovery (Recommended)
- Cancel and return to repository selection

### Step 4: Phase 1 - Automated Context Discovery (~10 seconds)
**Action:** Run `python3 Scripts/discover_repo_context.py <repo_path> --repos-root <repos_root>`

**Detects automatically:**
- Languages & frameworks (C#, F#, TypeScript, Python, Go, Java, Kotlin)
- IaC & orchestration (Terraform, Bicep, Helm, Kustomize, Skaffold, Tilt, K8s manifests)
- Container runtime (Dockerfile analysis: base images, ports, user, health checks, multi-stage builds)
- Network topology (VNets, subnets, NSGs, private endpoints, peerings)
- Hosting (App Service, AKS, Functions, Container Apps)
- CI/CD (Azure Pipelines, GitHub Actions, GitLab CI)
- API routes (MapGet/MapPost, [HttpGet], route mapping JSON files)
- Authentication methods (JWT, OAuth, APIM keys, mTLS, Digital Signatures)
- External dependencies (databases with auth method, storage, queues, monitoring, backend APIs via APIM)
- Ingress patterns (App Gateway, Front Door from code & Terraform)
- APIM routing (mock vs real backend routing)
- Backend services (from HttpClient config and route mapping JSON files)

**Output:** `Output/Summary/Repos/<RepoName>.md` with:
- 🗺️ Architecture Diagram (infrastructure topology, colored borders, clean connections)
- 📊 TL;DR (Phase 2 TODO markers for security review)
- 🛡️ Security Observations (detected controls, Phase 2 guidance)
- 🧭 Overview (purpose, hosting, dependencies, auth, container/network details)
- 🚦 Traffic Flow (Phase 2 TODO with route mappings table)
- Updates `Output/Knowledge/Repos.md` and `Output/Knowledge/<Provider>.md`
- Cloud architecture files in `Output/Summary/Cloud/Architecture_*.md` (provider + overview)
- Kubernetes detail files when AKS/EKS/GKE exists: `Output/Summary/Cloud/<Provider>/Architecture_<Provider>_Kubernetes_<ClusterName>.md`
- Provider resource summaries under `Output/Summary/Cloud/<Provider>/` (top-level `Cloud/` kept for `Architecture_*.md` only)

### Step 5: Phase 2 - Manual Context Analysis
**Action:** Launch ONE explore agent per repo
**Purpose:** Complete Phase 2 TODO markers with deep code understanding
- Trace middleware execution order (numbered steps)
- Document routing logic (how backend selection works)
- Explain business purpose and key files
- Complete Traffic Flow section with full details
- See `Agents/ContextDiscoveryAgent.md` for Phase 2 prompt template

### Step 6: Phase 3 - Security Review (Manual, Using Gathered Context)
**Action:** Perform qualitative security review using Phase 1 + Phase 2 context
- Review authentication/authorization flows for bypass risks
- Check IaC configurations (public exposure, weak encryption, missing controls)
- Review routing logic and middleware for security gaps
- Identify injection risks, insecure deserialization, secrets in code
- **Invoke Dev Skeptic** - Review from developer perspective
- **Invoke Platform Skeptic** - Review from platform perspective
- Document findings in Security Observations section
- Update TL;DR with final scores and skeptic reasoning
- Note: This is qualitative code/config review, NOT automated vulnerability scanning

### Step 7: Phase 4 - Cloud Architecture Update (If IaC Detected)
**Action:** Launch ArchitectureAgent
- Updates `Output/Summary/Cloud/Architecture_<Provider>.md`
- Shows where this repo/service fits in overall cloud estate
- Multiple focused diagrams (Ingress, Routing, Backend, Network)
- Adds hyperlinks (🔗) between services for navigation

### Step 8: Remote Sync Check
**Action:** Check if local repo is in sync with remote
- If up-to-date: Proceed to Step 6
- If behind/diverged: Ask user whether to pull or scan current version
- If no remote configured: Proceed with local scan

### Step 6: Post-Context Discovery Menu
**Prompt:** "Context discovery complete for {repo_name}. What would you like to do next?"

**Options:**
1. SAST scan on {repo_name}
2. SCA scan on {repo_name}
3. Secrets scan on {repo_name}
4. IaC scan on {repo_name}
5. All scans on {repo_name}
6. Scan another repository (→ returns to Step 2)
7. Return to main menu (→ returns to Main Menu)

**Allow freeform:** No (clear structured choices)

**Navigation:**
- Options 1-5 → Security Scan Flow (with selected scan type)
- Option 6 → Repository Scan Flow (Step 2)
- Option 7 → Main Menu

---

## Security Scan Flow

### Step 1: Execute Scan
**Action:** Run selected scan type(s) on the repository
- SAST: Static Application Security Testing (code analysis)
- SCA: Software Composition Analysis (dependency vulnerabilities)
- Secrets: Credential/secret detection
- IaC: Infrastructure as Code security analysis
- All: Execute all scan types

**Output:**
- Create findings in `Output/Findings/Code/`
- Update `Output/Knowledge/Repos.md` with scan results
- Update audit log with scan timing and results

### Step 2: Post-Scan Menu
**Prompt:** "Scan complete for {repo_name}. What would you like to do next?"

**Options:**
1. Run another scan on {repo_name} (→ returns to Post-Context Discovery Menu, Step 5)
2. Review and triage findings (→ Finding Review Flow)
3. Generate summary report (→ Summary Generation Flow)
4. Scan another repository (→ Repository Scan Flow, Step 2)
5. Return to main menu (→ Main Menu)

**Allow freeform:** No

---

## Single Issue Triage Flow

### Step 1: Receive Issue Text
**Prompt:** "Please paste the issue to triage:"

**Action:** User pastes finding text (cloud, code, or other)

### Step 2: Identify Triage Type
**Action:** Analyze pasted content to determine:
- Cloud finding (Azure/AWS/GCP)
- Code finding (SAST/SCA/vulnerability)
- Infrastructure/other

**Navigation:**
- Cloud → Cloud Triage Flow
- Code → Code Triage Flow

### Step 3: Post-Triage Menu
**Prompt:** "Triage complete. What would you like to do next?"

**Options:**
1. Triage another issue (→ returns to Step 1)
2. Process bulk intake (→ Bulk Triage Flow)
3. Scan a repo (→ Repository Scan Flow)
4. Return to main menu (→ Main Menu)

---

## Bulk Triage Flow

### Step 1: Select Intake Path
**Prompt:** "Select intake path for bulk triage:"

**Options:**
- List all non-empty folders under Intake/ and Sample Findings/
- Example: Intake/Cloud, Intake/Code, Sample Findings/Cloud, Sample Findings/Code
- Allow freeform for custom paths

### Step 2: Check for Duplicates
**Action:** Run `python3 Scripts/compare_intake_to_findings.py --intake <path> --findings Output/Findings/<type>`
- If duplicates found: Ask to proceed with new items only
- If no new items: Stop and notify user

### Step 3: Infer Triage Type
**Action:** Determine scope from folder name or content
- If Intake/Cloud or Sample Findings/Cloud → Cloud triage
- If Intake/Code or Sample Findings/Code → Code triage
- Otherwise: Ask user to specify

### Step 4: Execute Bulk Triage
**Action:** Process all items in selected path
- Create findings in `Output/Findings/<type>/`
- Update knowledge files
- Log all actions to audit log

### Step 5: Post-Bulk-Triage Menu
**Prompt:** "Bulk triage complete ({N} findings created). What would you like to do next?"

**Options:**
1. Process another bulk intake (→ returns to Step 1)
2. Review and triage findings (→ Finding Review Flow)
3. Generate summary report (→ Summary Generation Flow)
4. Scan a repo (→ Repository Scan Flow)
5. Return to main menu (→ Main Menu)

---

## Finding Review Flow

**Status:** To be defined (depends on review workflow requirements)

**Potential navigation:**
- Filter findings by severity/type
- Apply DevSkeptic/PlatformSkeptic reviews
- Update scores and applicability
- Return to previous menu

---

## Summary Generation Flow

**Status:** To be defined (depends on summary generation requirements)

**Potential navigation:**
- Generate risk register
- Create executive summaries
- Export findings reports
- Return to previous menu

---

## Navigation Principles

### Consistent Menu Structure
Every menu should:
1. Present clear, actionable choices
2. Include "Return to main menu" option (except Main Menu itself)
3. Include "Go back" or contextual return option where appropriate
4. Use `ask_user` tool with `allow_freeform: false` for structured menus
5. Use `allow_freeform: true` only when custom input is legitimately needed

### Menu Choice Format
- Use clear action-oriented language: "SAST scan on {repo_name}", not "SAST"
- Include context in choices: "{action} on {target}"
- Group related actions logically
- Put recommended options first with "(Recommended)" suffix if applicable

### Navigation State Management
- Always know where the user is in the workflow
- Log navigation decisions to audit log
- Update session metadata when changing contexts (e.g., repo, scan type)
- Preserve context when returning to previous menus

### Exit Points
Every workflow should have clear exit points:
1. Complete current task and return to previous menu
2. Skip to main menu
3. Continue with related task in same context

---

## Future Enhancements

### To Be Added
- Finding Review Flow details
- Summary Generation Flow details
- Cloud Context Discovery menu (when cloud triage is selected)
- Multiple repository parallel scanning workflow
- Incremental scan workflow (scan only changed files)

### To Be Refined
- Error handling and retry workflows
- Cancel/abort operations mid-workflow
- Session save/resume functionality
- Multi-step workflows with checkpoints
