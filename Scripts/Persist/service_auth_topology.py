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
            # Aggregate by mapped service name so multiple resource types that map to the same
            # service are shown once (summing counts and combining auth/issue descriptions).
            service_summary: dict[str, dict] = {}
            for resource_type, count in service_types:
                risk = auth_risks.get(resource_type)
                if not risk:
                    continue

                service_name, auth_method, risk_level, issue = risk
                if service_name not in service_summary:
                    service_summary[service_name] = {"count": 0, "auth_methods": set(), "issues": set()}
                service_summary[service_name]["count"] += count
                service_summary[service_name]["auth_methods"].add(auth_method)
                service_summary[service_name]["issues"].add(issue)

            for service_name, data in service_summary.items():
                lines.append(f"**{service_name}** (x{data['count']})")
                lines.append(f"  - Auth: {', '.join(sorted(data['auth_methods']))}")
                lines.append(f"  - Why: {', '.join(sorted(data['issues']))}")
                lines.append("")

            # Do not persist findings in Phase 1 — return topology description only
            # (AI enrichment will create scored findings during Phase 2/Enrich).

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
