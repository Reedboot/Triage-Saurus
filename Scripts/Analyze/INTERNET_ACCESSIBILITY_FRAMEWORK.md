# Internet Accessibility Analysis & Audit Framework

## Overview

This framework provides automated, scriptable internet accessibility analysis for cloud resources. It:

1. **Computes** which resources are reachable from the Internet through graph traversal
2. **Stores** results in a dedicated database table for future queries and audits
3. **Integrates** with diagram generation to highlight exposed resources
4. **Generates** detailed audit reports with risk scoring and recommendations
5. **Tracks** changes across multiple scans for vulnerability trending

## Architecture

### Components

#### 1. Internet Accessibility Analyzer (`internet_accessibility_analyzer.py`)
Core graph traversal engine that:
- Identifies Internet entry points (public IPs, public endpoints)
- Uses BFS to traverse resource connections
- Computes shortest paths from Internet to each resource
- Stores results in `resource_internet_accessibility` table

**Key Algorithm:**
```
1. Find all entry points:
   - Public IP resources
   - Publicly-accessible endpoints (APIM, App Service, Function App, LB, CDN)
   - Explicitly marked resources (internet_access=true)

2. BFS traversal:
   - Start queue: all entry points
   - For each resource, find outbound connections
   - Skip administrative edge types (contains, parent_of, etc.)
   - Mark targets as accessible
   - Track path distance and entry point

3. Store accessibility info:
   - Resource ID
   - Is accessible (boolean)
   - Shortest path distance
   - Entry point used
   - Access method (direct IP, endpoint, identity)
   - Authentication level
   - Complete path nodes
```

**Usage:**
```bash
python Scripts/Analyze/internet_accessibility_analyzer.py --experiment-id <ID>
```

#### 2. Internet Accessibility UI Helper (`internet_accessibility_ui.py`)
Provides utilities for:
- Loading accessibility data from database
- Querying accessible resource lists
- Generating accessibility badges for diagrams
- Creating summary metrics
- Building visibility tables

**Key Classes:**
- `InternetAccessibilityHelper`: Query interface to loaded data
- Functions for enriching resources with accessibility info
- HTML/Markdown table generation

#### 3. Audit Script (`audit_internet_accessibility.py`)
Comprehensive audit tool that:
- Runs the analyzer
- Generates detailed reports (Markdown or JSON)
- Calculates risk scores (0-100)
- Provides recommendations by risk level
- Can be integrated into CI/CD pipelines

**Usage:**
```bash
python Scripts/Analyze/audit_internet_accessibility.py \
  --experiment-id azuregoat-scan-001 \
  --format markdown \
  --output audit_report.md
```

### Database Schema

**Table: `resource_internet_accessibility`**

```sql
CREATE TABLE resource_internet_accessibility (
  id INTEGER PRIMARY KEY,
  experiment_id TEXT NOT NULL,
  resource_id INTEGER NOT NULL,
  resource_name TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  
  -- Computed accessibility flags
  is_internet_accessible BOOLEAN DEFAULT 0,
  shortest_path_distance INTEGER,        -- Number of hops from Internet
  path_data TEXT,                        -- JSON: {entry_point, distance, path_nodes, via_*}
  
  -- Access method indicators
  via_public_ip BOOLEAN DEFAULT 0,       -- Direct public IP
  via_public_endpoint BOOLEAN DEFAULT 0, -- Public endpoint (APIM, App Service, etc.)
  via_managed_identity BOOLEAN DEFAULT 0,-- Via service principal/identity
  
  -- Detailed info
  entry_point TEXT,                      -- Resource name of Internet entry point
  auth_level TEXT,                       -- "none", "key", "identity", "certificate"
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  UNIQUE(experiment_id, resource_id)
);

-- Indexes for fast queries
CREATE INDEX idx_internet_accessibility_experiment 
  ON resource_internet_accessibility(experiment_id, is_internet_accessible);
CREATE INDEX idx_internet_accessibility_distance 
  ON resource_internet_accessibility(experiment_id, shortest_path_distance);
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Scan Complete (resources, connections stored)               │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  Analyzer                  │
        │  - Load resources & conns  │
        │  - Identify entry points   │
        │  - BFS traversal           │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │ Store in DB                │
        │ resource_internet_          │
        │ accessibility table        │
        └────────────┬───────────────┘
                     │
        ┌────────────┴─────────────────┐
        │                              │
        ▼                              ▼
    ┌────────────────┐       ┌──────────────────────┐
    │ Diagram        │       │ Audit Reports        │
    │ Generation     │       │ - Markdown           │
    │ (Enrichment)   │       │ - JSON               │
    └────────────────┘       │ - Risk scoring       │
                             │ - Recommendations    │
                             └──────────────────────┘
```

