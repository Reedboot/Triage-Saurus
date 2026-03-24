#!/usr/bin/env python3
"""Merge JSON findings produced by AI/opengrep into markdown finding files for UI consumption.

Usage:
  python3 Scripts/Utils/merge_json_findings_to_md.py <json_file> [--out-dir Output/Findings/Repo]

This converts each finding entry in the JSON into a markdown file under the given out-dir using
Templates/CodeFinding.md when appropriate.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: merge_json_findings_to_md.py <json_file> [--out-dir OUT]")
    raise SystemExit(2)

json_path = Path(sys.argv[1])
out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('Output/Findings')
out_dir.mkdir(parents=True, exist_ok=True)

try:
    data = json.loads(json_path.read_text())
except Exception as e:
    print('Failed to load JSON:', e)
    raise SystemExit(1)

# Template fallback
if (Path('Templates/CodeFinding.md')).exists():
    template = Path('Templates/CodeFinding.md').read_text()
else:
    template = '# Finding: {title}\n\n### 🧾 Summary\n{description}\n'

findings = data.get('findings') or data.get('results') or []
for i, f in enumerate(findings, start=1):
    title = f.get('title') or f.get('finding_name') or f.get('rule_id') or f.get('check_id') or f.get('id') or f'finding_{i}'
    safe_title = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)[:80].strip()
    fname = out_dir / f'{safe_title}.md'
    body = template.replace('{title}', title)
    # Basic fields
    description = f.get('description') or f.get('message') or f.get('message_text') or ''
    body = body.replace('{description}', description)
    # Add metadata block
    metadata = []
    if f.get('rule_id'):
        metadata.append(f'Rule: {f.get("rule_id")}')
    if f.get('check_id'):
        metadata.append(f'Check: {f.get("check_id")}')
    if f.get('path'):
        metadata.append(f'File: {f.get("path")}')
    if metadata:
        body = f'---\n' + '\n'.join(metadata) + '\n---\n\n' + body
    try:
        fname.write_text(body)
        print('Wrote', fname)
    except Exception as e:
        print('Failed writing', fname, e)
