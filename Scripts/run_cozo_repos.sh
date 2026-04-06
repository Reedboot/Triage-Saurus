#!/usr/bin/env bash
set -euo pipefail

# --- Colours ------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
ORANGE='\033[38;5;208m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_PARENT="$(cd "$REPO_ROOT/.." && pwd)"
REPOS_FILE="$REPO_ROOT/Intake/ReposToScan.txt"
RULES_DIR="$REPO_ROOT/Rules"
OUTPUT_DIR="$REPO_ROOT/Output/Data/opengrep"
PYTHON_SCRIPT="$REPO_ROOT/Scripts/Scan/store_opengrep_for_cozo.py"
COZO_DB_PATH="$REPO_ROOT/Output/Data/cozo.db"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
ANALYTICS_DB_DIR="$REPO_ROOT/Output/Data"
ANALYTICS_DB="$COZO_DB_PATH"
AUDIT_LOG="$REPO_ROOT/Output/Audit/CozoScan_$(date +%Y-%m-%d_%H%M%S).md"
ONE_HOUR=3600
FORCE_SCAN=false

# Parse args: support --force and --repo <name>
REPO_OVERRIDE=""
CLEANUP_TMP=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE_SCAN=true
      shift
      ;;
    --repo)
      REPO_OVERRIDE="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [ -n "$REPO_OVERRIDE" ]; then
  TMP_REPOS=$(mktemp)
  echo "$REPO_OVERRIDE" > "$TMP_REPOS"
  REPOS_FILE="$TMP_REPOS"
  CLEANUP_TMP=true
  # ensure temporary file is removed on exit
  trap 'if [ "$CLEANUP_TMP" = true ]; then rm -f "$TMP_REPOS"; fi' EXIT
fi

if [ ! -f "$REPOS_FILE" ]; then
  echo "Repos file not found: $REPOS_FILE" >&2
  exit 1
fi

if ! command -v opengrep >/dev/null 2>&1; then
  echo "opengrep is not installed or not on PATH" >&2
  exit 1
fi

_venv_setup_box() {
  local venv_path="$1"
  local req_path="$2"
  echo -e "${RED}${BOLD}" >&2
  echo -e "  ╔══════════════════════════════════════════════════════════════╗" >&2
  echo -e "  ║  ❌  Python virtual environment not found                   ║" >&2
  echo -e "  ╚══════════════════════════════════════════════════════════════╝${RESET}" >&2
  echo -e "" >&2
  echo -e "  ${BOLD}Triage-Saurus requires a .venv at:${RESET}" >&2
  echo -e "    ${CYAN}$venv_path${RESET}" >&2
  echo -e "" >&2
  echo -e "  ${BOLD}Set it up with these three commands:${RESET}" >&2
  echo -e "" >&2
  echo -e "    ${GREEN}python3 -m venv $venv_path${RESET}" >&2
  echo -e "    ${GREEN}source $venv_path/bin/activate${RESET}" >&2
  echo -e "    ${GREEN}pip install -r $req_path${RESET}" >&2
  echo -e "" >&2
  echo -e "  Then re-run:  ${BOLD}bash Scripts/run_cozo_repos.sh${RESET}" >&2
  echo -e "" >&2
}

if [ ! -d "$REPO_ROOT/.venv" ]; then
  _venv_setup_box "$REPO_ROOT/.venv" "$REPO_ROOT/requirements.txt"
  exit 1
fi

EXPECTED_VENV="$REPO_ROOT/.venv"
if [ -z "${VIRTUAL_ENV:-}" ] || [ "$VIRTUAL_ENV" != "$EXPECTED_VENV" ]; then
  echo -e "${YELLOW}⚡ .venv not activated — activating automatically${RESET}"
  # shellcheck disable=SC1091
  if ! source "$EXPECTED_VENV/bin/activate" 2>/dev/null; then
    echo -e "${RED}❌ Failed to activate $EXPECTED_VENV${RESET}" >&2
    _venv_setup_box "$EXPECTED_VENV" "$REPO_ROOT/requirements.txt"
    exit 1
  fi
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo -e "${RED}❌ Python binary not found at $PYTHON_BIN${RESET}" >&2
  echo -e "${YELLOW}   The .venv may be corrupt — try recreating it:${RESET}" >&2
  echo -e "    ${GREEN}rm -rf $REPO_ROOT/.venv${RESET}" >&2
  echo -e "    ${GREEN}python3 -m venv $REPO_ROOT/.venv && pip install -r $REPO_ROOT/requirements.txt${RESET}" >&2
  exit 1
