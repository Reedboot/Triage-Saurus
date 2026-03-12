# Cozo Provenance

Cozo now records provenance for relationship and node changes so you can trace who/what caused a link to be created, updated, or removed.

Schema
- relationship_audit (append-only):
  - id, from_node, to_node, rel_type, action (created|updated|deleted), actor_type (scanner|user|ai), actor_id (rule_id|username|model:prompt_hash), scan_id, evidence_finding_id, confidence, details_json, created_at
- edges: lightweight `source_scan_id` column added for fast lookup

How it works
- When enrichment or persist code creates an edge, it calls cozo_helpers.insert_relationship(..., actor_type, actor_id, scan_id)
  - This inserts the edge and writes a relationship_audit row (non-fatal on failure)
- Deletions should call cozo_helpers.delete_relationship(...) to remove the edge and record the deletion reason.

Query examples
- Find when a relationship was created for a node pair:
  ```sql
  SELECT * FROM relationship_audit WHERE from_node = 'azurerm_storage_account.myacc' AND to_node = 'aws_s3_bucket.other' ORDER BY created_at DESC;
  ```

- Find all changes from a particular scan_id:
  ```sql
  SELECT * FROM relationship_audit WHERE scan_id = 'kai_20260312T120000Z' ORDER BY created_at ASC;
  ```

- Find deletions in the last 7 days:
  ```sql
  SELECT * FROM relationship_audit WHERE action = 'deleted' AND created_at >= datetime('now', '-7 days') ORDER BY created_at DESC;
  ```

Recommendations
- Instrument callers that create/modify the graph to pass actor_type/actor_id/scan_id. This is implemented for discover_repo_context.py and persist_graph.persist_context; consider adding to LLM enrichment flows and UI actions.
- Don't rely on deleted edges for reconciliation; relationship_audit is the source-of-truth for why changes happened.

