#!/usr/bin/env python3
"""Triage-Saurus web UI — Flask server for repo scanning and Mermaid diagram generation."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import os
from pathlib import Path

from flask import Flask, Response, render_template, request, stream_with_context, jsonify

app = Flask(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "Scripts"
PIPELINE = SCRIPTS / "Utils" / "run_pipeline.py"
EXPERIMENTS_DIR = REPO_ROOT / "Output" / "Learning" / "experiments"
INTAKE_REPOS = REPO_ROOT / "Intake" / "ReposToScan.txt"
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"

# Directories searched when resolving a bare repo name from Intake
_SEARCH_ROOTS = [
    REPO_ROOT.parent,
    Path.home() / "code",
    Path.home() / "repos",
    Path.home() / "projects",
    Path.home(),
]


def _resolve_repos() -> list[dict]:
    """Return list of {name, path, found} for every entry in ReposToScan.txt."""
    if not INTAKE_REPOS.exists():
        return []

    entries: list[dict] = []
    for line in INTAKE_REPOS.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        resolved: Path | None = None
        for root in _SEARCH_ROOTS:
            candidate = root / name
            if candidate.is_dir():
                resolved = candidate.resolve()
                break
        entries.append({
            "name": name,
            "path": str(resolved) if resolved else "",
            "found": resolved is not None,
        })
    return entries


def _get_db() -> sqlite3.Connection | None:
    """Return a sqlite3.Connection to the learning DB, or None if unavailable."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _extract_mermaid_blocks(md_text: str) -> list[str]:
    """Return all mermaid code block bodies from a markdown string."""
    return [
        m.group(1).strip()
        for m in re.finditer(r"```mermaid\n(.*?)\n```", md_text, re.DOTALL)
    ]


def _collect_diagrams(experiment_id: str) -> list[dict]:
    """Return list of {title, code} dicts for all architecture diagrams in an experiment."""
    candidates = sorted(EXPERIMENTS_DIR.glob(f"{experiment_id}_*"))
    if not candidates:
        return []
    exp_dir = candidates[0]
    cloud_dir = exp_dir / "Summary" / "Cloud"
    if not cloud_dir.exists():
        return []

    diagrams: list[dict] = []
    for arch_file in sorted(cloud_dir.glob("Architecture_*.md")):
        try:
            text = arch_file.read_text(encoding="utf-8")
        except OSError:
            continue
        provider = arch_file.stem.replace("Architecture_", "")
        blocks = _extract_mermaid_blocks(text)
        if blocks:
            # Markdown report with ```mermaid``` fenced blocks
            for block in blocks:
                diagrams.append({"title": f"{provider} Architecture", "code": block})
        elif re.match(r"^\s*(flowchart|graph|sequenceDiagram|classDiagram)\b", text):
            # Raw Mermaid file with no fences
            diagrams.append({"title": f"{provider} Architecture", "code": text.strip()})
    return diagrams


def _extract_mermaid_nodes(mermaid_code: str) -> set[str]:
    """Extract meaningful node labels from a Mermaid flowchart definition.

    Matches label text inside node shape brackets: NodeId[Label], NodeId("Label"),
    NodeId{Label}, NodeId[(Label)], NodeId([Label]), etc.
    Filters out flowchart direction keywords, subgraph names, and edge labels.
    """
    nodes: set[str] = set()
    # Matches: word_chars followed by optional whitespace then opening bracket
    for m in re.finditer(
        r'\b\w[\w.-]*\s*(?:\[\[|\[\(|\[|\(\[|\(\"|\(\(|\{)[^\]\)\}\n]{1,120}',
        mermaid_code,
    ):
        raw = m.group(0)
        # Extract the label content after the opening bracket
        label_match = re.search(r'[\[\(\{]+(.*?)$', raw)
        if not label_match:
            continue
        label = label_match.group(1).strip().strip('"\'').strip()
        # Skip pure whitespace, very short tokens, and Mermaid keywords
        if len(label) < 2 or label.lower() in {"tb", "lr", "td", "bt", "rl"}:
            continue
        # Strip trailing emoji or punctuation clusters
        label = re.sub(r'[\[\]\(\)\{\}]+$', '', label).strip()
        if label:
            nodes.add(label)
    return nodes


