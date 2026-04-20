#!/usr/bin/env python3
"""Triage-Saurus web UI — Flask server for repo scanning and Mermaid diagram generation."""

from __future__ import annotations

import json
import html
import contextvars
import re
import sqlite3
import shlex
import shutil
import subprocess
import sys
import os
import select
import tempfile
import time
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, Response, render_template, request, stream_with_context, jsonify

app = Flask(__name__)

# Jinja2 custom filters
import os as _os
from markupsafe import Markup as _Markup
app.jinja_env.filters["basename"] = lambda p: _os.path.basename(p or "") if p else ""

def _format_list_text(text):
    """Convert semicolon-separated text to HTML list if it contains multiple items."""
    if not text or not isinstance(text, str):
        return text
    # Check if text contains semicolons (likely a list)
    if ';' in text:
        items = [item.strip() for item in text.split(';') if item.strip()]
        if len(items) > 1:
            # Render as list
            list_items = ''.join(f"<li>{_html.escape(item)}</li>" for item in items)
            return _Markup(f"<ul style=\"margin: 6px 0; padding-left: 20px;\">{list_items}</ul>")
    return text

import html as _html
app.jinja_env.filters["format_list"] = _format_list_text

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "Scripts"
PIPELINE = SCRIPTS / "Utils" / "run_pipeline.py"
ENRICH_FINDINGS = SCRIPTS / "Enrich" / "enrich_findings.py"
RUN_SKEPTICS = SCRIPTS / "Utils" / "run_skeptics.py"
GENERATE_PROJECT_OVERVIEW = SCRIPTS / "Enrich" / "generate_project_overview.py"
EXPERIMENTS_DIR = REPO_ROOT / "Output" / "Learning" / "experiments"
INTAKE_REPOS = REPO_ROOT / "Intake" / "ReposToScan.txt"
DB_PATH = REPO_ROOT / "Output" / "Data" / "cozo.db"

# DB helpers are used to persist Copilot-generated overview metadata.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS / "Utils"))
from Scripts.Persist import db_helpers
from Scripts.Generate.internet_exposure_detector import InternetExposureDetector

# Load prompt builder for agent instructions
try:
    from Scripts.Utils import prompt_builder
    # Pre-load agent instructions at module level for performance
    AGENT_INSTRUCTIONS = {
        "SecurityAgent": prompt_builder.load_agent_instruction("SecurityAgent"),
        "DevSkeptic": prompt_builder.load_agent_instruction("DevSkeptic"),
        "PlatformSkeptic": prompt_builder.load_agent_instruction("PlatformSkeptic"),
        "ContextDiscoveryAgent": prompt_builder.load_agent_instruction("ContextDiscoveryAgent"),
        "ArchitectureAgent": prompt_builder.load_agent_instruction("ArchitectureAgent"),
        "ArchitectureValidationAgent": prompt_builder.load_agent_instruction("ArchitectureValidationAgent"),
    }
    PROMPT_BUILDER_AVAILABLE = True
except Exception as e:
    print(f"Warning: Could not load prompt_builder or agent instructions: {e}")
    AGENT_INSTRUCTIONS = {}
    PROMPT_BUILDER_AVAILABLE = False

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
_ACTIVE_AI_JOB_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar("ACTIVE_AI_JOB_KEY", default=None)


def _ai_job_key(experiment_id: str, repo_name: str) -> str:
    return f"{experiment_id}:{repo_name.lower()}"


def _ai_raw_output_file(key: str) -> Path:
    """Return the per-job raw Copilot output file."""
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return REPO_ROOT / "Output" / "AILogs" / f"{safe_key}-raw.txt"


def _touch_ai_job_activity(key: str) -> None:
    """Record that a job is still actively producing output."""
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job:
            return
        job["last_activity_at"] = time.time()
        _AI_ANALYSIS_JOBS[key] = job


def _extract_rules_from_llm_output(text: str) -> list[dict]:
    """Extract opengrep rules from LLM output that may be malformed/decorated.

    The Copilot CLI wraps responses with a bullet prefix, formats separators as
    lines of dashes, and word-wraps block scalars — all of which break standard
    YAML parsing.  This function uses regex field extraction to avoid those
    indentation problems entirely.
    """
    import textwrap as _textwrap

    # Strip Copilot CLI decorations: bullet chars and markdown fences
    text = re.sub(r'^[\u25cf\u2022\*]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```[^\n]*', '', text)

    # Split into rule blocks on separator lines (10+ dashes) or literal ---
    raw_blocks = re.split(r'\n\s*-{10,}\s*\n|\n---\n', text)

    seen_ids: set[str] = set()
    rules: list[dict] = []

    for block in raw_blocks:
        block = _textwrap.dedent(block).strip()
        if not block or 'id:' not in block:
            continue

        m_id = re.search(r'\bid:\s*(\S+)', block)
        if not m_id:
            continue
        rule_id = m_id.group(1).strip().strip('"\'')
        if not re.match(r'^[a-z][a-z0-9-]*$', rule_id) or rule_id in seen_ids:
            continue
        seen_ids.add(rule_id)

        m_sev = re.search(r'\bseverity:\s*(\S+)', block)
        severity = m_sev.group(1).strip().strip('"\'').upper() if m_sev else 'WARNING'
        if severity not in ('INFO', 'WARNING', 'ERROR'):
            severity = 'WARNING'

        m_lang = re.search(r'\blanguages:\s*(.+)', block)
        languages = ['python']
        if m_lang:
            found = re.findall(r'[a-z]+', m_lang.group(1))
            if found:
                languages = found

        m_msg = re.search(r'\bmessage:\s*(?:\|\s*)?\n?(.*?)(?=\n\s*\w[\w-]*:|\Z)', block, re.DOTALL)
        message = ''
        if m_msg:
            raw_msg = re.sub(r'^\s*\|\s*\n', '', m_msg.group(1))
            message = ' '.join(raw_msg.split()).strip()

        m_pat = re.search(r'\bpattern:\s*(?:\|\s*)?\n(.*?)(?=\n\s*\w[\w-]*:|\Z)', block, re.DOTALL)
        pattern_code = ''
        if m_pat:
            pat_lines = [l for l in m_pat.group(1).splitlines() if l.strip()]
            pattern_code = _textwrap.dedent('\n'.join(pat_lines)).strip() if pat_lines else ''

        rule: dict = {
            'id': rule_id,
            'message': message or f'Security issue detected: {rule_id}',
            'severity': severity,
            'languages': languages,
        }
        if pattern_code:
            rule['patterns'] = [{'pattern': pattern_code}]

        metadata: dict = {'category': 'security'}
        m_cwe = re.search(r'cwe:\s*(CWE-\d+)', block, re.IGNORECASE)
        if m_cwe:
            metadata['cwe'] = m_cwe.group(1).upper()
        m_sub = re.search(r'subcategory:\s*\[([^\]]+)\]', block)
        if m_sub:
            metadata['subcategory'] = [s.strip() for s in m_sub.group(1).split(',')]
        m_tech = re.search(r'technology:\s*\[([^\]]+)\]', block)
        if m_tech:
            metadata['technology'] = [s.strip() for s in m_tech.group(1).split(',')]
        rule['metadata'] = metadata

        rules.append(rule)

    return rules

