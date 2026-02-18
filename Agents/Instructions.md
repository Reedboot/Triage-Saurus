# 🟣 Agent Instructions

## Purpose
This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Output/Findings/` using the relevant template.
3. Capture confirmed facts under `Output/Knowledge/` (Confirmed + Assumptions). Keep it focused on reusable environment facts used during triage (services in use, identity model, network posture, guardrails).
   - Keep provider knowledge files consistent: use `## Confirmed`, `## Assumptions`, and
     `## Unknowns` headings (avoid ad-hoc extra sections).
   - If you need an append-only audit trail (e.g., bulk imports, Q&A/triage decisions), write it under `Output/Audit/` and clearly mark it as **AUDIT LOG ONLY — do not load into LLM triage context**.
4. Update `Output/Summary/` outputs (cloud resource summaries and risk register).

**Navigation:** For menu structures and user journey flows, see `Templates/Workflows.md`.

## Behaviour
- **Kickoff trigger:** if the user types `sessionkickoff` (case-insensitive), treat it as “run the session kickoff”.
  - Read `AGENTS.md` and `Agents/Instructions.md`, then scan `Output/Knowledge/` and existing `Output/Findings/` for missing context.
  - If there are **no findings** under `Output/Findings/`, assume this is a **new instance** and move straight to collecting the first triage input (single issue, bulk `Intake/` path, sample import, or repo scan).
  - **Preferred workspace scan (stdout-only):**
- **Clear session trigger:** if the user types `clearsession` (case-insensitive), treat it as "run the clear session script".
  - First run dry-run: `python3 Scripts/clear_session.py` to show what will be deleted
  - Ask user: "This will delete the listed session artifacts (findings, knowledge, audit logs, summaries). Proceed?"
  - If confirmed, run: `python3 Scripts/clear_session.py --yes`
  - This clears all Output/ artifacts (Findings, Knowledge, Summary, Audit) and sample-staged Intake/Sample/ content while preserving Templates/ and user Intake/ files.
    - `python3 Scripts/scan_workspace.py`
    It scans `Output/Knowledge/` (refinement questions), `Output/Findings/`, and common `Intake/`/sample paths.
  - **Check for draft findings requiring validation:**
    - `python3 Scripts/check_draft_findings.py`
    It identifies findings with generic boilerplate that need evidence, applicability confirmation, and accurate risk scoring.
    - If **>10% of findings are drafts**, prominently warn the user and offer to complete them:
      - "⚠️ Found **N draft findings** that need validation. These have placeholder scores and generic boilerplate."
      - "Would you like to **validate draft findings** now, or **proceed with new triage**?"
    - Draft findings show as "⚠️ Draft - Needs Triage" in the Risk Register Status column.
    - **Draft completion workflow (question-first):** when the user chooses to validate drafts:
      - First, skim the draft set and identify **common missing context** before asking per-finding questions.
        - Preferred helper (stdout-only): `python3 Scripts/triage_queue.py`
      - Ask **cross-cutting** questions first (one at a time, prefix with `❓`) so answers apply to many findings, e.g.:
        - cloud provider confirmation (if missing)
        - production vs non-prod scoping (if known)
        - internet exposure posture (public endpoints, management ports)
        - private connectivity patterns (Private Endpoints/Private Link)
        - privileged access model (Bastion/JIT, jump hosts, allowlisted admin IPs)
        - storage access model (public blobs, shared keys)
      - Then apply those answers across the impacted drafts and only then go deeper into the highest-severity items.
      - **Do not** "validate" a finding by removing boilerplate text. Set validation explicitly using the finding `Validation Status` field (e.g., `⚠️ Draft - Needs Triage` vs `✅ Validated`) once evidence/applicability is sufficiently confirmed.
  - **Targeted helpers (stdout-only):**
    - **Check `Output/Knowledge/`:** `python3 Scripts/scan_knowledge_refinement.py`
      It lists Markdown files under `Output/Knowledge/` and prints any non-empty sections under `## Unknowns` / `## ❓ Open Questions`.
    - **Enumerate `Intake/` files:** `python3 Scripts/scan_intake_files.py <Intake/Subfolder>`
      It walks the filesystem and lists `.txt` / `.csv` / `.md` reliably (avoid relying on recursive globbing, which can be flaky on some WSL/Windows mounts).
    - **Check whether `Output/Findings/` has anything in it:** `python3 Scripts/scan_findings_files.py`
      It walks `Output/Findings/` and lists `.md` files reliably.
  - If `Output/Knowledge/` contains outstanding items under `## Unknowns` and/or `## ❓ Open Questions`, tell the user: “I’ve found some **refinement questions** — do you want to answer them now?” (then offer *resume* vs *proceed to new triage*).
  - If there are **no refinement questions** *and* the `Output/Knowledge/` scan indicates **no Knowledge markdown files** (i.e., `scan_knowledge_refinement.py` reports `Knowledge markdown files: 0`), treat this as a **first run / fresh workspace** and start with:
    - `🦖 Welcome to Triage-Saurus.`
  - Then use the **ask_user tool** with selectable choices (keep sample options lower, since they’re mainly for onboarding):
    - **Copy/paste a single issue to triage**
    - **Provide a path under Intake/ to process in bulk**
    - **Scan a repo**
    - **Scan a sample repo**
    - **Import and triage the sample findings**
    - If they choose bulk intake, present a **selectable** multiple-choice list of common paths (and allow freeform for a custom `Intake/...` path).
      - Do **not** include numeric prefixes in the choice labels; the UI will handle numbering/selection.
      - Before offering choices, verify which candidate folders are **non-empty** using (stdout-only):
        - `python3 Scripts/scan_intake_files.py <candidate-path>`
      - Only offer **non-empty** candidates as choices.
  - **Multiple-choice questions (UX):**
    - When asking a multiple-choice question in plain chat, use **numbered bullet points** (e.g., `1. **Option text**`) for better readability.
      - Always include a **“Don’t know”** option as one of the numbered choices.
      - Accept either the **number** or the **full text** as a valid answer.
    - Exception: when using a **selectable** UI prompt (where the client renders choices), do **not** include numeric prefixes in the labels.
  - **Idempotency (multi-day runs):** before processing a selected intake path, check for overlap with already-processed findings and only proceed with *new* items.
    - Run (stdout-only): `python3 Scripts/compare_intake_to_findings.py --intake <Intake/...> --findings Output/Findings/Cloud`
    - If **duplicates are detected** (Already processed > 0), **ask for confirmation** before proceeding:
      - proceed with **new items only** (recommended), or
        - stop and let the user adjust the intake.
      - If **no new items** remain, stop and tell the user.
      - If **new items exist**, proceed using only that new-item subset.
      - Candidate paths in this repo: `Intake/Cloud`, `Intake/Code`, `Intake/Sample/Cloud` (if present), `Intake/Sample/Code` (if present)
