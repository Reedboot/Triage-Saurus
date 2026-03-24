from __future__ import annotations

from typing import cast

from db_helpers import get_db_connection, get_repository_id


def render_service_auth_topology(experiment_id: str, repo_name: str) -> str:
    """Return a markdown description of the service authentication topology for a repo."""
    try:
        with get_db_connection() as conn:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
                (experiment_id, repo_name),
            ).fetchone()
            if not repo_row:
                return "- No services detected"

            repo_id = cast(int, repo_row[0])

            service_types = conn.execute(
                """
                SELECT resource_type, COUNT(*) as count
                FROM resources
                WHERE repo_id = ?
                GROUP BY resource_type
                ORDER BY count DESC
                """,
                (repo_id,),
            ).fetchall()

            if not service_types:
                return "- No infrastructure services detected"

            lines: list[str] = []
            lines.append("**Services Detected:**\n")

            auth_risks: dict[str, tuple[str, str, str, str]] = {
                "azurerm_servicebus_namespace": ("Service Bus", "Connection strings", "🟠 HIGH", "Namespace-level SAS tokens"),
                "azurerm_servicebus_topic": ("Service Bus Topic", "Inherited SAS", "🟠 HIGH", "No operation-level auth"),
                "azurerm_servicebus_queue": ("Service Bus Queue", "Inherited SAS", "🟠 HIGH", "All consumers share same auth"),
                "azurerm_storage_account": ("Storage Account", "SAS tokens/keys", "🟠 HIGH", "Container access control required"),
                "azurerm_cosmosdb_account": ("Cosmos DB", "Primary/secondary keys", "🔴 CRITICAL", "Master keys in code = total compromise"),
                "azurerm_sql_server": ("SQL Database", "SQL auth/AAD", "🔴 CRITICAL", "Managed identity preferred"),
                "azurerm_api_management_subscription": ("APIM Subscription", "API keys", "🟠 HIGH", "Static credentials, no expiration"),
                "azurerm_key_vault": ("Key Vault", "RBAC/MSI", "🟢 LOW", "Secure secret storage"),
                "azurerm_app_configuration": ("App Config", "Connection string", "🟡 MEDIUM", "Should use MSI"),
            }

            findings_to_create = []
            for resource_type, count in service_types:
                risk = auth_risks.get(resource_type)
                if not risk:
                    continue

                service_name, auth_method, risk_level, issue = risk
                lines.append(f"{risk_level} **{service_name}** (x{count})")
                lines.append(f"  - Auth: {auth_method}")
                lines.append(f"  - Issue: {issue}")
                lines.append("")

                # Prepare corresponding finding to ensure Risks/Findings include these issues
                findings_to_create.append({
                    'title': f"{service_name}: {issue}",
                    'severity': 'CRITICAL' if '🔴' in risk_level or 'critical' in risk_level.lower() else 'HIGH',
                    'description': f"Detected {count} {service_name}(s) using {auth_method}. Issue: {issue}",
                    'resource_type': resource_type,
                })

            # Persist suggested findings idempotently so they appear in Findings/Risks
            try:
                with db_helpers.get_db_connection() as conn:
                    findings_for_insert = []
                    for f in findings_to_create:
                        rule_id = 'service_auth_topology'
                        title = f['title']
                        # Deduplicate on experiment + rule_id + title
                        exists = conn.execute(
                            "SELECT id FROM findings WHERE experiment_id = ? AND rule_id = ? AND title = ? LIMIT 1",
                            (experiment_id, rule_id, title),
                        ).fetchone()
                        if exists:
                            continue
                        base_severity = f['severity']
                        severity_score = 10 if base_severity.upper() == 'CRITICAL' else 8 if base_severity.upper() == 'HIGH' else 5
                        findings_for_insert.append({
                            'experiment_id': experiment_id,
                            'repo_id': conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone()[0] if conn.execute("SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?", (experiment_id, repo_name)).fetchone() else None,
                            'resource_id': None,
                            'title': title,
                            'description': f.get('description'),
                            'category': 'Topology',
                            'severity_score': severity_score,
                            'base_severity': base_severity,
                            'evidence_location': f"service_auth_topology:{f['resource_type']}",
                            'source_file': None,
                            'source_line_start': None,
                            'source_line_end': None,
                            'rule_id': rule_id,
                            'proposed_fix': None,
                            'code_snippet': None,
                            'reason': None,
                        })
                    if findings_for_insert:
                        ids = db_helpers.batch_insert_findings(conn, findings_for_insert)
                        for fid, fd in zip(ids, findings_for_insert):
                            db_helpers.record_risk_score(fid, fd['severity_score'], scored_by='service_auth_topology', conn=conn)
            except Exception:
                # Best-effort persistence: don't fail topology rendering when DB ops fail
                pass

            lines.append("**Service-to-Service Auth Flows:**\n")
            # Only include generic KeyVault node if a Key Vault was actually detected
            has_kv = any(rt for rt, _ in service_types if rt.lower().find('key_vault') != -1 or rt.lower().find('keyvault') != -1)
            if has_kv:
                lines.append("```mermaid\ngraph LR\n    Client[Client] -->|Subscription key| APIM[APIM]\n    APIM -->|Connection string| ServiceBus[Service Bus]\n    ServiceBus -->|SAS policy| EventHandlers[Event Handlers]\n    EventHandlers -->|DB auth| DB[SQL/Cosmos]\n    APIM -->|Calls| DB\n    DB -->|Secrets| KeyVault[Key Vault]\n```\n")
            else:
                # Produce a simplified flow without an implied KeyVault node
                lines.append("```mermaid\ngraph LR\n    Client[Client] -->|Subscription key| APIM[APIM]\n    APIM -->|Connection string| ServiceBus[Service Bus]\n    ServiceBus -->|SAS policy| EventHandlers[Event Handlers]\n    EventHandlers -->|DB auth| DB[SQL/Cosmos]\n    APIM -->|Calls| DB\n```\n")

            return "\n".join(lines)
    except Exception as exc:  # pragma: no cover - best-effort text
        return f"- Error analyzing services: {str(exc)}"
