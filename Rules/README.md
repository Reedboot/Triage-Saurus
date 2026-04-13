# Template Variable Patterns - Safe Pipeline Replacements

This document defines template variable patterns that are **SAFE** and should **NOT** be flagged as secrets by detection rules.

## Safe Patterns

Template variables are placeholders that get replaced by CI/CD pipelines, configuration management tools, or environment variable expansion at deployment time. These are **NOT** hardcoded secrets.

### Common Template Variable Formats

| Pattern | Example | Used By |
|---------|---------|---------|
| `${VAR_NAME}` | `${MSSQL_SA_PASSWORD}` | Bash, Docker, Kubernetes, Terraform interpolation |
| `$(VAR_NAME)` | `$(Build.BuildId)` | Azure DevOps Pipelines, Make |
| `%VAR_NAME%` | `%DATABASE_PASSWORD%` | Windows batch, cmd |
| `{{VAR_NAME}}` | `{{.Values.password}}` | Helm, Ansible, Jinja2 |
| `$VAR_NAME` | `$DATABASE_URL` | Shell scripts, Docker Compose |
| `env.VAR_NAME` | `env.DB_PASSWORD` | Node.js, Python (dotenv) |

### Regex Pattern for Detection Rules

To exclude template variables from secret detection, use this pattern-not-regex:

```yaml
pattern-not-regex: '(\$\{[^}]+\}|\$\([^)]+\)|%[A-Za-z_][A-Za-z0-9_]*%|\{\{[^}]+\}\})'
```

Or for simple shell variables:
```yaml
pattern-not-regex: '\$[A-Za-z_][A-Za-z0-9_]*'
```

## Example: Safe Connection Strings

### ✅ Safe (Template Variables)
```bash
# Docker Compose
SQLSERVER_CONNECTION=Server=sql.example.com;Password=${MSSQL_SA_PASSWORD}

# Kubernetes
- name: DB_CONNECTION_STRING
  value: "Server=$(DB_SERVER);Password=$(DB_PASSWORD)"

# Terraform
connection_string = "DefaultEndpointsProtocol=https;AccountName=${var.storage_account_name};AccountKey=${var.storage_account_key}"

# Azure DevOps
SQL_CONNECTION: Server=sql.azure.com;User ID=$(SQL_USER);Password=$(SQL_PASSWORD)
```

### ❌ Unsafe (Hardcoded Values)
```bash
# Docker Compose - REAL SECRET
SQLSERVER_CONNECTION=Server=sql.example.com;Password=MyRealPassword123!

# Kubernetes - REAL AWS KEY
- name: AWS_SECRET_ACCESS_KEY
  value: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# Terraform - REAL CONNECTION STRING
connection_string = "DefaultEndpointsProtocol=https;AccountName=mystorage;AccountKey=abc123def456=="
```

## Implementation Guidelines

### For AI Analysis (web/app.py)
The AI prompt already includes:
```
Template variables are SAFE: ${VAR}, {{VAR}}, $VAR, $(VAR), %VAR% are pipeline/CI variables.
ONLY flag as secrets: actual hardcoded values like passwords, connection strings with real credentials.
```

### For OpenGrep Rules
Updated rules that now handle template variables:
- ✅ `Rules/Misconfigurations/Secrets/sql-connection-string.yml`
- ✅ `Rules/Misconfigurations/Cloud/hardcoded-connection-string.yml`
- ✅ `Rules/Misconfigurations/Terraform/Secrets/terraform-hardcoded-keyvault-secret.yml`
- ✅ `Rules/Misconfigurations/Secrets/hardcoded-aws-credentials-k8s.yml`
- ✅ `Rules/Misconfigurations/AWS/EC2/aws-ec2-user-data-credentials.yml`

## Context vs Real Secrets

### Documentation Files
Files like `README.md`, `quickstart.md`, `docs/`, `examples/` may contain:
- ✅ Template variables for documentation purposes (safe)
- ✅ Example connection strings with placeholders (safe)
- ❌ Should NOT be used as evidence of real vulnerabilities

The AI has been instructed to:
> "Documentation files provide CONTEXT but code examples within them are NOT implemented code.
> EXCLUDE findings from documentation/example files when creating action_items."

### Deployable Files
Only flag secrets in files that actually deploy:
- Production code (*.cs, *.js, *.py, etc.)
- IaC: `*.tf`, `*.tfvars`, `*.bicep`, `*.json` (ARM templates)
- `docker-compose.yml`
- Kubernetes manifests: `*.yaml`, `*.yml` in k8s/ dirs
- CI/CD configs: `.github/workflows/`, `.gitlab-ci.yml`, `azure-pipelines.yml`

## Testing Detection Rules

### Test Case 1: Should NOT Trigger
```terraform
resource "azurerm_key_vault_secret" "example" {
  name         = "db-password"
  value        = var.database_password        # ✅ Variable reference
  key_vault_id = azurerm_key_vault.example.id
}
```

### Test Case 2: Should NOT Trigger
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-credentials
stringData:
  password: ${DB_PASSWORD}  # ✅ Template variable
```

### Test Case 3: SHOULD Trigger
```terraform
resource "azurerm_key_vault_secret" "example" {
  name         = "db-password"
  value        = "SuperSecret123!"  # ❌ Hardcoded
  key_vault_id = azurerm_key_vault.example.id
}
```

### Test Case 4: SHOULD Trigger
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: aws-creds
stringData:
  access_key: AKIAIOSFODNN7EXAMPLE  # ❌ Real AWS key format
  secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY  # ❌ Real secret format
```

## Common False Positives to Avoid

1. **Build/Release Variables**: `$(Release.Artifacts.Drop.BuildNumber)`
2. **Helm Values**: `{{ .Values.database.password }}`
3. **Docker Build Args**: `ARG DB_PASSWORD` (becomes `$DB_PASSWORD`)
4. **Terraform Data Sources**: `data.azurerm_key_vault_secret.example.value`
5. **Generated Passwords**: `random_password.db.result`

## Rules That Need Review

Run this to find other secret detection rules that might need template variable exclusions:

```bash
grep -r "password\|secret\|credential\|api.?key" Rules/ --include="*.yml" | grep -E "(pattern-regex|pattern-either)" | cut -d: -f1 | sort -u
```

Then check if they exclude template variables using pattern-not-regex.