- After summarising what you’ve done (kickoff, scans, imports, bulk triage, file writes), always ask the user what they want to do next.
- **Cloud context survey:** Before starting cloud triage (after provider is confirmed), check if `Output/Knowledge/<Provider>.md` has sufficient environmental context. If sparse/missing, offer: "Would you like to run a cloud context survey first? (~10 questions, builds foundational context for faster triage)" — see `Agents/CloudContextAgent.md` for the survey workflow.
- If triage type is **Cloud** and the provider is not explicit from the folder path, quickly skim the intake titles and:
  - if they strongly indicate a provider, state it plainly (e.g., “From looking at the items to triage, it looks like you are using Azure.”)
  - then ask a single confirmation question prefixed with `❓` **on its own line**.
    - Use the provider name as the choice label (avoid “Yes (Azure)”): `Azure` / `AWS` / `GCP` / `Don’t know` (freeform allowed for other).
    - In the line *above* the `❓` question, include a brief “why” based on the titles (e.g., 🤔 Key Vault / Entra / Defender ⇒ Azure).
- Follow `Settings/Styling.md` for formatting rules.
  - In `Output/Summary/`, ensure any references to findings are **markdown links** (clickable),
    not inline-code backticks.
- At session start, quickly review existing `Output/Knowledge/` and any existing findings under `Output/Findings/` to spot missing context; ask targeted questions to fill gaps before proceeding.
- **UK spelling in user-facing questions:** use UK English (e.g., *prioritise, assess, organisation*).
- Ask one targeted question at a time; avoid bundling multiple confirmations into a single prompt.
- For **info-gathering / refinement** questions, avoid broad prompts (e.g., “Do you have internet-facing workloads?”). Instead:
  - briefly state the purpose (e.g., “To better assess applicability and prioritisation…”), and
  - ask a bounded, easy-to-answer question (prefer multiple-choice categories such as *public customer-facing*, *mostly private/internal*, *private-only*), and
  - include **“Don’t know”**.
