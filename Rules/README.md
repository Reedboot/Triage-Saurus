# Context Discovery Rules

**Purpose:** Extract services, frameworks, and languages for reconnaissance phase.  
**Execution:** Run FIRST, before security scanning.  
**Output:** Populate context to filter security rules and guide analysis.

---

## Directory Structure

```
Rules/Context/
├── azure-resources/     # Azure service detection from Terraform
│   ├── sql-server-detection.yml
│   ├── keyvault-detection.yml
│   ├── storage-account-detection.yml
│   └── aks-cluster-detection.yml
├── aws-resources/       # AWS service detection from Terraform
├── gcp-resources/       # GCP service detection
├── app-config/          # Application configuration & connection string detection
│   ├── servicebus-connection-detection.yml
│   ├── appinsights-connection-detection.yml
│   ├── sql-connection-detection.yml
│   ├── storage-connection-detection.yml
│   └── redis-connection-detection.yml
├── containers/          # Container/Dockerfile detection
│   └── dockerfile-base-image-detection.yml
└── frameworks/          # Programming frameworks and versions
    ├── dotnet-detection.yml
    ├── nodejs-detection.yml
    ├── python-detection.yml
    ├── golang-detection.yml
    └── java-maven-detection.yml
```

---

## Rules Summary

### Azure Resources (28 rules)

| Rule ID | Service | Resource Type | Target Files |
|---------|---------|---------------|--------------|
| context-azure-sql-server | SQL Server | azurerm_mssql_server | *.tf |
| context-azure-keyvault | Key Vault | azurerm_key_vault | *.tf |
| context-azure-storage-account | Storage Account | azurerm_storage_account | *.tf |
| context-azure-aks-cluster | AKS Cluster | azurerm_kubernetes_cluster | *.tf |

**Extracted Properties:**
- Service type (SQL Server, Key Vault, etc.)
- Resource name
- Location
- Service-specific configuration (version, SKU, RBAC settings)

### Frameworks (5 rules)

| Rule ID | Framework | Language | Target Files | Version Extracted |
|---------|-----------|----------|--------------|-------------------|
| context-dotnet-csproj | .NET | C# | *.csproj | net8.0 → .NET 8 |
| context-nodejs-package-json | Node.js | JavaScript | package.json | engines.node |
| context-python-requirements | Python | Python | requirements.txt | python_requires |
| context-golang-go-mod | Go | Go | go.mod | go 1.21 |
| context-java-maven | Maven | Java | pom.xml | compiler.source |

**Extracted Properties:**
- Framework name and version
- Language version
- Popular libraries/frameworks detected (Spring Boot, Express, Django, etc.)

---

## Usage

### Execution Flow

**Phase 1: Run Context Rules**
```bash
# Scan repository with context rules
semgrep --config Rules/Context/ /path/to/repo
```

**Output Example:**
```json
{
  "context": {
    "SQL Server": ["sqlserver-tycho", "sqlserver-db2"],
    "Key Vault": ["keyvault-ganymede"],
    "Storage Account": ["storage-lab-pallas"],
    ".NET": {
      "version": ".NET 8",
      "language": "C#",
      "target_framework": "net8.0"
    }
  }
}
```

**Phase 2: Filter Security Rules**
```python
# Only run SQL security rules if SQL Server detected
if "SQL Server" in context:
    run_rules("Rules/Security/database/azure-sql-*.yml")

# Skip Key Vault rules if no Key Vault
if "Key Vault" not in context:
    skip_rules("Rules/Security/secrets/azure-keyvault-*.yml")
```

---

## Rule Metadata Standard

All context rules MUST include:

```yaml
metadata:
  rule_type: context_discovery           # Required: identifies as context rule
  category: infrastructure | application # Required: broad category
  subcategory: database | framework      # Required: specific category
  technology: [terraform, azure, sql]    # Required: tech stack tags
  target_files: ["*.tf"]                 # Required: file patterns to scan
  extracts:                              # Required: what to extract
    service_type: SQL Server
    resource_type: azurerm_mssql_server
    resource_name: $NAME
    properties:                          # Optional: additional properties
      - name: location
      - name: version
  description: |                         # Required: human-readable purpose
    What this rule detects and why.
```

