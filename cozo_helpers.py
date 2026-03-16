# Minimal cozo_helpers shim to avoid optional pycozo dependency during local generation
from pathlib import Path

# No-op provenance recorder for local runs
def _insert_relationship_audit(*args, **kwargs):
    return None

# No-op execute_sql
def _execute_sql(sql: str, params: tuple = ()):  # pragma: no cover
    return None

# Provide other no-op helpers that callers may use
def insert_resource_node(*args, **kwargs):
    return None

def insert_enrichment_node(*args, **kwargs):
    return None

def insert_relationship(*args, **kwargs):
    return None

def insert_equivalence(*args, **kwargs):
    return None

def link_enrichment(*args, **kwargs):
    return None

def insert_task_dependency(*args, **kwargs):
    return None

def delete_relationship(*args, **kwargs):
    return None

# Lightweight wrappers for client access (not implemented locally)
def _open_client(*args, **kwargs):
    raise RuntimeError("pycozo Client not available in this environment")
