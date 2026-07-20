from __future__ import annotations


def subscription_env_badge(environment: str | None) -> str:
    badges = {
        "prod": "danger",
        "staging": "warning",
        "dev": "info",
        "shared": "secondary",
    }
    return badges.get(environment or "unknown", "secondary")


def available_subscriptions(conn) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT id, display_name, environment FROM subscriptions ORDER BY environment, display_name"
    ).fetchall()
    return [
        {
            "id": r[0],
            "display_name": r[1] or r[0],
            "environment": r[2] or "unknown",
            "env_badge": subscription_env_badge(r[2]),
        }
        for r in rows
    ]


def linked_repo_subscriptions(conn, experiment_id: str, repo_name: str) -> dict:
    repo = conn.execute(
        "SELECT id FROM repositories WHERE experiment_id=? AND repo_name=?",
        (experiment_id, repo_name),
    ).fetchone()
    if not repo:
        return {"repo_found": False, "linked": []}

    rows = conn.execute(
        """
        SELECT rs.subscription_id, rs.deploy_role, rs.notes, rs.created_at,
               s.display_name, s.environment, s.state, s.last_synced,
               COUNT(DISTINCT pa.id) AS asset_count
        FROM repository_subscriptions rs
        JOIN subscriptions s ON s.id = rs.subscription_id
        LEFT JOIN provisioned_assets pa ON pa.subscription_id = s.id
        WHERE rs.repository_id = ?
        GROUP BY rs.subscription_id
        ORDER BY rs.deploy_role, s.display_name
        """,
        (repo[0],),
    ).fetchall()

    linked = [
        {
            "subscription_id": r[0],
            "deploy_role": r[1] or "primary",
            "notes": r[2],
            "created_at": r[3],
            "display_name": r[4],
            "environment": r[5] or "unknown",
            "env_badge": subscription_env_badge(r[5]),
            "state": r[6],
            "last_synced": r[7],
            "asset_count": r[8] or 0,
        }
        for r in rows
    ]
    return {"repo_found": True, "repo_id": repo[0], "linked": linked}


def link_repo_subscription(
    conn,
    *,
    experiment_id: str,
    repo_name: str,
    subscription_id: str,
    deploy_role: str,
    notes: str,
) -> bool:
    repo = conn.execute(
        "SELECT id FROM repositories WHERE experiment_id=? AND repo_name=?",
        (experiment_id, repo_name),
    ).fetchone()
    if not repo:
        return False

    conn.execute(
        """
        INSERT INTO repository_subscriptions (repository_id, subscription_id, deploy_role, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repository_id, subscription_id) DO UPDATE SET
            deploy_role = excluded.deploy_role,
            notes = excluded.notes
        """,
        (repo[0], subscription_id, deploy_role, notes),
    )
    conn.commit()
    return True


def unlink_repo_subscription(conn, *, experiment_id: str, repo_name: str, subscription_id: str) -> bool:
    repo = conn.execute(
        "SELECT id FROM repositories WHERE experiment_id=? AND repo_name=?",
        (experiment_id, repo_name),
    ).fetchone()
    if not repo:
        return False

    conn.execute(
        "DELETE FROM repository_subscriptions WHERE repository_id=? AND subscription_id=?",
        (repo[0], subscription_id),
    )
    conn.commit()
    return True


def subscription_summary_rows(rows) -> list[dict]:
    subscriptions = []
    for r in rows:
        subscriptions.append(
            {
                "id": r[0],
                "display_name": r[1],
                "environment": r[2] or "unknown",
                "env_badge": subscription_env_badge(r[2]),
                "provider": "Azure",
                "state": r[3],
                "last_synced": r[4],
                "asset_count": r[5] or 0,
                "public_count": r[6] or 0,
            }
        )
    return subscriptions

