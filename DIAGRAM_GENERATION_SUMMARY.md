# Mermaid Diagram Generation Summary

## Overview
Generated mermaid architecture diagrams for all saved scans in the Triage-Saurus database.

## Execution
- **Script used:** `Scripts/Generate/generate_diagram.py`
- **Method:** Split-by-provider mode to generate separate diagrams per cloud provider/platform
- **Output directory:** `Output/Diagrams/{experiment_id}/`
- **Database:** All diagrams registered in `Output/triage.db` → `cloud_diagrams` table

## Results

### Diagrams Generated (14 total)

| Experiment | Provider | Diagram File | Resources | Lines |
|---|---|---|---|---|
| 001 | Azure | Architecture_Azure.md | 34 | 57 |
| 001 | Terraform | Architecture_Terraform.md | - | 1 |
| 002 | AWS | Architecture_Aws.md | 285 | 354 |
| 002 | Terraform | Architecture_Terraform.md | - | 1 |
| 003 | AWS | Architecture_Aws.md | 285 | 354 |
| 003 | Terraform | Architecture_Terraform.md | - | 1 |
| 005 | GCP | Architecture_Gcp.md | 53 | 76 |
| 005 | Terraform | Architecture_Terraform.md | - | 1 |
| 006 | GCP | Architecture_Gcp.md | 53 | 76 |
| 006 | Terraform | Architecture_Terraform.md | - | 1 |
| 007 | AWS | Architecture_Aws.md | 19 | 21 |
| 007 | Azure | Architecture_Azure.md | - | 9 |
| 007 | Kubernetes | Architecture_Kubernetes.md | - | 13 |
| 008 | Kubernetes | Architecture_Kubernetes.md | 1 | 11 |

### Database Registration

All 14 diagrams have been registered in the `cloud_diagrams` table:

```sql
SELECT experiment_id, provider, COUNT(*) as diagrams 
FROM cloud_diagrams 
GROUP BY experiment_id, provider 
ORDER BY experiment_id, provider;
```

Results:
- **001:** Azure (1), Terraform (1)
- **002:** AWS (1), Terraform (1)
- **003:** AWS (1), Terraform (1)
- **005:** GCP (1), Terraform (1)
- **006:** GCP (1), Terraform (1)
- **007:** AWS (1), Azure (1), Kubernetes (1)
- **008:** Kubernetes (1)

**Total:** 14 diagrams in database

## Files Location
```
Output/Diagrams/
├── 001/
│   ├── Architecture_Azure.md
│   └── Architecture_Terraform.md
├── 002/
│   ├── Architecture_Aws.md
│   └── Architecture_Terraform.md
├── 003/
│   ├── Architecture_Aws.md
│   └── Architecture_Terraform.md
├── 005/
│   ├── Architecture_Gcp.md
│   └── Architecture_Terraform.md
├── 006/
│   ├── Architecture_Gcp.md
│   └── Architecture_Terraform.md
├── 007/
│   ├── Architecture_Aws.md
│   ├── Architecture_Azure.md
│   └── Architecture_Kubernetes.md
└── 008/
    └── Architecture_Kubernetes.md
```

## Notes

- **Terraform diagrams** (marked with 1 line) indicate no resources were found for Terraform provider in those experiments
- **Azure and AWS diagrams** contain the most detail (20-57 lines) due to higher resource counts
- **Kubernetes diagrams** are smaller (9-13 lines) as they represent simpler cluster configurations
- All diagrams use **Mermaid flowchart syntax** with proper styling and visual hierarchy
- Diagrams include resource relationships, data flows, and architectural zones
- Each diagram is color-coded by resource category (Compute, Database, Storage, Network, Security, etc.)

## Next Steps

To use these diagrams:
1. View individual files in `Output/Diagrams/{exp_id}/`
2. Query the database directly: `sqlite3 Output/triage.db "SELECT * FROM cloud_diagrams WHERE experiment_id = '001'"`
3. Render as Mermaid: copy the `mermaid_code` column content into any Mermaid viewer
4. Regenerate anytime with: `python3 Scripts/Generate/generate_diagram.py <exp_id> --split-by-provider --output Output/Diagrams/<exp_id>`
