# Generic Resource Detection - LLM-Driven Context Discovery

**Concept:** Use simple pattern matching to extract resource types, then let the LLM interpret semantic meaning.

---

## The Problem

**Traditional approach:** Create 100+ specific rules for each resource type.

```yaml
# Need separate rules for each:
sql-server-detection.yml        # azurerm_mssql_server
keyvault-detection.yml          # azurerm_key_vault
storage-account-detection.yml   # azurerm_storage_account
cosmos-db-detection.yml         # azurerm_cosmosdb_account
...
# Repeat for 100+ Azure resources
# Repeat for 200+ AWS resources
# Repeat for 50+ Kubernetes kinds
```

**Maintenance nightmare:**
- New Azure service? Add new rule.
- AWS launches new resource type? Add new rule.
- Kubernetes adds new CRD? Add new rule.
- Total: 500+ detection rules across providers

---

## The Solution: Generic Rules + LLM

**New approach:** 1 rule per provider that extracts ALL resources, LLM interprets.

```yaml
# ONE rule for all Azure resources
generic-azure-resource-detection.yml
  pattern: resource "azurerm_$TYPE" $NAME { ... }
  requires_llm: true

# ONE rule for all AWS resources  
generic-aws-resource-detection.yml
  pattern: resource "aws_$TYPE" $NAME { ... }
  requires_llm: true

# ONE rule for all Kubernetes resources
generic-kubernetes-resource-detection.yml
  pattern: kind: $KIND
  requires_llm: true
```

---

## How It Works

### Step 1: Pattern Match (Fast, No LLM)

**Generic rule extracts structure:**
```
Resource detected:
  provider: azurerm
  resource_type: mssql_server
  resource_name: sqlserver-tycho
  location: East US
```

### Step 2: LLM Interpretation (Batch Processing)

**Send batch of resource types to LLM:**
```json
{
  "resources": [
    {"type": "azurerm_mssql_server", "name": "sqlserver-tycho"},
    {"type": "azurerm_key_vault", "name": "keyvault-ganymede"},
    {"type": "azurerm_storage_account", "name": "storage-pallas"},
    {"type": "azurerm_cosmosdb_account", "name": "cosmos-db-main"}
  ]
}
```

**LLM responds with interpretations:**
```json
{
  "interpretations": [
    {
      "type": "azurerm_mssql_server",
      "service_name": "SQL Server",
      "category": "database",
      "security_relevant": true,
      "suggested_rules": ["sql-auditing", "sql-firewall", "sql-tde"]
    },
    {
      "type": "azurerm_key_vault",
      "service_name": "Key Vault",
      "category": "secrets-management",
      "security_relevant": true,
      "suggested_rules": ["keyvault-network-acl", "keyvault-logging", "keyvault-rbac"]
    },
    {
      "type": "azurerm_storage_account",
      "service_name": "Storage Account",
      "category": "storage",
      "security_relevant": true,
      "suggested_rules": ["storage-encryption", "storage-network-acl", "storage-https"]
    },
    {
      "type": "azurerm_cosmosdb_account",
      "service_name": "Cosmos DB",
      "category": "database",
      "security_relevant": true,
      "suggested_rules": ["cosmosdb-network", "cosmosdb-backup", "cosmosdb-tls"]
    }
  ]
}
```

### Step 3: Context Population

**Final context dictionary:**
```python
context = {
  "SQL Server": ["sqlserver-tycho"],
  "Key Vault": ["keyvault-ganymede"],
  "Storage Account": ["storage-pallas"],
  "Cosmos DB": ["cosmos-db-main"],
  
  "services_by_category": {
    "database": ["SQL Server", "Cosmos DB"],
    "secrets-management": ["Key Vault"],
    "storage": ["Storage Account"]
  },
  
  "suggested_security_rules": [
    "sql-auditing", "sql-firewall", "sql-tde",
    "keyvault-network-acl", "keyvault-logging",
    "storage-encryption", "storage-https",
    "cosmosdb-network", "cosmosdb-tls"
  ]
}
```

---

## Benefits

### 1. Scalability
- **Before:** 100 rules for 100 Azure resources
- **After:** 1 rule for ALL Azure resources
- **Reduction:** 99% fewer rules

### 2. Automatic Coverage
- New Azure service launches → Automatically detected
- No rule updates needed
- LLM learns new services over time

### 3. Intelligent Categorization
- LLM provides semantic understanding
- Groups related services (databases, storage, compute)
- Suggests relevant security rules dynamically

### 4. Multi-Cloud Support
- Same pattern for Azure, AWS, GCP
- 3 generic rules cover 500+ resource types