# ── Open question auto-resolver ──────────────────────────────────────────────
# Patterns that indicate a value is a placeholder, not a real credential
_PLACEHOLDER_PATTERNS = re.compile(
    r"""
    <[^>]+>                          # <placeholder>, <your-server>, etc.
    | \[your[^\]]*\]                 # [your-value], [your-password]
    | \{your[^\}]*\}                 # {your_token}
    | \byour[-_]?\w+                 # your-server, your_password
    | \bYOUR[_-]?\w+                 # YOUR_SECRET, YOUR-TOKEN
    | \bCHANGEME\b                   # CHANGEME
    | \bREPLACEME\b                  # REPLACEME
    | \bTODO\b                       # TODO
    | \bFIXME\b                      # FIXME
    | \bexample\.com\b               # example.com
    | \blocalhost\b                  # localhost
    | \b127\.0\.0\.1\b               # 127.0.0.1
    | \bxxx+\b                       # xxx, xxxx
    | \*{3,}                         # *****, ****
    | \bpassword123\b                # password123
    | \btest-?password\b             # testpassword, test-password
    | \bchange[_-]?me\b              # changeme
    | \bnot[_-]?a[_-]?real\b        # not-a-real-credential
    | \bsample\b                     # sample
    | \bdummy\b                      # dummy
    | \bfake\b                       # fake
    | \bplaceholder\b                # placeholder
    | \bdemo\b                       # demo
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Patterns that suggest a value might be a real credential
_REAL_CREDENTIAL_PATTERNS = re.compile(
    r"""
    # Azure storage / service bus connection string anatomy
    DefaultEndpointsProtocol=https?;AccountName=[^;]{4,}
    | Endpoint=sb://[a-z0-9-]+\.servicebus\.windows\.net
    # SQL Server connection string with non-placeholder server
    | Server=tcp:[a-z0-9-]+\.database\.windows\.net
    | Data\s+Source=[a-z0-9-]+\.database\.windows\.net
    # Long random-looking tokens (hex, base64) — 20+ chars
    | (?<![a-zA-Z0-9_-])(?:[A-Za-z0-9+/]{20,}={0,2})(?![a-zA-Z0-9+/=])
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CREDENTIAL_QUESTION_PATTERNS = re.compile(
    r"\b(real|actual|genuine|live|production|valid|rotated|placeholder|fake|dummy|test)\b.*"
    r"\b(credential|secret|password|token|key|connection.?string|api.?key)\b"
    r"|\b(credential|secret|password|token|key|connection.?string|api.?key)\b.*"
    r"\b(real|actual|genuine|live|production|valid|rotated|placeholder|fake|dummy|test)\b",
    re.IGNORECASE,
)


def _resolve_repo_path(repo_name: str) -> Path | None:
    """Find the actual directory for a repo given its name."""
    for root in _SEARCH_ROOTS:
        candidate = root / repo_name
        if candidate.is_dir():
            return candidate
    return None


def _read_file_snippet(repo_path: Path, rel_file: str, around_line: int | None, context: int = 10) -> str:
    """Read up to `context` lines around `around_line` from a file in the repo."""
    fp = repo_path / rel_file if not Path(rel_file).is_absolute() else Path(rel_file)
    if not fp.exists():
        # Try stripping leading path components
        for part_count in range(1, 5):
            parts = Path(rel_file).parts
            if len(parts) > part_count:
                candidate = repo_path / Path(*parts[part_count:])
                if candidate.exists():
                    fp = candidate
                    break
        else:
            return ""
    try:
        lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        if around_line and around_line > 0:
            start = max(0, around_line - context - 1)
            end = min(len(lines), around_line + context)
        else:
            start, end = 0, min(50, len(lines))
        return "\n".join(lines[start:end])
    except OSError:
        return ""


def _auto_resolve_open_questions(
    questions: list[dict],
    experiment_id: str,
    repo_name: str,
    conn,
) -> dict[str, dict]:
    """
    Try to auto-answer open questions through static analysis.

    Returns a mapping of question_text_lower → {answer, confidence, rationale}
    for questions that could be resolved.  Persists results to subscription_context
    with answered_by='auto_analysis'.
    """
    resolved: dict[str, dict] = {}
    repo_path = _resolve_repo_path(repo_name)

    for q in questions:
        text = (q.get("question") or "").strip()
        if not text:
            continue

        answer = confidence = rationale = None

        # ── Category: credential reality check ────────────────────────────
        if _CREDENTIAL_QUESTION_PATTERNS.search(text):
            file_hint = (q.get("file") or "").strip()
            line_hint = q.get("line")
            snippet = ""
            if repo_path and file_hint:
                snippet = _read_file_snippet(repo_path, file_hint, line_hint)

            if snippet:
                has_placeholder = bool(_PLACEHOLDER_PATTERNS.search(snippet))
                has_real = bool(_REAL_CREDENTIAL_PATTERNS.search(snippet))

                if has_placeholder and not has_real:
                    answer = "No"
                    confidence = 0.9
                    rationale = (
                        "Static analysis of the referenced file found placeholder patterns "
                        f"(e.g. <placeholder>, YOUR_, example.com) in {file_hint or 'the file'} — "
                        "these are not real credentials."
                    )
                elif has_real and not has_placeholder:
                    answer = "Yes"
                    confidence = 0.75
                    rationale = (
                        "Static analysis found what appears to be a real connection string / credential "
                        f"in {file_hint or 'the file'} — review required."
                    )
                elif has_real and has_placeholder:
                    answer = "Don't know"
                    confidence = 0.4
                    rationale = (
                        f"Mixed signals in {file_hint or 'the file'}: both placeholder-like and "
                        "real-looking credential patterns detected — manual review recommended."
                    )
                # else: no signals found — leave unanswered

        if answer is None:
            continue  # Could not auto-resolve

        resolved[text.lower()] = {
            "answer": answer,
            "confidence": confidence,
            "rationale": rationale,
            "question": text,
        }

        # Persist to subscription_context
        try:
            import datetime as _dt
            now = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            existing = conn.execute(
                """
                SELECT id FROM subscription_context
                WHERE experiment_id = ? AND scope_key = 'repo'
                  AND LOWER(COALESCE(repo_name,'')) = LOWER(?)
                  AND LOWER(question) = LOWER(?)
                LIMIT 1
                """,
                (experiment_id, repo_name, text),
            ).fetchone()
            note = f"{answer} — {rationale}"
            if existing:
                conn.execute(
                    """
                    UPDATE subscription_context
                    SET answer = ?, answered_by = 'auto_analysis', confidence = ?,
                        tags = 'open_question,auto_resolved', updated_at = ?
                    WHERE id = ?
                    """,
                    (note, confidence, now, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO subscription_context
                    (experiment_id, scope_key, repo_name, question, answer,
                     answered_by, confidence, tags, created_at, updated_at)
                    VALUES (?, 'repo', ?, ?, ?, 'auto_analysis', ?, 'open_question,auto_resolved', ?, ?)
                    """,
                    (experiment_id, repo_name, text, note, confidence, now, now),
                )
            conn.commit()
        except Exception as _e:
            print(f"[auto_resolve] Could not persist answer for '{text[:60]}': {_e}")

    return resolved


def _detect_resume_state(experiment_id: str, repo_name: str, key: str) -> dict:
    """
    Detect which steps have already been completed for a run.
    
    Returns dict with:
    - resume_from_step: 1, 2, or 3 (which step to start from)
    - has_raw_output: bool (whether Step 2 output exists)
    - parsed_content: dict or None (parsed results if Step 3 already done)
    """
    from pathlib import Path
    
    resume_state = {
        "resume_from_step": 1,
        "has_raw_output": False,
        "parsed_content": None,
    }
    
    # Check if raw output exists (indicates Step 2 was done)
    raw_file = _ai_raw_output_file(key)
    
    if raw_file.exists():
        resume_state["has_raw_output"] = True
        resume_state["resume_from_step"] = 3  # Skip to parsing step
        
        # Try to parse it
        try:
            content = raw_file.read_text(encoding='utf-8', errors='replace')
            parsed = _extract_json_object(content)
            if parsed:
                resume_state["parsed_content"] = parsed
        except Exception:
            pass
    
    return resume_state


def _get_db_job_status(experiment_id: str, repo_name: str) -> dict | None:
    """Check context_metadata for a persisted job completion record (survives server restarts)."""
    try:
        conn = _get_db()
        if conn is None:
            return None
        try:
            row = conn.execute(
                """
                SELECT key, value FROM context_metadata
                WHERE experiment_id = ? AND namespace = 'ai_overview'
                  AND key IN ('ai_analysis_completed_at', 'ai_analysis_failed_at')
                  AND repo_id = (
                    SELECT id FROM repositories
                    WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1
                  )
                ORDER BY key LIMIT 2
                """,
                (experiment_id, experiment_id, repo_name),
            ).fetchall()
        finally:
            conn.close()
        if not row:
            return None
        for r in row:
            if r['key'] == 'ai_analysis_completed_at':
                return {"status": "completed", "experiment_id": experiment_id, "repo_name": repo_name,
                        "completed_at": float(r['value'] or 0)}
            if r['key'] == 'ai_analysis_failed_at':
                return {"status": "failed", "experiment_id": experiment_id, "repo_name": repo_name,
                        "error": "Job failed (recovered from DB record)"}
    except Exception:
        pass
    return None


def _try_recover_from_raw_output(experiment_id: str, repo_name: str, key: str) -> bool:
    """
    Attempt to recover a failed AI analysis by parsing the saved raw output file.
    
    Returns True if recovery succeeded (results persisted to DB), False otherwise.
    This handles cases where the stream disconnected during Step 3 (parsing & persisting).
    """
    try:
        raw_file = _ai_raw_output_file(key)
        
        if not raw_file.exists():
            return False
        
        # Try to read and parse the raw output
        combined = raw_file.read_text(encoding='utf-8', errors='replace')
        if not combined:
            return False
        
        # Extract JSON from raw output (same logic as main parsing)
        parsed = _extract_json_object(combined)
        if not parsed:
            return False
        
        # Minimal persistence: just mark as completed since we can't re-run the full Step 3
        # The raw output is available for manual inspection, and subsequent runs can use it
        try:
            db_helpers.upsert_context_metadata(
                experiment_id,
                repo_name,
                "ai_analysis_recovered",
                str(int(time.time())),
                namespace="ai_overview",
                source="recovery_from_raw",
            )
            db_helpers.upsert_context_metadata(
                experiment_id,
                repo_name,
                "ai_analysis_completed_at",
                str(int(time.time())),
                namespace="ai_overview",
                source="recovery_from_raw",
            )
            return True
        except Exception:
            return False
    except Exception:
        return False


def _append_ai_job_log(key: str, line: str) -> None:
    """Append a log line to an AI analysis job with a bounded history and write to a per-job logfile.

    Logfile path: Output/AILogs/<key>.log (safe filename characters).
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    safe_key = re.sub(r'[^A-Za-z0-9_.-]', '_', key)
    log_dir = REPO_ROOT / 'Output' / 'AILogs'
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_file = log_dir / f"{safe_key}.log"

    line_with_ts = f"[{ts}] {line}"

    # Append to in-memory job logs
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if job:
            logs = job.get("logs", [])
            logs.append(line_with_ts)
            if len(logs) > 500:
                logs = logs[-500:]
            job["logs"] = logs
            job["last_activity_at"] = time.time()
            _AI_ANALYSIS_JOBS[key] = job

    # Append to disk logfile for post-mortem
    try:
        with open(log_file, 'a', encoding='utf-8') as fh:
            fh.write(line_with_ts + "\n")
    except Exception:
        pass


def _resolve_copilot_command() -> tuple[list[str] | None, str | None]:
    """Resolve the command used to invoke Copilot and reject known placeholder wrappers."""
    override = (os.environ.get("COPILOT_COMMAND") or os.environ.get("COPILOT_CMD") or "").strip()
    candidates: list[list[str]] = []

    if override:
        try:
            candidates.append(shlex.split(override, posix=False))
        except ValueError as exc:
            return None, f"Invalid COPILOT_COMMAND/COPILOT_CMD value: {exc}"
    else:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                npm_cmd = Path(appdata) / "npm" / "copilot.cmd"
                npm_sh = Path(appdata) / "npm" / "copilot"
                if npm_cmd.exists():
                    candidates.append([str(npm_cmd)])
                if npm_sh.exists():
                    candidates.append([str(npm_sh)])
        # Standalone Copilot CLI must take priority over `gh copilot` — the flags
        # passed (--model, --stream, --allow-all-tools, etc.) are Copilot CLI-specific
        # and will cause `gh copilot` to exit immediately with an error.
        candidates.extend([
            ["copilot"],
        ])

    for candidate in candidates:
        if not candidate:
            continue

        first = candidate[0]
        if os.path.isabs(first) and Path(first).exists():
            resolved_path = Path(first)
        else:
            resolved = shutil.which(first)
            if not resolved:
                continue
            resolved_path = Path(resolved)

        if resolved_path.suffix.lower() == ".ps1":
            try:
                script_text = resolved_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                script_text = ""

            if "api.your-api-endpoint.com/ask" in script_text and "Invoke-RestMethod" in script_text:
                return None, (
                    f"Resolved Copilot command points to placeholder wrapper {resolved_path}. "
                    "Set COPILOT_COMMAND to a real Copilot CLI executable before running AI summaries."
                )

        return [str(resolved_path), *candidate[1:]], None

    return None, "No usable Copilot CLI found. Install the standalone Copilot CLI (npm install -g @githubnext/copilot-cli) or set the COPILOT_COMMAND environment variable."


def _build_copilot_launch(
    command: list[str],
    cwd: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str] | None, str, dict[str, str], str | None, str | None]:
    """Build a Copilot subprocess launch without altering the runtime environment."""
    launch_env = dict(os.environ, PYTHONUNBUFFERED="1")
    if extra_env:
        launch_env.update(extra_env)
    return command, cwd, launch_env, None, None


def _prepare_copilot_prompt(prompt: str, cwd: str) -> tuple[list[str], Path | None]:
    """Return CLI prompt arguments, using a prompt file when the inline prompt would be too large."""
    prompt_file: Path | None = None
    prompt_args = ["-p", prompt]

    # Windows process creation fails once the command line grows too large; stage big prompts in a file.
    if os.name == "nt" and len(prompt) > 6000:
        prompt_dir = Path(cwd) / ".triage-saurus"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            prefix="copilot_prompt_",
            dir=prompt_dir,
            delete=False,
        ) as handle:
            handle.write(prompt)
            prompt_file = Path(handle.name)

        relative_prompt = prompt_file.relative_to(Path(cwd))
        prompt_args = [
            "-p",
            (
                f"Read the full assignment from {relative_prompt.as_posix()} in the current working directory, "
                "follow it exactly, and return only the requested output format."
            ),
        ]

    return prompt_args, prompt_file


def _summarize_copilot_output(text: str, max_lines: int = 6, max_chars: int = 500) -> str:
    """Return a compact summary of Copilot output suitable for logs and status messages."""
    lines = [line.strip() for line in text.splitlines() if line and line.strip()]
    if not lines:
        return ""

    summary = " | ".join(lines[-max_lines:])
    if len(summary) > max_chars:
        summary = summary[-max_chars:]
    return summary


def _extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from model output.

    Enhanced: handles leading bullets/characters, tidy common stray line breaks inside
    JSON string values, and attempts to repair half-quoted multiline strings by
    joining broken lines inside quotes.
    """
    if not text:
        return None
    s = text.strip()

    # Copilot often prefixes every output line with a bullet marker (● or • or - etc.).
    # Strip them from the start of every line so JSON structure is preserved.
    s = '\n'.join(re.sub(r'^\s*[\u25CF\u2022\*\-]+\s*', '', ln) for ln in s.splitlines())

    # Try direct parse first
    if s.startswith('{') and s.endswith('}'):
        try:
            return json.loads(s)
        except Exception:
            pass

    # Try fenced JSON block
    fence_m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', s, re.IGNORECASE)
    if fence_m:
        try:
            return json.loads(fence_m.group(1))
        except Exception:
            pass

    # Extract largest {...} blob
    blob_m = re.search(r'(\{[\s\S]*\})', s)
    candidate = blob_m.group(1) if blob_m else s

    # Attempt naive repair: join lines that appear to be broken inside string values.
    # This looks for patterns like "...": "some text\n more text" split across lines
    def _repair_multiline_strings(t: str) -> str:
        lines = t.splitlines()
        out_lines = []
        in_quote = False
        buf = ''
        for ln in lines:
            if not in_quote:
                if re.search(r'"\s*:\s*"[^"]*$', ln):
                    # Line starts a quoted value but doesn't close it
                    in_quote = True
                    buf = ln
                else:
                    out_lines.append(ln)
            else:
                # We're inside an unterminated quote; append this line to buffer
                buf += ' ' + ln.strip()
                if '"' in ln:
                    # Heuristic: closing quote on this line — end buffer
                    in_quote = False
                    out_lines.append(buf)
                    buf = ''
        if in_quote and buf:
            out_lines.append(buf)
        repaired = '\n'.join(out_lines) if out_lines else t
        return repaired

    tried = candidate
    try:
        return json.loads(tried)
    except Exception:
        pass

    # Try repair and parse
    repaired = _repair_multiline_strings(candidate)
    try:
        return json.loads(repaired)
    except Exception:
        pass

    # As a last resort, attempt to convert smart-quoted multiline JSON by
    # replacing ALL bare newlines (with optional leading whitespace) with a space.
    # This handles embedded file paths like "foo\nbar.tf" where no indent follows.
    simple_norm = re.sub(r"\n\s*", ' ', candidate)
    try:
        return json.loads(simple_norm)
    except Exception:
        return None


def _normalize_attack_paths(raw_value: object, reviewer: str | None = None) -> list[dict]:
    """Normalize AI- or script-produced attack path entries into a stable schema."""
    if not raw_value:
        return []

    items = raw_value if isinstance(raw_value, list) else [raw_value]
    normalized: list[dict] = []

    for item in items:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("path") or "").strip()
            path = str(item.get("path") or item.get("chain") or "").strip()
            summary = str(item.get("summary") or item.get("detail") or item.get("description") or "").strip()
            impact = str(item.get("impact") or item.get("risk") or item.get("severity_reason") or "").strip()
            confidence = str(item.get("confidence") or "").strip().lower()
            source = str(item.get("source") or item.get("kind") or "").strip()
            reviewer_name = str(item.get("reviewer") or reviewer or "").strip()
            evidence_raw = item.get("evidence") or item.get("references") or []
        else:
            title = str(item).strip()
            path = ""
            summary = ""
            impact = ""
            confidence = ""
            source = ""
            reviewer_name = reviewer or ""
            evidence_raw = []

        evidence: list[str] = []
        if isinstance(evidence_raw, list):
            for ref in evidence_raw[:6]:
                if isinstance(ref, dict):
                    ref_rule = str(ref.get("rule_id") or "").strip()
                    ref_file = str(ref.get("file") or "").strip()
                    ref_line = ref.get("line")
                    ref_snippet = str(ref.get("snippet") or "").strip()
                    parts = [p for p in [ref_rule, ref_file + (f":{ref_line}" if ref_file and ref_line else ref_file), ref_snippet] if p]
                    if parts:
                        evidence.append(" | ".join(parts))
                else:
                    text = str(ref).strip()
                    if text:
                        evidence.append(text)
        elif isinstance(evidence_raw, dict):
            text = "; ".join(f"{k}: {v}" for k, v in evidence_raw.items() if str(v).strip())
            if text:
                evidence.append(text)
        else:
            text = str(evidence_raw).strip()
            if text:
                evidence.append(text)

        if confidence not in {"high", "medium", "low"}:
            confidence = ""

        if not (title or path or summary or impact):
            continue

        normalized.append({
            "title": title or path or summary[:120] or "Attack path",
            "path": path,
            "summary": summary,
            "impact": impact,
            "confidence": confidence,
            "source": source,
            "reviewer": reviewer_name,
            "evidence": evidence,
        })

    return normalized


def _dedupe_attack_paths(paths: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        key = (
            str(path.get("title") or "").strip().lower(),
            str(path.get("path") or "").strip().lower(),
            str(path.get("reviewer") or "").strip().lower(),
        )
        if key in seen:
            continue
        deduped.append(path)
        seen.add(key)
    return deduped


def _derive_overview_attack_paths(facts: dict) -> list[dict]:
    """Infer candidate attack paths from findings, RBAC, and exposure evidence."""
    assets_by_id = {
        str(asset.get("id")): asset
        for asset in (facts.get("assets") or [])
        if asset.get("id") is not None
    }
    derived: list[dict] = []

    def _is_broad_role_name(value: object) -> bool:
        role_text = str(value or "").strip().lower()
        return role_text in {"owner", "contributor", "user access administrator"}

    def _is_broad_scope_name(value: object) -> bool:
        scope_text = str(value or "").strip().lower()
        return any(token in scope_text for token in ("/subscriptions/", "/resourcegroups/", "resource group", "subscription"))

    for role in (facts.get("roles") or [])[:50]:
        role_name = str(role.get("role_name") or role.get("permissions") or "").strip()
        scope_name = str(role.get("scope_name") or role.get("resource_name") or "").strip()
        identity_name = str(role.get("identity_name") or role.get("principal_id") or "identity").strip()
        role_text = role_name.lower()
        if not role_name:
            continue
        if role.get("is_excessive") or (_is_broad_role_name(role_name) and _is_broad_scope_name(scope_name)):
            derived.append({
                "title": f"Broad {role_name} scope via {identity_name}",
                "path": f"Compromised workload or identity -> {identity_name} -> {role_name} on {scope_name or 'assigned scope'}",
                "summary": (
                    f"The identity {identity_name} carries {role_name} on a broad scope, which can convert a single workload compromise into wider control."
                ),
                "impact": f"Potential control over {scope_name or 'the wider environment'}.",
                "confidence": "high" if role.get("is_excessive") else "medium",
                "source": "rbac",
                "evidence": [f"Role assignment: {identity_name} -> {role_name} -> {scope_name or 'assigned scope'}"],
            })
        elif "automation" in identity_name.lower() and role_text in {"owner", "contributor"}:
            derived.append({
                "title": f"Automation identity with {role_name}",
                "path": f"Modify automation -> run as {identity_name} -> {role_name} on {scope_name or 'assigned scope'}",
                "summary": "Automation-backed identities can turn code or runbook modification into privileged control-plane access.",
                "impact": f"Privilege escalation into {scope_name or 'the assigned scope'}.",
                "confidence": "medium",
                "source": "rbac",
                "evidence": [f"Automation identity: {identity_name} has {role_name}"],
            })

    for port in (facts.get("ports") or [])[:25]:
        resource_id = port.get("resource_id")
        asset = assets_by_id.get(str(resource_id), {})
        resource_name = str(asset.get("resource_name") or f"resource #{resource_id}").strip()
        protocol = str(port.get("protocol") or "").strip()
        port_num = str(port.get("port") or "").strip()
        evidence = str(port.get("evidence") or "public endpoint evidence").strip()
        derived.append({
            "title": f"Internet reachability to {resource_name}",
            "path": f"Internet -> {resource_name}",
            "summary": f"The service appears reachable from the Internet via {protocol or 'network'} {port_num or 'exposure'}.",
            "impact": "Remote attack surface is exposed directly to unauthenticated network access unless additional controls apply.",
            "confidence": "medium",
            "source": "exposure",
            "evidence": [evidence],
        })

    public_keywords = ("public", "anonymous", "internet", "blob", "container", "cosmos")
    for finding in (facts.get("findings") or [])[:60]:
        title = str(finding.get("title") or "").strip()
        description = str(finding.get("description") or "").strip()
        rule_id = str(finding.get("rule_id") or "").strip()
        resource_name = str(finding.get("resource_name") or "").strip()
        resource_type = str(finding.get("resource_type") or "").strip()
        haystack = f"{title} {description} {rule_id}".lower()
        if resource_name and any(keyword in haystack for keyword in public_keywords):
            derived.append({
                "title": f"Public data path to {resource_name}",
                "path": f"Internet -> {resource_name}",
                "summary": f"The finding suggests {resource_name} ({resource_type or 'resource'}) is exposed publicly or accessible through a public endpoint.",
                "impact": "Data exposure or remote attack surface may exist depending on authentication and network controls.",
                "confidence": "medium",
                "source": "findings",
                "evidence": [text for text in [title, rule_id, description[:160]] if text],
            })

    return _dedupe_attack_paths(derived)[:8]


def _fetch_overview_facts(experiment_id: str, repo_name: str) -> tuple[int, dict] | None:
    """Fetch a compact fact-set for Copilot to summarise."""
    conn = _get_db()
    if conn is None:
        return None

    try:
        repo_row = conn.execute(
            """
            SELECT id FROM repositories
            WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
            LIMIT 1
            """,
            (experiment_id, repo_name),
        ).fetchone()
        if not repo_row:
            return None
        repo_id = int(repo_row["id"])

        # Ensure inferred AKS cluster exists (so facts/AI can see it) when only k8s workloads are present.
        try:
            from Scripts.Persist import db_helpers as _dbh  # type: ignore
            _dbh.ensure_inferred_aks_cluster(experiment_id, repo_name)
            _dbh.infer_aks_cluster_link(experiment_id, repo_name)
        except Exception:
            pass

        providers = [
            r["provider"]
            for r in conn.execute(
                """
                SELECT COALESCE(provider, 'unknown') AS provider
                FROM resources
                WHERE experiment_id = ? AND repo_id = ?
                GROUP BY COALESCE(provider, 'unknown')
                ORDER BY COUNT(*) DESC
                LIMIT 5
                """,
                (experiment_id, repo_id),
            ).fetchall()
        ]

        resource_types = [
            r["resource_type"]
            for r in conn.execute(
                """
                SELECT resource_type
                FROM resources
                WHERE experiment_id = ? AND repo_id = ?
                GROUP BY resource_type
                ORDER BY COUNT(*) DESC
                LIMIT 8
                """,
                (experiment_id, repo_id),
            ).fetchall()
        ]

        interaction_types = [
            r["connection_type"]
            for r in conn.execute(
                """
                SELECT connection_type
                FROM resource_connections
                WHERE experiment_id = ?
                  AND (source_repo_id = ? OR target_repo_id = ?)
                  AND connection_type IS NOT NULL
                  AND LOWER(connection_type) NOT IN ('contains')
                GROUP BY connection_type
                ORDER BY COUNT(*) DESC
                LIMIT 8
                """,
                (experiment_id, repo_id, repo_id),
            ).fetchall()
        ] if _table_exists(conn, "resource_connections") else []

        dep_types = [
            r["connection_type"]
            for r in conn.execute(
                """
                SELECT connection_type
                FROM resource_connections
                WHERE experiment_id = ?
                  AND source_repo_id = ?
                  AND connection_type IS NOT NULL
                  AND LOWER(connection_type) NOT IN ('contains')
                GROUP BY connection_type
                ORDER BY COUNT(*) DESC
                LIMIT 8
                """,
                (experiment_id, repo_id),
            ).fetchall()
        ] if _table_exists(conn, "resource_connections") else []

        top_findings = [
            (r["title"] or r["rule_id"] or "Untitled")
            for r in conn.execute(
                """
                SELECT title, rule_id
                FROM findings
                WHERE experiment_id = ? AND repo_id = ?
                ORDER BY severity_score DESC, id ASC
                LIMIT 8
                """,
                (experiment_id, repo_id),
            ).fetchall()
        ] if _table_exists(conn, "findings") else []

        # Include full findings data (all findings for this repo) so the AI has the complete set
        # of findings and associated metadata from the Findings tab.
        all_findings = []
        if _table_exists(conn, "findings"):
            rows = conn.execute(
                """
              SELECT f.id, f.resource_id, f.rule_id, f.title, f.description, f.severity_score, f.category,
                       f.source_file, f.source_line_start, f.evidence_location,
                   SUBSTR(f.code_snippet, 1, 300) AS code_snippet,
                   r.resource_name,
                   r.resource_type
              FROM findings f
              LEFT JOIN resources r ON f.resource_id = r.id
              WHERE f.experiment_id = ? AND f.repo_id = ?
              ORDER BY f.severity_score DESC, f.id ASC
                """,
                (experiment_id, repo_id),
            ).fetchall()
            all_findings = [dict(r) for r in rows]

        facts = {
            "providers": providers,
            "resource_types": resource_types,
            "interaction_types": interaction_types,
            "dependency_types": dep_types,
            "top_findings": top_findings,
            "findings": all_findings,
        }

        # Global Knowledge Q&A (per experiment; optionally scoped to this repo)
        try:
            qna = {"global": [], "repo": []}
            if _table_exists(conn, "subscription_context"):
                g_rows = conn.execute(
                    "SELECT question, answer, confidence, tags, answered_by, updated_at FROM subscription_context WHERE experiment_id = ? AND scope_key = 'global' ORDER BY updated_at DESC, id DESC LIMIT 50",
                    (experiment_id,),
                ).fetchall()
                qna["global"] = [dict(r) for r in g_rows] if g_rows else []

                r_rows = conn.execute(
                    "SELECT question, answer, confidence, tags, answered_by, updated_at FROM subscription_context WHERE experiment_id = ? AND scope_key = 'repo' AND LOWER(repo_name)=LOWER(?) ORDER BY updated_at DESC, id DESC LIMIT 50",
                    (experiment_id, repo_name),
                ).fetchall()
                qna["repo"] = [dict(r) for r in r_rows] if r_rows else []
            facts["global_knowledge_qna"] = qna
        except Exception:
            facts["global_knowledge_qna"] = {"global": [], "repo": []}

        # Minimal additional tab data: assets, ingress (api operations), egress, roles (RBAC), containers, ports, terraform modules
        # Each key contains a list of lightweight dicts representing rows to give AI context about each tab.
        try:
            # Assets: core resource rows
            assets_rows = conn.execute(
                "SELECT id, resource_name, resource_type, provider, region, source_file FROM resources WHERE experiment_id = ? AND repo_id = ? LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, "resources") else []
            assets = [dict(r) for r in assets_rows]
        except Exception:
            assets = []

        try:
            # Ingress/API operations: mirror what the ingress tab renders
            ops_rows = conn.execute(
                "SELECT id, operation_name, resource_type, source_file, source_line_start, is_public, internet_access FROM resources WHERE experiment_id = ? AND repo_id = ? AND resource_type LIKE '%api%' LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, "resources") else []
            ingress = [dict(r) for r in ops_rows]
        except Exception:
            ingress = []

        try:
            # Egress: resource connections where this repo is source
            eg_rows = conn.execute(
                "SELECT source_resource_id, target_resource_id, connection_type FROM resource_connections WHERE experiment_id = ? AND source_repo_id = ? LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, "resource_connections") else []
            egress = [dict(r) for r in eg_rows]
        except Exception:
            egress = []

        try:
            # Roles: role assignments and identity-related resources
            role_rows = conn.execute(
                                """
                                SELECT
                                        res.id,
                                        res.resource_name AS identity_name,
                                        res.resource_type,
                                        res.provider,
                                        MAX(CASE WHEN LOWER(rp.property_key) IN ('role_name', 'role_definition_name', 'role_definition_id', 'role', 'permissions') THEN rp.property_value END) AS role_name,
                                        MAX(CASE WHEN LOWER(rp.property_key) IN ('scope_resource', 'scope', 'scope_name', 'scope_id', 'resource_id', 'target_resource_name') THEN rp.property_value END) AS scope_name,
                                        MAX(CASE WHEN LOWER(rp.property_key) = 'principal_id' THEN rp.property_value END) AS principal_id,
                                        MAX(CASE WHEN LOWER(rp.property_key) = 'is_excessive' THEN rp.property_value END) AS is_excessive
                                FROM resources res
                                LEFT JOIN resource_properties rp ON rp.resource_id = res.id
                                WHERE res.experiment_id = ? AND res.repo_id = ?
                                    AND (
                                        res.resource_type LIKE '%role%'
                                        OR res.resource_type LIKE '%identity%'
                                        OR res.resource_type LIKE '%managed_identity%'
                                        OR res.resource_type LIKE '%user_assigned_identity%'
                                        OR res.resource_type LIKE '%service_principal%'
                                    )
                                GROUP BY res.id, res.resource_name, res.resource_type, res.provider
                                LIMIT 200
                                """,
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, "resources") else []
            roles = [dict(r) for r in role_rows]
        except Exception:
            roles = []

        try:
            # Containers: kubernetes deployments / components
            cont_rows = conn.execute(
                "SELECT id, resource_name, resource_type, source_file FROM resources WHERE experiment_id = ? AND repo_id = ? AND (resource_type LIKE '%kubernetes%' OR resource_type LIKE '%container%') LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, "resources") else []
            containers = [dict(r) for r in cont_rows]
        except Exception:
            containers = []

        try:
            # Ports: exposures or ports table if present; fallback to exposure_analysis
            ports = []
            if _table_exists(conn, 'exposure_analysis'):
                p_rows = conn.execute(
                    "SELECT resource_id, port, protocol, evidence FROM exposure_analysis WHERE experiment_id = ? LIMIT 200",
                    (experiment_id,),
                ).fetchall()
                ports = [dict(r) for r in p_rows]
        except Exception:
            ports = []

        # Terraform modules: extracted module sources (to detect shared/internal modules across repos)
        try:
            mod_rows = conn.execute(
                "SELECT key, value FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND namespace = 'phase2_code' AND key LIKE 'terraform.module.%' ORDER BY id DESC LIMIT 200",
                (experiment_id, repo_id),
            ).fetchall() if _table_exists(conn, 'context_metadata') else []
            terraform_modules = []
            for r in mod_rows:
                k = (r['key'] or '')
                name = k.split('terraform.module.', 1)[-1] if 'terraform.module.' in k else k
                terraform_modules.append({"name": name, "value": r['value']})
        except Exception:
            terraform_modules = []

        # Attach these to facts with descriptive keys so AI knows their origin
        facts['assets'] = assets
        facts['ingress'] = ingress
        facts['egress'] = egress
        facts['roles'] = roles
        facts['containers'] = containers
        facts['ports'] = ports
        facts['terraform_modules'] = terraform_modules

        # Include mermaid diagram node lists (if diagrams are present in DB) so AI can detect missing arrows
        try:
            from Scripts.Persist.db_helpers import get_cloud_diagrams  # type: ignore
            db_diags = get_cloud_diagrams(experiment_id)
            diagrams = []
            for d in db_diags:
                code = d.get('mermaid_code') or ''
                nodes = list(_extract_mermaid_nodes(code)) if code else []
                diagrams.append({
                    'title': d.get('diagram_title'),
                    'diagram_title': d.get('diagram_title'),
                    'mermaid_code': code,
                    'nodes': nodes,
                    'code_snippet': (code[:200] + '...') if code and len(code) > 200 else code,
                })
            facts['diagrams'] = diagrams
        except Exception:
            facts['diagrams'] = []

        facts['attack_paths'] = _derive_overview_attack_paths(facts)

        return repo_id, facts
    finally:
        conn.close()


def _run_ai_analysis_job(experiment_id: str, repo_name: str) -> None:
    """Run AI enrichment + skeptic analysis in background for an experiment."""
    key = _ai_job_key(experiment_id, repo_name)
    commands = [
        ("enrich_findings", [sys.executable, str(ENRICH_FINDINGS), "--experiment", experiment_id]),
        ("run_skeptics", [sys.executable, str(RUN_SKEPTICS), "--experiment", experiment_id, "--reviewer", "all"]),
        ("generate_project_overview", [sys.executable, str(GENERATE_PROJECT_OVERVIEW), "--experiment", experiment_id, "--repo", repo_name]),
    ]

    # Friendly names so UI can treat skeptics as a subtask/status flag.
    step_labels = {
        "enrich_findings": "Enriching findings",
        "run_skeptics": "Running skeptics",
        "generate_project_overview": "Generating overview",
    }

    with _AI_ANALYSIS_LOCK:
        _AI_ANALYSIS_JOBS[key] = {
            "status": "running",
            "experiment_id": experiment_id,
            "repo_name": repo_name,
            "started_at": time.time(),
            "last_activity_at": time.time(),
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
        label = step_labels.get(step_name, step_name)
        with _AI_ANALYSIS_LOCK:
            job = _AI_ANALYSIS_JOBS.get(key, {})
            job["active_step"] = step_name
            job["active_step_label"] = label
            job["skeptics_running"] = True if step_name == "run_skeptics" else False
            _AI_ANALYSIS_JOBS[key] = job
        _append_ai_job_log(key, f"Starting step: {label}")
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=subprocess_env,
        )
        stdout_tail = "\n".join((result.stdout or "").splitlines()[-12:])
        stderr_tail = "\n".join((result.stderr or "").splitlines()[-12:])
        label = step_labels.get(step_name, step_name)
        _append_ai_job_log(key, f"Step {label} finished with exit code {result.returncode}")
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
                "label": step_labels.get(step_name, step_name),
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
        job["active_step"] = None
        job["active_step_label"] = None
        job["skeptics_running"] = False
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

    # Capture external image references and drop internal stage aliases.
    # Example: "FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build" then "FROM build"
    # should only count the external image, not the stage alias.
    from_pattern = re.compile(
        r"^FROM(?:\s+--[^\s]+)*\s+([^\s]+)(?:\s+AS\s+([^\s]+))?",
        re.IGNORECASE,
    )
    parsed_froms: list[tuple[str, int]] = []
    stage_aliases: set[str] = set()
    for idx, line in enumerate(txt.splitlines(), start=1):
        s = line.strip()
        m = from_pattern.match(s)
        if not m:
            continue
        image = (m.group(1) or "").strip()
        alias = (m.group(2) or "").strip().lower()
        if image:
            parsed_froms.append((image, idx))
        if alias:
            stage_aliases.add(alias)

    entries: list[tuple[str, int]] = [
        (image, line_no)
        for image, line_no in parsed_froms
        if image.lower() not in stage_aliases
    ]
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

    # 7. Preserve "contains" edges so relationship-heavy diagrams remain connected.
    lines = code.splitlines()

    # 8. Collapse newlines inside bracketed labels across all lines first
    collapsed = [re.sub(r'\[([^\]]*\n[^\]]*)\]', lambda m: '[' + m.group(1).replace('\n', ' ').replace('\r',' ') + ']', ln) for ln in lines]

    # 9. Remove self-edges (node linking to itself) from the collapsed lines
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

    # 10. Deduplicate subgraph blocks, node defs and style lines
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
        "classdef",
        "class",
        "linkstyle",
        "default",
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


def _collect_diagrams_dbfirst(experiment_id: str) -> list[dict]:
    """Return DB-backed cloud diagrams only (no markdown fallback)."""
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

    return []


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
    """Return True if the experiment has at least one diagram in cloud_diagrams."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Scripts.Persist.db_helpers import get_cloud_diagrams  # type: ignore
        return bool(get_cloud_diagrams(experiment_id))
    except Exception:
        return False


def _cleanup_stale_locks():
    """Clean up lock files from interrupted scans on server startup.
    
    Lock files are created when scans start and should be deleted when scans complete.
    If the server restarts, orphaned lock files may remain. This function removes them
    so users can start fresh scans without getting "scan in progress" messages for
    scans that no longer exist.
    """
    try:
        lock_dir = REPO_ROOT / "Output" / "Learning" / "running_scans"
        if not lock_dir.exists():
            return
        
        for lock_file in lock_dir.glob("*.lock"):
            try:
                # Try to read the experiment ID from the lock file
                exp_id = lock_file.read_text(encoding="utf-8").strip()
                if not exp_id:
                    # Empty lock file - delete it
                    lock_file.unlink()
                    continue
                
                # Check if the experiment directory still exists and scan is actually running
                exp_candidates = sorted((REPO_ROOT / "Output" / "Learning" / "experiments").glob(f"{exp_id}_*"))
                if not exp_candidates:
                    # Experiment directory doesn't exist - delete stale lock
                    lock_file.unlink()
                    continue
                
                exp_dir = exp_candidates[0]
                exp_json = exp_dir / "experiment.json"
                if not exp_json.exists():
                    # Experiment JSON missing - delete stale lock
                    lock_file.unlink()
                    continue
                
                # Check if experiment status is still "running"
                try:
                    cfg = json.loads(exp_json.read_text(encoding="utf-8"))
                    if cfg.get("status") != "running":
                        # Scan completed or failed - delete lock file
                        lock_file.unlink()
                except Exception:
                    # Can't read experiment JSON - delete lock file to be safe
                    lock_file.unlink()
            except Exception as e:
                print(f"[Startup] Warning: Could not process lock file {lock_file.name}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[Startup] Warning: Could not clean up stale locks: {e}", file=sys.stderr)


def _sse(event: str, data) -> str:
    """Format a single SSE message."""
    payload = json.dumps(data) if not isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a log line for clean web rendering."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _decorate_scan_log_line(line: str) -> str:
    """Add lightweight emoji markers to scan logs while preserving existing prefixes."""
    if line is None:
        return ""

    clean = _strip_ansi(str(line)).rstrip("\r")
    if not clean.strip():
        return clean

    source_emoji = {
        "web": "🕵️",
        "pipeline": "🧭",
        "detection": "🔍",
        "misconfigurations": "🛡️",
        "store": "💾",
        "info": "ℹ️",
        "warning": "⚠️",
        "warn": "⚠️",
        "error": "❌",
        "success": "✅",
    }

    source_match = re.match(
        r"^\[(Info|Web|Pipeline|Detection|Misconfigurations|Store|Error|Warning|Warn|Success)\](\s*)(.*)$",
        clean,
        flags=re.IGNORECASE,
    )
    if source_match:
        tag, spacing, rest = source_match.groups()
        emoji = source_emoji.get(tag.lower(), "")
        if emoji and not rest.startswith(emoji):
            spacer = spacing if spacing else " "
            clean = f"[{tag}]{spacer}{emoji} {rest}"

    if clean.startswith("[*]"):
        clean = "[Info] 🔄 " + clean[3:].lstrip()
    elif clean.startswith("[+]"):
        clean = "[Success] ✅ " + clean[3:].lstrip()
    elif clean.startswith("[✓]"):
        clean = "[Success] ✅ " + clean[3:].lstrip()

    if clean.startswith("[Connected to scan stream]"):
        clean = "[Info] 🔌 Connected to scan stream"
    elif clean.startswith("[Receiving data..."):
        clean = "[Info] 📡 Receiving data..."
    elif clean.startswith("[Stream closed]"):
        clean = "[Info] 🔒 Stream closed"
    elif clean.startswith("▶") and "Starting scan:" in clean:
        clean = "🚀 " + clean
    elif re.match(r"^(?:▶\s*)?Phase\s+\d", clean, re.IGNORECASE):
        clean = "🧩 " + clean
    elif re.match(r"^(?:▶\s*)?PHASE\s+\d", clean):
        clean = "🧩 " + clean
    elif "Pipeline complete" in clean and "✅" not in clean:
        clean = clean.replace("Pipeline complete", "✅ Pipeline complete")
    elif clean.strip().startswith("✓"):
        clean = clean.replace("✓", "✅", 1)
    elif clean.startswith("Info:"):
        clean = clean.replace("Info:", "ℹ️ Info:", 1)
    elif clean.startswith("Warning:"):
        clean = clean.replace("Warning:", "⚠️ Warning:", 1)
    elif clean.startswith("Next steps:"):
        clean = clean.replace("Next steps:", "🧩 Next steps:", 1)

    return clean


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

    # Open per-repo scan log file (truncate at start of new scan so reconnects
    # always see the freshest run from the beginning).
    scan_log_dir = REPO_ROOT / "Output" / "ScanLogs"
    scan_log_file: Path | None = None
    scan_log_fh = None
    try:
        scan_log_dir.mkdir(parents=True, exist_ok=True)
        safe_repo_name = re.sub(r'[^A-Za-z0-9_.-]', '_', repo.name)
        scan_log_file = scan_log_dir / f"{safe_repo_name}.log"
        scan_log_fh = open(scan_log_file, "w", encoding="utf-8", buffering=1)
    except Exception:
        scan_log_fh = None

    def _write_scan_log(line: str) -> None:
        if scan_log_fh:
            try:
                scan_log_fh.write(line + "\n")
            except Exception:
                pass

    def _emit_scan_log(line: str) -> str:
        rendered = _decorate_scan_log_line(line)
        _write_scan_log(rendered)
        return _sse("log", rendered)

    # Yield immediately to confirm connection
    first_line = f"[Web] Initializing scan for repository: {repo.name}"
    yield _emit_scan_log(first_line)

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
                            yield _emit_scan_log(f"[Web] Reusing running experiment id from lock: {experiment_id}")
            except Exception:
                # If lock read fails or experiment not found, fall through to normal creation
                experiment_id = None

        if experiment_id is None and triage_script.exists():
            yield _emit_scan_log("[Web] Creating new experiment...")
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
                    msg = f"[Web] Failed to pre-create experiment: {res.stderr.splitlines()[0]}"
                    yield _emit_scan_log(msg)

        # Persist lock for the experiment so concurrent requests reuse it
        if experiment_id:
            try:
                lock_file.write_text(str(experiment_id), encoding="utf-8")
            except Exception:
                pass
    except Exception as exc:
        msg = f"[Web] Experiment creation attempt failed: {exc}"
        yield _emit_scan_log(msg)

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
        yield _emit_scan_log(f"[Web] Using experiment id: {experiment_id}")
        # Inform the UI immediately of the experiment id so it can bind sections/queries
        yield _sse("experiment", experiment_id)

    for msg in [
        f"▶  Starting scan: {repo}",
        f"   Command: {' '.join(cmd)}",
        "",
        "Info: Comprehensive scan in progress — 156 detection rules + 212 misconfig rules",
        "      Estimated duration: 5-6 minutes (detection → misconfig → code analysis)",
        "",
    ]:
        yield _emit_scan_log(msg)

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        # Start process in a new session group so it survives if the HTTP request is closed
        # This ensures scans continue running even if the browser disconnects
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
            bufsize=1,
            env=env,
            start_new_session=True,  # Create new process group (Unix/Linux)
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
                yield _emit_scan_log(line)

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
                    # Dynamic estimate: if elapsed > 5.5 min, assume scan has issues and just report elapsed time
                    if elapsed < 330:  # 5.5 minutes
                        est_total = 330
                        est_remaining = max(0, est_total - elapsed)
                        hb = f"[Web] Scan in progress — elapsed {elapsed}s (est. {est_remaining}s remaining)"
                    else:
                        # Scan has exceeded expected time — may be hung or processing large repo
                        hb = f"[Web] ⚠️ Scan in progress — elapsed {elapsed}s (taking longer than expected)"
                    yield _emit_scan_log(hb)
                    last_hb = now

            if process.poll() is not None:
                # Drain remaining output
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    yield _emit_scan_log(line)
                    if experiment_id is None:
                        m = re.search(r"Experiment(?:\sID)?\s*[:\s]+([0-9]+)", line)
                        if m:
                            experiment_id = m.group(1)
                break
    except Exception as exc:
        yield _sse("error", f"Stream error: {exc}")
    finally:
        if scan_log_fh:
            try:
                scan_log_fh.close()
            except Exception:
                pass

    if experiment_id:
        # DB-only: read architecture diagrams from cloud_diagrams.
        diagrams = []
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from Scripts.Persist.db_helpers import get_cloud_diagrams
            db_diags = get_cloud_diagrams(experiment_id)
            if db_diags:
                diagrams = [{"title": d["diagram_title"], "code": d["mermaid_code"]} for d in db_diags]
        except Exception:
            diagrams = []

        if diagrams:
            yield _sse("diagrams", diagrams)
        else:
            yield _emit_scan_log("[Web] No architecture diagrams found in cloud_diagrams.")

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

@app.route("/api/scan_log/<repo_name>")
def api_scan_log(repo_name: str):
    """Return the most recent scan log lines for a repo (written during _stream_scan)."""
    safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', repo_name)
    log_file = REPO_ROOT / "Output" / "ScanLogs" / f"{safe_name}.log"
    if not log_file.exists():
        return jsonify({"lines": []})
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        return jsonify({"lines": lines})
    except Exception as exc:
        return jsonify({"lines": [], "error": str(exc)})


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
    
    # Check if there's a running scan for this repo
    running_experiment = None
    running_experiment_created_at = None
    try:
        lock_dir = REPO_ROOT / "Output" / "Learning" / "running_scans"
        lock_file = lock_dir / f"{repo_name}.lock"
        if lock_file.exists():
            running_exp_id = lock_file.read_text(encoding="utf-8").strip()
            if running_exp_id:
                # Verify the experiment directory still exists
                exp_base = REPO_ROOT / "Output" / "Learning" / "experiments"
                if exp_base.exists():
                    exp_candidates = sorted(exp_base.glob(f"{running_exp_id}_*"))
                    exp_dir = exp_candidates[0] if exp_candidates else None
                    
                    if exp_dir and (exp_dir / "experiment.json").exists():
                        # Experiment directory exists - trust the lock file
                        running_experiment = running_exp_id
                        # Get the directory creation time
                        try:
                            stat_info = exp_dir.stat()
                            # Use st_birthtime (creation) if available, otherwise st_mtime
                            created_timestamp = stat_info.st_birthtime if hasattr(stat_info, 'st_birthtime') else stat_info.st_mtime
                            running_experiment_created_at = int(created_timestamp * 1000)  # milliseconds
                        except Exception:
                            pass
                    else:
                        # Experiment doesn't exist - clean up stale lock file
                        try:
                            lock_file.unlink()
                        except Exception:
                            pass
                else:
                    # Experiments directory doesn't exist - clean up stale lock
                    try:
                        lock_file.unlink()
                    except Exception:
                        pass
    except Exception:
        pass
    
    result = {"scans": scans, "running_experiment": running_experiment}
    if running_experiment_created_at:
        result["running_experiment_created_at"] = running_experiment_created_at
    return jsonify(result)


@app.route("/api/analysis/start/<experiment_id>/<repo_name>", methods=["POST"])
def api_analysis_start(experiment_id: str, repo_name: str):
    """Start a fresh AI analysis job (Steps 1, 2, 3 from the beginning)."""
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


@app.route("/api/analysis/resume/<experiment_id>/<repo_name>", methods=["POST"])
def api_analysis_resume(experiment_id: str, repo_name: str):
    """Resume a failed/interrupted AI analysis from where it left off.
    
    If raw Copilot output exists, skips Step 2 (expensive agents) and resumes Step 3.
    If no raw output, starts from Step 1 (same as /start endpoint).
    """
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
    
    # Check what can be resumed
    resume_state = _detect_resume_state(resolved_exp_id, repo_name, key)
    
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

    return jsonify({
        "status": "started",
        "experiment_id": resolved_exp_id,
        "repo_name": repo_name,
        "resume_from_step": resume_state["resume_from_step"],
        "message": f"Resuming from step {resume_state['resume_from_step']}" if resume_state["resume_from_step"] > 1 else "Starting fresh"
    })


@app.route("/api/analysis/status/<experiment_id>/<repo_name>")
def api_analysis_status(experiment_id: str, repo_name: str):
    """Get status for current/last AI analysis job for an experiment+repo.

    Optional query param ``suffix`` appends to the job key (e.g. ``generate_rules``).
    """
    conn = _get_db()
    resolved_exp_id = experiment_id
    if conn:
        try:
            resolved = _get_experiment_for_repo(conn, repo_name, experiment_id)
            if resolved:
                resolved_exp_id = resolved
        except Exception:
            pass
        finally:
            conn.close()

    suffix = request.args.get("suffix", "").strip()
    base_key = _ai_job_key(resolved_exp_id, repo_name)
    key = (base_key + ":" + suffix) if suffix else base_key
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job:
            # Check DB for a persisted completion — handles server restarts
            db_status = _get_db_job_status(resolved_exp_id, repo_name)
            if db_status:
                return jsonify(db_status)
            return jsonify({"status": "idle", "experiment_id": resolved_exp_id, "repo_name": repo_name})

        # Auto-resolve stale "running" jobs — client disconnect aborts the SSE generator
        # before the completion block executes, leaving the job stuck in "running".
        if job.get("status") == "running":
            started_at = job.get("started_at") or 0
            last_activity_at = job.get("last_activity_at") or started_at
            age_secs = time.time() - last_activity_at
            proc = job.get("process")
            proc_alive = proc is not None and proc.poll() is None
            # For pure SSE-generator jobs (no subprocess), use a shorter but still
            # generous timeout because progress is tracked via last_activity_at.
            try:
                stale_threshold = int(os.environ.get('AI_STREAM_TIMEOUT', '600')) if proc is None else int(os.environ.get('AI_JOB_TIMEOUT', '3600'))
            except Exception:
                stale_threshold = 600 if proc is None else 3600

            if not proc_alive and age_secs > stale_threshold:
                db_status = _get_db_job_status(resolved_exp_id, repo_name)
                if db_status and db_status.get("status") in ("completed", "failed"):
                    job.update(db_status)
                    _AI_ANALYSIS_JOBS[key] = job
                else:
                    # Try to recover from raw output file before marking as failed
                    if _try_recover_from_raw_output(resolved_exp_id, repo_name, key):
                        job["status"] = "completed"
                        job["completed_at"] = time.time()
                        job["error"] = None
                        job["notes"] = "Recovered from raw output after stream disconnect"
                        _AI_ANALYSIS_JOBS[key] = job
                    else:
                        # No DB record and no recoverable raw output — mark as failed/stale
                        job["status"] = "failed"
                        job["error"] = "Job timed out or was interrupted (stream disconnected)"
                        job["completed_at"] = time.time()
                        _AI_ANALYSIS_JOBS[key] = job
        
        # For "failed" jobs, try recovery if not already attempted
        elif job.get("status") == "failed" and not job.get("recovery_attempted"):
            # Check if we can recover from raw output
            if _try_recover_from_raw_output(resolved_exp_id, repo_name, key):
                job["status"] = "completed"
                job["error"] = None
                job["notes"] = "Recovered from raw output"
                job["recovery_attempted"] = True
                _AI_ANALYSIS_JOBS[key] = job
            else:
                # Mark that we've already tried recovery to avoid repeated attempts
                job["recovery_attempted"] = True
                _AI_ANALYSIS_JOBS[key] = job

        # Create a shallow serializable copy excluding non-serializable fields like subprocess handles
        safe_job = {}
        for k, v in job.items():
            if k == 'process':
                continue
            try:
                json.dumps(v)
                safe_job[k] = v
            except Exception:
                safe_job[k] = str(v)
        return jsonify(safe_job)


@app.route("/api/analysis/copilot/stream/<experiment_id>/<repo_name>")
def api_analysis_copilot_stream(experiment_id: str, repo_name: str):
    """Stream a Copilot-generated overview into the Log panel via SSE."""

    def _build_legacy_prompt(repo_name: str, fact_json: dict, skeptic_rows: list) -> str:
        """Build legacy hardcoded prompt as fallback."""
        qna_json = json.dumps((fact_json or {}).get("global_knowledge_qna") or {"global": [], "repo": []})
        diagrams_json = json.dumps((fact_json or {}).get("diagrams") or [])
        assets_json = json.dumps((fact_json or {}).get("assets") or [])
        findings_json = json.dumps((fact_json or {}).get("findings") or [])
        roles_json = json.dumps((fact_json or {}).get("roles") or [])
        ports_json = json.dumps((fact_json or {}).get("ports") or [])
        attack_paths_json = json.dumps((fact_json or {}).get("attack_paths") or [])
        other_facts = dict(fact_json or {})
        for k in ("global_knowledge_qna", "diagrams", "assets", "findings", "roles", "ports", "attack_paths"):
            other_facts.pop(k, None)
        
        return (
            "You are creating an executive technical overview for a security triage portal. "
            "Use the supplied repository facts and respond with JSON only. "
            "Be concise and high-signal.\n\n"
            f"Repository: {repo_name}\n\n"
            f"1) Global Knowledge Q&A JSON: {qna_json}\n\n"
            f"2) Architecture / diagrams JSON: {diagrams_json}\n\n"
            f"3) Assets JSON: {assets_json}\n\n"
            f"4) Detected findings (evidence) JSON: {findings_json}\n\n"
            f"5) Roles / permissions JSON: {roles_json}\n\n"
            f"6) Internet / exposure evidence JSON: {ports_json}\n\n"
            f"7) Candidate attack paths JSON: {attack_paths_json}\n\n"
            f"8) Other extracted facts JSON: {json.dumps(other_facts)}\n\n"
            f"Skeptic reviews (sample): {json.dumps(skeptic_rows)}\n\n"
            "Return compact JSON with keys: project_summary, deployment_summary, interactions_summary, auth_summary, dependencies_summary, issues_summary, skeptic_summary, attack_paths, action_items, open_questions, observations, asset_visibility, learning_suggestions, new_assets, fixed_information\n\n"
            "In auth_summary and observations, explicitly reason about privilege escalation chains: compromised compute -> managed identity -> automation/resource control -> broader RBAC scope.\n"
            "Refine the candidate attack paths using findings, roles, and public exposure evidence, and return the final list in attack_paths.\n"
            "Distinguish anonymous public access from authenticated public endpoints for data services such as Storage and Cosmos DB.\n"
            "The attack_paths array must use objects with: title, path, summary, impact, confidence, evidence.\n"
            "For the 'observations' array each item MUST follow this schema:\n"
            "  {\"title\": \"<short title>\", \"detail\": \"<1-3 sentence explanation>\", \"target\": \"<primary file/resource>\", "
            "\"references\": [{\"finding_id\": <int or null>, \"rule_id\": \"<str>\", \"file\": \"<path>\", \"line\": <int or null>, \"snippet\": \"<≤120 char excerpt>\"}]}\n"
            "Populate 'references' with the specific findings (using ids/files/snippets from the 'Detected findings' JSON) that directly support each observation. Include 1-4 references per observation."
        )

    def _build_legacy_architecture_prompt(repo_name: str, fact_json: dict, skeptic_rows: list) -> str:
        """Build a fallback architecture-validation prompt."""
        assets_json = json.dumps((fact_json or {}).get("assets") or [])
        diagrams_json = json.dumps((fact_json or {}).get("diagrams") or [])
        findings_json = json.dumps((fact_json or {}).get("findings") or [])
        roles_json = json.dumps((fact_json or {}).get("roles") or [])
        ports_json = json.dumps((fact_json or {}).get("ports") or [])
        attack_paths_json = json.dumps((fact_json or {}).get("attack_paths") or [])
        other_facts = dict(fact_json or {})
        for k in ("assets", "diagrams", "findings", "roles", "ports", "attack_paths"):
            other_facts.pop(k, None)

        return (
            "You are the Architecture Validation Agent for a security triage portal. "
            "Validate the generated architecture diagrams and return JSON only.\n\n"
            f"Repository: {repo_name}\n\n"
            f"1) Architecture diagrams JSON: {diagrams_json}\n\n"
            f"2) Assets JSON: {assets_json}\n\n"
            f"3) Findings JSON: {findings_json}\n\n"
            f"4) Roles / permissions JSON: {roles_json}\n\n"
            f"5) Internet / exposure evidence JSON: {ports_json}\n\n"
            f"6) Candidate attack paths JSON: {attack_paths_json}\n\n"
            f"7) Other extracted facts JSON: {json.dumps(other_facts)}\n\n"
            f"Skeptic reviews (sample): {json.dumps(skeptic_rows)}\n\n"
            "Return compact JSON with keys: architecture_summary, attack_paths, new_assets, diagram_corrections, learning_suggestions, open_questions, fixed_information\n\n"
            "The 'diagram_corrections' array should list missing assets, missing connections, incorrect hierarchy, incorrect grouping, and internet exposure issues.\n"
            "Also check privilege attack paths: if compromised compute can manage automation, managed identities, or broad Contributor/Owner scopes, require explicit diagram arrows or notes.\n"
            "For data stores, distinguish anonymous public access from public endpoints that still require authentication.\n"
            "The attack_paths array must use objects with: title, path, summary, impact, confidence, evidence.\n"
            "When recommending fixes, include concrete code or rule targets when known."
        )

    def _gen():
        def _wait_with_heartbeats(label: str, func, interval: float = 2.0):
            """Run a blocking step while emitting periodic SSE heartbeats."""
            result_box: dict[str, object] = {}
            error_box: list[BaseException] = []
            done = threading.Event()

            def _worker():
                try:
                    result_box["value"] = func()
                except BaseException as exc:  # noqa: BLE001 - propagate exact failure
                    error_box.append(exc)
                finally:
                    done.set()

            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()

            while not done.wait(interval):
                yield _sse("log", f"[Web] {label} still running...")

            if error_box:
                raise error_box[0]
            return result_box.get("value")

        conn = _get_db()
        if conn is None:
            yield _sse("error", "DB unavailable")
            return

        try:
            resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
            if not resolved_exp_id:
                yield _sse("error", f"No completed scan found for {repo_name}.")
                return
        finally:
            conn.close()

        key = _ai_job_key(resolved_exp_id, repo_name)
        analysis_mode = (request.args.get("mode") or "").strip().lower()
        raw_file = _ai_raw_output_file(key)

        with _AI_ANALYSIS_LOCK:
            existing = _AI_ANALYSIS_JOBS.get(key)
            if existing and existing.get("status") == "running":
                yield _sse("log", "Already running")
                yield _sse("done", {"status": "running"})
                return

        _AI_ANALYSIS_JOBS[key] = {
            "status": "running",
            "experiment_id": resolved_exp_id,
            "repo_name": repo_name,
            "started_at": time.time(),
            "last_activity_at": time.time(),
            "completed_at": None,
            "steps": [],
            "logs": [],
            "error": "",
            "analysis_mode": analysis_mode or "default",
        }

        _append_ai_job_log(key, "Copilot streaming job started")
        base_sse = globals()["_sse"]

        def _sse(event: str, data) -> str:
            _touch_ai_job_activity(key)
            return base_sse(event, data)
        
        # ═══ RESUME DETECTION ═══
        # Check if we can skip steps based on previous work
        resume_state = _detect_resume_state(resolved_exp_id, repo_name, key)
        
        if resume_state["resume_from_step"] == 3:
            yield _sse("log", "⚡ Resume detected: Skipping Steps 1-2, jumping to parsing & persisting...")
            _append_ai_job_log(key, f"Resume from Step 3: raw output found ({len(resume_state.get('parsed_content') or {})} keys)")
            combined = raw_file.read_text(encoding='utf-8', errors='replace') if resume_state["has_raw_output"] else ""
        else:
            combined = None
        
        if resume_state["resume_from_step"] <= 1:
            yield _sse("log", "Step 1/3: Collecting repository facts from DB...")
            try:
                facts = yield from _wait_with_heartbeats(
                    "Collecting repository facts",
                    lambda: _fetch_overview_facts(resolved_exp_id, repo_name),
                )
            except BaseException as step_exc:  # noqa: BLE001 - surface exact failure to the UI
                err = f"Repo facts collection failed: {step_exc}"
                _append_ai_job_log(key, err)
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["status"] = "failed"
                    job["error"] = err
                    job["completed_at"] = time.time()
                    job.pop("process", None)
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("error", err)
                return
            if not facts:
                err = "Repo facts unavailable (missing DB rows)"
                _append_ai_job_log(key, err)
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["status"] = "failed"
                    job["error"] = err
                    job["completed_at"] = time.time()
                    job.pop("process", None)
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("error", err)
                return

            if analysis_mode == "architecture":
                yield _sse("log", "Step 2/3: Running focused architecture AI review...")
            else:
                yield _sse("log", "Step 2/3: Running focused per-agent AI reviews (Security → Dev → Platform)...")
        else:
            # Resuming, need to fetch facts for context but skip the AI review
            try:
                facts = yield from _wait_with_heartbeats(
                    "Collecting repository facts for resume",
                    lambda: _fetch_overview_facts(resolved_exp_id, repo_name),
                )
            except BaseException as step_exc:  # noqa: BLE001 - surface exact failure to the UI
                err = f"Repo facts collection failed during resume: {step_exc}"
                _append_ai_job_log(key, err)
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["status"] = "failed"
                    job["error"] = err
                    job["completed_at"] = time.time()
                    job.pop("process", None)
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("error", err)
                return
            if not facts:
                err = "Repo facts unavailable for resume (missing DB rows)"
                _append_ai_job_log(key, err)
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["status"] = "failed"
                    job["error"] = err
                    job["completed_at"] = time.time()
                    job.pop("process", None)
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("error", err)
                return
            
            _append_ai_job_log(key, "Resume: Skipping Step 2 (AI review), using cached Copilot output")

        _, fact_json = facts
        # Gather recent skeptic reviews for prompt enrichment (if present)
        skeptic_rows = []
        try:
            conn3 = _get_db()
            if conn3:
                try:
                    skeptic_rows = conn3.execute(
                        "SELECT f.id AS finding_id, f.rule_id, f.title, f.severity_score, sr.reviewer_type, sr.adjusted_score, sr.confidence, sr.reasoning FROM findings f LEFT JOIN skeptic_reviews sr ON sr.finding_id = f.id WHERE f.experiment_id = ?",
                        (resolved_exp_id,),
                    ).fetchall()
                    skeptic_rows = [dict(r) for r in skeptic_rows]
                finally:
                    conn3.close()
        except Exception:
            skeptic_rows = []

        # Extract baseline data for prompt builder
        findings_data = (fact_json or {}).get("findings", [])
        assets_data = (fact_json or {}).get("assets", [])
        diagrams_data = (fact_json or {}).get("diagrams", [])

        baseline_data = {
            "findings": findings_data,
            "resources": assets_data,
            "diagrams": diagrams_data,
            "roles": (fact_json or {}).get("roles", []),
            "ports": (fact_json or {}).get("ports", []),
            "attack_paths": (fact_json or {}).get("attack_paths", []),
            "placeholder_tldr": f"Repository with {len(assets_data)} resources, {len(findings_data)} findings",
            "skeptic_reviews": skeptic_rows,
        }

        # ═══ STEP 2: Run AI Agents (or Skip if Resuming) ═══
        if analysis_mode == "architecture":
            REVIEWER_AGENTS = [
                ("ArchitectureValidationAgent", "🏗️ Architecture validation", "architecture_review"),
            ]
        else:
            REVIEWER_AGENTS = [
                ("ContextDiscoveryAgent", "🔍 Context extraction", "context_extraction"),
                ("SecurityAgent",         "🔒 Security",           "security_review"),
                ("DevSkeptic",            "💻 Dev Skeptic",         "dev_review"),
                ("PlatformSkeptic",       "☁️ Platform",           "platform_review"),
            ]

        per_agent_combined: dict[str, str] = {}
        aggregated_attack_paths: list[dict] = []
        temp_copy_dir = None
        
        if resume_state["resume_from_step"] <= 2:
            # Normal path: Run reviewer agents
            # Initialise per-agent status in the job dict
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key, {})
                job["agent_steps"] = {name: "pending" for name, _, _ in REVIEWER_AGENTS}
                _AI_ANALYSIS_JOBS[key] = job

            # We stream *progress* + a compact formatted result, not the raw token stream.
            model = os.environ.get("COPILOT_MODEL", "gpt-5.4-mini")

            # Set up a single working copy of the repo that all three agents share.
            repo_cwd = str(REPO_ROOT)
            try:
                conn2 = _get_db()
                if conn2:
                    try:
                        row = conn2.execute(
                            "SELECT COALESCE(path, '') AS path FROM repositories WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1",
                            (resolved_exp_id, repo_name),
                        ).fetchone()
                        if row and row["path"]:
                            p = Path(row["path"]).expanduser().resolve()
                            if p.exists() and p.is_dir():
                                # Create a temporary copy inside the server's Output/WorkingCopies folder
                                wc_root = REPO_ROOT / "Output" / "WorkingCopies"
                                wc_root.mkdir(parents=True, exist_ok=True)
                                dest = wc_root / f"{resolved_exp_id}_{repo_name}"
                                # Remove any stale copy first
                                if dest.exists():
                                    try:
                                        import shutil

                                        shutil.rmtree(dest)
                                    except Exception:
                                        pass
                                try:
                                    import shutil

                                    shutil.copytree(p, dest, dirs_exist_ok=True)
                                    temp_copy_dir = dest
                                    repo_cwd = str(dest)
                                except Exception:
                                    # Fall back to original repo path if copy fails
                                    repo_cwd = str(p)
                    finally:
                        conn2.close()
            except Exception:
                # Best-effort: fall back to REPO_ROOT when DB lookup fails
                pass

            copilot_cmd, copilot_err = _resolve_copilot_command()
            if copilot_err:
                _append_ai_job_log(key, copilot_err)
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["status"] = "failed"
                    job["error"] = copilot_err
                    job["completed_at"] = time.time()
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("error", copilot_err)
                if temp_copy_dir:
                    try:
                        import shutil

                        shutil.rmtree(temp_copy_dir)
                    except Exception:
                        pass
                return

            # Run one focused Copilot job per reviewer agent.
            # The first reviewer output drives the merged parse; all results are saved.
        rc = 0  # aggregate exit code — set to non-zero if any agent fails

        for agent_idx, (agent_name, agent_label, agent_section_key) in enumerate(REVIEWER_AGENTS, 1):
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key, {})
                job["active_agent"] = agent_name
                job["active_agent_label"] = agent_label
                job.setdefault("agent_steps", {})[agent_name] = "running"
                _AI_ANALYSIS_JOBS[key] = job

            yield _sse("log", f"")
            yield _sse("log", f"▶ STAGE [{agent_idx}/{len(REVIEWER_AGENTS)}] — {agent_label}")

            agent_content = AGENT_INSTRUCTIONS.get(agent_name, "") if PROMPT_BUILDER_AVAILABLE and AGENT_INSTRUCTIONS else ""
            if PROMPT_BUILDER_AVAILABLE and agent_content:
                try:
                    if agent_name == "ContextDiscoveryAgent":
                        agent_prompt = yield from _wait_with_heartbeats(
                            f"Building {agent_name} prompt",
                            lambda: prompt_builder.build_context_extraction_prompt(
                                agent_content=agent_content,
                                baseline_data=baseline_data,
                                repo_name=repo_name,
                                experiment_id=resolved_exp_id,
                            ),
                        )
                    elif agent_name == "ArchitectureValidationAgent":
                        agent_prompt = yield from _wait_with_heartbeats(
                            f"Building {agent_name} prompt",
                            lambda: prompt_builder.build_architecture_review_prompt(
                                agent_content=agent_content,
                                baseline_data=baseline_data,
                                repo_name=repo_name,
                                experiment_id=resolved_exp_id,
                            ),
                        )
                    else:
                        agent_prompt = yield from _wait_with_heartbeats(
                            f"Building {agent_name} prompt",
                            lambda: prompt_builder.build_focused_prompt(
                                agent_name=agent_name,
                                agent_content=agent_content,
                                baseline_data=baseline_data,
                                repo_name=repo_name,
                                experiment_id=resolved_exp_id,
                            ),
                        )
                    yield _sse("log", f"  ↳ Prompt ready: {len(agent_prompt):,} chars ({agent_name})")
                except Exception as build_err:
                    yield _sse("log", f"  ✗ Prompt build failed: {build_err} — using legacy fallback")
                    if agent_name == "ArchitectureValidationAgent":
                        agent_prompt = _build_legacy_architecture_prompt(repo_name, fact_json, skeptic_rows)
                    else:
                        agent_prompt = _build_legacy_prompt(repo_name, fact_json, skeptic_rows)
            else:
                if agent_name == "ArchitectureValidationAgent":
                    agent_prompt = _build_legacy_architecture_prompt(repo_name, fact_json, skeptic_rows)
                else:
                    agent_prompt = _build_legacy_prompt(repo_name, fact_json, skeptic_rows)
                yield _sse("log", f"  ↳ Agent instructions unavailable — using legacy prompt")

            prompt_file = None
            prompt_args, prompt_file = _prepare_copilot_prompt(agent_prompt, repo_cwd)
            if prompt_file:
                _append_ai_job_log(key, f"Prompt staged in {prompt_file}")
                yield _sse("log", f"  ↳ Prompt staged to file (avoids command-line limits)")

            cmd = [
                *copilot_cmd,
                "--no-color",
                "--model",
                model,
                "--stream",
                "off",
                "-s",
                "--allow-all-tools",
                "--allow-all-paths",
                "--allow-all-urls",
                *prompt_args,
            ]

            launch_cmd, launch_cwd, launch_env, launch_note, launch_err = _build_copilot_launch(cmd, repo_cwd)
            if launch_err:
                _append_ai_job_log(key, launch_err)
                if prompt_file:
                    try:
                        if prompt_file.exists():
                            prompt_file.unlink()
                    except Exception:
                        pass
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job.setdefault("agent_steps", {})[agent_name] = "failed"
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("log", f"  ✗ Launch failed — skipping: {launch_err}")
                per_agent_combined[agent_name] = ""
                continue
            if launch_note:
                yield _sse("log", f"  ↳ {launch_note}")

            yield _sse("log", f"  ↳ Copilot starting… (model: {model})")

            try:
                proc = subprocess.Popen(
                    launch_cmd,
                    cwd=launch_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=launch_env,
                )
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["process"] = proc
                    _AI_ANALYSIS_JOBS[key] = job
            except Exception as exc:
                err = f"Failed to start Copilot for {agent_name}: {exc}"
                _append_ai_job_log(key, err)
                if prompt_file:
                    try:
                        if prompt_file.exists():
                            prompt_file.unlink()
                    except Exception:
                        pass
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job.setdefault("agent_steps", {})[agent_name] = "failed"
                    _AI_ANALYSIS_JOBS[key] = job
                yield _sse("log", f"  ✗ {err}")
                per_agent_combined[agent_name] = ""
                continue

            start_ts = time.time()
            last_hb = start_ts
            lines: list[str] = []
            
            # Provide context-specific thinking messages based on agent type
            thinking_stages = {
                "ContextDiscoveryAgent": [
                    "analyzing repository structure and dependencies",
                    "scanning for CI/CD pipelines and workflows",
                    "extracting framework and language information",
                    "identifying external service integrations",
                ],
                "SecurityAgent": [
                    "evaluating security findings and risk scores",
                    "analyzing access controls and permissions",
                    "checking for secrets and credential exposure",
                    "assessing data protection mechanisms",
                ],
                "ArchitectureValidationAgent": [
                    "validating cloud architecture design",
                    "checking resource relationships and dependencies",
                    "verifying security zones and boundaries",
                    "analyzing data flows and exposure paths",
                ],
                "DevSkeptic": [
                    "reviewing code patterns and best practices",
                    "analyzing configuration and deployment logic",
                    "checking for common development vulnerabilities",
                    "validating error handling and logging",
                ],
                "PlatformSkeptic": [
                    "evaluating platform architecture and scaling",
                    "checking infrastructure as code practices",
                    "analyzing network configuration and security groups",
                    "validating compliance and operational controls",
                ],
            }
            stage_messages = thinking_stages.get(agent_name, ["analyzing…"])

            try:
                while True:
                    reads, _, _ = select.select([proc.stdout], [], [], 0.5)
                    if reads:
                        raw = proc.stdout.readline()
                        if raw == "" and proc.poll() is not None:
                            break
                        if raw:
                            lines.append(raw.rstrip("\n"))

                    now = time.time()
                    if now - last_hb >= 2.0:
                        elapsed = int(now - start_ts)
                        # Cycle through thinking stages based on elapsed time
                        stage_idx = (elapsed // 2) % len(stage_messages)
                        thinking_msg = stage_messages[stage_idx]
                        yield _sse("log", f"  ⏳ {elapsed}s — {thinking_msg}")
                        last_hb = now

                    if proc.poll() is not None:
                        for raw in proc.stdout or []:
                            if raw:
                                lines.append(raw.rstrip("\n"))
                        break
            finally:
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
                finally:
                    with _AI_ANALYSIS_LOCK:
                        job = _AI_ANALYSIS_JOBS.get(key, {})
                        if job.get("process") is proc:
                            job.pop("process", None)
                            _AI_ANALYSIS_JOBS[key] = job

            if prompt_file:
                try:
                    if prompt_file.exists():
                        prompt_file.unlink()
                except Exception:
                    pass

            elapsed_total = round(time.time() - start_ts, 1)
            agent_rc = proc.returncode
            agent_combined = "\n".join([ln for ln in lines if ln and ln.strip()])
            per_agent_combined[agent_name] = agent_combined

            # On non-zero exit or empty output, dump the raw lines to the log for debugging
            if agent_rc != 0 or not agent_combined.strip():
                if agent_rc != 0:
                    rc = agent_rc  # propagate failure to overall rc
                yield _sse("log", f"  ✗ {agent_label} exited with code {agent_rc}" if agent_rc != 0 else f"  ✗ {agent_label} produced no output")
                raw_lines_preview = [ln for ln in lines if ln.strip()][:20]
                if raw_lines_preview:
                    yield _sse("log", f"  ↳ Raw output ({len(raw_lines_preview)} lines shown):")
                    for raw_ln in raw_lines_preview:
                        yield _sse("log", f"    {raw_ln}")
                else:
                    yield _sse("log", f"  ↳ No output captured from process")
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job.setdefault("agent_steps", {})[agent_name] = "failed"
                    _AI_ANALYSIS_JOBS[key] = job
                per_agent_combined[agent_name] = ""
                continue

            # Show a brief preview of what the AI produced and persist key fields per-agent
            try:
                preview = _extract_json_object(agent_combined)
                if preview:
                    summary_text = (
                        preview.get("architecture_summary")
                        or preview.get("context_summary")
                        or preview.get("enhanced_tldr")
                        or ""
                    )
                    if summary_text:
                        short = summary_text[:220].replace("\n", " ")
                        yield _sse("log", f"  💬 {short}{'…' if len(summary_text) > 220 else ''}")
                        # Persist the context summary — this is the best human-readable
                        # description of the repo and should lead the Overview tab.
                        # ContextDiscoveryAgent owns this; only overwrite if we have something better.
                        try:
                            db_helpers.upsert_context_metadata(
                                resolved_exp_id,
                                repo_name,
                                "ai_context_summary",
                                summary_text,
                                namespace="ai_overview",
                                source=f"copilot_{agent_name}",
                            )
                        except Exception:
                            pass

                    # Persist per-agent summaries that aren't in the final merged parse
                    per_agent_persist = {
                        "project_summary": f"ai_project_summary_{agent_name}",
                        "security_summary": f"ai_security_summary",
                        "dev_summary": f"ai_dev_summary",
                        "platform_summary": f"ai_platform_summary",
                    }
                    for src_key, dest_key in per_agent_persist.items():
                        agent_val = (preview.get(src_key) or "").strip()
                        if agent_val:
                            try:
                                db_helpers.upsert_context_metadata(
                                    resolved_exp_id,
                                    repo_name,
                                    dest_key,
                                    agent_val,
                                    namespace="ai_overview",
                                    source=f"copilot_{agent_name}",
                                )
                            except Exception:
                                pass

                    new_assets = preview.get("new_assets") or []
                    if new_assets:
                        yield _sse("log", f"  📦 {len(new_assets)} new asset(s) identified")
                    gaps = preview.get("connection_gaps") or []
                    if gaps:
                        yield _sse("log", f"  🔗 {len(gaps)} connection gap(s) found")
                    adjustments = preview.get("score_adjustments") or []
                    if adjustments:
                        yield _sse("log", f"  ⚖️  {len(adjustments)} score adjustment(s) proposed")
                    attack_paths = _normalize_attack_paths(preview.get("attack_paths"), reviewer=agent_name)
                    if attack_paths:
                        aggregated_attack_paths.extend(attack_paths)
                        yield _sse("log", f"  🛤️  {len(attack_paths)} attack path(s) proposed")
                    questions = preview.get("open_questions") or []
                    if questions:
                        yield _sse("log", f"  ❓ {len(questions)} open question(s) raised")
            except Exception:
                pass

            # Persist this agent's raw output for diagnostics and section status tracking
            try:
                db_helpers.upsert_ai_section(
                    resolved_exp_id,
                    repo_name,
                    section_key=agent_section_key,
                    title=f"{agent_label} Review",
                    content_html=f"<pre style='white-space:pre-wrap'>{agent_combined[:4000]}</pre>",
                    generated_by=agent_name,
                )
            except Exception:
                pass

            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key, {})
                job.setdefault("agent_steps", {})[agent_name] = "done"
                _AI_ANALYSIS_JOBS[key] = job
            yield _sse("log", f"  ✓ {agent_label} complete in {elapsed_total}s")

        # SecurityAgent output drives the main parse; fall back to whichever ran.
        # (or use cached output if resuming from Step 3)
        if resume_state["resume_from_step"] <= 2:
            if analysis_mode == "architecture":
                combined_source = per_agent_combined.get("ArchitectureValidationAgent") or ""
            else:
                combined_source = (
                    per_agent_combined.get("SecurityAgent")
                    or per_agent_combined.get("DevSkeptic")
                    or per_agent_combined.get("PlatformSkeptic")
                    or ""
                )
            combined = combined_source
        else:
            # Already have combined from resume detection
            pass

        yield _sse("log", f"")
        yield _sse("log", f"▶ STEP 3/3 — Parsing & persisting results")
        # Persist raw Copilot output for diagnostics
        try:
            raw_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                raw_file.write_text(combined, encoding='utf-8')
                _append_ai_job_log(key, f"Raw Copilot output saved to: {raw_file}")
            except Exception:
                _append_ai_job_log(key, "Failed to save raw Copilot output to file")
        except Exception:
            pass

        parsed = _extract_json_object(combined)
        output_summary = _summarize_copilot_output(combined)
        parse_error = ""

        if parsed:
            # Stream a compact, human-friendly view to the Log panel.
            yield _sse("log", "")
            yield _sse("log", "=== Copilot Summary ===")
            for label, k in [
                ("Project", "project_summary"),
                ("Deployment", "deployment_summary"),
                ("Interactions", "interactions_summary"),
                ("Auth", "auth_summary"),
                ("Dependencies", "dependencies_summary"),
                ("Issues", "issues_summary"),
                ("Skeptics", "skeptic_summary"),
            ]:
                val = (parsed.get(k) or "").strip()
                if val:
                    # Prevent embedded newlines from breaking log prefixes/formatting.
                    val = re.sub(r"\s+", " ", val).strip()
                    yield _sse("log", f"{label}: {val}")

            # New assets detected by the AI
            new_assets = parsed.get("new_assets")
            if isinstance(new_assets, list) and new_assets:
                yield _sse("log", "")
                yield _sse("log", f"New assets reported by AI ({len(new_assets)} total — dedup runs at persist stage):")
                for a in new_assets:
                    try:
                        name = a.get("name") or a.get("label") if isinstance(a, dict) else str(a)
                        atype = a.get("type", "") if isinstance(a, dict) else ""
                        label = f"{name} ({atype})" if atype else name
                    except Exception:
                        label = str(a)
                    yield _sse("log", f"- {label}")

            # Fixed information reported by the AI
            fixed_info = parsed.get("fixed_information") or parsed.get("fixed_info")
            if isinstance(fixed_info, list) and fixed_info:
                yield _sse("log", "")
                yield _sse("log", "Fixed information (what was corrected and why):")
                for f in fixed_info:
                    s = f if not isinstance(f, dict) else (f.get("description") or str(f))
                    yield _sse("log", f"- {s}")

            observations = parsed.get("observations")
            if isinstance(observations, list) and observations:
                yield _sse("log", "")
                yield _sse("log", "Observations (potential improvements / inconsistencies):")
                clean_obs = []
                for o in observations[:8]:
                    if isinstance(o, dict):
                        title = str(o.get('title') or '').strip()
                        detail = str(o.get('detail') or '').strip()
                        target = str(o.get('target') or '').strip()
                        s = title or detail or target
                        if s:
                            # Sanitise references: keep only safe scalar fields
                            raw_refs = o.get('references') or []
                            refs = []
                            if isinstance(raw_refs, list):
                                for ref in raw_refs[:4]:
                                    if not isinstance(ref, dict):
                                        continue
                                    refs.append({
                                        "finding_id": ref.get("finding_id"),
                                        "rule_id": str(ref.get("rule_id") or "").strip(),
                                        "file": str(ref.get("file") or "").strip(),
                                        "line": ref.get("line"),
                                        "snippet": str(ref.get("snippet") or "").strip()[:200],
                                    })
                            clean_obs.append({"title": title, "detail": detail, "target": target, "references": refs})
                            suffix = f" ({target})" if target else ""
                            yield _sse("log", f"- {(title or detail)}{suffix}")
                    else:
                        s = str(o).strip()
                        if s:
                            clean_obs.append({"title": s, "detail": "", "target": "", "references": []})
                            yield _sse("log", f"- {s}")
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_observations",
                        json.dumps(clean_obs),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

            attack_paths = _dedupe_attack_paths(
                _normalize_attack_paths(parsed.get("attack_paths")) + aggregated_attack_paths
            )
            if attack_paths:
                yield _sse("log", "")
                yield _sse("log", "Attack paths:")
                for attack_path in attack_paths[:8]:
                    headline = str(attack_path.get("title") or attack_path.get("path") or "Attack path").strip()
                    path_text = str(attack_path.get("path") or "").strip()
                    impact_text = str(attack_path.get("impact") or "").strip()
                    suffix = f" [{attack_path.get('reviewer')}]" if attack_path.get("reviewer") else ""
                    yield _sse("log", f"- {headline}{suffix}")
                    if path_text:
                        yield _sse("log", f"  path: {path_text}")
                    if impact_text:
                        yield _sse("log", f"  impact: {impact_text}")
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_attack_paths",
                        json.dumps(attack_paths[:12]),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

            asset_visibility = parsed.get("asset_visibility")
            if isinstance(asset_visibility, list) and asset_visibility:
                yield _sse("log", "")
                yield _sse("log", "Asset visibility suggestions (what to hide/show):")
                clean_vis = []
                for v in asset_visibility[:20]:
                    if not isinstance(v, dict):
                        continue
                    rtype = str(v.get('resource_type') or '').strip()
                    name = str(v.get('resource_name') or '').strip()
                    decision = str(v.get('decision') or '').strip().lower()  # show/hide
                    reason = str(v.get('reason') or '').strip()
                    if not (rtype or name):
                        continue
                    clean_vis.append({"resource_type": rtype, "resource_name": name, "decision": decision, "reason": reason})
                    label = name or rtype
                    yield _sse("log", f"- {label}: {decision or 'review'}{(' — ' + reason) if reason else ''}")
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_asset_visibility",
                        json.dumps(clean_vis),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

            learning = parsed.get("learning_suggestions")
            if isinstance(learning, list) and learning:
                yield _sse("log", "")
                yield _sse("log", "Learning suggestions (improve rules/logic):")
                clean_learning = []
                for s in learning[:12]:
                    if not isinstance(s, dict):
                        continue
                    kind = str(s.get('kind') or '').strip()
                    target = str(s.get('target') or '').strip()
                    rationale = str(s.get('rationale') or '').strip()
                    evidence = str(s.get('example_evidence') or '').strip()
                    proposed = str(s.get('proposed_change') or '').strip()
                    if not (kind or target or rationale or proposed):
                        continue
                    clean_learning.append({
                        "kind": kind,
                        "target": target,
                        "rationale": rationale,
                        "example_evidence": evidence,
                        "proposed_change": proposed,
                    })
                    headline = (target or kind or 'suggestion').strip()
                    why = f" — {rationale}" if rationale else ""
                    yield _sse("log", f"- {kind}: {headline}{why}")
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_learning_suggestions",
                        json.dumps(clean_learning),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

            action_items = parsed.get("action_items")
            if isinstance(action_items, list) and action_items:
                yield _sse("log", "")
                yield _sse("log", "Action items:")
                for item in action_items[:6]:
                    if isinstance(item, dict):
                        title = str(item.get('title') or '').strip()
                        file = str(item.get('file') or '').strip()
                        line = item.get('line')
                        what = str(item.get('what') or '').strip()
                        why = str(item.get('why') or '').strip()
                        parts = [p for p in [title, what, why] if p]
                        loc = file + (f":{line}" if line else "")
                        if loc:
                            parts.append(loc)
                        s = " — ".join(parts)
                    else:
                        s = str(item).strip()
                    if s:
                        yield _sse("log", f"- {s}")

            open_q = parsed.get("open_questions")
            if isinstance(open_q, list) and open_q:
                yield _sse("log", "")
                yield _sse("log", "Open questions:")
                clean_q = []
                for item in open_q[:5]:
                    if isinstance(item, dict):
                        q = str(item.get('question') or '').strip()
                        fpath = str(item.get('file') or '').strip()
                        line = item.get('line')
                        asset = str(item.get('asset') or '').strip()
                        if q:
                            clean_q.append({"question": q, "file": fpath, "line": line, "asset": asset})
                            loc = fpath + (f":{line}" if line else "")
                            suffix = f" ({loc})" if loc else ""
                            yield _sse("log", f"- {q}{suffix}")
                    else:
                        s = str(item).strip()
                        if s:
                            clean_q.append({"question": s, "file": "", "line": None, "asset": ""})
                            yield _sse("log", f"- {s}")

                # Persist in Overview metadata as JSON for richer rendering.
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_open_questions",
                        json.dumps(clean_q),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

                # Auto-resolve questions that can be answered via static analysis
                try:
                    _auto_conn = _get_db()
                    if _auto_conn and clean_q:
                        auto_resolved = _auto_resolve_open_questions(clean_q, resolved_exp_id, repo_name, _auto_conn)
                        if auto_resolved:
                            yield _sse("log", f"[Auto-analysis] Resolved {len(auto_resolved)} question(s) via static analysis.")
                            for _qt, _ar in auto_resolved.items():
                                yield _sse("log", f"  ✓ {_ar['question'][:80]} → {_ar['answer']}")
                        _auto_conn.close()
                except Exception as _ae:
                    print(f"[auto_resolve] Error: {_ae}")

                # Store on the job so the UI can navigate user to Q&A after completion.
                with _AI_ANALYSIS_LOCK:
                    job = _AI_ANALYSIS_JOBS.get(key, {})
                    job["open_questions"] = clean_q
                    _AI_ANALYSIS_JOBS[key] = job

            # Persist to DB so Overview tab renders it.
            mapping = {
                "project_summary": "ai_project_summary",
                "deployment_summary": "ai_deployment_summary",
                "interactions_summary": "ai_interactions_summary",
                "auth_summary": "ai_auth_summary",
                "dependencies_summary": "ai_dependencies_summary",
                "issues_summary": "ai_issues_summary",
                "skeptic_summary": "ai_skeptic_summary",
            }
            for k_in, k_out in mapping.items():
                val = (parsed.get(k_in) or "").strip()
                if val:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        k_out,
                        val,
                        namespace="ai_overview",
                        source="copilot_stream",
                    )

            if isinstance(action_items, list) and action_items:
                # Prefer structured JSON for richer HTML rendering in Overview.
                if all(isinstance(x, dict) for x in action_items[:6]):
                    action_payload = json.dumps(action_items[:6])
                else:
                    action_payload = "; ".join([str(x).strip() for x in action_items[:6] if str(x).strip()])
                db_helpers.upsert_context_metadata(
                    resolved_exp_id,
                    repo_name,
                    "ai_action_items",
                    action_payload,
                    namespace="ai_overview",
                    source="copilot_stream",
                )

            # ai_open_questions is now persisted above (as JSON) when present.

            # Persist new assets and fixed information for later review
            if isinstance(new_assets, list) and new_assets:
                try:
                    # Normalize to list of dicts with name/type when possible
                    normalized = []
                    for a in new_assets:
                        if isinstance(a, dict):
                            normalized.append(a)
                        else:
                            s = str(a)
                            # Heuristic: split 'Name (type1, type2)'
                            m = re.match(r"^(.*?)\s*\((.*)\)", s)
                            if m:
                                name = m.group(1).strip()
                                types = [t.strip() for t in m.group(2).split(',') if t.strip()]
                                normalized.append({"label": name, "types": types})
                            else:
                                normalized.append({"label": s})

                    # Fuzzy match against existing resources: attempt to find likely matches
                    conn = _get_db()
                    matches = []
                    if conn:
                        try:
                            rows = conn.execute(
                                "SELECT id, resource_name, resource_type, provider FROM resources WHERE experiment_id = ?",
                                (resolved_exp_id,),
                            ).fetchall()
                            existing = [dict(r) for r in rows]
                        finally:
                            conn.close()
                    else:
                        existing = []

                    def _fuzzy_match(candidate_label, candidate_types):
                        # Simple case-insensitive containment + type overlap scoring
                        label = candidate_label.lower()
                        best = None
                        best_score = 0
                        for e in existing:
                            score = 0
                            if e.get('resource_name') and e['resource_name'].lower() == label:
                                score += 50
                            elif e.get('resource_name') and label in e['resource_name'].lower():
                                score += 20
                            # type overlap
                            etype = (e.get('resource_type') or '').lower()
                            for t in (candidate_types or []):
                                if t.lower() in etype or etype in t.lower():
                                    score += 10
                            if score > best_score:
                                best = e
                                best_score = score
                        return best, best_score

                    # Dedup guard: separate assets into truly novel vs already tracked.
                    # score >= 50 = exact name match in resources table → already tracked
                    # score 20-49 = partial match → flag but keep (may be a rename/alias)
                    # score  < 20 = no match → genuinely new
                    genuinely_new = []
                    already_tracked = []
                    for cand in normalized:
                        label = cand.get('label') or cand.get('name') or ''
                        types = cand.get('types') or []
                        match, score = _fuzzy_match(label, types)
                        entry = {
                            'candidate': cand,
                            'matched_resource_id': match['id'] if match else None,
                            'matched_name': match.get('resource_name') if match else None,
                            'matched_type': match.get('resource_type') if match else None,
                            'match_score': score,
                        }
                        if score >= 50:
                            already_tracked.append(entry)
                        else:
                            genuinely_new.append(entry)

                    if already_tracked:
                        yield _sse("log", f"  ⏭️  {len(already_tracked)} AI 'new' asset(s) skipped — already in resources table")

                    # Only persist genuinely new assets (not already tracked by the scanner)
                    if genuinely_new:
                        db_helpers.upsert_context_metadata(
                            resolved_exp_id,
                            repo_name,
                            "ai_new_assets",
                            json.dumps(genuinely_new),
                            namespace="ai_overview",
                            source="copilot_stream",
                        )
                    else:
                        # Clear any stale ai_new_assets from a previous run
                        try:
                            db_helpers.upsert_context_metadata(
                                resolved_exp_id,
                                repo_name,
                                "ai_new_assets",
                                json.dumps([]),
                                namespace="ai_overview",
                                source="copilot_stream",
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

            if isinstance(fixed_info, list) and fixed_info:
                try:
                    db_helpers.upsert_context_metadata(
                        resolved_exp_id,
                        repo_name,
                        "ai_fixed_information",
                        json.dumps(fixed_info),
                        namespace="ai_overview",
                        source="copilot_stream",
                    )
                except Exception:
                    pass

            # === NEW: Parse and persist AI enhancements from agent-based review ===
            
            # 1. Parse score_adjustments and persist to skeptic_reviews table
            score_adjustments = parsed.get("score_adjustments")
            if isinstance(score_adjustments, list) and score_adjustments:
                yield _sse("log", "")
                yield _sse("log", "Score adjustments (AI review):")
                persisted_count = 0
                for adj in score_adjustments:
                    if not isinstance(adj, dict):
                        continue
                    finding_id = adj.get("finding_id")
                    old_score = adj.get("old_score")
                    new_score = adj.get("new_score")
                    reasoning = adj.get("reasoning", "")
                    agent_used = adj.get("agent_used", "ai_copilot")
                    
                    if finding_id and new_score is not None:
                        try:
                            conn = _get_db()
                            if conn:
                                try:
                                    # Insert into skeptic_reviews table
                                    conn.execute(
                                        """
                                        INSERT INTO skeptic_reviews
                                            (finding_id, reviewer_type, score_adjustment, adjusted_score, 
                                             confidence, reasoning, recommendation, reviewed_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                        """,
                                        (
                                            finding_id,
                                            agent_used,  # e.g., "DevSkeptic", "PlatformSkeptic"
                                            float(new_score) - float(old_score) if old_score else 0,
                                            float(new_score),
                                            1.0,  # High confidence from AI
                                            reasoning,
                                            "confirm",  # Default recommendation
                                        ),
                                    )
                                    conn.commit()
                                    persisted_count += 1
                                    yield _sse("log", f"- Finding #{finding_id}: {old_score} → {new_score} ({agent_used})")
                                finally:
                                    conn.close()
                        except Exception as e:
                            _append_ai_job_log(key, f"Failed to persist score adjustment for finding {finding_id}: {e}")
                
                if persisted_count > 0:
                    yield _sse("log", f"Persisted {persisted_count} score adjustments to skeptic_reviews table")
            
            # 2. Parse enhanced_tldr and persist to repo_ai_content table
            enhanced_tldr = parsed.get("enhanced_tldr")
            if enhanced_tldr and isinstance(enhanced_tldr, str):
                try:
                    db_helpers.upsert_ai_section(
                        experiment_id=resolved_exp_id,
                        repo_name=repo_name,
                        section_key="enhanced_tldr",
                        title="AI-Enhanced Summary",
                        content_html=f"<p>{enhanced_tldr}</p>",
                        generated_by="copilot_agent_review",
                    )
                    yield _sse("log", "")
                    yield _sse("log", f"Enhanced TLDR: {enhanced_tldr[:100]}...")
                except Exception as e:
                    _append_ai_job_log(key, f"Failed to persist enhanced TLDR: {e}")
            
            # 3. Parse diagram_corrections and update cloud_diagrams table
            diagram_corrections = parsed.get("diagram_corrections")
            if isinstance(diagram_corrections, list) and diagram_corrections:
                yield _sse("log", "")
                yield _sse("log", "Diagram corrections:")
                corrected_count = 0
                for corr in diagram_corrections:
                    if not isinstance(corr, dict):
                        continue
                    diagram_title = corr.get("diagram_title")
                    issue_type = corr.get("issue_type")
                    correction = corr.get("correction")
                    corrected_mermaid = corr.get("corrected_mermaid_code")
                    
                    if diagram_title and correction:
                        yield _sse("log", f"- {diagram_title}: {issue_type} - {correction[:80]}...")
                        
                        # If AI provided corrected Mermaid code, update the diagram
                        if corrected_mermaid and isinstance(corrected_mermaid, str):
                            try:
                                conn = _get_db()
                                if conn:
                                    try:
                                        # Update cloud_diagrams table
                                        conn.execute(
                                            """
                                            UPDATE cloud_diagrams
                                            SET mermaid_code = ?,
                                                updated_at = CURRENT_TIMESTAMP
                                            WHERE experiment_id = ? AND diagram_title = ?
                                            """,
                                            (corrected_mermaid, resolved_exp_id, diagram_title),
                                        )
                                        conn.commit()
                                        corrected_count += 1
                                    finally:
                                        conn.close()
                            except Exception as e:
                                _append_ai_job_log(key, f"Failed to update diagram '{diagram_title}': {e}")
                        
                        # Store correction reasoning in repo_ai_content
                        try:
                            db_helpers.upsert_ai_section(
                                experiment_id=resolved_exp_id,
                                repo_name=repo_name,
                                section_key=f"diagram_correction_{diagram_title}",
                                title=f"Diagram Correction: {diagram_title}",
                                content_html=f"<p><strong>{issue_type}</strong>: {correction}</p>",
                                generated_by="copilot_agent_review",
                            )
                        except Exception as e:
                            _append_ai_job_log(key, f"Failed to persist diagram correction reasoning: {e}")
                
                if corrected_count > 0:
                    yield _sse("log", f"Updated {corrected_count} architecture diagrams with AI corrections")
            
            # 4. Parse description_enhancements and update findings table
            description_enhancements = parsed.get("description_enhancements")
            if isinstance(description_enhancements, list) and description_enhancements:
                yield _sse("log", "")
                yield _sse("log", "Enhanced descriptions:")
                enhanced_count = 0
                for enh in description_enhancements:
                    if not isinstance(enh, dict):
                        continue
                    finding_id = enh.get("finding_id")
                    enhanced_desc = enh.get("enhanced_description")
                    
                    if finding_id and enhanced_desc:
                        try:
                            conn = _get_db()
                            if conn:
                                try:
                                    conn.execute(
                                        """
                                        UPDATE findings
                                        SET description = ?,
                                            llm_enriched_at = CURRENT_TIMESTAMP
                                        WHERE id = ?
                                        """,
                                        (enhanced_desc, finding_id),
                                    )
                                    conn.commit()
                                    enhanced_count += 1
                                finally:
                                    conn.close()
                        except Exception as e:
                            _append_ai_job_log(key, f"Failed to update finding {finding_id} description: {e}")
                
                if enhanced_count > 0:
                    yield _sse("log", f"Enhanced {enhanced_count} finding descriptions")
            
            # === END: AI enhancements parsing ===

        # ── Finding themes analysis ───────────────────────────────────────────
        # Run a focused Copilot pass that looks at ALL findings, clusters them
        # by theme/pattern, and proposes a hypothesis the user can confirm or deny.
        yield _sse("log", "")
        yield _sse("log", "▶ Finding themes — analysing patterns across findings…")
        try:
            findings_for_themes = []
            conn_t = _get_db()
            if conn_t:
                try:
                    f_rows = conn_t.execute(
                        """
                        SELECT f.id, f.rule_id, f.title, f.description,
                               COALESCE(f.base_severity, 'MEDIUM') AS severity,
                               f.source_file, f.source_line_start,
                               r.resource_name, r.resource_type
                        FROM findings f
                        LEFT JOIN resources r ON f.resource_id = r.id
                        JOIN repositories repo ON f.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                        ORDER BY f.severity_score DESC, f.rule_id
                        LIMIT 60
                        """,
                        (resolved_exp_id, repo_name),
                    ).fetchall()
                    findings_for_themes = [dict(r) for r in f_rows]
                finally:
                    conn_t.close()
        except Exception as _fe:
            findings_for_themes = []
            _append_ai_job_log(key, f"Could not fetch findings for theme analysis: {_fe}")

        if findings_for_themes:
            themes_prompt = (
                "You are a security analyst reviewing findings from a static analysis scan.\n"
                "Your task is to identify COMMON THEMES across the findings listed below, "
                "then for each theme propose a plausible REASON that would explain all findings "
                "in that cluster — taking into account that the reason may be an intentional "
                "design decision (e.g. using subscription keys instead of JWT, internal-only traffic, etc.).\n\n"
                "For each theme, generate a YES/NO question the development team can answer to confirm "
                "your hypothesis. If true, it likely explains why those findings are acceptable (or not).\n\n"
                f"Repo: {repo_name}\n\n"
                f"Findings ({len(findings_for_themes)} total):\n"
                + "\n".join(
                    f"- [{f['severity']}] {f['title']} (rule: {f['rule_id'] or 'n/a'})"
                    + (f" | resource: {f['resource_name']} ({f['resource_type']})" if f.get('resource_name') else "")
                    + (f" | {f['source_file']}" if f.get('source_file') else "")
                    for f in findings_for_themes
                )
                + "\n\n"
                "Return ONLY a JSON array (no markdown). Each element must have:\n"
                '  "theme": short title (max 8 words)\n'
                '  "hypothesis": one sentence explaining the likely root cause or design reason\n'
                '  "question": a yes/no question for the team to confirm the hypothesis\n'
                '  "findings": list of rule_id strings that belong to this theme\n'
                '  "if_yes_means": one sentence — what confirming "yes" implies for risk\n'
                '  "if_no_means": one sentence — what confirming "no" implies for risk\n'
                "Limit to at most 5 themes. Only group findings where there is a genuine pattern."
            )

            copilot_cmd_t, copilot_err_t = _resolve_copilot_command()
            if not copilot_err_t:
                tmp_dir_t = Path(tempfile.mkdtemp())
                try:
                    prompt_args_t, prompt_file_t = _prepare_copilot_prompt(themes_prompt, str(tmp_dir_t))
                    cmd_t = [*copilot_cmd_t, "--no-color", *prompt_args_t]
                    t0_t = time.time()
                    proc_t = subprocess.run(
                        cmd_t, capture_output=True, text=True, timeout=600
                    )
                    elapsed_t = round(time.time() - t0_t, 1)
                    raw_t = (proc_t.stdout or "").strip()
                    if proc_t.returncode != 0 or not raw_t:
                        yield _sse("log", f"  ⚠ Theme analysis skipped (exit {proc_t.returncode})")
                    else:
                        # Extract JSON from output
                        themes_parsed = None
                        try:
                            m = re.search(r"\[.*\]", raw_t, re.DOTALL)
                            if m:
                                themes_parsed = json.loads(m.group(0))
                        except Exception:
                            themes_parsed = None

                        if isinstance(themes_parsed, list) and themes_parsed:
                            yield _sse("log", f"  ✓ {len(themes_parsed)} theme(s) identified in {elapsed_t}s")
                            for th in themes_parsed:
                                yield _sse("log", f"    • {th.get('theme', '?')}: {th.get('hypothesis', '')[:80]}")
                            try:
                                db_helpers.upsert_context_metadata(
                                    resolved_exp_id,
                                    repo_name,
                                    "ai_finding_themes",
                                    json.dumps(themes_parsed),
                                    namespace="ai_overview",
                                    source="copilot_stream",
                                )
                            except Exception:
                                pass
                        else:
                            yield _sse("log", "  (no clear themes identified)")
                finally:
                    try:
                        import shutil as _shutil
                        _shutil.rmtree(tmp_dir_t, ignore_errors=True)
                    except Exception:
                        pass
        else:
            yield _sse("log", "  (no findings to analyse)")

        
            yield _sse("log", "Copilot output wasn't valid JSON; no summaries extracted.")
            parse_error = "Copilot completed but did not return valid JSON"
            if output_summary:
                parse_error = f"{parse_error}: {output_summary}"

        with _AI_ANALYSIS_LOCK:
            job = _AI_ANALYSIS_JOBS.get(key, {})
            if parse_error:
                job["status"] = "failed"
                job["error"] = parse_error
            elif rc == 0 or bool(parsed):
                # Mark completed if Copilot produced valid JSON even when it exited non-zero
                # (API stream-close returns non-zero but output was successfully captured).
                job["status"] = "completed"
            else:
                job["status"] = "failed"
                job["error"] = job.get("error") or (
                    f"copilot exited with code {rc}: {output_summary}" if output_summary else f"copilot exited with code {rc}"
                )
            job["completed_at"] = time.time()
            _AI_ANALYSIS_JOBS[key] = job

        # ── Container image summaries ─────────────────────────────────────────
        # Collect all unique image names from resources + resource_properties,
        # ask Copilot what each one does, and persist to context_metadata.
        yield _sse("log", "")
        yield _sse("log", "▶ Container images — generating AI summaries…")
        try:
            image_names: list[str] = []
            conn_ci = _get_db()
            if conn_ci:
                try:
                    ci_rows = conn_ci.execute(
                        """
                        SELECT DISTINCT rp.property_value AS img
                        FROM resource_properties rp
                        JOIN resources r ON rp.resource_id = r.id
                        JOIN repositories repo ON r.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                          AND rp.property_key = 'image'
                          AND rp.property_value IS NOT NULL AND rp.property_value != ''
                        ORDER BY rp.property_value
                        """,
                        (resolved_exp_id, repo_name),
                    ).fetchall()
                    image_names = [r["img"] for r in ci_rows if r["img"]]
                finally:
                    conn_ci.close()
        except Exception as _cie:
            image_names = []
            _append_ai_job_log(key, f"Could not fetch container images: {_cie}")

        if image_names:
            img_prompt = (
                "You are a DevSecOps expert reviewing container images in a repository.\n"
                "For each image listed below, provide:\n"
                "1. A short plain-English summary of what the image/service likely does (1-2 sentences)\n"
                "2. Its typical role (e.g. web API, background worker, sidecar, message consumer, cache, etc.)\n"
                "3. Any notable security considerations for this image type (1 sentence, or null if none)\n\n"
                f"Repository: {repo_name}\n\n"
                "Images:\n"
                + "\n".join(f"- {img}" for img in image_names)
                + "\n\n"
                "Return ONLY a JSON object (no markdown). Keys are the exact image strings above. "
                "Each value must have: \"summary\" (string), \"role\" (string), \"security_note\" (string or null).\n"
                "Example: {\"nginx:alpine\": {\"summary\": \"Lightweight web server.\", \"role\": \"reverse proxy\", \"security_note\": \"Ensure HTTP headers are hardened.\"}}"
            )

            copilot_cmd_ci, err_ci = _resolve_copilot_command()
            if not err_ci:
                tmp_dir_ci = Path(tempfile.mkdtemp())
                try:
                    prompt_args_ci, _ = _prepare_copilot_prompt(img_prompt, str(tmp_dir_ci))
                    cmd_ci = [*copilot_cmd_ci, "--no-color", *prompt_args_ci]
                    t0_ci = time.time()
                    proc_ci = subprocess.run(cmd_ci, capture_output=True, text=True, timeout=600)
                    elapsed_ci = round(time.time() - t0_ci, 1)
                    raw_ci = (proc_ci.stdout or "").strip()

                    if proc_ci.returncode == 0 and raw_ci:
                        summaries_parsed = None
                        try:
                            m = re.search(r"\{.*\}", raw_ci, re.DOTALL)
                            if m:
                                summaries_parsed = json.loads(m.group(0))
                        except Exception:
                            summaries_parsed = None

                        if isinstance(summaries_parsed, dict) and summaries_parsed:
                            yield _sse("log", f"  ✓ Summarised {len(summaries_parsed)} image(s) in {elapsed_ci}s")
                            try:
                                db_helpers.upsert_context_metadata(
                                    resolved_exp_id,
                                    repo_name,
                                    "ai_container_summaries",
                                    json.dumps(summaries_parsed),
                                    namespace="ai_overview",
                                    source="copilot_stream",
                                )
                            except Exception:
                                pass
                        else:
                            yield _sse("log", f"  ⚠ Could not parse container summaries (exit {proc_ci.returncode})")
                    else:
                        yield _sse("log", f"  ⚠ Container summary skipped (exit {proc_ci.returncode})")
                finally:
                    try:
                        import shutil as _sh2
                        _sh2.rmtree(tmp_dir_ci, ignore_errors=True)
                    except Exception:
                        pass
        else:
            yield _sse("log", "  (no container images found)")

        if job.get("status") == "completed":
            try:
                db_helpers.upsert_context_metadata(
                    resolved_exp_id,
                    repo_name,
                    "ai_analysis_completed_at",
                    str(int(job["completed_at"])),
                    namespace="ai_overview",
                    source="copilot_stream",
                )
            except Exception:
                pass
            yield _sse("done", {"status": "completed"})
        else:
            # Persist failure so the status endpoint can recover after a disconnect
            try:
                db_helpers.upsert_context_metadata(
                    resolved_exp_id,
                    repo_name,
                    "ai_analysis_failed_at",
                    str(int(job.get("completed_at") or time.time())),
                    namespace="ai_overview",
                    source="copilot_stream",
                )
            except Exception:
                pass
            yield _sse("error", job.get("error") or "copilot failed")

        # Clean up any temporary repository copy we created
        if prompt_file:
            try:
                if prompt_file.exists():
                    prompt_file.unlink()
            except Exception:
                pass
        if temp_copy_dir:
            try:
                import shutil

                shutil.rmtree(temp_copy_dir)
            except Exception:
                pass

    return Response(stream_with_context(_gen()), mimetype="text/event-stream")


@app.route("/api/analysis/generate_rules/<experiment_id>/<repo_name>", methods=["POST"])
def api_analysis_generate_rules(experiment_id: str, repo_name: str):
    """Start a background job that asks Copilot to synthesise opengrep detection rules.

    The job runs similarly to the Copilot overview stream but writes generated
    rules into Output/WorkingCopies/<experiment>_<repo>/generated_rules.txt for
    review. It returns immediately with status 'started'.
    """
    # Resolve experiment_id the same way the status endpoint does so both use the same job key.
    _conn = _get_db()
    resolved_exp_id = experiment_id
    if _conn:
        try:
            _resolved = _get_experiment_for_repo(_conn, repo_name, experiment_id)
            if _resolved:
                resolved_exp_id = _resolved
        except Exception:
            pass
        finally:
            _conn.close()

    key = _ai_job_key(resolved_exp_id, repo_name) + ":generate_rules"
    with _AI_ANALYSIS_LOCK:
        existing = _AI_ANALYSIS_JOBS.get(key)
        if existing and existing.get("status") == "running":
            # Only block a re-run if the subprocess is actually still alive.
            # If the process has exited without updating status (e.g. crash, OOM) the
            # job would be stuck "running" forever — fall through to restart instead.
            proc = existing.get("process")
            if proc is None or proc.poll() is None:
                return jsonify({"status": "running"}), 202
            # Process is dead; reset and restart below.
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

    def _bg():
        key_local = key
        _append_ai_job_log(key_local, "Generate rules job started")
        # Gather brief findings and example snippets from DB
        conn = _get_db()
        snippets = []
        try:
            if conn:
                try:
                    rows = conn.execute(
                        "SELECT title, rule_id, source_file, source_line_start, source_line_end FROM findings WHERE experiment_id = ? AND repo_id = (SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name)=LOWER(?) LIMIT 1) LIMIT 20",
                        (resolved_exp_id, resolved_exp_id, repo_name),
                    ).fetchall()
                    for r in rows:
                        snippets.append({
                            "title": r["title"],
                            "rule_id": r["rule_id"],
                            "source_file": r["source_file"],
                            "start": r["source_line_start"],
                            "end": r["source_line_end"],
                        })
                finally:
                    conn.close()
        except Exception:
            pass

        prompt = (
            "You are a security engineer writing opengrep (semgrep-compatible) detection rules.\n"
            "Generate one opengrep YAML rule per finding listed below.\n"
            "Output ONLY raw YAML — no markdown fences, no prose, no explanation.\n"
            "Each rule must be a complete, standalone YAML document starting with 'rules:'.\n"
            "Separate multiple rules with a line containing only '---'.\n\n"
            "IMPORTANT formatting rules:\n"
            "- Use exactly 2-space indentation throughout.\n"
            "- The 'message' field MUST be a single-line quoted string, NOT a block scalar.\n"
            "  Example:  message: \"Explanation of risk and remediation in one line.\"\n"
            "- Do NOT use 'message: |' or 'message: >' — only quoted inline strings.\n\n"
            "Required YAML structure for each rule:\n"
            "rules:\n"
            "  - id: <kebab-case-id>\n"
            "    message: \"<one-line explanation of the risk and remediation>\"\n"
            "    severity: <INFO|WARNING|ERROR>\n"
            "    languages: [<language list e.g. python, javascript, java, csharp, go>]\n"
            "    patterns:\n"
            "      - pattern: |\n"
            "          <semgrep pattern>\n"
            "    metadata:\n"
            "      category: security\n"
            "      subcategory: [<relevant tags>]\n"
            "      technology: [<relevant tech>]\n"
            "      cwe: <CWE-NNN>\n\n"
            f"Repo: {repo_name}\n"
            f"Findings (JSON): {json.dumps(snippets)}\n"
        )

        wc_root = REPO_ROOT / "Output" / "WorkingCopies"
        wc_root.mkdir(parents=True, exist_ok=True)
        out_dir = wc_root / f"{resolved_exp_id}_{repo_name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "generated_rules.txt"
        rules_out_dir = REPO_ROOT / "Rules" / "Generated" / f"{resolved_exp_id}_{repo_name}"

        copilot_cmd, copilot_err = _resolve_copilot_command()
        if copilot_err:
            _append_ai_job_log(key_local, copilot_err)
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key_local, {})
                job["status"] = "failed"
                job["completed_at"] = time.time()
                job["error"] = copilot_err
                _AI_ANALYSIS_JOBS[key_local] = job
            return

        prompt_args, prompt_file = _prepare_copilot_prompt(prompt, str(out_dir))
        cmd = [
            *copilot_cmd,
            "--no-color",
            "--model",
            os.environ.get("COPILOT_MODEL", "gpt-5.4-mini"),
            "--stream",
            "off",
            *prompt_args,
        ]

        launch_cmd, launch_cwd, launch_env, launch_note, launch_err = _build_copilot_launch(cmd, str(out_dir))
        if launch_err:
            _append_ai_job_log(key_local, launch_err)
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key_local, {})
                job["status"] = "failed"
                job["completed_at"] = time.time()
                job["error"] = launch_err
                _AI_ANALYSIS_JOBS[key_local] = job
            return
        if launch_note:
            _append_ai_job_log(key_local, launch_note)

        try:
            popen = subprocess.Popen(
                launch_cmd, cwd=launch_cwd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=launch_env,
            )
            # Store process handle so the status endpoint uses the subprocess stale threshold
            # (default 3600s) instead of the 60s SSE-job threshold.
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key_local, {})
                job["process"] = popen
                _AI_ANALYSIS_JOBS[key_local] = job

            stdout_txt, stderr_txt = popen.communicate()
            txt = stdout_txt or ''

            # Save raw output for debugging
            try:
                out_file.write_text(txt, encoding='utf-8')
            except Exception:
                _append_ai_job_log(key_local, "Failed to save generated rules raw output")

            # Parse YAML blocks and write individual .yaml rule files
            saved_count = 0
            if txt.strip():
                try:
                    import yaml as _yaml
                    rules_out_dir.mkdir(parents=True, exist_ok=True)
                    extracted = _extract_rules_from_llm_output(txt)
                    for rule in extracted:
                        rule_id = rule.get('id', '')
                        if not rule_id:
                            continue
                        rule_path = rules_out_dir / f"{rule_id}.yaml"
                        try:
                            rule_path.write_text(
                                _yaml.dump({'rules': [rule]}, default_flow_style=False, allow_unicode=True),
                                encoding='utf-8',
                            )
                            saved_count += 1
                        except Exception:
                            pass
                except Exception as parse_err:
                    _append_ai_job_log(key_local, f"Rule parsing warning: {parse_err}")

            _append_ai_job_log(key_local, f"Rule generation completed — {saved_count} rule(s) saved to Rules/Generated/")
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key_local, {})
                job["status"] = "completed" if popen.returncode == 0 else "failed"
                job["completed_at"] = time.time()
                job["rules_saved"] = saved_count
                job["rules_dir"] = str(rules_out_dir)
                if popen.returncode != 0:
                    job["error"] = (stderr_txt or '').splitlines()[-1] if stderr_txt else f"exit {popen.returncode}"
                _AI_ANALYSIS_JOBS[key_local] = job
        except Exception as e:
            _append_ai_job_log(key_local, f"Rule generation failed: {e}")
            with _AI_ANALYSIS_LOCK:
                job = _AI_ANALYSIS_JOBS.get(key_local, {})
                job["status"] = "failed"
                job["completed_at"] = time.time()
                job["error"] = str(e)
                _AI_ANALYSIS_JOBS[key_local] = job
        finally:
            if prompt_file:
                try:
                    if prompt_file.exists():
                        prompt_file.unlink()
                except Exception:
                    pass

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/diagrams/blast_radius/<experiment_id>/<resource_name>")
def api_blast_radius(experiment_id: str, resource_name: str):
    """Return a Mermaid blast radius diagram for a specific resource."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Scripts.Generate.generate_diagram import generate_blast_radius_diagram  # type: ignore

        code = generate_blast_radius_diagram(experiment_id, resource_name)
        return jsonify({"code": code})
    except ValueError as exc:
        # Resource not found or other validation error
        app.logger.warning("Validation error in blast_radius for %s/%s: %s", experiment_id, resource_name, exc)
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        app.logger.exception("blast_radius error for %s/%s", experiment_id, resource_name)
        return jsonify({"error": f"Failed to generate blast radius diagram: {str(exc)}"}), 500



@app.route("/api/diagrams/<experiment_id>")
def api_diagrams(experiment_id: str):
    """Return Mermaid diagrams for a past experiment.

    Uses cloud_diagrams DB table only.
    """
    repo_name = (request.args.get("repo_name") or "").strip()
    include_api_operations_raw = (request.args.get("include_api_operations") or "").strip().lower()
    include_api_operations_override: bool | None = None
    if include_api_operations_raw in {"1", "true", "yes", "on"}:
        include_api_operations_override = True
    elif include_api_operations_raw in {"0", "false", "no", "off"}:
        include_api_operations_override = False
    # Always run with strict_architecture=False (synthetic/inferred edges enabled)
    strict_architecture = False
    force_regenerate = include_api_operations_override is not None

    def _edge_count(code: str) -> int:
        if not code:
            return 0
        return sum(1 for ln in code.splitlines() if ("-->" in ln or "-.>" in ln))

    def _response_payload(diagrams: list[dict]) -> dict:
        return {
            "diagrams": [
                {"title": d.get("diagram_title"), "code": d.get("mermaid_code")}
                for d in diagrams
                if d.get("mermaid_code")
            ]
        }

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Scripts.Persist.db_helpers import get_cloud_diagrams  # type: ignore

        # For repo-scoped requests, prefer returning per-provider diagrams.
        if repo_name:
            try:
                # First, check for persisted per-provider diagrams for this repo/experiment.
                db_diagrams = get_cloud_diagrams(experiment_id, repo_name=repo_name)
                if db_diagrams and not force_regenerate:
                    return jsonify(_response_payload(db_diagrams))

                # No persisted diagrams — regenerate per-provider from DB topology.
                from Scripts.Generate.generate_diagram import generate_architecture_diagram  # type: ignore
                from Scripts.Persist.db_helpers import get_db_connection as _get_conn  # type: ignore

                with _get_conn() as conn:
                    prov_rows = conn.execute(
                        """
                        SELECT DISTINCT LOWER(COALESCE(r.provider, '')) AS provider
                        FROM resources r
                        JOIN repositories repo ON repo.id = r.repo_id
                        WHERE r.experiment_id = ?
                          AND LOWER(repo.repo_name) = LOWER(?)
                          AND LOWER(COALESCE(r.provider, '')) NOT IN ('', 'unknown', 'terraform', 'kubernetes')
                        ORDER BY provider
                        """,
                        (experiment_id, repo_name),
                    ).fetchall()
                    providers = sorted({
                        _canonical_provider_key(row['provider'])
                        for row in prov_rows
                        if row['provider']
                    })

                generated: list[dict] = []
                for provider in providers:
                    try:
                        code = generate_architecture_diagram(
                            experiment_id,
                            repo_name=repo_name,
                            provider=provider,
                            include_operation_resources=include_api_operations_override,
                            strict_architecture=strict_architecture,
                        )
                    except Exception:
                        code = None
                    if code and "No resources found" not in code:
                        provider_display = _provider_display_name(provider)
                        generated.append({
                            "provider": provider_display,
                            "diagram_title": f"{provider_display} Architecture",
                            "mermaid_code": code,
                            "display_order": len(generated),
                        })
                        # Persist the regenerated diagrams for faster subsequent responses
                        if not force_regenerate:
                            try:
                                db_helpers.upsert_cloud_diagram(
                                    experiment_id=experiment_id,
                                    provider=provider,
                                    diagram_title=f"{provider_display} Architecture",
                                    mermaid_code=code,
                                    display_order=len(generated) - 1,
                                )
                            except Exception:
                                pass

                if generated:
                    return jsonify(_response_payload(generated))
            except Exception:
                # Fall back to legacy behaviour below
                pass

        db_diagrams = get_cloud_diagrams(experiment_id, repo_name=repo_name or None)

        # For repo-scoped requests, also fetch experiment-scoped diagrams 
        # (these may have been persisted from the initial scan before repo-filtering was added)
        if repo_name and not db_diagrams and not force_regenerate:
            db_diagrams = get_cloud_diagrams(experiment_id)
        
        # If still no diagrams found and repo_name was provided, fall back to regenerating
        # (diagrams may not have been persisted yet)

        # If persisted diagrams are missing/skeletal for this repo, regenerate from DB topology.
        if repo_name and (force_regenerate or not db_diagrams or max((_edge_count(d.get("mermaid_code") or "") for d in db_diagrams), default=0) == 0):
            try:
                from Scripts.Generate.generate_hierarchical_diagram import HierarchicalDiagramBuilder  # type: ignore

                _repo_path: Optional[str] = None
                try:
                    for ent in _resolve_repos():
                        if (ent.get('name') or '').lower() == repo_name.lower() and ent.get('found'):
                            _repo_path = ent.get('path')
                            break
                except Exception:
                    pass

                # Get list of providers to generate diagrams for
                providers: list[str] = []
                try:
                    from Scripts.Persist.db_helpers import get_db_connection as _get_conn  # type: ignore
                    with _get_conn() as conn:
                        prov_rows = conn.execute(
                            """
                            SELECT DISTINCT LOWER(COALESCE(r.provider, '')) AS provider
                            FROM resources r
                            JOIN repositories repo ON repo.id = r.repo_id
                            WHERE r.experiment_id = ?
                              AND LOWER(repo.repo_name) = LOWER(?)
                              AND LOWER(COALESCE(r.provider, '')) NOT IN ('', 'unknown', 'terraform', 'kubernetes')
                            ORDER BY provider
                            """,
                            (experiment_id, repo_name),
                        ).fetchall()
                        providers = sorted({
                            _canonical_provider_key(row['provider'])
                            for row in prov_rows
                            if row['provider']
                        })
                except Exception:
                    pass

                generated: list[dict] = []
                if providers:
                    # Generate per-provider diagrams
                    for provider in providers:
                        try:
                            _builder = HierarchicalDiagramBuilder(
                                experiment_id,
                                repo_name=repo_name,
                                repo_path=_repo_path,
                                provider_filter=provider,
                            )
                            _builder.load_data()
                            if _builder.resources:
                                _code = _builder.generate()
                                provider_display = _provider_display_name(provider)
                                if _code and "No resources found" not in _code:
                                    generated.append({
                                        "provider": provider_display,
                                        "diagram_title": f"{provider_display} Architecture",
                                        "mermaid_code": _code,
                                        "display_order": len(generated),
                                    })
                                    if not force_regenerate:
                                        try:
                                            db_helpers.upsert_cloud_diagram(
                                                experiment_id=experiment_id,
                                                provider=provider,
                                                diagram_title=f"{provider_display} Architecture",
                                                mermaid_code=_code,
                                                display_order=len(generated) - 1,
                                            )
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                else:
                    # Fallback to single diagram if no providers detected
                    _builder = HierarchicalDiagramBuilder(
                        experiment_id,
                        repo_name=repo_name,
                        repo_path=_repo_path,
                    )
                    _code = _builder.generate()
                    _provider = _canonical_provider_key(_builder.detect_cloud_provider())
                    _provider_display = _provider_display_name(_provider)

                    if _code and "No resources found" not in _code:
                        generated.append({
                            "provider": _provider_display,
                            "diagram_title": f"{_provider_display} Architecture",
                            "mermaid_code": _code,
                            "display_order": 0,
                        })
                        if not force_regenerate:
                            try:
                                db_helpers.upsert_cloud_diagram(
                                    experiment_id=experiment_id,
                                    provider=_provider,
                                    diagram_title=f"{_provider_display} Architecture",
                                    mermaid_code=_code,
                                    display_order=0,
                                )
                            except Exception:
                                pass

                if generated:
                    return jsonify(_response_payload(generated))
            except Exception:
                pass

        if db_diagrams:
            return jsonify(_response_payload(db_diagrams))
    except Exception:
        return jsonify({"diagrams": [], "error": "Failed to query cloud_diagrams"}), 500

    return jsonify({"diagrams": [], "error": f"No diagrams found in cloud_diagrams for experiment {experiment_id}"}), 404


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

    # If persisted diagrams are missing for either scan, attempt to regenerate
    # repo-scoped diagrams from DB topology (best-effort fallback).
    try:
        if (not diagrams_from) or (not diagrams_to):
            sys.path.insert(0, str(REPO_ROOT))
            from Scripts.Persist import db_helpers as _dbh  # type: ignore
            from Scripts.Generate.generate_diagram import generate_architecture_diagram  # type: ignore
            with _dbh.get_db_connection() as conn:
                prov_rows = conn.execute(
                    """
                    SELECT DISTINCT LOWER(COALESCE(r.provider, '')) AS provider
                    FROM resources r
                    JOIN repositories repo ON repo.id = r.repo_id
                    WHERE r.experiment_id = ?
                      AND LOWER(repo.repo_name) = LOWER(?)
                      AND LOWER(COALESCE(r.provider, '')) NOT IN ('', 'unknown', 'terraform', 'kubernetes')
                    ORDER BY provider
                    """,
                    (id_from, repo),
                ).fetchall()
                providers = sorted({
                    _canonical_provider_key(row["provider"])
                    for row in prov_rows
                    if row["provider"]
                })
                gen = []
                for provider in providers:
                    code = generate_architecture_diagram(id_from, repo_name=repo, provider=provider)
                    if code and "No resources found" not in code:
                        gen.append({"title": f"{_provider_display_name(provider)} Architecture", "code": code})
                if gen and not diagrams_from:
                    diagrams_from = gen
            # Repeat for 'to' scan
            with _dbh.get_db_connection() as conn:
                prov_rows = conn.execute(
                    """
                    SELECT DISTINCT LOWER(COALESCE(r.provider, '')) AS provider
                    FROM resources r
                    JOIN repositories repo ON repo.id = r.repo_id
                    WHERE r.experiment_id = ?
                      AND LOWER(repo.repo_name) = LOWER(?)
                      AND LOWER(COALESCE(r.provider, '')) NOT IN ('', 'unknown', 'terraform', 'kubernetes')
                    ORDER BY provider
                    """,
                    (id_to, repo),
                ).fetchall()
                providers = sorted({
                    _canonical_provider_key(row["provider"])
                    for row in prov_rows
                    if row["provider"]
                })
                gen = []
                for provider in providers:
                    code = generate_architecture_diagram(id_to, repo_name=repo, provider=provider)
                    if code and "No resources found" not in code:
                        gen.append({"title": f"{_provider_display_name(provider)} Architecture", "code": code})
                if gen and not diagrams_to:
                    diagrams_to = gen
    except Exception:
        # Best-effort fallback: if regeneration fails, continue with empty lists
        pass

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
                MAX(CASE UPPER(f.base_severity)
                    WHEN 'CRITICAL' THEN 5
                    WHEN 'HIGH'     THEN 4
                    WHEN 'MEDIUM'   THEN 3
                    WHEN 'LOW'      THEN 2
                    WHEN 'INFO'     THEN 1
                    ELSE 0 END) AS max_sev_rank
            FROM resources res
            JOIN repositories repo ON res.repo_id = repo.id
            LEFT JOIN findings f ON (
                f.experiment_id = res.experiment_id AND
                f.repo_id = res.repo_id AND
                (f.source_file = res.source_file OR f.source_file LIKE '%' || res.source_file) AND
                (
                    f.source_line_start = res.source_line_start OR
                    (
                        f.source_line_start > res.source_line_start AND
                        (
                            res.source_line_end IS NULL OR
                            f.source_line_start <= res.source_line_end
                        ) AND
                        NOT EXISTS (
                            SELECT 1 FROM resources r2
                            WHERE r2.experiment_id = res.experiment_id AND
                                r2.repo_id = res.repo_id AND
                                (r2.source_file = res.source_file OR r2.source_file LIKE '%' || res.source_file) AND
                                r2.source_line_start > res.source_line_start AND
                                r2.source_line_start <= f.source_line_start
                        )
                    )
                )
            )
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
            {"key": "roles",      "label": "🧑‍💼 Roles & Permissions"},
            {"key": "traffic",    "label": "📶 Traffic"},
            {"key": "subscription", "label": "🌐 Global Knowledge Q&A"},
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
        framework_ver_sel = "framework_version" if "framework_version" in repo_cols else "'' AS framework_version"
        framework_name_sel = "framework_name" if "framework_name" in repo_cols else "'' AS framework_name"
        iac_type_sel = "iac_type" if "iac_type" in repo_cols else "'' AS iac_type"
        repo_row = conn.execute(
            f"""
            SELECT {repo_type_sel}, {primary_lang_sel}, {framework_ver_sel}, {framework_name_sel}, {iac_type_sel}
            FROM repositories
            WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
            LIMIT 1
            """,
            (resolved_exp_id, repo_name),
        ).fetchone()

        repo_type = repo_row["repo_type"] if repo_row else ""
        primary_language = repo_row["primary_language"] if repo_row else ""
        framework_version = repo_row["framework_version"] if repo_row else ""
        framework_name = repo_row["framework_name"] if repo_row else ""
        iac_type = repo_row["iac_type"] if repo_row else ""

        # Supplement primary_language from context_metadata if the column is empty
        if not primary_language and _table_exists(conn, "context_metadata"):
            cm_pl = conn.execute(
                """
                SELECT value FROM context_metadata
                WHERE experiment_id = ? AND key = 'languages_detected'
                  AND repo_id = (
                    SELECT id FROM repositories
                    WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1
                  )
                LIMIT 1
                """,
                (resolved_exp_id, resolved_exp_id, repo_name),
            ).fetchone()
            if cm_pl and cm_pl["value"]:
                # Use the first entry as the primary when the column was never populated
                primary_language = cm_pl["value"].split(",")[0].strip()

        # Providers that are tools/runtimes, not actual cloud platforms
        _NON_CLOUD_PROVIDERS = {
            'kubernetes', 'helm', 'terraform', 'local', 'null', 'random',
            'time', 'tls', 'http', 'archive', 'external', 'template',
        }
        _PROVIDER_DISPLAY = {
            'azurerm': 'Azure', 'azure': 'Azure',
            'aws': 'AWS',
            'google': 'Google Cloud', 'googlecloud': 'Google Cloud',
            'alicloud': 'Alibaba Cloud',
            'oracle': 'Oracle Cloud',
            'oci': 'Oracle Cloud',
            'tencentcloud': 'Tencent Cloud',
            'huaweicloud': 'Huawei Cloud',
            'ibm': 'IBM Cloud',
            'digitalocean': 'DigitalOcean',
        }

        def _guess_hosting() -> str:
            if not _table_exists(conn, "resources"):
                return ""
            # Gather all declared providers for the repo
            rows = conn.execute(
                """
                SELECT DISTINCT LOWER(COALESCE(TRIM(provider), '')) AS p, LOWER(COALESCE(resource_type, '')) AS rt, LOWER(COALESCE(resource_name, '')) AS rn
                FROM resources res
                JOIN repositories repo ON res.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                  AND COALESCE(TRIM(provider), '') != ''
                """,
                (resolved_exp_id, repo_name),
            ).fetchall()
            raw = {r["p"] for r in rows if r["p"]}

            # If Kubernetes is the runtime, try to infer the underlying cloud(s) more precisely
            if "kubernetes" in raw or iac_type == "Kubernetes":
                try:
                    cluster_rows = conn.execute(
                        """
                        SELECT DISTINCT LOWER(COALESCE(r.provider, '')) AS p, LOWER(COALESCE(r.resource_type, '')) AS rt, LOWER(COALESCE(r.resource_name, '')) AS rn
                        FROM resources r
                        JOIN repositories repo ON r.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                          AND (
                                LOWER(r.resource_type) IN (
                                    'azurerm_kubernetes_cluster', 'aws_eks_cluster', 'google_container_cluster',
                                    'alicloud_cs_kubernetes_cluster', 'oci_containerengine_cluster'
                                )
                                OR LOWER(r.resource_type) LIKE '%kubernetes_cluster%'
                                OR LOWER(r.resource_type) LIKE '%eks_cluster%'
                                OR LOWER(r.resource_type) LIKE '%aks_cluster%'
                                OR LOWER(r.resource_type) LIKE '%gke_cluster%'
                              )
                        """,
                        (resolved_exp_id, repo_name),
                    ).fetchall()
                    cloud_names = [
                        _PROVIDER_DISPLAY.get(r['p'], r['p'].capitalize())
                        for r in cluster_rows
                        if r.get('p') and r.get('p') not in _NON_CLOUD_PROVIDERS and r.get('p') != 'kubernetes'
                    ]
                    cloud_names = sorted(set([n for n in cloud_names if n]))
                except Exception:
                    cloud_names = []

                if cloud_names:
                    return f"Kubernetes ({', '.join(cloud_names)})"
                # Fallback: if no clear cluster/provider mapping found, keep generic Kubernetes
                return "Kubernetes"

            # Non-Kubernetes: report real cloud provider when obvious
            cloud = [p for p in raw if p not in _NON_CLOUD_PROVIDERS]
            if len(cloud) > 1:
                return "Multi-cloud"
            if cloud:
                p = next(iter(cloud))
                return _PROVIDER_DISPLAY.get(p, p.capitalize())
            return ""

        def _guess_providers() -> str:
            if not _table_exists(conn, "resources"):
                return ""
            rows = conn.execute(
                """
                SELECT LOWER(COALESCE(TRIM(provider), '')) AS p, COUNT(*) AS cnt
                FROM resources res
                JOIN repositories repo ON res.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                  AND COALESCE(TRIM(provider), '') != ''
                GROUP BY p
                ORDER BY cnt DESC
                LIMIT 10
                """,
                (resolved_exp_id, repo_name),
            ).fetchall()
            seen: set = set()
            names: list[str] = []
            for row in rows:
                p = row["p"]
                if not p or p in _NON_CLOUD_PROVIDERS:
                    continue
                display = _PROVIDER_DISPLAY.get(p, p.replace("_", " ").title())
                if display not in seen:
                    seen.add(display)
                    names.append(display)
            return ", ".join(names[:3])

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
                    SUM(CASE WHEN category = 'Database' THEN 1 ELSE 0 END) AS database_count,
                    SUM(CASE WHEN category = 'Compute' THEN 1 ELSE 0 END) AS compute_count,
                    SUM(CASE WHEN category = 'Container' THEN 1 ELSE 0 END) AS container_count,
                    SUM(CASE WHEN category = 'Monitoring' THEN 1 ELSE 0 END) AS monitoring_count,
                    SUM(CASE WHEN category NOT IN ('Identity','Network','Storage','Database','Compute','Container','Monitoring') THEN 1 ELSE 0 END) AS other_count
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
        compute_count = counts["compute_count"] if counts else 0
        container_count = counts["container_count"] if counts else 0
        monitoring_count = counts["monitoring_count"] if counts else 0
        other_count = counts["other_count"] if counts else 0

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
                    SUM(CASE WHEN UPPER({base_sev_expr}) IN ('CRITICAL','HIGH') THEN 1 ELSE 0 END) AS critical_high,
                    SUM(CASE WHEN UPPER({base_sev_expr}) = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_count,
                    SUM(CASE WHEN UPPER({base_sev_expr}) = 'HIGH' THEN 1 ELSE 0 END) AS high_count,
                    SUM(CASE WHEN UPPER({base_sev_expr}) = 'MEDIUM' THEN 1 ELSE 0 END) AS medium_count,
                    SUM(CASE WHEN UPPER({base_sev_expr}) = 'LOW' THEN 1 ELSE 0 END) AS low_count
                FROM findings f
                JOIN repositories repo ON f.repo_id = repo.id
                WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                """,
                (resolved_exp_id, repo_name),
            ).fetchone()

        total_findings = findings_summary["total_findings"] if findings_summary else 0
        high_or_above = findings_summary["high_or_above"] if findings_summary else 0
        critical_high = findings_summary["critical_high"] if findings_summary else 0
        critical_count = findings_summary["critical_count"] if findings_summary else 0
        high_count = findings_summary["high_count"] if findings_summary else 0
        medium_count = findings_summary["medium_count"] if findings_summary else 0
        low_count = findings_summary["low_count"] if findings_summary else 0

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

        # Infer repo type from available signals.
        # Override the bare "Infrastructure" default when we have better evidence.
        if not repo_type or repo_type.lower() == "infrastructure":
            has_code = bool(primary_language and primary_language.lower() not in ('terraform',))
            has_iac  = bool(iac_type)
            if has_code and has_iac:
                repo_type = f"Code + IaC ({iac_type})"
            elif has_iac:
                if iac_type == "Kubernetes":
                    repo_type = "Kubernetes Manifests"
                else:
                    repo_type = f"Infrastructure ({iac_type})"
            elif has_code:
                repo_type = "Application"
            else:
                # Check for Kubernetes resource types in the resources table
                if _table_exists(conn, "resources"):
                    k8s_check = conn.execute(
                        """
                        SELECT 1 FROM resources r
                        JOIN repositories repo ON r.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                          AND LOWER(r.resource_type) IN (
                            'deployment','service','pod','statefulset','daemonset',
                            'ingress','configmap','secret','namespace'
                          )
                        LIMIT 1
                        """,
                        (resolved_exp_id, repo_name),
                    ).fetchone()
                    if k8s_check:
                        repo_type = "Kubernetes Application"
                if not repo_type:
                    repo_type = "Infrastructure"

        rows_html = []

        def add_row(label: str, value: str, link: str = None) -> None:
            if value:
                if link:
                    value = f'<a href="#{link}" style="color: var(--link-color); text-decoration: underline; cursor: pointer;">{value}</a>'
                rows_html.append(f"<tr><td>{label}</td><td>{value}</td></tr>")

        add_row("Type", repo_type)
        add_row("Primary language", primary_language or "Unknown")

        # Tech stack: framework only (version + name); IaC is its own row
        tech_parts = []
        if framework_version:
            tech_parts.append(framework_version)
        if framework_name:
            tech_parts.append(framework_name)
        if tech_parts:
            add_row("Tech Stack", " + ".join(tech_parts))
        if iac_type:
            add_row("IaC", iac_type)
        
        # Dependencies summary
        if _table_exists(conn, "dependencies"):
            dep_row = conn.execute(
                """
                SELECT 
                    COUNT(*) as total,
                    COUNT(DISTINCT package_manager) as pkg_managers,
                    COUNT(DISTINCT project_path) as projects
                FROM dependencies
                WHERE experiment_id = ? AND repo_id = (
                    SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1
                )
                """,
                (resolved_exp_id, resolved_exp_id, repo_name)
            ).fetchone()
            
            if dep_row and dep_row["total"] > 0:
                dep_summary = f"{dep_row['total']} packages across {dep_row['projects']} project(s)"
                add_row("Dependencies", dep_summary)

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

            # Supplement with context_metadata['languages_detected'] stored during scan
            cm_langs: list[str] = []
            if _table_exists(conn, "context_metadata"):
                try:
                    cm_row2 = conn.execute(
                        """
                        SELECT value FROM context_metadata
                        WHERE experiment_id = ? AND key = 'languages_detected'
                          AND repo_id = (
                            SELECT id FROM repositories
                            WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1
                          )
                        LIMIT 1
                        """,
                        (resolved_exp_id, resolved_exp_id, repo_name),
                    ).fetchone()
                    if cm_row2 and cm_row2["value"]:
                        cm_langs = [l.strip() for l in cm_row2["value"].split(",") if l.strip()]
                except Exception:
                    pass

            # Merge file-evidence detections with metadata-stored list (metadata wins for ordering)
            if cm_langs:
                seen = set()
                merged = []
                for name in cm_langs:
                    if name not in seen:
                        seen.add(name)
                        merged.append(name)
                for name in detected:
                    if name not in seen:
                        seen.add(name)
                        merged.append(name)
                add_row("Languages detected", ", ".join(merged))
            elif detected:
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

        add_row("Hosting", hosting_model)
        if cicd_tool:
            add_row("CI/CD", cicd_tool)
        add_row("Cloud providers", provider_summary)
        add_row("Resources discovered", str(total_resources), link="assets")
        if total_resources:
            details = []
            if compute_count:
                details.append(f"Compute: {compute_count}")
            if identity_count:
                details.append(f"Identity: {identity_count}")
            if network_count:
                details.append(f"Network: {network_count}")
            if storage_count:
                details.append(f"Storage: {storage_count}")
            if database_count:
                details.append(f"Database: {database_count}")
            if container_count:
                details.append(f"Container: {container_count}")
            if monitoring_count:
                details.append(f"Monitoring: {monitoring_count}")
            if other_count:
                details.append(f"Other: {other_count}")
            if details:
                add_row("Breakdown", ", ".join(details))
        add_row("Findings discovered", str(total_findings), link="findings")
        if total_findings:
            sev_parts = []
            if critical_count:
                sev_parts.append(f"{critical_count} critical")
            if high_count:
                sev_parts.append(f"{high_count} high")
            if medium_count:
                sev_parts.append(f"{medium_count} medium")
            if low_count:
                sev_parts.append(f"{low_count} low")
            add_row("Severity breakdown", " · ".join(sev_parts) if sev_parts else "0 findings")

        # Exposure analysis summary
        try:
            exp_row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN exposure_level='direct_exposure' THEN 1 ELSE 0 END) as direct_cnt,
                  SUM(CASE WHEN exposure_level='mitigated' THEN 1 ELSE 0 END) as mitigated_cnt,
                  SUM(CASE WHEN exposure_level='isolated' THEN 1 ELSE 0 END) as isolated_cnt
                FROM exposure_analysis WHERE experiment_id=?
                """,
                (resolved_exp_id,),
            ).fetchone()
            if exp_row and (exp_row["direct_cnt"] or exp_row["mitigated_cnt"]):
                direct = exp_row["direct_cnt"] or 0
                mitigated = exp_row["mitigated_cnt"] or 0
                exp_parts = []
                if direct:
                    exp_parts.append(f'<span style="color:#ff4444;">{direct} directly exposed</span>')
                if mitigated:
                    exp_parts.append(f'<span style="color:#ff9900;">{mitigated} behind controls</span>')
                add_row("Internet exposure", " · ".join(exp_parts))
        except Exception:
            pass

        # Trust boundary summary
        try:
            tb_rows = conn.execute(
                """SELECT name, boundary_type, COUNT(tbm.resource_id) as members
                   FROM trust_boundaries tb
                   LEFT JOIN trust_boundary_members tbm ON tb.id=tbm.trust_boundary_id
                   WHERE tb.experiment_id=?
                   GROUP BY tb.id, tb.name, tb.boundary_type
                   ORDER BY tb.boundary_type""",
                (resolved_exp_id,),
            ).fetchall()
            if tb_rows:
                tb_html = "<div style='display:flex; flex-wrap:wrap; gap:6px;'>"
                icons = {"internet": "🌐", "network_boundary": "🔷", "data_tier": "🗄️"}
                for tb in tb_rows:
                    icon = icons.get(tb["boundary_type"], "🔶")
                    tb_html += (
                        f"<span style='font-size:0.8rem; padding:2px 8px; border-radius:4px; "
                        f"background:var(--surface-2); border:1px solid var(--border-subtle);'>"
                        f"{icon} {tb['name']} ({tb['members']})</span>"
                    )
                tb_html += "</div>"
                add_row("Trust boundaries", tb_html)
        except Exception:
            pass

        # Scan status heuristics
        ai_ready = False
        ai_text = "pending"
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
                               AND cm.namespace = 'ai_overview') AS ai_overview,
                            (SELECT COUNT(*)
                             FROM context_metadata cm
                             WHERE cm.experiment_id = ?
                               AND cm.repo_id = repo.id
                               AND cm.namespace = 'ai_overview'
                               AND cm.key = 'ai_analysis_completed_at') AS ai_completed
                        FROM findings f
                        JOIN repositories repo ON f.repo_id = repo.id
                        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
                        """,
                        (resolved_exp_id, resolved_exp_id, resolved_exp_id, resolved_exp_id, repo_name),
                    ).fetchone()
                    if ai_counts:
                        total = ai_counts["total_findings"] or 0
                        enriched = ai_counts["enriched_findings"] or 0
                        skeptic_reviews = ai_counts["skeptic_reviews"] or 0
                        ai_overview = ai_counts["ai_overview"] or 0
                        ai_completed = ai_counts["ai_completed"] or 0
                        if ai_completed > 0:
                            ai_icon, ai_text = "🟢", "complete"
                        elif ai_overview > 0 and (total == 0 or enriched >= total):
                            ai_icon, ai_text = "🟢", "complete"
                        elif enriched > 0 or skeptic_reviews > 0 or ai_overview > 0:
                            ai_icon, ai_text = "🟠", "partial"
                        else:
                            ai_icon, ai_text = "🟡", "pending"

                # Per-agent review status from repo_ai_content section keys
                def _agent_status_line(section_key: str, label: str) -> str:
                    try:
                        row = conn.execute(
                            """SELECT 1 FROM repo_ai_content
                               WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
                                 AND section_key = ? LIMIT 1""",
                            (resolved_exp_id, repo_name, section_key),
                        ).fetchone()
                        icon, text = ("🟢", "complete") if row else (ai_icon if ai_icon == "🟠" else "🟡", "pending")
                    except Exception:
                        icon, text = "🟡", "pending"
                    return f"<div class='scan-status-line'>{icon} <span>{label} ({text})</span></div>"

                status_html = "".join(
                    [
                        line("Discovery & inventory", stage_counts["p1"]),
                        line("Exposure mapping", stage_counts["p2"]),
                        line("Findings correlation", stage_counts["p3"]),
                        _agent_status_line("context_extraction", "🔍 Context extraction"),
                        _agent_status_line("security_review",    "🔒 Security review"),
                        _agent_status_line("dev_review",         "💻 Dev skeptic"),
                        _agent_status_line("platform_review",    "☁️ Platform skeptic"),
                    ]
                )
                add_row("Scan status", status_html)

                ai_ready = bool(stage_counts["p1"] and stage_counts["p2"] and stage_counts["p3"])
        except Exception:
            ai_ready = False

        table_html = (
            '<table class="tldr-table">'
            "<tbody>"
            + "".join(rows_html)
            + "</tbody></table>"
        )

        # Check for AI-enhanced TLDR
        ai_tldr = ""
        try:
            ai_section = conn.execute(
                """
                SELECT content_html, generated_by, updated_at
                FROM repo_ai_content
                WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?)
                  AND section_key = 'enhanced_tldr'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (resolved_exp_id, repo_name),
            ).fetchone()
            if ai_section and ai_section["content_html"]:
                ai_tldr = (
                    '<div class="ai-enhanced-section" style="background:#1c2128; border-left:3px solid #58a6ff; padding:12px; margin-bottom:16px; border-radius:6px;">'
                    '<div style="font-weight:600; color:#58a6ff; margin-bottom:8px; display:flex; align-items:center; gap:6px;">'
                    '🤖 AI-Enhanced Summary'
                    '<span style="font-weight:400; font-size:0.75rem; color:#8b949e;">(generated by Copilot with agent instructions)</span>'
                    '</div>'
                    + ai_section["content_html"]
                    + '</div>'
                )
        except Exception:
            pass

        # Combine AI TLDR + baseline table
        final_html = ai_tldr + table_html

        return _db_render(
            "tab_tldr.html",
            tldr_html=final_html,
            experiment_id=resolved_exp_id,
            repo_name=repo_name,
            ai_ready=ai_ready,
            ai_status=ai_text,
        )
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
    ai_context_summary_val: str = ""
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
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    def _safe_fetchone(sql: str, params: tuple = ()):
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError:
            return None

    module_deps_data: list[dict] = []
    available_repos: list[str] = []

    try:
        repo_cols = _table_columns("repositories")
        if not repo_cols:
            return _db_render('tab_overview.html', overview_html='', experiment_id=resolved_exp_id, repo_name=repo_name)
        repo_select = ["id"]
        repo_select.append("primary_language" if "primary_language" in repo_cols else "'' AS primary_language")
        repo_select.append("files_scanned" if "files_scanned" in repo_cols else "0 AS files_scanned")
        repo_select.append("iac_files_count" if "iac_files_count" in repo_cols else "0 AS iac_files_count")
        repo_select.append("code_files_count" if "code_files_count" in repo_cols else "0 AS code_files_count")
        repo_select.append("framework_version" if "framework_version" in repo_cols else "'' AS framework_version")
        repo_select.append("framework_name" if "framework_name" in repo_cols else "'' AS framework_name")
        repo_select.append("iac_type" if "iac_type" in repo_cols else "'' AS iac_type")
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
        # Pull ai_context_summary out early so it can be injected into the scripted section
        ai_context_summary_val = ""
        for _r in ai_overview_rows:
            if _r["key"] == "ai_context_summary":
                ai_context_summary_val = (_r["value"] or "").strip()
                break

        has_ai_attack_paths = False
        if ai_overview_rows:
            label_map = {
                "ai_context_summary": "What This Repo Does",
                "ai_project_summary": "Project",
                "ai_deployment_summary": "Deployment",
                "ai_interactions_summary": "Interactions",
                "ai_auth_summary": "Auth",
                "ai_attack_paths": "Attack Paths",
                "ai_dependencies_summary": "Dependencies",
                "ai_issues_summary": "Issues",
                "ai_skeptic_summary": "Skeptics",
                "ai_security_summary": "Security Review",
                "ai_dev_summary": "Dev Review",
                "ai_platform_summary": "Platform Review",
                "ai_action_items": "Actions",
                "ai_finding_themes": "Finding Themes",
                "ai_open_questions": "Open questions",
                "ai_observations": "Observations",
                "ai_asset_visibility": "Asset visibility",
                "ai_learning_suggestions": "Learning suggestions",
                "ai_analysis_completed_at": "Analysis completed",
                "ai_analysis_recovered": "Recovered",
            }
            # ai_context_summary should render first, then findings themes, then attack paths
            priority_keys = ["ai_context_summary", "ai_finding_themes", "ai_attack_paths"]
            # Fields that should be formatted as numbered lists (semicolon-separated)
            list_fields = {"ai_action_items", "ai_open_questions", "ai_observations", "ai_asset_visibility", "ai_learning_suggestions"}
            # Fields that contain JSON arrays (skip rendering raw JSON)
            json_fields = {"ai_new_assets", "ai_fixed_information"}
            timestamp_fields = {"ai_analysis_completed_at", "ai_analysis_recovered"}

            # Sort rows: priority keys first, then alphabetical
            def _row_sort_key(r):
                k = r["key"]
                if k in priority_keys:
                    return (0, priority_keys.index(k))
                return (1, k)
            ai_overview_rows_sorted = sorted(ai_overview_rows, key=_row_sort_key)

            ai_sections = []
            for row in ai_overview_rows_sorted:
                val = (row["value"] or "").strip()
                if not val or row["key"] in json_fields:
                    continue
                label = label_map.get(row["key"], row["key"])

                # ai_context_summary is rendered inside "What This Repo Does" — skip here
                if row["key"] == "ai_context_summary":
                    continue

                # ai_finding_themes: render as hypothesis cards with confirmable Y/N/DK buttons
                if row["key"] == "ai_finding_themes":
                    themes_parsed = None
                    try:
                        themes_parsed = json.loads(val)
                    except Exception:
                        pass
                    if isinstance(themes_parsed, list) and themes_parsed:
                        # Load existing answers
                        theme_answers: dict[str, dict] = {}
                        try:
                            th_ans_rows = _safe_fetchall(
                                """
                                SELECT LOWER(question) AS q, answer, answered_by
                                FROM subscription_context
                                WHERE experiment_id = ? AND scope_key = 'repo'
                                  AND LOWER(COALESCE(repo_name,'')) = LOWER(?)
                                  AND tags LIKE '%finding_theme%'
                                """,
                                (resolved_exp_id, repo_name),
                            )
                            for ar in th_ans_rows:
                                if ar['q']:
                                    raw_a = (ar['answer'] or '').strip().lower()
                                    ak = "yes" if raw_a.startswith("yes") else ("no" if raw_a.startswith("no") else ("dont_know" if "don" in raw_a else ""))
                                    theme_answers[ar['q']] = {"answer": ak, "auto": ar['answered_by'] == 'auto_analysis'}
                        except Exception:
                            pass

                        cards = []
                        for th in themes_parsed[:5]:
                            theme_title  = esc((th.get('theme') or '').strip())
                            hypothesis   = esc((th.get('hypothesis') or '').strip())
                            question     = (th.get('question') or '').strip()
                            if_yes       = esc((th.get('if_yes_means') or '').strip())
                            if_no        = esc((th.get('if_no_means') or '').strip())
                            rule_ids     = th.get('findings') or []

                            saved        = theme_answers.get(question.lower(), {})
                            answer_key   = saved.get("answer", "")

                            rules_html = "".join(
                                f'<code class="theme-rule-id">{esc(r)}</code>'
                                for r in rule_ids[:8]
                            )

                            # Encode question payload for the widget
                            qpayload = json.dumps({
                                "question": question,
                                "theme": th.get('theme', ''),
                                "if_yes": th.get('if_yes_means', ''),
                                "if_no": th.get('if_no_means', ''),
                                "answer": answer_key,
                                "tag": "finding_theme",
                            }).replace("</script>", "<\\/script>")

                            cards.append(
                                f'<div class="theme-card{" theme-card--answered" if answer_key else ""}"'
                                f' data-answer="{esc(answer_key)}">'
                                f'<div class="theme-card-header">'
                                f'<span class="theme-badge">🔍 Theme</span>'
                                f'<strong class="theme-title">{theme_title}</strong>'
                                f'</div>'
                                f'<p class="theme-hypothesis">{hypothesis}</p>'
                                + (f'<div class="theme-rules">{rules_html}</div>' if rules_html else '')
                                + f'<div class="theme-question-row">'
                                f'<span class="theme-question-lbl">Confirm:</span>'
                                f'<span class="theme-question-text">{esc(question)}</span>'
                                f'</div>'
                                + (
                                    f'<div class="theme-implications">'
                                    f'<span class="theme-impl theme-impl--yes">If Yes: {if_yes}</span>'
                                    f'<span class="theme-impl theme-impl--no">If No: {if_no}</span>'
                                    f'</div>'
                                    if if_yes or if_no else ''
                                )
                                + f'<div class="theme-btns oq-btns">'
                                f'<button type="button" class="oq-btn oq-btn--yes{" oq-btn--active" if answer_key == "yes" else ""}" data-ans="yes">✓ Yes</button>'
                                f'<button type="button" class="oq-btn oq-btn--no{" oq-btn--active" if answer_key == "no" else ""}" data-ans="no">✗ No</button>'
                                f'<button type="button" class="oq-btn oq-btn--dont_know{" oq-btn--active" if answer_key == "dont_know" else ""}" data-ans="dont_know">? Don\'t know</button>'
                                f'<span class="oq-status"></span>'
                                f'<script type="application/json" class="theme-data">{qpayload}</script>'
                                f'</div>'
                                f'</div>'
                            )

                        if cards:
                            ai_sections.append(
                                f'<h3>{esc(label)}</h3>'
                                f'<div class="theme-cards" '
                                f'data-experiment-id="{esc(resolved_exp_id)}" '
                                f'data-repo-name="{esc(repo_name)}">'
                                + ''.join(cards)
                                + '</div>'
                            )
                    continue

                if row["key"] == "ai_attack_paths":
                    parsed_paths = None
                    try:
                        parsed_paths = json.loads(val)
                    except Exception:
                        parsed_paths = None

                    normalized_paths = _normalize_attack_paths(parsed_paths)
                    if normalized_paths:
                        has_ai_attack_paths = True
                        cards = []
                        for attack_path in normalized_paths[:8]:
                            title = esc(str(attack_path.get("title") or "Attack path"))
                            path_text = esc(str(attack_path.get("path") or ""))
                            summary_text = esc(str(attack_path.get("summary") or ""))
                            impact_text = esc(str(attack_path.get("impact") or ""))
                            confidence = esc(str(attack_path.get("confidence") or ""))
                            reviewer = esc(str(attack_path.get("reviewer") or ""))
                            source = esc(str(attack_path.get("source") or ""))
                            evidence_items = []
                            for evidence in (attack_path.get("evidence") or [])[:4]:
                                text = str(evidence).strip()
                                if text:
                                    evidence_items.append(f"<li>{esc(text)}</li>")

                            badges = ""
                            if confidence:
                                badges += f'<span class="how-badge" style="margin-left:6px">{confidence}</span>'
                            if reviewer:
                                badges += f'<span class="how-badge" style="margin-left:6px">{reviewer}</span>'
                            if source:
                                badges += f'<span class="how-badge" style="margin-left:6px">{source}</span>'

                            block = f'<div class="ai-action"><h4 class="ai-action-title">{title}{badges}</h4>'
                            if path_text:
                                block += f'<div class="ai-action-row"><span class="ai-action-label">Path</span> <span>{path_text}</span></div>'
                            if summary_text:
                                block += f'<div class="ai-action-row"><span class="ai-action-label">Why it matters</span> <span>{summary_text}</span></div>'
                            if impact_text:
                                block += f'<div class="ai-action-row"><span class="ai-action-label">Impact</span> <span>{impact_text}</span></div>'
                            if evidence_items:
                                block += f'<div class="ai-action-row"><span class="ai-action-label">Evidence</span><ul>{"".join(evidence_items)}</ul></div>'
                            block += '</div>'
                            cards.append(block)

                        if cards:
                            ai_sections.append(f'<h3>{esc(label)}</h3><div class="ai-actions">' + ''.join(cards) + '</div>')
                    continue
                
                if row["key"] in list_fields:
                    # Actions may be stored as structured JSON for rich rendering.
                    if row["key"] == "ai_action_items":
                        parsed_actions = None
                        try:
                            parsed_actions = json.loads(val)
                        except Exception:
                            parsed_actions = None

                        if isinstance(parsed_actions, list) and parsed_actions and all(isinstance(x, dict) for x in parsed_actions):
                            blocks = []
                            for idx, a in enumerate(parsed_actions[:6], start=1):
                                title = (a.get('title') or '').strip() or f"Action {idx}"
                                what = (a.get('what') or '').strip()
                                why = (a.get('why') or '').strip()
                                file = (a.get('file') or '').strip()
                                line = a.get('line')
                                current_snip = (a.get('current_snippet') or '').strip()
                                fix_snip = (a.get('fix_snippet') or '').strip()

                                loc_html = ''
                                if file:
                                    base = Path(file).name
                                    loc_html = f"<span class=\"file-link\" title=\"{esc(file)}\">{esc(base)}</span>"
                                    if line:
                                        loc_html += f"<span class=\"line-num\">:{esc(str(line))}</span>"

                                block = f"<div class=\"ai-action\">"
                                block += f"<h4 class=\"ai-action-title\">{esc(title)}</h4>"
                                if what:
                                    block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">What</span> <span>{esc(what)}</span></div>"
                                if why:
                                    block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Why</span> <span>{esc(why)}</span></div>"
                                if loc_html:
                                    block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Where</span> {loc_html}</div>"
                                if current_snip:
                                    block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Current</span><pre class=\"md-code\"><code>{esc(current_snip)}</code></pre></div>"
                                if fix_snip:
                                    block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Proposed</span><pre class=\"md-code\"><code>{esc(fix_snip)}</code></pre></div>"
                                block += "</div>"
                                blocks.append(block)

                            ai_sections.append(f"<h3>{esc(label)}</h3><div class=\"ai-actions\">" + ''.join(blocks) + "</div>")
                        else:
                            # Fallback: Split semicolon-separated items into numbered list
                            items = [item.strip() for item in val.split(';') if item.strip()]
                            if items:
                                list_html = "<ol>" + ''.join(f"<li>{esc(item)}</li>" for item in items) + "</ol>"
                                ai_sections.append(f"<h3>{esc(label)}</h3>{list_html}")
                    else:
                        # Open questions may be stored as structured JSON for richer rendering.
                        if row["key"] == "ai_open_questions":
                            parsed_q = None
                            try:
                                parsed_q = json.loads(val)
                            except Exception:
                                parsed_q = None

                            if isinstance(parsed_q, list) and parsed_q and all(isinstance(x, dict) for x in parsed_q):
                                # Run auto-resolver for any unanswered questions (idempotent)
                                try:
                                    _auto_resolve_open_questions(parsed_q, resolved_exp_id, repo_name, conn)
                                except Exception:
                                    pass

                                # Load existing answers from subscription_context (includes auto-resolved)
                                existing_answers: dict[str, str] = {}
                                try:
                                    ans_rows = _safe_fetchall(
                                        """
                                        SELECT LOWER(question) AS q, answer, answered_by FROM subscription_context
                                        WHERE experiment_id = ? AND scope_key = 'repo'
                                          AND LOWER(COALESCE(repo_name,'')) = LOWER(?)
                                        """,
                                        (resolved_exp_id, repo_name),
                                    )
                                    for ar in ans_rows:
                                        if ar['q']:
                                            existing_answers[ar['q']] = {
                                                "answer": (ar['answer'] or '').strip(),
                                                "auto": (ar['answered_by'] or '') == 'auto_analysis',
                                            }
                                except Exception:
                                    pass

                                questions_payload = []
                                for q in parsed_q[:10]:
                                    text = (q.get('question') or '').strip()
                                    if not text:
                                        continue
                                    saved_entry = existing_answers.get(text.lower(), {})
                                    if isinstance(saved_entry, dict):
                                        raw_answer = saved_entry.get("answer", "")
                                        is_auto = saved_entry.get("auto", False)
                                    else:
                                        raw_answer = saved_entry
                                        is_auto = False
                                    # Normalise stored answer to yes/no/dont_know key for the widget
                                    answer_key = ""
                                    al = (raw_answer or "").strip().lower()
                                    if al.startswith("yes"):
                                        answer_key = "yes"
                                    elif al.startswith("no"):
                                        answer_key = "no"
                                    elif "don" in al or "unknown" in al:
                                        answer_key = "dont_know"
                                    questions_payload.append({
                                        "question": text,
                                        "file": (q.get('file') or '').strip(),
                                        "line": q.get('line'),
                                        "asset": (q.get('asset') or '').strip(),
                                        "answer": answer_key,
                                        "auto_answered": is_auto,
                                        "auto_rationale": raw_answer if is_auto else "",
                                    })

                                if questions_payload:
                                    # Collect auto-answered "Yes" items — these need prominent callout
                                    confirmed_risks = [
                                        q for q in questions_payload
                                        if q.get("auto_answered") and q.get("answer") == "yes"
                                    ]
                                    if confirmed_risks:
                                        risk_items = "".join(
                                            f'<li>'
                                            f'<strong>{esc(q["question"])}</strong>'
                                            + (
                                                f' <span class="file-link" title="{esc(q["file"])}">'
                                                f'{esc(q["file"].split("/")[-1])}'
                                                + (f':{esc(str(q["line"]))}' if q.get("line") else "")
                                                + '</span>'
                                                if q.get("file") else ""
                                            )
                                            + f'<div class="oq-rationale" style="margin-top:4px">'
                                            f'<span class="oq-auto-badge">🔍 Auto-analysed</span>'
                                            f'{esc(q["auto_rationale"])}</div>'
                                            f'</li>'
                                            for q in confirmed_risks
                                        )
                                        ai_sections.append(
                                            f'<div class="callout-warning">'
                                            f'<strong>⚠️ Static analysis confirmed ({len(confirmed_risks)})</strong>'
                                            f'<ul class="oq-confirmed-list">{risk_items}</ul>'
                                            f'</div>'
                                        )

                                    # Render as a data island — the inline script below turns it into interactive widgets
                                    payload_json = json.dumps(questions_payload).replace('</script>', '<\\/script>')
                                    ai_sections.append(
                                        f'<h3>{esc(label)}</h3>'
                                        f'<div class="open-questions-widget" '
                                        f'data-experiment-id="{esc(resolved_exp_id)}" '
                                        f'data-repo-name="{esc(repo_name)}">'
                                        f'<script type="application/json" class="oq-data">{payload_json}</script>'
                                        f'</div>'
                                    )
                                    continue

                        # Observations stored as JSON: list of {title, detail, target, references}
                        if row["key"] == "ai_observations":
                            parsed_o = None
                            try:
                                parsed_o = json.loads(val)
                            except Exception:
                                parsed_o = None

                            if isinstance(parsed_o, list) and parsed_o and all(isinstance(x, dict) for x in parsed_o):
                                li = []
                                for o in parsed_o[:8]:
                                    title = (o.get('title') or '').strip()
                                    detail = (o.get('detail') or '').strip()
                                    target = (o.get('target') or '').strip()
                                    text = title or detail
                                    if not text:
                                        continue
                                    target_html = f" <span class=\"file-link\" style=\"margin-left:8px\">{esc(target)}</span>" if target else ""
                                    detail_html = f'<div style="margin-top:4px;color:var(--text-muted);font-size:0.82rem">{esc(detail)}</div>' if detail and title else ''

                                    # Render references (code evidence)
                                    refs_html = ""
                                    refs = o.get('references') or []
                                    if isinstance(refs, list) and refs:
                                        ref_items = []
                                        for ref in refs[:4]:
                                            if not isinstance(ref, dict):
                                                continue
                                            ref_file = (ref.get('file') or '').strip()
                                            ref_line = ref.get('line')
                                            ref_rule = (ref.get('rule_id') or '').strip()
                                            ref_snippet = (ref.get('snippet') or '').strip()
                                            loc = ref_file
                                            if ref_line:
                                                loc = f"{loc}:{ref_line}"
                                            label = loc or ref_rule or "finding"
                                            ref_html = f'<span class="file-link">{esc(label)}</span>'
                                            if ref_rule and loc:
                                                ref_html += f' <span style="font-size:0.7rem;color:var(--text-muted)">({esc(ref_rule)})</span>'
                                            if ref_snippet:
                                                ref_html += f'<pre class="md-code" style="margin:3px 0 0;font-size:0.72rem;padding:4px 8px;max-height:80px;overflow:auto"><code>{esc(ref_snippet)}</code></pre>'
                                            ref_items.append(f'<li style="margin-bottom:4px">{ref_html}</li>')
                                        if ref_items:
                                            refs_html = (
                                                '<details style="margin-top:6px">'
                                                '<summary style="font-size:0.75rem;color:var(--text-muted);cursor:pointer;user-select:none">'
                                                f'📎 {len(ref_items)} reference{"s" if len(ref_items) != 1 else ""}'
                                                '</summary>'
                                                f'<ul style="margin:4px 0 0 12px;padding:0;list-style:disc;font-size:0.78rem">'
                                                + ''.join(ref_items) +
                                                '</ul></details>'
                                            )

                                    li.append(f"<li><strong>{esc(text)}</strong>{target_html}{detail_html}{refs_html}</li>")
                                if li:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><ul>" + ''.join(li) + "</ul>")
                                    continue

                        # Asset visibility suggestions stored as JSON: list of {resource_type, resource_name, decision, reason}
                        if row["key"] == "ai_asset_visibility":
                            parsed_v = None
                            try:
                                parsed_v = json.loads(val)
                            except Exception:
                                parsed_v = None

                            if isinstance(parsed_v, list) and parsed_v and all(isinstance(x, dict) for x in parsed_v):
                                li = []
                                for v in parsed_v[:20]:
                                    rtype = (v.get('resource_type') or '').strip()
                                    rname = (v.get('resource_name') or '').strip()
                                    decision = (v.get('decision') or '').strip().lower()
                                    reason = (v.get('reason') or '').strip()
                                    label_txt = rname or rtype
                                    if not label_txt:
                                        continue
                                    badge = ''
                                    if decision in ('hide', 'show'):
                                        badge = f" <span class=\"how-badge\" style=\"margin-left:6px\"\">{esc(decision)}</span>"
                                    details = f"<div style=\"margin-top:4px;color:var(--text-muted);font-size:0.82rem\">{esc(reason)}</div>" if reason else ''
                                    li.append(f"<li><strong>{esc(label_txt)}</strong>{badge}{details}</li>")
                                if li:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><ul>" + ''.join(li) + "</ul>")
                                    continue

                        # Learning suggestions stored as JSON: list of {kind, target, rationale, example_evidence, proposed_change}
                        if row["key"] == "ai_learning_suggestions":
                            parsed_l = None
                            try:
                                parsed_l = json.loads(val)
                            except Exception:
                                parsed_l = None

                            if isinstance(parsed_l, list) and parsed_l and all(isinstance(x, dict) for x in parsed_l):
                                blocks = []
                                for s in parsed_l[:12]:
                                    kind = (s.get('kind') or '').strip()
                                    target = (s.get('target') or '').strip()
                                    rationale = (s.get('rationale') or '').strip()
                                    evidence = (s.get('example_evidence') or '').strip()
                                    proposed = (s.get('proposed_change') or '').strip()
                                    title = target or kind or 'Suggestion'
                                    badge = f" <span class=\"how-badge\" style=\"margin-left:6px\"\">{esc(kind)}</span>" if kind else ''
                                    block = f"<div class=\"ai-action\">"
                                    block += f"<h4 class=\"ai-action-title\">{esc(title)}{badge}</h4>"
                                    if rationale:
                                        block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Why</span> <span>{esc(rationale)}</span></div>"
                                    if evidence:
                                        block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Evidence</span><pre class=\"md-code\"><code>{esc(evidence)}</code></pre></div>"
                                    if proposed:
                                        block += f"<div class=\"ai-action-row\"><span class=\"ai-action-label\">Proposed</span><pre class=\"md-code\"><code>{esc(proposed)}</code></pre></div>"
                                    block += "</div>"
                                    blocks.append(block)
                                if blocks:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><div class=\"ai-actions\">" + ''.join(blocks) + "</div>")
                                    continue
                            parsed_v = None
                            try:
                                parsed_v = json.loads(val)
                            except Exception:
                                parsed_v = None

                            if isinstance(parsed_v, list) and parsed_v and all(isinstance(x, dict) for x in parsed_v):
                                li = []
                                for v in parsed_v[:20]:
                                    rtype = (v.get('resource_type') or '').strip()
                                    rname = (v.get('resource_name') or '').strip()
                                    decision = (v.get('decision') or '').strip().lower()
                                    reason = (v.get('reason') or '').strip()
                                    label_txt = rname or rtype
                                    if not label_txt:
                                        continue
                                    badge = ''
                                    if decision in ('hide', 'show'):
                                        badge = f" <span class=\"how-badge\" style=\"margin-left:6px\"\">{esc(decision)}</span>"
                                    details = f"<div style=\"margin-top:4px;color:var(--text-muted);font-size:0.82rem\">{esc(reason)}</div>" if reason else ''
                                    li.append(f"<li><strong>{esc(label_txt)}</strong>{badge}{details}</li>")
                                if li:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><ul>" + ''.join(li) + "</ul>")
                                    continue
                            parsed_o = None
                            try:
                                parsed_o = json.loads(val)
                            except Exception:
                                parsed_o = None

                            if isinstance(parsed_o, list) and parsed_o and all(isinstance(x, dict) for x in parsed_o):
                                li = []
                                for o in parsed_o[:8]:
                                    title = (o.get('title') or '').strip()
                                    detail = (o.get('detail') or '').strip()
                                    target = (o.get('target') or '').strip()
                                    text = title or detail
                                    if not text:
                                        continue
                                    suffix = f" <span class=\"file-link\" style=\"margin-left:8px\"\">{esc(target)}</span>" if target else ""
                                    li.append(f"<li><strong>{esc(text)}</strong>{suffix}{(f'<div style=\"margin-top:4px;color:var(--text-muted);font-size:0.82rem\">{esc(detail)}</div>' if detail and title else '')}</li>")
                                if li:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><ul>" + ''.join(li) + "</ul>")
                                    continue
                            parsed_q = None
                            try:
                                parsed_q = json.loads(val)
                            except Exception:
                                parsed_q = None

                            if isinstance(parsed_q, list) and parsed_q and all(isinstance(x, dict) for x in parsed_q):
                                li = []
                                for q in parsed_q[:5]:
                                    text = (q.get('question') or '').strip()
                                    fpath = (q.get('file') or '').strip()
                                    line = q.get('line')
                                    asset = (q.get('asset') or '').strip()
                                    if not text:
                                        continue
                                    loc = ''
                                    if fpath:
                                        base = Path(fpath).name
                                        loc = f"<span class=\"file-link\" title=\"{esc(fpath)}\">{esc(base)}</span>" + (f"<span class=\"line-num\">:{esc(str(line))}</span>" if line else "")
                                    extra = f" <span class=\"how-badge\" style=\"margin-left:6px\"\">{esc(asset)}</span>" if asset else ""
                                    suffix = f" <span style=\"margin-left:8px\">{loc}</span>" if loc else ""
                                    li.append(f"<li>{esc(text)}{extra}{suffix}</li>")
                                if li:
                                    ai_sections.append(f"<h3>{esc(label)}</h3><ol>" + ''.join(li) + "</ol>")
                                    continue

                        # Fallback: Split semicolon-separated items into numbered list
                        items = [item.strip() for item in val.split(';') if item.strip()]
                        if items:
                            list_html = "<ol>" + ''.join(f"<li>{esc(item)}</li>" for item in items) + "</ol>"
                            ai_sections.append(f"<h3>{esc(label)}</h3>{list_html}")
                else:
                    if row["key"] in timestamp_fields:
                        try:
                            timestamp_value = float(val)
                            if timestamp_value > 10_000_000_000:
                                timestamp_value /= 1000.0
                            val = datetime.fromtimestamp(timestamp_value).strftime("%Y-%m-%d %H:%M:%S")
                        except (TypeError, ValueError, OSError, OverflowError):
                            pass
                    # Regular paragraph
                    ai_sections.append(f"<h3>{esc(label)}</h3><p>{esc(val)}</p>")
            
            if ai_sections:
                overview_sections.append("<h2>Project Overview</h2>" + ''.join(ai_sections))

        if not has_ai_attack_paths:
            try:
                facts_for_paths = _fetch_overview_facts(resolved_exp_id, repo_name)
                derived_paths = _normalize_attack_paths((facts_for_paths[1] or {}).get("attack_paths") if facts_for_paths else [])
            except Exception:
                derived_paths = []
            if derived_paths:
                blocks = []
                for attack_path in derived_paths[:6]:
                    title = esc(str(attack_path.get("title") or "Attack path"))
                    path_text = esc(str(attack_path.get("path") or ""))
                    summary_text = esc(str(attack_path.get("summary") or ""))
                    impact_text = esc(str(attack_path.get("impact") or ""))
                    confidence = esc(str(attack_path.get("confidence") or ""))
                    source = esc(str(attack_path.get("source") or ""))
                    badges = ""
                    if confidence:
                        badges += f'<span class="how-badge" style="margin-left:6px">{confidence}</span>'
                    if source:
                        badges += f'<span class="how-badge" style="margin-left:6px">{source}</span>'
                    block = f'<div class="ai-action"><h4 class="ai-action-title">{title}{badges}</h4>'
                    if path_text:
                        block += f'<div class="ai-action-row"><span class="ai-action-label">Path</span> <span>{path_text}</span></div>'
                    if summary_text:
                        block += f'<div class="ai-action-row"><span class="ai-action-label">Why it matters</span> <span>{summary_text}</span></div>'
                    if impact_text:
                        block += f'<div class="ai-action-row"><span class="ai-action-label">Impact</span> <span>{impact_text}</span></div>'
                    block += '</div>'
                    blocks.append(block)
                overview_sections.append("<h2>Likely Attack Paths</h2><div class=\"ai-actions\">" + ''.join(blocks) + "</div>")

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

        # Always build the tech/language breakdown (appended to AI-provided purpose or shown standalone)
        # Fetch all_languages from context_metadata
        all_languages_row = _safe_fetchone(
            "SELECT value FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND key = 'languages_detected' LIMIT 1",
            (resolved_exp_id, repo_id),
        ) if _table_exists("context_metadata") else None
        all_languages_str = (all_languages_row['value'] if all_languages_row else '') or ''
        all_languages = [l.strip() for l in all_languages_str.split(',') if l.strip()] if all_languages_str else []

        primary_language = (repo_row['primary_language'] or '').strip()
        framework_name = (repo_row['framework_name'] or '').strip()
        framework_version = (repo_row['framework_version'] or '').strip()
        iac_type = (repo_row['iac_type'] or '').strip()

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
            if module_names:
                purpose_points.append(f"<li>Detected Terraform modules: {esc(', '.join(module_names[:6]))}.</li>")

        # Language breakdown bullet
        lang_parts = []
        if primary_language:
            lang_parts.append(f"Primary: <strong>{esc(primary_language)}</strong>")
        extra_langs = [l for l in all_languages if l.lower() != primary_language.lower()] if primary_language else all_languages
        if extra_langs:
            lang_parts.append("also: " + esc(', '.join(extra_langs)))
        if iac_type:
            lang_parts.append(f"IaC: <strong>{esc(iac_type)}</strong>")
        elif repo_row['iac_files_count']:
            lang_parts.append("IaC: Terraform")
        if lang_parts:
            purpose_points.insert(0, f"<li>Languages: {'; '.join(lang_parts)}. Scanned files: {repo_row['files_scanned'] or 0} (IaC: {repo_row['iac_files_count'] or 0}, code: {repo_row['code_files_count'] or 0}).</li>")

        # Framework bullet
        if framework_name or framework_version:
            fw_str = ' '.join(filter(None, [framework_name, framework_version]))
            purpose_points.insert(1, f"<li>Framework: <strong>{esc(fw_str)}</strong></li>")

        # If AI produced a context summary, render it as the prose lead for this section
        ai_lead_html = ""
        if ai_context_summary_val:
            ai_lead_html = (
                f'<div class="ai-context-summary">'
                f'<span class="ai-context-icon">🤖</span>'
                f'<p>{esc(ai_context_summary_val)}</p>'
                f'</div>'
            )

        if purpose_points:
            tech_details = (
                f'<details class="tech-details">'
                f'<summary>Technical details</summary>'
                f'<ul>{"".join(purpose_points)}</ul>'
                f'</details>'
            ) if ai_lead_html else f'<ul>{"".join(purpose_points)}</ul>'
        else:
            tech_details = ""

        # 1) Summary with resource and findings counts (with links)
        summary_items = []
        if _table_exists("resources"):
            resource_count = _safe_fetchone(
                "SELECT COUNT(*) as cnt FROM resources WHERE repo_id = ? AND experiment_id = ?",
                (repo_id, resolved_exp_id)
            )
            if resource_count and resource_count['cnt'] > 0:
                summary_items.append(f"<li><strong>Resources discovered:</strong> <a href=\"#assets\" style=\"color: var(--link-color);\"><strong>{resource_count['cnt']}</strong></a></li>")
        
        if _table_exists("findings"):
            findings_count = _safe_fetchone(
                "SELECT COUNT(*) as cnt FROM findings WHERE repo_id = ? AND experiment_id = ?",
                (repo_id, resolved_exp_id)
            )
            if findings_count and findings_count['cnt'] > 0:
                summary_items.append(f"<li><strong>Findings discovered:</strong> <a href=\"#findings\" style=\"color: var(--link-color);\"><strong>{findings_count['cnt']}</strong></a></li>")
        
        if summary_items:
            overview_sections.append(f"<h2>📊 Quick Summary</h2><ul>{''.join(summary_items)}</ul>")

        overview_sections.append(
            f"<h2>📝 What This Repo Does</h2>"
            f"{ai_lead_html}{tech_details}"
        )

        # 2) Where it is deployed
        # Filter out non-cloud/tool providers (terraform, kubernetes, helm, local, null, etc.)
        non_cloud = {'kubernetes', 'helm', 'terraform', 'local', 'null', 'random',
                     'time', 'tls', 'http', 'archive', 'external', 'template', 'unknown'}
        provider_rows = _safe_fetchall(
            """
            SELECT provider, COUNT(*) AS cnt
            FROM resources
            WHERE repo_id = ? AND experiment_id = ?
              AND provider IS NOT NULL AND TRIM(provider) != ''
            GROUP BY provider
            ORDER BY cnt DESC
            """,
            (repo_id, resolved_exp_id),
        ) if _table_exists("resources") else []
        # Filter to only cloud providers
        provider_rows = [r for r in provider_rows if r['provider'].lower() not in non_cloud]
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
            footprint_items = ''.join(f"<li>{esc(r['resource_type'])} x{r['cnt']}</li>" for r in footprint_rows)
            deploy_points.append(f"<li>Deployment footprint:<ul>{footprint_items}</ul></li>")
        if deploy_points:
            overview_sections.append("<h2>🌍 Where It Is Deployed</h2><ul>" + ''.join(deploy_points) + "</ul>")

        # 2b) Projects & Dependencies
        if _table_exists("dependencies"):
            deps_by_lang = {}
            deps_rows = _safe_fetchall(
                """
                SELECT DISTINCT language, project_path, COUNT(*) as dep_count
                FROM dependencies
                WHERE repo_id = ? AND experiment_id = ?
                GROUP BY language, project_path
                ORDER BY language, project_path
                """,
                (repo_id, resolved_exp_id),
            )
            for row in deps_rows:
                lang = row['language'] or 'Other'
                if lang not in deps_by_lang:
                    deps_by_lang[lang] = []
                deps_by_lang[lang].append(row)
            
            if deps_by_lang:
                deps_sections = []
                for lang in sorted(deps_by_lang.keys()):
                    projects = deps_by_lang[lang]
                    project_list = ''.join(
                        f"<li><code>{esc(p['project_path'] or 'root')}</code> – {p['dep_count']} dependencies</li>"
                        for p in projects
                    )
                    deps_sections.append(f"<strong>{esc(lang)}</strong><ul>{project_list}</ul>")
                
                overview_sections.append(
                    f"<h2>📦 Projects & Dependencies</h2><div style=\"margin: 12px 0;\">"
                    + "".join(deps_sections) + 
                    "</div>"
                )

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
            SELECT resource_name, resource_type, provider, source_file
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
        res_cols = _table_columns("resources")

        _AUTH_TYPE_LABEL = {
            'azurerm_user_assigned_identity': 'Managed Identity',
            'azurerm_managed_identity': 'Managed Identity',
            'aws_iam_role': 'IAM Role',
            'aws_iam_user': 'IAM User',
            'google_service_account': 'Service Account',
            'service_account': 'Service Account',
            'service_principal': 'Service Principal',
        }
        _CTYPE_DESC = {
            'grants_access_to': 'One resource grants permissions to another (e.g. RBAC role assignment).',
            'depends_on': 'A resource has an explicit or inferred dependency on another.',
            'routes_ingress_to': 'Inbound traffic is routed from one component to another (e.g. APIM → backend, ingress → service).',
            'connects_to': 'A connection between two resources (e.g. app reading from storage).',
            'reads_from': 'A resource reads data from another.',
            'writes_to': 'A resource writes data to another.',
            'manages': 'A resource manages or controls another.',
        }
        # Friendly display labels for common Terraform/Kubernetes resource types
        _RTYPE_OVERRIDES = {
            'azurerm_role_assignment':                  'Role Assignment',
            'azurerm_application_insights':             'App Insights',
            'azurerm_servicebus_namespace':             'Service Bus',
            'azurerm_servicebus_queue':                 'Service Bus Queue',
            'azurerm_servicebus_topic':                 'Service Bus Topic',
            'azurerm_api_management':                   'API Management',
            'azurerm_api_management_api':               'APIM API',
            'azurerm_api_management_api_policy':        'APIM Policy',
            'azurerm_kubernetes_cluster':               'AKS Cluster',
            'azurerm_resource_group':                   'Resource Group',
            'azurerm_key_vault':                        'Key Vault',
            'azurerm_key_vault_secret':                 'Key Vault Secret',
            'azurerm_storage_account':                  'Storage Account',
            'azurerm_storage_container':                'Storage Container',
            'azurerm_sql_server':                       'SQL Server',
            'azurerm_sql_database':                     'SQL Database',
            'azurerm_mssql_server':                     'SQL Server',
            'azurerm_mssql_database':                   'SQL Database',
            'azurerm_cosmosdb_account':                 'Cosmos DB',
            'azurerm_monitor_action_group':             'Monitor Action Group',
            'azurerm_monitor_activity_log_alert':       'Activity Log Alert',
            'azurerm_monitor_metric_alert':             'Metric Alert',
            'azurerm_container_registry':               'Container Registry',
            'azurerm_virtual_network':                  'VNet',
            'azurerm_subnet':                           'Subnet',
            'azurerm_network_security_group':           'NSG',
            'azurerm_user_assigned_identity':           'Managed Identity',
            'azuread_group':                            'AAD Group',
            'azuread_application':                      'AAD App',
            'kubernetes_deployment':                    'K8s Deployment',
            'kubernetes_service':                       'K8s Service',
            'kubernetes_ingress':                       'K8s Ingress',
            'kubernetes_config_map':                    'ConfigMap',
            'kubernetes_secret':                        'K8s Secret',
            'kubernetes_namespace':                     'K8s Namespace',
            'kubernetes_api':                           'K8s API',
            'helm_release':                             'Helm Release',
        }

        def _rtype_label(rtype: str) -> str:
            """Return a short human-friendly label for a resource type."""
            if not rtype:
                return ''
            if rtype in _RTYPE_OVERRIDES:
                return _RTYPE_OVERRIDES[rtype]
            # Strip provider prefix (azurerm_, kubernetes_, helm_, etc.) and title-case
            stripped = re.sub(r'^(azurerm|azuread|kubernetes|helm|aws|google)_', '', rtype)
            return stripped.replace('_', ' ').title()

        def _resource_label(name: str, rtype: str) -> str:
            """Format resource as 'name (Friendly Type)' or just 'name'."""
            label = _rtype_label(rtype)
            if label:
                return f"{name} ({label})"
            return name

        auth_detail_rows = []
        if rc_cols and res_cols and "source_resource_id" in rc_cols and "resource_name" in res_cols:
            _am_sel  = "rc.auth_method"   if "auth_method"   in rc_cols else "NULL AS auth_method"
            _enc_sel = "rc.is_encrypted"  if "is_encrypted"  in rc_cols else "NULL AS is_encrypted"
            _pro_sel = "rc.protocol"      if "protocol"      in rc_cols else "NULL AS protocol"
            auth_detail_rows = _safe_fetchall(
                f"""
                SELECT rc.connection_type, rc.authentication, rc.authorization,
                       {_am_sel}, {_enc_sel}, {_pro_sel},
                       src.resource_name AS src_name, src.resource_type AS src_type,
                       tgt.resource_name AS tgt_name, tgt.resource_type AS tgt_type
                FROM resource_connections rc
                LEFT JOIN resources src ON rc.source_resource_id = src.id
                LEFT JOIN resources tgt ON rc.target_resource_id = tgt.id
                WHERE rc.experiment_id = ?
                  AND (rc.source_repo_id = ? OR rc.target_repo_id = ?)
                  AND (
                    (rc.authentication IS NOT NULL AND TRIM(rc.authentication) != '') OR
                    (rc.authorization  IS NOT NULL AND TRIM(rc.authorization)  != '') OR
                    LOWER(rc.connection_type) LIKE '%auth%' OR
                    LOWER(rc.connection_type) LIKE '%grant%' OR
                    LOWER(rc.connection_type) LIKE '%access%'
                  )
                ORDER BY rc.connection_type, src.resource_name
                LIMIT 30
                """,
                (resolved_exp_id, repo_id, repo_id),
            )
        elif rc_cols:
            _am_sel2 = "auth_method" if "auth_method" in rc_cols else "NULL AS auth_method"
            auth_detail_rows = _safe_fetchall(
                f"""
                SELECT connection_type, authentication, authorization,
                       {_am_sel2}, NULL AS is_encrypted, NULL AS protocol,
                       NULL AS src_name, NULL AS src_type, NULL AS tgt_name, NULL AS tgt_type
                FROM resource_connections
                WHERE experiment_id = ?
                  AND (source_repo_id = ? OR target_repo_id = ?)
                  AND (
                    (authentication IS NOT NULL AND TRIM(authentication) != '') OR
                    (authorization  IS NOT NULL AND TRIM(authorization)  != '') OR
                    LOWER(connection_type) LIKE '%auth%' OR
                    LOWER(connection_type) LIKE '%grant%'
                  )
                ORDER BY connection_type
                LIMIT 20
                """,
                (resolved_exp_id, repo_id, repo_id),
            )

        auth_points = []

        # --- Identities sub-section ---
        if id_rows:
            id_items = []
            for r in id_rows:
                name     = r['resource_name'] or 'Unnamed'
                rtype    = r['resource_type'] or ''
                provider = r['provider'] or 'unknown'
                type_label = _AUTH_TYPE_LABEL.get(rtype.lower(), rtype)
                file_hint = (
                    f" <span class=\"file-link\" style=\"font-size:0.75rem\">{esc(r['source_file'])}</span>"
                    if r.get('source_file') else ""
                )
                id_items.append(
                    f"<li><strong>{esc(name)}</strong>"
                    f" <span class=\"how-badge\">{esc(type_label)}</span>"
                    f" <span style=\"color:var(--text-muted);font-size:0.78rem\">({esc(provider)})</span>"
                    f"{file_hint}</li>"
                )
            auth_points.append("<h3>Identities &amp; Service Principals</h3><ul>" + ''.join(id_items) + "</ul>")

        # --- Access grants sub-section (grouped by connection type) ---
        if auth_detail_rows:
            by_ctype_auth: dict = defaultdict(list)
            for r in auth_detail_rows:
                by_ctype_auth[r['connection_type'] or 'auth-related'].append(r)

            grant_items = []
            for ctype, rows in by_ctype_auth.items():
                sample   = rows[0]
                authz    = (sample.get('authorization') or '').strip()
                authn    = (sample.get('authentication') or '').strip()
                method   = (sample.get('auth_method') or '').strip()
                protocol = (sample.get('protocol') or '').strip()
                badges: list[str] = []
                if authz:                       badges.append(authz)
                if authn and authn != authz:    badges.append(authn)
                if method and method not in (authz, authn): badges.append(method)
                if protocol:                    badges.append(protocol)
                badge_html = ''.join(f" <span class=\"how-badge\">{esc(b)}</span>" for b in badges)

                ctype_desc = _CTYPE_DESC.get(ctype, '')
                desc_html  = (
                    f"<div style=\"color:var(--text-muted);font-size:0.78rem;margin-top:2px\">{esc(ctype_desc)}</div>"
                    if ctype_desc else ""
                )

                pairs_seen: set = set()
                pair_items = []
                for r in rows[:6]:
                    src_name = r.get('src_name') or r.get('src_type') or ''
                    tgt_name = r.get('tgt_name') or r.get('tgt_type') or ''
                    if not src_name and not tgt_name:
                        continue
                    pk = (src_name, tgt_name)
                    if pk in pairs_seen:
                        continue
                    pairs_seen.add(pk)
                    src = _resource_label(src_name, r.get('src_type') or '')
                    tgt = _resource_label(tgt_name, r.get('tgt_type') or '')
                    enc_html = " 🔒" if r.get('is_encrypted') else ""
                    pair_items.append(
                        f"<li style=\"color:var(--text-muted);font-size:0.8rem\">"
                        f"{esc(src)} → {esc(tgt)}{enc_html}</li>"
                    )
                if len(rows) > 6:
                    pair_items.append(
                        f"<li style=\"color:var(--text-muted);font-size:0.75rem;list-style:none\">"
                        f"…and {len(rows) - 6} more</li>"
                    )
                pairs_html = (
                    "<ul style=\"margin:4px 0 0 12px;list-style:disc\">" + ''.join(pair_items) + "</ul>"
                    if pair_items else ""
                )
                ctype_label = ctype.replace('_', ' ')
                grant_items.append(
                    f"<li><strong>{esc(ctype_label)}</strong> ({len(rows)}){badge_html}"
                    f"{desc_html}{pairs_html}</li>"
                )
            if grant_items:
                auth_points.append("<h3>Access Grants</h3><ul>" + ''.join(grant_items) + "</ul>")

        if auth_points:
            overview_sections.append("<h2>🔐 How Access Is Controlled</h2>" + ''.join(auth_points))

        # 5) Dependencies
        module_deps_data = []
        dep_detail_rows = []
        if _table_exists("resource_connections"):
            _rc2 = rc_cols or _table_columns("resource_connections")
            _rs2 = res_cols or _table_columns("resources")
            if _rc2 and _rs2 and "source_resource_id" in _rc2 and "resource_name" in _rs2:
                _enc2  = "rc.is_encrypted" if "is_encrypted" in _rc2 else "NULL AS is_encrypted"
                _cr2   = "rc.is_cross_repo" if "is_cross_repo" in _rc2 else "0 AS is_cross_repo"
                dep_detail_rows = _safe_fetchall(
                    f"""
                    SELECT rc.connection_type,
                           src.resource_name AS src_name, src.resource_type AS src_type,
                           tgt.resource_name AS tgt_name, tgt.resource_type AS tgt_type,
                           {_enc2}, {_cr2}
                    FROM resource_connections rc
                    LEFT JOIN resources src ON rc.source_resource_id = src.id
                    LEFT JOIN resources tgt ON rc.target_resource_id = tgt.id
                    WHERE rc.experiment_id = ? AND rc.source_repo_id = ?
                      AND rc.connection_type IS NOT NULL
                      AND LOWER(rc.connection_type) NOT IN ('contains')
                    ORDER BY rc.connection_type, src.resource_name
                    LIMIT 60
                    """,
                    (resolved_exp_id, repo_id),
                )
            elif _rc2:
                dep_detail_rows = _safe_fetchall(
                    """
                    SELECT connection_type, NULL AS src_name, NULL AS src_type,
                           NULL AS tgt_name, NULL AS tgt_type,
                           NULL AS is_encrypted, 0 AS is_cross_repo
                    FROM resource_connections
                    WHERE experiment_id = ? AND source_repo_id = ?
                      AND connection_type IS NOT NULL
                      AND LOWER(connection_type) NOT IN ('contains')
                    ORDER BY connection_type
                    LIMIT 30
                    """,
                    (resolved_exp_id, repo_id),
                )

        dep_points = []
        if dep_detail_rows:
            by_ctype_dep: dict = defaultdict(list)
            for r in dep_detail_rows:
                by_ctype_dep[r['connection_type']].append(r)

            conn_dep_items = []
            for ctype, rows in by_ctype_dep.items():
                ctype_label = ctype.replace('_', ' ')
                ctype_desc  = _CTYPE_DESC.get(ctype, '')
                desc_html   = (
                    f"<div style=\"color:var(--text-muted);font-size:0.78rem;margin-top:2px\">{esc(ctype_desc)}</div>"
                    if ctype_desc else ""
                )

                pairs_seen2: set = set()
                pair_items2 = []
                for r in rows[:6]:
                    src_name = r.get('src_name') or r.get('src_type') or ''
                    tgt_name = r.get('tgt_name') or r.get('tgt_type') or ''
                    if not src_name and not tgt_name:
                        continue
                    pk2 = (src_name, tgt_name)
                    if pk2 in pairs_seen2:
                        continue
                    pairs_seen2.add(pk2)
                    src = _resource_label(src_name, r.get('src_type') or '')
                    tgt = _resource_label(tgt_name, r.get('tgt_type') or '')
                    extras = ""
                    if r.get('is_encrypted'):
                        extras += " 🔒"
                    if r.get('is_cross_repo'):
                        extras += " <span class=\"how-badge\" style=\"font-size:0.7rem\">cross-repo</span>"
                    pair_items2.append(
                        f"<li style=\"color:var(--text-muted);font-size:0.8rem\">"
                        f"{esc(src)} → {esc(tgt)}{extras}</li>"
                    )
                if len(rows) > 6:
                    pair_items2.append(
                        f"<li style=\"color:var(--text-muted);font-size:0.75rem;list-style:none\">"
                        f"…and {len(rows) - 6} more</li>"
                    )
                pairs_html2 = (
                    "<ul style=\"margin:4px 0 0 12px;list-style:disc\">" + ''.join(pair_items2) + "</ul>"
                    if pair_items2 else ""
                )
                conn_dep_items.append(
                    f"<li><strong>{esc(ctype_label)}</strong> ({len(rows)})"
                    f"{desc_html}{pairs_html2}</li>"
                )
            if conn_dep_items:
                dep_points.append("<h3>Connection Dependencies</h3><ul>" + ''.join(conn_dep_items) + "</ul>")

        module_repo_root = None
        try:
            for ent in _resolve_repos():
                if str(ent.get('name') or '').strip().lower() != repo_name.lower():
                    continue
                if not ent.get('found'):
                    continue
                candidate_root = Path(str(ent.get('path') or '')).expanduser()
                if candidate_root.exists() and candidate_root.is_dir():
                    module_repo_root = candidate_root
                    break
        except Exception:
            module_repo_root = None

        def _infer_module_line_number(module_name: str, module_file: str) -> int | None:
            if not module_repo_root or not module_file or not module_name:
                return None

            rel_path = Path(module_file)
            candidate_paths = [module_repo_root / rel_path]
            if module_file.startswith('./'):
                candidate_paths.append(module_repo_root / module_file[2:])
            candidate_paths.append(module_repo_root / rel_path.name)

            seen = set()
            pattern = re.compile(r'^\s*module\s+"([^"]+)"\s*\{')
            for candidate in candidate_paths:
                try:
                    resolved = candidate.resolve()
                except Exception:
                    resolved = candidate
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                if not resolved.exists() or not resolved.is_file():
                    continue

                try:
                    lines = resolved.read_text(encoding='utf-8', errors='ignore').splitlines()
                except Exception:
                    continue

                for idx, line in enumerate(lines, start=1):
                    m = pattern.match(line)
                    if m and m.group(1).strip() == module_name:
                        return idx
            return None

        # Load existing module→repo mappings
        existing_mappings: dict = {}
        if _table_exists('context_metadata'):
            _map_rows = _safe_fetchall(
                "SELECT key, value FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND key LIKE 'module.mapping.%' ORDER BY id DESC LIMIT 200",
                (resolved_exp_id, repo_id),
            )
            for _mr in _map_rows:
                _k = (_mr['key'] or '')
                if _k.startswith('module.mapping.'):
                    _modname = _k.split('module.mapping.', 1)[1]
                    try:
                        existing_mappings[_modname] = json.loads(_mr['value'])
                    except Exception:
                        existing_mappings[_modname] = _mr['value']

        # Build structured module_deps_data for interactive template dropdowns
        module_rows = _safe_fetchall(
            """
            SELECT key, value
            FROM context_metadata
            WHERE experiment_id = ? AND repo_id = ? AND (key LIKE 'module:%' OR key LIKE 'terraform.module.%')
            ORDER BY
                -- prefer terraform.module.* (richer JSON payload) over module:* entries
                CASE WHEN key LIKE 'terraform.module.%' THEN 0 ELSE 1 END,
                key
            LIMIT 100
            """,
            (resolved_exp_id, repo_id),
        ) if _table_exists("context_metadata") else []
        
        # Group modules by source, deduplicating by name within each group.
        # Both 'module:name' and 'terraform.module.name' keys may exist for the
        # same module (different discovery passes); only keep the first seen.
        modules_by_source: dict = {}
        seen_names: set = set()
        for r in module_rows:
            key = r['key']
            if key.startswith('module:'):
                name = key.split(':', 1)[1]
            elif 'terraform.module.' in key:
                name = key.split('terraform.module.', 1)[1]
            else:
                name = key

            if name in seen_names:
                continue
            seen_names.add(name)

            raw_value = (r['value'] or '').strip()
            source = ''
            module_file = ''
            module_line = None

            if raw_value:
                parsed_value = None
                try:
                    parsed_value = json.loads(raw_value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed_value = None

                if isinstance(parsed_value, dict):
                    source = str(parsed_value.get('source') or '').strip()
                    module_file = str(parsed_value.get('file') or '').strip()
                    module_line = parsed_value.get('line')
                else:
                    source = raw_value

            if module_line is not None:
                try:
                    module_line = int(str(module_line).strip())
                except (TypeError, ValueError):
                    module_line = None
            if module_line is None:
                module_line = _infer_module_line_number(name, module_file)

            if source not in modules_by_source:
                modules_by_source[source] = []
            modules_by_source[source].append({
                'name': name,
                'file': module_file,
                'line': module_line,
                'current_mapping': existing_mappings.get(name),
            })
        
        # Convert to list of source groups
        for source in sorted(modules_by_source.keys()):
            module_deps_data.append({
                'source': source,
                'modules': sorted(modules_by_source[source], key=lambda m: m['name']),
            })

        # Module Dependencies are fully handled by the interactive template section below the body.
        # Do not add them to dep_points here to avoid a duplicate header with no mapping controls.

        if dep_points:
            overview_sections.append("<h2>🧩 Dependencies</h2>" + ''.join(dep_points))

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
        seen_titles: set = set()
        for r in top_issue_rows:
            title = (r['title'] or r['rule_id'] or 'Untitled finding')
            if title in seen_titles:
                continue
            seen_titles.add(title)
            issue_points.append(
                f"<li><strong>{esc(title)}</strong> (score {r['severity_score']})</li>"
            )
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

    # Extract timestamps from ai_overview_rows for footer
    analysis_completed_at = None
    analysis_recovered = None
    if 'ai_overview_rows' in locals() and ai_overview_rows:
        for _r in ai_overview_rows:
            if _r["key"] == "ai_analysis_completed_at":
                analysis_completed_at = (_r["value"] or "").strip()
            elif _r["key"] == "ai_analysis_recovered":
                analysis_recovered = (_r["value"] or "").strip()
    
    # Fallback: if no AI metadata, use scan timestamp
    if not analysis_completed_at:
        try:
            conn = _get_db()
            if conn:
                scanned_row = _safe_fetchone(
                    "SELECT scanned_at FROM repositories WHERE LOWER(repo_name) = LOWER(?) AND experiment_id = ? ORDER BY scanned_at DESC LIMIT 1",
                    (repo_name, resolved_exp_id),
                    conn
                )
                if scanned_row and scanned_row.get("scanned_at"):
                    # Format ISO timestamp nicely
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(scanned_row["scanned_at"].replace("Z", "+00:00"))
                        analysis_completed_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        analysis_completed_at = str(scanned_row["scanned_at"])
                conn.close()
        except:
            pass

    final_html = '<div class="markdown-content">' + ''.join(overview_sections) + '</div>' if overview_sections else ''
    
    # Add footer with analysis metadata if available
    if analysis_completed_at or analysis_recovered:
        footer_html = '<footer class="overview-footer">'
        if analysis_completed_at:
            footer_html += '<div class="overview-footer-item"><div class="overview-footer-label">Analysis completed</div><div class="overview-footer-value">' + analysis_completed_at + '</div></div>'
        if analysis_recovered:
            footer_html += '<div class="overview-footer-item"><div class="overview-footer-label">Recovered</div><div class="overview-footer-value">' + analysis_recovered + '</div></div>'
        footer_html += '</footer>'
        final_html += footer_html
    
    # Fetch available repos for module mapping dropdowns — ReposToScan.txt is the primary source
    available_repos = []
    try:
        # Seed from ReposToScan.txt first so those entries appear at the top
        for _ent in _resolve_repos():
            _rname = (_ent.get('name') or '').strip()
            if _rname and _rname not in available_repos:
                available_repos.append(_rname)
        # Supplement with any repos recorded in the DB that aren't already listed
        if _table_exists('repositories'):
            _repo_name_rows = _safe_fetchall("SELECT DISTINCT repo_name FROM repositories ORDER BY repo_name")
            for _rr in _repo_name_rows:
                _rname = (_rr['repo_name'] or '').strip()
                if _rname and _rname not in available_repos:
                    available_repos.append(_rname)
    except Exception as _e:
        print(f"Warning: Could not build available_repos: {_e}")
        available_repos = []
    
    # Load AI-provided metadata (if present) for template rendering

    ai_new_assets = None
    ai_fixed_information = None
    try:
        sys.path.insert(0, str(REPO_ROOT))
        ai_new_assets = db_helpers.get_context_metadata(resolved_exp_id, repo_name, 'ai_new_assets', namespace='ai_overview')
        ai_fixed_information = db_helpers.get_context_metadata(resolved_exp_id, repo_name, 'ai_fixed_information', namespace='ai_overview')
        # db_helpers.get_context_metadata may return JSON strings; attempt to parse
        try:
            if ai_new_assets:
                ai_new_assets = json.loads(ai_new_assets)
        except Exception:
            pass
        try:
            if ai_fixed_information:
                ai_fixed_information = json.loads(ai_fixed_information)
        except Exception:
            pass
    except Exception:
        ai_new_assets = None
        ai_fixed_information = None

    return _db_render('tab_overview.html', overview_html=final_html, experiment_id=resolved_exp_id, repo_name=repo_name, ai_new_assets=ai_new_assets, ai_fixed_information=ai_fixed_information, module_deps=module_deps_data, available_repos=available_repos, analysis_completed_at=analysis_completed_at, analysis_recovered=analysis_recovered)


@app.route("/api/module/mappings/<experiment_id>/<repo_name>", methods=["POST"])
def api_module_mappings_save(experiment_id: str, repo_name: str):
    """Save module→repo mappings to context_metadata."""
    data = request.get_json(silent=True) or {}
    mappings = data.get('mappings') or {}
    if not isinstance(mappings, dict):
        return jsonify({'ok': False, 'error': 'mappings must be an object'}), 400

    conn = _get_db()
    if conn is None:
        return jsonify({'ok': False, 'error': 'DB unavailable'}), 503

    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id) or experiment_id

        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1",
            (resolved_exp_id, repo_name),
        ).fetchone()
        if not repo_row:
            return jsonify({'ok': False, 'error': f'Repo {repo_name!r} not found in experiment'}), 404
        repo_id = repo_row['id']

        if not _table_exists(conn, 'context_metadata'):
            return jsonify({'ok': False, 'error': 'context_metadata table unavailable'}), 503

        for mod_name, mapping_val in mappings.items():
            key = f'module.mapping.{mod_name}'
            if mapping_val is None:
                conn.execute(
                    "DELETE FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND key = ?",
                    (resolved_exp_id, repo_id, key),
                )
            else:
                value_str = json.dumps(mapping_val) if not isinstance(mapping_val, str) else mapping_val
                try:
                    conn.execute(
                        """
                        INSERT INTO context_metadata (experiment_id, repo_id, namespace, key, value, source)
                        VALUES (?, ?, 'module_mapping', ?, ?, 'ui')
                        ON CONFLICT(experiment_id, repo_id, namespace, key) DO UPDATE SET
                          value = excluded.value,
                          source = excluded.source,
                          created_at = CURRENT_TIMESTAMP
                        """,
                        (resolved_exp_id, repo_id, key, value_str, 'ui'),
                    )
                except Exception:
                    conn.execute(
                        "DELETE FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND key = ?",
                        (resolved_exp_id, repo_id, key),
                    )
                    conn.execute(
                        "INSERT INTO context_metadata (experiment_id, repo_id, namespace, key, value, source) VALUES (?, ?, 'module_mapping', ?, ?, 'ui')",
                        (resolved_exp_id, repo_id, key, value_str, 'ui'),
                    )

        conn.commit()
        return jsonify({'ok': True, 'saved': len(mappings)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500
    finally:
        conn.close()


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

        # Best-effort: if k8s workloads exist but no AKS cluster is defined in this repo,
        # create an inferred cluster so Assets can nest correctly.
        try:
            from Scripts.Persist import db_helpers as _dbh  # type: ignore
            _dbh.ensure_inferred_aks_cluster(experiment_target, repo_name)
            _dbh.infer_aks_cluster_link(experiment_target, repo_name)
        except Exception:
            pass

        rows = conn.execute(
            """
            SELECT res.id, res.resource_type, res.resource_name, res.provider,
                   res.region, res.source_file, res.source_line_start,
                   res.discovered_by, res.discovery_method, res.status,
                   res.parent_resource_id,
                   COUNT(f.id) AS finding_count,
                   MAX(CASE UPPER(f.base_severity)
                       WHEN 'CRITICAL' THEN 5 WHEN 'HIGH' THEN 4
                       WHEN 'MEDIUM'   THEN 3 WHEN 'LOW'  THEN 2
                       WHEN 'INFO'     THEN 1 ELSE 0 END) AS max_sev_rank
            FROM resources res
            JOIN repositories repo ON res.repo_id = repo.id
            LEFT JOIN findings f ON (
                f.experiment_id = res.experiment_id AND
                f.repo_id = res.repo_id AND
                (f.source_file = res.source_file OR f.source_file LIKE '%' || res.source_file) AND
                (
                    f.source_line_start = res.source_line_start OR
                    (
                        f.source_line_start > res.source_line_start AND
                        (
                            res.source_line_end IS NULL OR
                            f.source_line_start <= res.source_line_end
                        ) AND
                        NOT EXISTS (
                            SELECT 1 FROM resources r2
                            WHERE r2.experiment_id = res.experiment_id AND
                                r2.repo_id = res.repo_id AND
                                (r2.source_file = res.source_file OR r2.source_file LIKE '%' || res.source_file) AND
                                r2.source_line_start > res.source_line_start AND
                                r2.source_line_start <= f.source_line_start
                        )
                    )
                )
            )
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            GROUP BY res.id
            ORDER BY res.provider, res.resource_type, res.resource_name
            """,
            (repo_name, experiment_target),
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

        # Build parent map for depth calculation
        asset_id_to_parent_id: dict = {}
        try:
            for hr in hierarchy_rows:
                asset_id_to_parent_id[hr['child_id']] = hr['parent_id']
        except Exception:
            pass

        # Function to calculate depth and all ancestor IDs for an asset
        def get_depth_and_ancestors(asset_id: str) -> tuple:
            """Returns (depth, [list of ancestor IDs from direct parent up to root])"""
            ancestors = []
            current_id = asset_id
            max_depth = 100  # Prevent infinite loops
            while current_id in asset_id_to_parent_id and max_depth > 0:
                parent_id = asset_id_to_parent_id[current_id]
                ancestors.append(parent_id)
                current_id = parent_id
                max_depth -= 1
            return len(ancestors), ancestors

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
            is_identity_like = (
                (a.get('render_category', '').lower() == 'identity')
                or (rtype_lower in skip_types)
                or rtype_lower.startswith('azuread_')
            )
            if is_identity_like:
                if not include_hidden:
                    hidden_count += 1
                    continue
                # else: include identity resources when include_hidden is True

            # If include_hidden is false, hide generator/utility types and any resource
            # explicitly marked as not to be displayed on architecture diagrams.
            hide_tokens = set(hidden_tokens)
            hide_tokens.add("terraform_data")
            if not include_hidden:
                if any(tok in rtype_lower for tok in hide_tokens) or (not a.get('display_on_architecture_chart', True)):
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

        # Calculate depth and ancestors for each asset
        for a in assets:
            depth, ancestors = get_depth_and_ancestors(a.get('id'))
            a['depth'] = depth
            a['ancestors'] = ','.join(str(x) for x in ancestors) if ancestors else ''

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


@app.route("/api/finding/triage/<experiment_id>/<finding_id>", methods=["POST"])
def api_finding_triage(experiment_id: str, finding_id: str):
    """Update triage status for a finding (learning signal)."""
    payload = request.get_json(force=True, silent=True) or {}
    status = (payload.get("triage_status") or "").strip()
    reason = (payload.get("triage_reason") or "").strip()
    if status not in ("valid", "false_positive", "needs_context"):
        return jsonify({"error": "invalid_status"}), 400

    conn = _get_db()
    if conn is None:
        return jsonify({"error": "db_unavailable"}), 500
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE findings SET triage_status = ?, triage_reason = ?, triage_set_by = ?, triage_set_at = ? WHERE experiment_id = ? AND id = ?",
            (status, reason, "human", now, experiment_id, int(finding_id)),
        )
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()



def _canonical_provider_key(provider: str | None) -> str:
    value = (provider or "").strip().lower()
    if value == "oracle":
        return "oci"
    return value or "unknown"


def _provider_display_name(provider: str | None) -> str:
    provider_key = _canonical_provider_key(provider)
    provider_map = {
        "azure": "Azure",
        "aws": "AWS",
        "gcp": "GCP",
        "google": "GCP",
        "kubernetes": "Kubernetes",
        "terraform": "Terraform",
        "alicloud": "Alicloud",
        "oci": "Oracle",
        "tencentcloud": "Tencent Cloud",
        "huaweicloud": "Huawei Cloud",
        "unknown": "Unknown",
    }
    return provider_map.get(provider_key, provider_key.title())


def _infer_provider_from_rule(rule_id: str) -> str:
    """Infer cloud provider from rule_id prefix. Returns lowercase to match resources table.
    
    Code findings (connection strings, hardcoded secrets, etc.) are returned as 'code'
    Infrastructure-as-code findings (terraform, cloudformation) are returned as 'code' unless they're cloud-specific.
    """
    if not rule_id:
        return 'Unknown'
    rule_lower = str(rule_id).lower()
    
    # Code/secrets findings - not cloud-specific
    code_patterns = {'connection-string', 'hardcoded', 'credential', 'secret', 'api-key', 'token', 'password'}
    if any(pattern in rule_lower for pattern in code_patterns):
        return 'code'
    
    # Cloud provider patterns
    if rule_lower.startswith('aws-'):
        return 'aws'
    elif rule_lower.startswith('azure-') or rule_lower.startswith('azurerm-'):
        return 'azure'
    elif rule_lower.startswith('gcp-') or rule_lower.startswith('google-'):
        return 'gcp'
    elif rule_lower.startswith('oci-'):
        return 'oci'
    elif rule_lower.startswith('alicloud-'):
        return 'alicloud'
    elif rule_lower.startswith('tencentcloud-'):
        return 'tencentcloud'
    elif rule_lower.startswith('huaweicloud-'):
        return 'huaweicloud'
    
    # IaC tools (terraform, cloudformation, etc.) - treat as code findings
    iac_patterns = {'terraform-', 'cloudformation-', 'helm-', 'kubernetes-', 'docker-'}
    if any(pattern in rule_lower for pattern in iac_patterns):
        return 'code'
    
    return 'Unknown'

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
                   f.source_line_end, f.code_snippet, f.proposed_fix,
                   f.resource_id, f.category,
                   COALESCE(NULLIF(f.triage_status,''), 'valid') AS triage_status,
                   f.triage_reason, f.triage_set_by, f.triage_set_at,
                   f.credential_classification, f.credential_note,
                   dev.adjusted_score AS dev_score, dev.confidence AS dev_confidence,
                   dev.reasoning AS dev_reasoning, dev.key_concerns AS dev_key_concerns,
                   plat.adjusted_score AS platform_score, plat.confidence AS platform_confidence,
                   plat.reasoning AS platform_reasoning, plat.key_concerns AS platform_key_concerns,
                   sec.adjusted_score AS security_score, sec.confidence AS security_confidence,
                   sec.reasoning AS security_reasoning, sec.key_concerns AS security_key_concerns,
                   ai.adjusted_score AS ai_score, ai.confidence AS ai_confidence,
                   ai.reasoning AS ai_reasoning, ai.reviewer_type AS ai_agent_used,
                   COALESCE(r.provider, '') AS provider,
                   r.resource_type, r.resource_name, f.rule_id
            FROM findings f
            JOIN repositories repo ON f.repo_id = repo.id
            LEFT JOIN resources r ON f.resource_id = r.id
            LEFT JOIN skeptic_reviews dev ON dev.finding_id = f.id AND dev.reviewer_type = 'dev'
            LEFT JOIN skeptic_reviews plat ON plat.finding_id = f.id AND plat.reviewer_type = 'platform'
            LEFT JOIN skeptic_reviews sec ON sec.finding_id = f.id AND sec.reviewer_type = 'security'
            LEFT JOIN skeptic_reviews ai ON ai.finding_id = f.id 
                AND ai.reviewer_type IN ('DevSkeptic', 'PlatformSkeptic', 'SecurityAgent', 'ai_copilot')
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            ORDER BY
                CASE UPPER(f.base_severity)
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'MEDIUM' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,
                f.severity_score DESC,
                f.rule_id, f.source_file
            """,
            (repo_name, target_exp),
        ).fetchall()
        findings = [dict(r) for r in rows]
        
        # Infer provider from rule_id if not set
        for f in findings:
            if not f.get('provider'):
                f['provider'] = _infer_provider_from_rule(f.get('rule_id', ''))
            provider_key = _canonical_provider_key(f.get('provider'))
            f['provider'] = 'Unknown' if provider_key == 'unknown' else provider_key
        
        # Sort by severity first (Critical → High → Medium → Low), then provider, then score desc
        provider_order = {
            'aws': 1,
            'azure': 2,
            'gcp': 3,
            'oci': 4,
            'alicloud': 5,
            'tencentcloud': 6,
            'huaweicloud': 7,
            'Unknown': 8,
        }
        severity_order = {'CRITICAL': 1, 'HIGH': 2, 'MEDIUM': 3, 'LOW': 4, 'INFO': 5}
        findings.sort(key=lambda f: (
            severity_order.get((f.get('base_severity') or 'INFO').upper(), 6),
            -(f.get('severity_score', 0) or 0),
            provider_order.get(_canonical_provider_key(f.get('provider')), provider_order['Unknown']),
        ))

        # Deduplicate same-rule / same-file findings: group them and attach a
        # hit_count so the template can show a "×N" badge instead of N identical rows.
        # The first occurrence in each group is kept as the representative row; the
        # remaining rows are discarded from the rendered list.
        _seen_rule_file: dict[tuple, int] = {}
        deduped_findings = []
        for f in findings:
            key = (f.get('rule_id') or '', f.get('source_file') or '')
            if key in _seen_rule_file:
                # Increment the hit count on the representative row
                deduped_findings[_seen_rule_file[key]]['hit_count'] = (
                    deduped_findings[_seen_rule_file[key]].get('hit_count', 1) + 1
                )
            else:
                _seen_rule_file[key] = len(deduped_findings)
                f['hit_count'] = 1
                deduped_findings.append(f)
        findings = deduped_findings

        # Enrich each finding with surrounding file context (±4 lines)
        CONTEXT_LINES = 4
        for f in findings:
            f['file_context'] = None
            src = (f.get('source_file') or '').strip()
            line_start = f.get('source_line_start')
            line_end   = f.get('source_line_end') or line_start
            if not src or not line_start:
                continue
            try:
                p = Path(src)
                if not p.exists():
                    continue
                all_lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
                ctx_start = max(1, line_start - CONTEXT_LINES)
                ctx_end   = min(len(all_lines), (line_end or line_start) + CONTEXT_LINES)
                ctx_lines = []
                for ln_no in range(ctx_start, ctx_end + 1):
                    ctx_lines.append({
                        'no': ln_no,
                        'text': all_lines[ln_no - 1],
                        'highlight': line_start <= ln_no <= (line_end or line_start),
                    })
                f['file_context'] = {
                    'lines': ctx_lines,
                    'path': src,
                    'start': ctx_start,
                }
            except Exception:
                pass

        return _db_render("tab_findings.html", findings=findings, experiment_id=resolved_exp_id)
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
                    'google_api_gateway_api_config',
                    'google_api_gateway_gateway',
                    'oci_apigateway_deployment',
                    'alicloud_api_gateway_api',
                    'alicloud_api_gateway_group'
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
                    ) as internet_access_signals,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'protocol' LIMIT 1),
                        'HTTPS'
                    ) as protocol
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

        operation_count = sum(
            1 for op in operations if op.get('resource_type') != 'azurerm_api_management_api'
        )

        ingress_resources = [dict(r) for r in res_rows]

        # Network ingress: always query for well-known internet-facing resource types
        # regardless of exposure_analysis results (since exposure analysis may be incomplete)
        network_ingress: list[dict] = []
        _NETWORK_INGRESS_TYPES = tuple(sorted(InternetExposureDetector.get_public_entry_types()))
        try:
            ni_rows = conn.execute(
                f"""
                SELECT DISTINCT
                    r.id, r.resource_name, r.resource_type,
                    r.provider, r.region, r.source_file, r.source_line_start
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type IN ({','.join('?' * len(_NETWORK_INGRESS_TYPES))})
                ORDER BY r.provider, r.resource_type, r.resource_name
                """,
                (repo_name, target_exp) + _NETWORK_INGRESS_TYPES,
            ).fetchall()
            network_ingress = [dict(r) for r in ni_rows]
        except Exception as e:
            print(f"Warning: Could not fetch network ingress resources: {e}")

        # Fallback: when no ingress resources were found from exposure_analysis or
        # finding_context, surface resources that have internet_access=true in
        # resource_properties. These are set during Phase 1 context extraction and
        # represent the best available internet-exposure signal before Phase 2 runs.
        if not ingress_resources:
            try:
                prop_rows = conn.execute(
                    """
                    SELECT DISTINCT
                        r.id,
                        r.resource_name,
                        r.resource_type,
                        r.provider,
                        r.region,
                        r.source_file,
                        r.source_line_start,
                        'internet_access_property' AS exposure_type,
                        COALESCE(
                            (SELECT property_value FROM resource_properties
                             WHERE resource_id = r.id AND property_key = 'internet_access_signals' LIMIT 1),
                            'internet_access = true'
                        ) AS exposure_value,
                        0 AS is_confirmed
                    FROM resource_properties rp
                    JOIN resources r ON rp.resource_id = r.id
                    JOIN repositories repo ON r.repo_id = repo.id
                    WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                      AND rp.property_key = 'internet_access'
                      AND LOWER(COALESCE(rp.property_value, '')) = 'true'
                    ORDER BY r.resource_type, r.resource_name
                    """,
                    (repo_name, target_exp),
                ).fetchall()
                ingress_resources = [dict(r) for r in prop_rows]
            except Exception as e:
                print(f"Warning: Could not fetch internet_access properties for ingress fallback: {e}")

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
            operation_count=operation_count,
            network_ingress=network_ingress,
        )
    except Exception as exc:
        return _db_render(
            "tab_ingress.html",
            ingress_resources=[],
            ingress_connections=[],
            operations=[],
            children_by_parent={},
            operations_by_id={},
            operation_count=0,
            network_ingress=[],
            error=str(exc),
        )
    finally:
        conn.close()


