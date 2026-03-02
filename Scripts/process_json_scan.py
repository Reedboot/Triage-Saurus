import json
import os
import subprocess
import datetime
import glob
from pathlib import Path

with open('Output/Audit/terragoat_scan.json') as f:
    data = json.load(f)

# Group findings by check_id
grouped = {}
for r in data.get('results', []):
    check_id = r['check_id']
    sev = r['extra'].get('severity', 'INFO')
    if 'Context' in check_id or sev == 'INFO':
        continue
    
    if check_id not in grouped:
        grouped[check_id] = []
    grouped[check_id].append(r)

# For each check_id, create a finding model
os.makedirs('Output/Audit/RenderInputs/Code', exist_ok=True)
count = 0
for check_id, results in grouped.items():
    rule_name = check_id.split('.')[-1]
    title = rule_name.replace('-', ' ').title()
    
    sev = results[0]['extra']['severity']
    if sev == 'ERROR':
        score = 8
        sev_label = 'High'
    elif sev == 'WARNING':
        score = 5
        sev_label = 'Medium'
    else:
        continue # skip INFO
    
    msg = results[0]['extra']['message']
    
    # gather evidence
    evidence = []
    for r in results[:5]: # limit to 5
        evidence.append(f"File: `{r['path']}:{r['start']['line']}`")
    if len(results) > 5:
        evidence.append(f"... and {len(results)-5} more occurrences")

    model = {
      "version": 1,
      "kind": "code",
      "title": title,
      "description": msg.split('\\n')[0],
      "overall_score": { "severity": sev_label, "score": score },
      "architecture_mermaid": "flowchart TB\\n  Internet[Internet] --> Svc[Affected Service]\\n  style Svc stroke:#ff0000,stroke-width:4px",
      "security_review": {
        "summary": msg,
        "applicability": { "status": "Yes", "evidence": "Detected via opengrep." },
        "key_evidence": evidence,
        "assumptions": ["Assume this code is deployed to production."],
        "exploitability": "Depends on exposure.",
        "recommendations": [{"text": "Review and fix the misconfiguration.", "score_from": score, "score_to": max(1, score-4)}],
        "countermeasures": ["🟢 Enforce policy-as-code"],
        "rationale": "Following best practices reduces attack surface."
      },
      "meta": {
        "category": "Security Misconfiguration",
        "languages": "IaC",
        "source": f"opengrep scan ({rule_name})",
        "last_updated": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
      }
    }
    
    safe_title = title.replace(' ', '_')
    out_json = f"Output/Audit/RenderInputs/Code/{safe_title}_terragoat.json"
    with open(out_json, 'w') as f:
        json.dump(model, f, indent=2)
    count += 1
    
print(f"Generated {count} json inputs")
