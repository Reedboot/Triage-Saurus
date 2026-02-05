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

## Styling
- All agents must follow the repository styling rules defined in
  settings/Styling.md when producing or editing markdown files.

## Knowledge Updates
- Create and maintain domain files under Knowlegde/ (e.g., Azure.md, Code.md,
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

- When an agent learns something new, prepend messages with a lightbulb emoji
  (💡) and include a one- or two-sentence explanation of the learning in chat
  only.
## Skeptic Sections

- In findings, use `### 🛠️ Dev` and `### 🏗️ Platform` under the `## Skeptic` heading.
- Skeptic score recommendations must use arrow emojis: `➡️ Keep`, `⬆️ Up`, `⬇️ Down`,
  and include a brief reason.
- **Dev sceptic:** Optimises to avoid unnecessary code/IaC changes, but will not
  accept risk that materially increases exposure or violates policy.
- **Platform sceptic:** Optimises to avoid unnecessary platform or configuration
  changes, but will not accept risk that materially increases exposure or violates
  policy.
