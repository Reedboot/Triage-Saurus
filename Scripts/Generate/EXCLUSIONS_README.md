# Architecture Diagram Resource Exclusions

## Overview

The file `architecture_diagram_exclusions.json` controls which resource types should be filtered out of architecture diagrams. Resources of excluded types won't appear in Mermaid diagrams even if they exist in the scan results.

## Purpose

Architecture diagrams should show the actual infrastructure topology - the resources that represent running systems and services. Many resources in Terraform IaC are configuration objects, metadata, or data sources that don't represent infrastructure components:

- **Configuration objects**: `azurerm_postgresql_configuration`, `aws_db_parameter_group`
- **Data sources**: `google_compute_zones`, `oci_objectstorage_namespace`
- **Credentials/Keys**: `azurerm_key_vault_secret`, `aws_kms_alias`
- **Temporary resources**: `random_string`, `null_resource`
- **Metadata**: `aws_caller_identity`, `azurerm_client_config`

## During AI Scans

When reviewing scan results, if you notice a resource type that:
1. Doesn't represent actual infrastructure
2. Is cluttering the architecture diagram
3. Should be excluded from future diagrams

**Add it to the exclusions list:**

```json
{
  "excluded_resource_types": [
    "existing_types_here",
    "new_type_to_exclude"
  ]
}
```

## Example Exclusions

- `aws_iam_policy_document` - just a data object, not a resource
- `azurerm_resource_group` - container/metadata, not infrastructure
- `random_integer` - temporary generation, not infrastructure
- `aws_db_parameter_group` - configuration for a DB, not the DB itself

## How It Works

1. Load: The exclusion list is loaded at startup by `generate_hierarchical_diagram.py`
2. Filter: During `load_data()`, resources matching excluded types are removed
3. Result: Only meaningful infrastructure resources appear in the final diagram

## Best Practices

- Focus on **resource types**, not resource names
- Include resource types that are genuinely non-architectural
- Keep the list organized by cloud provider (AWS, Azure, GCP, etc)
- Add notes explaining why types are excluded
