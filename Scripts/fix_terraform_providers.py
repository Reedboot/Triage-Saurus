#!/usr/bin/env python3
"""Fix provider for terraform meta-resources in the database."""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "Output/Data/cozo.db"

def fix_terraform_meta_providers():
    """Update provider for terraform meta-resources from 'unknown' to 'terraform'."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    meta_types = [
        'terraform_data',
        'null_resource',
        'random_id',
        'random_string',
        'random_password',
        'time_sleep',
        'time_rotating',
    ]
    
    for rtype in meta_types:
        cursor.execute(
            "UPDATE resources SET provider = 'terraform' WHERE resource_type = ? AND (provider = 'unknown' OR provider IS NULL)",
            (rtype,)
        )
        print(f"Updated {cursor.rowcount} {rtype} resources")
    
    conn.commit()
    conn.close()
    print("✓ Fixed terraform meta-resource providers")

if __name__ == "__main__":
    fix_terraform_meta_providers()
