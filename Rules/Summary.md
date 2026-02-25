# Security Rules Summary

**Total Rules:** 50+  
**Format:** Opengrep/Semgrep compatible  
**License:** Personal Use (see /LICENSE)

---

## Rules by Category

### IaC - Terraform/Azure (33 rules)
**Secrets & Credentials:**
- terraform-nonsensitive-secrets.yml
- terraform-hardcoded-keyvault-secret.yml
- terraform-sensitive-variable-not-marked.yml
- azure-vm-extension-secrets-in-settings.yml
- azure-public-blob-sensitive-content.yml

**Network Security (Pillar 1):**
- azure-sql-firewall-all-access.yml
- azure-nsg-rule-port-mismatch.yml
- azure-keyvault-network-acl-allow-all.yml
- azure-storage-network-allow-all.yml

**Access Control (Pillar 2):**
- azure-service-principal-contributor-scope.yml
- azure-storage-shared-key-enabled.yml
- azure-vm-password-auth-enabled.yml
- azure-sql-aad-auth-missing.yml
- azure-aks-aad-integration-missing.yml
- azure-container-registry-admin-enabled.yml
- azure-keyvault-administrator-role.yml

**Audit Logging (Pillar 3):**
- azure-sql-auditing-disabled.yml
- azure-sql-threat-detection-disabled.yml
- azure-keyvault-logging-disabled.yml
- azure-storage-logging-disabled.yml
- azure-nsg-flow-logs-disabled.yml

**Data Protection (Pillar 5):**
- azure-storage-encryption-disabled.yml
- azure-sql-tls-version-old.yml

**Infrastructure:**
- terraform-local-backend-unencrypted.yml
- terraform-provider-version-unpinned.yml
- terraform-resource-group-deletion-guard-disabled.yml
- azure-user-force-password-change-disabled.yml
- azure-aks-local-accounts-enabled.yml
- azure-vm-eol-ubuntu.yml
- azure-kubernetes-privileged-sp-in-secret.yml
- terraform-sql-credential-string-interpolation.yml

### IaC - Kubernetes (17 rules)
**Container Security:**
- kubernetes-privileged-container.yml
- kubernetes-host-network.yml
- kubernetes-host-pid.yml
- kubernetes-host-path-volume.yml
- kubernetes-allow-privilege-escalation.yml
- kubernetes-run-as-root.yml
- kubernetes-dangerous-capabilities.yml
- kubernetes-image-latest-tag.yml

**Network:**
- kubernetes-loadbalancer-public-exposure.yml
- kubernetes-nodeport-exposure.yml
- kubernetes-ingress-no-auth.yml
- kubernetes-ingress-no-tls.yml
- kubernetes-ingress-no-rate-limit.yml

**RBAC:**
- kubernetes-wildcard-rbac.yml
- kubernetes-cluster-admin-binding.yml
- kubernetes-rbac-pod-discovery.yml

**Malware Detection:**
- kubernetes-suspicious-command-pattern.yml

### Secrets Detection (2 rules)
- aws-access-key-id.yml
- sql-connection-string.yml

---

## Coverage

**Sources:**
- ✅ All 22 Opus findings from ExpanseAzureLab
- ✅ Kubernetes checks from discover_repo_context.py
- ✅ Five Pillars Framework (SecurityAgent.md)
  - Pillar 1: Network Security
  - Pillar 2: Access Control
  - Pillar 3: Audit Logging
  - Pillar 5: Data Protection

**Cloud Providers:**
- Azure (primary)
- AWS (secrets only)
- Kubernetes (platform)

**Severities:**
- ERROR: 15 rules (CRITICAL issues)
- WARNING: 25 rules (HIGH issues)
- INFO: 10 rules (MEDIUM issues)

---

## Usage

### Manual Detection
Each rule includes detection steps for manual grep/checking.

### Automated (Future)
```bash
# When opengrep installed:
opengrep scan --config Rules/ /path/to/repo
```

### Script Integration
Scripts can read Rules/ folder and execute checks programmatically.

---

## Maintenance

**Adding Rules:**
1. Create .yml file in appropriate subfolder
2. Follow opengrep/Semgrep format
3. Include metadata (CWE, technology, five_pillars)
4. Add test case if possible

**Updating Rules:**
- Edit .yml files directly
- Version control tracks changes
- No code changes in scripts

---

**Last Updated:** 2026-02-25  
**Total Rules:** 50+  
**Architecture:** Rules = declarative, Scripts = execution engines
