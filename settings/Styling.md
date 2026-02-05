# Styling Guidelines
This file documents the repository's styling conventions for code and documentation.

- **Filenames:** Follow the Titlecase convention (first letter uppercase, rest lowercase)
  as specified in Instructions.md.
- **Markdown:** Use 80-100 character soft wrap for paragraphs; use ATX-style headings
  (e.g., # Heading).
- **Code blocks:** Specify the language for fenced code blocks (```py, ```js, ```go).
- **Commits:** Use short imperative subject line and body; reference issue numbers when
  applicable.

Keep this file concise; expand with language-specific linters or formatter configs when
those tools are added to the repo.

## Additional MD Rules
- **Footer timestamp:** Only markdown files under findings/ require a "Last updated"
  timestamp in the footer using UK date and time (DD/MM/YYYY HH:MM with a colon).
  Seconds are optional and not required.
- **Headers:** Use headings to structure all markdown files; avoid long unheaded blocks.
- **Bullet point colon formatting:** In bullet lists, when a line contains a colon (`:`),
  format the text left of the colon in bold. Example:
  - **Key:** value

- **Severity emoji bullets:** Use coloured emoji bullets for severity levels in
  documentation lists:
  - 🔴 Critical 8-10
  - 🟠 High 6-7
  - 🟡 Medium 4-5
  - 🟢 Low 1-3

- **Overall score:** Use a 1-10 scale where 10 is worst, and include a coloured
  circle plus severity label. Use this mapping:
  - 🔴 Critical 8-10
  - 🟠 High 6-7
  - 🟡 Medium 4-5
  - 🟢 Low 1-3
  - Example: `- **Overall Score:** 🔴 Critical 9/10`
- **Header bullet:** Only the document title should include a purple bullet
  point emoji (🟣) after the `#` and before the title text.
- **Review emoji:** Use consistent emoji prefixes for reviewer section headings.
  For cloud findings, use 🛠️ for `Dev Review` and 🏗️ for `Platform Review`.

Last updated: 05/02/2026 21:04