### 5. CRD/Custom Resources
- Kubernetes CRDs automatically detected
- No need to know every custom resource type upfront

---

## Execution Flow

```
┌─────────────────────────────────────────────────┐
│  Phase 1a: Generic Pattern Extraction (Fast)   │
│  - Scan *.tf files                              │
│  - Extract: resource "azurerm_*" ...            │
│  - Result: List of resource_types + names      │
│  - Time: <1 second per repo                    │
└─────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────┐
│  Phase 1b: LLM Batch Interpretation             │
│  - Send batch: [azurerm_mssql_server, ...]     │
│  - LLM responds: Service names + categories    │
│  - Cache results for future runs               │
│  - Time: ~2 seconds per batch (10 resources)   │
└─────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────┐
│  Phase 2: Security Rules (Filtered)             │
│  - Apply suggested rules from LLM               │
│  - Skip irrelevant rules (no SQL? skip SQL)    │
│  - Time: ~30 seconds                            │
└─────────────────────────────────────────────────┘
```

---

## Caching Strategy

**Problem:** Don't want to ask LLM "what is azurerm_mssql_server?" every scan.

**Solution:** Cache interpretations locally.

```python
# Cache file: ~/.triage-saurus/resource_type_cache.json
{
  "azurerm_mssql_server": {
    "service_name": "SQL Server",
    "category": "database",
    "cached_at": "2026-02-26T08:00:00Z",
    "llm_model": "claude-sonnet-4.5"
  },
  "azurerm_key_vault": {...},
  ...
}

# On scan:
if resource_type in cache:
    interpretation = cache[resource_type]
else:
    interpretation = ask_llm(resource_type)
    cache[resource_type] = interpretation
```

**Cache invalidation:**
- Manual refresh: `triage-saurus cache --refresh`
- Auto-refresh: After 30 days
- Model change: Clear cache if LLM model upgraded

---

## Example: Multi-Cloud Scan

**Repository with mixed infrastructure:**
```
terraform/
├── azure.tf         # 15 azurerm_* resources
├── aws.tf           # 8 aws_* resources
├── gcp.tf           # 5 google_* resources
└── kubernetes.yaml  # 20 Kubernetes resources
```

**Traditional approach:**
- Scan with 48 specific detection rules (one per resource type)
- Maintain 48 separate rule files
- Update rules when new resource types added

**Generic approach:**
- Scan with 4 generic rules (azure, aws, gcp, k8s)
- Extract 48 resource types
- Send 1 batch to LLM: "Interpret these 48 resource types"
- Get back categorized context
- Apply relevant security rules

**Result:**
- 92% fewer rule files
- Automatic support for new resource types
- Faster maintenance

---

## Migration Strategy

### Phase 1: Parallel Operation (Current)
```
Rules/Context/
├── azure-resources/
│   ├── sql-server-detection.yml          # Specific (keep for now)
│   ├── keyvault-detection.yml            # Specific (keep for now)
│   └── generic-azure-resource-detection.yml  # NEW: Generic + LLM
```

**Run both:** Validate LLM accuracy vs specific rules.

### Phase 2: Transition
- Compare specific rule hits vs generic rule + LLM hits
- Validate LLM accuracy (should be >95%)
- Keep specific rules for critical services (SQL, Key Vault)

### Phase 3: Full Adoption
- Remove specific detection rules for common resources
- Keep generic rules + LLM interpretation
- Maintain cache for performance

---

## Performance

### Before (Specific Rules):
```
100 rules × 0.1s each = 10 seconds scanning
No LLM cost
```

### After (Generic Rule + LLM):
```
1 rule × 0.1s = 0.1 seconds scanning
1 LLM batch call (10 resources) = 2 seconds
Cache hit rate: 90% (after first run)

First run:  2.1 seconds + LLM cost ($0.01)
Cached run: 0.1 seconds + no LLM cost

Result: Faster after cache is warm!
```

---

## Rule Files Created

✅ `generic-azure-resource-detection.yml`  
✅ `generic-aws-resource-detection.yml`  
✅ `generic-kubernetes-resource-detection.yml`

---

## Next Steps

1. **Test generic rules** with semgrep on sample repos
2. **Implement LLM interpretation** in discover_repo_context.py
3. **Add caching layer** for resource type interpretations
4. **Validate accuracy** against existing specific rules
5. **Measure performance** (specific vs generic + LLM)
6. **Decide migration strategy** based on results

---

**Created:** 2026-02-26  
**Status:** PROTOTYPE - Validation needed  
**Innovation:** Rule-driven structure extraction + LLM semantic interpretation
