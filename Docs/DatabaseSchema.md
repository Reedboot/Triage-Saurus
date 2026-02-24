# Database Schema Documentation

**Location:** `Output/Learning/triage.db`  
**Type:** SQLite 3  
**Purpose:** Single source of truth for experiments, resources, connections, findings, and learning data.

## Quick Start

```bash
# Initialize database (creates all tables)
python3 Scripts/init_database.py

# Generate diagrams from database
python3 Scripts/generate_diagram.py 008 --type architecture
python3 Scripts/generate_diagram.py 008 --type security --min-score 7
python3 Scripts/generate_diagram.py 008 --type blast_radius --from-resource "rocinante"

# Query database
python3 -c "
import sqlite3
conn = sqlite3.connect('Output/Learning/triage.db')
cursor = conn.cursor()
cursor.execute('SELECT resource_name, resource_type FROM resources')
print(cursor.fetchall())
"
```

## Architecture Overview

The database serves as:
- **Single source of truth** for all resources, connections, and findings
- **Cross-experiment tracking** for comparison and learning
- **Query foundation** for diagram generation, risk analysis, and reporting
- **Persistent storage** across sessions and experiments

## Core Tables

### experiments
Tracks each experiment run with its configuration, strategy, and results.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Experiment ID (e.g., "008") |
| name | TEXT | Human-readable name |
| parent_experiment_id | TEXT | Parent experiment for comparison |
| agent_versions | TEXT | Agent instruction versions used |
| strategy_version | TEXT | Strategy version |
| changes_description | TEXT | What changed from parent |
| repos | TEXT | Repositories scanned |
| status | TEXT | running/completed/failed |
| findings_count | INT | Total findings discovered |
| high_value_count | INT | Findings with score >= 7 |
| avg_score | REAL | Average finding score |
| false_positives | INT | Incorrect findings |
| accuracy_rate | REAL | (TP / (TP + FP)) |

**Purpose:** Track what was changed between experiments and measure impact on results.

### repositories
Tracks repositories scanned in each experiment.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment ID |
| experiment_id | TEXT FK | Links to experiments.id |
| repo_name | TEXT | Folder name only (e.g., "ExpanseAzureLab") |
| git_remote_url | TEXT | Full git URL if available |
| repo_type | TEXT | Infrastructure/Application/Library |
| files_scanned | INT | Total files processed |
| iac_files_count | INT | Terraform/CloudFormation/ARM files |
| code_files_count | INT | Source code files |

**Purpose:** Portable repository identification (no hardcoded paths).

### resources
All discovered resources (VMs, databases, storage, etc.) with their location.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment ID |
| experiment_id | TEXT FK | Links to experiments.id |
| repo_id | INTEGER FK | Links to repositories.id |
| resource_name | TEXT | Resource identifier |
| resource_type | TEXT | VM/AKS/SQL/Storage/KeyVault/etc. |
| provider | TEXT | Azure/AWS/GCP |
| region | TEXT | Deployment region |
| source_file | TEXT | Relative path (e.g., "tfscripts/main.tf") |
| source_line_start | INT | Starting line number |
| source_line_end | INT | Ending line number |
| status | TEXT | active/deleted/unknown |
| first_seen | TIMESTAMP | When first discovered |
| last_seen | TIMESTAMP | Last experiment where detected |

**Key Design:**
- **Generic resource_type** supports any cloud resource (no hardcoded columns)
- **Split line numbers** track code location drift between experiments
- **Portable paths** use repo-relative paths, no `/mnt/c/Repos/` prefixes

### resource_properties
Key-value properties for resources (EAV pattern for flexibility).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment ID |
| resource_id | INTEGER FK | Links to resources.id |
| property_key | TEXT | Property name (e.g., "has_public_ip") |
| property_value | TEXT | Value as string |
| property_type | TEXT | security/network/identity/compute/storage |
| is_security_relevant | BOOLEAN | True for security-related properties |

**Purpose:** Flexible schema supports any resource type without ALTER TABLE.

### resource_connections
Connections between resources (network, data flow, dependencies).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment ID |
| experiment_id | TEXT FK | Links to experiments.id |
| source_resource_id | INTEGER FK | Source resource |
| target_resource_id | INTEGER FK | Target resource |
| connection_type | TEXT | accesses/queries/deploys/manages |
| protocol | TEXT | HTTPS/TDS/SSH/RDP/etc. |
| port | TEXT | Port number if known |
| authentication | TEXT | Managed Identity/SQL Auth/Key/etc. |
| is_cross_repo | BOOLEAN | True if source/target in different repos |

**Purpose:** Enables blast radius queries and dependency analysis.

