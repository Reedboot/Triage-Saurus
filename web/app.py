#!/usr/bin/env python3
"""Triage-Saurus web UI — Flask server for repo scanning and Mermaid diagram generation."""

from __future__ import annotations

import json
import html
import re
import sqlite3
import subprocess
import sys
import os
import select
import time
import threading
from pathlib import Path

from flask import Flask, Response, render_template, request, stream_with_context, jsonify

app = Flask(__name__)

# Jinja2 custom filters
import os as _os
app.jinja_env.filters["basename"] = lambda p: _os.path.basename(p or "") if p else ""

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "Scripts"
PIPELINE = SCRIPTS / "Utils" / "run_pipeline.py"
ENRICH_FINDINGS = SCRIPTS / "Enrich" / "enrich_findings.py"
RUN_SKEPTICS = SCRIPTS / "Utils" / "run_skeptics.py"
GENERATE_PROJECT_OVERVIEW = SCRIPTS / "Enrich" / "generate_project_overview.py"
EXPERIMENTS_DIR = REPO_ROOT / "Output" / "Learning" / "experiments"
INTAKE_REPOS = REPO_ROOT / "Intake" / "ReposToScan.txt"
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"

# Load repo search paths from config
def _load_search_paths():
    config_file = REPO_ROOT / "Settings" / "paths.json"
    if config_file.exists():
        try:
            import json
            config = json.loads(config_file.read_text())
            paths = config.get("repo_search_paths", [])
            # Expand ~ and environment variables
            return [Path(p).expanduser() for p in paths]
        except Exception:
            pass
    # Fallback to defaults
    return [
        REPO_ROOT.parent,
        Path.home() / "repos",
        Path.home() / "code",
        Path.home() / "projects",
        Path.home(),
    ]

# Directories searched when resolving a bare repo name from Intake
_SEARCH_ROOTS = _load_search_paths()

# Cache of parsed Dockerfile base images: {abs_path: (mtime_ns, size, ((image, line), ...))}
_DOCKERFILE_CACHE: dict[str, tuple[int, int, tuple[tuple[str, int | None], ...]]] = {}
_DOCKERFILE_CACHE_MAX = 512
_RESOLVED_REPOS_CACHE: dict[str, object] = {"sig": None, "entries": []}
_AI_ANALYSIS_JOBS: dict[str, dict] = {}
_AI_ANALYSIS_LOCK = threading.Lock()


def _ai_job_key(experiment_id: str, repo_name: str) -> str:
    return f"{experiment_id}:{repo_name.lower()}"


def _append_ai_job_log(key: str, line: str) -> None:
    """Append a log line to an AI analysis job with a bounded history."""
    ts = time.strftime("%H:%M:%S")
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job:
            return
        logs = job.get("logs", [])
        logs.append(f"[{ts}] {line}")
        if len(logs) > 500:
            logs = logs[-500:]
        job["logs"] = logs
        _AI_ANALYSIS_JOBS[key] = job


def _run_ai_analysis_job(experiment_id: str, repo_name: str) -> None:
    """Run AI enrichment + skeptic analysis in background for an experiment."""
    key = _ai_job_key(experiment_id, repo_name)
    commands = [
        ("enrich_findings", [sys.executable, str(ENRICH_FINDINGS), "--experiment", experiment_id]),
        ("run_skeptics", [sys.executable, str(RUN_SKEPTICS), "--experiment", experiment_id, "--reviewer", "all"]),
        ("generate_project_overview", [sys.executable, str(GENERATE_PROJECT_OVERVIEW), "--experiment", experiment_id, "--repo", repo_name]),
    ]

    with _AI_ANALYSIS_LOCK:
        _AI_ANALYSIS_JOBS[key] = {
            "status": "running",
            "experiment_id": experiment_id,
            "repo_name": repo_name,
            "started_at": time.time(),
            "completed_at": None,
            "steps": [],
            "logs": [],
            "error": "",
        }

    _append_ai_job_log(key, f"AI analysis started for repo '{repo_name}' (experiment {experiment_id})")

    # Mirror pipeline subprocess imports: include Scripts paths on PYTHONPATH
    # so internal modules (for example db_helpers) resolve consistently.
    existing = os.environ.get("PYTHONPATH", "")
    py_paths = [str(SCRIPTS), str(SCRIPTS / "Persist"), str(SCRIPTS / "Utils")]
    if existing:
        py_paths.append(existing)
    subprocess_env = dict(
        os.environ,
        PYTHONUNBUFFERED="1",
        PYTHONPATH=os.pathsep.join(py_paths),
    )

    for step_name, cmd in commands:
        started = time.time()
        _append_ai_job_log(key, f"Starting step: {step_name}")
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=subprocess_env,
        )
        stdout_tail = "\n".join((result.stdout or "").splitlines()[-12:])
        stderr_tail = "\n".join((result.stderr or "").splitlines()[-12:])
        _append_ai_job_log(key, f"Step {step_name} finished with exit code {result.returncode}")
        if stdout_tail:
            for ln in stdout_tail.splitlines():
                _append_ai_job_log(key, f"{step_name} stdout: {ln}")
        if stderr_tail:
            for ln in stderr_tail.splitlines():
                _append_ai_job_log(key, f"{step_name} stderr: {ln}")

        with _AI_ANALYSIS_LOCK:
            job = _AI_ANALYSIS_JOBS.get(key, {})
            steps = job.get("steps", [])
            steps.append({
                "name": step_name,
                "returncode": result.returncode,
                "duration_sec": round(time.time() - started, 2),
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            })
            job["steps"] = steps

            if result.returncode != 0:
                job["status"] = "failed"
                detail = stderr_tail or stdout_tail
                if detail:
                    job["error"] = f"{step_name} failed with exit code {result.returncode}: {detail.splitlines()[-1]}"
                else:
                    job["error"] = f"{step_name} failed with exit code {result.returncode}"
                job["completed_at"] = time.time()
                _AI_ANALYSIS_JOBS[key] = job
                _append_ai_job_log(key, f"AI analysis failed: {job['error']}")
                return

            _AI_ANALYSIS_JOBS[key] = job

    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key, {})
        job["status"] = "completed"
        job["completed_at"] = time.time()
        _AI_ANALYSIS_JOBS[key] = job
    _append_ai_job_log(key, "AI analysis completed successfully")


def _resolve_repos() -> list[dict]:
    """Return list of {name, path, found} for every entry in ReposToScan.txt."""
    if not INTAKE_REPOS.exists():
        return []

    try:
        st = INTAKE_REPOS.stat()
        sig = (st.st_mtime_ns, st.st_size)
        if _RESOLVED_REPOS_CACHE.get("sig") == sig:
            return list(_RESOLVED_REPOS_CACHE.get("entries") or [])
    except OSError:
        sig = None

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

    _RESOLVED_REPOS_CACHE["sig"] = sig
    _RESOLVED_REPOS_CACHE["entries"] = list(entries)
    return entries


def _get_base_images_from_dockerfile(df_path: Path) -> list[dict]:
    """Return cached base image entries ({image, line}) for a Dockerfile."""
    try:
        stat = df_path.stat()
    except OSError:
        return []

    key = str(df_path.resolve())
    cached = _DOCKERFILE_CACHE.get(key)
    signature = (stat.st_mtime_ns, stat.st_size)
    if cached and cached[0] == signature[0] and cached[1] == signature[1]:
        return [{"image": img, "line": line} for img, line in cached[2]]

    try:
        txt = df_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    entries: list[tuple[str, int]] = []
    for idx, line in enumerate(txt.splitlines(), start=1):
        s = line.strip()
        if s.upper().startswith("FROM "):
            parts = s.split()
            if len(parts) > 1:
                entries.append((parts[1], idx))
    serialized = tuple(entries)
    if len(_DOCKERFILE_CACHE) >= _DOCKERFILE_CACHE_MAX:
        # Drop an arbitrary entry to cap growth.
        try:
            _DOCKERFILE_CACHE.pop(next(iter(_DOCKERFILE_CACHE)))
        except StopIteration:
            pass
    _DOCKERFILE_CACHE[key] = (signature[0], signature[1], serialized)
    return [{"image": img, "line": line} for img, line in serialized]


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

    # 9. Replace underscores inside bracketed/parenthesized labels (visible text) with spaces so long identifiers can wrap
    code = re.sub(r'([\[\(\{])([^\]\)\}]*?)([\]\)\}])', lambda m: m.group(1) + m.group(2).replace('_', ' ') + m.group(3), code, flags=re.M|re.S)

    # 10. Insert soft break opportunities after '.' and '_' inside visible labels longer than 8 characters
    def _insert_soft_breaks(m):
        s = m.group(0)
        if len(s) <= 2:
            return s
        opening = s[0]
        closing = s[-1]
        inner = s[1:-1]
        # Strip surrounding quotes for length check when quoted
        check_len = len(inner)
        if check_len <= 8:
            return s
        # Insert zero-width space after dots and underscores to allow line breaks there
        inner2 = inner.replace('.', '.' + '\u200B').replace('_', '_' + '\u200B')
        return opening + inner2 + closing

    code = re.sub(r'("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\([^\)]*\))', _insert_soft_breaks, code)

    # 11. Normalize invalid Mermaid IDs (e.g. Terraform interpolations like ${var.environment})
    # Mermaid node/style identifiers must be simple tokens; normalize anything outside [A-Za-z0-9_].
    id_candidates: set[str] = set()
    for ln in code.splitlines():
        trimmed = ln.strip()
        node_m = re.match(r'^([^\s\[\(\{]+)\s*(?:\[\[|\[\(|\[|\(\[|\("|\(\(|\{)', trimmed)
        if node_m:
            id_candidates.add(node_m.group(1))
        style_m = re.match(r'^style\s+([^\s]+)', trimmed, flags=re.I)
        if style_m:
            id_candidates.add(style_m.group(1))

    reserved = {
        "flowchart",
        "graph",
        "subgraph",
        "end",
        "style",
        "classDef",
        "class",
        "linkStyle",
    }

    def _safe_id(raw: str, used: set[str]) -> str:
        candidate = re.sub(r'[^A-Za-z0-9_]', '_', raw)
        candidate = re.sub(r'_+', '_', candidate).strip('_')
        if not candidate:
            candidate = "node"
        if candidate[0].isdigit() or candidate.lower() in reserved:
            candidate = f"n_{candidate}"
        base = candidate
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

    id_map: dict[str, str] = {}
    used_ids: set[str] = set()
    for original in sorted(id_candidates):
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', original) and original.lower() not in reserved:
            continue
        id_map[original] = _safe_id(original, used_ids)

    if id_map:
        for original in sorted(id_map.keys(), key=len, reverse=True):
            safe = id_map[original]
            code = re.sub(
                rf'(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])',
                safe,
                code,
            )

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
        if key == "terraform":
            continue
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


