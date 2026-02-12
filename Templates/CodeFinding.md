# 🟣 Code Finding Template
This document defines the layout for code security findings. For formatting
rules, follow `Settings/Styling.md`. For behavioural rules, follow
`Agents/Instructions.md`.

## Workflow Overview
1. **SecurityAgent** runs first, analyses the target, and outputs findings to
   a new file: `Findings/Code/A01_Broken_Access_Control.md`.
2. **SecurityAgent** updates `Knowledge/` with any new inferred/confirmed facts
   discovered while writing the finding (inferred facts must be marked as
   **assumptions** and user-verified).
3. **Dev** and **Platform** review the findings, each appending their own
   sections under `## 🤔 Skeptic`.
4. **SecurityAgent** reconciles feedback, updates the final score, and appends
   the collaboration summary and metadata.

## Filename Conventions
- **Location:** All findings are stored in `Findings/Code/`.
- **Format:** `Findings/Code/A01_Broken_Access_Control.md` (use a short,
  Titlecase identifier).
- **Finding title:** Use a short, Titlecase identifier from the finding source
  (e.g., `A01_Broken_Access_Control`).

## File Template
```md
# 🟣 <finding-title>

- **Description:** <short description>
- **Overall Score:** <severity emoji + label> <score>/10

## 🛡️ Security Review
### Summary
<brief business impact summary: what it means to the business if this isn’t fixed>

### Applicability
- **Status:** Yes / No / Don’t know
- **Evidence:** <what makes this true/false>

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
  (use Markdown backlinks, e.g., `Findings/Code/Foo.md`)

## Meta Data
- 🗓️ **Last updated:** DD/MM/YYYY HH:MM
```

## Required Sections
- 🛡️ Security Review
- 🤔 Skeptic
- 🤝 Collaboration
- Compounding Findings
- Meta Data

## Testing
- Use the `sample/` directory for test runs and mock findings.