## Usage Examples

### Example 1: Run Analysis After Scan

```bash
# After scanning an experiment
cd /Repos/Triage-Saurus
python Scripts/Analyze/internet_accessibility_analyzer.py --experiment-id my-scan-1
```

### Example 2: Generate Audit Report

```bash
# Create comprehensive audit report
python Scripts/Analyze/audit_internet_accessibility.py \
  --experiment-id my-scan-1 \
  --format markdown \
  --output reports/internet_accessibility_audit.md

# JSON format for programmatic access
python Scripts/Analyze/audit_internet_accessibility.py \
  --experiment-id my-scan-1 \
  --format json \
  --output reports/internet_accessibility.json
```

### Example 3: Query Results Programmatically

```python
from Scripts.Generate.internet_accessibility_ui import InternetAccessibilityHelper

helper = InternetAccessibilityHelper("my-scan-1")
helper.load()

# Get all internet-accessible resources
accessible = helper.get_internet_accessible_resources()
for resource_id, info in accessible:
    print(f"{info['resource_name']}: {info['shortest_path_distance']} hops")

# Get risk badge for display
badge = helper.get_accessibility_badge(resource_id)
print(f"Badge: {badge}")

# Query metrics
from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics
metrics = query_accessibility_metrics("my-scan-1")
print(f"Total resources: {metrics['total_resources']}")
print(f"Internet accessible: {metrics['internet_accessible_count']}")
print(f"Percentage: {metrics['internet_accessible_percentage']:.1f}%")
```

### Example 4: Integrate with CI/CD

```yaml
# GitHub Actions example
- name: Run Internet Accessibility Audit
  run: |
    python Scripts/Analyze/audit_internet_accessibility.py \
      --experiment-id ${{ github.run_id }} \
      --format markdown \
      --output internet_audit.md
  
- name: Comment PR with Findings
  uses: actions/github-script@v6
  with:
    script: |
      const report = fs.readFileSync('internet_audit.md', 'utf8');
      github.rest.issues.createComment({
        issue_number: context.issue.number,
        owner: context.repo.owner,
        repo: context.repo.repo,
        body: report
      });
```

## Risk Scoring Formula

```
Risk Score (0-100) = 
  (Accessible% × 0.4) +           # 40% weight: coverage
  (PublicIP_count × 15) +          # 15 pts per public IP
  (PublicEndpoint_count × 5)       # 5 pts per public endpoint

Risk Levels:
  >= 80: 🔴 CRITICAL
  >= 60: 🟠 HIGH
  >= 40: 🟡 MEDIUM
  >= 20: 🟢 LOW
  <  20: ✅ MINIMAL
```

## Audit Report Output Example