def _collect_diagrams_dbfirst(experiment_id: str) -> list[dict]:
    """Try DB-backed cloud_diagrams first, fall back to _collect_diagrams."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Scripts.Persist.db_helpers import get_cloud_diagrams  # type: ignore
        db_diagrams = get_cloud_diagrams(experiment_id)
        if db_diagrams:
            filtered = [
                d for d in db_diagrams
                if (d.get("provider") or "").strip().lower() != "terraform"
            ]
            if filtered:
                return [{"title": d["diagram_title"], "code": d["mermaid_code"]} for d in filtered]
    except Exception:
        pass

    return _collect_diagrams(experiment_id)


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

    # Try to create an experiment up-front so the UI can receive a numeric experiment/scan id.
    # Use a short-lived lock file to avoid starting duplicate pipelines for the same repo.
    experiment_id: str | None = None
    triage_script = SCRIPTS / "Experiments" / "triage_experiment.py"

    LOCK_DIR = REPO_ROOT / "Output" / "Learning" / "running_scans"
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best-effort: if lock dir cannot be created, continue without locking
        pass

    lock_file = LOCK_DIR / f"{repo.name}.lock"

    try:
        # If a lock file exists and points to a running experiment, reuse it instead of creating a new one.
        if lock_file.exists():
            try:
                existing = lock_file.read_text(encoding="utf-8").strip()
                if existing:
                    # Find experiment directory by numeric prefix
                    candidates = sorted((REPO_ROOT / "Output" / "Learning" / "experiments").glob(f"{existing}_*"))
                    exp_dir = candidates[0] if candidates else None
                    if exp_dir and (exp_dir / "experiment.json").exists():
                        cfg = json.loads((exp_dir / "experiment.json").read_text(encoding="utf-8"))
                        if cfg.get("status") == "running":
                            experiment_id = existing
                            yield _sse("log", f"[Web] Reusing running experiment id from lock: {experiment_id}")
            except Exception:
                # If lock read fails or experiment not found, fall through to normal creation
                experiment_id = None

        if experiment_id is None and triage_script.exists():
            res = subprocess.run(
                [sys.executable, str(triage_script), "new", scan_name, "--repos", str(repo)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            # If triage_experiment.py succeeded, try to parse the machine-readable marker
            if res.returncode == 0 and res.stdout:
                for ln in res.stdout.splitlines():
                    if ln.startswith("EXPERIMENT_CREATED::"):
                        full = ln.split("::", 1)[1].strip()
                        if full:
                            experiment_id = full.split("_")[0]
                            break
                # Fallback to legacy parsing
                if experiment_id is None:
                    for ln in res.stdout.splitlines():
                        if ln.startswith("Created experiment:"):
                            tag = ln.split(":", 1)[1].strip()
                            if tag:
                                experiment_id = tag.split("_")[0]
                                break
            else:
                # If triage_experiment failed, include stderr in logs for diagnostics
                if res.stderr:
                    yield _sse("log", f"[Web] Failed to pre-create experiment: {res.stderr.splitlines()[0]}")

        # Persist lock for the experiment so concurrent requests reuse it
        if experiment_id:
            try:
                lock_file.write_text(str(experiment_id), encoding="utf-8")
            except Exception:
                pass
    except Exception as exc:
        yield _sse("log", f"[Web] Experiment creation attempt failed: {exc}")

    # Build pipeline command. If an experiment was created, pass it to the pipeline so all scripts use the same id.
    cmd = [
        sys.executable,
        "-u",
        str(PIPELINE),
        "--repo", str(repo),
        "--name", scan_name,
    ]
    if experiment_id:
        cmd.extend(["--experiment", experiment_id])
        yield _sse("log", f"[Web] Using experiment id: {experiment_id}")
        # Inform the UI immediately of the experiment id so it can bind sections/queries
        yield _sse("experiment", experiment_id)

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

    # preserve experiment_id set above (do not reset) — allow capture from child output if missing
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
                    m = re.search(r"Experiment(?:\sID)?\s*[:\s]+([0-9]+)", line)
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
                        m = re.search(r"Experiment(?:\sID)?\s*[:\s]+([0-9]+)", line)
                        if m:
                            experiment_id = m.group(1)
                break
    except Exception as exc:
        yield _sse("error", f"Stream error: {exc}")

    if experiment_id:
        # Prefer DB-backed diagrams to avoid duplicates caused by multiple Architecture_*.md files
        diagrams = []
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from Scripts.Persist.db_helpers import get_cloud_diagrams
            db_diags = get_cloud_diagrams(experiment_id)
            if db_diags:
                diagrams = [{"title": d["diagram_title"], "code": d["mermaid_code"]} for d in db_diags]
        except Exception:
            diagrams = []

        # Fall back to file-based collection if DB didn't have any diagrams
        if not diagrams:
            diagrams = _collect_diagrams(experiment_id)

        if diagrams:
            yield _sse("diagrams", diagrams)
        else:
            yield _sse("log", "[Web] No architecture diagrams found in experiment output.")

    # Remove lock file if it refers to this experiment so future scans can start.
    try:
        if lock_file.exists():
            existing = lock_file.read_text(encoding="utf-8").strip()
            if existing and experiment_id and existing == str(experiment_id):
                lock_file.unlink()
    except Exception:
        pass

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


@app.route("/api/analysis/start/<experiment_id>/<repo_name>", methods=["POST"])
def api_analysis_start(experiment_id: str, repo_name: str):
    """Start AI analysis job for findings in an experiment."""
    conn = _get_db()
    if conn is None:
        return jsonify({"error": "DB unavailable"}), 503

    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return jsonify({"error": f"No completed scan found for {repo_name}."}), 404
    finally:
        conn.close()

    key = _ai_job_key(resolved_exp_id, repo_name)
    with _AI_ANALYSIS_LOCK:
        existing = _AI_ANALYSIS_JOBS.get(key)
        if existing and existing.get("status") == "running":
            return jsonify({"status": "running", "experiment_id": resolved_exp_id, "repo_name": repo_name}), 202

        thread = threading.Thread(
            target=_run_ai_analysis_job,
            args=(resolved_exp_id, repo_name),
            daemon=True,
        )
        thread.start()

    return jsonify({"status": "started", "experiment_id": resolved_exp_id, "repo_name": repo_name})


@app.route("/api/analysis/status/<experiment_id>/<repo_name>")
def api_analysis_status(experiment_id: str, repo_name: str):
    """Get status for current/last AI analysis job for an experiment+repo."""
    key = _ai_job_key(experiment_id, repo_name)
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job:
            return jsonify({"status": "idle", "experiment_id": experiment_id, "repo_name": repo_name})
        return jsonify(job)


@app.route("/api/diagrams/<experiment_id>")
def api_diagrams(experiment_id: str):
    """Return Mermaid diagrams for a past experiment.

    Prefers the cloud_diagrams DB table; falls back to Architecture_*.md files
    for backwards compatibility with experiments run before the DB migration.
    """
    # Try DB first
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Scripts.Persist.db_helpers import get_cloud_diagrams  # type: ignore
        db_diagrams = get_cloud_diagrams(experiment_id)
        if db_diagrams:
            return jsonify({
                "diagrams": [
                    {"title": d["diagram_title"], "code": d["mermaid_code"]}
                    for d in db_diagrams
                ]
            })
    except Exception:
        pass

    # Fall back to legacy file-based approach
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

    diagrams_from = _collect_diagrams_dbfirst(id_from)
    diagrams_to   = _collect_diagrams_dbfirst(id_to)

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
        diags = _collect_diagrams_dbfirst(exp_id)
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


# ── /api/view/ — DB-driven section routes ─────────────────────────────────────

def _db_render(template_name: str, **ctx):
    """Render a partial template with given context."""
    from flask import render_template as _rt
    return _rt(f"partials/{template_name}", **ctx)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True when a table exists in the connected SQLite database."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return the set of column names for a SQLite table, or empty set."""
    if not _table_exists(conn, table_name):
        return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(r[1]) for r in rows}
    except Exception:
        return set()


def _get_experiment_for_repo(conn, repo_name: str, experiment_id: str = "") -> str:
    """Return a valid experiment_id for the repo, preferring the provided id."""
    if not _table_exists(conn, "repositories"):
        return ""
    repo_cols = _table_columns(conn, "repositories")
    if "experiment_id" not in repo_cols or "repo_name" not in repo_cols:
        return ""

    try:
        if experiment_id:
            exists = conn.execute(
                """
                SELECT 1 FROM repositories
                WHERE LOWER(repo_name) = LOWER(?) AND experiment_id = ?
                LIMIT 1
                """,
                (repo_name, experiment_id),
            ).fetchone()
            if exists:
                return experiment_id

        order_clause = "ORDER BY scanned_at DESC" if "scanned_at" in repo_cols else "ORDER BY experiment_id DESC"
        row = conn.execute(
            f"""SELECT experiment_id FROM repositories
               WHERE LOWER(repo_name) = LOWER(?)
               {order_clause} LIMIT 1""",
            (repo_name,),
        ).fetchone()
        return row["experiment_id"] if row else ""
    except Exception:
        return ""


@app.route("/api/view/tabs/<experiment_id>/<repo_name>")
def api_view_tabs(experiment_id: str, repo_name: str):
    """Return the list of available tabs for this experiment/repo."""
    try:
        tabs = [
            {"key": "tldr",       "label": "📊 TL;DR"},
            {"key": "overview",   "label": "📝 Overview"},
            {"key": "assets",     "label": "🗂️ Assets"},
            {"key": "findings",   "label": "🔎 Findings"},
            {"key": "containers", "label": "🐳 Containers"},
            {"key": "ports",      "label": "🔌 Ports & Protocols"},
            {"key": "roles",      "label": "🧑‍💼 Roles & Permissions"},
            {"key": "ingress",    "label": "➡️ Ingress"},
            {"key": "egress",     "label": "⬅️ Egress"},
        ]

        return jsonify({"tabs": tabs})
    except Exception as e:
        # Return a JSON error so the UI can parse and show a message instead of failing silently
        return jsonify({"tabs": [], "error": str(e)}), 500


def _normalize_display_name(name: str) -> str:
    """Normalize Terraform/local references for friendlier UI labels."""
    if not name:
        return name
    value = re.sub(r"\$\{([^}]+)\}", r"\1", name)
    if value.startswith("local.") or value.startswith("var."):
        value = value.replace(".", " ")
    return value


# Attach a normalizer hook for templates by exposing a simple mapping endpoint
# (templates call /api/view/normalize?name=... via JS when rendering ingress rows)
@app.route("/api/view/normalize")
def api_view_normalize():
    q = request.args.get('name', '')
    return jsonify({'name': _normalize_display_name(q)})


