# Rule Creation Guidelines

## When to Create a Rule

✅ **Always create a rule when:**
1. **New vulnerability discovered** - Found security issue not in existing rules
2. **Detection gap identified** - Scanner missed something it should catch
3. **Learning from experiments** - Experiment validation reveals missing checks
4. **External findings** - Security tools (Opus, tfsec, etc.) find issues we don't
5. **Script checks extraction** - Converting hardcoded checks to declarative rules

## Rule File Structure

### Naming Convention
- **Format:** `technology-specific-issue-name.yml`
- **Examples:**
  - `azure-sql-auditing-disabled.yml`
  - `kubernetes-privileged-container.yml`
  - `terraform-nonsensitive-secrets.yml`

### File Location
```
Rules/
├── iac/           # Infrastructure as Code rules
│   ├── azure-*.yml
│   ├── terraform-*.yml
│   ├── kubernetes-*.yml
│   └── aws-*.yml
└── secrets/       # Secret detection rules
    ├── aws-access-key-id.yml
    └── sql-connection-string.yml
```

### Required YAML Structure
```yaml
rules:
  - id: unique-rule-identifier
    message: |
      Clear description of the security issue.
      Include remediation guidance.
    severity: ERROR | WARNING | INFO
    languages: [terraform, yaml, hcl, python, etc.]
    
    # Detection pattern (choose one approach)
    pattern: |
      code pattern to match
    # OR
    pattern-regex: 'regex pattern'
    # OR
    patterns:
      - pattern: first pattern
      - pattern-not: exclude this pattern
      - metavariable-regex:
          metavariable: $VAR
          regex: 'pattern'
    
    metadata:
      category: security
      subcategory: [secrets, network, authentication, etc.]
      cwe: CWE-XXX
      confidence: HIGH | MEDIUM | LOW
      likelihood: HIGH | MEDIUM | LOW
      impact: HIGH | MEDIUM | LOW
      technology: [terraform, azure, kubernetes, etc.]
      five_pillars: Pillar X (if applicable)
      compliance: [pci-dss, gdpr, soc2] (if applicable)
```

## Severity Guidelines

### ERROR (Critical/High)
- Direct credential exposure
- Remote code execution
- Authentication bypass
- Critical misconfigurations enabling full compromise
- **Example:** Public storage with service principal secrets

### WARNING (Medium/High)
- Privilege escalation paths
- Missing security controls
- Excessive permissions
- Encryption/logging disabled
- **Example:** SQL auditing disabled

### INFO (Low/Medium)
- Best practice violations
- Informational findings
- Supply chain concerns (version pinning)
- **Example:** Container image uses :latest tag

## Detection Pattern Tips

### For Terraform/HCL
```yaml
# Check for resource without required property
patterns:
  - pattern: |
      resource "azurerm_sql_server" "$NAME" {
        ...
      }
  - pattern-not: |
      resource "azurerm_sql_server" "$NAME" {
        ...
        minimum_tls_version = "1.2"
        ...
      }
```

### For Kubernetes YAML
```yaml
# Check for dangerous securityContext
pattern: |
  securityContext:
    ...
    privileged: true
    ...
```

### For Secret Detection
```yaml
# Regex for AWS keys
pattern-regex: '(AKIA|ASIA)[0-9A-Z]{16}'
```

### For Metavariable Matching
```yaml
patterns:
  - pattern: |
      output "$NAME" {
        ...
        value = nonsensitive($SECRET)
      }
  - metavariable-regex:
      metavariable: $SECRET
      regex: .*(password|secret|key|token).*
```

## Testing Rules

### Manual Testing
```bash
# Create test file with vulnerable code
cat > test.tf << 'EOF'
resource "azurerm_key_vault_secret" "test" {
  value = "hardcoded-secret"
}
EOF

# Test rule (when opengrep available)
opengrep scan --config Rules/IaC/terraform-hardcoded-keyvault-secret.yml test.tf
```

### Test Case in Rule
```yaml
# Add to metadata
test_case:
  vulnerable_code: |
    resource "azurerm_key_vault_secret" "bad" {
      value = "Th$s$sS3cr3t!"
    }
  expected: 1 finding
  validation: Should detect literal string in value
```

## Common Patterns to Check

### Terraform
- Hardcoded secrets in resources
- Missing encryption/TLS settings
- Network ACLs allowing all traffic
- Authentication mechanisms (AAD vs legacy)
- Logging/auditing disabled
- Excessive permissions (Contributor/Owner)

### Kubernetes
- Privileged containers
- Host namespace sharing (hostNetwork, hostPID, hostPath)
- Running as root
- Dangerous capabilities
- Missing network policies
- Missing admission controls
- Wildcard RBAC permissions

### Secrets
- Cloud provider credentials (AWS AKIA, Azure connection strings)
- Database connection strings
- API keys
- Private keys

## Learning from Missed Detections

When an experiment or external tool finds something we missed:

1. **Analyze the finding** - What pattern should we have detected?
2. **Create the rule** - Write opengrep pattern matching the issue
3. **Test against vulnerable code** - Validate rule catches the issue
4. **Document in learning** - Record why we missed it originally
5. **Track effectiveness** - Monitor if rule catches future instances

## Example: Creating Rule from Opus Finding

**Scenario:** Opus found nonsensitive() wrapping secrets, we missed it

**Analysis:**
- Pattern: `nonsensitive($VAR)` where `$VAR` contains "password/secret/key"
- Location: outputs.tf
- Severity: CRITICAL (exposes secrets in state/logs)

**Rule Created:**
```yaml
rules:
  - id: terraform-nonsensitive-secrets
    message: |
      The nonsensitive() function removes Terraform's sensitivity marker,
      causing secrets to be printed in outputs, state files, and CI logs.
      Remove nonsensitive() or mark output as sensitive.
    severity: ERROR
    languages: [terraform, hcl]
    patterns:
      - pattern: |
          output "$NAME" {
            ...
            value = nonsensitive($SECRET)
            ...
          }
      - metavariable-regex:
          metavariable: $SECRET
          regex: .*(password|secret|key|token|credential).*
```

**Learning Documented:**
- Added to `Output/Learning/LEARNING_Opus_Detection_Techniques.md`
- Noted we need to systematically check outputs.tf
- Rule prevents future misses

---

## Quick Reference

**Create rule:** `Rules/IaC/technology-issue.yml`  
**Format:** Opengrep/Semgrep YAML  
**Test:** Manual grep or opengrep scan  
**Track:** Reference rule-id in findings  
**Learn:** Document missed detections and new rules
