#!/usr/bin/env python3
"""Update resource_types table categories from resource_type_db.py fallback data."""

import sqlite3
from pathlib import Path

# Import the fallback data
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from Persist.resource_type_db import _FALLBACK, DB_PATH

def update_categories():
    """Update categories in the database from the _FALLBACK dict."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    updated = 0
    for terraform_type, info in _FALLBACK.items():
        category = info.get('category')
        if category:
            cursor.execute(
                """
                UPDATE resource_types 
                SET category = ? 
                WHERE terraform_type = ?
                """,
                (category, terraform_type)
            )
            if cursor.rowcount > 0:
                updated += cursor.rowcount
    
    conn.commit()
    conn.close()
    print(f"✓ Updated {updated} resource type categories in the database")

if __name__ == "__main__":
    update_categories()
