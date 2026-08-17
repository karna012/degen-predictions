#!/usr/bin/env bash
# Watchdog for the Degen Predictions gunicorn app: if /healthz stops
# responding (process hung, not just crashed -- systemd's Restart=on-failure
# already covers crashes), restart the systemd service. Runs every 2 min via
# degen-watchdog.timer. Only checks HTTP liveness (connection + 200 status),
# NOT the ok:false feed-integrity field in the response body -- a feed API
# outage isn't fixed by restarting gunicorn.

set -euo pipefail

LOG="/home/ubuntu/Degen_predictions/deploy/logs/watchdog.log"
MARKER="/home/ubuntu/Degen_predictions/deploy/.watchdog_last_restart"
MIN_RESTART_GAP=180   # seconds; avoid restart loops if the app is truly broken
URL="http://127.0.0.1:8000/healthz"

mkdir -p "$(dirname "$LOG")"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

ok=0
code="000"
for _ in 1 2 3; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL" || echo "000")
    if [ "$code" = "200" ]; then
        ok=1
        break
    fi
    sleep 5
done

if [ "$ok" = "1" ]; then
    exit 0
fi

echo "$(ts) healthz unreachable after 3 attempts (last code=$code)" >> "$LOG"

now=$(date +%s)
last=0
[ -f "$MARKER" ] && last=$(cat "$MARKER")
gap=$((now - last))

if [ "$gap" -lt "$MIN_RESTART_GAP" ]; then
    echo "$(ts) skipping restart, last one was ${gap}s ago (< ${MIN_RESTART_GAP}s)" >> "$LOG"
    exit 0
fi

echo "$(ts) restarting degen-predictions" >> "$LOG"
echo "$now" > "$MARKER"
systemctl restart degen-predictions
