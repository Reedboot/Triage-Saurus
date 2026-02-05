# Agent Instructions

This file is the canonical place to store instructions for AI agents interacting with this repository.

- Use UK English spelling throughout documentation and messages.
- sample/ folder: Use sample/ to store example outputs or test artifacts for findings and checks; it's safe to commit placeholder files (e.g., .gitkeep) to reserve the directory.
  - When asked to "test the styling", agents should delete previous contents of sample/ and generate new sample output files into sample/ following Styling.md rules.

When updating, prefer short, explicit directives and keep examples minimal.

Operational verbosity:
- Agents should emit a brief, verbose log (in their output or comments) describing each processing step when handling a finding. Include which agent is running, the actions it's taking (e.g., parsing evidence, checking policies, scoring), and any key heuristics used.
- Logs should be concise but informative; avoid raw dumps of large evidence unless necessary.

All agents must follow the repository styling rules defined in settings/Styling.md when producing or editing markdown files.

Knowledge updates:
- Create and maintain domain files under Knowlegde/ (e.g., Azure.md, Code.md, DevOps.md). When an agent learns something new, append it to the appropriate file and include a "Last updated" timestamp in UK date/time format (DD/MM/YYYY HH:MM) in the file footer.

File naming convention:
- Use Titlecase for filenames where the first letter is uppercase and the rest of the word is lowercase (e.g., Instructions.md, Readme.md).

Chat behavior:
- When an agent learns something new, prepend messages with a lightbulb emoji (💡) and include a one- or two-sentence explanation of the learning in chat only.
