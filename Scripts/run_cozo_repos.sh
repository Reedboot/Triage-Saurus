#!/usr/bin/env bash
set -euo pipefail

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
PYTHON_BIN="$REPO_ROOT/.venv-cozo/bin/python"
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

[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3"

mkdir -p "$OUTPUT_DIR"
mkdir -p "$SUMMARY_OUTPUT_DIR"
mkdir -p "$(dirname "$AUDIT_LOG")"
mkdir -p "$ANALYTICS_DB_DIR"

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

log "Ensuring learning schema exists inside Cozo DB at $ANALYTICS_DB"
"$PYTHON_BIN" "$REPO_ROOT/Scripts/Utils/init_cozo_learning.py" init "$COZO_DB_PATH" >/dev/null 2>&1

should_skip_repo() {
  local repo="$1"

  if [ "$FORCE_SCAN" = true ]; then
    return 1
  fi

  if [ ! -f "$COZO_DB_PATH" ]; then
    return 1
  fi

  local safe_repo
  safe_repo=$(printf "%s" "$repo" | sed "s/'/''/g")
  local last_scan
  last_scan=$(sqlite3 "$COZO_DB_PATH" \
    "SELECT scan_time FROM repo_scans WHERE repo_name = '$safe_repo' ORDER BY scan_time DESC LIMIT 1;" 2>/dev/null | tr -d '\n')
  if [ -z "$last_scan" ]; then
    return 1
  fi

  local last_epoch
  last_epoch=$(date -d "$last_scan" +%s 2>/dev/null || echo "0")
  local now_epoch
  now_epoch=$(date +%s)
  local diff=$((now_epoch - last_epoch))

  if [ "$diff" -lt "$ONE_HOUR" ]; then
    return 0
  fi
  return 1
}

REPO_LIST_COUNT=$(grep -cEv '^[[:space:]]*(#|$)' "$REPOS_FILE" || true)

cat > "$AUDIT_LOG" <<EOF
# 🟣 Cozo Scan Audit Log — $(date '+%Y-%m-%d %H:%M:%S')

**Repo list:** $REPOS_FILE
**Entries:** $REPO_LIST_COUNT
**Cozo DB:** $COZO_DB_PATH
**Force scans:** $FORCE_SCAN

EOF

log "🦖 Starting Cozo repo scan"
log "- Repos: $REPO_LIST_COUNT entries from $REPOS_FILE"
log "- Cozo DB: $COZO_DB_PATH"
log "- Force mode: $FORCE_SCAN"
log "- Audit log: $AUDIT_LOG"
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
  log "[$TOTAL] Processing repo: $repo_name"
  repo_path="$REPO_PARENT/$repo_name"

  if [ ! -d "$repo_path" ]; then
    log "  ⚠️  SKIP: Directory not found at $repo_path"
    SKIPPED_NOTFOUND=$((SKIPPED_NOTFOUND + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — SKIPPED (not found)" >> "$AUDIT_LOG"
    continue
  fi

  if should_skip_repo "$repo_name"; then
    log "  ⏭️  SKIP: Scanned within last hour, use --force to override"
    SKIPPED_RECENT=$((SKIPPED_RECENT + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — SKIPPED (recent scan)" >> "$AUDIT_LOG"
    continue
  fi

  log "  Phase 1-2: Context discovery (learning DB: $ANALYTICS_DB)"
  if ! "$PYTHON_BIN" "$REPO_ROOT/Scripts/Context/discover_repo_context.py" "$repo_path" --database "$ANALYTICS_DB"; then
    log "  ❌ Context discovery failed for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (context discovery)" >> "$AUDIT_LOG"
    continue
  fi

  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  json_output="$OUTPUT_DIR/opengrep_${repo_name}_${timestamp}.json"
  scan_id="${repo_name}_${timestamp}"

  if ! opengrep scan --config "$RULES_DIR" "$repo_path" --json --output "$json_output"; then
    log "  ❌ opengrep scan failed for $repo_name (see $json_output)"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (opengrep)" >> "$AUDIT_LOG"
    continue
  fi

  if [ ! -f "$json_output" ]; then
    log "  ⚠️  No scan output for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (no JSON output)" >> "$AUDIT_LOG"
    continue
  fi

  if ! "$PYTHON_BIN" -u "$PYTHON_SCRIPT" "$json_output" --repo "$repo_name" --repo-path "$repo_path" --scan-id "$scan_id"; then
    log "  ❌ Cozo import failed for $repo_name"
    FAILED=$((FAILED + 1))
    echo "### $(date '+%H:%M:%S') - Repo $repo_name — FAILED (Cozo import)" >> "$AUDIT_LOG"
    continue
  fi

  echo "Resources detected for $repo_name (scan $scan_id):"
  "$PYTHON_BIN" -u - <<'PY' "$COZO_DB_PATH" "$scan_id" "$repo_name"
from pycozo import Client
import sys

cozo_db, scan_id_arg, repo_arg = sys.argv[1:4]

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
    print(f"  (no findings stored for scan {scan_id_arg})")
else:
    for row in rows:
        provider = row[label_map["provider"]] or "unknown"
        severity = row[label_map["severity"]]
        rule_id = row[label_map["rule_id"]]
        source = row[label_map["source_file"]]
        line = row[label_map["start_line"]]
        print(f"  - {rule_id} @ {source}:{line} [{severity}] ({provider})")
PY

  if ! "$PYTHON_BIN" "$SUMMARY_SCRIPT" --repo "$repo_name" --scan-id "$scan_id" --output-dir "$SUMMARY_OUTPUT_DIR"; then
    echo "Failed to render repo summary for $repo_name" >&2
  fi

  log "  ✅ Scan + Cozo import complete for $repo_name"
  SUCCESS=$((SUCCESS + 1))
  echo "### $(date '+%H:%M:%S') - Repo $repo_name — COMPLETE (scan_id: $scan_id)" >> "$AUDIT_LOG"
done < "$REPOS_FILE"

TOTAL_TIME=$(( $(date +%s) - START_TIME ))
TOTAL_MIN=$((TOTAL_TIME / 60))
TOTAL_HOURS=$((TOTAL_TIME / 3600))
REMAINING_MIN=$((TOTAL_MIN % 60))

log ""
log "✅ Cozo repo scan complete"
log "- Processed: $TOTAL"
log "- Success: $SUCCESS"
log "- Failed: $FAILED"
log "- Skipped (missing): $SKIPPED_NOTFOUND"
log "- Skipped (recent): $SKIPPED_RECENT"
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
