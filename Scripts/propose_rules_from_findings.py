#!/usr/bin/env python3
"""
Propose detection rules from findings that lack matching Rules/

Usage:
  python3 Scripts/propose_rules_from_findings.py [--experiment-dir <path>] [--audit-file <path>]

This script:
 - Scans Output/Findings/** for .md finding files
 - Checks Rules/Summary.md and Rules/**/*.yml for existing rule names
 - If a finding appears rule-detectable but no rule exists, writes a draft rule YAML
   into Output/Learning/proposed_rules/ (or under the provided experiment dir)
 - Appends a short audit note to the latest Output/Audit/Session_*.md

This is intentionally lightweight - drafts must be reviewed by a human before merging.
"""

import os
import re
import glob
import argparse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROPOSE_DIR = os.path.join(ROOT, 'Output', 'Learning', 'proposed_rules')
RULES_SUMMARY = os.path.join(ROOT, 'Rules', 'Summary.md')
RULES_GLOB = os.path.join(ROOT, 'Rules', '**', '*.yml')
FINDINGS_GLOB = os.path.join(ROOT, 'Output', 'Findings', '**', '*.md')
AUDIT_GLOB = os.path.join(ROOT, 'Output', 'Audit', 'Session_*.md')

COMMON_PATTERNS = [
    r'azurerm_storage_account',
    r'azurerm_mssql_firewall_rule',
    r'network_security_group',
    r'\bnsg\b',
    r'private[_-]?endpoint',
    r'connection_string',
    r'azuread_service_principal_password',
    r'azuread_application_password',
    r'nonsensitive\(',
    r'AKIA[0-9A-Z]{16}',
    r'password',
]


def load_existing_rule_names():
    names = set()
    # From Summary.md, extract *.yml tokens
    if os.path.exists(RULES_SUMMARY):
        with open(RULES_SUMMARY, 'r', encoding='utf-8') as f:
            text = f.read()
        for m in re.findall(r"[\w\-]+\.yml", text):
            names.add(m.lower())
    # Also scan Rules/ dir for files
    for p in glob.glob(RULES_GLOB, recursive=True):
        names.add(os.path.basename(p).lower())
    return names


def find_finding_files():
    return sorted(glob.glob(FINDINGS_GLOB, recursive=True))


def choose_detection_pattern(content):
    for p in COMMON_PATTERNS:
        if re.search(p, content, re.IGNORECASE):
            return p
    # fallback: longest camel/underscore token >6 chars
    toks = re.findall(r"\b[A-Za-z0-9_]{6,}\b", content)
    toks = sorted(set(toks), key=lambda s: -len(s))
    return toks[0] if toks else 'TODO_PATTERN'


def latest_audit_file():
    files = glob.glob(AUDIT_GLOB)
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def append_audit(audit_path, lines):
    if not audit_path:
        return
    try:
        with open(audit_path, 'a', encoding='utf-8') as f:
            f.write('\n')
            f.write('## Proposed Rules\n')
            for l in lines:
                f.write('- ' + l + '\n')
    except Exception as e:
        print('Failed to update audit file:', e)


def make_proposed_rule(propose_dir, finding_path, detection, idx):
    os.makedirs(propose_dir, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    base = os.path.splitext(os.path.basename(finding_path))[0]
    fname = f'draft_{ts}_{idx}_{base}.yml'
    path = os.path.join(propose_dir, fname)
    title = f'Auto-draft: detect {detection} (from {base})'
    content = f"""# Auto-generated draft rule
id: draft-{ts}-{idx}
title: "{title}"
description: |
  This draft rule was automatically generated from finding: {finding_path}
  Review and refine the detection pattern and severity before merging into Rules/.
technology: IaC
five_pillars: [Network, Access, Audit, Data]
severity: WARNING

# Detection: a simple grep-style pattern (human review recommended)
detection:
  pattern: "{detection}"
  type: grep

# Example test referencing the original finding file
test:
  - path: "{finding_path}"
    expect: match
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--propose-dir', default=DEFAULT_PROPOSE_DIR)
    parser.add_argument('--audit-file', default=None)
    args = parser.parse_args()

    existing = load_existing_rule_names()
    findings = find_finding_files()
    if not findings:
        print('No finding files found under Output/Findings/. Nothing to do.')
        return

    created = []
    idx = 1
    for fpath in findings:
        try:
            with open(fpath, 'r', encoding='utf-8') as fh:
                txt = fh.read()
        except Exception as e:
            print('Skipping', fpath, 'read error', e)
            continue
        lower = txt.lower()
        matched = False
        for rn in existing:
            key = rn.replace('.yml','')
            if key and key in lower:
                matched = True
                break
        if matched:
            continue
        # propose rule
        detection = choose_detection_pattern(txt)
        p = make_proposed_rule(args.propose_dir, fpath, detection, idx)
        created.append((fpath, p, detection))
        idx += 1

    if created:
        print('Created', len(created), 'proposed rules under', args.propose_dir)
        for orig, newf, det in created:
            print('-', orig, '->', newf, '(pattern:', det, ')')
        audit = args.audit_file or latest_audit_file()
        lines = [f"Proposed rule {os.path.basename(n)} for finding {orig} (pattern: {det})" for orig, n, det in created]
        append_audit(audit, lines)
        print('Appended summary to audit file:', audit)
    else:
        print('No unmatched findings found; no rules proposed.')


if __name__ == '__main__':
    main()
