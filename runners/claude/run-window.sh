#!/usr/bin/env bash
# WB Monitor — Claude gold-standard discovery run-window.
# Mirrors the GlobaLeaks rig model: single-instance (flock), sources the
# isolated wbmonitor token env, runs the discovery driver for a bounded number
# of batches, then commits results. Schedule via cron (e.g. every 5h) for a
# resumable multi-window campaign that stays within the monthly plan budget.
#
# Usage: run-window.sh [ARCHIVE_DATE] [MAX_BATCHES]
set -euo pipefail

REPO="$HOME/whistleblowing-monitor-italia"
ENV_FILE="$HOME/.wb-discovery-env"
LOCK="/tmp/wb-discover.lock"
LOG="$REPO/data/logs/wb-discover.log"

ARCHIVE_DATE="${1:-$(date +%F)}"
MAX_BATCHES="${2:-40}"
BATCH="${WB_BATCH:-15}"

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date -Is) another run-window is active, exiting" >> "$LOG"
    exit 0
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "$(date -Is) ERROR: $ENV_FILE missing (run tools/setup_wb_token.sh)" >> "$LOG"
    exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

cd "$REPO"
mkdir -p "$(dirname "$LOG")"
echo "$(date -Is) === window start date=$ARCHIVE_DATE max_batches=$MAX_BATCHES batch=$BATCH ===" >> "$LOG"

.venv/bin/python -m runners.claude.wb_discover \
    --date "$ARCHIVE_DATE" --batch "$BATCH" --max-batches "$MAX_BATCHES" \
    >> "$LOG" 2>&1 || echo "$(date -Is) driver exited non-zero" >> "$LOG"

# Persist gold verdicts (DB is gitignored; commit nothing heavy — just log a marker).
echo "$(date -Is) === window end ===" >> "$LOG"
