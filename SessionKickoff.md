# 🟣 Session Kick-off

## Purpose
This document provides the session initialization flow for Triage-Saurus. When the user types `sessionkickoff`, the agent should:
1. Load canonical operating rules from `AGENTS.md` and `Agents/Instructions.md`
2. Scan the workspace for existing context
3. Check for outstanding refinement questions
4. Present triage options to the user

**Note:** This file contains only the kickoff flow. For detailed navigation flows and menu structures, see `Templates/Workflows.md`. All detailed operational rules (bulk processing, question formatting, repo scanning, knowledge recording, etc.) are in `Agents/Instructions.md`.

## Helper Scripts

Prefer the consolidated workspace scanner (stdout-only):
- `python3 Scripts/scan_workspace.py` — scans Knowledge/, Findings/, and common Intake/sample paths

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

3. **Create audit log:** Create `Output/Audit/Session_YYYY-MM-DD_HHMMSS.md` using the template from `Templates/AuditLog.md`. Log session metadata (date, start time, triage type TBD).

4. **Scan workspace:** Run `python3 Scripts/scan_workspace.py` to check:
   - Output/Knowledge/ for refinement questions (## Unknowns / ## ❓ Open Questions)
   - Output/Findings/ for existing findings
   - Intake/ and Sample Findings/ for available triage items

5. **Check for refinement questions:**
   - If outstanding questions exist: ask whether to resume answering those now (or proceed to new triage).
   - If Knowledge/ is empty (0 knowledge files): treat as first run and say "🦖 Welcome to Triage-Saurus."

6. **Present triage menu** using ask_user tool with selectable choices:
   - **Answer questions to build context** (if existing knowledge/findings exist)
   - **Copy/paste a single issue to triage**
   - **Provide a path under Intake/ to process in bulk**
   - **Scan a repo**
   - **Scan a sample repo**
   - **Import and triage the sample findings**

7. **Handle bulk intake selection:**
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
   - Check Output/Knowledge/Repos.md for known repo root path(s).
   - If none recorded: suggest default using `python3 Scripts/get_cwd.py`
   - Ask user to confirm the repos root directory path.
   - **Request repos folder access permission:** Before any repo operations, ask user once to grant read access to the repos directory (e.g., `/mnt/c/Repos`). This covers discovery and scanning. Do NOT ask again for individual repos during the session.
   - Discover available repos: `ls -1 <confirmed_repos_root_path>`
   - Present repos as selectable choices using ask_user tool:
     - List all individual repo names as choices
     - Add special choices like "Scan all terraform-* repos" or "Scan multiple repos (specify pattern)"
     - Allow freeform input for custom repo names/patterns
   - If wildcard pattern selected: expand to concrete names and confirm before scanning.
   - **DO NOT hand off to general-purpose agent yet**
   - **Phase 1 - Fast Context Discovery (<1 min):**
     - Run parallel explore agents to discover context (see Agents/ContextDiscoveryAgent.md for discovery targets)
     - Use 4-6 parallel explore agents for: purpose/README, tech stack, IaC files, ingress points, **traffic flow (MANDATORY)**, databases, architecture
     - Create `Output/Summary/Repos/<RepoName>.md` with discovered context including:
       - 🗺️ Architecture Diagram (Mermaid) at the top
       - 🚦 Traffic Flow section showing complete request path
     - Update `Output/Knowledge/Repos.md` with repository entry
     - Generate architecture diagram with findings
   - **Phase 2 - Security Scanning (if requested):**
     - Based on context, decide which scans to run (IaC/SCA/SAST/Secrets)
     - Can use task agent for long-running scans or run directly
   - See Agents/Instructions.md lines 118-240 for detailed repo scan rules.

11. **Follow operational rules:**
   - Log ALL questions, answers, actions, and assumptions to the audit log (append-only)
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