- When asking **multiple-choice** questions, always include a **“Don’t know”** option.
- When asking a triage question:
  - start with `📌 Trigger:` `<finding title>`
  - then ask exactly one question prefixed with `❓`
  - include the service name so the question is understandable out of context.
  - include a lightweight progress indicator with remaining count (e.g., `Progress: Q3/10 (7 left)`) when doing bulk refinement.
  - every 5 questions, remind the user they can pause and resume refinement at any time, and include the estimated remaining *batches of 5* (e.g., `~3 batches left`).
  - avoid asking tautological applicability questions when the input already implies at least one affected resource (e.g., title-only exports phrased as “should …`).
- When you adjust a finding score based on user confirmation or `Output/Knowledge/`, add a one-line note (e.g., `Score change: 5/10 ➜ 7/10 — confirmed internet-facing prod exposure`).
- **Applicability check (per finding):**
  - For *evidence-backed* findings (e.g., scanner output that clearly indicates a failing resource), treat applicability as **Yes** by default and ask only scoping questions that change severity/remediation.
  - For *recommendation-style* findings where applicability is genuinely unclear, ask one question to establish whether the condition is currently true (Yes / No / Don’t know).
  - **Conflicting signals / partial coverage rule:** if user-provided context says a control is enabled (e.g., “Yes, WAF is enabled”) but the finding still exists (or other evidence suggests it’s not universal), assume **partial coverage**.
    - Do **not** downgrade based on the optimistic answer.
    - Default to the **worse (more severe) interpretation** and ask a single follow-up question explaining the conflict (e.g., multiple subscriptions/environments, or user uncertainty).
    - If the user answers “Yes — all”, but the finding indicates otherwise, keep severity higher until the scope is reconciled.
  - If applicability is **No** (confirmed false positive / out of scope), mark as **FALSE POSITIVE** and handle based on source:
    - **External scanner source (Snyk, Aikido, Defender, etc.):** Recommendation should be to mark the finding as false positive IN THE TOOL ITSELF (not just document). This prevents repeated alerting and keeps the scanner signal-to-noise ratio high.
    - **Repo scan source (dotnet list package, npm audit, manual analysis):** No external tool to suppress in, so mark as **FALSE POSITIVE (Informative)** with `- **Overall Score:** 🟢 **FALSE POSITIVE (Informative)**`. Document the rationale for future reference but no remediation action required.
    - **Key distinction:** Scanner-based findings can be suppressed at source; repo scan findings cannot, so they're documentation only.
    - **Example false positive scenarios:** CVE applies to different runtime version, vulnerability requires feature not in use, transitive dependency not executed, environment-specific CVE that doesn't apply to this deployment.
- **Scope discipline:** do **not** create new findings that were not in the original
  input list (e.g., title-only export). It’s fine to:
  - add new environment context to `Output/Knowledge/`, and
  - update the *existing* finding to note: "if X is true, the score increases" (or
    set Applicability to **Yes** when confirmed).
- **Post-triage assumption confirmation:** after bulk triage (or whenever assumptions accumulate), ask follow-up questions to confirm/deny assumptions.
  - Ask **service-specific** questions where possible.
  - Ask **cross-cutting** questions once (e.g., “Are Private Endpoints used anywhere?”) and then apply the answer across relevant services.
  - Prefix these prompts with `❓` so they’re easy to spot in chat history.
- **Audit logging (MANDATORY for all sessions):**
  - Create `Output/Audit/Session_YYYY-MM-DD_HHMMSS.md` at the start of each triage session (use timestamp from session start).
  - **Log ALL of the following:**
    - **Session initialization:** triage type selected, cloud provider, intake path, repo path, scan scope
    - **Questions asked:** Every `❓` question asked during triage with timestamp
    - **User answers:** All user responses (including "Don't know" / freeform / multiple choice selections)
    - **Assumptions made:** When the agent infers context and records it as an assumption
    - **Actions taken:** Finding created/updated, Knowledge updated, Summary regenerated
    - **Bulk operations:** Import source, count of items, which items were processed
    - **Score changes:** When findings are rescored (initial → Dev skeptic → Platform skeptic)
  - **Audit log format:**
    ```markdown
    # 🟣 Audit Log - Session YYYY-MM-DD HHMMSS
    
    **AUDIT LOG ONLY — do not load into LLM triage context**
    
    ## Session Metadata
    - **Date:** DD/MM/YYYY
    - **Start time:** HH:MM
    - **Triage type:** Cloud / Code / Repo scan / Mixed
    - **Provider:** Azure / AWS / GCP / N/A
    - **Intake source:** <path or "Interactive paste">
    - **Scan scope:** IaC+SCA / All / N/A
    
    ## Scan Timing & Tools
    
    ### Scan Type: <IaC / SCA / SAST / Secrets>
    - **Duration:** MM:SS or HH:MM:SS
    - **Tools used:** <comma-separated list of tools/commands>
    - **Findings count:** N
    - **Status:** Completed / Failed / Skipped
    
    ## Q&A Log
    
    ### HH:MM - Question
    ❓ <question text>
    
    **Answer:** <user response>
    
    **Action taken:** <what was done with this answer - e.g., "Updated Azure.md Confirmed section", "Set applicability to Yes">
    
    ## Actions Log
    
    ### HH:MM - <Action Type>
    - **Action:** <Created/Updated/Deleted>
    - **Target:** <file path>
    - **Reason:** <why this action was taken>
    - **Impact:** <what changed - e.g., "Added 3 services to Confirmed", "Score changed 7→5">
    
    ## Bulk Operations
    
    ### HH:MM - Bulk Import
    - **Source:** <path>
    - **Items count:** N
    - **Items processed:** <list or "See details below">
    - **Duration:** <if long-running>
    
    ## Token Usage by Operation
    
    | Operation | Duration | Tokens | Efficiency (tok/sec) | Model |
    |-----------|----------|--------|----------------------|-------|
    | Session kickoff | MM:SS | N | N/A | Sonnet 4.5 |
    | Git history analysis | MM:SS | N | X.X | Sonnet 4.5 |
    | IaC scan | MM:SS | N | X.X | Sonnet 4.5 |
    | Finding generation | MM:SS | N | X.X | Sonnet 4.5 |
    | **Total** | **HH:MM** | **N** | **Avg: X.X** | - |
    
    ### Token Budget
    - **Allocated:** 1,000,000 tokens
    - **Used:** N tokens (X.X%)
    - **Remaining:** N tokens
    
    ## Summary
    - **Total findings created:** N
    - **Total findings updated:** N
    - **Knowledge files updated:** <list>
    - **Summaries regenerated:** <list>
    - **Questions asked:** N
    - **Assumptions made:** N (see Knowledge files for details)
    ```
  - **Token tracking:** Track token consumption deltas for major operations (scan types, finding generation, skeptic reviews) by noting system warnings before/after operations. Calculate efficiency (tokens/second) to identify optimization opportunities.
  - **When to append (not replace):** Always append to the session log, never overwrite previous entries
  - **Audit log size:** for bulk title imports, prefer an audit summary (count + source file path + timestamp). Only include per-item lists when the user explicitly asks or when count is <20 items.
  - **Audit log is append-only:** clearly mark at the top of each audit file as shown above. These logs are for human review and compliance tracking, not for feeding back into context windows.

- When kickoff questions are answered (triage type, cloud provider, repo path, scanner/source/scope, repo roots), check whether the answer adds new context vs existing `Output/Knowledge/`.
- **Repo scans:**
  - Prefer using `python3 Scripts/scan_repo_quick.py <abs-repo-path>` for an initial structure + module + secrets skim (stdout only).
  - **Create repo summary FIRST:** Before creating any findings, immediately create `Output/Summary/Repos/<RepoName>.md` following the `Templates/RepoFinding.md` template. This ensures all findings can link to the summary and the summary can be progressively updated as the scan progresses. Use the exact repo name as-is (e.g., `my_api.md` for repo `my_api`, not `Repo_my_api.md` or `Repo_MY_API.md`).
  - Repo findings should include `## 🤔 Skeptic` with both `### 🛠️ Dev` and `### 🏗️ Platform` sections (same as Cloud/Code findings).
  - **Track scan timing and tools used:** For each scan type (IaC, SCA, SAST, Secrets), record start time, end time, duration, tools/commands used, findings count, and status. Log in audit file under `## Scan Timing & Tools` section. See `Agents/RepoAgent.md` for details and tool examples.
  - **After creating findings, automatically run skeptic reviews:** Once repo scan findings are created, immediately run both Dev and Platform skeptic reviews in parallel:
    - Launch `general-purpose` task agent for Dev Skeptic review (follows `Agents/DevSkeptic.md`)
    - Launch `general-purpose` task agent for Platform Skeptic review (follows `Agents/PlatformSkeptic.md`)
    - Both agents should update the `### 🛠️ Dev` and `### 🏗️ Platform` sections respectively
    - Wait for both to complete before presenting final summary to user
  - **Scanner scope defaults to "IaC + SCA"** (logic discovery + code flow bugs) — SAST is available but not default (more time-intensive, less actionable for initial triage).
  - **Code findings must be fully populated (no FILL placeholders):** Unlike bulk cloud finding generation (which uses FILL for user-provided context), code findings from repo scans must have all sections completed with evidence-backed content. Use the CodeFinding template sections with actual findings from the scan.
  - **Prioritise IaC/platform repos first:** When the user has IaC repos (Terraform/Pulumi/CloudFormation) or platform/shared module repos available, **strongly recommend scanning those first** before triaging cloud findings. Explain the value:
    - "Scanning your IaC/platform repos first will help me understand your security defaults, intended architecture, and existing controls. This makes cloud finding triage much more accurate - I'll know which controls are already baked into your platform layer."
    - Look for repo names containing: `*-modules`, `*-platform*`, `terraform-*`, `pulumi-*`, `cloudformation-*`, `infrastructure`, `iac`
  - **Multi-repo scans:** when scanning multiple repos (e.g., from a wildcard pattern):
    - **Ask for permission first** before launching parallel scans. Tell the user:
      - How many repos will be scanned
      - How many parallel batches (e.g., "3 batches of 5, 5, and 4 repos")
      - What will be created (repo findings, knowledge updates, audit entries)
      - Estimated total time
      - **Note:** No real-time progress bars per repo (task agents run independently); you'll see batch completion summaries
      - Offer choices: **"Proceed with batched scans"** / **"Scan one at a time"** / **"Cancel"**
    - Delegate each repo scan to a separate `general-purpose` task agent so each has its own full context window.
    - **Launch repo scans in parallel batches** using adaptive sizing:
      - **Start conservative:** First batch = 3 repos
      - **If batch succeeds (all complete):** Increase next batch (e.g., 5 repos)
      - **If batch has failures/interruptions:** Reduce next batch (e.g., 2 repos)
      - This learns the actual system concurrency limits empirically
    - **Before launching each batch**, tell the user:
      - Current batch (e.g., "Batch 1/3: scanning 5 repos...")
      - Estimated time for the batch (roughly 1-3 minutes per repo)
    - **After each batch completes**, immediately summarize:
      - Which repos in the batch completed successfully
      - Any that failed or were interrupted
      - Progress indicator (e.g., "7/14 repos completed")
    - **After all scans complete**, run a **consolidation pass**:
      1. Review all generated repo summaries under `Output/Summary/Repos/`
      2. Check `Output/Knowledge/` for cross-repo patterns (shared modules, common auth patterns, repeated issues)
      3. Identify **countermeasures** (controls in one repo that mitigate risks in another)
      4. Identify **compounding issues** (weaknesses that chain across repos)
      5. Update finding scores and add cross-references using clickable markdown links under `## Compounding Findings` sections
      6. **Synchronize diagrams:** Review cloud architecture diagrams (`Output/Summary/Cloud/Architecture_*.md`) and verify consistency with repo-specific diagrams. Check authentication flows, network boundaries, service relationships. Update cloud architecture if new information discovered. See `Agents/ArchitectureAgent.md` for synchronization guidance.
      7. Regenerate risk register: `python3 Scripts/risk_register.py`
  - First check `Output/Knowledge/Repos.md` for known repo root path(s).
  - If it doesn’t exist or is empty, **suggest a default based on the current working directory**.
    - Prefer using the stdout-only helper to avoid guesswork: `python3 Scripts/get_cwd.py` (prints `cwd` + `suggested_repos_root`).
    - Then ask: **"I don’t currently know the root directory for your repos — should I use `<suggested path>`?"** (include **Yes / No / Don’t know**).
  - If the user confirms or provides an alternative, persist it into `Output/Knowledge/Repos.md`.
  - **Only after** at least one repo root is recorded (or the user explicitly confirms **"current repo"**), ask which repo/directory under that root should be scanned.
    - Accept either a single repo name/path, a list (comma/newline separated), or a simple wildcard/prefix pattern like `terraform-*`.
    - If the user provides a pattern/wildcard, **expand it into concrete repo names** and ask for an explicit confirmation of the expanded list before scanning.
    - If many repos match and the user hasn’t expressed a priority: scan shared module repos first (e.g., `*-modules`), then edge networking/security repos (network, firewall, gateway/WAF, DDoS), then identity, then data stores, then app/service repos.
  - Do not ask for language/ecosystem up-front; infer **languages + frameworks** from repo contents (lockfiles, build files, manifests, imports) and record them in the repo summary.
  - **Extract repository purpose** from README files, package/project metadata, repo name patterns, or inferred from code structure/primary functions. Record in the repo summary under `## 🧭 Overview` and in `Output/Knowledge/Repos.md` where it provides reusable context. Example purposes: "Terraform platform modules for Azure PaaS", "API gateway service", "CI/CD pipeline definitions", "Shared authentication library".
  - **Repository knowledge structure:** 
    - **Repo summary (CREATED FIRST):** Create `Output/Summary/Repos/<RepoName>.md` following `Templates/RepoFinding.md` structure as the FIRST step of any repo scan. This file tracks architecture diagram, languages/frameworks, security review, skeptic reviews, and recommendations. All subsequent findings should link back to this summary. Use the exact repo name as-is (e.g., `my_api.md` for repo `my_api`, not `Repo_my_api.md` or `Repo_MY_API.md`).
    - **Detailed knowledge:** Create `Output/Knowledge/<RepoName>_Repo.md` for tech stack, dependencies, and reusable context
    - **Index:** Update `Output/Knowledge/Repos.md` as an index/summary only
  - **Cloud architecture extraction (MANDATORY for repos with IaC or cloud services):** When a repo scan discovers cloud architecture context (Azure/AWS/GCP services, ingress paths, network patterns, authentication mechanisms), immediately create/update:
    - `Output/Knowledge/<Provider>.md` (e.g., Azure.md, AWS.md) - Add discovered services, network topology, authentication patterns under `## Confirmed` or `## Assumptions`
    - `Output/Summary/Cloud/Architecture_<Provider>.md` - Create/update architecture diagram showing discovered services, connections, and security controls (follow `Agents/ArchitectureAgent.md`)
    - This is separate from the repo-specific knowledge - extract reusable cloud environment facts that apply across multiple applications
    - **Example:** If repo deploys to Azure App Service, add App Service to both Azure.md and Architecture_Azure.md
  - **Trace request ingress path:** For application/service repos, determine how requests reach the service by examining:
    - IaC files (load balancers, API gateways, ingress controllers, public IPs, network configs)
    - Application configuration (listening ports, hostnames, base URLs)
    - Middleware/routing code (reverse proxy patterns, forwarding logic, HTTP client calls)
    - README/documentation (deployment architecture, request flow descriptions)
    - **CRITICAL - Verify direction:** Don't assume! Check if service is BEHIND gateway (inbound: Gateway → Service) or CALLING gateway (outbound: Service → Gateway). Look for:
      - **Inbound indicators:** Gateway/LB forwards to service, service receives traffic from gateway
      - **Outbound indicators:** Service makes HTTP calls TO gateway/API, reverse proxy pattern, HTTP client to external API
      - **README descriptions:** "reverse proxy to...", "routes requests to...", "calls into..."
    - **Record in architecture diagram:** Show the full path from origin (Internet/VPN/Internal) → entry point → service **as a Mermaid flowchart** in the repo summary's `## 🗺️ Architecture Diagram` section (NOT as text-based flow). Include middleware pipeline, authentication points, logging flows (dotted lines), and service dependencies.
    - **Mark as Assumption if uncertain:** If ingress path is inferred but not explicitly confirmed, mark with dotted border in diagram (`style node stroke-dasharray: 5 5`) and capture as assumption in Knowledge with validation steps
    - **Examples to detect:**
      - Direct public endpoint (App Service with public access)
      - Behind API Gateway (AWS API Gateway, Azure APIM, Kong)
      - Behind load balancer (ALB, App Gateway, nginx)
      - Internal-only (private endpoint, service mesh)
      - Hybrid (multiple ingress paths for different clients)
  - **Extract IaC provider versions** from repo scans (Terraform `required_providers` blocks, Pulumi, CloudFormation). Record in `Output/Knowledge/Repos.md`:
    ```markdown
    ## IaC Provider Versions
    - **Terraform azurerm:** ~> 3.85 (detected in terraform-platform-modules, 16/02/2026)
    - **Terraform aws:** ~> 5.x (detected in terraform-aws-infra, 16/02/2026)
    ```
  - **Look up security-relevant defaults** for detected provider versions and record in `Output/Knowledge/<Provider>.md` under `## 🏗️ IaC Provider Defaults`. Example defaults to capture:
    - **Azure (azurerm v3.x):** Storage Account public blob access, TLS version, SQL public network access, Key Vault network access, AKS RBAC/policy, ACR admin user
    - **AWS (aws v5.x):** S3 bucket ACL defaults, RDS public access, EC2 instance metadata defaults, Security Group defaults
    - Use these defaults during triage to distinguish **intended IaC config** vs **drift/manual changes**
  - If new: append it **immediately** to `Output/Knowledge/` as **Confirmed** with a timestamp.
  - If already captured: don’t duplicate.
  - If Cloud + provider is confirmed: immediately update `Output/Summary/Cloud/Architecture_<Provider>.md`.
- Prefer confirmed facts, **but capture inferred context** in `Output/Knowledge/` as an
  explicit **assumption** and then ask the user to confirm/deny.
- When a finding implies additional environment context (e.g., “Defender for Cloud” recommendations imply Defender is enabled), record it in `Output/Knowledge/` as an **assumption** and immediately ask the user to confirm/deny.
- When findings reference a specific cloud service as the **subject** of the finding (e.g., AKS, Key Vault, Storage Accounts), record that service as **Confirmed in use** in `Output/Knowledge/` without asking (the finding itself implies the service exists).
  - This also applies to **bulk title-only imports**: if a title clearly names an Azure service (e.g., “secure transfer on storage accounts”, “enable SQL auditing”, “disable ACR admin user”), treat that service as **Confirmed in use**.
- If a finding recommends enabling an **additional** service/control (e.g., DDoS Standard, Defender plan, Private Link), record that additional service/control as an **Assumption** until the user confirms.
- When processing findings in bulk (including sample findings), process items **sequentially**.
  - Use a default priority order unless the user overrides it:
    1) Internet exposure (public SSH/RDP, public PaaS endpoints, public management planes)
    2) High-value data stores (SQL/Cosmos/Storage) and secrets (Key Vault)
    3) Identity/privilege guardrails (owners/RBAC)
    4) Detection/logging/monitoring
    5) Hardening baselines
  - After completing one finding, **immediately continue to the next finding** without asking
    “should I continue?”.
  - Only pause for user input when you need a decision that materially changes remediation,
    applicability, scoring, or scope.
  - Still update `Output/Knowledge/` with inferred services/controls as **assumptions**, then ask the
    user to verify the assumptions as a follow-up step.
