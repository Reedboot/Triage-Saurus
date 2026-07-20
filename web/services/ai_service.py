from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

try:
    from web.core.parsing import parse_json_list
except ImportError:
    from core.parsing import parse_json_list  # type: ignore


def resolve_repo_for_experiment(conn, experiment_id: str) -> tuple[str, str]:
    repo_name = ""
    repo_path = ""

    row = conn.execute(
        """
        SELECT repo_name
        FROM repositories
        WHERE experiment_id = ?
          AND COALESCE(repo_name, '') <> ''
        ORDER BY scanned_at DESC
        LIMIT 1
        """,
        (experiment_id,),
    ).fetchone()
    if row:
        repo_name = (row["repo_name"] or "").strip()

    if not repo_name:
        exp_row = conn.execute(
            "SELECT repos FROM experiments WHERE id = ? LIMIT 1",
            (experiment_id,),
        ).fetchone()
        if exp_row:
            repos_raw = exp_row["repos"]
            repos_list = parse_json_list(repos_raw)
            if repos_list:
                repo_path = str(repos_list[0]).strip()
                repo_name = Path(repo_path).name

    return repo_name, repo_path


def fetch_prior_ai_input_fingerprint(
    get_db: Callable[[], object | None],
    experiment_id: str,
    repo_name: str,
) -> str | None:
    conn = get_db()
    if conn is None:
        return None
    try:
        row = conn.execute(
            """
            SELECT value FROM context_metadata
            WHERE experiment_id = ? AND namespace = 'ai_overview'
              AND key = 'ai_input_fingerprint'
              AND repo_id = (
                SELECT id FROM repositories
                WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1
              )
            LIMIT 1
            """,
            (experiment_id, experiment_id, repo_name),
        ).fetchone()
        return row["value"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def launch_analysis_job_if_idle(
    *,
    lock,
    jobs: dict,
    key: str,
    target: Callable[[str, str], None],
    experiment_id: str,
    repo_name: str,
) -> bool:
    with lock:
        existing = jobs.get(key)
        if existing and existing.get("status") == "running":
            return False

        thread = threading.Thread(
            target=target,
            args=(experiment_id, repo_name),
            daemon=True,
        )
        thread.start()
        return True
