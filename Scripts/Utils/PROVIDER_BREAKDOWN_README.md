# Per-Provider Issue Breakdown

## Overview

Issues and findings are now automatically broken down by cloud provider throughout Triage-Saurus, providing multi-cloud visibility and attack surface analysis per provider.

## Features

### 1. Web UI - Findings Tab

The Findings tab now groups all findings by cloud provider with an intuitive interface:

**Grouping:**
- Findings automatically grouped by provider: **AWS**, **Azure**, **Google Cloud**, **Oracle Cloud**, **Alibaba Cloud**, **Unknown**
- Each provider section is collapsible for cleaner UI
- Shows count of findings per provider with severity breakdown

**Filtering:**
- **Provider Filter**: Select findings from a specific cloud provider
- **Severity Filter**: Filter by severity (Critical, High, Medium, Low, Info) within selected provider
- **Text Search**: Full-text search across title, rule ID, category, file path, and description

**Display Format:**
```
☁️ AWS (5 findings)
  ├─ 🔴 CRITICAL (2)
  ├─ 🟠 HIGH (2)
  └─ 🟡 MEDIUM (1)
  └─ Finding cards...

☁️ Azure (3 findings)
  ├─ 🔴 CRITICAL (1)
  └─ Finding cards...

☁️ Unknown Provider (2 findings)
  └─ Finding cards...
```

### 2. Risk Register Export (Excel)

The Risk Register spreadsheet now includes a **Cloud Provider** column:

| Priority | **Cloud Provider** | Resource Type | Issue | Risk Score | Severity | Status |
|----------|-----|------|------|-----|----------|--------|
| 1 | AWS | Security Group | Port 22 open to internet | 8 | HIGH | ✅ Validated |
| 2 | Azure | Key Vault | Access policy too permissive | 7 | HIGH | ✅ Validated |
| 3 | GCP | Cloud Storage | Bucket publicly readable | 9 | CRITICAL | ⚠️ Draft |

**Benefits:**
- Sorted by provider first, then by severity
- Enables easy filtering and pivot analysis in Excel
- Helps identify provider-specific risk patterns
- Supports multi-cloud compliance tracking

### 3. Database Integration

Provider information flows from the resources table:

```python
# Resources table has provider field:
# - 'aws', 'azure', 'gcp', 'oci', 'alicloud', or 'Unknown'

# Findings are linked via resource_id:
SELECT f.id, f.title, f.severity_score,
       COALESCE(r.provider, 'Unknown') AS provider
FROM findings f
LEFT JOIN resources r ON f.resource_id = r.id
ORDER BY r.provider, f.severity_score DESC
```

## Provider Detection

The system uses multiple methods to determine provider (in order of precedence):

1. **Direct Provider Field**: If resource has `provider` column (most reliable)
2. **Resource Type Inference**: Detects provider from resource type patterns:
   - `azurerm_*` → Azure
   - `aws_*` → AWS
   - `google_*` → GCP
   - `oci_*` → Oracle Cloud
   - `alicloud_*` → Alibaba Cloud
3. **Finding Context**: Analyzes finding metadata and context
4. **File Path Analysis**: Infers from file path conventions
5. **Default**: 'Unknown' for findings without provider information

## Helper Functions

Module: `Scripts/Utils/findings_by_provider.py`

### Group Findings by Provider
```python
from findings_by_provider import group_findings_by_provider

groups = group_findings_by_provider(findings)
# Returns: {'aws': [...], 'azure': [...], 'gcp': [...], 'Unknown': [...]}
```

### Get Provider Display Name
```python
from findings_by_provider import provider_display_name

name = provider_display_name('aws')  # Returns: '☁️ AWS'
```

### Count by Provider and Severity
```python
from findings_by_provider import count_findings_by_provider_and_severity

counts = count_findings_by_provider_and_severity(findings)
# Returns: {'aws': {'CRITICAL': 5, 'HIGH': 3, ...}, ...}
```

### Generate Summary String
```python
from findings_by_provider import get_provider_summary

summary = get_provider_summary(findings)
# Returns: "AWS: 5 Critical, 3 High | Azure: 2 Critical | GCP: 1 High"
```

## Implementation Details

### Web App Changes (app.py)

The `api_view_findings()` endpoint now:
1. Joins findings with resources table to fetch provider information
2. Orders results by provider first, then severity
3. Passes provider data to template in each finding

```python
rows = conn.execute("""
    SELECT f.id, f.title, ...,
           COALESCE(r.provider, 'Unknown') AS provider,
           r.resource_type
    FROM findings f
    LEFT JOIN resources r ON f.resource_id = r.id
    ORDER BY provider ASC, severity DESC
""")
```

### Template Changes (tab_findings.html)

The findings template now:
1. Groups findings by provider using Jinja2 logic
2. Creates collapsible sections per provider
3. Adds provider filter dropdown
4. Updates JavaScript filtering to include provider dimension

```html
{# Group findings by provider #}
{% for provider in ['aws', 'azure', 'gcp', ...] %}
  <div class="provider-group" data-provider="{{ provider }}">
    <!-- Provider section with findings -->
  </div>
{% endfor %}
```

### Risk Register Changes (risk_register.py)

The RiskRow dataclass now includes:
```python
@dataclass(frozen=True)
class RiskRow:
    provider: str = "Unknown"  # Cloud provider
    # ... other fields
```

Provider is:
- Extracted from resources table (database method)
- Inferred from file paths (markdown fallback method)
- Included in Excel export as 'Cloud Provider' column

## Usage Examples

### Multi-Cloud Attack Surface Analysis

1. **Open Findings tab** in web UI
2. **Observe provider groups** showing all clouds in one view
3. **Filter by provider** to focus on specific cloud's issues
4. **Compare severity** across clouds to identify risk concentration

### Risk Register Analysis

1. **Generate Risk Register** → outputs Excel file
2. **Open "Cloud Provider" column** → sort by provider
3. **Create pivot table** → group by provider and severity
4. **Identify patterns** → which provider has most critical issues?

### Automated Reporting

```python
from Scripts.Utils.findings_by_provider import get_provider_summary

findings = fetch_experiment_findings(exp_id='001')
summary = get_provider_summary(findings)

# Output: "AWS: 8 Critical, 5 High | Azure: 2 Critical | GCP: 1 Medium"
# Use in reports, dashboards, notifications
```

## Data Quality Notes

**⚠️ Important**: 162 findings in the database have no `resource_id`

This means:
- They cannot be directly linked to a resource provider
- These findings default to provider='Unknown'
- They still appear in the Findings tab under "Unknown Provider" section

**Recommendations:**
1. Ensure new findings always capture `resource_id` when available
2. Populate `resource_id` retroactively for important findings
3. Monitor "Unknown Provider" findings for data quality issues

## Future Enhancements

Potential improvements:

1. **Provider Color Coding**: Assign consistent colors to each provider in UI
2. **Cross-Provider Comparisons**: Dashboard showing metrics per provider
3. **Provider-Specific Reports**: Generate separate reports per cloud
4. **Multi-Cloud Compliance**: Track compliance status by provider
5. **Cost Impact Analysis**: Correlate severity with remediation cost by provider
6. **Provider Migration Tracking**: Identify findings that block cloud migration

