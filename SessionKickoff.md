# 🟣 Session Kick-off

## Purpose
This document provides the session initialization flow for Triage-Saurus. When the user types `sessionkickoff`, the agent should:
1. Load canonical operating rules from `AGENTS.md` and `Agents/Instructions.md`
2. Check for experiment state (for cross-session continuity)
3. Scan the workspace for existing context
4. Check for outstanding refinement questions
5. Present triage options to the user

**Note:** This file contains only the kickoff flow. For detailed navigation flows and menu structures, see `Templates/Workflows.md`. All detailed operational rules (bulk processing, question formatting, repo scanning, knowledge recording, etc.) are in `Agents/Instructions.md`.

## Helper Scripts

Prefer the consolidated workspace scanner (stdout-only):
- `python3 Scripts/scan_workspace.py` — scans Knowledge/, Findings/, and common Intake/sample paths

Experiment management (for self-optimizing triage):
- `python3 Scripts/triage_experiment.py resume` — check experiment state, continue from last position
- `python3 Scripts/triage_experiment.py status` — detailed experiment + learning status
- `python3 Scripts/triage_experiment.py list` — show all experiments with metrics
- `python3 Scripts/learning_db.py status` — show SQLite learning database status

Targeted helpers (stdout-only):
- `python3 Scripts/scan_knowledge_refinement.py`
- `python3 Scripts/scan_findings_files.py`
- `python3 Scripts/scan_intake_files.py <Intake/Subfolder>`
- `python3 Scripts/triage_queue.py` — use after bulk imports to identify common missing context
- `python3 Scripts/get_cwd.py` — suggests repo root path based on current directory
- `python3 Scripts/compare_intake_to_findings.py --intake <path> --findings <path>` — checks for duplicates before bulk processing

## Kickoff Flow