- Keep findings actionable: impact, exploitability, and concrete remediation.
  - The `### Summary` section should start with a **business-impact** sentence. The Risk
    Register “Business Impact” column is a **single short sentence** for management and
    should avoid countermeasure/implementation detail.
  - **Validated summary refresh:** when a finding’s `Validation Status` is set to `✅ Validated`, replace any title-only boilerplate in `### 🧾 Summary` with a short, evidence-backed summary based on **confirmed** context (do not over-claim specific resource IDs if you don’t have them yet).
  - **TL;DR - Executive Summary:** After collaboration (Dev/Platform Skeptic reviews) is complete, **immediately populate** the `## 📊 TL;DR - Executive Summary` section (which should be placed immediately after the architecture diagram). This provides security engineers quick access to:
    - Final score with adjustment tracking (Security Review → Dev → Platform)
    - Top 3 priority actions with effort estimates
    - Material risks summary (2-3 sentences)
    - Why the score changed (if adjustments were made)
    - **Critical:** The TL;DR must be populated by the skeptic review agents, not left as a placeholder. If using task agents for skeptic reviews, instruct them to populate the TL;DR section.
  - **Overall Score reconciliation:** After Dev and Platform Skeptic reviews are complete, update the top-level `- **Overall Score:**` line to show the full score progression. Format: `<emoji> **X/10** (<severity>) — *Final: Security Y/10 → Dev [✅/⬇️/⬆️]Z/10 → Platform [✅/⬇️/⬆️]X/10*` where X is the final reconciled score. This shows transparency in the decision-making process and which skeptic's recommendation was accepted.
  - **Validation Required:** If there are critical **unconfirmed assumptions** that could significantly change the risk score, add a `## ❓ Validation Required` section immediately after the TL;DR. This must:
    - Clearly state what assumption was made and why it matters
    - Show evidence found vs evidence NOT found
    - Explain impact on score if assumption is wrong
    - Ask a specific question for the human reviewer
    - Common critical assumptions: network ingress paths, public vs private access, authentication mechanisms, blast radius
    - Helper (writes files; use when needed): `python3 Scripts/update_validated_summaries.py --path Output/Findings/Cloud --in-place`