@app.route("/api/view/subscription/<experiment_id>/<repo_name>")
def api_view_subscription(experiment_id: str, repo_name: str):
    """Render subscription-wide Q&A (stored per experiment, applies across repos)."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_subscription.html", entries=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            resolved_exp_id = experiment_id
        if not _table_exists(conn, "subscription_context"):
            return _db_render("tab_subscription.html", repo_entries=[], global_entries=[], experiment_id=resolved_exp_id, repo_name=repo_name)

        # Repo-scoped
        repo_rows = conn.execute(
            "SELECT id, question, answer, answered_by, confidence, tags, created_at, updated_at FROM subscription_context WHERE experiment_id = ? AND scope_key = 'repo' AND LOWER(repo_name)=LOWER(?) ORDER BY updated_at DESC, id DESC LIMIT 200",
            (resolved_exp_id, repo_name),
        ).fetchall()
        # Global
        global_rows = conn.execute(
            "SELECT id, question, answer, answered_by, confidence, tags, created_at, updated_at FROM subscription_context WHERE experiment_id = ? AND scope_key = 'global' ORDER BY updated_at DESC, id DESC LIMIT 200",
            (resolved_exp_id,),
        ).fetchall()

        repo_entries = [dict(r) for r in repo_rows] if repo_rows else []
        global_entries = [dict(r) for r in global_rows] if global_rows else []

        # Suggested questions from AI scan (unanswered)
        suggested = []
        try:
            repo_row = conn.execute(
                "SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name)=LOWER(?) LIMIT 1",
                (resolved_exp_id, repo_name),
            ).fetchone()
            repo_id = int(repo_row["id"]) if repo_row else None
            if repo_id and _table_exists(conn, "context_metadata"):
                cm = conn.execute(
                    "SELECT value FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND namespace = 'ai_overview' AND key = 'ai_open_questions' ORDER BY id DESC LIMIT 1",
                    (resolved_exp_id, repo_id),
                ).fetchone()
                if cm and cm["value"]:
                    parsed_q = json.loads(cm["value"])
                    if isinstance(parsed_q, list):
                        answered_keys = set()
                        for e in repo_entries:
                            answered_keys.add(("repo", (e.get("question") or "").strip().lower()))
                        for e in global_entries:
                            answered_keys.add(("global", (e.get("question") or "").strip().lower()))

                        for q in parsed_q:
                            if not isinstance(q, dict):
                                continue
                            text = (q.get("question") or "").strip()
                            if not text:
                                continue
                            # default scope suggestion is repo; if user answers globally it will be stored there
                            if ("repo", text.lower()) in answered_keys or ("global", text.lower()) in answered_keys:
                                continue
                            suggested.append({
                                "question": text,
                                "file": (q.get("file") or ""),
                                "line": q.get("line"),
                                "asset": (q.get("asset") or ""),
                            })
        except Exception:
            suggested = []

        return _db_render(
            "tab_subscription.html",
            repo_entries=repo_entries,
            global_entries=global_entries,
            suggested_questions=suggested,
            experiment_id=resolved_exp_id,
            repo_name=repo_name,
        )
    except Exception as exc:
        return _db_render("tab_subscription.html", entries=[], error=str(exc), experiment_id=experiment_id, repo_name=repo_name)
    finally:
        conn.close()


@app.route("/api/subscription_context/<experiment_id>", methods=["POST"])
def api_subscription_context_upsert(experiment_id: str):
    """Upsert a subscription-wide Q&A entry."""
    payload = request.get_json(force=True, silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "missing_question"}), 400
    scope = (payload.get("scope") or "repo").strip().lower() or "repo"
    if scope not in ("repo", "global"):
        scope = "repo"
    repo_name = (payload.get("repo_name") or "").strip()

    answer = (payload.get("answer") or "").strip()
    answered_by = (payload.get("answered_by") or "").strip()
    tags = (payload.get("tags") or "").strip()
    confidence = payload.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None and confidence != "" else None
    except Exception:
        confidence = None

    conn = _get_db()
    if conn is None:
        return jsonify({"error": "db_unavailable"}), 500
    try:
        if not _table_exists(conn, "subscription_context"):
            return jsonify({"error": "table_missing"}), 500
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # Upsert key is (experiment_id, scope_key, repo_name?, question)
        if scope == "repo" and not repo_name:
            repo_name = "(unknown)"

        row = conn.execute(
            "SELECT id FROM subscription_context WHERE experiment_id = ? AND scope_key = ? AND LOWER(COALESCE(repo_name,'')) = LOWER(?) AND LOWER(question) = LOWER(?) LIMIT 1",
            (experiment_id, scope, repo_name if scope == 'repo' else '', question),
        ).fetchone()

        # If this is a new question, only allow questions that were generated by the AI scan (ai_open_questions)
        if not row:
            allowed = False
            try:
                # Determine repo_id from repo_name for this experiment
                if repo_name:
                    rr = conn.execute(
                        "SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name)=LOWER(?) LIMIT 1",
                        (experiment_id, repo_name),
                    ).fetchone()
                    repo_id = int(rr["id"]) if rr else None
                else:
                    repo_id = None

                if repo_id and _table_exists(conn, "context_metadata"):
                    cm = conn.execute(
                        "SELECT value FROM context_metadata WHERE experiment_id = ? AND repo_id = ? AND namespace='ai_overview' AND key='ai_open_questions' ORDER BY id DESC LIMIT 1",
                        (experiment_id, repo_id),
                    ).fetchone()
                    if cm and cm["value"]:
                        pq = json.loads(cm["value"])
                        if isinstance(pq, list):
                            for q in pq:
                                if isinstance(q, dict) and str(q.get('question') or '').strip().lower() == question.lower():
                                    allowed = True
                                    break
            except Exception:
                allowed = False

            if not allowed:
                return jsonify({"error": "unknown_question"}), 400
        if row:
            conn.execute(
                "UPDATE subscription_context SET answer = ?, answered_by = ?, confidence = ?, tags = ?, updated_at = ? WHERE id = ?",
                (answer, answered_by, confidence, tags, now, int(row["id"])),
            )
        else:
            conn.execute(
                "INSERT INTO subscription_context (experiment_id, scope_key, repo_name, question, answer, answered_by, confidence, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (experiment_id, scope, repo_name if scope == 'repo' else None, question, answer, answered_by, confidence, tags, now, now),
            )
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
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
        
        # Build outbound connections: only rows where THIS repo's resource is the SOURCE
        # (i.e. the code hosted here is making the call out).  Ingress connections
        # (where source_resource_id = 0 / Internet calls INTO the service) must not appear here.
        egress_connections = []
        try:
            rc_rows = conn.execute(
                """
                SELECT DISTINCT 
                    COALESCE(src.resource_name, 'Unknown source') AS source_name,
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
                JOIN resources src ON rc.source_resource_id = src.id
                LEFT JOIN resources tgt ON rc.target_resource_id = tgt.id
                JOIN repositories repo_src ON src.repo_id = repo_src.id
                LEFT JOIN repositories repo_tgt ON tgt.repo_id = repo_tgt.id
                WHERE LOWER(repo_src.repo_name) = LOWER(?)
                  AND repo_src.experiment_id = ?
                  AND COALESCE(rc.connection_type, '') NOT IN (
                    'contains', 'orchestrates', 'composed_of', 'parent_child',
                    'routes_ingress_to', 'restricts_access'
                  )
                  AND (rc.inferred_internet IS NULL OR rc.inferred_internet = 0)
                ORDER BY source_name, target_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in rc_rows:
                entry = dict(row)
                purpose = ""
                tgt_type = (entry.get("target_type") or "").lower()
                conn_type = (entry.get("connection_type") or "").lower()
                target_domain = (entry.get("target_domain") or "").lower()

                # Check connection_metadata for service catalogue info first
                try:
                    meta = json.loads(entry.get("connection_metadata") or "{}")
                    if meta.get("category"):
                        cat = meta["category"]
                        purpose = {
                            "alerting":       "alerting / incident management",
                            "observability":  "observability / APM",
                            "logging":        "log aggregation",
                            "payment":        "payment processing",
                            "email":          "email delivery",
                            "sms":            "SMS / communication",
                            "messaging":      "messaging",
                            "auth":           "external auth / identity",
                            "vcs":            "version control API",
                            "issue_tracking": "issue tracking",
                            "data_warehouse": "data warehouse",
                        }.get(cat, cat)
                        if meta.get("service"):
                            entry["target_name"] = entry.get("target_name") or meta["service"]
                except Exception:
                    pass

                if not purpose:
                    # Classify by target resource type
                    if "topic" in tgt_type or "queue" in tgt_type or "servicebus" in tgt_type:
                        purpose = "messaging"
                    elif "database" in tgt_type or "sql" in tgt_type or "cosmos" in tgt_type or "redis" in tgt_type:
                        purpose = "data storage"
                    elif "storage" in tgt_type or "blob" in tgt_type:
                        purpose = "blob storage"
                    elif "vault" in tgt_type or "keyvault" in tgt_type:
                        purpose = "secrets"
                    elif "insight" in tgt_type or "monitor" in tgt_type or "telemetry" in tgt_type:
                        purpose = "telemetry"
                    elif "apim" in tgt_type or "api_management" in tgt_type or "api management" in tgt_type:
                        purpose = "API management"
                    elif tgt_type in ("external", "") or target_domain:
                        purpose = "external API / internet egress"
                    elif conn_type in ("reads_from", "reads"):
                        purpose = "read"
                    elif conn_type in ("writes_to", "writes", "publishes_to"):
                        purpose = "write"
                    elif conn_type in ("connects_to", "depends_on", "calls"):
                        purpose = "outbound call"

                entry["connection_purpose"] = purpose or conn_type or "outbound"
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
        has_finding_context = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='finding_context' LIMIT 1"
        ).fetchone()
        if has_finding_context:
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
        
        # D3: Detect actual internet egress via topology inference.
        # Note: resource_properties only stores terraform_block=resource for most types;
        # attribute-level properties (cidr_blocks, gateway_id) are not extracted.
        # Instead we use topology heuristics based on resource types present.
        internet_egress: list[dict] = []
        try:
            # Heuristic 1: aws_route alongside aws_internet_gateway in same VPC = internet egress.
            # Look for aws_route resources connected to (or near) an aws_internet_gateway.
            igw_routes = conn.execute(
                """
                SELECT DISTINCT
                    route_r.resource_name AS route_name,
                    route_r.resource_type,
                    route_r.provider,
                    route_r.source_file,
                    igw.resource_name AS igw_name,
                    subnet_r.resource_name AS subnet_name
                FROM resources route_r
                JOIN repositories repo ON route_r.repo_id = repo.id
                -- Find IGW in same repo+experiment
                JOIN resources igw ON igw.repo_id = route_r.repo_id
                    AND igw.resource_type = 'aws_internet_gateway'
                -- Optionally join to subnet via route_table → subnet connection
                LEFT JOIN resource_connections rc_rtb ON rc_rtb.source_resource_id = route_r.id
                LEFT JOIN resource_connections rc_sub ON rc_sub.source_resource_id = rc_rtb.target_resource_id
                LEFT JOIN resources subnet_r ON subnet_r.id = rc_sub.target_resource_id
                    AND subnet_r.resource_type = 'aws_subnet'
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND route_r.resource_type = 'aws_route'
                ORDER BY route_r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in igw_routes:
                rd = dict(row)
                internet_egress.append({
                    'source_name': rd.get('subnet_name') or rd['route_name'],
                    'source_type': rd['resource_type'],
                    'target_name': f"Internet via {rd['igw_name']} (0.0.0.0/0)",
                    'target_type': 'internet',
                    'protocol': 'Any',
                    'port': '*',
                    'is_encrypted': None,
                    'connection_purpose': f"Internet egress — aws_route {rd['route_name']} routes via Internet Gateway {rd['igw_name']}",
                    'provider': rd['provider'],
                    'source_file': rd['source_file'],
                })
        except Exception as e:
            print(f"Warning: Could not query IGW routes for internet egress: {e}")

        try:
            # Heuristic 2: Security group / firewall rule resources named 'egress' or 'allow_all'.
            # Resource name strongly indicates open egress (e.g. terragoat's 'egress' sg rule,
            # GCP's 'allow_all' firewall).
            sg_egress_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.resource_name, r.resource_type, r.provider, r.source_file,
                    parent.resource_name AS sg_name
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type IN ('aws_security_group_rule', 'azurerm_network_security_rule',
                                          'google_compute_firewall', 'alicloud_security_group_rule')
                  AND (
                    LOWER(r.resource_name) LIKE '%egress%'
                    OR LOWER(r.resource_name) LIKE '%allow_all%'
                    OR LOWER(r.resource_name) LIKE '%allow-all%'
                    OR LOWER(r.resource_name) LIKE '%outbound%'
                  )
                ORDER BY r.provider, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in sg_egress_rows:
                rd = dict(row)
                internet_egress.append({
                    'source_name': rd.get('sg_name') or rd['resource_name'],
                    'source_type': rd['resource_type'],
                    'target_name': 'Internet (0.0.0.0/0)',
                    'target_type': 'internet',
                    'protocol': 'Any',
                    'port': '*',
                    'is_encrypted': None,
                    'connection_purpose': f"Security group rule '{rd['resource_name']}' permits outbound internet traffic",
                    'provider': rd['provider'],
                    'source_file': rd['source_file'],
                })
        except Exception as e:
            print(f"Warning: Could not query SG egress rules: {e}")

        return _db_render("tab_egress.html", egress_connections=egress_connections,
                          internet_egress=internet_egress)
    except Exception as exc:
        return _db_render("tab_egress.html", egress_connections=[], internet_egress=[], error=str(exc))
    finally:
        conn.close()


@app.route("/api/view/traffic/<experiment_id>/<repo_name>")
def api_view_traffic(experiment_id: str, repo_name: str):
    """Render the combined traffic tab with ingress and egress sections."""
    conn = _get_db()
    if conn is None:
        return _db_render("tab_traffic.html", network_ingress=[], operations=[], egress_connections=[], internet_egress=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_traffic.html", network_ingress=[], operations=[], egress_connections=[], internet_egress=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ INGRESS DATA ━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Primary: exposure_analysis table (entry points and internet-facing resources)
        network_ingress = []
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
                    r.source_line_start
                FROM exposure_analysis ea
                JOIN resources r ON ea.resource_id = r.id
                JOIN repositories repo ON r.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND (ea.is_entry_point = 1 OR ea.has_internet_path = 1)
                ORDER BY r.resource_type, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            network_ingress = [dict(row) for row in ea_rows]
        except Exception as e:
            print(f"Warning: Could not fetch network_ingress from exposure_analysis: {e}")
        
        # Load API operations for ingress
        operations = []
        operations_by_id = {}
        children_by_parent = {}
        operation_count = 0
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
                    ) as internet_access_signals,
                    COALESCE(
                        (SELECT property_value FROM resource_properties 
                         WHERE resource_id = r.id AND property_key = 'protocol' LIMIT 1),
                        'HTTPS'
                    ) as protocol
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
            
            # Build parent-child relationships
            for op in operations:
                parent_id = op.get('parent_resource_id')
                if parent_id and parent_id in operations_by_id:
                    if parent_id not in children_by_parent:
                        children_by_parent[parent_id] = []
                    children_by_parent[parent_id].append(op['id'])
            
            operation_count = sum(
                len([c for c in operations if c.get('resource_type') == 'azurerm_api_management_api_operation'])
                for _ in [None]
            )
        except Exception as e:
            print(f"Warning: Could not fetch operations for traffic: {e}")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ EGRESS DATA ━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Build outbound connections
        egress_connections = []
        try:
            rc_rows = conn.execute(
                """
                SELECT DISTINCT 
                    COALESCE(src.resource_name, 'Unknown source') AS source_name,
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
                JOIN resources src ON rc.source_resource_id = src.id
                LEFT JOIN resources tgt ON rc.target_resource_id = tgt.id
                JOIN repositories repo_src ON src.repo_id = repo_src.id
                LEFT JOIN repositories repo_tgt ON tgt.repo_id = repo_tgt.id
                WHERE LOWER(repo_src.repo_name) = LOWER(?)
                  AND repo_src.experiment_id = ?
                  AND COALESCE(rc.connection_type, '') NOT IN (
                    'contains', 'orchestrates', 'composed_of', 'parent_child',
                    'routes_ingress_to', 'restricts_access'
                  )
                  AND (rc.inferred_internet IS NULL OR rc.inferred_internet = 0)
                ORDER BY source_name, target_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in rc_rows:
                entry = dict(row)
                purpose = ""
                tgt_type = (entry.get("target_type") or "").lower()
                conn_type = (entry.get("connection_type") or "").lower()
                target_domain = (entry.get("target_domain") or "").lower()

                # Check connection_metadata for service catalogue info
                try:
                    meta = json.loads(entry.get("connection_metadata") or "{}")
                    if meta.get("category"):
                        cat = meta["category"]
                        purpose = {
                            "alerting":       "alerting / incident management",
                            "observability":  "observability / APM",
                            "logging":        "log aggregation",
                            "payment":        "payment processing",
                            "email":          "email delivery",
                            "sms":            "SMS / communication",
                            "messaging":      "messaging",
                            "auth":           "external auth / identity",
                            "vcs":            "version control API",
                            "issue_tracking": "issue tracking",
                            "data_warehouse": "data warehouse",
                        }.get(cat, cat)
                        if meta.get("service"):
                            entry["target_name"] = entry.get("target_name") or meta["service"]
                except Exception:
                    pass

                if not purpose:
                    # Classify by target resource type
                    if "topic" in tgt_type or "queue" in tgt_type or "servicebus" in tgt_type:
                        purpose = "messaging"
                    elif "database" in tgt_type or "sql" in tgt_type or "cosmos" in tgt_type or "redis" in tgt_type:
                        purpose = "data storage"
                    elif "storage" in tgt_type or "blob" in tgt_type:
                        purpose = "blob storage"
                    elif "vault" in tgt_type or "keyvault" in tgt_type:
                        purpose = "secrets"
                    elif "insight" in tgt_type or "monitor" in tgt_type or "telemetry" in tgt_type:
                        purpose = "telemetry"
                    elif "apim" in tgt_type or "api_management" in tgt_type or "api management" in tgt_type:
                        purpose = "API management"
                    elif tgt_type in ("external", "") or target_domain:
                        purpose = "external API / internet egress"
                    elif conn_type in ("reads_from", "reads"):
                        purpose = "read"
                    elif conn_type in ("writes_to", "writes", "publishes_to"):
                        purpose = "write"
                    elif conn_type in ("connects_to", "depends_on", "calls"):
                        purpose = "outbound call"

                entry["connection_purpose"] = purpose or conn_type or "outbound"
                egress_connections.append(entry)
        except Exception as e:
            print(f"Warning: Could not fetch egress_connections: {e}")

        # Exposure analysis: include data-legged destinations
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

        # Detect actual internet egress via topology inference
        internet_egress = []
        try:
            # Heuristic 1: aws_route alongside aws_internet_gateway in same VPC = internet egress
            igw_routes = conn.execute(
                """
                SELECT DISTINCT
                    route_r.resource_name AS route_name,
                    route_r.resource_type,
                    route_r.provider,
                    route_r.source_file,
                    igw.resource_name AS igw_name,
                    subnet_r.resource_name AS subnet_name
                FROM resources route_r
                JOIN repositories repo ON route_r.repo_id = repo.id
                JOIN resources igw ON igw.repo_id = route_r.repo_id
                    AND igw.resource_type = 'aws_internet_gateway'
                LEFT JOIN resource_connections rc_rtb ON rc_rtb.source_resource_id = route_r.id
                LEFT JOIN resource_connections rc_sub ON rc_sub.source_resource_id = rc_rtb.target_resource_id
                LEFT JOIN resources subnet_r ON subnet_r.id = rc_sub.target_resource_id
                    AND subnet_r.resource_type = 'aws_subnet'
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND route_r.resource_type = 'aws_route'
                ORDER BY route_r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in igw_routes:
                rd = dict(row)
                internet_egress.append({
                    'source_name': rd.get('subnet_name') or rd['route_name'],
                    'source_type': rd['resource_type'],
                    'target_name': f"Internet via {rd['igw_name']} (0.0.0.0/0)",
                    'target_type': 'internet',
                    'protocol': 'Any',
                    'port': '*',
                    'is_encrypted': None,
                    'connection_purpose': f"Internet egress — aws_route {rd['route_name']} routes via Internet Gateway {rd['igw_name']}",
                    'provider': rd['provider'],
                    'source_file': rd['source_file'],
                })
        except Exception as e:
            print(f"Warning: Could not query IGW routes for internet egress: {e}")

        try:
            # Heuristic 2: Security group / firewall rule resources named 'egress' or 'allow_all'
            sg_egress_rows = conn.execute(
                """
                SELECT DISTINCT
                    r.resource_name, r.resource_type, r.provider, r.source_file,
                    parent.resource_name AS sg_name
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type IN ('aws_security_group_rule', 'azurerm_network_security_rule',
                                          'google_compute_firewall', 'alicloud_security_group_rule')
                  AND (
                    LOWER(r.resource_name) LIKE '%egress%'
                    OR LOWER(r.resource_name) LIKE '%allow_all%'
                    OR LOWER(r.resource_name) LIKE '%allow-all%'
                    OR LOWER(r.resource_name) LIKE '%outbound%'
                  )
                ORDER BY r.provider, r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in sg_egress_rows:
                rd = dict(row)
                internet_egress.append({
                    'source_name': rd.get('sg_name') or rd['resource_name'],
                    'source_type': rd['resource_type'],
                    'target_name': 'Internet (0.0.0.0/0)',
                    'target_type': 'internet',
                    'protocol': 'Any',
                    'port': '*',
                    'is_encrypted': None,
                    'connection_purpose': f"Security group rule '{rd['resource_name']}' permits outbound internet traffic",
                    'provider': rd['provider'],
                    'source_file': rd['source_file'],
                })
        except Exception as e:
            print(f"Warning: Could not query SG egress rules: {e}")

        return _db_render(
            "tab_traffic.html",
            network_ingress=network_ingress,
            operations=operations,
            operations_by_id=operations_by_id,
            children_by_parent=children_by_parent,
            operation_count=operation_count,
            egress_connections=egress_connections,
            internet_egress=internet_egress,
        )
    except Exception as exc:
        return _db_render(
            "tab_traffic.html",
            network_ingress=[],
            operations=[],
            operations_by_id={},
            children_by_parent={},
            operation_count=0,
            egress_connections=[],
            internet_egress=[],
            error=str(exc),
        )
    finally:
        conn.close()


@app.route("/api/view/roles/<experiment_id>/<repo_name>")
def api_view_roles(experiment_id: str, repo_name: str):
    """Render the roles & permissions tab HTML."""
    def _normalize_role_name(value: object) -> str:
        return str(value or "").strip().lower()

    def _is_broad_role(value: object) -> bool:
        return _normalize_role_name(value) in {"owner", "contributor", "user access administrator"}

    def _is_broad_scope(value: object) -> bool:
        scope_text = str(value or "").strip().lower()
        return any(token in scope_text for token in ("/subscriptions/", "/resourcegroups/", "resource group", "subscription"))

    def _is_compute_like(resource_type: object) -> bool:
        rt = str(resource_type or "").strip().lower()
        return any(token in rt for token in (
            "virtual_machine", "linux_virtual_machine", "windows_virtual_machine",
            "app_service", "function_app", "container_app", "web_app", "compute",
        ))

    def _normalize_permission_lines(raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        text = str(raw_value).strip()
        if not text:
            return []

        parsed = None
        if (text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}")):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None

        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
        if isinstance(parsed, dict):
            out: list[str] = []
            for k, v in parsed.items():
                if isinstance(v, list):
                    joined = ", ".join(str(x).strip() for x in v if str(x).strip())
                    if joined:
                        out.append(f"{k}: {joined}")
                elif str(v).strip():
                    out.append(f"{k}: {v}")
            return out

        if "\n" in text:
            return [line.strip() for line in text.splitlines() if line.strip()]
        if ";" in text:
            return [part.strip() for part in text.split(";") if part.strip()]
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]

    conn = _get_db()
    if conn is None:
        return _db_render("tab_roles.html", roles=[], error="DB unavailable")
    try:
        resolved_exp_id = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if not resolved_exp_id:
            return _db_render("tab_roles.html", roles=[], error=f"No scan found for {repo_name}.")
        target_exp = resolved_exp_id
        principal_rows = conn.execute(
            """
            SELECT
                r.id,
                r.resource_name,
                r.resource_type,
                MAX(CASE WHEN LOWER(rp.property_key) = 'principal_id' THEN rp.property_value END) AS principal_id,
                MAX(CASE WHEN LOWER(rp.property_key) = 'client_id' THEN rp.property_value END) AS client_id
            FROM resources r
            JOIN repositories repo ON r.repo_id = repo.id
            LEFT JOIN resource_properties rp ON rp.resource_id = r.id
            WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
            GROUP BY r.id, r.resource_name, r.resource_type
            """,
            (repo_name, target_exp),
        ).fetchall()
        principal_lookup: dict[str, dict] = {}
        for row in principal_rows:
            row_dict = dict(row)
            for key in ("principal_id", "client_id"):
                token = (row_dict.get(key) or "").strip()
                if token and token not in principal_lookup:
                    principal_lookup[token] = row_dict

        identity_consumers: dict[str, list[dict]] = {}
        try:
            auth_rows = conn.execute(
                """
                SELECT
                    src.resource_name AS consumer_name,
                    src.resource_type AS consumer_type,
                    tgt.resource_name AS identity_name,
                    tgt.resource_type AS identity_type
                FROM resource_connections rc
                JOIN resources src ON rc.source_resource_id = src.id
                JOIN resources tgt ON rc.target_resource_id = tgt.id
                JOIN repositories repo ON src.repo_id = repo.id
                WHERE LOWER(repo.repo_name) = LOWER(?)
                  AND repo.experiment_id = ?
                  AND LOWER(COALESCE(rc.connection_type, '')) = 'authenticates_via'
                """,
                (repo_name, target_exp),
            ).fetchall()
            for row in auth_rows:
                row_dict = dict(row)
                key = (row_dict.get("identity_name") or "").strip().lower()
                if key:
                    identity_consumers.setdefault(key, []).append(row_dict)
        except Exception:
            identity_consumers = {}

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
                MAX(CASE WHEN LOWER(rp.property_key) IN ('role_name','role_definition_name','role_definition_id','role') THEN rp.property_value END) AS role_name,
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
            resource_id = entry.get("id")
            scope_candidates = [
                (entry.pop("scope_prop", None) or "").strip(),
                (entry.pop("subscription_scope", None) or "").strip(),
                (entry.pop("parent_name", None) or "").strip(),
                entry.get("resource_name", ""),
                entry.get("identity_name", ""),
            ]
            entry["resource_name"] = next((s for s in scope_candidates if s), "")

            role_props = conn.execute(
                """
                SELECT LOWER(property_key) AS property_key, property_value
                FROM resource_properties
                WHERE resource_id = ?
                """,
                (resource_id,),
            ).fetchall()
            prop_map = {p["property_key"]: (p["property_value"] or "") for p in role_props}

            role_name = (
                (entry.get("role_name") or "").strip()
                or (prop_map.get("role_definition_name") or "").strip()
                or (prop_map.get("role_definition_id") or "").strip()
                or (prop_map.get("role") or "").strip()
            )
            resolved_principal = principal_lookup.get((entry.get("principal_id") or "").strip())
            if resolved_principal:
                entry["principal_name"] = resolved_principal.get("resource_name")
                entry["principal_resource_type"] = resolved_principal.get("resource_type")
                if str(entry.get("role_type") or "").lower() == "azurerm_role_assignment":
                    entry["identity_name"] = resolved_principal.get("resource_name") or entry.get("identity_name")

            permission_details: list[str] = []
            if role_name:
                permission_details.append(f"Role: {role_name}")
            if entry.get("principal_name"):
                permission_details.append(
                    f"Principal resource: {entry['principal_name']} ({entry.get('principal_resource_type') or 'identity'})"
                )

            for label, key in (
                ("Allowed actions", "actions"),
                ("Allowed data actions", "data_actions"),
                ("Denied actions", "not_actions"),
                ("Denied data actions", "not_data_actions"),
            ):
                values = _normalize_permission_lines(prop_map.get(key))
                if values:
                    permission_details.append(f"{label}: {', '.join(values)}")

            raw_permissions = _normalize_permission_lines(entry.get("permissions") or prop_map.get("permissions"))
            if raw_permissions:
                permission_details.extend([f"Permissions: {v}" for v in raw_permissions])

            if entry.get("resource_name"):
                permission_details.append(f"Scope: {entry['resource_name']}")
            if entry.get("principal_id"):
                permission_details.append(f"Principal: {entry['principal_id']}")

            for consumer in identity_consumers.get((entry.get("identity_name") or "").strip().lower(), []):
                permission_details.append(
                    f"Used by: {consumer.get('consumer_name')} ({consumer.get('consumer_type')})"
                )

            if _is_broad_role(role_name) and _is_broad_scope(entry.get("resource_name")):
                entry["is_excessive"] = 1
                permission_details.append("⚠️ Broad role at resource-group/subscription scope")

            if _is_broad_role(role_name) and resolved_principal and _is_compute_like(resolved_principal.get("resource_type")):
                permission_details.append(
                    f"Attack path: compromise of {resolved_principal.get('resource_name')} may yield {role_name} over {entry.get('resource_name') or 'the assigned scope'}"
                )

            if _is_broad_role(role_name):
                for consumer in identity_consumers.get((entry.get("identity_name") or "").strip().lower(), []):
                    consumer_name = consumer.get("consumer_name") or "attached workload"
                    consumer_type = str(consumer.get("consumer_type") or "")
                    permission_details.append(
                        f"Attack path: anyone able to control {consumer_name} can execute with {role_name} on {entry.get('resource_name') or 'the assigned scope'}"
                    )
                    if "automation" in consumer_type.lower() or "automation" in str(consumer_name).lower():
                        permission_details.append(
                            f"⚠️ Automation chain: {consumer_name} runs as this identity; compromise or modification of that automation can inherit {role_name}"
                        )

            seen: set[str] = set()
            deduped_details: list[str] = []
            for line in permission_details:
                if not line or line in seen:
                    continue
                deduped_details.append(line)
                seen.add(line)

            entry["permission_details"] = deduped_details
            entry["permission_summary"] = deduped_details[0] if deduped_details else "—"

            if role_name:
                entry["permissions"] = role_name
            elif raw_permissions:
                entry["permissions"] = raw_permissions[0]

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
                    op_list = [o.strip() for o in str(ops).split(',') if o.strip()]
                    r_dict['permission_details'] = [f"Operation: {op}" for op in op_list]
                    r_dict['permission_summary'] = f"{len(op_list)} operation(s)" if op_list else "Access to operations"
                    r_dict['permissions'] = r_dict['permission_summary']
                else:
                    summary = f"Access to {r_dict.get('resource_name', 'API')}"
                    r_dict['permission_details'] = [summary]
                    r_dict['permission_summary'] = summary
                    r_dict['permissions'] = summary
                roles.append(r_dict)
        except Exception as e:
            print(f"Warning: Could not fetch API keys/subscriptions: {e}")
        
        # E3: Add aws_iam_access_key resources — these are static credentials and a critical concern
        try:
            access_key_rows = conn.execute(
                """
                SELECT r.id, r.resource_name AS identity_name, r.resource_type AS role_type,
                       r.provider, r.source_file, parent.resource_name AS parent_name
                FROM resources r
                JOIN repositories repo ON r.repo_id = repo.id
                LEFT JOIN resources parent ON r.parent_resource_id = parent.id
                WHERE LOWER(repo.repo_name) = LOWER(?) AND repo.experiment_id = ?
                  AND r.resource_type = 'aws_iam_access_key'
                ORDER BY r.resource_name
                """,
                (repo_name, target_exp),
            ).fetchall()
            for r in access_key_rows:
                rd = dict(r)
                iam_user = rd.get('parent_name') or rd['identity_name']
                roles.append({
                    'id': rd['id'],
                    'identity_name': rd['identity_name'],
                    'role_type': rd['role_type'],
                    'provider': rd['provider'],
                    'source_file': rd['source_file'],
                    'resource_name': iam_user,
                    'permissions': '⚠️ Static credential',
                    'permission_details': [
                        f"⚠️ Static AWS access key for IAM user: {iam_user}",
                        "Static access keys are a critical security risk — they never expire and can be leaked",
                        "Prefer IAM roles with temporary credentials (STS AssumeRole) instead",
                    ],
                    'permission_summary': '⚠️ Static AWS access key',
                    'is_excessive': 1,
                })
        except Exception as e:
            print(f"Warning: Could not fetch IAM access keys: {e}")

        # E1/E2: Flag IAM policies with names indicating broad access
        # (actual policy content not extracted to DB — note this limitation)
        _BROAD_POLICY_NAMES = ('excess_policy', 'admin', 'full_access', 'allow_all', 'root')
        for role in roles:
            role_name = (role.get('identity_name') or '').lower()
            if role.get('is_excessive') is None and any(p in role_name for p in _BROAD_POLICY_NAMES):
                role['is_excessive'] = 1
                role['permission_details'] = list(role.get('permission_details') or []) + [
                    "⚠️ Policy name suggests broad permissions — verify actions and resource scope",
                ]

        # Convert is_excessive to integer if stored as string
        for role in roles:
            val = role.get("is_excessive")
            if val is not None:
                role["is_excessive"] = 1 if str(val).lower() in ("1", "true", "yes") else 0

        automation_owner_paths: list[dict] = []
        for role in roles:
            if not (_is_broad_role(role.get("permissions")) and str(role.get("resource_name") or "").strip()):
                continue
            for consumer in identity_consumers.get((role.get("identity_name") or "").strip().lower(), []):
                consumer_name = consumer.get("consumer_name") or ""
                consumer_type = str(consumer.get("consumer_type") or "")
                if "automation" in consumer_type.lower() or "automation" in consumer_name.lower():
                    automation_owner_paths.append({
                        "consumer_name": consumer_name,
                        "scope": role.get("resource_name"),
                        "role_name": role.get("permissions"),
                        "identity_name": role.get("identity_name"),
                    })

        for role in roles:
            if not (_is_compute_like(role.get("principal_resource_type")) and _is_broad_role(role.get("permissions"))):
                continue
            details = list(role.get("permission_details") or [])
            for path in automation_owner_paths:
                if str(path.get("scope") or "").strip() != str(role.get("resource_name") or "").strip():
                    continue
                if _normalize_role_name(path.get("role_name")) != "owner":
                    continue
                details.append(
                    f"Privilege chain: this principal can manage automation resource {path['consumer_name']} in the same scope; {path['identity_name']} attached to it has Owner"
                )
            deduped: list[str] = []
            seen_details: set[str] = set()
            for line in details:
                if line and line not in seen_details:
                    deduped.append(line)
                    seen_details.add(line)
            role["permission_details"] = deduped
            if deduped:
                role["permission_summary"] = deduped[0]
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
                                     OR r.resource_type IN (
                                         'aws_ecr_repository', 'aws_ecr_registry',
                                         'google_artifact_registry_repository',
                                         'azurerm_container_registry',
                                         'azurerm_kubernetes_cluster',
                                         'google_container_cluster', 'google_container_node_pool',
                                         'aws_eks_cluster', 'aws_eks_node_group',
                                         'aws_ecs_cluster', 'aws_ecs_task_definition',
                                         'aws_ecs_service'
                                     )
                   OR EXISTS (
                       SELECT 1 FROM resource_properties rp_d
                                             WHERE rp_d.resource_id = r.id
                                                 AND rp_d.property_key IN ('dockerfile', 'image', 'registry')
                   )
              )
            GROUP BY r.id, r.resource_name, r.resource_type, r.provider, r.source_file
            ORDER BY r.provider, r.resource_name
        """, (repo_name, resolved_exp_id)).fetchall()
        
        _ORCHESTRATOR_TYPES = {
            'google_container_cluster', 'google_container_node_pool',
            'azurerm_kubernetes_cluster', 'aws_eks_cluster', 'aws_eks_node_group',
            'aws_ecs_cluster',
        }
        _REGISTRY_TYPES = {
            'aws_ecr_repository', 'aws_ecr_registry',
            'google_artifact_registry_repository', 'azurerm_container_registry',
        }
        _WORKLOAD_TYPES = {
            'aws_ecs_task_definition', 'aws_ecs_service',
        }

        containers = []
        providers = set()
        for row in rows:
            container = dict(row)
            rt = (container.get("resource_type") or "").lower()
            if rt in _ORCHESTRATOR_TYPES:
                container["container_type"] = "Orchestrator"
            elif rt in _REGISTRY_TYPES:
                container["container_type"] = "Registry"
            elif rt in _WORKLOAD_TYPES:
                container["container_type"] = "Workload"
            elif "dockerfile" in rt:
                container["container_type"] = "Dockerfile"
            else:
                container["container_type"] = "Container"
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
                    "usage_keys": set(),
                    "containers": set(),
                    "references": {},
                })
                entry["containers"].add(c.get('resource_name') or '—')

                parsed_line = None
                if line_no is not None:
                    try:
                        parsed_line = int(line_no)
                    except (TypeError, ValueError):
                        parsed_line = None
                occurrence_key = (ref, parsed_line if parsed_line is not None else img_name.lower())
                entry["usage_keys"].add(occurrence_key)

                ref_entry = entry["references"].setdefault(ref, {
                    "reference": ref,
                    "occurrence_keys": set(),
                    "containers": set(),
                    "lines": set(),
                })
                ref_entry["occurrence_keys"].add(occurrence_key[1])
                ref_entry["containers"].add(c.get('resource_name') or '—')
                if parsed_line is not None:
                    ref_entry["lines"].add(parsed_line)

        base_image_usages = []
        for img_name, entry in base_image_map.items():
            refs = []
            for ref_item in entry["references"].values():
                refs.append({
                    "reference": ref_item["reference"],
                    "count": len(ref_item["occurrence_keys"]),
                    "container_count": len(ref_item["containers"]),
                    "lines": sorted(ref_item["lines"]),
                })

            refs.sort(key=lambda r: (-r["count"], r["reference"].lower()))
            preview_limit = 8
            base_image_usages.append({
                "image": img_name,
                "usage_count": len(entry["usage_keys"]),
                "container_count": len(entry["containers"]),
                "reference_count": len(refs),
                "reference_preview": refs[:preview_limit],
                "remaining_reference_count": max(0, len(refs) - preview_limit),
            })

        base_image_usages.sort(key=lambda entry: (-entry["usage_count"], entry["image"].lower()))

        # Load AI container summaries from context_metadata
        container_summaries: dict[str, dict] = {}
        try:
            cs_row = conn.execute(
                """
                SELECT value FROM context_metadata
                WHERE experiment_id = ? AND namespace = 'ai_overview' AND key = 'ai_container_summaries'
                  AND repo_id = (SELECT id FROM repositories WHERE experiment_id = ? AND LOWER(repo_name) = LOWER(?) LIMIT 1)
                LIMIT 1
                """,
                (resolved_exp_id, resolved_exp_id, repo_name),
            ).fetchone()
            if cs_row and cs_row["value"]:
                container_summaries = json.loads(cs_row["value"])
        except Exception:
            pass

        def _match_summary(image_str: str, summaries: dict) -> dict | None:
            """Fuzzy match an image string to a summaries dict key."""
            if not image_str or not summaries:
                return None
            # Exact match first
            if image_str in summaries:
                return summaries[image_str]
            # Normalise: strip tag, strip registry prefix
            def _normalise(s: str) -> str:
                s = s.split("@")[0]          # remove digest
                s = s.rsplit(":", 1)[0]      # remove tag
                s = s.rsplit("/", 1)[-1]     # take last path component
                return s.lower()
            norm = _normalise(image_str)
            for key, val in summaries.items():
                if _normalise(key) == norm:
                    return val
            # Substring fallback
            for key, val in summaries.items():
                if norm in _normalise(key) or _normalise(key) in norm:
                    return val
            return None

        # Attach summaries to container cards
        for c in containers:
            img = c.get("image") or ""
            c["ai_summary"] = _match_summary(img, container_summaries)
            # Also try base images if no direct match
            if not c["ai_summary"]:
                for bi in (c.get("base_images") or []):
                    bi_name = bi.get("image") if isinstance(bi, dict) else str(bi)
                    match = _match_summary(bi_name, container_summaries)
                    if match:
                        c["ai_summary"] = match
                        break

        # Attach summaries to base image usage rows
        for entry in base_image_usages:
            entry["ai_summary"] = _match_summary(entry["image"], container_summaries)

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



# ── Export Routes ─────────────────────────────────────────────────────────────

@app.route("/api/export/csv/<experiment_id>/<repo_name>/<section>")
def api_export_csv(experiment_id: str, repo_name: str, section: str):
    """Export a section's data as a CSV file.

    Sections: assets, findings, ingress, egress, roles, ports, containers, risks
    Filename: {repo_name}_scan{experiment_id}_{section}.csv
    """
    import csv
    import io

    conn = _get_db()
    if conn is None:
        return jsonify({"error": "DB unavailable"}), 503

    section = section.lower().strip()
    safe_repo = re.sub(r"[^\w\-]", "_", repo_name)
    safe_exp  = re.sub(r"[^\w\-]", "_", experiment_id)
    filename  = f"{safe_repo}_scan{safe_exp}_{section}.csv"

    # Resolve repo_id
    try:
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE LOWER(repo_name) = LOWER(?) AND experiment_id = ?",
            (repo_name, experiment_id),
        ).fetchone()
        repo_id = repo_row["id"] if repo_row else None
    except Exception:
        repo_id = None

    _SECTION_QUERIES: dict[str, tuple[str, list]] = {
        "assets": (
            """
            SELECT
              r.resource_name        AS "Resource Name",
              r.resource_type        AS "Resource Type",
              r.provider             AS "Provider",
              r.region               AS "Region",
              r.source_file          AS "Source File",
              r.source_line_start    AS "Line",
              r.discovered_by        AS "Discovered By",
              COALESCE(f.finding_count, 0) AS "Finding Count",
              COALESCE(f.worst_severity, '') AS "Worst Severity"
            FROM resources r
            LEFT JOIN (
              SELECT resource_id,
                     COUNT(*) AS finding_count,
                     MAX(base_severity) AS worst_severity
              FROM findings
              WHERE experiment_id = ?
              GROUP BY resource_id
            ) f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
              AND (? IS NULL OR r.repo_id = ?)
            ORDER BY r.provider, r.resource_type, r.resource_name
            """,
            [experiment_id, experiment_id, repo_id, repo_id],
        ),
        "findings": (
            """
            SELECT
              f.rule_id              AS "Rule ID",
              f.title                AS "Title",
              f.base_severity        AS "Severity",
              f.severity_score       AS "Score",
              f.category             AS "Category",
              r.resource_name        AS "Resource",
              r.resource_type        AS "Resource Type",
              r.provider             AS "Provider",
              f.source_file          AS "Source File",
              f.source_line_start    AS "Line",
              f.triage_status        AS "Triage Status",
              f.description          AS "Description",
              f.reason               AS "Reason"
            FROM findings f
            LEFT JOIN resources r ON f.resource_id = r.id
            WHERE f.experiment_id = ?
              AND (? IS NULL OR f.repo_id = ?)
            ORDER BY f.severity_score DESC, f.base_severity, f.title
            """,
            [experiment_id, repo_id, repo_id],
        ),
        "ingress": (
            """
            SELECT
              r_tgt.resource_name    AS "Target Resource",
              r_tgt.resource_type    AS "Target Type",
              r_tgt.provider         AS "Provider",
              rc.connection_type     AS "Connection Type",
              rc.protocol            AS "Protocol",
              rc.port                AS "Port",
              rc.auth_method         AS "Auth Method",
              CASE rc.is_encrypted WHEN 1 THEN 'Yes' WHEN 0 THEN 'No' ELSE '' END AS "Encrypted",
              rc.via_component       AS "Via Component",
              rc.notes               AS "Notes"
            FROM resource_connections rc
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
              AND rc.connection_type = 'internet_to'
              AND (? IS NULL OR r_tgt.repo_id = ?)
            ORDER BY r_tgt.provider, r_tgt.resource_type, r_tgt.resource_name
            """,
            [experiment_id, repo_id, repo_id],
        ),
        "egress": (
            """
            SELECT
              r_src.resource_name    AS "Source Resource",
              r_src.resource_type    AS "Source Type",
              r_src.provider         AS "Provider",
              r_tgt.resource_name    AS "Target Resource",
              r_tgt.resource_type    AS "Target Type",
              rc.connection_type     AS "Connection Type",
              rc.protocol            AS "Protocol",
              rc.port                AS "Port",
              rc.auth_method         AS "Auth Method",
              CASE rc.is_encrypted WHEN 1 THEN 'Yes' WHEN 0 THEN 'No' ELSE '' END AS "Encrypted",
              rc.notes               AS "Notes"
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
              AND rc.connection_type IN ('data_access', 'depends_on')
              AND (? IS NULL OR r_src.repo_id = ?)
            ORDER BY r_src.provider, r_src.resource_name
            """,
            [experiment_id, repo_id, repo_id],
        ),
        "ports": (
            """
            SELECT
              r_src.resource_name    AS "Source Resource",
              r_src.resource_type    AS "Source Type",
              r_tgt.resource_name    AS "Target Resource",
              r_tgt.resource_type    AS "Target Type",
              r_src.provider         AS "Provider",
              rc.protocol            AS "Protocol",
              rc.port                AS "Port",
              rc.auth_method         AS "Auth Method",
              CASE rc.is_encrypted WHEN 1 THEN 'Yes' WHEN 0 THEN 'No' ELSE '' END AS "Encrypted",
              rc.notes               AS "Notes"
            FROM resource_connections rc
            JOIN resources r_src ON rc.source_resource_id = r_src.id
            JOIN resources r_tgt ON rc.target_resource_id = r_tgt.id
            WHERE rc.experiment_id = ?
              AND rc.port IS NOT NULL AND rc.port != ''
              AND (? IS NULL OR r_src.repo_id = ?)
            ORDER BY rc.port, r_src.provider, r_src.resource_name
            """,
            [experiment_id, repo_id, repo_id],
        ),
        "roles": (
            """
            SELECT
              r.resource_name        AS "Resource Name",
              r.resource_type        AS "Resource Type",
              r.provider             AS "Provider",
              r.region               AS "Region",
              r.source_file          AS "Source File",
              r.source_line_start    AS "Line",
              COALESCE(f.finding_count, 0) AS "Finding Count",
              COALESCE(f.worst_severity, '') AS "Worst Severity"
            FROM resources r
            LEFT JOIN (
              SELECT resource_id, COUNT(*) AS finding_count, MAX(base_severity) AS worst_severity
              FROM findings WHERE experiment_id = ? GROUP BY resource_id
            ) f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
              AND (? IS NULL OR r.repo_id = ?)
              AND (
                r.resource_type LIKE '%iam%'
                OR r.resource_type LIKE '%role%'
                OR r.resource_type LIKE '%policy%'
                OR r.resource_type LIKE '%identity%'
                OR r.resource_type LIKE '%rbac%'
                OR r.resource_type LIKE '%permission%'
                OR r.resource_type LIKE '%access_key%'
                OR r.resource_type LIKE '%managed_identity%'
                OR r.resource_type LIKE '%service_account%'
              )
            ORDER BY r.provider, r.resource_type, r.resource_name
            """,
            [experiment_id, experiment_id, repo_id, repo_id],
        ),
        "containers": (
            """
            SELECT
              r.resource_name        AS "Resource Name",
              r.resource_type        AS "Resource Type",
              r.provider             AS "Provider",
              r.region               AS "Region",
              r.source_file          AS "Source File",
              r.source_line_start    AS "Line",
              COALESCE(f.finding_count, 0) AS "Finding Count",
              COALESCE(f.worst_severity, '') AS "Worst Severity"
            FROM resources r
            LEFT JOIN (
              SELECT resource_id, COUNT(*) AS finding_count, MAX(base_severity) AS worst_severity
              FROM findings WHERE experiment_id = ? GROUP BY resource_id
            ) f ON r.id = f.resource_id
            WHERE r.experiment_id = ?
              AND (? IS NULL OR r.repo_id = ?)
              AND (
                r.resource_type LIKE '%container%'
                OR r.resource_type LIKE '%kubernetes%'
                OR r.resource_type LIKE '%docker%'
                OR r.resource_type LIKE '%aks%'
                OR r.resource_type LIKE '%eks%'
                OR r.resource_type LIKE '%gke%'
                OR r.resource_type LIKE '%oci_containerengine%'
                OR r.resource_type LIKE '%cs_managed_kubernetes%'
                OR r.resource_type LIKE '%deployment%'
                OR r.resource_type LIKE '%pod%'
                OR r.resource_type LIKE '%ingress%'
              )
            ORDER BY r.provider, r.resource_type, r.resource_name
            """,
            [experiment_id, experiment_id, repo_id, repo_id],
        ),
        "risks": (
            """
            SELECT
              f.base_severity        AS "Severity",
              f.severity_score       AS "Score",
              f.rule_id              AS "Rule ID",
              f.title                AS "Title",
              f.category             AS "Category",
              r.resource_name        AS "Resource",
              r.resource_type        AS "Resource Type",
              r.provider             AS "Provider",
              f.source_file          AS "Source File",
              f.source_line_start    AS "Line",
              f.triage_status        AS "Triage Status",
              f.description          AS "Description",
              f.proposed_fix         AS "Proposed Fix"
            FROM findings f
            LEFT JOIN resources r ON f.resource_id = r.id
            WHERE f.experiment_id = ?
              AND (? IS NULL OR f.repo_id = ?)
            ORDER BY f.severity_score DESC, f.base_severity, f.title
            """,
            [experiment_id, repo_id, repo_id],
        ),
    }

    if section not in _SECTION_QUERIES:
        conn.close()
        return jsonify({"error": f"Unknown section '{section}'. Valid: {', '.join(_SECTION_QUERIES)}"}), 400

    sql_query, params = _SECTION_QUERIES[section]
    try:
        rows = conn.execute(sql_query, params).fetchall()
    except Exception as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    # Build CSV in memory
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    else:
        output.write("(no data)\n")

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility

    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(csv_bytes)),
        },
    )


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
            "Transfer-Encoding": "chunked",
        },
    )


if __name__ == "__main__":
    # Clean up stale lock files from previous server crashes/restarts
    print("[Startup] Cleaning up stale lock files...", file=sys.stderr)
    _cleanup_stale_locks()
    
    debug_env = os.getenv("TRIAGE_DEBUG", "0").lower()
    debug = debug_env in ("1", "true", "yes", "on")
    app.run(debug=debug, host="0.0.0.0", port=9000, threaded=True)
@app.route("/api/analysis/stop/<experiment_id>/<repo_name>", methods=["POST"])
def api_analysis_stop(experiment_id: str, repo_name: str):
    """Stop a running Copilot/AI job for this experiment+repo."""
    conn = _get_db()
    resolved_id = experiment_id
    if conn is None:
        return jsonify({"error": "DB unavailable"}), 503
    try:
        resolved = _get_experiment_for_repo(conn, repo_name, experiment_id)
        if resolved:
            resolved_id = resolved
    finally:
        conn.close()

    key = _ai_job_key(resolved_id, repo_name)
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job or job.get("status") != "running":
            return jsonify({"status": "idle"}), 200
        proc = job.get("process")
        job["status"] = "failed"
        job["error"] = "stopped by user"
        job["completed_at"] = time.time()
        job.pop("process", None)
        _AI_ANALYSIS_JOBS[key] = job

    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    return jsonify({"status": "stopped"})
