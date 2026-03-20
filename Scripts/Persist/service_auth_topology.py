from __future__ import annotations

from typing import cast

from db_helpers import get_db_connection


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

            for resource_type, count in service_types:
                risk = auth_risks.get(resource_type)
                if not risk:
                    continue

                service_name, auth_method, risk_level, issue = risk
                lines.append(f"- {risk_level} **{service_name}** (x{count})")
                lines.append(f"  - Auth: {auth_method}")
                lines.append(f"  - Issue: {issue}")
                lines.append("")

            lines.append("**Service-to-Service Auth Flows:**\n")
            lines.append("1. **Client → APIM** - Subscription key (⚠️ static, trackable)")
            lines.append("2. **APIM → Service Bus** - Connection string (⚠️ likely hardcoded)")
            lines.append("3. **Service Bus → Event Handlers** - SAS policy (⚠️ namespace-wide)")
            lines.append("4. **Services → SQL/Cosmos** - Keys or SQL auth (⚠️ should use MSI)")
            lines.append("5. **Apps → Key Vault** - Managed identity (✅ best practice)")

            return "\n".join(lines)
    except Exception as exc:  # pragma: no cover - best-effort text
        return f"- Error analyzing services: {str(exc)}"
