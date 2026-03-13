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
SUMMARY_SCRIPT="$REPO_ROOT/Scripts/Generate/generate_repo_summary_from_cozo.py"
SUMMARY_OUTPUT_DIR="$REPO_ROOT/Output/Summary/Repos"
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
mkdir -p "$SUMMARY_OUTPUT_DIR"
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

  log_step "  Phase 1-2: Context discovery (learning DB: $ANALYTICS_DB)"
  if ! "$PYTHON_BIN" "$REPO_ROOT/Scripts/Context/discover_repo_context.py" "$repo_path" --database "$ANALYTICS_DB"; then
    log_err "  ❌ Context discovery failed for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (context discovery)" >> "$AUDIT_LOG"
    continue
  fi

  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  json_output="$OUTPUT_DIR/opengrep_${repo_name}_${timestamp}.json"
  scan_id="${repo_name}_${timestamp}"

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

  # remove the opengrep json after successful import
  if rm -f "$json_output"; then
    log "  🗑️  Removed opengrep JSON"
  else
    log_warn "  ⚠️  Failed to remove opengrep JSON: $json_output (manual cleanup may be required)"
  fi

  echo -e "${CYAN}Resources detected for ${BOLD}$repo_name${RESET}${CYAN} (scan $scan_id):${RESET}"
  "$PYTHON_BIN" -u - <<'PY' "$COZO_DB_PATH" "$scan_id" "$repo_name" "$repo_path" || log_warn "  ⚠️  Failed to display findings summary for $repo_name"
from pycozo import Client
import json, sys

ORANGE = "\033[38;5;208m"
GREEN  = "\033[0;32m"
CYAN   = "\033[0;36m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

cozo_db, scan_id_arg, repo_arg, repo_path_arg = sys.argv[1:5]


def shorten_rule(rule_id):
    """Strip the full dotted path prefix, keep only Rules-relative portion.

    e.g. home.neil.code.Triage-Saurus.Rules.Misconfigurations.Azure.SQL.azure-sql-tls
         → Misconfigurations/Azure/SQL/azure-sql-tls
    """
    marker = "Rules."
    idx = rule_id.find(marker)
    if idx != -1:
        return rule_id[idx + len(marker):].replace(".", "/")
    return rule_id


def classify_from_metadata(metadata_json):
    """Use the rule's own metadata fields to determine finding kind.

    Returns ('detection' | 'misconfiguration', meta_dict).
    Prefers finding_kind / rule_type over path heuristics.
    """
    try:
        meta = json.loads(metadata_json) if metadata_json else {}
    except Exception:
        meta = {}
    finding_kind = meta.get("finding_kind", "")
    rule_type    = meta.get("rule_type", "")
    if finding_kind == "Asset" or rule_type == "context_discovery":
        return "detection", meta
    return "misconfiguration", meta


def shorten_source(source):
    prefix = repo_path_arg.rstrip("/") + "/"
    return source[len(prefix):] if source.startswith(prefix) else source


client = Client(engine="sqlite", path=cozo_db, dataframe=False)
try:
    data = client.export_relations(["findings"])
finally:
    client.close()

label_map = {col: idx for idx, col in enumerate(data["findings"]["headers"])}
rows = [
    row for row in data["findings"]["rows"]
    if row[label_map["scan_id"]] == scan_id_arg
]

if not rows:
    print(f"  {DIM}(no findings stored for scan {scan_id_arg}){RESET}")
else:
    misconfig_count = 0
    asset_count = 0
    for row in rows:
        provider      = row[label_map["provider"]] or "unknown"
        rule_id       = row[label_map["rule_id"]]
        source        = row[label_map["source_file"]]
        line          = row[label_map["start_line"]]
        metadata_json = row[label_map["metadata_json"]]

        short_rule = shorten_rule(rule_id)
        rel_source = shorten_source(source)
        kind, meta = classify_from_metadata(metadata_json)

        if kind == "detection":
            asset_count += 1
            subcategory = meta.get("subcategory", "")
            sub_str = f"  {DIM}[{subcategory}]{RESET}" if subcategory else ""
            print(f"  {GREEN}🔍 Asset   {RESET}{BOLD}{short_rule}{RESET}{sub_str}  {DIM}{rel_source}:{line}{RESET}  ({provider})")
        else:
            misconfig_count += 1
            confidence = meta.get("confidence", "")
            impact     = meta.get("impact", "")
            subcategory = meta.get("subcategory", "")
            # Build a compact badge string from available metadata
            badges = "  ".join(filter(None, [
                f"confidence:{confidence}" if confidence else "",
                f"impact:{impact}"         if impact     else "",
                f"[{subcategory}]"         if subcategory and not isinstance(subcategory, list)
                    else (f"[{', '.join(subcategory)}]" if isinstance(subcategory, list) else ""),
            ]))
            badge_str = f"  {DIM}{badges}{RESET}" if badges else ""
            print(f"  {ORANGE}🔥 Miscfg  {RESET}{BOLD}{short_rule}{RESET}{badge_str}  {DIM}{rel_source}:{line}{RESET}  ({provider})")

    parts = []
    if asset_count:
        parts.append(f"{GREEN}{asset_count} asset(s) detected{RESET}")
    if misconfig_count:
        parts.append(f"{ORANGE}{misconfig_count} misconfiguration(s){RESET}")
    if parts:
        print(f"  {'  '.join(parts)}")
PY

  if ! "$PYTHON_BIN" "$SUMMARY_SCRIPT" --repo "$repo_name" --scan-id "$scan_id" --output-dir "$SUMMARY_OUTPUT_DIR"; then
    log_warn "Failed to render repo summary for $repo_name" >&2
  fi

  log_ok "  ✅ Scan + Cozo import complete for $repo_name"
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
