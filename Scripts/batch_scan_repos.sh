#!/bin/bash
# Batch repo scanner: Phase 1-2 discovery only (no opengrep, no LLM)
# Usage: bash Scripts/batch_scan_repos.sh [--force]

set -e

# Use repository-relative paths
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPOS_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

INTAKE_FILE="$REPO_ROOT/Intake/ReposToScan.txt"
DB_PATH="$REPO_ROOT/Output/Learning/triage.db"
AUDIT_LOG="$REPO_ROOT/Output/Audit/Session_$(date +%Y-%m-%d_%H%M%S).md"
TIMEOUT=120  # 2 minute timeout per repo

# Parse arguments
FORCE_SCAN=false
if [[ "$1" == "--force" ]]; then
  FORCE_SCAN=true
fi

# Helper function for timestamped echo
log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

# Ensure triage.db exists
if [ ! -f "$DB_PATH" ]; then
  log "triage.db not found, initializing..."
  .venv/bin/python Scripts/learning_db.py init
fi

# Check if repo was scanned within last hour
should_skip_repo() {
  local repo_name="$1"
  
  # Skip check if --force flag is set
  if [ "$FORCE_SCAN" = true ]; then
    return 1  # Don't skip
  fi
  
  # Query DB for last scan time
  local last_scan=$(sqlite3 "$DB_PATH" \
    "SELECT MAX(scanned_at) FROM repositories WHERE repo_name='$repo_name';" 2>/dev/null)
  
  if [ -z "$last_scan" ]; then
    return 1  # No previous scan, don't skip
  fi
  
  # Convert timestamps to Unix epoch
  local last_scan_epoch=$(date -d "$last_scan" +%s 2>/dev/null || echo "0")
  local now_epoch=$(date +%s)
  local diff=$((now_epoch - last_scan_epoch))
  local one_hour=3600
  
  if [ "$diff" -lt "$one_hour" ]; then
    return 0  # Skip (scanned within last hour)
  else
    return 1  # Don't skip
  fi
}

log "🦖 Starting batch repo scan (Phase 1-2 only, no opengrep)"
log "- Repos root: $REPOS_ROOT"
log "- Total repos: $(wc -l < "$INTAKE_FILE")"
log "- Database: $DB_PATH"
log "- Timeout: ${TIMEOUT}s per repo"
if [ "$FORCE_SCAN" = true ]; then
  log "- Force mode: ON (rescan all repos)"
else
  log "- Smart skip: ON (skip repos scanned within last hour)"
fi
echo ""

# Counters
TOTAL=0
SUCCESS=0
FAILED=0
SKIPPED_NOTFOUND=0
SKIPPED_RECENT=0
TIMEOUT_COUNT=0
START_TIME=$(date +%s)

# Create audit log
mkdir -p "$(dirname "$AUDIT_LOG")"
cat > "$AUDIT_LOG" << AUDIT_EOF
# 🟣 Audit Log - Batch Scan $(date '+%Y-%m-%d %H:%M:%S')

**AUDIT LOG ONLY — do not load into LLM triage context**

## Session Metadata
- 🗓️ **Date:** $(date '+%d/%m/%Y')
- ⏰ **Start time:** $(date '+%H:%M')
- 🏷️ **Triage type:** Repo scan (batch)
- 🦖 **Mode:** Phase 1-2 context discovery only (no LLM, opengrep skipped)
- ⏳ **Timeout:** ${TIMEOUT}s per repo
- 🚦 **Force mode:** $FORCE_SCAN
- 🗃️ **Database:** $DB_PATH
- 📂 **Source:** $INTAKE_FILE
- 📊 **Total repos:** $(wc -l < "$INTAKE_FILE")

---

## Actions Log

AUDIT_EOF

