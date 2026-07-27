from __future__ import annotations

import sys
import sqlite3
from urllib.parse import urlencode
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def precompute_subscription_diagram(db_path: Path | str, sub_id: str) -> None:
    """Persist the Mermaid subscription payload without requiring a web server."""
    from web.app import api_subscription_diagram, app
    from web.core.db import configure_db_path

    configure_db_path(Path(db_path))
    with app.test_request_context(f"/api/subscriptions/{sub_id}/diagram"):
        response = api_subscription_diagram(sub_id)
    if response.status_code != 200:
        try:
            detail = response.get_json()
        except Exception:
            detail = response.get_data(as_text=True)
        raise RuntimeError(
            f"diagram endpoint returned HTTP {response.status_code}: {detail}"
        )
    precompute_subscription_traces(db_path, sub_id)


def precompute_subscription_traces(db_path: Path | str, sub_id: str) -> None:
    """Warm route and component trace caches for known harvested assets."""
    from web.app import api_cloud_component_trace, api_cloud_route_trace, app
    from web.core.db import configure_db_path

    db_path = Path(db_path)
    configure_db_path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        endpoints: set[str] = set()
        for query in (
            "SELECT hostname FROM appgw_routing_rules WHERE subscription_id = ?",
            "SELECT host FROM aks_routes WHERE subscription_id = ?",
            "SELECT url FROM apim_backends WHERE subscription_id = ?",
            "SELECT fqdn FROM provisioned_assets WHERE subscription_id = ?",
        ):
            try:
                endpoints.update(
                    str(row[0]).strip()
                    for row in conn.execute(query, (sub_id,)).fetchall()
                    if row[0] and str(row[0]).strip()
                )
            except sqlite3.OperationalError:
                continue

        components: list[tuple[str, str, str, str]] = []
        try:
            components = [
                (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""), str(row[3] or ""))
                for row in conn.execute(
                    """
                    SELECT id, name, type, COALESCE(fqdn, '')
                    FROM provisioned_assets
                    WHERE subscription_id = ?
                    """,
                    (sub_id,),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            pass
    finally:
        try:
            conn.execute("DELETE FROM subscription_trace_cache WHERE sub_id = ?", (sub_id,))
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.close()

    for endpoint in sorted(endpoints):
        query = urlencode({"endpoint": endpoint})
        with app.test_request_context(f"/api/subscriptions/{sub_id}/trace-route?{query}"):
            response = api_cloud_route_trace(sub_id)
        if response.status_code != 200:
            raise RuntimeError(f"route trace precompute failed for {endpoint!r}: HTTP {response.status_code}")

    for component_id, component_name, component_type, component_fqdn in components:
        params = urlencode(
            {
                "component_id": component_id,
                "component_name": component_name,
                "component_type": component_type,
                "component_fqdn": component_fqdn,
            }
        )
        with app.test_request_context(f"/api/subscriptions/{sub_id}/trace-component?{params}"):
            response = api_cloud_component_trace(sub_id)
        if response.status_code != 200:
            raise RuntimeError(
                f"component trace precompute failed for {component_name!r}: "
                f"HTTP {response.status_code}"
            )
