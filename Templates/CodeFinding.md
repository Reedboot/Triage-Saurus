# 🟣 Code Finding Template
This document defines the layout for code security findings. For formatting
rules, follow `settings/Styling.md`. For behavioural rules, follow
`agents/Instructions.md`.

## Workflow Overview
1. **SecurityAgent** runs first, analyses the target, and outputs findings to
   a new file: `Findings/Code/A01_Broken_Access_Control.md`.
2. **Dev** and **Platform** review the findings, each appending their own
   sections under `## 🤔 Skeptic`.
3. **SecurityAgent** reconciles feedback, updates the final score, and appends
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
<summary>

### Exploitability
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

## Processing Log
- **Security review:** <summary>
- **Dev sceptic:** <summary>
- **Platform sceptic:** <summary>
- **Collaboration:** <summary>

## Compounding Findings
- **Compounds with:** <finding list or None identified>
  (use Markdown backlinks, e.g., `[Findings/Code/Foo.md](Findings/Code/Foo.md)`)

## Meta Data
- 🗓️ **Last updated:** DD/MM/YYYY HH:MM
```

## Required Sections
- 🛡️ Security Review
- 🤔 Skeptic
- 🤝 Collaboration
- Processing Log
- Compounding Findings
- Meta Data

## Testing
- Use the `sample/` directory for test runs and mock findings.
