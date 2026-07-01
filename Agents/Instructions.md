# 🟣 Agent Instructions

## Purpose
Keep triage outputs consistent across findings, knowledge, summaries, and rules.

1. Triage an issue.
2. Update `Output/Findings/`.
3. Capture reusable facts in `Output/Knowledge/`.
4. Update `Output/Summary/` outputs when needed.
5. Add or improve rules in `Rules/` when detection gaps appear.

## Rules
- Treat `Rules/` as the source of truth for security checks.
- When `opengrep` is available, every IaC/code scan starts with:
  `opengrep scan --config Rules/ <target>`
- Do not use manual grep fallbacks for normal scanning.
- If a resource type is missing coverage, add a rule under `Rules/Detection/` and map it into the rule set.
- Rules must be portable across repositories of the same technology.
- If a finding cannot be expressed as a reusable rule, document it directly in the finding instead of forcing it into `Rules/`.

## Rule creation checks
- Use generic metavariables or constrained regex.
- Avoid project-specific names, tenants, hostnames, or subscription IDs.
- Avoid rules that are too broad to be meaningful.
- After adding a rule, run `python3 Scripts/Validate/validate_rule_portability.py <rule-file.yml>`.

## Scan workflow
1. Run context discovery first for a new repo.
2. Run `opengrep scan --config Rules/ <repo>` for the full rule set.
3. Run skeptic reviews for findings that need challenge/confirmation.
4. Record `detected_by_rule` in finding metadata.
5. If a repo scan produces no findings, perform manual review for missed issues.

## Context and knowledge hygiene
- Keep `Output/Knowledge/<Provider>.md` focused on reusable environment facts.
- Use `## Confirmed`, `## Assumptions`, and `## Unknowns` headings.
- Put append-only audit trails in `Output/Audit/` and mark them as audit-only.
- After scans, summarise new learnings into the correct `Output/Learning/` location.

## Session behaviour
- `sessionkickoff` means run the kickoff flow.
- `clearsession` means run the clear-session flow after a dry run and confirmation.
- If `Output/Findings/` is empty, treat the workspace as a fresh instance.
- For bulk work, verify candidate intake folders are non-empty before offering them.

## Repo scan sequence
- Confirm repo root access.
- Run fast context discovery.
- Run deeper code/context analysis if needed.
- Run the mandatory rule scan.
- Run security review and architecture updates if IaC is present.

## Manual review reminders
- Trace attack paths, not isolated snippets.
- Distinguish direct user input from internal/trusted data.
- Distinguish public endpoints from authenticated public endpoints.
- Score based on actual exploitability and reachable blast radius.

## See also
- `Agents/ContextDiscoveryAgent.md`
- `Agents/SecurityAgent.md`
- `Agents/RepoAgent.md`
- `Agents/ArchitectureAgent.md`
- `Agents/LearningAgent.md`