- When a finding is created or updated, **immediately** update `Output/Knowledge/` with any
  new inferred or confirmed facts discovered while writing the finding.
  - Capture inferred facts as **assumptions** and ask the user to confirm/deny.
  - Prefer reusable environment knowledge (services in use, guardrails, identity
    model, network defaults, dependencies/modules) over one-off resource IDs.
  - It is OK to list dependencies/modules (including private/internal module repos).
  - **Repo finding Key Evidence section:**
    - Use emoji markers: 💡 (in use/neutral signal), ✅ (security-positive), ❌ (security-negative)
    - For secret-like signals: check module context before flagging
      - If inside a secure module (e.g., Key Vault storage), use 💡 or ✅
      - Only flag as ❌ if cleartext exposure or insecure handling is confirmed
    - For language/framework detection: infer from lockfiles/build files (*.tf = Terraform, go.mod = Go, package.json = Node.js, etc)
      - Do NOT report CI systems or containers as languages
      - The scan script now outputs a "Languages/frameworks detected" section - use that
  - **Repo finding Overview "Evidence for detection":**
    - If single evidence file: show inline
    - If multiple evidence files: format as bullet list
  - If a repo scan finds **Terraform module usage**, automatically:
    1) extract and classify module dependencies using (stdout-only): `python3 Scripts/analyze_terraform_modules.py <repo-path>`
    2) scan any **local-path** modules immediately,
    3) for any module repo/path that is **not already recorded in `Output/Knowledge/Repos.md` (or otherwise known)**, ask the user whether you can scan it next to increase context/accuracy,
       - if the module source is a remote git URL (e.g., Azure DevOps/GitHub), first ask the user for the **local path** (or confirmation it exists under a known repo root) before attempting any scan,
       - for **Terraform Registry modules** (registry.terraform.io), **do not ask to scan them**; just record them as upstream dependencies in the repo finding/audit.
       - use `python3 Scripts/scan_repo_quick.py` for the initial scan.
    4) repeat this process recursively for newly scanned module repos until no new modules are discovered (or the user says stop).
  - **Terraform module value resolution:** when reviewing Terraform code that calls modules, do not assume a variable/output implies insecure behaviour in the root module.
    - Example: a variable named `secret` or an output named `client_secret` may be passed into a module that stores it in Key Vault and only returns a reference/ID.
    - Rule: if a repo uses modules, treat security-relevant intent (secrets handling, network exposure defaults, RBAC) as **potentially hidden inside modules**; prioritise scanning the module code before drawing conclusions.
    - If the module source is not locally available, ask the user for the local path (per the module discovery rules) so you can confirm how values are actually handled.
    - If you have **reasonable suspicion** (e.g., an output/variable appears secret-like, a pipeline consumes a value as a secret, or a resource suggests public exposure) and you cannot confirm intent from available code, it is OK to:
      - ask a single `❓` confirmation question, and/or
      - add a short **"Follow-up task for repo owners"** bullet in the repo finding describing what to verify (and where).
  - If a dependency/module points to another company repo (e.g., Terraform modules), ask the user to provide that repo next for better context.
  - For Dockerfiles, capture both the **dev/local image** and the **shipping/runtime base image** (often multi-stage builds with multiple `FROM` lines; the later stages are commonly the shipped service base).
  - When you discover CI/CD (pipelines, runners, deploy scripts), it is OK to ask clarification questions about:
    - where secrets are stored (vault vs CI variables vs cloud secret store) and whether they are encrypted/rotated,
    - how CI/CD authenticates to the target environment (OIDC/workload identity vs long-lived keys/service principals),
    - and how CI/CD reaches the environment (network path, VPN/peering, private endpoints).
  - If you detect **Hiera** (YAML hierarchy/overrides), treat it as an environment-scope signal, but **do not** ask about environment tiers during the repo scan itself. Record it in `Output/Knowledge/` as an **Assumption** and defer any environment-scope questions until the user starts cloud triage (or explicitly requests environment scoping).
