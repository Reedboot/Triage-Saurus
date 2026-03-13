#!/usr/bin/env python3
"""Triage-Saurus web UI — Flask server for repo scanning and Mermaid diagram generation."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import os
from pathlib import Path

from flask import Flask, Response, render_template, request, stream_with_context

app = Flask(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "Scripts"
PIPELINE = SCRIPTS / "Utils" / "run_pipeline.py"
EXPERIMENTS_DIR = REPO_ROOT / "Output" / "Learning" / "experiments"
INTAKE_REPOS = REPO_ROOT / "Intake" / "ReposToScan.txt"

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
        for block in _extract_mermaid_blocks(text):
            provider = arch_file.stem.replace("Architecture_", "")
            diagrams.append({"title": f"{provider} Architecture", "code": block})
    return diagrams


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
        # Output format: "  Experiment : 001" or "Created experiment: 001_name"
        if experiment_id is None:
            m = re.search(r"Experiment\s*[:\s]+(\d+)", line)
            if m:
                experiment_id = m.group(1)

    process.wait()

    # Attempt to send generated Mermaid diagrams
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
    # Allow disabling the debugger via environment variable TRIAGE_DEBUG
    debug_env = os.getenv("TRIAGE_DEBUG", "0").lower()
    debug = debug_env in ("1", "true", "yes", "on")
    app.run(debug=debug, host="0.0.0.0", port=5000, threaded=True)
