#!/usr/bin/env bash
# Daily feedback loop: pulls each day's newly-finished MLB/NPB games and
# rebuilds Elo ratings/history from them, so every prediction from tomorrow
# on already reflects today's actual results (win/loss, runs, hits, HR, K).
# This is intentionally cheap (no model retraining) so it's safe to run
# straight on the small EC2 instance -- the heavy ML ensemble retrain
# (scripts/mlb/train_ml_models.py) is a separate, occasional, manual step
# that should run on a machine with more RAM, not this one, since it once
# exhausted this instance's memory and took the site down.
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

echo "==> Done: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
