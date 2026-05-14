#!/usr/bin/env python3
"""
Module Registry System — Complete Workflow

This system enables the Triage-Saurus scanner to understand what infrastructure
external Terraform modules create, allowing diagrams to show complete architecture
even when resources are delegated to external modules.

## Components

### 1. module_registry.py
- Analyzes a module repo to extract:
  * All resource types it declares (azurerm_kubernetes_cluster, etc.)
  * Its outputs (what it exposes)
  * Its variables (what it accepts as input)
- Stores this metadata in a SQLite database
- Functions:
  * analyze_module(path) -> ModuleMetadata
  * register_module(db, metadata)
  * lookup_module(db, source) -> ModuleMetadata

### 2. infer_module_resources.py
- Analyzes a repo that USES modules
- Extracts all module invocations: module "aks" { source = "..." }
- Looks up each module in the registry
- Returns what resources will be created
- Functions:
  * infer_resources_from_modules(repo_path, registry_db) -> Dict

### 3. register_scanned_module.py
- Convenience CLI to register a module after scanning
- Called after scanning terraform-aks, terraform-network, etc.
- Usage:
    python3 Scripts/Context/register_scanned_module.py \\
      /path/to/terraform-aks \\
      "git::https://dev.azure.com/.../terraform-aks"

## Workflow

### Step 1: Scan a Module Repo
```bash
# User selects terraform-aks from the module detection modal
# Scan runs: opengrep scan, discovers resources, creates diagrams
```

### Step 2: Register the Module
```bash
python3 Scripts/Context/register_scanned_module.py \\
  /mnt/c/Repos/terraform-aks \\
  "git::https://dev.azure.com/.../terraform-aks"

# Output:
# ✅ Module registered successfully
#    Resource types: 42 (azurerm_kubernetes_cluster, azurerm_node_pool, ...)
#    Outputs: 21
#    Variables: 89
```

### Step 3: Scan a Repo That Uses Modules
```bash
# User scans fi_authentication
# Scanner detects modules and calls infer_module_resources()
# fi_authentication shows:
#   - Directly declared resources (azurerm_cosmosdb_account, etc.)
#   - Inferred resources from modules (aks -> azurerm_kubernetes_cluster, ...)
```

### Step 4: Diagram Shows Complete Infrastructure
```
The architecture diagram now shows:
  ✅ Cosmos DB (declared directly)
  ✅ App Insights (declared directly)
  ✅ AKS Cluster (inferred from module)
  ✅ Key Vault (inferred from module)
  ✅ All networking (inferred from module)
```

## Database Schema

### module_registry table
```
module_source: "git::https://..."  (unique, primary key)
module_name: "terraform-aks"
resource_types: ["azurerm_kubernetes_cluster", ...]  (JSON)
outputs: {"cluster_id": "azurerm_kubernetes_cluster.aks.id"}  (JSON)
variables: {"app_name": null, "environment": null}  (JSON)
scanned_at: timestamp
```

### module_usage table
```
experiment_id: experiment ID during scan
repo_id: the repo that uses the module
module_instance_name: "aks" (from module "aks" { ... })
module_source: "git::https://..." (foreign key)
source_file: "terraform/kubernetes.tf"
source_line: 1
resolved_resource_types: [...]  (JSON)
```

## Example: Parameterization

When the same module is invoked with different parameters:

```hcl
# In fi_authentication
module "aks" {
  source = "git::https://dev.azure.com/.../terraform-aks"
  app_name = "fi-authentication"
}

# In another repo
module "aks" {
  source = "git::https://dev.azure.com/.../terraform-aks"
  app_name = "payments-service"
}
```

Both repos infer the SAME resource types from the registry:
- azurerm_kubernetes_cluster
- azurerm_node_pool
- azurerm_key_vault
- etc.

The NAMING differs (fi-authentication-aks vs payments-service-aks), but the
infrastructure type and shape is understood to be identical.

## Future Enhancements

1. **Cross-Repo Dependencies**
   - When module A calls module B, infer B's resources too
   - Build a complete dependency tree

2. **Parameterization Awareness**
   - Track which variables affect which resources
   - "If app_name is set, azurerm_kubernetes_cluster is created"

3. **Variable Propagation**
   - When fi_authentication passes var.environment to aks module,
     understand that the module's environment is the repo's environment

4. **Diagram Labeling**
   - "AKS Cluster (terraform-aks module, v2.1.0)"
   - Show where each resource comes from

5. **Change Tracking**
   - "When terraform-aks updated from v1.0 to v2.0, added azurerm_policy_definition"
   - Alert on upstream module changes affecting diagrams

## Testing

```bash
# Analyze a module
python3 Scripts/Context/module_registry.py analyze /path/to/module

# Register it
python3 Scripts/Context/register_scanned_module.py /path/to/module "source-url"

# Infer resources in a consuming repo
python3 Scripts/Context/infer_module_resources.py /path/to/repo registry.db
```
"""