fi

# --- Requirements check -------------------------------------------------------
check_requirements() {
  local missing=()
  while IFS= read -r line; do
    line=$(echo "$line" | sed 's/#.*//' | tr -d ' \t')
    [ -z "$line" ] && continue
    pkg=$(echo "$line" | sed 's/[><=!~].*//')
    [ -z "$pkg" ] && continue
    if ! "$PYTHON_BIN" -m pip show "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done < "$REPO_ROOT/requirements.txt"
  if [ "${#missing[@]}" -gt 0 ]; then
    echo -e "${ORANGE}⚠️  Missing packages: ${missing[*]}${RESET}" >&2
    echo -e "" >&2
    echo -e "  ${BOLD}Install them with:${RESET}" >&2
    echo -e "    ${GREEN}source $REPO_ROOT/.venv/bin/activate${RESET}" >&2
    echo -e "    ${GREEN}pip install -r $REPO_ROOT/requirements.txt${RESET}" >&2
    return 1
  fi
}

echo -e "${CYAN}⚙️  Checking requirements...${RESET}"
if ! check_requirements; then
  exit 1
fi
echo -e "${GREEN}✅ All requirements satisfied${RESET}"

mkdir -p "$OUTPUT_DIR"
mkdir -p "$(dirname "$AUDIT_LOG")"
mkdir -p "$ANALYTICS_DB_DIR"

log()      { echo -e "${DIM}[$(date '+%H:%M:%S')]${RESET} $*"; }
log_ok()   { echo -e "${DIM}[$(date '+%H:%M:%S')]${RESET} ${GREEN}$*${RESET}"; }
log_warn() { echo -e "${DIM}[$(date '+%H:%M:%S')]${RESET} ${YELLOW}$*${RESET}"; }
log_err()  { echo -e "${DIM}[$(date '+%H:%M:%S')]${RESET} ${RED}$*${RESET}"; }
log_step() { echo -e "${DIM}[$(date '+%H:%M:%S')]${RESET} ${CYAN}$*${RESET}"; }

log "Ensuring Cozo DB exists at ${CYAN}$COZO_DB_PATH${RESET}"
if [ ! -f "$COZO_DB_PATH" ]; then
  log "Cozo DB not found — initializing learning schema at $COZO_DB_PATH"
  if ! "$PYTHON_BIN" "$REPO_ROOT/Scripts/Utils/init_cozo_learning.py" init "$COZO_DB_PATH"; then
    log_err "  ❌ Failed to initialize Cozo DB at $COZO_DB_PATH"
    exit 1
  fi
  log_ok "  ✅ Initialized Cozo DB at $COZO_DB_PATH"
else
  log "Cozo DB already present at $COZO_DB_PATH — ensuring learning schema"
  # Try to ensure relations/schema but don't fail the entire run if it errors
  if ! "$PYTHON_BIN" "$REPO_ROOT/Scripts/Utils/init_cozo_learning.py" init "$COZO_DB_PATH" >/dev/null 2>&1; then
    log_warn "  ⚠️  Warning: failed to ensure learning schema (non-fatal)"
  fi
fi

should_skip_repo() {
  local repo="$1"

  if [ "$FORCE_SCAN" = true ]; then
    return 1
  fi

  if [ ! -f "$COZO_DB_PATH" ]; then
    return 1
  fi

  "$PYTHON_BIN" - "$COZO_DB_PATH" "$repo" "$ONE_HOUR" <<'PY' 2>/dev/null
import sys, sqlite3, time, datetime
db, repo, max_age = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    conn = sqlite3.connect(db, timeout=5)
    row = conn.execute(
        "SELECT scan_time FROM repo_scans WHERE repo_name = ? ORDER BY scan_time DESC LIMIT 1",
        (repo,),
    ).fetchone()
    conn.close()
    if not row:
        sys.exit(1)
    age = int(time.time()) - int(datetime.datetime.fromisoformat(row[0]).timestamp())
    sys.exit(0 if age < max_age else 1)
except Exception:
    sys.exit(1)
PY
}

REPO_LIST_COUNT=$(grep -cEv '^[[:space:]]*(#|$)' "$REPOS_FILE" || true)

cat > "$AUDIT_LOG" <<EOF
# 🟣 Cozo Scan Audit Log — $(date '+%Y-%m-%d %H:%M:%S')

**Repo list:** $REPOS_FILE
**Entries:** $REPO_LIST_COUNT
**Cozo DB:** $COZO_DB_PATH
**Force scans:** $FORCE_SCAN

