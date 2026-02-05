# Styling Guidelines

This file documents the repository's styling conventions for code and documentation.

- Filenames: Follow the Titlecase convention (first letter uppercase, rest lowercase) as specified in Instructions.md.
- Markdown: Use 80-100 character soft wrap for paragraphs; use ATX-style headings (e.g., # Heading).
- Code blocks: Specify the language for fenced code blocks (```py, ```js, ```go).
- Commits: Use short imperative subject line and body; reference issue numbers when applicable.

Keep this file concise; expand with language-specific linters or formatter configs when those tools are added to the repo.

Additional MD rules:
- Footer timestamp: Every .md file must include a "Last updated" timestamp in the footer using UK date and time (DD/MM/YYYY HH:MM). Seconds are optional and not required.
- Headers: Use headings to structure all markdown files; avoid long unheaded blocks.
- Bullet point colon formatting: In bullet lists, when a line contains a colon (`:`), format the text left of the colon in bold. Example:
  - **Key:** value

- Severity emoji bullets: Use colored emoji bullets for severity levels in documentation lists:
  - 🔴 Critical
  - 🟠 High
  - 🟡 Medium
  - 🟢 Low

- Header bullet: Headings should be prefixed with a purple bullet point emoji (🟣) on the first line after the heading for visual identification.

