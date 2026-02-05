# CloudFinding Workflow

This document describes the workflow for cloud security findings review using SecurityAgent, DevSkeptic, and PlatformSkeptic.

## Workflow Overview
1. **SecurityAgent** runs first, analyzes the target, and outputs findings to a new file: `findings/cloud/<unique-id>.md`.
2. **DevSkeptic** and **PlatformSkeptic** review the findings, each appending their own section to the same file under clearly labeled reviewer sections.
3. **SecurityAgent** reviews the reviewers' comments, updates the final score, and appends a summary.
4. The overall score and status are updated in the file.

## Filename Conventions
- All findings are stored in `findings/cloud/`.
- Filename format: `CF-YYYYMMDD-HHMM-xxxx.md`
  - `YYYYMMDD-HHMM` = UTC date and time of finding creation
  - `xxxx` = 4-digit random or sequential identifier

## File Template
```
# Cloud Security Finding: <unique-id>

**Created:** <timestamp>
**Status:** Open/Closed
**Overall Score:** <score>

## SecurityAgent Findings
<details>
<summary>Timestamp: <timestamp></summary>
<content>
</details>

## DevSkeptic Review
<details>
<summary>Timestamp: <timestamp></summary>
<content>
</details>

## PlatformSkeptic Review
<details>
<summary>Timestamp: <timestamp></summary>
<content>
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

## Required Sections
- SecurityAgent Findings
- DevSkeptic Review
- PlatformSkeptic Review
- SecurityAgent Final Review
- Overall Score & Status

## Testing
- Use the `sample/` directory for test runs and mock findings.

## ask_user Usage
- Use `ask_user` to:
  - Confirm reviewer assignment if ambiguous
  - Request clarification on findings or review comments

## Lightbulb Rule
- If a reviewer or agent has a significant insight ("lightbulb moment"), highlight it with a :bulb: emoji in their section.