- When `Output/Knowledge/` is created or updated (including assumptions), **immediately**
  generate or update the provider architecture diagram under `Output/Summary/Cloud/` (e.g.,
  `Output/Summary/Cloud/Architecture_Azure.md`) to reflect the current known state and
  include any newly discovered services.
  - **CRITICAL SCOPE RULE:** `Architecture_<Provider>.md` is **ALWAYS comprehensive platform-wide**, showing ALL discovered Azure/AWS/GCP services and infrastructure modules. It is **NEVER scoped to a single service or repo**.
  - **UPDATE, don't replace:** If the architecture file already exists, **update it** to add newly discovered services/modules. Do not replace the entire file with single-service content.
  - **Structure:** Include multiple focused diagrams (Ingress, Network, Data, Compute, Identity, IaC modules) rather than one monolithic diagram.
  - **Note:** This applies only to cloud findings and cloud-related knowledge. Code-only repo scans do NOT create `Output/Summary/Cloud/` files unless cloud architecture context (ingress paths, Azure/AWS/GCP services) is discovered during the scan.
  - This is a **standing rule throughout the session** (do not wait until session
    kickoff or the end of triage).
  - Draw the diagram **from the internet inwards** (request flow / access paths).
  - Prefer **top-down** Mermaid (`flowchart TB`) so external → internal flows read naturally.
  - Only include **confirmed services** on the Mermaid diagram unless the user explicitly asks
    to include assumed components.
  - If any `✅ Validated` findings still contain title-only boilerplate in `### 🧾 Summary`, refresh them (writes files): `python3 Scripts/update_validated_summaries.py --path Output/Findings/Cloud --in-place`
