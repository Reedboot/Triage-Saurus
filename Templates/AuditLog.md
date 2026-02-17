# 🟣 Audit Log - Session YYYY-MM-DD HHMMSS

**AUDIT LOG ONLY — do not load into LLM triage context**

*This log tracks all questions, answers, assumptions, and actions taken during a triage session. It is for human review, compliance tracking, and session replay - not for feeding into LLM context windows.*

## Session Metadata
- **Date:** DD/MM/YYYY
- **Start time:** HH:MM
- **End time:** HH:MM (update at session end)
- **Triage type:** Cloud / Code / Repo scan / Mixed
- **Provider:** Azure / AWS / GCP / N/A
- **Intake source:** <path or "Interactive paste">
- **Scan scope:** SAST / SCA / Secrets / IaC / IaC+SCA / All / N/A

---

## Scan Timing & Tools

### Scan Type: <IaC / SCA / SAST / Secrets>
- **Duration:** MM:SS or HH:MM:SS
- **Tools used:** <comma-separated list of tools/commands>
- **Findings count:** N
- **Status:** Completed / Failed / Skipped

### Scan Type: <next type>
- **Duration:** MM:SS or HH:MM:SS
- **Tools used:** <comma-separated list>
- **Findings count:** N
- **Status:** Completed / Failed / Skipped

---

## Q&A Log

### HH:MM - Question 1
❓ <question text>

**Answer:** <user response>

**Action taken:** <what was done with this answer>
- Example: "Updated Azure.md Confirmed section: Added Key Vault, Storage Accounts to services in use"
- Example: "Set finding ABC_001 applicability to Yes based on confirmed internet exposure"

### HH:MM - Question 2
❓ <question text>

**Answer:** <user response>

**Action taken:** <what was done with this answer>

---

## Actions Log

### HH:MM - Finding Created
- **Action:** Created
- **Target:** `Output/Findings/Cloud/Public_Storage_Account.md`
- **Reason:** Processing bulk intake from Intake/Cloud/cloud.txt
- **Impact:** New finding, initial score 8/10

### HH:MM - Knowledge Updated
- **Action:** Updated
- **Target:** `Output/Knowledge/Azure.md`
- **Reason:** User confirmed Private Endpoints are in use
- **Impact:** Moved "Private Endpoints" from Assumptions to Confirmed

### HH:MM - Finding Updated
- **Action:** Updated
- **Target:** `Output/Findings/Cloud/Public_Storage_Account.md`
- **Reason:** Dev Skeptic review completed
- **Impact:** Score adjusted 8/10 → 6/10 (compensating control: VNet integration)

### HH:MM - Summary Regenerated
- **Action:** Updated
- **Target:** `Output/Summary/Risk Register.xlsx`
- **Reason:** Bulk triage completed, 15 new findings
- **Impact:** Risk register now contains 19 total findings

---

## Bulk Operations

### HH:MM - Bulk Import Started
- **Source:** `Intake/Cloud/cloud.txt`
- **Items count:** 15
- **Processing order:** Internet exposure → Data stores → Identity → Detection → Hardening

### HH:MM - Bulk Import Completed
- **Duration:** 12 minutes
- **Items processed:** 15/15
- **Findings created:** 15
- **Knowledge updates:** Azure.md (added 8 services)

### HH:MM - Items Processed (if <20 items)
1. Public Storage Account → Finding created
2. SQL Firewall Allows Azure → Finding created
3. Key Vault Public Access → Finding created
... (continue for all items if count < 20)

---

## Assumptions Made

### HH:MM - Assumption
**Service/Topic:** Azure Private Endpoints

**Assumption:** Private Endpoints are NOT in use (inferred from multiple public access findings)

**Status:** Pending confirmation

**Impact if wrong:** Would downgrade 8 findings from HIGH to MEDIUM

---

## Score Changes

### HH:MM - Finding: Public_Storage_Account.md
- **Initial (Security Review):** 8/10 (High)
- **After Dev Skeptic:** 6/10 (Medium) - VNet integration confirmed
- **After Platform Skeptic:** 6/10 (Medium) - No change
- **Final:** 6/10 (Medium)
- **Rationale:** VNet integration limits blast radius despite public DNS

---

## Token Usage by Operation

| Operation | Duration | Tokens | Efficiency (tok/sec) | Model |
|-----------|----------|--------|----------------------|-------|
| Session kickoff | MM:SS | N | N/A | Sonnet 4.5 |
| Git history analysis | MM:SS | N | X.X | Sonnet 4.5 |
| IaC scan | MM:SS | N | X.X | Sonnet 4.5 |
| SCA scan | MM:SS | N | X.X | Sonnet 4.5 |
| SAST scan | MM:SS | N | X.X | Sonnet 4.5 |
| Finding generation | MM:SS | N | X.X | Sonnet 4.5 |
| Dev Skeptic review | MM:SS | N | X.X | Sonnet 4.5 |
| Platform Skeptic review | MM:SS | N | X.X | Sonnet 4.5 |
| Risk register generation | MM:SS | N | X.X | Sonnet 4.5 |
| **Total** | **HH:MM** | **N** | **Avg: X.X** | - |

### Token Budget
- **Allocated:** 1,000,000 tokens
- **Used:** N tokens (X.X%)
- **Remaining:** N tokens

### Cost Efficiency Notes
- *Add observations about which operations were most/least token-efficient*
- *Note any unexpectedly high token consumption for investigation*

---

## Summary
- **Session duration:** HH:MM
- **Total findings created:** N
- **Total findings updated:** N
- **Knowledge files updated:** <comma-separated list>
- **Summaries regenerated:** <comma-separated list>
- **Questions asked:** N
- **Questions answered:** N
- **Assumptions made:** N (see Assumptions Made section)
- **Assumptions confirmed:** N
- **Assumptions rejected:** N

---

## Notes
*Add any special observations, edge cases encountered, or context that would help someone reviewing this audit log later.*
