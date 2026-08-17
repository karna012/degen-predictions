#!/usr/bin/env bash
# Morning Top-3 digest + optional channel posts.
# Schedule: 03:30 UTC = 09:00 IST (see deploy/systemd/degen-daily-distribute.timer)
#
# Manual:
#   bash deploy/daily_distribute.sh
#   bash deploy/daily_distribute.sh --dry-run
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/deploy/production.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PYTHON="${ROOT}/venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || command -v python)"
fi

mkdir -p "$ROOT/logs" "$ROOT/data/daily_digest" "$ROOT/output_tables/daily_drafts"
LOG="$ROOT/logs/daily_distribute_$(date -u +%Y%m%d).log"

echo "==> Daily distribute $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
cd "$ROOT/webapp"
"$PYTHON" daily_digest.py "$@" 2>&1 | tee -a "$LOG"