EOF

log "${BOLD}${CYAN}🦖 Starting Cozo repo scan${RESET}"
log "- Repos: ${BOLD}$REPO_LIST_COUNT${RESET} entries from $REPOS_FILE"
log "- Cozo DB: ${CYAN}$COZO_DB_PATH${RESET}"
log "- Force mode: $FORCE_SCAN"
log "- Audit log: ${DIM}$AUDIT_LOG${RESET}"
log ""

TOTAL=0
SUCCESS=0
FAILED=0
SKIPPED_NOTFOUND=0
SKIPPED_RECENT=0
START_TIME=$(date +%s)

while IFS= read -r line || [ -n "$line" ]; do
  entry="${line%%#*}"
  repo_name="$(printf "%s" "$entry" | awk '{gsub(/^[ \t]+|[ \t]+$/, ""); print}')"
  if [ -z "$repo_name" ]; then
    continue
  fi

  TOTAL=$((TOTAL + 1))
  log "[$TOTAL] Processing ${BOLD}$repo_name${RESET}"
  repo_path="$REPO_PARENT/$repo_name"

  if [ ! -d "$repo_path" ]; then
    log_warn "  ⚠️  SKIP: Directory not found at $repo_path"
    SKIPPED_NOTFOUND=$((SKIPPED_NOTFOUND + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — SKIPPED (not found)" >> "$AUDIT_LOG"
    continue
  fi

  if should_skip_repo "$repo_name"; then
    log_warn "  ⏭️  SKIP: Scanned within last hour, use --force to override"
    SKIPPED_RECENT=$((SKIPPED_RECENT + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — SKIPPED (recent scan)" >> "$AUDIT_LOG"
    continue
  fi

  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  json_output="$OUTPUT_DIR/opengrep_${repo_name}_${timestamp}.json"
  scan_id="${repo_name}_${timestamp}"

  # Create the experiment row before the scan so all steps share the same ID
  "$PYTHON_BIN" -u - <<'PY' "$COZO_DB_PATH" "$scan_id" "$repo_name" 2>/dev/null || true
import sys, sqlite3, json
from datetime import datetime
db_path, exp_id, repo_name = sys.argv[1], sys.argv[2], sys.argv[3]
conn = sqlite3.connect(db_path)
conn.execute(
    "INSERT OR IGNORE INTO experiments (id, name, repos, status, started_at) VALUES (?, ?, ?, 'running', ?)",
    (exp_id, f"Scan {repo_name}", json.dumps([repo_name]), datetime.now().isoformat()),
)
conn.commit()
conn.close()
PY

  log_step "  Phase 1-2: Context discovery (learning DB: $ANALYTICS_DB)"
  if ! "$PYTHON_BIN" "$REPO_ROOT/Scripts/Context/discover_repo_context.py" "$repo_path" --database "$ANALYTICS_DB" --experiment-id "$scan_id"; then
    log_err "  ❌ Context discovery failed for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (context discovery)" >> "$AUDIT_LOG"
    continue
  fi

  if ! opengrep scan --config "$RULES_DIR" "$repo_path" --json --output "$json_output"; then
    log_err "  ❌ opengrep scan failed for $repo_name (see $json_output)"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (opengrep)" >> "$AUDIT_LOG"
    continue
  fi

  if [ ! -f "$json_output" ]; then
    log_warn "  ⚠️  No scan output for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (no JSON output)" >> "$AUDIT_LOG"
    continue
  fi

  # --- Check for rule parse/run errors in the opengrep output ----------------
  "$PYTHON_BIN" -u - <<'PY' "$json_output" "$RULES_DIR" || true
import json, sys, os

BG_RED  = "\033[41m"
RED     = "\033[0;31m"
YELLOW  = "\033[1;33m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

json_path, rules_dir = sys.argv[1], sys.argv[2]

with open(json_path) as f:
    data = json.load(f)

issues = []
for e in data.get("errors", []):
    rule_id = (
        e.get("rule_id")
        or (e.get("path") or {}).get("value", "")
        or "unknown"
    )
    msg     = e.get("message", str(e)).strip()
    level   = e.get("level", "error").upper()
    issues.append((rule_id, level, msg))

for s in data.get("skipped_rules", []):
    rule_id = s.get("rule_id", "unknown")
    reason  = s.get("reason", "unknown reason")
    issues.append((rule_id, "SKIPPED", reason))

if issues:
    width = 56
    bar = "═" * width
    print(f"\n  {BG_RED}{BOLD}╔{bar}╗{RESET}")
    print(f"  {BG_RED}{BOLD}║  🚨  RULE ERRORS / SKIPPED RULES{' ' * (width - 33)}║{RESET}")
    print(f"  {BG_RED}{BOLD}╚{bar}╝{RESET}")
    for rule_id, level, msg in issues:
        # Shorten rule_id to Rules-relative path
        marker = "Rules" + os.sep
        idx = rule_id.replace(".", os.sep).find(marker)
        short = rule_id[idx + len(marker):].replace(".", "/") if idx != -1 else rule_id
        colour = RED if level not in ("SKIPPED", "WARN") else YELLOW
        print(f"  {colour}{BOLD}[{level}]{RESET} {BOLD}{short}{RESET}")
        print(f"         {colour}{msg}{RESET}")
    print(f"  {BOLD}{RED}↑ Fix the above rules before relying on scan results ↑{RESET}\n")
    # Also write to stderr so it's hard to miss in CI
    print(f"RULE ERRORS DETECTED — {len(issues)} issue(s) in opengrep rules", file=sys.stderr)
PY

  if ! "$PYTHON_BIN" -u "$PYTHON_SCRIPT" "$json_output" --repo "$repo_name" --repo-path "$repo_path" --scan-id "$scan_id"; then
    log_err "  ❌ Cozo import failed for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (Cozo import)" >> "$AUDIT_LOG"
    continue
  fi

  # Also store findings to SQLite findings table (used by web UI) via store_findings.py
  if "$PYTHON_BIN" -u "$REPO_ROOT/Scripts/Persist/store_findings.py" "$json_output" \
      --experiment "$scan_id" --repo "$repo_name" > /dev/null 2>&1; then
    log "  📋 Findings stored to SQLite"
  else
    log_warn "  ⚠️  SQLite findings store failed (non-fatal)"
  fi

  # remove the opengrep json after successful import
  if rm -f "$json_output"; then
    log "  🗑️  Removed opengrep JSON"
  else
    log_warn "  ⚠️  Failed to remove opengrep JSON: $json_output (manual cleanup may be required)"
  fi

  echo -e "${CYAN}Resources detected for ${BOLD}$repo_name${RESET}${CYAN} (scan $scan_id):${RESET}"
  "$PYTHON_BIN" -u - <<'PY' "$COZO_DB_PATH" "$scan_id" "$repo_name" "$repo_path" || log_warn "  ⚠️  Failed to display findings summary for $repo_name"
import sqlite3, sys

ORANGE = "\033[38;5;208m"
GREEN  = "\033[0;32m"
CYAN   = "\033[0;36m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

cozo_db, scan_id_arg, repo_arg, repo_path_arg = sys.argv[1:5]

def shorten_source(source):
    prefix = repo_path_arg.rstrip("/") + "/"
    return source[len(prefix):] if source.startswith(prefix) else source

try:
    conn = sqlite3.connect(cozo_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT f.rule_id, f.title, f.base_severity, f.source_file, f.source_line_start,
               COALESCE(r.provider, '') AS provider
        FROM findings f
        JOIN repositories repo ON f.repo_id = repo.id
        LEFT JOIN resources r ON f.resource_id = r.id
        WHERE repo.experiment_id = ? AND LOWER(repo.repo_name) = LOWER(?)
        ORDER BY f.base_severity DESC, f.rule_id
        """,
        (scan_id_arg, repo_arg),
    ).fetchall()
    conn.close()
except Exception as e:
    print(f"  {DIM}(could not display findings summary: {e}){RESET}")
    sys.exit(0)

if not rows:
    print(f"  {DIM}(no findings stored for scan {scan_id_arg}){RESET}")
else:
    sev_counts = {}
    for row in rows:
        sev = row["base_severity"] or "Unknown"
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        provider = row["provider"] or "unknown"
        rel_source = shorten_source(row["source_file"] or "")
        line = row["source_line_start"] or ""
        rule_id = row["rule_id"] or ""
        print(f"  {ORANGE}🔥 {RESET}{BOLD}{rule_id}{RESET}  {DIM}{rel_source}:{line}{RESET}  ({provider}  {sev})")
    parts = [f"{v} {k.lower()}" for k, v in sorted(sev_counts.items(), key=lambda x: ['Critical','High','Medium','Low','Unknown'].index(x[0]) if x[0] in ['Critical','High','Medium','Low','Unknown'] else 9)]
    print(f"  {ORANGE}{len(rows)} finding(s): {', '.join(parts)}{RESET}")
PY

  log_ok "  ✅ Scan + Cozo import complete for $repo_name"

  # C1 fix: relink findings to resources so resource_id is populated for provider filtering + risk scoring
  RELINK_SCRIPT="$REPO_ROOT/Scripts/Persist/relink_findings_to_resources.py"
  if [ -f "$RELINK_SCRIPT" ]; then
    "$PYTHON_BIN" -u "$RELINK_SCRIPT" --experiment "$scan_id" > /dev/null 2>&1 && \
      log "  🔗 Findings linked to resources" || \
      log_warn "  ⚠️  Findings relink step failed (non-fatal)"
  fi

  # Run exposure analysis + infer data flows
  EXPOSURE_SCRIPT="$REPO_ROOT/Scripts/Analyze/exposure_analyzer.py"
  INFER_SCRIPT="$REPO_ROOT/Scripts/Analyze/infer_semantic_connections.py"
  if [ -f "$EXPOSURE_SCRIPT" ]; then
    "$PYTHON_BIN" -u "$EXPOSURE_SCRIPT" --experiment "$scan_id" > /dev/null 2>&1 && \
      log "  🔍 Exposure analysis complete" || \
      log_warn "  ⚠️  Exposure analysis failed (non-fatal)"
  fi
  if [ -f "$INFER_SCRIPT" ]; then
    "$PYTHON_BIN" -u "$INFER_SCRIPT" --experiment "$scan_id" > /dev/null 2>&1 && \
      log "  🔗 Data flows inferred" || \
      log_warn "  ⚠️  Data flow inference failed (non-fatal)"
  fi

  # Generate architecture diagrams (one per provider) and persist to cloud_diagrams table
  DIAGRAM_SCRIPT="$REPO_ROOT/Scripts/Generate/generate_diagram.py"
  DIAGRAM_OUT_DIR="$REPO_ROOT/Output/Data/diagrams/$scan_id"
  if [ -f "$DIAGRAM_SCRIPT" ]; then
    PYTHONPATH="$REPO_ROOT/Scripts/Persist:$REPO_ROOT/Scripts/Utils:$REPO_ROOT/Scripts/Generate" \
      "$PYTHON_BIN" -u "$DIAGRAM_SCRIPT" "$scan_id" --split-by-provider --output "$DIAGRAM_OUT_DIR" > /dev/null 2>&1 && \
      log "  🗺️  Architecture diagrams generated" || \
      log_warn "  ⚠️  Diagram generation failed (non-fatal)"
  fi

  # G1 fix: mark experiment as complete in DB
  "$PYTHON_BIN" -u - <<'PY' "$COZO_DB_PATH" "$scan_id" 2>/dev/null || true
import sys, sqlite3
db_path, scan_id = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db_path)
conn.execute("UPDATE experiments SET status='complete', completed_at=datetime('now') WHERE id=? AND status='running'", [scan_id])
conn.commit()
conn.close()
PY
  SUCCESS=$((SUCCESS + 1))
  echo "### $(date '+%H:%M:%S') - Repo $repo_name — COMPLETE (scan_id: $scan_id)" >> "$AUDIT_LOG"
done < "$REPOS_FILE"

TOTAL_TIME=$(( $(date +%s) - START_TIME ))
TOTAL_HOURS=$((TOTAL_TIME / 3600))
REMAINING_MIN=$(( (TOTAL_TIME % 3600) / 60 ))

log ""
log_ok "✅ Cozo repo scan complete"
log "- Processed: $TOTAL"
log_ok "- Success:   $SUCCESS"
if [ "$FAILED" -gt 0 ]; then
  log_err "- Failed:    $FAILED"
else
  log "- Failed:    $FAILED"
fi
log_warn "- Skipped (missing): $SKIPPED_NOTFOUND"
log_warn "- Skipped (recent):  $SKIPPED_RECENT"
log "- Duration: ${TOTAL_HOURS}h ${REMAINING_MIN}m"

cat >> "$AUDIT_LOG" <<EOF

---

## Summary
- **Total repos processed:** $TOTAL
- **Success:** $SUCCESS
- **Failed:** $FAILED
- **Skipped (missing):** $SKIPPED_NOTFOUND
- **Skipped (recent scan):** $SKIPPED_RECENT
- **Duration:** ${TOTAL_HOURS}h ${REMAINING_MIN}m
- **End time:** $(date '+%H:%M:%S')

EOF