---

## Version Mapping

### .NET Framework Monikers
| Moniker | Friendly Name |
|---------|---------------|
| net8.0 | .NET 8 |
| net7.0 | .NET 7 |
| net6.0 | .NET 6 |
| netcoreapp3.1 | .NET Core 3.1 |
| net48 | .NET Framework 4.8 |

### Node.js Engine Constraints
| Pattern | Example |
|---------|---------|
| `"node": ">=18.0.0"` | Node.js 18+ |
| `"node": "^20.11.0"` | Node.js 20.11.x |

### Python Version Constraints
| Pattern | Example |
|---------|---------|
| `python_requires = ">=3.11"` | Python 3.11+ |
| `Python==3.12.1` | Python 3.12.1 |

---

## Adding New Context Rules

### Template

```yaml
rules:
  - id: context-<technology>-<service>
    message: |
      <Service> detected in infrastructure.
      This rule extracts <service> configuration for context analysis.
    severity: INFO
    languages: [<language>]
    pattern: |
      <pattern to match>
    metadata:
      rule_type: context_discovery
      category: infrastructure | application
      subcategory: database | framework | ...
      technology: [tech1, tech2]
      target_files: ["*.ext"]
      extracts:
        service_type: <Service Name>
        resource_type: <resource_type>
        resource_name: $NAME
        properties:
          - name: property1
          - name: property2
      description: |
        What this rule detects and how it's used.
```

### Testing

```bash
# Test rule against sample file
semgrep --config Rules/Context/azure-resources/sql-server-detection.yml \
        /path/to/terraform/main.tf

# Expected output: List of SQL Server resources found
```

---

## Integration with Security Rules

Security rules can reference context discoveries:

```yaml
# Rules/Security/database/azure-sql-auditing-disabled.yml
metadata:
  rule_type: security_misconfiguration
  requires_context: sql-server-detection  # Only run if SQL found
  severity: ERROR
```

Script logic:
```python
context = run_context_rules()

for rule in security_rules:
    if rule.metadata.requires_context:
        if rule.metadata.requires_context not in context:
            continue  # Skip this security rule
    
    findings.append(run_rule(rule))
```

---

## Next Steps

1. ✅ Create pilot context rules (9 rules created)
2. ⏳ Update discover_repo_context.py to use rule-driven discovery
3. ⏳ Create script to parse rule outputs into context dictionary
4. ⏳ Update security rules to reference context requirements
5. ⏳ Validate with Experiment 016

---

**Created:** 2026-02-26  
**Status:** PILOT - Ready for testing  
**See Also:** Output/Learning/RULE_STRUCTURE_PROPOSAL.md
### Application Configuration (5 rules)

| Rule ID | Signal | Target Files | Key Properties |
|---------|--------|--------------|----------------|
| context-azure-servicebus-connection | Service Bus connection string | *.config, *.json, *.tf* | namespace, SAS key name |
| context-azure-application-insights-connection | App Insights instrumentation | *.config, *.json, *.js | instrumentation key, ingestion endpoint |
| context-azure-sql-connection-string | SQL DB connection string | *.config, *.json, *.tf* | server, database, authentication |
| context-azure-storage-connection | Storage account connection string | *.config, *.json, *.tf* | account name, endpoint suffix |
| context-azure-redis-connection | Redis cache connection string | *.config, *.json, *.tf* | host, SSL enabled |

**Use case:** Detects runtime dependencies directly from configuration, enabling security rules to run even when IaC doesn't exist.

### Containers (1 rule)

| Rule ID | Signal | Target Files | Key Properties |
|---------|--------|--------------|----------------|
| context-dockerfile-base-image | Docker base images per stage | Dockerfile* | base_image, stage_name |

**Use case:** Extracts all FROM statements to understand builder vs runtime images, multi-service Dockerfiles, and base OS coverage.