### findings
Security findings linked to resources and experiments.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment ID |
| experiment_id | TEXT FK | Links to experiments.id |
| repo_id | INTEGER FK | Links to repositories.id |
| resource_id | INTEGER FK | Links to resources.id |
| title | TEXT | Finding title |
| description | TEXT | Full description |
| category | TEXT | Cryptography/Access/Network/etc. |
| severity_score | INT | Numeric score (1-10) |
| base_severity | TEXT | Critical/High/Medium/Low |
| overall_score | TEXT | Combined score string |
| evidence_location | TEXT | Where issue was found |
| source_file | TEXT | File containing issue |
| finding_path | TEXT | Path to finding markdown file |
| status | TEXT | open/fixed/accepted/false_positive |

**Purpose:** Link security issues to specific resources for risk register.

## Supporting Tables

### resource_context
Business context for resources (criticality, data classification, environment).

| Column | Type | Description |
|--------|------|-------------|
| resource_id | INTEGER FK | Links to resources.id |
| business_criticality | TEXT | Critical/High/Medium/Low |
| data_classification | TEXT | Public/Internal/Confidential/Restricted |
| environment | TEXT | Production/Staging/Development |
| owner_team | TEXT | Responsible team |

### countermeasures
Controls that reduce risk (for adjusted scoring).

| Column | Type | Description |
|--------|------|-------------|
| resource_id | INTEGER FK | Links to resources.id |
| control_type | TEXT | Preventive/Detective/Corrective |
| control_name | TEXT | Control name |
| effectiveness | REAL | 0.0 to 1.0 (1.0 = fully mitigates) |

### context_questions & context_answers
Track which questions improve accuracy.

| Column | Type | Description |
|--------|------|-------------|
| question_text | TEXT | Question asked |
| question_category | TEXT | environment/architecture/security |
| times_asked | INT | How often asked |
| times_helpful | INT | How often answer changed outcome |
| effectiveness_rate | REAL | helpful / asked |

### knowledge_facts
Confirmed facts about the environment.

| Column | Type | Description |
|--------|------|-------------|
| fact_text | TEXT | The fact statement |
| category | TEXT | network/identity/data/etc. |
| confidence | REAL | 0.0 to 1.0 |
| confirmed_experiments | TEXT | Comma-separated experiment IDs |

## Querying Patterns

### Find all resources in an experiment
```sql
SELECT r.resource_name, r.resource_type, repo.repo_name
FROM resources r
JOIN repositories repo ON r.repo_id = repo.id
WHERE r.experiment_id = '008';
```

### Find vulnerable resources
```sql
SELECT DISTINCT r.resource_name, f.severity_score, f.title
FROM resources r
JOIN findings f ON f.resource_id = r.id
WHERE f.severity_score >= 7
ORDER BY f.severity_score DESC;
```

### Find blast radius from compromised resource
```sql
WITH RECURSIVE blast_radius AS (
  SELECT target_resource_id, 1 AS depth
  FROM resource_connections
  WHERE source_resource_id = (SELECT id FROM resources WHERE resource_name = 'rocinante')
  
  UNION
  
  SELECT rc.target_resource_id, br.depth + 1
  FROM resource_connections rc
  JOIN blast_radius br ON rc.source_resource_id = br.target_resource_id
  WHERE br.depth < 5
)
SELECT DISTINCT r.resource_name, r.resource_type, br.depth
FROM blast_radius br
JOIN resources r ON br.target_resource_id = r.id;
```

### Compare experiments
```sql
-- Resources added between experiments
SELECT resource_name, resource_type
FROM resources
WHERE experiment_id = '009'
  AND resource_name NOT IN (SELECT resource_name FROM resources WHERE experiment_id = '008');
```

## Database as Source of Truth

**Current State:**
- ✅ Resources inserted during discovery (`Scripts/discover_repo_context.py`)
- ✅ Properties stored with security relevance tagging
- ✅ Connections tracked (currently manual PaaS detection)
- ✅ Diagrams generated from database (`Scripts/generate_diagram.py`)
- ✅ Risk register queries database first, falls back to markdown

**Future Enhancements:**
- Parse findings markdown and populate findings table
- Store architecture diagrams as generated_diagrams records
- Track question effectiveness (which questions improve accuracy)
- Auto-detect connections from code analysis (not just Terraform)
- Store experiment learnings for continuous improvement

## Maintenance

### Reset database
```bash
rm Output/Learning/triage.db
python3 Scripts/init_database.py
```

### Backup database
```bash
cp Output/Learning/triage.db Output/Learning/triage_backup_$(date +%Y%m%d).db
```

### Upgrade schema
The init script automatically adds missing columns to existing tables for backward compatibility.

```bash
# Safe to run on existing database
python3 Scripts/init_database.py
```
