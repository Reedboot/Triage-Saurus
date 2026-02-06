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
- **Location:** All findings are stored in `Findings/Cloud/`.
- **Format:** `<finding-title>.md`
- **Finding title:** Use a short, Titlecase identifier from the finding source
  (e.g., `AZ-001_Unprotected_Storage_Account`).

## File Template
```md
# 🟣 Cloud Security Finding: <unique-id>

**Overall Score:** <score>

## SecurityAgent Findings
<details>
<summary>Summary</summary>
<content>
### Recommendations
- <recommendation>

### Considered Countermeasures
- 🔴 <countermeasure> — <effectiveness note>
- 🟡 <countermeasure> — <effectiveness note>
- 🟢 <countermeasure> — <effectiveness note>
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
- For countermeasures, indicate effectiveness with traffic light circles:
  🔴 Ineffective, 🟡 Partially effective, 🟢 Effective.
```

## Required Sections
- SecurityAgent Findings
- Skeptic
- SecurityAgent Final Review
- Overall Score
- Recommendations
- Considered Countermeasures

## Cross-Checks
- Always check existing findings to see if they compound the new issue.
- If they compound, state that clearly, review both issues, and add backlinks
  between them.

## Testing
- Use the `sample/` directory for test runs and mock findings.

## ask_user Usage
- Use `ask_user` to confirm reviewer assignment if ambiguous.
- Use `ask_user` to request clarification on findings or review comments.

## Lightbulb Rule
- If a reviewer or agent has a significant insight ("lightbulb moment"),
  highlight it with a :bulb: emoji in their section.
