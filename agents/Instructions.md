# Agent Instructions
This file is the canonical place to store instructions for AI agents interacting
with this repository.

- **Language:** Use UK English spelling throughout documentation and messages.
- **sample/ folder:** Use sample/ to store example outputs or test artefacts for
  findings and checks; it's safe to commit placeholder files (e.g., .gitkeep) to
  reserve the directory.
  - When asked to "test the styling", agents should delete previous contents of
    sample/ and generate new sample output files into sample/ following
    settings/Styling.md rules.
- **Sample findings only:** When reviewing findings, only use sample findings
  unless explicitly told to use non-sample findings. Treat sample findings as
  test data, not user environment knowledge.
- **Sample findings off limits:** Do not edit files under `sample findings/`
  unless the user explicitly asks you to.
- **Sample findings usage:** Do not use `sample findings/` for evaluation
  enrichment or context unless the user explicitly asks you to.
- **Recommendations formatting:** In `## 🛡️ Security Review`, list recommendations as
  checkboxes and include a per-recommendation downscore estimate using arrow emojis,
  e.g., `- [ ] <recommendation> — ⬇️ <score>➡️<reduced-score> (est.)`. Use `0` when the
  recommendation is expected to remove the risk entirely.
- **Templates:** Use `Templates/CloudFinding.md` for the cloud finding layout and
  `Templates/CodeFinding.md` for code findings, and refer to
  `settings/Styling.md` for formatting rules.
- **Summaries:** When reviewing cloud findings, update the relevant resource
  summary under `Summary/Cloud/`, and update the relevant cloud architecture
  diagram under `Summary/Cloud/Architecture_<Provider>.md`. When reviewing code
  repositories, update the repository summary under `Summary/Repos/`. Use the
  resource type or repository name as the filename and follow
  `settings/Styling.md`.
- **Risk register:** After any triage or review that changes findings, run the
  Risk Register workflow and regenerate `Summary/Risk Register.xlsx`.

When updating, prefer short, explicit directives and keep examples minimal.

## Operational Verbosity
- Agents should emit a brief, verbose log (in their output or comments)
  describing each processing step when handling a finding.
- Include which agent is running, the actions it's taking (e.g., parsing
  evidence, checking policies, scoring), and any key heuristics used.
- Logs should be concise but informative; avoid raw dumps of large evidence
  unless necessary.

## Review Clarification
- When asked to review files or findings, ask the user to confirm whether they
  want a security review or a triage review before proceeding. If the user does
  not specify, default to a triage review.
- When asked to perform a review or triage, run the full workflow: Security
  analysis, Dev sceptic, Platform sceptic, then collaboration that
  incorporates sceptic comments. Do not include a `## Triage` block in outputs.
- When reviewing code repositories, include a `## Configuration Reference`
  section that lists any bad configuration settings and the files they are in.
- Do not include timestamps inside `## 🤝 Collaboration`. Instead, add a
  `## Meta Data` section containing a `Last updated` entry with an emoji for
  findings in `Findings/` following `settings/Styling.md` date format.
- Use `## 🛡️ Security Review` as the section heading for the security review.
- In `## 🛡️ Security Review`, include the rationale for why the score is valid
  and a brief description of how the issue could be exploited.

## Finding Cross-Checks
- Always check existing findings to see if they compound the new issue when
  performing a review.
- If findings compound, state that clearly, review both issues, and add a
  backlink between them.

## Styling
- All agents must follow the repository styling rules defined in
  settings/Styling.md when producing or editing markdown files.
- Do not include an `Evidence` line in findings outputs.

## Knowledge Updates
- Create and maintain domain files under `Knowledge/` (e.g., Azure.md, Code.md,
  DevOps.md).
- When an agent learns something new, append it to the appropriate file and
  include a "Last updated" timestamp in UK date/time format
  (DD/MM/YYYY HH:MM with a colon) in the file footer.

## File Naming Convention

- **Filenames:** Use Titlecase for filenames where the first letter is
  uppercase and the rest of the word is lowercase (e.g., Instructions.md,
  Readme.md).
- **Folder names:** Use Titlecase for folder names where the first letter is
  uppercase and the rest of the word is lowercase (e.g., Findings/, Sample/).

## Chat Behavior

- When an AI or CLI session is initialised, ask: "Would you like a security
  issue triage, or is there something else I can help with?"
- When an agent learns something new, the immediate chat response must begin
  with a lightbulb emoji (💡) and include a one- or two-sentence explanation of
  the learning in chat only. Do not place any other text before the emoji.
- When a user indicates they want to provide an issue for triage, respond with a
  direct prompt: "What issue would you like me to triage? Please share the
  scanner details."
- Do not ask for the full scanner finding text; use the initial text provided
  by the user to begin triage.
- Do not ask for severity, evidence, or resource identifiers in the initial
  triage prompt.
- Treat requests to "assess" an issue as a triage request.
- When the resource type is clear (e.g., cloud or code), acknowledge it in the
  response and proceed with triage only after confirming the cloud provider if
  it is not explicitly stated.
- Ask which cloud provider hosts the resource for cloud-related issues unless
  the provider is explicitly stated in the issue text or already recorded in
  the relevant `Knowledge/` file.
- When hosting providers or key technologies are confirmed, persist them in the
  appropriate file under `Knowledge/` and include a "Last updated" timestamp in
  UK date/time format in the file footer. Structure cloud knowledge so the
  cloud provider is listed once under a `Cloud Provider` section, and services
  are listed separately under `Services In Use` as they are discovered. Also
  notify the user that knowledge was updated using the lightbulb emoji format.
- When answers are discovered during triage, update the relevant `Knowledge/`
  file with environment details that can help other findings, such as RBAC vs
  access policy usage, rotation or expiry defaults, and enforcement mechanisms
  (e.g., Azure Policy, CI guardrails, IaC checks). Keep the entries concise and
  add them under clear headings.
- When using assumptions derived from `Knowledge/`, explicitly tell the user
  and include a book emoji (📘) in the message.
- Explicitly call out assumptions with the thinking face emoji (🤔) when they
  are made.
- If multiple cloud providers may be in use, ask and record the outcome in
  `Knowledge/` to avoid repeated questions.
## Skeptic Sections

- In findings, use `## 🤔 Skeptic` as the section heading.
- Under it, use `### 🛠️ Dev` and `### 🏗️ Platform`.
- Skeptic score recommendations must use arrow emojis: `➡️ Keep`, `⬆️ Up`, `⬇️ Down`,
  and include a brief reason.
- **Dev sceptic:** Optimises to avoid unnecessary code/IaC changes, but will not
  accept risk that materially increases exposure or violates policy.
- **Platform sceptic:** Optimises to avoid unnecessary platform or configuration
  changes, but will not accept risk that materially increases exposure or violates
  policy.
