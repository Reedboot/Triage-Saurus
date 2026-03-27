from __future__ import annotations

from typing import cast

from db_helpers import get_db_connection, get_repository_id


def _build_namespace_topology(conn, repo_id: int, experiment_id: str) -> dict:
    """
    Return a dict mapping each SB namespace (by resource_id) to its queues, topics,
    and the AKS modules / services that reference it.

    Structure:
        {
            ns_id: {
                "name": str,
                "resource_type": str,
                "queues": [{"name": str, "id": int}, ...],
                "topics": [{"name": str, "id": int,
                             "subscriptions": [{"name": str, "id": int}]}],
                "consumers": [str],   # AKS module / kubernetes resource names
            }
        }
    """
    # Fetch all Service Bus resources in this repo
    rows = conn.execute(
        """
        SELECT id, resource_name, resource_type, parent_resource_id
        FROM resources
        WHERE repo_id = ?
          AND resource_type LIKE '%servicebus%'
        """,
        (repo_id,),
    ).fetchall()

    namespaces: dict[int, dict] = {}
    queues: list[tuple[int, str, int | None]] = []
    topics: dict[int, dict] = {}
    subscriptions: list[tuple[int, str, int | None]] = []

    for r_id, r_name, r_type, parent_id in rows:
        if "namespace" in r_type:
            namespaces[r_id] = {
                "name": r_name,
                "resource_type": r_type,
                "queues": [],
                "topics": [],
                "consumers": [],
            }
        elif "queue" in r_type:
            queues.append((r_id, r_name, parent_id))
        elif "topic" in r_type and "subscription" not in r_type:
            topics[r_id] = {"name": r_name, "id": r_id, "parent_id": parent_id, "subscriptions": []}
        elif "subscription" in r_type:
            subscriptions.append((r_id, r_name, parent_id))

    # Wire subscriptions → topics
    for s_id, s_name, s_parent in subscriptions:
        if s_parent in topics:
            topics[s_parent]["subscriptions"].append({"name": s_name, "id": s_id})

    # Wire topics → namespaces
    for t_id, t_info in topics.items():
        ns_id = t_info.get("parent_id")
        if ns_id in namespaces:
            namespaces[ns_id]["topics"].append(t_info)

    # Wire queues → namespaces
    for q_id, q_name, q_parent in queues:
        if q_parent in namespaces:
            namespaces[q_parent]["queues"].append({"name": q_name, "id": q_id})

    # Find AKS modules / k8s resources that reference each namespace
    # via resource_connections or resource_properties (ServiceBusFullyQualifiedNamespace)
    for ns_id, ns_info in namespaces.items():
        ns_name = ns_info["name"]
        # Look for resources whose properties reference this namespace name
        consumer_rows = conn.execute(
            """
            SELECT DISTINCT re.resource_name
            FROM resource_properties rp
            JOIN resources re ON rp.resource_id = re.id
            WHERE re.repo_id = ?
              AND rp.property_key LIKE '%ServiceBus%'
              AND (rp.property_value LIKE ? OR rp.property_value LIKE ?)
            """,
            (repo_id, f"%{ns_name}%", f"%servicebus%"),
        ).fetchall()
        for (consumer_name,) in consumer_rows:
            if consumer_name not in ns_info["consumers"]:
                ns_info["consumers"].append(consumer_name)

    return namespaces


def _build_namespace_mermaid(namespaces: dict) -> str:
    """
    Build a data-driven Mermaid LR graph showing:
      - Each SB namespace as its own subgraph
      - Queues/topics (and topic subscriptions) nested inside
      - AKS consumer edges into the namespace
    """
    if not namespaces:
        return ""

    lines = ["```mermaid", "graph LR"]

    def _safe_id(prefix: str, rid: int) -> str:
        return f"{prefix}_{rid}"

    for ns_id, ns in namespaces.items():
        ns_node = _safe_id("ns", ns_id)
        # Subgraph per namespace
        ns_label = ns["name"].replace('"', "'")
        is_external = any(token in ns["resource_type"] for token in ("data",)) or ns_label.startswith("var.")
        ns_icon = "📨🔗" if is_external else "📨"
        lines.append(f'  subgraph {ns_node}["{ns_icon} {ns_label}"]')

        for queue in ns.get("queues", []):
            q_node = _safe_id("q", queue["id"])
            lines.append(f'    {q_node}["📤 {queue["name"]}"]')

        for topic in ns.get("topics", []):
            t_node = _safe_id("t", topic["id"])
            lines.append(f'    {t_node}["📬 {topic["name"]}"]')
            for sub in topic.get("subscriptions", []):
                s_node = _safe_id("s", sub["id"])
                lines.append(f'    {s_node}["🔔 {sub["name"]}"]')
                lines.append(f'    {t_node} -->|delivers| {s_node}')

        lines.append("  end")

        # Consumer edges (AKS modules / services)
        for consumer in ns.get("consumers", []):
            consumer_node = f'consumer_{abs(hash(consumer)) % 10000}'
            lines.append(f'  {consumer_node}["{consumer}"] -->|connects to| {ns_node}')

    lines.append("```")
    return "\n".join(lines)


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

            lines.append("**Service-to-Service Auth Flows:**\n")

            # Build data-driven topology from the DB
            namespaces = _build_namespace_topology(conn, repo_id, experiment_id)
            mermaid = _build_namespace_mermaid(namespaces)
            if mermaid:
                lines.append(mermaid)
            else:
                # Fallback: simplified static diagram when no SB resources are present
                has_kv = any(rt for rt, _ in service_types if "key_vault" in rt)
                if has_kv:
                    lines.append("```mermaid\ngraph LR\n    Client[Client] -->|Subscription key| APIM[APIM]\n    APIM -->|Connection string| ServiceBus[Service Bus]\n    ServiceBus -->|SAS policy| EventHandlers[Event Handlers]\n    EventHandlers -->|DB auth| DB[SQL/Cosmos]\n    DB -->|Secrets| KeyVault[Key Vault]\n```\n")
                else:
                    lines.append("```mermaid\ngraph LR\n    Client[Client] -->|Subscription key| APIM[APIM]\n    APIM -->|Connection string| ServiceBus[Service Bus]\n    ServiceBus -->|SAS policy| EventHandlers[Event Handlers]\n    EventHandlers -->|DB auth| DB[SQL/Cosmos]\n```\n")

            lines.append("\n> 🔗 **External (data source) namespaces** are pre-existing shared resources — queues/topics created within them are app-specific.")

            return "\n".join(lines)
    except Exception as exc:  # pragma: no cover - best-effort text
        return f"- Error analyzing services: {str(exc)}"
