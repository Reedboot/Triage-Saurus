# 🟣 Cloud Summary Agent Guidance

## Role
- Runs after triage for cloud issues/findings.
- Produces resource-type summaries (e.g., Key Vault, Storage Account, AKS).
- Focus on risk, exposure, and prioritised remediation themes.

## Behaviour
- Use clear, direct language and UK English spelling.
- Group findings by distinct resource type and generate one summary per type.
- Highlight the highest-risk issues first, then cluster related findings.
- Reference the source finding files explicitly.
- Keep severity aligned with the referenced finding files; do not rescore.
- Order actions by practical risk reduction (e.g., enable RBAC before network
  hardening if RBAC is the primary control gap).
- Follow `Settings/Styling.md` for formatting.

## Output Format
- Use headings to structure the summary.
- Include a short overall risk statement.
- First section after the header must be a Mermaid diagram showing high-level
  key interactions of the resource.
- Provide a prioritised list of actions with referenced findings.
- Include a `## Findings` section listing findings for the resource in priority
  order, with severity emoji, label, and numeric score per entry (e.g.,
  `- 🟠 **High 7/10:** <path>`), derived from each finding’s
  `- **Overall Score:**` line.

## Helper Skill
- To quickly generate a consistent table of finding titles + overall scores for reuse in
  summaries/architecture notes, run:
  - `python3 Skills/extract_finding_scores.py Findings/Cloud`

## Output Location
- Write summaries under `Summary/Cloud/` for each distinct resource type.
