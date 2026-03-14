#!/usr/bin/env python3
"""Triage-Saurus web UI — Flask server for repo scanning and Mermaid diagram generation."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import os
import select
import time
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


def _sanitize_mermaid(code: str) -> str:
    """Fix CSS property names and known syntax issues in generated Mermaid code.

    Agents occasionally emit:
    - ``stroke_width`` / ``stroke_dasharray`` with underscores (CSS requires hyphens)
    - ``stroke-dasharray: N N`` with space-separated values (Mermaid needs commas)
    - U+FE0F variation-selector characters after emoji in labels (breaks the lexer)
    - bare ``&`` inside quoted labels (breaks HTML rendering in ``securityLevel:'loose'``)

    Also performs light deduplication and removal of noisy directives that can
    cause the renderer to fail (duplicate subgraphs/nodes, repeated style
    lines, explicit "contains" edges, and self-edges).
    """
    # 1. Hyphenate underscore CSS property names
    replacements = [
        ("stroke_width", "stroke-width"),
        ("stroke_dasharray", "stroke-dasharray"),
        ("stroke_opacity", "stroke-opacity"),
        ("fill_opacity", "fill-opacity"),
        ("font_size", "font-size"),
        ("font_weight", "font-weight"),
        ("text_anchor", "text-anchor"),
        ("line_height", "line-height"),
    ]
    for bad, good in replacements:
        code = code.replace(bad, good)

    # 2. Strip U+FE0F emoji variation selectors — invisible chars that break Mermaid's lexer
    code = code.replace("\ufe0f", "")

    # 3. Replace bare & in quoted Mermaid labels with "and"
    code = re.sub(r'("(?:[^"\\]|\\.)*")', lambda m: m.group(0).replace("&", "and"), code)

    # 4. Fix stroke-dasharray space-separated values → comma-separated
    code = re.sub(
        r'stroke-dasharray\s*:\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)',
        lambda m: f"stroke-dasharray:{m.group(1)},{m.group(2)}",
        code,
    )

    # 5. Remove linkStyle directives which can reference out-of-range indices
    code = re.sub(r'^\s*linkStyle\s+\d+[^\n]*\n', '', code, flags=re.M)

    # 6. Collapse newlines inside bracketed labels across the whole document
    # This handles cases where the opening '[' and closing ']' are on different lines
    code = re.sub(r'\[([^\]]*\n[^\]]*)\]', lambda m: '[' + m.group(1).replace('\n', ' ').replace('\r',' ') + ']', code, flags=re.M|re.S)

    # 7. Remove explicit "contains" edges — containment should be represented via subgraphs
    lines = [ln for ln in code.splitlines() if not re.search(r'\bcontains\b', ln, flags=re.I)]

    # 7. Collapse newlines inside bracketed labels across all lines first
    collapsed = [re.sub(r'\[([^\]]*\n[^\]]*)\]', lambda m: '[' + m.group(1).replace('\n', ' ').replace('\r',' ') + ']', ln) for ln in lines]

    # 8. Remove self-edges (node linking to itself) from the collapsed lines
    filtered: list[str] = []
    for ln in collapsed:
        if re.search(r'[-.]+>', ln):
            parts = re.split(r'[-.]+>', ln)
            if len(parts) >= 2:
                # strip common delimiters and whitespace to compare IDs
                left = re.sub(r"['\"`\[\]\(\)\{\}\s]", '', parts[0]).strip().lower()
                right = re.sub(r"['\"`\[\]\(\)\{\}\s].*$", '', parts[1])
                right = re.sub(r"['\"`\[\]\(\)\{\}\s]", '', right).strip().lower()
                if left and right and left == right:
                    # skip self-edge
                    continue
        filtered.append(ln)

    # 8. Deduplicate subgraph blocks, node defs and style lines
    out_lines: list[str] = []
    seen_subs: set[str] = set()
    seen_nodes: set[str] = set()
    seen_styles: set[str] = set()

    i = 0
    skip_sub = 0
    while i < len(filtered):
        ln = filtered[i]
        trimmed = ln.strip()

        if skip_sub > 0:
            if re.match(r'^\s*end\s*$', trimmed, flags=re.I):
                skip_sub -= 1
            i += 1
            continue

        sub_m = re.match(r'^subgraph\s+([^\s\[]+)', trimmed, flags=re.I)
        if sub_m:
            sid = sub_m.group(1)
            if sid in seen_subs:
                # skip whole subgraph block until matching 'end'
                skip_sub = 1
                i += 1
                continue
            seen_subs.add(sid)
            out_lines.append(ln)
            i += 1
            continue

        node_m = re.match(r'^([^\s\[]+)\s*(?:\[\[|\[\(|\[|\(\[|\(\"|\(\(|\{)', trimmed)
        if node_m:
            nid = node_m.group(1)
            if nid in seen_nodes:
                i += 1
                continue
            seen_nodes.add(nid)
            out_lines.append(ln)
            i += 1
            continue

        style_m = re.match(r'^style\s+([^\s]+)', trimmed, flags=re.I)
        if style_m:
            key = trimmed
            if key in seen_styles:
                i += 1
                continue
            seen_styles.add(key)
            out_lines.append(ln)
            i += 1
            continue

        out_lines.append(ln)
        i += 1

    code = "\n".join(out_lines)

    return code


def _extract_mermaid_blocks(md_text: str) -> list[str]:
    """Return all mermaid code block bodies from a markdown string."""
    return [
        _sanitize_mermaid(m.group(1).strip())
        for m in re.finditer(r"```mermaid\n(.*?)\n```", md_text, re.DOTALL)
    ]


def _collect_diagrams(experiment_id: str) -> list[dict]:
    """Return list of {title, code} dicts for all architecture diagrams in an experiment.

    Groups Architecture_*.md files by provider (case-insensitive) to avoid duplicate
    tabs caused by differing filename casing. If a provider has multiple mermaid
    blocks (or multiple files), each block becomes its own tab with an index when
    needed: "Azure Architecture (1)", "Azure Architecture (2)".
    """
    candidates = sorted(EXPERIMENTS_DIR.glob(f"{experiment_id}_*"))
    if not candidates:
        return []
    exp_dir = candidates[0]
    cloud_dir = exp_dir / "Summary" / "Cloud"
    if not cloud_dir.exists():
        return []

    # Collect blocks keyed by provider (normalized to lowercase)
    provider_map: dict[str, dict] = {}
    for arch_file in sorted(cloud_dir.glob("Architecture_*.md")):
        try:
            text = arch_file.read_text(encoding="utf-8")
        except OSError:
            continue
        provider = arch_file.stem.replace("Architecture_", "")
        key = provider.lower()
        blocks = _extract_mermaid_blocks(text)
        if blocks:
            provider_map.setdefault(key, {"provider": provider, "blocks": []})["blocks"].extend(blocks)
        elif re.match(r"^\s*(flowchart|graph|sequenceDiagram|classDiagram)\b", text, re.I):
            provider_map.setdefault(key, {"provider": provider, "blocks": []})["blocks"].append(_sanitize_mermaid(text.strip()))

    diagrams: list[dict] = []
    for key in sorted(provider_map.keys()):
        entry = provider_map[key]
        raw_provider = entry.get("provider", key)
        # Normalize display name (Title case the provider)
        disp = raw_provider.capitalize()
        blocks = entry.get("blocks", [])
        for idx, block in enumerate(blocks):
            title = f"{disp} Architecture" if len(blocks) == 1 else f"{disp} Architecture ({idx+1})"
            diagrams.append({"title": title, "code": block})

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
    """Generator yielding SSE events for the full scan pipeline.

    Uses a select-based read loop and runs the pipeline in unbuffered Python mode
    so output appears promptly. Emits periodic heartbeat log entries while the
    pipeline produces no output so the UI knows work is ongoing.
    """
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        yield _sse("error", f"Path not found or not a directory: {repo_path}")
        return

    cmd = [
        sys.executable,
        "-u",
        str(PIPELINE),
        "--repo", str(repo),
        "--name", scan_name,
    ]

    yield _sse("log", f"▶  Starting scan: {repo}")
    yield _sse("log", f"   Command: {' '.join(cmd)}")
    yield _sse("log", "")

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        yield _sse("error", f"Failed to start pipeline: {exc}")
        return

    experiment_id: str | None = None
    start_time = time.time()
    last_hb = start_time

    try:
        # Read using select so we can emit heartbeats when the child is silent
        while True:
            reads, _, _ = select.select([process.stdout], [], [], 1.0)
            if reads:
                raw_line = process.stdout.readline()
                if raw_line == '' and process.poll() is not None:
                    break
                line = raw_line.rstrip()
                yield _sse("log", line)

                # Capture experiment ID printed by run_pipeline.py
                if experiment_id is None:
                    m = re.search(r"Experiment\s*[:\s]+(\d+)", line)
                    if m:
                        experiment_id = m.group(1)

                last_hb = time.time()
            else:
                # No output available — send a gentle heartbeat every 5s
                now = time.time()
                if now - last_hb >= 5:
                    elapsed = int(now - start_time)
                    yield _sse("log", f"[Web] Scan in progress — elapsed {elapsed}s")
                    last_hb = now

            if process.poll() is not None:
                # Drain remaining output
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    yield _sse("log", line)
                    if experiment_id is None:
                        m = re.search(r"Experiment\s*[:\s]+(\d+)", line)
                        if m:
                            experiment_id = m.group(1)
                break
    except Exception as exc:
        yield _sse("error", f"Stream error: {exc}")

    if experiment_id:
        diagrams = _collect_diagrams(experiment_id)
        if diagrams:
            yield _sse("diagrams", diagrams)
        else:
            yield _sse("log", "[Web] No architecture diagrams found in experiment output.")

    yield _sse("done", {
        "exit_code": process.returncode if process else -1,
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


@app.route("/api/repo_summary/<experiment_id>/<repo_name>")
def api_repo_summary(experiment_id: str, repo_name: str):
    """Return parsed sections for a repo summary Markdown file inside an experiment folder."""
    candidates = sorted(EXPERIMENTS_DIR.glob(f"{experiment_id}_*"))
    if not candidates:
        return jsonify({"sections": [], "error": f"Experiment {experiment_id} not found"}), 404
    exp_dir = candidates[0]
    repo_file = exp_dir / "Summary" / "Repos" / f"{repo_name}.md"
    if not repo_file.exists():
        return jsonify({"sections": [], "error": f"Repo summary not found for {repo_name} in experiment {experiment_id}"}), 404
    try:
        text = repo_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return jsonify({"sections": [], "error": "Failed to read summary file"}), 500

    pattern = re.compile(r'^(#{2,3})\s*(.+)$', re.M)
    matches = list(pattern.finditer(text))
    sections = []
    if not matches:
        sections.append({"title": f"Summary: {repo_name}", "level": 1, "markdown": text})
        return jsonify({"sections": sections})
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections.append({"title": title, "level": level, "markdown": content})
    return jsonify({"sections": sections})


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


@app.route("/api/assets/<repo_name>")
def api_assets(repo_name: str):
    """Return detected assets for a repo (latest or specified experiment).

    Query params:
      experiment_id  – optional; defaults to the most-recent scan for the repo.

    Response:
      {
        "assets": [{ resource_type, resource_name, provider, region, source_file,
                     source_line_start, discovered_by, discovery_method, status,
                     finding_count }, ...],
        "experiment_id": "022",
        "total": 50,
        "by_provider": {"azure": 50, "aws": 67, ...}
      }
    """
    experiment_id = request.args.get("experiment_id", "").strip()
    conn = _get_db()
    if conn is None:
        return jsonify({"assets": [], "error": "DB unavailable"})

    try:
        if not experiment_id:
            row = conn.execute(
                """SELECT experiment_id FROM repositories
                   WHERE LOWER(repo_name) = LOWER(?)
                   ORDER BY scanned_at DESC LIMIT 1""",
                (repo_name,),
            ).fetchone()
            if row:
                experiment_id = row["experiment_id"]

        if not experiment_id:
            return jsonify({"assets": [], "experiment_id": "", "total": 0, "by_provider": {}})

        rows = conn.execute(
            """
            SELECT
                res.id,
                res.resource_type,
                res.resource_name,
                res.provider,
                res.region,
                res.source_file,
                res.source_line_start,
                res.source_line_end,
                res.discovered_by,
                res.discovery_method,
                res.status,
                COUNT(f.id) AS finding_count,
                MAX(CASE f.base_severity
                    WHEN 'CRITICAL' THEN 5
                    WHEN 'HIGH'     THEN 4
                    WHEN 'MEDIUM'   THEN 3
                    WHEN 'LOW'      THEN 2
                    WHEN 'INFO'     THEN 1
                    ELSE 0 END) AS max_sev_rank
            FROM resources res
            JOIN repositories repo ON res.repo_id = repo.id
            LEFT JOIN findings f ON f.resource_id = res.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            GROUP BY res.id
            ORDER BY res.provider, res.resource_type, res.resource_name
            """,
            (repo_name, experiment_id),
        ).fetchall()

        sev_labels = {5: "CRITICAL", 4: "HIGH", 3: "MEDIUM", 2: "LOW", 1: "INFO"}
        assets: list[dict] = []
        by_provider: dict[str, int] = {}
        for row in rows:
            a = dict(row)
            rank = a.pop("max_sev_rank") or 0
            a["worst_severity"] = sev_labels.get(rank, "")
            provider_key = (a.get("provider") or "unknown").lower()
            by_provider[provider_key] = by_provider.get(provider_key, 0) + 1
            assets.append(a)

        return jsonify({
            "assets": assets,
            "experiment_id": experiment_id,
            "total": len(assets),
            "by_provider": by_provider,
        })
    except Exception as exc:
        return jsonify({"assets": [], "error": str(exc)})
    finally:
        conn.close()


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