def _has_diagrams(experiment_id: str) -> bool:
    """Return True if the experiment has at least one Architecture_*.md with a mermaid block."""
    candidates = sorted(EXPERIMENTS_DIR.glob(f"{experiment_id}_*"))
    if not candidates:
        return False
    cloud_dir = candidates[0] / "Summary" / "Cloud"
    return cloud_dir.exists() and any(cloud_dir.glob("Architecture_*.md"))


def _sse(event: str, data) -> str:
    """Format a single SSE message."""
    payload = json.dumps(data) if not isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_scan(repo_path: str, scan_name: str):
    """Generator yielding SSE events for the full scan pipeline."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        yield _sse("error", f"Path not found or not a directory: {repo_path}")
        return

    cmd = [
        sys.executable,
        str(PIPELINE),
        "--repo", str(repo),
        "--name", scan_name,
    ]

    yield _sse("log", f"▶  Starting scan: {repo}")
    yield _sse("log", f"   Command: {' '.join(cmd)}")
    yield _sse("log", "")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
            bufsize=1,
        )
    except Exception as exc:
        yield _sse("error", f"Failed to start pipeline: {exc}")
        return

    experiment_id: str | None = None

    for raw_line in process.stdout:
        line = raw_line.rstrip()
        yield _sse("log", line)

        # Capture experiment ID printed by run_pipeline.py
        if experiment_id is None:
            m = re.search(r"Experiment\s*[:\s]+(\d+)", line)
            if m:
                experiment_id = m.group(1)

    process.wait()

    if experiment_id:
        diagrams = _collect_diagrams(experiment_id)
        if diagrams:
            yield _sse("diagrams", diagrams)
        else:
            yield _sse("log", "[Web] No architecture diagrams found in experiment output.")

    yield _sse("done", {
        "exit_code": process.returncode,
        "experiment_id": experiment_id,
    })


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/api/scans/<repo_name>")
def api_scans(repo_name: str):
    """Return previous scan history for a repo, ordered oldest → newest."""
    conn = _get_db()
    if conn is None:
        return jsonify({"scans": [], "error": "DB unavailable"})

    try:
        rows = conn.execute(
            """
            SELECT DISTINCT experiment_id, MAX(scanned_at) AS scanned_at
            FROM repositories
            WHERE LOWER(repo_name) = LOWER(?)
            GROUP BY experiment_id
            ORDER BY experiment_id ASC
            """,
            (repo_name,),
        ).fetchall()
    except Exception as exc:
        conn.close()
        return jsonify({"scans": [], "error": str(exc)})
    finally:
        conn.close()

    scans = []
    for row in rows:
        exp_id = row["experiment_id"]
        scans.append({
            "experiment_id": exp_id,
            "scanned_at": row["scanned_at"],
            "has_diagrams": _has_diagrams(exp_id),
        })
    return jsonify({"scans": scans})


@app.route("/api/diagrams/<experiment_id>")
def api_diagrams(experiment_id: str):
    """Return Mermaid diagrams for a past experiment."""
    diagrams = _collect_diagrams(experiment_id)
    if not diagrams:
        return jsonify({"diagrams": [], "error": f"No diagrams found for experiment {experiment_id}"}), 404
    return jsonify({"diagrams": diagrams})


@app.route("/api/diff")
def api_diff():
    """Compare findings and architecture between two scan experiment IDs.

    Query params: from=<id1>, to=<id2>, repo=<repo_name>
    Returns diagrams for both scans and a timeline of changes across all
    intermediate scans between id1 and id2.
    """
    id_from = (request.args.get("from") or "").strip()
    id_to   = (request.args.get("to") or "").strip()
    repo    = (request.args.get("repo") or "").strip()

    if not id_from or not id_to or not repo:
        return jsonify({"error": "from, to and repo are required"}), 400

    diagrams_from = _collect_diagrams(id_from)
    diagrams_to   = _collect_diagrams(id_to)

    # Collect all experiment IDs for this repo between id_from and id_to
    conn = _get_db()
    all_ids: list[str] = []
    if conn:
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT experiment_id, MAX(scanned_at) AS scanned_at
                FROM repositories
                WHERE LOWER(repo_name) = LOWER(?)
                GROUP BY experiment_id
                ORDER BY experiment_id ASC
                """,
                (repo,),
            ).fetchall()
            all_ids = [r["experiment_id"] for r in rows
                       if id_from <= r["experiment_id"] <= id_to]
        except Exception:
            pass
        finally:
            conn.close()

    # If DB had nothing useful, fall back to just the two endpoints
    if not all_ids:
        all_ids = sorted({id_from, id_to})

    # Build timeline by comparing adjacent pairs
    timeline: list[dict] = []
    prev_findings: set[str] = set()
    prev_nodes: dict[str, set[str]] = {}  # provider -> node labels

    for i, exp_id in enumerate(all_ids):
        conn = _get_db()
        curr_findings: set[str] = set()
        if conn:
            try:
                rows = conn.execute(
                    """
                    SELECT f.rule_id, f.title, f.base_severity
                    FROM findings f
                    JOIN repositories r ON f.experiment_id = r.experiment_id
                      AND f.repo_id = r.id
                    WHERE f.experiment_id = ? AND LOWER(r.repo_name) = LOWER(?)
                    """,
                    (exp_id, repo),
                ).fetchall()
                curr_findings = {r["rule_id"] for r in rows if r["rule_id"]}
                # Build title/severity lookup
                finding_meta = {r["rule_id"]: dict(r) for r in rows if r["rule_id"]}
            except Exception:
                finding_meta = {}
            finally:
                conn.close()
        else:
            finding_meta = {}

        # Architecture nodes for this experiment (per provider)
        curr_nodes: dict[str, set[str]] = {}
        diags = _collect_diagrams(exp_id)
        for d in diags:
            provider = d["title"].replace(" Architecture", "")
            curr_nodes[provider] = _extract_mermaid_nodes(d["code"])

        if i == 0:
            # Baseline — no diff to report
            prev_findings = curr_findings
            prev_nodes = curr_nodes
            continue

        # Compute changes vs previous scan
        new_findings = curr_findings - prev_findings
        resolved_findings = prev_findings - curr_findings

        resource_added: list[str] = []
        resource_removed: list[str] = []
        all_providers = set(curr_nodes) | set(prev_nodes)
        for prov in sorted(all_providers):
            a = prev_nodes.get(prov, set())
            b = curr_nodes.get(prov, set())
            for n in sorted(b - a):
                resource_added.append(f"{prov}: {n}")
            for n in sorted(a - b):
                resource_removed.append(f"{prov}: {n}")

        has_changes = bool(new_findings or resolved_findings or resource_added or resource_removed)
        if has_changes:
            timeline.append({
                "experiment_id": exp_id,
                "new_findings": [
                    {"rule_id": r, "title": finding_meta.get(r, {}).get("title", r),
                     "severity": finding_meta.get(r, {}).get("base_severity", "")}
                    for r in sorted(new_findings)
                ],
                "resolved_findings": sorted(resolved_findings),
                "resources_added": resource_added,
                "resources_removed": resource_removed,
            })

        prev_findings = curr_findings
        prev_nodes = curr_nodes

    return jsonify({
        "from": id_from,
        "to": id_to,
        "repo": repo,
        "diagrams_from": diagrams_from,
        "diagrams_to": diagrams_to,
        "timeline": timeline,
    })


# ── Page Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", repos=_resolve_repos())


@app.route("/scan", methods=["POST"])
def scan():
    repo_path = request.form.get("repo_path", "").strip()
    scan_name = (request.form.get("scan_name", "") or "web_scan").strip()

    if not repo_path:
        return {"error": "repo_path is required"}, 400

    return Response(
        stream_with_context(_stream_scan(repo_path, scan_name)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    debug_env = os.getenv("TRIAGE_DEBUG", "0").lower()
    debug = debug_env in ("1", "true", "yes", "on")
    app.run(debug=debug, host="0.0.0.0", port=5000, threaded=True)