# Process each repo
while IFS= read -r repo_name || [ -n "$repo_name" ]; do
  # Skip empty lines or comments
  [ -z "$repo_name" ] && continue
  [[ "$repo_name" =~ ^# ]] && continue
  
  TOTAL=$((TOTAL + 1))
  # Remove carriage returns and whitespace from repo_name
  repo_name_clean=$(echo "$repo_name" | tr -d '\r' | xargs)
  REPO_PATH="$REPOS_ROOT/$repo_name_clean"
  REPO_START=$(date +%s)
  
  log "[$TOTAL] Processing: $repo_name_clean"
  
  # Check if repo exists
  if [ ! -d "$REPO_PATH" ]; then
    log "  ⚠️  SKIP: Directory not found"
    SKIPPED_NOTFOUND=$((SKIPPED_NOTFOUND + 1))
    continue
  fi
  
  # Check if recently scanned
  if should_skip_repo "$repo_name"; then
    log "  ⏭️  SKIP: Scanned within last hour"
    SKIPPED_RECENT=$((SKIPPED_RECENT + 1))
    continue
  fi
  
  # Phase 1-2: Context discovery with timeout
  log "  Phase 1-2: Context discovery..."
  if timeout $TIMEOUT .venv/bin/python "$REPO_ROOT/Scripts/discover_repo_context.py" "$REPO_PATH" --database "$DB_PATH" > /dev/null 2>&1; then
    log "  ✅ Complete"
    SUCCESS=$((SUCCESS + 1))
  else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
      log "  ⏱️  TIMEOUT (>${TIMEOUT}s) - skipping"
      TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
      echo "### $(date +"%H:%M") - Repo Scan Timeout: $repo_name" >> "$AUDIT_LOG"
      echo "- **Reason:** Exceeded ${TIMEOUT}s timeout" >> "$AUDIT_LOG"
      echo "" >> "$AUDIT_LOG"
    else
      log "  ❌ Failed"
      FAILED=$((FAILED + 1))
      echo "### $(date +"%H:%M") - Repo Scan Failed: $repo_name" >> "$AUDIT_LOG"
      echo "- **Reason:** Context discovery error" >> "$AUDIT_LOG"
      echo "" >> "$AUDIT_LOG"
    fi
    continue
  fi
  
  # Calculate repo duration
  REPO_END=$(date +%s)
  REPO_DURATION=$((REPO_END - REPO_START))
  log "  ⏱️  Took ${REPO_DURATION}s"
  
  # Progress report every 50 repos
  if [ $((TOTAL % 50)) -eq 0 ]; then
    ELAPSED=$(($(date +%s) - START_TIME))
    PROCESSED=$((SUCCESS + FAILED))
    if [ "$PROCESSED" -gt 0 ]; then
      AVG_TIME=$((ELAPSED / PROCESSED))
      REMAINING=$((744 - TOTAL))
      ETA=$((REMAINING * AVG_TIME))
      ETA_MIN=$((ETA / 60))
      
      log "📊 Progress: $TOTAL/744 | Success: $SUCCESS | Failed: $FAILED | Timeout: $TIMEOUT_COUNT | Skipped: $((SKIPPED_NOTFOUND + SKIPPED_RECENT))"
      log "⏱️  Avg: ${AVG_TIME}s/repo | ETA: ${ETA_MIN} minutes"
      
      echo "### $(date +"%H:%M") - Progress: $TOTAL repos" >> "$AUDIT_LOG"
      echo "- **Success:** $SUCCESS, **Failed:** $FAILED, **Timeout:** $TIMEOUT_COUNT, **Skipped (not found):** $SKIPPED_NOTFOUND, **Skipped (recent):** $SKIPPED_RECENT" >> "$AUDIT_LOG"
      echo "- **Avg time:** ${AVG_TIME}s/repo, **ETA:** ${ETA_MIN} min" >> "$AUDIT_LOG"
      echo "" >> "$AUDIT_LOG"
    fi
  fi
  
done < "$INTAKE_FILE"

# Summary
TOTAL_TIME=$(($(date +%s) - START_TIME))
TOTAL_MIN=$((TOTAL_TIME / 60))
TOTAL_HOURS=$((TOTAL_TIME / 3600))
REMAINING_MIN=$((TOTAL_MIN % 60))
echo ""
log "✅ Batch scan complete"
log "- Total processed: $TOTAL"
log "- Success: $SUCCESS"
log "- Failed: $FAILED"
log "- Timeout: $TIMEOUT_COUNT"
log "- Skipped (not found): $SKIPPED_NOTFOUND"
log "- Skipped (recent scan): $SKIPPED_RECENT"
log "- Duration: ${TOTAL_HOURS}h ${REMAINING_MIN}m"

# Final audit log
cat >> "$AUDIT_LOG" << AUDIT_EOF

---

## Summary
- **Session duration:** ${TOTAL_HOURS}h ${REMAINING_MIN}m
- **Total repos processed:** $TOTAL
- **Success:** $SUCCESS
- **Failed:** $FAILED
- **Timeout:** $TIMEOUT_COUNT
- **Skipped (not found):** $SKIPPED_NOTFOUND
- **Skipped (recent scan):** $SKIPPED_RECENT
- **End time:** $(date '+%H:%M')

AUDIT_EOF

log "📋 Audit log: $AUDIT_LOG"