```text
1. **Load instructions:** Read AGENTS.md and Agents/Instructions.md for operating rules.

2. **Request Output folder permission:** At the start of the session, ask user once to grant write access to the `Output/` folder. This covers all operations (audit logs, findings, knowledge, summaries). Do NOT ask again during the session.

3. **Check experiment state:** Run `python3 Scripts/triage_experiment.py resume` to check:
   - If an experiment is in progress → offer to continue it
   - If experiment awaiting review → prompt for review
   - If learning pending → offer to apply learnings
   - If fresh/no experiments → proceed to normal triage flow

4. **Create audit log:** Create `Output/Audit/Session_YYYY-MM-DD_HHMMSS.md` using the template from `Templates/AuditLog.md`. Log session metadata (date, start time, triage type TBD).

5. **Scan workspace:** Run `python3 Scripts/scan_workspace.py` to check:
   - Output/Knowledge/ for refinement questions (## Unknowns / ## ❓ Open Questions)
   - Output/Findings/ for existing findings
   - Intake/ and Sample Findings/ for available triage items

6. **Check for refinement questions:**
   - If outstanding questions exist: ask whether to resume answering those now (or proceed to new triage).
   - If Knowledge/ is empty (0 knowledge files): treat as first run and say "🦖 Welcome to Triage-Saurus."

7. **Present triage menu** using ask_user tool with selectable choices:
   - **Continue experiment** (if experiment in progress)
   - **Start experiment mode** (for self-optimizing triage)
   - **Answer questions to build context** (if existing knowledge/findings exist)
   - **Copy/paste a single issue to triage**
   - **Provide a path under Intake/ to process in bulk**
   - **Scan a repo**
   - **Scan a sample repo**
   - **Import and triage the sample findings**

8. **Handle bulk intake selection:**
   - If user chooses bulk intake, offer selectable folder paths (no numeric prefixes).
   - Verify folders are non-empty using `python3 Scripts/scan_intake_files.py <path>` before offering.
   - Common paths in this repo:
     - Intake/Cloud
     - Intake/Code
     - Sample Findings/Cloud
     - Sample Findings/Code
   - Before starting bulk triage, check for duplicates:
     `python3 Scripts/compare_intake_to_findings.py --intake <path> --findings Output/Findings/Cloud`
   - If duplicates found: ask to proceed with new items only.
   - If no new items: stop and notify user.

8. **Infer triage type:**
   - If folder path implies scope (Intake/Cloud, Intake/Code), use that.
   - Otherwise, ask what to triage (Cloud / Code / Repo scan).

9. **Cloud triage initialization:**
   - Infer provider from folder name or skim intake titles.
   - If provider strongly indicated, explain reasoning with 🤔 and confirm with ❓.
   - Choices: Azure / AWS / GCP / Don't know
   - See Agents/CloudContextAgent.md for targeted context questions.
   - Create/update: Output/Knowledge/<Provider>.md and Output/Summary/Cloud/Architecture_<Provider>.md

10. **Repo scan initialization:**
   - **FIRST: Request repos folder access permission** — Before checking for repos or doing any repo operations, ask user once to grant read access to the repos directory (e.g., `/mnt/c/Repos` or wherever repos are stored). This covers discovery and scanning. Do NOT ask again for individual repos during the session.
   - Check Output/Knowledge/Repos.md for known repo root path(s).
   - If none recorded: suggest default using `python3 Scripts/get_cwd.py`
   - Ask user to confirm the repos root directory path.
   - Discover available repos: `ls -1 <confirmed_repos_root_path>`
   - Present repos as selectable choices using ask_user tool:
     - List all individual repo names as choices
     - Add special choices like "Scan all terraform-* repos" or "Scan multiple repos (specify pattern)"
     - Allow freeform input for custom repo names/patterns
   - If wildcard pattern selected: expand to concrete names and confirm before scanning.
   - **DO NOT hand off to general-purpose agent yet**
   - **Phase 1 - Fast Context Discovery (~10 seconds per repo):**
      - Use the Rules/ catalog (Rules/Summary.md) to derive rule-based grep patterns and guide discovery; run programmatic grep checks when opengrep isn't available.

     - Run `python3 Scripts/discover_repo_context.py <repo_path> --repos-root <repos_root_path>` for each repo
     - Script discovers: languages, IaC/orchestration (Terraform, Helm, Skaffold), container runtime (Dockerfile analysis), network topology (VNets, NSGs), hosting, CI/CD, routes, authentication, dependencies
     - Script automatically creates `Output/Summary/Repos/<RepoName>.md` with:
       - 🗺️ Architecture Diagram (Mermaid) - infrastructure topology with colored borders
       - 📊 TL;DR - Executive summary with Phase 2 TODO markers
       - 🛡️ Security Observations - Detected controls, Phase 2 guidance
       - 🧭 Overview - Purpose, hosting, dependencies, auth, container/network details
       - 🚦 Traffic Flow - Phase 2 TODO marker with detected hints and route mappings table
     - Script automatically updates `Output/Knowledge/Repos.md` with repository entry
     - When running in **experiment isolation** mode (i.e., `--output-dir Output/Learning/experiments/<id>_<name>`), the script also generates an experiment-scoped provider architecture summary under `Summary/Cloud/Architecture_<Provider>.md`.
     - Review the generated summary before proceeding
   - **Phase 2 - Deeper Context Search (~30-60 seconds per repo):**
     - Launch ONE explore agent to complete Phase 2 TODO markers
     - Agent traces middleware execution order, routing logic, business purpose
     - Updates Traffic Flow section with complete details
     - See Agents/ContextDiscoveryAgent.md for Phase 2 prompt template
   - **Phase 3 - Security Review (manual, based on gathered context):**
     - Use Phase 1 + Phase 2 context to perform qualitative security review
     - Check auth flows, IaC configs, routing logic, error handling
     - Invoke Dev Skeptic and Platform Skeptic for scoring
     - Update TL;DR and Security Observations sections
   - **Phase 4 - Cloud Architecture Update (if IaC detected):**
     - Launch ArchitectureAgent to update `Output/Summary/Cloud/Architecture_<Provider>.md`
     - Shows where this repo/service fits in overall cloud estate
   - **Optional - Automated Vulnerability Scanning (if requested):**
     - SCA (dependency vulnerabilities), SAST (code scanning), Secrets, IaC misconfiguration scans
     - These are separate from the security review above
   - See Agents/Instructions.md lines 118-240 for detailed repo scan rules.

11. **Follow operational rules:**
   - Log ALL questions, answers, actions, and assumptions to the audit log (append-only)
   - After scans and security reviews, run a Post-Scan Rule Assessment: map findings to Rules/ and, if a finding could be rule-detected but no rule exists, create a draft rule under Rules/ with a test case and record it under `Output/Learning/experiments/<id>/proposed_rules/` or `Output/Learning/proposed_rules/`.
   - Update audit Summary section at end of session
   - All detailed triage behavior is in Agents/Instructions.md
   - Question formatting, bulk processing, knowledge recording, cross-cutting questions, etc.
   - Refer to specific agent files (DevSkeptic, PlatformSkeptic, SecurityAgent, etc.) for specialized reviews.
```

## See Also
- **Repo overview + workflow:** README.md
- **Navigation flows and menus:** Templates/Workflows.md
- **Canonical operating rules:** Agents/Instructions.md
- **Agent discovery:** AGENTS.md
