# 🟣 Repository Knowledge: <repo-name>

## 📋 Overview
- **Full Path:** /path/to/repo
- **Purpose:** <one-line description>
- **Primary Function:** <what this repo does>
- **Last Scanned:** DD/MM/YYYY HH:MM

## 🛠️ Technology Stack

### Languages & Frameworks
- **Primary Language:** <language> (<version>)
- **Framework:** <framework> (<version>)
- **Target Runtime:** <platform>

### Key Dependencies (Production)
- **<package>** <version> - <purpose>

### Test/Dev Dependencies
- **<package>** <version> - <purpose> (⚠️ vulnerabilities if applicable)

## 🗺️ Architecture

### Request Ingress Path
**Evidence:** <cite files that prove this path>
```
[Origin]
  ↓ [protocol]
[Entry Point] — terraform/ingress.tf:45-67
  ↓ [mechanism]
[Service]
  ↓ [auth/routing]
[Dependencies]
```

### Middleware Pipeline / Request Flow
**Evidence:** <cite Startup.cs, main.go, etc.>
1. **<Middleware>** - <purpose> — <file>:<line>
2. **<Middleware>** - <purpose> — <file>:<line>

### Dependencies
**Evidence:** <cite code/IaC files>
- **<Service/API>** - <purpose> — <file>:<line>

## ☁️ Infrastructure as Code

### Provider & Versions
**Evidence:** <cite terraform/versions.tf, providers.tf>
- **IaC Tool:** Terraform/Pulumi/CloudFormation <version>
- **Cloud Provider:** Azure/AWS/GCP
- **Provider Version:** <version>

### Infrastructure Components
**Evidence:** <cite specific .tf/.yml files>
- **Compute:** <type> — <file>:<line>
- **Networking:** <type> — <file>:<line>
- **Security:** <type> — <file>:<line>

### Security Configurations
**Evidence:** <cite configuration in IaC>
- ✅ **<Control>:** Enabled — <file>:<line>
- ⚠️ **<Control>:** Not configured — <file>:<line>
- ❌ **<Control>:** Disabled — <file>:<line>

## 🛡️ Security Posture

### Confirmed Controls (with Evidence)
- ✅ **<Control>**: <description> — **Evidence:** <file>:<line>

### Missing Controls
- ❌ **<Control>**: Not implemented — **Searched:** <files checked>

### Security Findings
- **<Severity>** (<score>/10): [<finding title>](../Summary/Repos/<RepoName>.md) — brief description

## 🔑 Configuration & Secrets

### Secret Management
**Evidence:** <cite appsettings.json, env files, etc.>
- **Storage Method:** <KeyVault/Secrets Manager/env vars>
- **Injection Mechanism:** <how secrets get into app>

### Secret References Found
**Evidence:** <cite code files>
- **<SecretName>**: <purpose> — <file>:<line>

## 🚀 CI/CD Pipeline

### Pipeline Configuration
**Evidence:** <cite .github/workflows, .azure-pipelines.yml, etc.>
- **Platform:** <GitHub Actions/Azure DevOps/GitLab CI>
- **Build Trigger:** <branches>
- **Test Framework:** <framework>
- **Deployment Target:** <environment>

## ⚠️ Assumptions (Unconfirmed)
1. **<Assumption>** — Impact: <how this affects risk assessment>
   - Why assumed: <reasoning>
   - Needs confirmation: <specific question>

## ✅ Confirmed Facts
- ✅ <Fact> — **Evidence:** <file>:<line>

## ❓ Open Questions
1. <Question that affects risk scoring>
2. <Question about architecture/defenses>

---

**Template Notes:**
- ALWAYS cite specific files and line numbers for claims
- Mark assumptions clearly - don't claim defenses exist without proof
- Update "Last Scanned" timestamp when repo is re-scanned
- Link to security findings using clickable markdown links with relative paths

Last updated: DD/MM/YYYY HH:MM