@app.route("/api/view/tldr/<experiment_id>/<repo_name>")
def api_view_tldr(experiment_id: str, repo_name: str):
    """Render the TL;DR tab from structured DB data only."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_tldr.html", tldr_html="", error="DB unavailable")

    try:
        if not _table_exists(conn, "repositories"):
            return _db_render("tab_tldr.html", tldr_html="", error="No repository table found in DB.")

        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_tldr.html", tldr_html="", error=f"No completed scan found for {repo_name}.")

        # Build DB-driven TL;DR stats table
        repo_cols = _table_columns(conn, "repositories")
        repo_type_sel = "repo_type" if "repo_type" in repo_cols else "'' AS repo_type"
        primary_lang_sel = "primary_language" if "primary_language" in repo_cols else "'' AS primary_language"
        repo_row = conn.execute(
            f"""
            SELECT {repo_type_sel}, {primary_lang_sel}
            FROM repositories
            WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
            LIMIT 1
            """,
            (resolved_exp_id, repo_name),
        ).fetchone()

        repo_type = repo_row["repo_type"] if repo_row else ""
        primary_language = repo_row["primary_language"] if repo_row else ""

        def _guess_hosting() -> str:
            if not _table_exists(conn, "resources"):
                return ""
            rows = conn.execute(
                """
                SELECT DISTINCT provider
                FROM resources res
                JOIN repositories repo ON res.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                  AND COALESCE(TRIM(provider), '') != ''
                """,
                (resolved_exp_id, repo_name),
            ).fetchall()
            providers = { (r["provider"] or "").strip().lower() for r in rows if r["provider"] }
            if len(providers) > 1:
                return "Multi-cloud"
            if providers:
                name = next(iter(providers))
                return f"{name.capitalize()} cloud"
            return ""

        def _guess_providers() -> str:
            if not _table_exists(conn, "resources"):
                return ""
            rows = conn.execute(
                """
                SELECT provider, COUNT(*) AS cnt
                FROM resources res
                JOIN repositories repo ON res.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                GROUP BY provider
                ORDER BY cnt DESC
                LIMIT 3
                """,
                (resolved_exp_id, repo_name),
            ).fetchall()
            return ", ".join(
                (row["provider"] or "Unknown").replace("_", " ").title()
                for row in rows if row["provider"]
            )

        hosting_model = _guess_hosting() or "Unknown"
        cicd_tool = ""
        provider_summary = _guess_providers() or "Unknown"


        # Resource counts
        counts = None
        if _table_exists(conn, "resources"):
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_resources,
                    SUM(CASE WHEN category = 'Identity' THEN 1 ELSE 0 END) AS identity_count,
                    SUM(CASE WHEN category = 'Network' THEN 1 ELSE 0 END) AS network_count,
                    SUM(CASE WHEN category = 'Storage' THEN 1 ELSE 0 END) AS storage_count,
                    SUM(CASE WHEN category = 'Database' THEN 1 ELSE 0 END) AS database_count
                FROM (
                    SELECT r.id,
                           COALESCE(rt.category, 'Other') AS category
                    FROM resources r
                    JOIN repositories repo ON r.repo_id = repo.id
                    LEFT JOIN resource_types rt ON r.resource_type = rt.terraform_type
                    WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                ) sub
                """,
                (resolved_exp_id, repo_name),
            ).fetchone()

        total_resources = counts["total_resources"] if counts else 0
        identity_count = counts["identity_count"] if counts else 0
        network_count = counts["network_count"] if counts else 0
        storage_count = counts["storage_count"] if counts else 0
        database_count = counts["database_count"] if counts else 0

        # Findings summary
        findings_summary = None
        findings_cols = _table_columns(conn, "findings")
        if findings_cols:
            sev_score_expr = "severity_score" if "severity_score" in findings_cols else "0"
            base_sev_expr = "base_severity" if "base_severity" in findings_cols else "''"
            findings_summary = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_findings,
                    SUM(CASE WHEN {sev_score_expr} >= 7 THEN 1 ELSE 0 END) AS high_or_above,
                    SUM(CASE WHEN {base_sev_expr} IN ('CRITICAL','HIGH') THEN 1 ELSE 0 END) AS critical_high
                FROM findings f
                JOIN repositories repo ON f.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                """,
                (resolved_exp_id, repo_name),
            ).fetchone()

        total_findings = findings_summary["total_findings"] if findings_summary else 0
        high_or_above = findings_summary["high_or_above"] if findings_summary else 0
        critical_high = findings_summary["critical_high"] if findings_summary else 0

        # CI/CD enrichment via context metadata
        if not cicd_tool and _table_exists(conn, "context_metadata"):
            cm_row = conn.execute(
                """
                SELECT value FROM context_metadata
                WHERE experiment_id = ? AND LOWER(key) = 'cicd' AND repo_id = (
                    SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name)=LOWER(?) LIMIT 1
                )
                LIMIT 1
                """,
                (resolved_exp_id, resolved_exp_id, repo_name),
            ).fetchone()
            if cm_row and cm_row["value"]:
                cicd_tool = cm_row["value"]

        rows_html = []

        def add_row(label: str, value: str) -> None:
            if value:
                rows_html.append(f"<tr><td>{label}</td><td>{value}</td></tr>")

        add_row("Type", repo_type or "Unknown")
        add_row("Primary language", primary_language or "Unknown")

        # Languages detected: derive from concrete source-file evidence first.
        # Falls back to primary language when no file-extension evidence exists.
        try:
            lang_rows = conn.execute(
                """
                WITH observed_files AS (
                    SELECT source_file AS path
                    FROM findings f
                    JOIN repositories repo ON f.repo_id = repo.id
                    WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                      AND source_file IS NOT NULL AND TRIM(source_file) != ''
                    UNION ALL
                    SELECT source_file AS path
                    FROM resources r
                    JOIN repositories repo ON r.repo_id = repo.id
                    WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                      AND source_file IS NOT NULL AND TRIM(source_file) != ''
                ),
                ext_counts AS (
                    SELECT LOWER(
                        CASE
                            WHEN instr(path, '.') > 0 THEN substr(path, instr(path, '.') + 1)
                            ELSE ''
                        END
                    ) AS ext,
                    COUNT(*) AS cnt
                    FROM observed_files
                    GROUP BY ext
                )
                SELECT ext, cnt
                FROM ext_counts
                WHERE ext IN ('py','js','ts','tsx','jsx','java','kt','go','rb','php','cs','cpp','c','rs','swift','scala','sql','tf')
                ORDER BY cnt DESC
                LIMIT 8
                """,
                (resolved_exp_id, repo_name, resolved_exp_id, repo_name),
            ).fetchall()

            language_map = {
                'py': 'Python', 'js': 'JavaScript', 'ts': 'TypeScript', 'tsx': 'TypeScript/TSX',
                'jsx': 'JavaScript/JSX', 'java': 'Java', 'kt': 'Kotlin', 'go': 'Go',
                'rb': 'Ruby', 'php': 'PHP', 'cs': 'C#/.NET', 'cpp': 'C++', 'c': 'C',
                'rs': 'Rust', 'swift': 'Swift', 'scala': 'Scala', 'sql': 'SQL', 'tf': 'Terraform',
            }
            detected = [language_map.get(row['ext'], row['ext']) for row in lang_rows if row['ext']]

            if detected:
                # keep order stable while removing duplicates
                seen = set()
                ordered = []
                for name in detected:
                    if name in seen:
                        continue
                    seen.add(name)
                    ordered.append(name)
                add_row("Languages detected", ", ".join(ordered))
            elif primary_language:
                add_row("Languages detected", primary_language)
        except Exception:
            if primary_language:
                add_row("Languages detected", primary_language)

        add_row("Hosting", hosting_model or "Unknown")
        add_row("CI/CD", cicd_tool or "Unknown")
        add_row("Cloud providers", provider_summary or "Unknown")
        add_row("Resources discovered", str(total_resources))
        if total_resources:
            details = []
            if identity_count:
                details.append(f"Identity: {identity_count}")
            if network_count:
                details.append(f"Network: {network_count}")
            if storage_count:
                details.append(f"Storage: {storage_count}")
            if database_count:
                details.append(f"Database: {database_count}")
            if details:
                add_row("Breakdown", ", ".join(details))
        add_row("Findings discovered", str(total_findings))
        if total_findings:
            add_row("High/Critical findings", f"{critical_high} critical/high · {high_or_above} sev ≥ 7")

        # Scan status heuristics
        try:
            stage_counts = conn.execute(
                """
                SELECT
                    (SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END FROM resources WHERE experiment_id = ?) AS p1,
                    (SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END FROM exposure_analysis WHERE experiment_id = ?) AS p2,
                    (SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END FROM findings WHERE experiment_id = ?) AS p3
                """,
                (resolved_exp_id, resolved_exp_id, resolved_exp_id),
            ).fetchone()
            if stage_counts:
                def icon_for(val: int) -> str:
                    return "🟢" if val else "🟡"

                def line(label: str, val: int) -> str:
                    status_text = "complete" if val else "pending"
                    return f"<div class='scan-status-line'>{icon_for(val)} <span>{label} ({status_text})</span></div>"

                # AI analysis status: prefer live job state, fall back to DB evidence.
                ai_key = _ai_job_key(resolved_exp_id, repo_name)
                with _AI_ANALYSIS_LOCK:
                    ai_job = _AI_ANALYSIS_JOBS.get(ai_key)

                ai_icon = "🟡"
                ai_text = "pending"
                if ai_job:
                    ai_state = (ai_job.get("status") or "").lower()
                    if ai_state == "running":
                        ai_icon, ai_text = "🟠", "running"
                    elif ai_state == "failed":
                        ai_icon, ai_text = "🔴", "failed"
                    elif ai_state == "completed":
                        ai_icon, ai_text = "🟢", "complete"
                else:
                    ai_counts = conn.execute(
                        """
                        SELECT
                            COUNT(*) AS total_findings,
                            SUM(CASE WHEN llm_enriched_at IS NOT NULL THEN 1 ELSE 0 END) AS enriched_findings,
                            (SELECT COUNT(*)
                             FROM skeptic_reviews sr
                             JOIN findings sf ON sf.id = sr.finding_id
                             WHERE sf.experiment_id = ?
                               AND sf.repo_id = repo.id) AS skeptic_reviews,
                            (SELECT COUNT(*)
                             FROM context_metadata cm
                             WHERE cm.experiment_id = ?
                               AND cm.repo_id = repo.id
                               AND cm.namespace = 'ai_overview'
                               AND cm.key = 'ai_project_summary') AS ai_overview
                        FROM findings f
                        JOIN repositories repo ON f.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                        """,
                        (resolved_exp_id, resolved_exp_id, resolved_exp_id, repo_name),
                    ).fetchone()
                    if ai_counts:
                        total = ai_counts["total_findings"] or 0
                        enriched = ai_counts["enriched_findings"] or 0
                        skeptic_reviews = ai_counts["skeptic_reviews"] or 0
                        ai_overview = ai_counts["ai_overview"] or 0
                        if total == 0:
                            ai_icon, ai_text = "🟡", "pending"
                        elif enriched >= total and skeptic_reviews > 0 and ai_overview > 0:
                            ai_icon, ai_text = "🟢", "complete"
                        elif enriched > 0 or skeptic_reviews > 0 or ai_overview > 0:
                            ai_icon, ai_text = "🟠", "partial"
                        else:
                            ai_icon, ai_text = "🟡", "pending"

                status_html = "".join(
                    [
                        line("Discovery & inventory", stage_counts["p1"]),
                        line("Exposure mapping", stage_counts["p2"]),
                        line("Findings correlation", stage_counts["p3"]),
                        f"<div class='scan-status-line'>{ai_icon} <span>AI analysis ({ai_text})</span></div>",
                    ]
                )
                add_row("Scan status", status_html)
        except Exception:
            pass

        table_html = (
            '<table class="tldr-table">'
            "<tbody>"
            + "".join(rows_html)
            + "</tbody></table>"
        )

        return _db_render("tab_tldr.html", tldr_html=table_html)
    except Exception as exc:
        return _db_render("tab_tldr.html", tldr_html="", error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/risks/<experiment_id>/<repo_name>")
def api_view_risks(experiment_id: str, repo_name: str):
    """Render the Risks tab from DB-driven findings summary."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_risks.html", risks_html="", error="DB unavailable")

    try:
        if not _table_exists(conn, "repositories"):
            return _db_render("tab_risks.html", risks_html="", error="No repository table found in DB.")

        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_risks.html", risks_html="", error=f"No completed scan found for {repo_name}.")

        # Build DB-driven risks summary from findings
        findings_cols = _table_columns(conn, "findings")
        if not findings_cols:
            return _db_render("tab_risks.html", risks_html="")

        title_sel = "f.title" if "title" in findings_cols else "f.rule_id AS title"
        desc_sel = "f.description" if "description" in findings_cols else "'' AS description"
        base_sev_sel = "f.base_severity" if "base_severity" in findings_cols else "'INFO' AS base_severity"
        sev_score_sel = "f.severity_score" if "severity_score" in findings_cols else "0 AS severity_score"
        rule_sel = "f.rule_id" if "rule_id" in findings_cols else "'' AS rule_id"
        has_resource_join = _table_exists(conn, "resources") and ("resource_id" in findings_cols)
        resource_name_sel = "r.resource_name" if has_resource_join else "NULL AS resource_name"
        resource_type_sel = "r.resource_type" if has_resource_join else "NULL AS resource_type"
        resource_join_sql = "LEFT JOIN resources r ON f.resource_id = r.id" if has_resource_join else ""

        rows = conn.execute(
            f"""
            SELECT {title_sel}, {desc_sel}, {base_sev_sel}, {sev_score_sel},
                   {rule_sel}, {resource_name_sel}, {resource_type_sel}
            FROM findings f
            JOIN repositories repo ON f.repo_id = repo.id
            {resource_join_sql}
            WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
            ORDER BY
                CASE {base_sev_sel.split(' AS ')[0] if ' AS ' in base_sev_sel else base_sev_sel}
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'MEDIUM' THEN 3
                    WHEN 'LOW' THEN 4
                    WHEN 'INFO' THEN 5
                    ELSE 6
                END,
                {sev_score_sel.split(' AS ')[0] if ' AS ' in sev_score_sel else sev_score_sel} DESC
            """,
            (resolved_exp_id, repo_name),
        ).fetchall()

        if not rows:
            return _db_render("tab_risks.html", risks_html="")

        sections = []
        for row in rows:
            sev = (row["base_severity"] or "INFO").upper()
            badge_class = {
                "CRITICAL": "critical",
                "HIGH": "high",
                "MEDIUM": "medium",
                "LOW": "low",
                "INFO": "info",
            }.get(sev, "info")
            resource_label = ""
            if row["resource_name"]:
                resource_label = f"<div class='risk-resource'>Resource: <strong>{row['resource_name']}</strong> ({row['resource_type'] or 'unknown type'})</div>"
            rule_label = f"<div class='risk-rule'>Rule: <code>{row['rule_id']}</code></div>" if row["rule_id"] else ""
            description = (row["description"] or "").strip()
            sections.append(
                "<section class='risk-card'>"
                f"<header><span class='risk-badge {badge_class}'>{sev}</span>"
                f"<h3>{row['title']}</h3></header>"
                f"<p>{description}</p>"
                f"{resource_label}{rule_label}"
                "</section>"
            )

        risks_html = "<div class='risk-cards'>" + "".join(sections) + "</div>"
        return _db_render("tab_risks.html", risks_html=risks_html)
    except Exception as exc:
        return _db_render("tab_risks.html", risks_html="", error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/overview/<experiment_id>/<repo_name>")
def api_view_overview(experiment_id: str, repo_name: str):
    """Render a true repo synopsis overview from structured DB data only."""
    conn = _get_db()
    resolved_exp_id = experiment_id
    if conn is not None:
        try:
            resolved = _get_experiment_for_repo(conn, repo_name, experiment_id)
            if resolved:
                resolved_exp_id = resolved
        except Exception:
            pass

    if conn is None:
        return _db_render('tab_overview.html', overview_html='', experiment_id=experiment_id, repo_name=repo_name)

    esc = html.escape
    overview_sections: list[str] = []

    table_exists_cache: dict[str, bool] = {}
    table_columns_cache: dict[str, set[str]] = {}

    def _table_exists(table_name: str) -> bool:
        if table_name in table_exists_cache:
            return table_exists_cache[table_name]
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (table_name,),
            ).fetchone()
            exists = bool(row)
        except Exception:
            exists = False
        table_exists_cache[table_name] = exists
        return exists

    def _table_columns(table_name: str) -> set[str]:
        if table_name in table_columns_cache:
            return table_columns_cache[table_name]
        if not _table_exists(table_name):
            table_columns_cache[table_name] = set()
            return set()
        try:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            cols = {str(r[1]) for r in rows}
        except Exception:
            cols = set()
        table_columns_cache[table_name] = cols
        return cols

    def _safe_fetchall(sql: str, params: tuple = ()):
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    def _safe_fetchone(sql: str, params: tuple = ()):
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError:
            return None

    try:
        repo_cols = _table_columns("repositories")
        if not repo_cols:
            return _db_render('tab_overview.html', overview_html='', experiment_id=resolved_exp_id, repo_name=repo_name)
        repo_select = ["id"]
        repo_select.append("primary_language" if "primary_language" in repo_cols else "'' AS primary_language")
        repo_select.append("files_scanned" if "files_scanned" in repo_cols else "0 AS files_scanned")
        repo_select.append("iac_files_count" if "iac_files_count" in repo_cols else "0 AS iac_files_count")
        repo_select.append("code_files_count" if "code_files_count" in repo_cols else "0 AS code_files_count")
        repo_row = _safe_fetchone(
            f"""
            SELECT {', '.join(repo_select)}
            FROM repositories
            WHERE LOWER(repo_name)=LOWER(?) AND experiment_id = ?
            LIMIT 1
            """,
            (repo_name, resolved_exp_id),
        )
        if not repo_row:
            return _db_render('tab_overview.html', overview_html='', experiment_id=resolved_exp_id, repo_name=repo_name)

        repo_id = repo_row['id']

        # 0) AI-generated overview (if available)
        ai_overview_rows = _safe_fetchall(
            """
            SELECT key, value
            FROM context_metadata
            WHERE experiment_id = ? AND repo_id = ? AND namespace = 'ai_overview'
            ORDER BY key
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("context_metadata") else []
        if ai_overview_rows:
            label_map = {
                "ai_project_summary": "Project",
                "ai_deployment_summary": "Deployment",
                "ai_interactions_summary": "Interactions",
                "ai_auth_summary": "Auth",
                "ai_dependencies_summary": "Dependencies",
                "ai_issues_summary": "Issues",
                "ai_skeptic_summary": "Skeptics",
            }
            ai_points = []
            for row in ai_overview_rows:
                val = (row["value"] or "").strip()
                if not val:
                    continue
                label = label_map.get(row["key"], row["key"])
                ai_points.append(f"<li><strong>{esc(label)}</strong>: {esc(val)}</li>")
            if ai_points:
                overview_sections.append("<h2>🤖 AI Project Overview</h2><ul>" + ''.join(ai_points) + "</ul>")

        # 1) What the repo does
        purpose_rows = _safe_fetchall(
            """
            SELECT key, value
            FROM context_metadata
            WHERE experiment_id = ? AND repo_id = ?
              AND namespace != 'ai_overview'
              AND (
                LOWER(key) LIKE '%summary%' OR
                LOWER(key) LIKE '%purpose%' OR
                LOWER(key) LIKE '%description%'
              )
            ORDER BY key
            LIMIT 3
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("context_metadata") else []
        purpose_points = [f"<li><strong>{esc(r['key'])}</strong>: {esc((r['value'] or '').strip())}</li>" for r in purpose_rows if (r['value'] or '').strip()]
        if not purpose_points:
            module_rows = _safe_fetchall(
                """
                SELECT key
                FROM context_metadata
                WHERE experiment_id = ? AND repo_id = ? AND key LIKE 'module:%'
                ORDER BY key
                LIMIT 6
                """,
                (resolved_exp_id, repo_id),
            ) if _table_exists("context_metadata") else []
            module_names = [r['key'].split(':', 1)[1] for r in module_rows]
            language = repo_row['primary_language'] or 'unknown'
            purpose_points.append(
                f"<li>Primary language: <strong>{esc(language)}</strong>; scanned files: {repo_row['files_scanned'] or 0} (IaC: {repo_row['iac_files_count'] or 0}, code: {repo_row['code_files_count'] or 0}).</li>"
            )
            if module_names:
                purpose_points.append(f"<li>Detected Terraform modules: {esc(', '.join(module_names[:6]))}.</li>")
        overview_sections.append("<h2>📝 What This Repo Does</h2><ul>" + ''.join(purpose_points) + "</ul>")

        # 2) Where it is deployed
        provider_rows = _safe_fetchall(
            """
            SELECT COALESCE(provider, 'unknown') AS provider, COUNT(*) AS cnt
            FROM resources
            WHERE repo_id = ? AND experiment_id = ?
            GROUP BY COALESCE(provider, 'unknown')
            ORDER BY cnt DESC
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("resources") else []
        region_rows = _safe_fetchall(
            """
            SELECT region, COUNT(*) AS cnt
            FROM resources
            WHERE repo_id = ? AND experiment_id = ?
              AND region IS NOT NULL AND TRIM(region) != ''
            GROUP BY region
            ORDER BY cnt DESC
            LIMIT 6
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("resources") else []
        deploy_points = []
        if provider_rows:
            deploy_points.append("<li>Providers: " + esc(', '.join(f"{r['provider']} ({r['cnt']})" for r in provider_rows)) + "</li>")
        if region_rows:
            deploy_points.append("<li>Regions: " + esc(', '.join(f"{r['region']} ({r['cnt']})" for r in region_rows)) + "</li>")
        footprint_rows = _safe_fetchall(
            """
            SELECT resource_type, COUNT(*) AS cnt
            FROM resources
            WHERE repo_id = ? AND experiment_id = ?
            GROUP BY resource_type
            ORDER BY cnt DESC
            LIMIT 8
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("resources") else []
        if footprint_rows:
            deploy_points.append("<li>Deployment footprint: " + esc(', '.join(f"{r['resource_type']} x{r['cnt']}" for r in footprint_rows)) + "</li>")
        if deploy_points:
            overview_sections.append("<h2>🌍 Where It Is Deployed</h2><ul>" + ''.join(deploy_points) + "</ul>")

        # 3) What talks to it
        talk_rows = _safe_fetchall(
            """
            SELECT connection_type, COUNT(*) AS cnt
            FROM resource_connections
            WHERE experiment_id = ?
              AND target_repo_id = ?
              AND connection_type IS NOT NULL
              AND LOWER(connection_type) NOT IN ('contains')
            GROUP BY connection_type
            ORDER BY cnt DESC
            LIMIT 10
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("resource_connections") else []
        if talk_rows:
            talk_points = [f"<li>{esc(r['connection_type'])}: {r['cnt']}</li>" for r in talk_rows]
            overview_sections.append("<h2>🔁 What Talks To It</h2><ul>" + ''.join(talk_points) + "</ul>")

        # 4) Authentication / authorization
        id_rows = _safe_fetchall(
            """
            SELECT resource_name, resource_type, provider
            FROM resources
            WHERE repo_id = ? AND experiment_id = ?
              AND LOWER(resource_type) IN (
                'azurerm_user_assigned_identity','azurerm_managed_identity','aws_iam_role',
                'aws_iam_user','google_service_account','service_account','service_principal'
              )
            ORDER BY provider, resource_type
            LIMIT 12
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("resources") else []
        rc_cols = _table_columns("resource_connections")
        if rc_cols:
            auth_method_select = "auth_method" if "auth_method" in rc_cols else "NULL AS auth_method"
            auth_method_predicate = "(auth_method IS NOT NULL AND TRIM(auth_method) != '') OR" if "auth_method" in rc_cols else ""
            auth_rows = _safe_fetchall(
                f"""
                SELECT connection_type, authentication, authorization, {auth_method_select}, COUNT(*) AS cnt
                FROM resource_connections
                WHERE experiment_id = ?
                  AND (source_repo_id = ? OR target_repo_id = ?)
                  AND (
                    (authentication IS NOT NULL AND TRIM(authentication) != '') OR
                    (authorization IS NOT NULL AND TRIM(authorization) != '') OR
                    {auth_method_predicate}
                    LOWER(connection_type) LIKE '%auth%' OR
                    LOWER(connection_type) LIKE '%grant%'
                  )
                GROUP BY connection_type, authentication, authorization, {auth_method_select}
                ORDER BY cnt DESC
                LIMIT 12
                """,
                (resolved_exp_id, repo_id, repo_id),
            )
        else:
            auth_rows = []
        auth_points = []
        if id_rows:
            auth_points.extend([
                f"<li>Identity: <strong>{esc(r['resource_name'] or 'Unnamed')}</strong> — {esc(r['resource_type'])} ({esc(r['provider'] or 'unknown')})</li>"
                for r in id_rows
            ])
        if auth_rows:
            for r in auth_rows:
                parts = [r['connection_type'] or 'auth-related']
                if r['auth_method']:
                    parts.append(f"method={r['auth_method']}")
                if r['authentication']:
                    parts.append(f"authentication={r['authentication']}")
                if r['authorization']:
                    parts.append(f"authorization={r['authorization']}")
                auth_points.append(f"<li>{esc('; '.join(parts))} (count {r['cnt']})</li>")
        if auth_points:
            overview_sections.append("<h2>🔐 How Access Is Controlled</h2><ul>" + ''.join(auth_points) + "</ul>")

        # 5) Dependencies
        dep_rows = _safe_fetchall(
            """
            SELECT connection_type, COUNT(*) AS cnt
            FROM resource_connections
            WHERE experiment_id = ?
              AND source_repo_id = ?
              AND connection_type IS NOT NULL
              AND LOWER(connection_type) NOT IN ('contains')
            GROUP BY connection_type
            ORDER BY cnt DESC
            LIMIT 10
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("resource_connections") else []
        module_rows = _safe_fetchall(
            """
            SELECT key, value
            FROM context_metadata
            WHERE experiment_id = ? AND repo_id = ? AND key LIKE 'module:%'
            ORDER BY key
            LIMIT 10
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("context_metadata") else []
        dep_points = [f"<li>Connection dependency: {esc(r['connection_type'])} ({r['cnt']})</li>" for r in dep_rows]
        for r in module_rows:
            name = r['key'].split(':', 1)[1]
            src = (r['value'] or '').strip()
            dep_points.append(f"<li>Module dependency: <strong>{esc(name)}</strong>{(': ' + esc(src)) if src else ''}</li>")
        if dep_points:
            overview_sections.append("<h2>🧩 Dependencies</h2><ul>" + ''.join(dep_points) + "</ul>")

        # 6) Issues (condensed)
        findings_cols = _table_columns("findings")
        severity_expr = "'INFO'"
        if "base_severity" in findings_cols and "severity" in findings_cols:
            severity_expr = "COALESCE(base_severity, severity, 'INFO')"
        elif "base_severity" in findings_cols:
            severity_expr = "COALESCE(base_severity, 'INFO')"
        elif "severity" in findings_cols:
            severity_expr = "COALESCE(severity, 'INFO')"

        sev_rows = _safe_fetchall(
            """
            SELECT UPPER({severity_expr}) AS sev, COUNT(*) AS cnt
            FROM findings
            WHERE repo_id = ? AND experiment_id = ?
            GROUP BY UPPER({severity_expr})
            ORDER BY CASE UPPER({severity_expr})
              WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END
            """,
            (repo_id, resolved_exp_id),
        ) if findings_cols else []
        severity_score_select = "severity_score" if "severity_score" in findings_cols else "0 AS severity_score"
        order_by_clause = "severity_score DESC, id ASC" if "severity_score" in findings_cols else "id ASC"
        top_issue_rows = _safe_fetchall(
            f"""
            SELECT title, rule_id, {severity_score_select}
            FROM findings
            WHERE repo_id = ? AND experiment_id = ?
            ORDER BY {order_by_clause}
            LIMIT 8
            """,
            (repo_id, resolved_exp_id),
        ) if findings_cols else []
        issue_points = []
        if sev_rows:
            issue_points.append("<li>Severity mix: " + esc(', '.join(f"{r['sev']}={r['cnt']}" for r in sev_rows)) + "</li>")
        issue_points.extend([
            f"<li><strong>{esc((r['title'] or r['rule_id'] or 'Untitled finding'))}</strong> (score {r['severity_score']})</li>"
            for r in top_issue_rows
        ])
        if issue_points:
            overview_sections.append("<h2>⚠️ Key Issues</h2><ul>" + ''.join(issue_points) + "</ul>")

        # 7) Skeptic outputs
        skeptic_rows = _safe_fetchall(
            """
            SELECT sr.reviewer_type,
                   COUNT(*) AS reviews,
                   ROUND(AVG(sr.adjusted_score), 2) AS avg_adjusted,
                   SUM(CASE WHEN LOWER(COALESCE(sr.recommendation, 'confirm')) = 'escalate' THEN 1 ELSE 0 END) AS escalations,
                   SUM(CASE WHEN LOWER(COALESCE(sr.recommendation, 'confirm')) = 'downgrade' THEN 1 ELSE 0 END) AS downgrades,
                   SUM(CASE WHEN LOWER(COALESCE(sr.recommendation, 'confirm')) = 'dismiss' THEN 1 ELSE 0 END) AS dismissals
            FROM skeptic_reviews sr
            JOIN findings f ON f.id = sr.finding_id
            WHERE f.repo_id = ? AND f.experiment_id = ?
            GROUP BY sr.reviewer_type
            ORDER BY sr.reviewer_type
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("skeptic_reviews") and findings_cols else []
        skeptic_points = [
            f"<li><strong>{esc(r['reviewer_type'])}</strong>: reviews={r['reviews']}, avg score={r['avg_adjusted']}, escalations={r['escalations']}, downgrades={r['downgrades']}, dismissals={r['dismissals']}</li>"
            for r in skeptic_rows
        ]
        concern_rows = _safe_fetchall(
            """
            SELECT sr.reviewer_type, COALESCE(NULLIF(TRIM(sr.key_concerns), ''), NULLIF(TRIM(sr.reasoning), '')) AS note
            FROM skeptic_reviews sr
            JOIN findings f ON f.id = sr.finding_id
            WHERE f.repo_id = ? AND f.experiment_id = ?
              AND COALESCE(NULLIF(TRIM(sr.key_concerns), ''), NULLIF(TRIM(sr.reasoning), '')) IS NOT NULL
            ORDER BY sr.reviewed_at DESC
            LIMIT 6
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("skeptic_reviews") and findings_cols else []
        skeptic_points.extend([
            f"<li>{esc(r['reviewer_type'])}: {esc(r['note'])}</li>"
            for r in concern_rows
        ])
        if skeptic_points:
            overview_sections.append("<h2>🧠 Skeptic Output</h2><ul>" + ''.join(skeptic_points) + "</ul>")
    finally:
        conn.close()

    final_html = '<div class="markdown-content">' + ''.join(overview_sections) + '</div>' if overview_sections else ''
    return _db_render('tab_overview.html', overview_html=final_html, experiment_id=resolved_exp_id, repo_name=repo_name)


@app.route("/api/view/assets/<experiment_id>/<repo_name>")
def api_view_assets(experiment_id: str, repo_name: str):
    """Render the assets tab HTML.

    Supports an optional query parameter `include_hidden=1` to show resources
    that are normally hidden from the Assets view (generator/utility tokens and
    items marked not to be displayed on architecture charts). Identity/RBAC
    resources remain excluded (they belong in the Roles tab).
    """
    include_hidden = str(request.args.get('include_hidden', '')).lower() in ('1', 'true', 'yes')
    conn = _get_db()
    if conn is None:
        return _db_render("tab_assets.html", assets=[], providers=[], include_hidden=include_hidden, hidden_count=0, total=0, experiment_id=experiment_id, repo_name=repo_name)
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render(
                "tab_assets.html",
                assets=[],
                providers=[],
                include_hidden=include_hidden,
                hidden_count=0,
                total=0,
                experiment_id="",
                repo_name=repo_name,
                error=f"No completed scan found for {repo_name}.",
            )
        experiment_target = resolved_exp_id
        rows = conn.execute(
            """
            SELECT res.id, res.resource_type, res.resource_name, res.provider,
                   res.region, res.source_file, res.source_line_start,
                   res.discovered_by, res.discovery_method, res.status,
                   res.parent_resource_id,
                   COUNT(f.id) AS finding_count,
                   MAX(CASE f.base_severity
                       WHEN 'CRITICAL' THEN 5 WHEN 'HIGH' THEN 4
                       WHEN 'MEDIUM'   THEN 3 WHEN 'LOW'  THEN 2
                       WHEN 'INFO'     THEN 1 ELSE 0 END) AS max_sev_rank
            FROM resources res
            JOIN repositories repo ON res.repo_id = repo.id
            LEFT JOIN findings f ON f.resource_id = res.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            GROUP BY res.id
            ORDER BY res.provider, res.resource_type, res.resource_name
            """,
            (repo_name, experiment_id),
        ).fetchall()
        hidden_count = 0

        sev_labels = {5: "CRITICAL", 4: "HIGH", 3: "MEDIUM", 2: "LOW", 1: "INFO"}
        assets = []
        providers = set()
        provider_counts = {}
        has_unknown = False
        # Attempt to enrich with render category and display flag using resource_type_db
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from Scripts.Persist.resource_type_db import get_render_category, _derive  # type: ignore
        except Exception:
            get_render_category = None
            _derive = None

        # Build parent-child hierarchy index
        children_by_parent: dict = {}  # parent_id -> list of child asset dicts
        child_resource_ids: set = set()
        try:
            hierarchy_rows = conn.execute(
                """
                SELECT child.id AS child_id, child.parent_resource_id AS parent_id
                FROM resources child
                JOIN repositories repo ON child.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND child.parent_resource_id IS NOT NULL
                """,
                (repo_name, experiment_target),
            ).fetchall()
            for hr in hierarchy_rows:
                parent_id = hr['parent_id']
                child_resource_ids.add(hr['child_id'])
                if parent_id not in children_by_parent:
                    children_by_parent[parent_id] = []
                children_by_parent[parent_id].append(hr['child_id'])
        except Exception:
            pass

        for row in rows:
            a = dict(row)
            rank = a.pop("max_sev_rank") or 0
            a["worst_severity"] = sev_labels.get(rank, "")

            # Enrich with render_category and display_on_architecture_chart
            rtype = (a.get('resource_type') or '')
            try:
                if get_render_category:
                    a['render_category'] = get_render_category(conn, rtype)
                else:
                    a['render_category'] = ''
            except Exception:
                a['render_category'] = ''
            try:
                if _derive:
                    a['display_on_architecture_chart'] = _derive(rtype).get('display_on_architecture_chart', True)
                else:
                    a['display_on_architecture_chart'] = True
            except Exception:
                a['display_on_architecture_chart'] = True

            # Skip identity / RBAC resources — they are shown in the Roles tab
            skip_types = {
                'azurerm_role_assignment', 'azurerm_role_definition',
                'aws_iam_role', 'aws_iam_policy', 'aws_iam_user_policy',
                'google_project_iam_member', 'google_project_iam_binding',
                'kubernetes_cluster_role', 'kubernetes_role_binding', 'kubernetes_cluster_role_binding',
                'managed_identity', 'user_assigned_identity', 'service_account', 'service_principal'
            }
            rtype_lower = (rtype or '').lower()
            # Generator/utility tokens (e.g., random_*, time_*, null_resource) are typically hidden from the Assets view
            hidden_tokens = ("random_", "time_", "null_resource")

            # Identity / RBAC resources are hidden by default (they appear in Roles).
            # When `include_hidden` is true, include them in the Assets view so the
            # displayed list can match the repository's total resource count.
            if (a.get('render_category', '').lower() == 'identity') or (rtype_lower in skip_types):
                if not include_hidden:
                    hidden_count += 1
                    continue
                # else: include identity resources when include_hidden is True

            # If include_hidden is false, hide generator/utility types and any resource
            # explicitly marked as not to be displayed on architecture diagrams.
            if not include_hidden:
                if any(tok in rtype_lower for tok in hidden_tokens) or (not a.get('display_on_architecture_chart', True)):
                    hidden_count += 1
                    continue

            # Normalize provider: use 'unknown' when missing
            prov = (a.get("provider") or "unknown")
            a["provider"] = prov
            # Count providers (preserve original casing in keys)
            provider_counts[prov] = provider_counts.get(prov, 0) + 1
            if prov.lower() == 'unknown':
                has_unknown = True
            providers.add(prov)

            # Hierarchy fields
            a['children_count'] = len(children_by_parent.get(a.get('id'), []))
            a['is_child'] = a.get('id') in child_resource_ids

            assets.append(a)

        # Recompute children_count to reflect only assets included in this view
        included_children_counts: dict = {}
        for aa in assets:
            parent_id = aa.get('parent_resource_id')
            if parent_id:
                included_children_counts[parent_id] = included_children_counts.get(parent_id, 0) + 1
        for aa in assets:
            aa['children_count'] = included_children_counts.get(aa.get('id'), 0)

        # Reorder: parents first, children immediately after their parent
        ordered_assets = []
        asset_by_id = {a['id']: a for a in assets if a.get('id') is not None}
        parent_assets = [a for a in assets if not a.get('is_child')]
        for parent in parent_assets:
            ordered_assets.append(parent)
            for child_id in children_by_parent.get(parent.get('id'), []):
                if child_id in asset_by_id:
                    ordered_assets.append(asset_by_id[child_id])
        # Add any remaining assets not yet included (orphaned children, etc.)
        added_ids = {a.get('id') for a in ordered_assets}
        for a in assets:
            if a.get('id') not in added_ids:
                ordered_assets.append(a)
        assets = ordered_assets

        # Ensure 'unknown' option exists if any asset lacked a provider
        if has_unknown:
            providers.add('unknown')
            provider_counts.setdefault('unknown', 0)
        return _db_render(
            "tab_assets.html",
            assets=assets,
            providers=sorted(providers),
            provider_counts=provider_counts,
            repo_name=repo_name,
            hidden_count=hidden_count,
            total=len(assets),
            include_hidden=include_hidden,
            experiment_id=experiment_target,
        )
    except Exception as exc:
        return _db_render("tab_assets.html", assets=[], providers=[], error=str(exc), hidden_count=0, total=0, include_hidden=include_hidden, experiment_id=experiment_id)
    finally:
        conn.close()


@app.route("/api/view/findings/<experiment_id>/<repo_name>")
def api_view_findings(experiment_id: str, repo_name: str):
    """Render the findings tab HTML."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_findings.html", findings=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_findings.html", findings=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        rows = conn.execute(
            """
            SELECT f.id, f.title, f.description, f.base_severity,
                   f.severity_score, f.rule_id, f.source_file, f.source_line_start,
                   f.resource_id, f.category
            FROM findings f
            JOIN repositories repo ON f.repo_id = repo.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            ORDER BY
                CASE f.base_severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4
                                WHEN 'INFO' THEN 5 ELSE 6 END,
                f.severity_score DESC
            """,
            (repo_name, target_exp),
        ).fetchall()
        findings = [dict(r) for r in rows]
        return _db_render("tab_findings.html", findings=findings)
    except Exception as exc:
        return _db_render("tab_findings.html", findings=[], error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/ingress/<experiment_id>/<repo_name>")
def api_view_ingress(experiment_id: str, repo_name: str):
    """Render the ingress tab HTML."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_ingress.html", ingress_resources=[], ingress_connections=[])
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_ingress.html", ingress_resources=[], ingress_connections=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        
        # Primary: exposure_analysis table (entry points and internet-facing resources)
        res_rows = []
        try:
            ea_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.id,
                    r.resource_name,
                    r.resource_type,
                    r.provider,
                    r.region,
                    r.source_file,
                    r.source_line_start,
                    ea.exposure_level AS exposure_type,
                    CASE 
                        WHEN ea.is_entry_point = 1 THEN 'entry_point'
                        WHEN ea.has_internet_path = 1 THEN 'internet_path'
                        ELSE 'internet_facing'
                    END AS exposure_value,
                    CASE WHEN ea.is_entry_point = 1 THEN 1 ELSE 0 END AS is_confirmed
                FROM exposure_analysis ea
                JOIN resources r ON ea.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND (ea.is_entry_point = 1 OR ea.has_internet_path = 1)
                ORDER BY r.resource_type, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            res_rows = list(ea_rows)
        except Exception as e:
            print(f"Warning: Could not fetch from exposure_analysis: {e}")
        
        # Secondary: findings with internet_exposure metadata
        existing_ids = {dict(r)['id'] for r in res_rows}
        try:
            findings_rows = conn.execute(
                """
                SELECT DISTINCT
                    COALESCE(parent.id, r.id) AS id,
                    COALESCE(parent.resource_name, r.resource_name) AS resource_name,
                    COALESCE(parent.resource_type, r.resource_type) AS resource_type,
                    COALESCE(parent.provider, r.provider) AS provider,
                    COALESCE(parent.region, r.region) AS region,
                    COALESCE(parent.source_file, r.source_file) AS source_file,
                    COALESCE(parent.source_line_start, r.source_line_start) AS source_line_start,
                    f.rule_id AS exposure_type,
                    'internet_exposure' AS exposure_value
                FROM findings f
                JOIN resources r ON f.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                JOIN finding_context fc ON fc.finding_id = f.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND fc.context_key = 'metadata.internet_exposure'
                  AND LOWER(fc.context_value) = 'true'
                ORDER BY COALESCE(parent.resource_type, r.resource_type), COALESCE(parent.resource_name, r.resource_name)
                """,
                (repo_name, target_exp),
            ).fetchall()
            for r in findings_rows:
                if dict(r)['id'] not in existing_ids:
                    res_rows.append(r)
                    existing_ids.add(dict(r)['id'])
        except Exception as e:
            print(f"Warning: Could not fetch findings: {e}")

        # Fallback: legacy start_ip_address context (firewall rules)
        try:
            fw_rows = conn.execute(
                """
                SELECT DISTINCT
                    COALESCE(parent.id, r.id) AS id,
                    COALESCE(parent.resource_name, r.resource_name) AS resource_name,
                    COALESCE(parent.resource_type, r.resource_type) AS resource_type,
                    COALESCE(parent.provider, r.provider) AS provider,
                    COALESCE(parent.region, r.region) AS region,
                    COALESCE(parent.source_file, r.source_file) AS source_file,
                    COALESCE(parent.source_line_start, r.source_line_start) AS source_line_start,
                    'firewall_0.0.0.0' AS exposure_type,
                    '0.0.0.0' AS exposure_value
                FROM findings f
                JOIN resources r ON f.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                JOIN finding_context fc ON fc.finding_id = f.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                 AND LOWER(fc.context_key) IN ('start_ip_address', 'start_ip', '$val')
                 AND fc.context_value = '0.0.0.0'
            """,
            (repo_name, target_exp),
        ).fetchall()
            # Merge, dedup by id
            for r in fw_rows:
                if dict(r)['id'] not in existing_ids:
                    res_rows.append(r)
                    existing_ids.add(dict(r)['id'])
        except Exception:
            pass

        # Add API operations and their parent gateways
        try:
            api_ops_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.id,
                    r.resource_name,
                    r.resource_type,
                    r.provider,
                    r.region,
                    r.source_file,
                    r.source_line_start,
                    parent.resource_name AS parent_gateway_name,
                    parent.resource_type AS parent_gateway_type,
                    'api_operation' AS exposure_type,
                    'api_endpoint' AS exposure_value
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type IN (
                    'azurerm_api_management_api_operation',
                    'aws_api_gateway_method',
                    'aws_api_gateway_resource',
                    'aws_apigatewayv2_route',
                    'google_api_gateway_gateway',
                    'oci_apigateway_deployment',
                    'alicloud_api_gateway_api'
                  )
                ORDER BY parent.resource_name, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            
            for r in api_ops_rows:
                r_dict = dict(r)
                if r_dict['id'] not in existing_ids:
                    res_rows.append(r)
                    existing_ids.add(r_dict['id'])
        except Exception as e:
            print(f"Warning: Could not fetch API operations: {e}")

        # Load API operations into context for Inline Ingress API section
        try:
            ops_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.resource_name as operation_name,
                    r.resource_type,
                    r.id,
                    r.parent_resource_id,
                    r.source_file,
                    r.source_line_start,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'custom_headers' LIMIT 1),
                        NULL
                    ) as custom_headers_json,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'is_ingress_endpoint' LIMIT 1),
                        'false'
                    ) as is_public,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'internet_access' LIMIT 1),
                        '—'
                    ) as internet_access,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'internet_access_signals' LIMIT 1),
                        ''
                    ) as internet_access_signals
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type IN ('azurerm_api_management_api', 'azurerm_api_management_api_operation')
                ORDER BY r.resource_type DESC, r.parent_resource_id, r.resource_name ASC
                """,
                (repo_name, target_exp),
            ).fetchall()
            operations = [dict(row) for row in ops_rows]
            operations_by_id = {op['id']: op for op in operations}
            children_by_parent = {}
            for op in operations:
                parent_id = op.get('parent_resource_id')
                if parent_id and parent_id in operations_by_id:
                    if parent_id not in children_by_parent:
                        children_by_parent[parent_id] = []
                    children_by_parent[parent_id].append(op['id'])

            # APIM exposure default: treat APIs as potentially exposed unless we can prove private.
            api_ids = [op['id'] for op in operations if op.get('resource_type') == 'azurerm_api_management_api']
            apim_props_by_id: dict[int, dict[str, str]] = {}
            if api_ids:
                placeholders = ",".join("?" for _ in api_ids)
                prop_rows = conn.execute(
                    f"""
                    SELECT resource_id, LOWER(property_key) AS k, COALESCE(property_value, '') AS v
                    FROM resource_properties
                    WHERE resource_id IN ({placeholders})
                    """,
                    tuple(api_ids),
                ).fetchall()
                for pr in prop_rows:
                    rid = pr['resource_id']
                    apim_props_by_id.setdefault(rid, {})[pr['k']] = pr['v']

            def _is_private_apim(props: dict[str, str]) -> bool:
                def _truthy(v: str) -> bool:
                    return str(v).strip().lower() in ('true', '1', 'yes', 'enabled')
                def _falsy(v: str) -> bool:
                    return str(v).strip().lower() in ('false', '0', 'no', 'disabled')

                for key, val in props.items():
                    k = (key or '').lower()
                    v = (val or '').lower()
                    if k in ('public_network_access', 'public_network_access_enabled') and _falsy(v):
                        return True
                    if k == 'virtual_network_type' and any(x in v for x in ('internal', 'injected', 'private')):
                        return True
                    if 'private_endpoint' in k and (_truthy(v) or 'enabled' in v):
                        return True
                    if 'private' in k and _truthy(v):
                        return True
                return False

            for op in operations:
                if op.get('resource_type') != 'azurerm_api_management_api':
                    continue
                current = (op.get('internet_access') or '').strip()
                if current and current != '—':
                    continue
                props = apim_props_by_id.get(op['id'], {})
                if _is_private_apim(props):
                    op['internet_access'] = 'false'
                    op['internet_access_signals'] = 'inferred: private APIM signals found in properties'
                else:
                    op['internet_access'] = 'true'
                    op['internet_access_signals'] = 'inferred: APIM assumed internet-exposed unless private evidence is present'

            # If API operations were not extracted as resources, infer child operations from OpenAPI specs.
            apim_api_rows = [op for op in operations if op.get('resource_type') == 'azurerm_api_management_api']
            has_any_children = any(children_by_parent.get(api['id']) for api in apim_api_rows)
            if apim_api_rows and not has_any_children:
                repo_entries = _resolve_repos()
                repo_path = None
                for ent in repo_entries:
                    if (ent.get('name') or '').lower() == repo_name.lower() and ent.get('found'):
                        repo_path = ent.get('path')
                        break

                if repo_path:
                    root = Path(str(repo_path))
                    openapi_candidates: list[Path] = []
                    for pat in ("*.openapi.yaml", "*.openapi.yml", "*openapi*.yaml", "*openapi*.yml"):
                        openapi_candidates.extend(root.rglob(pat))
                    # dedupe while preserving order
                    seen_paths = set()
                    openapi_files = []
                    for p in openapi_candidates:
                        sp = str(p)
                        if sp in seen_paths:
                            continue
                        seen_paths.add(sp)
                        openapi_files.append(p)

                    def _extract_openapi_ops(path: Path) -> list[tuple[str, int]]:
                        ops: list[tuple[str, int]] = []
                        try:
                            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
                        except Exception:
                            return ops
                        in_paths = False
                        current_path = None
                        for idx, ln in enumerate(lines, start=1):
                            if re.match(r'^\s*paths\s*:\s*$', ln):
                                in_paths = True
                                current_path = None
                                continue
                            if in_paths and re.match(r'^\s{0,1}[A-Za-z_]+\s*:\s*$', ln):
                                # likely moved to a top-level sibling section
                                in_paths = False
                                current_path = None
                                continue
                            if not in_paths:
                                continue
                            m_path = re.match(r'^\s{2,}(/[^\s:]+)\s*:\s*$', ln)
                            if m_path:
                                current_path = m_path.group(1)
                                continue
                            m_method = re.match(r'^\s{4,}(get|post|put|patch|delete|head|options)\s*:\s*$', ln, re.I)
                            if m_method and current_path:
                                ops.append((f"{m_method.group(1).upper()} {current_path}", idx))
                        return ops

                    extracted_by_file = {str(p): _extract_openapi_ops(p) for p in openapi_files}
                    extracted_by_file = {k: v for k, v in extracted_by_file.items() if v}

                    if extracted_by_file:
                        def _tokens(s: str) -> set[str]:
                            return {t for t in re.split(r'[^a-z0-9]+', (s or '').lower()) if len(t) >= 3}

                        unused_files = set(extracted_by_file.keys())
                        synthetic_id = -1
                        for api in apim_api_rows:
                            api_tokens = _tokens(str(api.get('operation_name') or ''))
                            best_file = None
                            best_score = -1
                            for f in list(unused_files):
                                stem_tokens = _tokens(Path(f).stem)
                                score = len(api_tokens.intersection(stem_tokens))
                                if score > best_score:
                                    best_score = score
                                    best_file = f
                            if best_file is None and len(unused_files) == 1:
                                best_file = next(iter(unused_files))
                            if best_file is None:
                                continue

                            parent_access = api.get('internet_access') or 'true'
                            parent_signal = api.get('internet_access_signals') or 'inherited from APIM API'
                            for op_name, line_no in extracted_by_file.get(best_file, []):
                                sid = synthetic_id
                                synthetic_id -= 1
                                op_row = {
                                    'id': sid,
                                    'operation_name': op_name,
                                    'resource_type': 'azurerm_api_management_api_operation',
                                    'parent_resource_id': api['id'],
                                    'source_file': str(Path(best_file).relative_to(root)).replace('\\\\', '/'),
                                    'source_line_start': line_no,
                                    'custom_headers_json': None,
                                    'is_public': 'false',
                                    'internet_access': parent_access,
                                    'internet_access_signals': f"{parent_signal}; inferred from OpenAPI operation",
                                }
                                operations.append(op_row)
                                operations_by_id[sid] = op_row
                                children_by_parent.setdefault(api['id'], []).append(sid)

                            unused_files.discard(best_file)
        except Exception as e:
            print(f"Warning: Could not load API operations for inline view: {e}")
            operations = []
            children_by_parent = {}
            operations_by_id = {}


        ingress_resources = [dict(r) for r in res_rows]

        # Build inbound connections showing the data flow path from Internet into the system
        # Try to build the full chain: Internet → APIM → API → K8s → Service
        ingress_connections = []
        try:
            # Get entry points first
            entry_points = conn.execute(
                """
                SELECT DISTINCT ea.resource_name, ea.resource_type, r.id as resource_id
                FROM exposure_analysis ea
                JOIN resources r ON ea.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND (ea.is_entry_point = 1 OR ea.has_internet_path = 1)
                """,
                (repo_name, target_exp),
            ).fetchall()
            
            # For each entry point, trace the connection chain
            for ep_row in entry_points:
                ep_dict = dict(ep_row)
                ep_name = ep_dict['resource_name']
                ep_id = ep_dict['resource_id']
                
                # Add Internet → Entry Point connection
                ingress_connections.append({
                    'source_name': 'Internet',
                    'target_name': ep_name,
                    'protocol': 'HTTPS',
                    'port': '443',
                    'authentication': 'Subscription Key' if 'api' in ep_dict['resource_type'].lower() else None,
                    'is_encrypted': 1,
                })
                # Persist inferred Internet relationship into resource_connections as an unconfirmed entry
                try:
                    # Use source_resource_id = 0 to indicate external Internet source (no resource record)
                    conn.execute(
                        "INSERT OR IGNORE INTO resource_connections (experiment_id, source_resource_id, target_resource_id, connection_type, protocol, port, auth_method, is_encrypted, inferred_internet) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (target_exp, 0, ep_id, 'internet_to', 'HTTPS', '443', ('Subscription Key' if 'api' in ep_dict['resource_type'].lower() else None), 1),
                    )
                    conn.commit()
                except Exception:
                    pass
                
                # Find what this entry point routes to
                downstream = conn.execute(
                    """
                    SELECT tgt.resource_name, tgt.resource_type, rc.protocol, rc.port, rc.auth_method, rc.is_encrypted
                    FROM resource_connections rc
                    JOIN resources tgt ON rc.target_resource_id = tgt.id
                    WHERE rc.source_resource_id = ? 
                      AND rc.connection_type IN ('routes_ingress_to', 'depends_on')
                    LIMIT 5
                    """,
                    (ep_id,),
                ).fetchall()
                
                for ds_row in downstream:
                    ds_dict = dict(ds_row)
                    ingress_connections.append({
                        'source_name': ep_name,
                        'target_name': ds_dict['resource_name'],
                        'protocol': ds_dict['protocol'] or 'HTTP',
                        'port': ds_dict['port'] or '80',
                        'authentication': ds_dict['auth_method'],
                        'is_encrypted': ds_dict['is_encrypted'],
                    })
        except Exception as e:
            print(f"Warning: Could not build connection chain: {e}")

        return _db_render(
            "tab_ingress.html",
            ingress_resources=ingress_resources,
            ingress_connections=ingress_connections,
            operations=operations,
            children_by_parent=children_by_parent,
            operations_by_id=operations_by_id,
        )
    except Exception as exc:
        return _db_render("tab_ingress.html", ingress_resources=[], ingress_connections=[], error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/egress/<experiment_id>/<repo_name>")
def api_view_egress(experiment_id: str, repo_name: str):
    """Render the egress tab HTML."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_egress.html", egress_connections=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_egress.html", egress_connections=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        
        # Build outbound connections by combining resource_connections and exposure_analysis
        egress_connections = []
        try:
            rc_rows = conn.execute(
                """
                SELECT DISTINCT 
                    COALESCE(
                        src.resource_name,
                        CASE WHEN rc.source_resource_id = 0 THEN 'Internet' END,
                        'Unknown source'
                    ) AS source_name,
                    src.resource_type AS source_type,
                    COALESCE(tgt.resource_name, rc.target_external, 'External Target') AS target_name,
                    COALESCE(tgt.resource_type, 'external') AS target_type,
                    rc.protocol,
                    rc.port,
                    rc.auth_method,
                    rc.is_encrypted,
                    rc.connection_type,
                    rc.connection_metadata,
                    rc.target_external AS target_domain,
                    COALESCE(repo_src.experiment_id, repo_tgt.experiment_id) AS conn_experiment_id
                FROM resource_connections rc
                LEFT JOIN resources src ON rc.source_resource_id = src.id
                LEFT JOIN resources tgt ON rc.target_resource_id = tgt.id
                LEFT JOIN repositories repo_src ON src.repo_id = repo_src.id
                LEFT JOIN repositories repo_tgt ON tgt.repo_id = repo_tgt.id
                WHERE (
                    (repo_src.id IS NOT NULL AND LOWER(repo_src.repo_name) = LOWER(?) AND repo_src.experiment_id = ?)
                    OR
                    (repo_src.id IS NULL AND repo_tgt.id IS NOT NULL AND LOWER(repo_tgt.repo_name) = LOWER(?) AND repo_tgt.experiment_id = ?)
                )
                  AND COALESCE(rc.connection_type, '') NOT IN ('contains', 'orchestrates', 'composed_of', 'parent_child')
                ORDER BY source_name, target_name
                """,
                (repo_name, target_exp, repo_name, target_exp),
            ).fetchall()
            for row in rc_rows:
                entry = dict(row)
                purpose = entry.get("connection_type", "") or ""
                tgt_type = (entry.get("target_type") or "").lower()
                if "topic" in tgt_type or "queue" in tgt_type:
                    purpose = "messaging"
                elif "database" in tgt_type or "sql" in tgt_type:
                    purpose = "data storage"
                elif "storage" in tgt_type:
                    purpose = "blob storage"
                elif "vault" in tgt_type:
                    purpose = "secrets"
                elif "insight" in tgt_type or "monitor" in tgt_type:
                    purpose = "telemetry"
                elif "internet" in (entry.get("connection_type") or ""):
                    purpose = "internet egress"
                entry["connection_purpose"] = purpose or entry.get("connection_type")
                egress_connections.append(entry)
        except Exception as e:
            print(f"Warning: Could not fetch resource_connections: {e}")

        # Exposure analysis: include data-legged destinations (normalized_role = 'data')
        try:
            ea_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.resource_name AS source_name,
                    r.resource_type AS source_type,
                    ea.resource_name AS target_name,
                    ea.resource_type AS target_type,
                    ea.protocol,
                    ea.port,
                    ea.auth_method,
                    ea.is_encrypted,
                    COALESCE(ea.destination_type, ea.normalized_role) AS connection_purpose,
                    ea.endpoint AS target_domain
                FROM exposure_analysis ea
                JOIN repositories repo ON ea.repo_id = repo.id
                LEFT JOIN resources r ON ea.resource_id = r.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND ea.normalized_role = 'data'
                ORDER BY r.resource_name, ea.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            egress_connections.extend([dict(r) for r in ea_rows])
        except Exception as e:
            print(f"Warning: Could not fetch exposure_analysis for egress: {e}")

        # Tertiary: finding_context for legacy outbound connections (legacy support)
        try:
            fc_rows = conn.execute(
                """
                SELECT DISTINCT r.id, r.resource_name AS source_name, r.resource_type AS source_type, r.provider,
                       r.source_file, r.source_line_start,
                       fc.context_key AS connection_purpose,
                       fc.context_value AS target_domain,
                       NULL AS target_name,
                       NULL AS target_type,
                       NULL AS protocol,
                       NULL AS port,
                       NULL AS auth_method,
                       NULL AS is_encrypted
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                JOIN findings f ON f.resource_id = r.id
                JOIN finding_context fc ON fc.finding_id = f.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND LOWER(fc.context_key) IN (
                    'server_id', 'server_name', 'namespace_id', 'namespace_name',
                    'key_vault_id', 'account_name', 'storage_account_name',
                    'kubernetes_cluster_id', 'cluster_id', 'virtual_machine_id',
                    'virtual_network_name', 'connection_string', 'endpoint', 'host'
                  )
                  AND fc.context_value IS NOT NULL
                  AND fc.context_value != ''
                ORDER BY r.provider, r.resource_type, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            egress_connections.extend([dict(r) for r in fc_rows])
        except Exception as e:
            print(f"Warning: Could not fetch finding_context: {e}")
        
        return _db_render("tab_egress.html", egress_connections=egress_connections)
    except Exception as exc:
        return _db_render("tab_egress.html", egress_connections=[], error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/roles/<experiment_id>/<repo_name>")
def api_view_roles(experiment_id: str, repo_name: str):
    """Render the roles & permissions tab HTML."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_roles.html", roles=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_roles.html", roles=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        rows = conn.execute(
            """
            SELECT
                res.id,
                res.resource_name AS identity_name,
                res.resource_type AS role_type,
                res.provider,
                res.source_file,
                MAX(CASE WHEN LOWER(rp.property_key) IN (
                    'scope_resource', 'scope', 'scope_name', 'scope_id',
                    'resource_id', 'target_resource_id', 'target_resource_name'
                ) THEN rp.property_value END) AS scope_prop,
                parent.resource_name AS parent_name,
                MAX(CASE WHEN LOWER(rp.property_key) IN ('subscription_scope', 'subscription_scope_name') THEN rp.property_value END) AS subscription_scope,
                MAX(CASE WHEN rp.property_key = 'principal_id' THEN rp.property_value END) AS principal_id,
                MAX(CASE WHEN rp.property_key IN ('permissions','role_definition_name','role') THEN rp.property_value END) AS permissions,
                MAX(CASE WHEN LOWER(rp.property_key) = 'is_excessive' THEN rp.property_value END) AS is_excessive
            FROM resources res
            JOIN repositories repo ON res.repo_id = repo.id
            LEFT JOIN resource_properties rp ON rp.resource_id = res.id
            LEFT JOIN resources parent ON res.parent_resource_id = parent.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
              AND LOWER(res.resource_type) IN (
                    'azurerm_role_assignment', 'azurerm_role_definition',
                    'azurerm_user_assigned_identity', 'azurerm_managed_identity',
                    'azurerm_subscription',
                    'aws_iam_role', 'aws_iam_policy', 'aws_iam_user_policy',
                    'google_project_iam_member', 'google_project_iam_binding',
                    'google_service_account', 'google_service_account_key',
                    'kubernetes_cluster_role', 'kubernetes_role_binding',
                    'kubernetes_cluster_role_binding', 'kubernetes_service_account',
                    'managed_identity', 'user_assigned_identity',
                    'service_account', 'service_principal'
              )
            GROUP BY res.id, parent.resource_name
            ORDER BY res.provider, res.resource_type, res.resource_name
            """,
            (repo_name, target_exp),
        ).fetchall()
        roles = []
        for r in rows:
            entry = dict(r)
            scope_candidates = [
                (entry.pop("scope_prop", None) or "").strip(),
                (entry.pop("subscription_scope", None) or "").strip(),
                (entry.pop("parent_name", None) or "").strip(),
                entry.get("resource_name", ""),
                entry.get("identity_name", ""),
            ]
            entry["resource_name"] = next((s for s in scope_candidates if s), "")
            roles.append(entry)
        
        # Add API subscriptions and keys
        try:
            api_keys_rows = conn.execute(
                """
                SELECT 
                    sub.id,
                    sub.resource_name AS identity_name,
                    sub.resource_type AS role_type,
                    sub.provider,
                    sub.source_file,
                    NULL AS principal_id,
                    COALESCE(
                        parent_api.resource_name,
                        parent_gw.resource_name,
                        'API Gateway'
                    ) AS resource_name,
                    GROUP_CONCAT(DISTINCT ops.resource_name) AS permissions,
                    NULL AS is_excessive
                FROM resources sub
                JOIN repositories repo ON sub.repo_id = repo.id
                LEFT JOIN resources parent_api ON sub.parent_resource_id = parent_api.id
                LEFT JOIN resources parent_gw ON parent_api.parent_resource_id = parent_gw.id
                LEFT JOIN resources ops ON ops.parent_resource_id = parent_api.id
                    AND ops.resource_type IN (
                        'azurerm_api_management_api_operation',
                        'aws_api_gateway_method',
                        'aws_apigatewayv2_route'
                    )
                WHERE LOWER(repo.repo_name) = LOWER(?) AND sub.experiment_id = ?
                  AND sub.resource_type IN (
                    'azurerm_api_management_subscription',
                    'azurerm_api_management_api',
                    'aws_api_gateway_api_key',
                    'aws_api_gateway_usage_plan',
                    'aws_api_gateway_usage_plan_key',
                    'google_api_gateway_api_config',
                    'google_api_gateway_api',
                    'oci_identity_api_key',
                    'oci_identity_auth_token',
                    'alicloud_api_gateway_app',
                    'alicloud_ram_access_key'
                  )
                GROUP BY sub.id
                ORDER BY sub.provider, sub.resource_type, sub.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            
            for r in api_keys_rows:
                r_dict = dict(r)
                # Format permissions to show accessible operations
                ops = r_dict.get('permissions')
                if ops:
                    r_dict['permissions'] = f"Access to operations: {ops}"
                else:
                    r_dict['permissions'] = f"Access to {r_dict.get('resource_name', 'API')}"
                roles.append(r_dict)
        except Exception as e:
            print(f"Warning: Could not fetch API keys/subscriptions: {e}")
        
        # Convert is_excessive to integer if stored as string
        for role in roles:
            val = role.get("is_excessive")
            if val is not None:
                role["is_excessive"] = 1 if str(val).lower() in ("1", "true", "yes") else 0
        return _db_render("tab_roles.html", roles=roles)
    except Exception as exc:
        return _db_render("tab_roles.html", roles=[], error=str(exc))
    finally:
        conn.close()




@app.route("/api/view/containers/<experiment_id>/<repo_name>")
def api_view_containers(experiment_id: str, repo_name: str):
    """Render the containers tab with base images and deployment info."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_containers.html", containers=[], container_providers=[], experiment_id=experiment_id, repo_name=repo_name)
    
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_containers.html", containers=[], container_providers=[], experiment_id="", repo_name=repo_name)
        
        # Query for Dockerfile resources and get their properties (base images)
        rows = conn.execute("""
            SELECT 
                r.id,
                r.resource_name,
                r.resource_type,
                r.provider,
                r.source_file,
                MAX(CASE WHEN rp.property_key = 'image' THEN rp.property_value END) AS image,
                MAX(CASE WHEN rp.property_key = 'registry' THEN rp.property_value END) AS registry,
                MAX(CASE WHEN rp.property_key = 'dockerfile' THEN rp.property_value END) AS dockerfile
            FROM resources r
            JOIN repositories repo ON r.repo_id = repo.id
            LEFT JOIN resource_properties rp ON rp.resource_id = r.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
              AND (
                                     LOWER(r.resource_type) LIKE '%docker%' 
                                     OR LOWER(r.resource_type) LIKE '%container%'
                                     OR LOWER(r.resource_type) LIKE '%image%'
                                     OR LOWER(r.resource_type) LIKE '%helm%'
                                     OR LOWER(r.resource_name) LIKE '%dockerfile%'
                   OR EXISTS (
                       SELECT 1 FROM resource_properties rp_d
                                             WHERE rp_d.resource_id = r.id
                                                 AND rp_d.property_key IN ('dockerfile', 'image', 'registry')
                   )
              )
            GROUP BY r.id, r.resource_name, r.resource_type, r.provider, r.source_file
            ORDER BY r.provider, r.resource_name
        """, (repo_name, resolved_exp_id)).fetchall()
        
        containers = []
        providers = set()
        for row in rows:
            container = dict(row)
            container_type = "Dockerfile" if "dockerfile" in container.get("resource_type", "").lower() else "Container"
            container["container_type"] = container_type
            providers.add(container.get("provider", "unknown") or "unknown")
            container["dockerfile_ref"] = container.get("dockerfile") or container.get("source_file")
            containers.append(container)
        
        # Attempt to resolve repository path so we can read Dockerfiles and extract base FROM images
        repo_path = None
        try:
            for e in _resolve_repos():
                if e.get('name', '').lower() == repo_name.lower() and e.get('found'):
                    repo_path = Path(e.get('path'))
                    break
        except Exception:
            repo_path = None

        for c in containers:
            c['base_images'] = []
            c['base_images_search'] = ''
            if not c.get('dockerfile_ref') and c.get('source_file'):
                c['dockerfile_ref'] = c.get('source_file')

        if repo_path:
            parsed_by_path: dict[str, list[dict]] = {}
            for c in containers:
                df = c.get('dockerfile') or (c.get('source_file') if (c.get('source_file') or '').lower().endswith('dockerfile') else None)
                if not df:
                    continue
                c['dockerfile_ref'] = df
                df_path = repo_path / df
                if not df_path.exists():
                    df_path = repo_path / df.lstrip('./')
                if df_path.exists():
                    cache_key = str(df_path.resolve())
                    if cache_key not in parsed_by_path:
                        parsed_by_path[cache_key] = _get_base_images_from_dockerfile(df_path)
                    c['base_images'] = parsed_by_path[cache_key]

        for c in containers:
            c['base_images_search'] = " ".join(
                (img.get('image') if isinstance(img, dict) else str(img))
                for img in (c.get('base_images') or [])
                if (img.get('image') if isinstance(img, dict) else img)
            )

        base_image_map: dict[str, dict] = {}
        for c in containers:
            ref = c.get('dockerfile_ref') or c.get('source_file') or '—'
            for image in c.get('base_images', []):
                img_name = image if isinstance(image, str) else image.get('image')
                line_no = None
                if isinstance(image, dict):
                    img_name = image.get('image')
                    line_no = image.get('line')
                if not img_name:
                    continue
                entry = base_image_map.setdefault(img_name, {
                    "image": img_name,
                    "usage_count": 0,
                    "containers": set(),
                    "references": {},
                })
                entry["usage_count"] += 1
                entry["containers"].add(c.get('resource_name') or '—')

                ref_entry = entry["references"].setdefault(ref, {
                    "reference": ref,
                    "count": 0,
                    "containers": set(),
                    "lines": set(),
                })
                ref_entry["count"] += 1
                ref_entry["containers"].add(c.get('resource_name') or '—')
                if line_no:
                    ref_entry["lines"].add(int(line_no))

        base_image_usages = []
        for img_name, entry in base_image_map.items():
            refs = []
            for ref_item in entry["references"].values():
                refs.append({
                    "reference": ref_item["reference"],
                    "count": ref_item["count"],
                    "container_count": len(ref_item["containers"]),
                    "lines": sorted(ref_item["lines"]),
                })

            refs.sort(key=lambda r: (-r["count"], r["reference"].lower()))
            preview_limit = 8
            base_image_usages.append({
                "image": img_name,
                "usage_count": entry["usage_count"],
                "container_count": len(entry["containers"]),
                "reference_count": len(refs),
                "reference_preview": refs[:preview_limit],
                "remaining_reference_count": max(0, len(refs) - preview_limit),
            })

        base_image_usages.sort(key=lambda entry: (-entry["usage_count"], entry["image"].lower()))

        return _db_render(
            "tab_containers.html",
            containers=containers,
            container_providers=sorted(providers),
            base_image_usages=base_image_usages,
            experiment_id=resolved_exp_id,
            repo_name=repo_name,
        )
    except Exception as exc:
        return _db_render("tab_containers.html", containers=[], container_providers=[], error=str(exc))
    finally:
        conn.close()



@app.route("/api/view/ports/<experiment_id>/<repo_name>")
def api_view_ports(experiment_id: str, repo_name: str):
    """Render the ports tab with port/protocol inventory."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_ports.html", ports=[], experiment_id=experiment_id, repo_name=repo_name)
    
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_ports.html", ports=[], experiment_id="", repo_name=repo_name)
        
        # Query resource connections for port information with enhanced context
        rows = conn.execute("""
            SELECT DISTINCT
                r_src.resource_name as source_name,
                r_src.resource_type as source_type,
                r_tgt.resource_name as target_name,
                rc.port,
                rc.protocol,
                rc.auth_method,
                CASE 
                    WHEN rp.property_value LIKE '%internet%' OR rp.property_value LIKE '%0.0.0.0%' THEN 1
                    ELSE 0
                END as is_internet_exposed,
                CASE
                    WHEN rc.protocol IN ('HTTPS', 'HTTP', 'TCP', 'UDP', 'TLS') THEN 'verified'
                    WHEN rc.protocol IN ('amqp', 'AMQP') THEN 'assumed'
                    ELSE 'inferred'
                END as protocol_confidence
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            JOIN repositories repo ON r_src.repo_id = repo.id
            LEFT JOIN resource_properties rp ON r_src.id = rp.resource_id AND rp.property_key = 'internet_access'
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
              AND (rc.port IS NOT NULL OR rc.protocol IS NOT NULL)
            ORDER BY COALESCE(rc.port, 0), r_src.resource_name
        """, (repo_name, resolved_exp_id)).fetchall()
        
        ports = [dict(row) for row in rows]
        
        return _db_render("tab_ports.html", ports=ports, experiment_id=resolved_exp_id, repo_name=repo_name)
    except Exception as exc:
        return _db_render("tab_ports.html", ports=[], error=str(exc))
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


