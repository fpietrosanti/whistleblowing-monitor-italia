#!/usr/bin/env bash
# Hourly retry of transient connection failures (datacenter egress).
# Safe to schedule while a full scan is running: it skips if a scan is active
# (so it never writes to an in-progress run), then auto-seeds + retries due
# entries once the scan has finished. Single-instance via flock.
set -euo pipefail

REPO="$HOME/whistleblowing-monitor-italia"
LOG="$REPO/data/logs/retry.log"
cd "$REPO"
mkdir -p "$(dirname "$LOG")"

# Don't interfere with an active full scan.
if pgrep -f "m src.scanner" >/dev/null 2>&1; then
    echo "$(date -Is) scan active, skipping retry window" >> "$LOG"
    exit 0
fi

exec 9>/tmp/wb-retry.lock
if ! flock -n 9; then
    echo "$(date -Is) another retry window active, skipping" >> "$LOG"
    exit 0
fi

echo "$(date -Is) === retry window start ===" >> "$LOG"
.venv/bin/python -m tools.retry_due --seed --egress datacenter --max-parallel 10 \
    >> "$LOG" 2>&1 || echo "$(date -Is) retry_due exited non-zero" >> "$LOG"
echo "$(date -Is) === retry window end ===" >> "$LOG"
