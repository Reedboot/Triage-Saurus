#!/usr/bin/env python3
"""
Scan a repository for connection-string patterns and infer service types and origins.
Writes redacted evidence and inferred service to the experiment Knowledge/Connections.md

Usage:
  python3 Scripts/extract_service_connections.py --repo /path/to/repo --experiment Output/Learning/experiments/Quick-Run-1

Safety: values that look like secrets are redacted; only metadata and inferred service types are stored.
"""

import re
import os
import argparse
from pathlib import Path

EXCLUDE_DIRS = {'README.md', 'Images', 'attack', 'attacks', '.git', 'Output'}

PATTERNS = [
    ('azure_storage_conn', re.compile(r'(DefaultEndpointsProtocol=.*AccountName=[^;]+;AccountKey=[^;]+(?:;.*)?)', re.IGNORECASE)),
    ('azure_storage_conn_short', re.compile(r'AccountName=[^;]+;AccountKey=[^;]+', re.IGNORECASE)),
    ('azure_storage_url', re.compile(r'https?://[A-Za-z0-9\-]+\.blob\.core\.windows\.net', re.IGNORECASE)),
    ('mssql_conn', re.compile(r'(Server=tcp:[^;]+;.*Initial Catalog=[^;]+;.*Password=[^;]+)', re.IGNORECASE)),
    ('mssql_conn_alt', re.compile(r'(Server=.*;Database=.*;User Id=.*;Password=.*)', re.IGNORECASE)),
    ('connection_string_assign', re.compile(r'connection_string\s*=\s*(.+)', re.IGNORECASE)),
    ('primary_connection_string_ref', re.compile(r'([A-Za-z0-9_]+\.[A-Za-z0-9_]+\.primary_connection_string)', re.IGNORECASE)),
    ('azurerm_storage_account_ref', re.compile(r'azurerm_storage_account\.([A-Za-z0-9_\-]+)', re.IGNORECASE)),
]

REDACT_PATTERN = re.compile(r'(AccountKey=|AccountKey\s*:\s*|Password=|Pwd=|SharedAccessSignature=)([^;\n]+)', re.IGNORECASE)


def is_excluded(path: Path):
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def redact(s: str) -> str:
    # redact secret-like fragments
    return REDACT_PATTERN.sub(lambda m: m.group(1) + '[REDACTED]', s)


def infer_service_from_match(key: str, match: str):
    key = key.lower()
    if 'azure_storage' in key or 'blob.core.windows.net' in match.lower() or 'accountkey' in match.lower():
        return 'Azure Storage'
    if 'mssql' in key or 'server=tcp' in match.lower() or 'initial catalog' in match.lower() or 'database=' in match.lower():
        return 'MSSQL (Azure SQL / SQL Server)'
    if 'primary_connection_string' in match.lower() or 'connection_string' in match.lower():
        # ambiguous; return generic datastore
        return 'Connection String (unknown type)'
    # fallback
    return 'Unknown/Needs Review'


def scan_repo(repo_root: Path):
    results = []
    for root, dirs, files in os.walk(repo_root):
        # prune excluded dirs
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in files:
            fp = Path(root) / fname
            if is_excluded(fp):
                continue
            try:
                text = fp.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            for i, (key, pattern) in enumerate(PATTERNS, start=1):
                for m in pattern.finditer(text):
                    snippet = m.group(0)
                    redacted = redact(snippet)
                    service = infer_service_from_match(key, snippet)
                    # attempt to capture resource name for references
                    resource = None
                    rm = re.search(r'azurerm_storage_account\.([A-Za-z0-9_\-]+)', snippet, re.IGNORECASE)
                    if rm:
                        resource = rm.group(1)
                    results.append({
                        'file': str(fp.relative_to(repo_root)),
                        'line_snippet': redacted.strip(),
                        'service': service,
                        'pattern_key': key,
                        'resource': resource,
                    })
    return results


def write_knowledge(results, experiment_dir: Path):
    kdir = experiment_dir / 'Knowledge'
    kdir.mkdir(parents=True, exist_ok=True)
    out = kdir / 'Connections.md'
    with out.open('w', encoding='utf-8') as f:
        f.write('# Detected Connection Evidence\n\n')
        f.write('**Note:** Values that look like secrets are redacted. This file records inferred service types and evidence locations only.\n\n')
        for r in results:
            f.write('## ' + (r.get('resource') or r['service']) + '\n')
            f.write(f'- File: `{r["file"]}`\n')
            f.write(f'- Inferred service: **{r["service"]}**\n')
            if r.get('resource'):
                f.write(f'- Resource reference: `{r.get("resource")}`\n')
            f.write(f'- Evidence snippet (redacted): `{r["line_snippet"]}`\n\n')
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default=os.getcwd())
    parser.add_argument('--experiment', default=None)
    args = parser.parse_args()
    repo = Path(args.repo).expanduser().resolve()
    exp = Path(args.experiment) if args.experiment else None
    if exp:
        exp = (Path.cwd() / exp).resolve()
    else:
        # default experiment path
        exp = Path.cwd() / 'Output' / 'Learning' / 'experiments' / 'Quick-Run-1'
    results = scan_repo(repo)
    if not results:
        print('No connection evidence found in', repo)
        return
    out = write_knowledge(results, exp)
    print('Wrote', out)

if __name__ == '__main__':
    main()