```markdown
# Internet Accessibility Audit Report

**Experiment ID:** azuregoat-scan-001
**Generated:** 2024-04-14T15:30:00

## Executive Summary
- Total Resources: 25
- Internet-Accessible: 5 (20.0%)

## Access Methods
- **Via Public IP:** 1 resources 🔴 (CRITICAL)
- **Via Public Endpoint:** 2 resources 🟠 (HIGH)
- **Via Managed Identity:** 1 resources 🟡 (MEDIUM)

## Detailed Internet-Accessible Resources

### 🔴 CRITICAL - Direct Public IP Access
- **VM_PublicIP** (azurerm_public_ip)
  - Entry Point: VM_PublicIP
  - Distance: 0 hops
  - Auth Level: none
  - Path: VM_PublicIP

### 🟠 HIGH - Public Endpoint Access
- **function_app** (azurerm_function_app)
  - Entry Point: function_app
  - Distance: 0 hops
  - Auth Level: key
  - Path: function_app → storage_account

## Security Assessment
**Overall Risk Level:** 🟠 HIGH
**Risk Score:** 62.5/100

## Recommendations
1. **URGENT:** Remove or restrict 1 public IP(s)
   - Consider using private endpoints or VPN access instead
   - Implement strict NSG/Security Group rules

2. **HIGH:** Secure 2 public endpoint(s)
   - Enforce authentication on all public APIs
   - Implement rate limiting and WAF rules
   - Use TLS/HTTPS exclusively
```

## Integration Points

### 1. Diagram Generation
Enrich nodes with accessibility badges:

```python
from Scripts.Generate.internet_accessibility_ui import enrich_resource_with_accessibility

for resource in resources:
    resource = enrich_resource_with_accessibility(resource, helper)
    # Resource now has:
    # - _is_internet_accessible: bool
    # - _accessibility_badge: str
    # - _internet_exposed_color: str (for styling)
```

### 2. Web UI
Display accessibility metrics on dashboard:

```python
from Scripts.Analyze.audit_internet_accessibility import query_accessibility_metrics

metrics = query_accessibility_metrics(experiment_id)
# Returns:
# {
#   'total_resources': 25,
#   'internet_accessible_count': 5,
#   'internet_accessible_percentage': 20.0,
#   'by_access_method': {
#     'via_public_ip': 1,
#     'via_public_endpoint': 2,
#     'via_managed_identity': 1
#   },
#   'shortest_path_distance_avg': 0.4,
#   'shortest_path_distance_max': 2
# }
```

### 3. Alert System
Trigger alerts on new exposures:

```python
# Compare current scan to previous
current = query_accessibility_metrics("scan-2024-04-14")
previous = query_accessibility_metrics("scan-2024-04-07")

if current['internet_accessible_count'] > previous['internet_accessible_count']:
    # New exposures detected!
    notify_security_team()
```

## Future Enhancements

1. **Exposure Trending**: Track metrics over time
2. **Change Detection**: Alert when resources become newly exposed
3. **Blast Radius Analysis**: Show impact if entry point is compromised
4. **Remediation Tracking**: Link fixes to exposures
5. **Compliance Mapping**: Map to CIS benchmarks, NIST controls
6. **Cross-Cloud Analysis**: Azure↔AWS↔GCP comparisons
7. **Network Segmentation**: Visualize trust boundary violations

## Testing

Run the test suite:

```bash
cd Scripts/Analyze
pytest test_internet_accessibility_analyzer.py -v
```

Key test scenarios:
- Public IP detection
- Public endpoint detection
- Single-hop traversal
- Multi-hop traversal
- No accessible resources
- Multiple entry points
- Administrative edges skipped
- Auth level detection

## Troubleshooting

**Q: Analyzer reports no accessible resources but I expect some**
A: Check that:
1. Public IPs are marked with resource_type containing "public_ip"
2. App Services/Function Apps have public_access_enabled != false
3. Connections are properly stored in resource_connections table
4. Connection types aren't all administrative (contains, parent_of, etc.)

**Q: Path data is empty in results**
A: Ensure resource_properties table is populated with internet-relevant properties

**Q: Risk score seems wrong**
A: Review the formula above and check metrics output:
```python
metrics = query_accessibility_metrics(experiment_id)
print(json.dumps(metrics, indent=2))
```

## Contributing

To extend the framework:

1. Add new entry point detection in `InternetAccessibilityAnalyzer._is_public_*_resource()`
2. Add new edge types to skip in `_build_adjacency_list()`
3. Add new auth levels in `_determine_auth_level()`
4. Add new recommendations in `audit_internet_accessibility._generate_recommendations()`