- While writing/updating cloud findings, scan the finding content for implied **cloud services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Output/Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
- **Cloud resource native defaults:** When triaging findings about specific cloud resources, look up the **native provider default** for that resource type and note it in the finding:
  - **Azure examples:**
    - Storage Account: Public network access **enabled by default** (public endpoint)
    - Azure SQL Server: Public network access **enabled by default** (requires explicit firewall rules)
    - Key Vault: Public network access **enabled by default**
    - Cosmos DB: Public network access **enabled by default**
    - App Service: Public by default (unless deployed into VNET)
  - **AWS examples:**
    - S3 Bucket: Block public access **enabled by default** (as of Apr 2023)
    - RDS Instance: Public accessibility **disabled by default**
    - EKS Cluster: Public endpoint **enabled by default**
  - **GCP examples:**
    - Cloud Storage Bucket: Public access **disabled by default**
    - Cloud SQL: Public IP **disabled by default** (must opt-in)
  - Record these in `Output/Knowledge/<Provider>.md` under `## 🏗️ Cloud Resource Native Defaults` as you discover them during triage
  - Use this context to assess findings: "Finding shows public Storage Account. Azure Storage defaults to public - this is expected **unless** private endpoints are explicitly configured or IaC overrides the default."
- **Finding content completeness:** ensure all findings have:
  - A clear **Overall Score** with severity and numeric score (e.g., `🔴 High 8/10`)
  - Proper **Summary** section (not generic boilerplate)
  - **Key Evidence** section with specific resource IDs, paths, or context
  - **Applicability** section with clear status (Yes/No/Don't know) and evidence
  - These sections are used by the risk register generator for accurate resource type classification and issue extraction
- When a recommendation depends on **platform SKU/tier/feature availability** (common examples: private endpoints, private registries, WAF features, auditing tiers), explicitly call out the dependency and note that remediation may require a **SKU change** (e.g., ACR private connectivity may require Premium depending on the provider/service).
- When a recommendation may require **reprovisioning/redeployment/restart** to take effect, explicitly warn about potential **downtime/maintenance windows** and rollout sequencing.
- For findings that materially affect platform operations (SKU changes, networking primitives, CI/CD constraints, or downtime risk), add a platform-engineering perspective under `## 🤔 Skeptic` → `### 🏗️ Platform` (see `Agents/PlatformSkeptic.md`).
- When a new finding overlaps an existing one, link them under **Compounding Findings**.
- **Avoid running git commands by default** (e.g., `git status`, `git diff`, `git restore`). Only use git when the user explicitly asks, and explain why it’s needed.
- **Avoid running scripts/automations by default**. If you propose running a script (including repo utilities like `python3 Scripts/risk_register.py`), first explain:
  - what it does,
  - what files it will write/change,
  - why it’s necessary now.
  - **Exception:** during **repo scans**, it is OK (and preferred) to run `python3 Scripts/scan_repo_quick.py <abs-repo-path>` as the default initial skim.
  - **Exception (user-requested automation):** if the user asks for summaries to update automatically as new information becomes available, it is OK to run `python3 Scripts/update_validated_summaries.py --path Output/Findings/Cloud --in-place` after each material Q&A/knowledge update (it only removes title-only boilerplate when there is confirmed/applicability context).
  - **Exception (user-requested automation):** if the user asks for descriptions to stop repeating titles, it is OK to run `python3 Scripts/update_descriptions.py --path Output/Findings/Cloud --in-place` after bulk imports and/or as part of draft validation.
  - **Exception (user-requested automation):** if the user asks to adjust scores based on confirmed countermeasures and compounding, it is OK to run `python3 Scripts/adjust_finding_scores.py --path Output/Findings/Cloud --in-place` after material Q&A/knowledge updates (it only adjusts when the finding contains confirmed context and records the applied drivers under `### 📐 Rationale`).
  - **Exception (user-requested automation):** if the user asks for the risk register to auto-regenerate, it is OK to run a watcher in a separate terminal: `python3 Scripts/watch_risk_register.py` (or `--full` to also run the refresh helpers).
- **Automation language preference:** when automating a repo task, prefer **Python** over other
  languages to minimize extra dependencies the user may need to install.

## Outputs

- **Default behaviour:** outputs under `Output/Findings/`, `Output/Knowledge/`, and `Output/Summary/` are
  **generated per-user/session and are intentionally untracked** (see `.gitignore`).
  Change that only if you explicitly want to commit triage artifacts.
  - **File path references:** When referencing files within the Triage-Saurus repository (findings, knowledge, templates, agents), use **clickable markdown links with relative paths** from the current file location (e.g., `[Finding.md](../../Findings/Cloud/Finding.md)`, not inline code like `` `Output/Findings/Cloud/Finding.md` ``). External repo paths can remain as inline code.

- **Cloud findings:** `Output/Findings/Cloud/<Titlecase>.md`
- **Code findings:** `Output/Findings/Code/<Titlecase>.md`
  - **Note:** Repo scans that identify specific code-level security vulnerabilities (e.g., SQL injection, XSS, insecure deserialization) should extract those as individual findings under `Output/Findings/Code/` for tracking and remediation.
- **Repo scan summaries:** `Output/Summary/Repos/<RepoName>.md` (one file per repo; follows `Templates/RepoFinding.md` structure with architecture diagram, security review, skeptic reviews, and metadata; use exact repo name without prefix)
  - Should reference any extracted code findings using clickable markdown links under `## Compounding Findings` or in relevant finding summaries
  - **Cloud architecture knowledge:** When scanning a repo, any cloud architecture knowledge discovered (ingress paths, services used, authentication patterns, network topology) should be immediately captured in:
    - `Output/Knowledge/<Provider>.md` (confirmed services, controls, architecture facts)
    - `Output/Summary/Cloud/Architecture_<Provider>.md` (updated architecture diagrams)
- **Cloud summaries:** `Output/Summary/Cloud/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Scripts/risk_register.py`
- **Optional bulk draft generator (titles → findings):** `python3 Scripts/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
  - With `--update-knowledge`, it also generates `Output/Summary/Cloud/*.md` per-service summaries, regenerates
    `Output/Summary/Risk Register.xlsx`, and appends audit entries under `Output/Audit/`.

## After changes to findings
- **Risk register must stay current:** after creating or updating any finding, regenerate:
  - `python3 Scripts/risk_register.py` (updates `Output/Summary/Risk Register.xlsx`)
- If you need a quick, consistent score list (for summaries/architecture notes), run:
  - `python3 Scripts/extract_finding_scores.py Output/Findings/Cloud`
  - Output: a Markdown table to stdout (Finding link + **Overall Score** + description).

## Mermaid diagram validation (MANDATORY)
- **After creating or updating any file with Mermaid diagrams** (findings, summaries, architecture diagrams, repo summaries), **ALWAYS run:**
  - `python3 Scripts/validate_markdown.py --path <path-to-file-or-directory>`
  - This validates Mermaid syntax and ensures **no `fill:` attributes** (which break dark themes)
- **Critical rule:** NEVER use `fill:#` in Mermaid style blocks. Use `stroke:` and `stroke-width:` instead.
  - ❌ `style node fill:#ff6b6b,stroke:#c92a2a` → ✅ `style node stroke:#c92a2a,stroke-width:3px`
- **Traffic Flow Standard (REQUIRED):** Use Mermaid `flowchart LR` diagrams for sequential traffic flows in repo summaries
  - ✅ Visualize request paths, authentication flows, data flows as Mermaid diagrams
  - ✅ Apply colored borders to show component types (security, network, identity, data)
  - ✅ Simple fan-out patterns (e.g., "APIM → 7 backends") can remain text-based lists
  - ❌ Long text arrow chains (`A → B → C → D → E → F`) are hard to scan - use Mermaid instead
- **Colored borders (REQUIRED for traffic flows, RECOMMENDED elsewhere):**
  - Security (red): `#ff6b6b` - Firewalls, WAF, auth services, security controls
  - Network (blue): `#1971c2` - VNets, subnets, gateways, load balancers, routing
  - Identity (orange): `#f59f00` - Key Vault, AAD, managed identities, secrets
  - Data (teal): `#96f2d7` - Databases, storage accounts, data services
  - Stroke width: 3px for critical components, 2px for secondary
- **UTF-8 handling:** Emojis are acceptable in Mermaid diagrams (node labels AND subgraph labels)
  - ✅ **ALWAYS use edit/create tools** for files with emojis or Unicode characters
  - ❌ **NEVER use bash heredocs** (`cat << 'EOF'`) for UTF-8 content - causes Unicode corruption
  - Example corruption: `🔗` becomes `��` when using heredocs
- See `Agents/ArchitectureAgent.md` and `Agents/ContextDiscoveryAgent.md` for complete Mermaid styling rules.

## Utility scripts
- **Clear session artifacts (destructive):**
  - Dry-run: `python3 Scripts/clear_session.py`
  - Delete: `python3 Scripts/clear_session.py --yes`

- Ensure each finding includes:
  - `## 🗺️ Architecture Diagram` **directly under the title** (first section, before Overview)
  - `- **Overall Score:** <severity> <n>/10` **immediately after the diagram** (before Overview)
  - `## Meta Data` as the final section in the file
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
  - **All finding types** (Cloud, Code, Repo) must include the Architecture Diagram section
