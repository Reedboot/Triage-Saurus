# 🟣 CloudFinding Workflow
This document describes the workflow for cloud security findings review using
SecurityAgent (aligned to ISO/IEC 27001:2022 intent), Dev, and Platform.

## Workflow Overview
1. **SecurityAgent** runs first, analyses the target, and outputs findings to a
   new file: `Findings/<unique-id>.md`.
2. **Dev** and **Platform** review the findings, each appending
   their own section to the same file under clearly labelled reviewer sections.
3. **SecurityAgent** reviews the reviewers' comments, updates the final score,
   and appends a summary.
4. The overall score and status are updated in the file.

## Filename Conventions
- **Location:** All findings are stored in `Findings/`.
- **Format:** `CF-YYYYMMDD-HHMM-xxxx.md`
- **Date and time:** `YYYYMMDD-HHMM` = UTC date and time of finding creation.
- **Identifier:** `xxxx` = 4-digit random or sequential identifier.

## File Template
```md
# 🟣 Cloud Security Finding: <unique-id>

**Overall Score:** <score>

## SecurityAgent Findings
<details>
<summary>Summary</summary>
<content>
</details>

## Skeptic
### 🛠️ Dev
<details>
<summary>Summary</summary>
<content>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down (explain why).
</details>

### 🏗️ Platform
<details>
<summary>Summary</summary>
<content>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down (explain why).
</details>

## SecurityAgent Final Review
<details>
<summary>Timestamp: <timestamp></summary>
<content>
</details>

---

- All timestamps in UTC ISO8601.
- Each reviewer appends their section with their comments and timestamp.
- SecurityAgent updates the final score after all reviews.
```

## Required Sections
- SecurityAgent Findings
- 🛠️ Dev Review
- 🏗️ Platform Review
- Skeptics
- SecurityAgent Final Review
- Overall Score and Status

## Testing
- Use the `sample/` directory for test runs and mock findings.

## ask_user Usage
- Use `ask_user` to confirm reviewer assignment if ambiguous.
- Use `ask_user` to request clarification on findings or review comments.

## Lightbulb Rule
- If a reviewer or agent has a significant insight ("lightbulb moment"),
  highlight it with a :bulb: emoji in their section.
