#!/usr/bin/env bash
# Daily data + feedback loop on the EXISTING degen-refresh-data.timer (every 3h):
# pulls finished MLB/NPB games, rebuilds Elo, settles published picks/combos,
# and writes data/feedback/*.json. Cheap on purpose (no model retrain, no new
# instance/timer) so EC2 billing stays the same. Heavy retrain stays on PC.
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate

echo "==> Refreshing MLB current-season data"
python scripts/mlb/download_current_season_espn.py
echo "==> Rebuilding MLB Elo ratings"
python scripts/mlb/build_elo.py > /dev/null

echo "==> Refreshing NPB data"
python scripts/npb/download_npb_data.py
echo "==> Rebuilding NPB Elo ratings"
python scripts/npb/build_elo.py > /dev/null

# Feedback loop (settle + score). Cheap SQLite/JSON only — no model retrain.
# Piggybacked on this existing timer so we do not add a new billed service.
echo "==> Settling published picks + writing feedback metrics"
python scripts/run_ec2_feedback_loop.py

echo "==> Done: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
