# Internet Exposure Analysis Template

> This template is used by `Scripts/Generate/render_exposure_summary.py` to generate
> per-provider exposure analysis reports. It combines:
> - Resource exposure classification (direct, mitigated, isolated)
> - OpenGrep vulnerability correlation
> - Risk scores (exposure × severity)
> - Mermaid architecture diagrams

## Header

```markdown
# Internet Exposure Analysis: {{ PROVIDER_NAME }}

Generated: {{ TIMESTAMP }}
Experiment: {{ EXPERIMENT_ID }}
```

## Summary Statistics

```markdown
## Summary

| Metric | Count |
|--------|-------|
| Directly Exposed | {{ COUNT_DIRECT_EXPOSURE }} |
| Mitigated | {{ COUNT_MITIGATED }} |
| Isolated | {{ COUNT_ISOLATED }} |
| With OpenGrep Violations | {{ COUNT_WITH_VIOLATIONS }} |
| Total Risk Score (weighted) | {{ TOTAL_RISK_SCORE }} |
```

## Architecture Diagram

Rendered as Mermaid graph with color coding:

```markdown
## Architecture Diagram

```mermaid
{{ MERMAID_DIAGRAM }}
```
```

## Risk Assessment Table

```markdown
## Risk Assessment

| Resource | Type | Exposure Level | Risk Score | Violations |
|---|---|---|---|---|
{{ RISK_TABLE_ROWS }}
```

Each row includes:
- **Resource**: Terraform resource name
- **Type**: Resource type (aws_s3_bucket, azurerm_storage_account, etc.)
- **Exposure Level**: direct_exposure | mitigated | isolated
- **Risk Score**: 0-10 (higher = more risk)
- **Violations**: OpenGrep rule IDs that flagged this resource

## Exposure Paths (Optional)

For directly exposed resources, show the path from entry point:

```markdown
## Directly Exposed Paths

### {{ RESOURCE_NAME }}
- **Entry Point**: {{ ENTRY_POINT_NAME }} ({{ ENTRY_POINT_TYPE }})
- **Path**: {{ ENTRY_POINT }} → {{ INTERMEDIATE_1 }} → ... → {{ RESOURCE }}
- **Violations**: {{ VIOLATION_LIST }}
```

## Recommendations

```markdown
## Recommendations

### Critical (Direct Exposure + Violations)
- {{ RESOURCE_NAME }}: [{{ VIOLATION }}] — Immediately implement WAF/NSG rules
- ...

### High (Direct Exposure, No Violations)
- {{ RESOURCE_NAME }}: Add security group/NSG to restrict access
- ...

### Medium (Mitigated + Violations)
- {{ RESOURCE_NAME }}: [{{ VIOLATION }}] — Verify countermeasure effectiveness
- ...
```

## Legend

```markdown
## Legend

- 🔴 **Red** (direct_exposure): Resource reachable from internet with no security controls
- 🟠 **Orange** (mitigated): Resource reachable from internet but behind WAF/NSG/Firewall
- 🟢 **Green** (isolated): Resource isolated from internet (private subnet, no IGW)

- 🌐 **Entry Points**: aws_internet_gateway, azurerm_public_ip, google_compute_address
- 🛡️ **Countermeasures**: aws_waf_web_acl, azurerm_application_gateway, google_compute_firewall
- ⚙️ **Compute**: aws_instance, azurerm_linux_virtual_machine, google_compute_instance
- 💾 **Data**: aws_s3_bucket, azurerm_storage_account, google_storage_bucket
```

## Notes

- **Multi-provider support**: One section per cloud provider (AWS, Azure, GCP)
- **OpenGrep correlation**: Violations are automatically pulled from findings DB
- **Risk scoring**: Final score = severity × exposure_multiplier (0-10 scale)
- **Path validation**: Traversal paths validated against resource_connections table
- **Confidence**: Classification confidence based on data completeness

## Implementation

Generated files:
- `Output/Summary/Cloud/Internet_Exposure_AWS.md`
- `Output/Summary/Cloud/Internet_Exposure_AZURE.md`
- `Output/Summary/Cloud/Internet_Exposure_GCP.md`
