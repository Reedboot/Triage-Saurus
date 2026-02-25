# ENV-XXX: [Environment-Level Security Gap Title]

## 🎯 Summary
[One sentence: What environment-level security infrastructure is missing?]

**Environment Scope:** [Azure Tenant / AWS Account / GCP Project]  
**Affected Resources:** [All resources in environment]  
**Security Pillar:** [Log Consumption / Network Segmentation / Identity Management]

---

## 📋 Description

[Explain what environment-level security infrastructure is missing and why it matters]

**What's missing:**
- [Infrastructure component 1]
- [Infrastructure component 2]
- [Infrastructure component 3]

**Industry standard:**
- [What mature organizations have in place]
- [Compliance requirements (SOC2, ISO 27001)]

---

## 🔍 Evidence

### Terraform/IaC Analysis
[List what was NOT found in infrastructure code]

```
Searched for:
- resource "azurerm_log_analytics_workspace" → NOT FOUND
- resource "azurerm_sentinel_log_analytics_workspace_onboarding" → NOT FOUND
- resource "azurerm_monitor_action_group" → NOT FOUND
```

### Knowledge Base Review
[Reference Knowledge/<Provider>.md - confirm infrastructure absent]

```
Azure environment contains:
- 5 storage accounts (all logging to storage only, not consumed)
- 3 SQL servers (auditing enabled, but no Log Analytics)
- 2 AKS clusters (diagnostic settings enabled, but no SIEM)
- 0 Log Analytics workspaces
- 0 Azure Sentinel instances
- 0 Monitor action groups
```

---

## 🎯 Exploitability

### Monitoring Maturity Assessment

**Current Level:** [0-4]
- **Level 0 (Blind):** No centralized logging, no SIEM, no alerts
- **Level 1 (Reactive):** Logs exist but not consumed
- **Level 2 (Alerted):** SIEM with basic alerts
- **Level 3 (Responsive):** Tuned alerts + incident response
- **Level 4 (Automated):** Automated response + threat hunting

**Risk:** [What can happen undetected?]
- Breaches remain undetected for months/years
- Attacker has time to establish persistence
- No visibility into lateral movement
- Compliance failures (no evidence of monitoring)

### Real-World Impact

**Attack scenarios that go undetected:**
1. [Scenario 1 - e.g., SQL injection exfiltrating data]
2. [Scenario 2 - e.g., Service Principal credential theft]
3. [Scenario 3 - e.g., Privilege escalation via RBAC abuse]

**Detection timeline:**
- Current: Breach discovered by external party (avg 277 days)
- With proper monitoring: Real-time alerts (< 1 hour)

---

## 🎓 Security Implications

### Impact on Other Findings

This environment-level gap **amplifies the risk** of all other findings:

| Finding | Current Score | With Monitoring | Score Increase |
|---------|---------------|----------------|----------------|
| SQL Injection | 8/10 HIGH | 6/10 MEDIUM | +2 (undetected exploitation) |
| Storage Public Access | 7/10 HIGH | 5/10 MEDIUM | +2 (undetected data theft) |
| Missing MFA | 6/10 MEDIUM | 4/10 LOW | +2 (undetected account compromise) |

**Rationale:** Without monitoring, attackers have unlimited dwell time to exploit vulnerabilities.

### Compliance Impact

**Regulations requiring security monitoring:**
- **SOC2 (CC7.2):** System operations monitored for anomalies
- **ISO 27001 (A.12.4.1):** Event logging for security events
- **PCI-DSS (Req 10):** Track and monitor all access to network resources
- **GDPR (Art 32):** Ability to ensure ongoing security of processing

**Current status:** Non-compliant (no evidence of monitoring capability)

---

## Data Classification

| Aspect | Value | Source |
|--------|-------|--------|
| Affected Data Types | [ALL environment data - TIER 1-5] | Environment-wide gap |
| Compliance Scope | [PCI-DSS / GDPR / HIPAA / SOC2] | Regulatory requirements |
| Breach Detection Time | Months to years (no monitoring) | Industry average (Verizon DBIR) |
| Breach Impact | CRITICAL - No visibility into unauthorized access | Risk assessment |

**Data at risk:**
- [List data types in environment based on service inventory]
- [Reference data classification from other findings]

---

## 💡 Recommendation

### Immediate Actions (Priority 1)

1. **Deploy Log Analytics Workspace** [Azure] / **CloudWatch Logs** [AWS] / **Cloud Logging** [GCP]
   ```
   Purpose: Centralized log collection
   Timeline: 1 week
   Cost: ~$X/month for expected log volume
   ```

2. **Enable SIEM: Azure Sentinel / GuardDuty / Security Command Center**
   ```
   Purpose: Threat detection and alerting
   Timeline: 2 weeks (includes rule tuning)
   Cost: ~$X/month based on data ingestion
   ```

3. **Configure Critical Alerts**
   ```
   Priority alerts:
   - Failed authentication attempts (>10 in 5 minutes)
   - New service principal created
   - RBAC/IAM role assignments modified
   - Public blob/S3 bucket created
   - Firewall rule changes
   ```

### Medium-Term Improvements (Priority 2)

4. **Connect All Data Sources**
   - [ ] All Storage accounts → diagnostic settings
   - [ ] All SQL servers → extended auditing
   - [ ] All AKS clusters → diagnostic settings
   - [ ] All NSGs → flow logs
   - [ ] Activity Log → Log Analytics

5. **Build Security Dashboards**
   - [ ] Authentication failures dashboard
   - [ ] Public exposure dashboard
   - [ ] RBAC/IAM changes dashboard
   - [ ] Compliance dashboard

6. **Incident Response Playbooks**
   - [ ] Automated responses (disable compromised accounts)
   - [ ] Notification workflows (email, Teams, PagerDuty)
   - [ ] Runbooks for common scenarios

### Long-Term Strategy (Priority 3)

7. **Continuous Improvement**
   - Alert tuning (reduce false positives)
   - Threat hunting capabilities
   - Integration with external threat intelligence
   - Automated remediation

---

## 📚 References

- [Azure Sentinel deployment guide](https://docs.microsoft.com/azure/sentinel/)
- [AWS GuardDuty best practices](https://docs.aws.amazon.com/guardduty/)
- [GCP Security Command Center setup](https://cloud.google.com/security-command-center)
- [NIST Cybersecurity Framework - Detect function](https://www.nist.gov/cyberframework)
- [MITRE ATT&CK - Detection strategies](https://attack.mitre.org/)
- [Verizon Data Breach Investigations Report - Detection timelines](https://www.verizon.com/business/resources/reports/dbir/)

---

**Created:** [YYYY-MM-DD]  
**Severity:** 🔴 HIGH (Environment-wide gap amplifying all other risks)  
**Monitoring Maturity:** Level [0-4]  
**Expected Remediation Timeline:** 4-8 weeks (phased deployment)
