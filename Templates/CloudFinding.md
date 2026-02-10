# 🟣 Cloud Finding Template
This document defines the layout for cloud security findings. For formatting
rules, follow `Settings/Styling.md`. For behavioural rules, follow
`Agents/Instructions.md`.

## Workflow Overview
1. **SecurityAgent** runs first, analyses the target, and outputs findings to
   a new file: `Findings/Cloud/Unprotected_Storage_Account.md`.
2. **Dev** and **Platform** review the findings, each appending their own
   sections under `## 🤔 Skeptic`.
3. **SecurityAgent** reconciles feedback, updates the final score, and appends
   the collaboration summary and metadata.

## Filename Conventions
- **Location:** All findings are stored in `Findings/Cloud/`.
- **Format:** `Findings/Cloud/Unprotected_Storage_Account.md` (use a
  short, Titlecase identifier).
- **Finding title:** Use a short, Titlecase identifier from the finding source
  (e.g., `Unprotected_Storage_Account`).

## File Template
```md
# 🟣 <finding-title>

- **Description:** <short description>
- **Overall Score:** <severity emoji + label> <score>/10

## 🛡️ Security Review
### Summary
<summary>

### 🎯 Exploitability
<exploitability>

### Recommendations
- [ ] <recommendation> — ⬇️ <score>➡️<reduced-score> (est.)

### Considered Countermeasures
- 🔴 <countermeasure> — <effectiveness note>
- 🟡 <countermeasure> — <effectiveness note>
- 🟢 <countermeasure> — <effectiveness note>

### Rationale
<rationale>

## 🤔 Skeptic
### 🛠️ Dev
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down (explain why).
- **Mitigation note:** <note>

### 🏗️ Platform
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down (explain why).
- **Mitigation note:** <note>

## 🤝 Collaboration
- **Outcome:** <outcome>
- **Next step:** <next step>

## Compounding Findings
- **Compounds with:** <finding list or None identified>
  (use Markdown backlinks, e.g., `Findings/Cloud/Foo.md`)

## Meta Data
- 🗓️ **Last updated:** DD/MM/YYYY HH:MM
```

## Required Sections
- 🛡️ Security Review
- 🤔 Skeptic
- 🤝 Collaboration
- Compounding Findings
- Meta Data

## Cross-Checks
- Always check existing findings to see if they compound the new issue.
- If they compound, state that clearly, review both issues, and add backlinks
  between them.

## Testing
- Use the `sample/` directory for test runs and mock findings.
