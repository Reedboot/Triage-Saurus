# Rule Creation Guidelines

## When to Create a Rule

✅ **Always create a rule when:**
1. **New vulnerability discovered** - Found security issue not in existing rules
2. **Detection gap identified** - Scanner missed something it should catch
3. **Learning from experiments** - Experiment validation reveals missing checks
4. **External findings** - Security tools (Opus, tfsec, etc.) find issues we don't
5. **Script checks extraction** - Converting hardcoded checks to declarative rules

## Rule Genericity Gate

**Every rule must pass this gate before being committed.** A rule that fails any check must be revised or discarded — do not push project-specific rules into the shared ruleset.

### Core Principle

Rules must target **configuration patterns within a technology**, not **named resources within a project**.

| ✅ Acceptable | ❌ Not Acceptable |
|--------------|-----------------|
| Scoped to a technology (Terraform, K8s YAML, Python) | Scoped to a specific named resource in the scanned project |
| Targets a misconfiguration class that any repo could have | Targets a resource only because it exists in this repo with this name |
| Would be useful to a team who has never heard of this project | Would only ever fire in the project currently being scanned |

**Key test:** Strip out the resource name and swap in a wildcard (`$_`). Does the rule still detect a real security issue? If yes, the rule is valid. If the rule becomes meaningless without the specific name, it was project-specific and should not be committed.

### The Three Failure Modes

| Failure | Description | Example |
|---------|-------------|---------|
| **Too specific** | Pattern is tied to a named resource from the scanned project | Detects storage account named `"bob"` with public access enabled |
| **Too broad** | Pattern matches so much normal code that findings are meaningless noise | Detects the word `linux`, any `http://` URL, any storage account existing at all |
| **Just right** | Detects a misconfiguration class for any resource of that type in any repo | Detects **any** storage account (regardless of name) with public access enabled |

### Self-Test Checklist

Before writing the rule, answer these questions:

1. **Named-resource check** — Does the pattern hardcode a specific resource name, label, or identifier from the scanned project (e.g., `"bob"`, `"prod-api"`, `"myapp-storage"`)?
   → If **yes**: replace the name with `$_` (wildcard) or `"$NAME"` (metavariable). The rule must match **all resources of that type**, not just the one you found it on.

2. **Environment-identifier check** — Does the pattern contain a tenant ID, subscription ID, account number, org name, hostname, domain, or URL path specific to one environment?
   → If **yes**: these are never acceptable in rules. Document the finding directly without creating a rule.

3. **Vulnerability-class check** — Is the detected pattern a recognised security misconfiguration?
   - Tied to a CWE, OWASP category, cloud provider security benchmark, or documented attack pattern
   - "This named resource exists" is not a finding — the misconfiguration must be what triggers the rule
   → If **no**: the rule has no security value — discard it.

4. **Cross-repo fire test** — Mentally apply the rule to three unrelated repos of the same technology from different companies. Would it fire on all three if they had the same misconfiguration, and not fire if they were correctly configured?
   → If **no**: the rule is too specific or too broad — revise it.

5. **Hello-world test** — Would this rule fire on a basic sample project for that technology with no security issues?
   → If **yes**: the rule is too broad — add structural constraints to narrow it.

### Good vs Bad Examples

```yaml
# ❌ TOO SPECIFIC — only fires on a storage account named "bob" in this one project
patterns:
  - pattern: |
      resource "azurerm_storage_account" "bob" {
        ...
        allow_nested_items_to_be_public = true
        ...
      }

# ✅ JUST RIGHT — fires on ANY storage account in ANY Terraform repo with public access
patterns:
  - pattern: |
      resource "azurerm_storage_account" "$_" {
        ...
        allow_nested_items_to_be_public = true
        ...
      }

# ✅ ALSO JUST RIGHT — fires on ANY storage account missing the setting (defaults to true)
patterns:
  - pattern: |
      resource "azurerm_storage_account" "$_" { ... }
  - pattern-not: |
      resource "azurerm_storage_account" "$_" {
        ...
        allow_nested_items_to_be_public = false
        ...
      }
```

```yaml
# ❌ TOO BROAD — any container existing at all is not a security issue
pattern: |
  resource "azurerm_storage_container" "$_" { ... }

# ✅ JUST RIGHT — detects when access is explicitly set to public (blob or container)
patterns:
  - pattern: |
      resource "azurerm_storage_container" "$_" {
        ...
        container_access_type = "$ACCESS"
        ...
      }
  - metavariable-regex:
      metavariable: $ACCESS
      regex: '^(blob|container)$'
```

```yaml
# ❌ TOO SPECIFIC — matches only resources named exactly "prod-sql-01" from one project
pattern: |
  resource "azurerm_sql_server" "prod-sql-01" { ... }

# ✅ JUST RIGHT — matches any SQL Server resource in any repo missing TLS 1.2
patterns:
  - pattern: |
      resource "azurerm_sql_server" "$_" { ... }
  - pattern-not: |
      resource "azurerm_sql_server" "$_" {
        ...
        minimum_tls_version = "1.2"
        ...
      }
```

### When You Cannot Make a Rule Generic

If the finding is inherently about a one-off configuration choice (e.g., a specific resource has an unusual setting that is only relevant in that project's context), **do not create a rule**. Instead:
- Document the finding directly in the relevant findings file.
- Note in the finding that no reusable rule could be created and why.

---

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
├── Detection/ or Misconfigurations/  # Phase determines folder as Code rules
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

### Required Validation — Run Before Committing

Every new or modified rule **must** pass the following command before being committed.  This runs both opengrep syntax validation and the portability checks in one step:

```bash
python3 Scripts/Validate/validate_rule_portability.py <rule-file.yml>
```

The script runs two phases:
1. **`opengrep validate`** — catches syntax errors and unsupported pattern constructs (exits non-zero on any error).
2. **Portability checks** — detects hardcoded project-specific identifiers: Terraform resource literal names, UUIDs/GUIDs, IP addresses, FQDNs, and long numeric account IDs.

A rule is not ready to commit until this script exits `0` for that file.

### Manual Functional Testing
```bash
# Create test file with vulnerable code
cat > test.tf << 'EOF'
resource "azurerm_key_vault_secret" "test" {
  value = "hardcoded-secret"
}
EOF

# Test rule (when opengrep available)
opengrep scan --config Rules/Misconfigurations/Terraform/terraform-hardcoded-keyvault-secret.yml test.tf
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

**Create rule:** `Rules/Detection/ or Rules/Misconfigurations/<Provider>/`  
**Format:** Opengrep/Semgrep YAML  
**Test:** Manual grep or opengrep scan  
**Track:** Reference rule-id in findings  
**Learn:** Document missed detections and new rules

## Context discovery metadata for assets
When writing context_discovery rules, include optional metadata to mark findings as assets and identify the provider:

metadata:
  finding_kind: Asset        # non-breaking label indicating this finding represents an asset/resource
  asset_provider: aws|azure|gcp|unknown

Do NOT change the `severity:` value — keep discovery rules as `INFO` so downstream scoring and storage behave correctly.
